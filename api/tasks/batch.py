"""
Asynchronous Batch Task Manager for PersonaWeave API.
Coordinates background queueing, status tracking, image downloading/decoding,
and webhook callbacks.
"""

import asyncio
import hashlib
import os
import shutil
import sqlite3
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from api.inference_limiter import InferenceLimiter
from api.media import decode_manual_mask, encode_image_to_base64, load_image_from_source
from api.schemas import (
    BatchTaskCreateRequest,
    BatchTaskSummary,
    ImageSwapResult,
    SyncSwapRequest,
    TaskDetailData,
    TaskStatus,
)
from api.tasks.persistence import SQLiteTaskStore, default_task_db_path
from api.tasks.provenance import describe_skipped_people
from api.tasks.sources import build_source_references, resolve_source_locations
from core.image_security import (
    encode_snapshot_image,
    pin_request_to_addresses,
    validate_public_url,
)
from core.memory_budget import MemoryPressureError, require_swap_memory
from core.pipeline import PersonaWeavePipeline
from utils.image_io import write_image


class TaskManager:
    """Manages asynchronous batch task scheduling, execution, and state storage."""

    def __init__(
        self,
        pipeline: PersonaWeavePipeline,
        output_base_dir: str = "outputs/api_results",
        inference_limiter: InferenceLimiter | None = None,
        db_path: str | Path | None = None,
    ):
        self.pipeline = pipeline
        self.output_base_dir = output_base_dir
        self.inference_limiter = inference_limiter or InferenceLimiter(1)
        self.tasks: dict[str, TaskDetailData] = {}
        self._requests: dict[str, BatchTaskCreateRequest] = {}
        self._persisted_requests: set[str] = set()
        self._inline_failed_originals: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self._batch_queue = asyncio.Semaphore(1)
        os.makedirs(self.output_base_dir, exist_ok=True)
        self._store = SQLiteTaskStore(db_path or default_task_db_path(output_base_dir))
        self._load_persisted_tasks()
        # Assigned by lifecycle: explicit post-local AI work remains server-owned after browser disconnects.
        self.refinement_hook = None

    def _persist(self, task_id: str):
        # The immutable request may contain many full-resolution data URIs. Save it once,
        # rather than creating batch-sized JSON copies for every progress update.
        request = self._requests.get(task_id) if task_id not in self._persisted_requests else None
        self._store.save(
            "batch",
            task_id,
            self.get_task(task_id).model_dump(mode="json"),
            request.model_dump(mode="json") if request is not None else None,
        )
        if request is not None:
            self._persisted_requests.add(task_id)

    def _load_persisted_tasks(self):
        for state in self._store.iter_states("batch"):
            try:
                task = TaskDetailData.model_validate(state)
            except (TypeError, ValueError):
                continue
            self.tasks[task.task_id] = task
            self._persisted_requests.add(task.task_id)
            # Old failures can have no request snapshot and their only input in
            # original_url. Keep those bytes in SQLite, hydrating on explicit detail
            # reads rather than retaining all historical failures in RAM.
            for result in task.results:
                if result.status == "FAILED" and result.original_url.startswith("data:"):
                    result.original_url = ""
                    self._inline_failed_originals.add(task.task_id)
            if task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
                task.status = TaskStatus.FAILED
                task.waiting_reason = None
                task.error = "Batch task was interrupted by an application restart; submit it again to retry"
                # Only persisted results are facts: unprocessed images are neither
                # successful nor failed attempts, and completed artifacts stay reviewable.
                task.processed_images = len(task.results)
                task.success_count = sum(item.status in {"SUCCESS", "PARTIAL"} for item in task.results)
                task.failure_count = sum(item.status == "FAILED" for item in task.results)
                task.progress = task.processed_images / task.total_images if task.total_images else 0.0
                task.has_partial_failure = bool(
                    task.success_count and (task.failure_count or task.processed_images < task.total_images)
                )
                task.completed_at = datetime.now(UTC).isoformat()
                self._persist(task.task_id)

    def create_batch_task(self, req: BatchTaskCreateRequest) -> tuple[str, float]:
        """
        Registers a new batch task in the store and spawns the background worker.

        Returns:
            (task_id, estimated_seconds)
        """
        task_id = f"pw_task_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        total_images = req.total_images
        estimated_seconds = self._estimate_seconds(total_images)

        task_data = TaskDetailData(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            progress=0.0,
            total_images=total_images,
            processed_images=0,
            created_at=now_iso,
            results=[],
        )
        self.tasks[task_id] = task_data
        self._requests[task_id] = req
        self._persist(task_id)
        self._requests.pop(task_id, None)

        # Run task in background asyncio loop
        # Queued jobs keep only an ID; their full image request is already durable.
        t = asyncio.create_task(self._run_batch_task(task_id))
        self._background_tasks.add(t)
        t.add_done_callback(self._background_tasks.discard)
        return task_id, estimated_seconds

    def _estimate_seconds(self, total_images: int) -> float:
        """Estimate duration from recent completed images; zero means insufficient evidence."""
        recent_costs = [
            result.cost_ms
            for task in self.tasks.values()
            for result in task.results
            if result.cost_ms > 0 and result.status in {"SUCCESS", "PARTIAL"}
        ][-100:]
        if not recent_costs:
            return 0.0
        return round(statistics.median(recent_costs) * total_images / 1000, 2)

    def get_request_snapshot(self, task_id: str) -> BatchTaskCreateRequest | None:
        """Read a detached recipe on demand without retaining historical images."""
        request = self._requests.get(task_id)
        if request is not None:
            return request.model_copy(deep=True)
        if task_id not in self.tasks:
            return None
        data = self._store.load_request("batch", task_id)
        try:
            return BatchTaskCreateRequest.model_validate(data) if data is not None else None
        except (TypeError, ValueError):
            return None

    def get_task(self, task_id: str) -> TaskDetailData | None:
        """Return current state, hydrating legacy inline inputs only on detail reads."""
        task = self.tasks.get(task_id)
        if task is None or task_id not in self._inline_failed_originals:
            return task
        state = self._store.load_state("batch", task_id)
        snapshot = task.model_copy(deep=True)
        saved_results = (state or {}).get("results", [])
        for result, saved in zip(snapshot.results, saved_results, strict=False):
            if result.status == "FAILED" and not result.original_url:
                result.original_url = saved.get("original_url", "")
        return snapshot

    def delete_task(self, task_id: str) -> bool:
        """Delete a terminal task and quarantine its artifacts before DB removal.

        Active tasks are rejected so their worker cannot recreate files or persist
        state after deletion. Artifact quarantine is restored if SQLite deletion
        fails; once the row is gone, quarantine cleanup is best-effort and is no
        longer reachable through the public file route.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ValueError("Active batch tasks cannot be deleted")

        output_root = Path(self.output_base_dir).resolve()
        task_dir = output_root / task_id
        if task_dir.parent.resolve() != output_root:
            raise ValueError("Invalid batch task artifact path")

        quarantined = None
        if task_dir.exists() or task_dir.is_symlink():
            quarantine_root = output_root.parent / ".task_deletion_quarantine"
            quarantine_root.mkdir(parents=True, exist_ok=True)
            quarantined = quarantine_root / f"{task_id}-{uuid.uuid4().hex}"
            os.replace(task_dir, quarantined)

        try:
            self._store.delete("batch", task_id)
        except Exception:
            if quarantined is not None and quarantined.exists():
                os.replace(quarantined, task_dir)
            raise

        self.tasks.pop(task_id, None)
        self._requests.pop(task_id, None)
        self._persisted_requests.discard(task_id)
        self._inline_failed_originals.discard(task_id)
        if quarantined is not None:
            if quarantined.is_symlink() or quarantined.is_file():
                quarantined.unlink(missing_ok=True)
            else:
                shutil.rmtree(quarantined, ignore_errors=True)
        return True

    def list_tasks(self, limit: int = 50) -> list[TaskDetailData]:
        """List complete recent tasks for backward-compatible API consumers."""
        all_tasks = list(self.tasks.values())
        all_tasks.reverse()
        return [self.get_task(task.task_id) for task in all_tasks[:limit]]

    def list_task_summaries(self, limit: int = 50) -> list[BatchTaskSummary]:
        """List recent task metadata without returning image URLs or embedded payloads."""
        # Summary reads must never hydrate legacy inline originals.
        all_tasks = list(reversed(self.tasks.values()))[:limit]
        return [
            BatchTaskSummary(
                task_id=task.task_id,
                status=task.status,
                progress=task.progress,
                total_images=task.total_images,
                processed_images=task.processed_images,
                success_count=task.success_count,
                failure_count=task.failure_count,
                has_partial_failure=task.has_partial_failure,
                viewable_results=sum(
                    result.status in {"SUCCESS", "PARTIAL"} and bool(result.processed_url) for result in task.results
                ),
                created_at=task.created_at,
                completed_at=task.completed_at,
                error=task.error,
            )
            for task in all_tasks
        ]

    async def _run_batch_task(self, task_id: str):
        """Keep later batches queued without preparing their reference images in RAM."""
        try:
            async with self._batch_queue:
                req = self.get_request_snapshot(task_id)
                if req is None:
                    task = self.tasks[task_id]
                    task.status = TaskStatus.FAILED
                    task.error = "Batch request is unavailable; submit the task again"
                    task.completed_at = datetime.now(UTC).isoformat()
                    self._persist(task_id)
                    return
                self._requests[task_id] = req
                try:
                    await self._execute_batch_task(task_id, req)
                finally:
                    # SQLite keeps the immutable recipe for recovery and refinement.
                    # Retaining it here would keep every completed batch's base64 in RAM.
                    self._requests.pop(task_id, None)
        except (Exception, asyncio.CancelledError) as exc:
            # Loading the saved recipe and initial persistence happen outside the
            # execution handler. A failure there must not leave a dead QUEUED job.
            task = self.tasks[task_id]
            if task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
                task.status = TaskStatus.FAILED
                task.waiting_reason = None
                task.error = f"批任务执行中断（{type(exc).__name__}），已保留完成结果，请检查后重新提交。"
                task.completed_at = datetime.now(UTC).isoformat()
                task.processed_images = len(task.results)
                task.success_count = sum(item.status in {"SUCCESS", "PARTIAL"} for item in task.results)
                task.failure_count = sum(item.status == "FAILED" for item in task.results)
                task.has_partial_failure = bool(task.success_count)
                try:
                    self._persist(task_id)
                except (OSError, sqlite3.Error):
                    # Keep the live terminal state even if storage itself is unavailable.
                    print("[PersonaWeave] Could not persist interrupted batch task; check local storage.")
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _execute_batch_task(self, task_id: str, req: BatchTaskCreateRequest):
        """Worker executing the batch task asynchronously."""
        task = self.tasks[task_id]
        task.status = TaskStatus.PROCESSING
        task.waiting_reason = None
        self._persist(task_id)

        def report_memory_wait(message):
            if task.waiting_reason != message:
                task.waiting_reason = message
                self._persist(task_id)

        task_output_dir = os.path.join(self.output_base_dir, task_id)
        os.makedirs(task_output_dir, exist_ok=True)

        def snapshot_reference(image):
            # Freeze once per distinct decoded reference, not once per target/AI round.
            data = encode_snapshot_image(image)
            digest = hashlib.sha256(data).hexdigest()
            directory = Path(task_output_dir) / "references"
            directory.mkdir(exist_ok=True)
            path = directory / f"{digest}.png"
            if not path.exists():
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
            return f"/api/v1/files/{task_id}/references/{digest}.png"

        snapshot = snapshot_reference if req.ai_refinement else None
        try:
            # 1. Load source references and build identity gallery
            # Local model calls are dispatched only after acquiring the shared limiter.
            def prepare_sources():
                # Check if multi-person mapping matrix is provided
                prepared_mappings = []
                if req.person_mappings:
                    for m_item in req.person_mappings:
                        target_img = load_image_from_source(m_item.target_reference_url)
                        target_faces = self.pipeline.detector.detect_faces(target_img, confidence_threshold=0.25)
                        target_faces.sort(key=lambda face: face.bbox[0])
                        target_position = m_item.target_reference_person_index - 1
                        if target_position >= len(target_faces):
                            raise ValueError(
                                f"Target person {m_item.person_index} is missing from its task reference image"
                            )
                        target_identity = self.pipeline.recognizer.extract_embedding(
                            target_img, target_faces[target_position]
                        )
                        m_locations = resolve_source_locations(
                            m_item.source_identity_id, m_item.source_asset_ids, m_item.source_manual_images
                        )
                        m_refs = build_source_references(self.pipeline, m_locations, snapshot)
                        m_gal = (
                            self.pipeline.recognizer.build_gallery_embedding(
                                [r["embedding"] for r in m_refs],
                                [r.get("quality_score", 0.5) for r in m_refs],
                            )
                            if m_refs
                            else None
                        )
                        prepared_mappings.append(
                            {
                                "target_index": m_item.person_index,
                                "source_references": m_refs,
                                "gallery_embedding": m_gal,
                                "gallery_reference_weights": [r.get("quality_score", 0.5) for r in m_refs],
                                "target_embedding": target_identity,
                                "similarity_threshold": req.parameters.similarity_threshold,
                                "source_mode": m_item.source_mode,
                            }
                        )

                # 1. Load source references (for standard single-gallery mode)
                source_refs = []
                gallery_emb = None
                target_emb = None
                source_locations = resolve_source_locations(
                    req.source_identity_id, req.source_asset_ids, req.source_manual_images
                )
                if source_locations:
                    source_refs = build_source_references(self.pipeline, source_locations, snapshot)
                    if source_refs:
                        gallery_emb = self.pipeline.recognizer.build_gallery_embedding(
                            [r["embedding"] for r in source_refs],
                            [r.get("quality_score", 0.5) for r in source_refs],
                        )

                # 2. Extract target reference identity once to anchor across the entire batch
                if req.target_reference_url:
                    tgt_img = load_image_from_source(req.target_reference_url)
                    tgt_faces = self.pipeline.detector.detect_faces(tgt_img, confidence_threshold=0.25)
                    if tgt_faces:
                        tgt_faces.sort(key=lambda f: f.bbox[0])
                        idx = (req.person_index - 1) if (req.person_index and req.person_index <= len(tgt_faces)) else 0
                        target_emb = self.pipeline.recognizer.extract_embedding(tgt_img, tgt_faces[idx])
                elif req.person_index is not None and req.normalized_batch_images:
                    first_img = load_image_from_source(req.normalized_batch_images[0].image_url)
                    first_faces = self.pipeline.detector.detect_faces(first_img, confidence_threshold=0.25)
                    if first_faces:
                        first_faces.sort(key=lambda f: f.bbox[0])
                        idx = min(req.person_index - 1, len(first_faces) - 1)
                        target_emb = self.pipeline.recognizer.extract_embedding(first_img, first_faces[idx])

                return source_refs, gallery_emb, target_emb, prepared_mappings

            (
                source_references,
                gallery_embedding,
                target_embedding,
                prepared_mappings,
            ) = await self.inference_limiter.run(prepare_sources, on_memory_wait=report_memory_wait)

            # 3. Process each image sequentially in thread pool to avoid blocking async event loop
            for idx, batch_image in enumerate(req.normalized_batch_images):
                t_start = time.time()
                img_url = batch_image.image_url
                try:

                    def process_one(batch_image=batch_image, img_url=img_url, idx=idx, t_start=t_start):
                        manual_mask_arr = decode_manual_mask(batch_image.manual_mask)
                        target_img = load_image_from_source(img_url)
                        require_swap_memory(target_img)

                        # Execute pipeline swap
                        def do_process(curr_img, curr_manual_mask=manual_mask_arr):
                            if prepared_mappings:
                                return self.pipeline.process_image_multi_mapping(
                                    image=curr_img,
                                    mappings=prepared_mappings,
                                    identity_blend_strength=req.parameters.identity_blend_strength,
                                    enable_color_transfer=req.parameters.enable_color_transfer,
                                    color_transfer_mode=req.parameters.color_transfer_mode,
                                    color_transfer_ring_strength=req.parameters.color_transfer_ring_strength,
                                    color_transfer_ring_width_px=req.parameters.color_transfer_ring_width_px,
                                    enable_dense_mask=req.parameters.enable_dense_mask,
                                    swapper_model=req.parameters.swapper_model,
                                    allow_research_models=req.parameters.allow_research_models,
                                    manual_mask=curr_manual_mask,
                                    enable_face_restoration=req.parameters.enable_face_restoration,
                                    restoration_fidelity_weight=req.parameters.restoration_fidelity_weight,
                                    restoration_strength=req.parameters.restoration_strength,
                                )
                            return self.pipeline.process_image(
                                image=curr_img,
                                source_references=source_references,
                                gallery_embedding=gallery_embedding,
                                gallery_reference_weights=[r.get("quality_score", 0.5) for r in source_references],
                                target_embedding=target_embedding,
                                similarity_threshold=req.parameters.similarity_threshold,
                                identity_blend_strength=req.parameters.identity_blend_strength,
                                source_mode=req.parameters.source_mode,
                                enable_color_transfer=req.parameters.enable_color_transfer,
                                color_transfer_mode=req.parameters.color_transfer_mode,
                                color_transfer_ring_strength=req.parameters.color_transfer_ring_strength,
                                color_transfer_ring_width_px=req.parameters.color_transfer_ring_width_px,
                                enable_dense_mask=req.parameters.enable_dense_mask,
                                swapper_model=req.parameters.swapper_model,
                                allow_research_models=req.parameters.allow_research_models,
                                manual_mask=curr_manual_mask,
                                enable_face_restoration=req.parameters.enable_face_restoration,
                                restoration_fidelity_weight=req.parameters.restoration_fidelity_weight,
                                restoration_strength=req.parameters.restoration_strength,
                            )

                        swap_res = do_process(target_img)
                        detail = describe_skipped_people(swap_res.person_outcomes)
                        # A completed no-op still has real detection/matching diagnostics. Keep
                        # them on the failed result without publishing an unchanged canvas.
                        # Inline originals remain in the immutable SQLite request for explicit
                        # project recovery; failed-result metadata must not retain another copy.
                        img_res = ImageSwapResult(
                            client_target_id=batch_image.client_target_id,
                            original_url="" if img_url.startswith("data:") else img_url,
                            status="FAILED",
                            faces_detected=swap_res.faces_detected,
                            faces_swapped=swap_res.faces_swapped,
                            swapped_person_indices=swap_res.swapped_person_indices,
                            requested_person_indices=swap_res.requested_person_indices,
                            person_outcomes=swap_res.person_outcomes,
                            matched_similarities=[round(float(s), 4) for s in swap_res.matched_similarities],
                            cost_ms=round((time.time() - t_start) * 1000, 2),
                            model_routing=swap_res.model_routing,
                        )
                        if swap_res.faces_swapped == 0:
                            img_res.error = (
                                detail or f"No visible face swap was produced from {swap_res.faces_detected} faces"
                            )
                        else:
                            # Persist both canvases under the task so history remains reviewable
                            # after an external source URL or browser data URI disappears.
                            original_filename = f"original_{idx + 1:04d}.png"
                            original_path = os.path.join(task_output_dir, original_filename)
                            if not write_image(original_path, target_img):
                                raise OSError(f"Failed to write original image: {original_path}")
                            out_filename = f"res_{idx + 1:04d}.jpg"
                            out_path = os.path.join(task_output_dir, out_filename)
                            if not write_image(out_path, swap_res.output_image):
                                raise OSError(f"Failed to write output image: {out_path}")

                            img_res.original_url = f"/api/v1/files/{task_id}/{original_filename}"
                            img_res.processed_url = f"/api/v1/files/{task_id}/{out_filename}"
                            img_res.status = "PARTIAL" if detail else "SUCCESS"
                            img_res.warning = detail
                            img_res.cost_ms = round((time.time() - t_start) * 1000, 2)
                        return img_res

                    img_res = await self.inference_limiter.run(process_one, on_memory_wait=report_memory_wait)
                except MemoryPressureError:
                    # Do not repeat a timed-out memory wait for every remaining picture. Completed files remain valid.
                    raise
                except Exception as e:
                    cost_ms = round((time.time() - t_start) * 1000, 2)
                    img_res = ImageSwapResult(
                        client_target_id=batch_image.client_target_id,
                        original_url="" if img_url.startswith("data:") else img_url,
                        status="FAILED",
                        cost_ms=cost_ms,
                        error=str(e),
                    )

                task.results.append(img_res)
                task.processed_images += 1
                task.progress = round(task.processed_images / max(task.total_images, 1), 2)
                self._persist(task_id)
                if req.ai_refinement and img_res.processed_url:
                    report_memory_wait(f"第 {idx + 1} 张本地换脸已完成，等待本图 AI 精修结束后继续下一张。")
                    try:
                        if self.refinement_hook is None:
                            raise RuntimeError("Refinement session service unavailable")
                        await self.refinement_hook(
                            task_id,
                            req,
                            img_res,
                            source_references,
                            prepared_mappings,
                            on_session_created=lambda: self._persist(task_id),
                        )
                    except Exception:
                        # A follow-up failure must not discard a successful immutable local result.
                        img_res.refinement_error = "AI 精修未启动；本地结果已保留，可进入精修工作区重试。"
                    finally:
                        report_memory_wait(None)
                    self._persist(task_id)
            task.success_count = sum(result.status in {"SUCCESS", "PARTIAL"} for result in task.results)
            task.failure_count = sum(result.status == "FAILED" for result in task.results)
            has_partial_result = any(result.status == "PARTIAL" for result in task.results)
            task.has_partial_failure = has_partial_result or (task.success_count > 0 and task.failure_count > 0)
            if task.failure_count == task.total_images:
                task.status = TaskStatus.FAILED
                task.error = "All batch images failed"
            elif task.has_partial_failure:
                task.status = TaskStatus.PARTIAL
            else:
                task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC).isoformat()
            self._persist(task_id)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.waiting_reason = None
            task.error = str(e)
            task.success_count = sum(item.status in {"SUCCESS", "PARTIAL"} for item in task.results)
            task.failure_count = sum(item.status == "FAILED" for item in task.results)
            task.has_partial_failure = bool(
                task.success_count and (task.failure_count or task.processed_images < task.total_images)
            )
            task.completed_at = datetime.now(UTC).isoformat()
            self._persist(task_id)

        # Send webhook callback notification if configured
        if req.callback_url:
            wt = asyncio.create_task(self._send_webhook(req.callback_url, task))
            self._background_tasks.add(wt)
            wt.add_done_callback(self._background_tasks.discard)

    async def _send_webhook(self, callback_url: str, task: TaskDetailData):
        """Send a callback only to a currently public destination."""
        try:
            addresses = validate_public_url(callback_url)
            pinned_url, pin_headers, extensions = pin_request_to_addresses(callback_url, addresses)
            task_data = task.model_dump()
            payload = {"code": 200, "data": task_data}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as client:
                response = await client.post(pinned_url, json=payload, headers=pin_headers, extensions=extensions)
                if response.is_redirect:
                    raise ValueError("Webhook redirects are not followed")
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"[Warning] Webhook callback to {callback_url} failed: {exc}")

    def execute_sync_swap(self, req: SyncSwapRequest) -> dict:
        """Executes a synchronous face swap for a single image."""
        t_start = time.time()
        manual_mask_arr = decode_manual_mask(req.parameters.manual_mask)
        # Multi-person mapping mode
        if req.person_mappings:
            prepared_mappings = []
            for m_item in req.person_mappings:
                target_img = load_image_from_source(m_item.target_reference_url)
                target_faces = self.pipeline.detector.detect_faces(target_img, confidence_threshold=0.25)
                target_faces.sort(key=lambda face: face.bbox[0])
                target_position = m_item.target_reference_person_index - 1
                if target_position >= len(target_faces):
                    raise ValueError(f"Target person {m_item.person_index} is missing from its task reference image")
                target_identity = self.pipeline.recognizer.extract_embedding(target_img, target_faces[target_position])
                m_locations = resolve_source_locations(
                    m_item.source_identity_id, m_item.source_asset_ids, m_item.source_manual_images
                )
                m_refs = build_source_references(self.pipeline, m_locations)
                m_gal = (
                    self.pipeline.recognizer.build_gallery_embedding(
                        [r["embedding"] for r in m_refs],
                        [r.get("quality_score", 0.5) for r in m_refs],
                    )
                    if m_refs
                    else None
                )
                prepared_mappings.append(
                    {
                        "target_index": m_item.person_index,
                        "source_references": m_refs,
                        "gallery_embedding": m_gal,
                        "gallery_reference_weights": [r.get("quality_score", 0.5) for r in m_refs],
                        "target_embedding": target_identity,
                        "similarity_threshold": req.parameters.similarity_threshold,
                        "source_mode": m_item.source_mode,
                    }
                )

            target_img = load_image_from_source(req.target_image_url)
            require_swap_memory(target_img)
            swap_res = self.pipeline.process_image_multi_mapping(
                image=target_img,
                mappings=prepared_mappings,
                identity_blend_strength=req.parameters.identity_blend_strength,
                enable_color_transfer=req.parameters.enable_color_transfer,
                color_transfer_mode=req.parameters.color_transfer_mode,
                color_transfer_ring_strength=req.parameters.color_transfer_ring_strength,
                color_transfer_ring_width_px=req.parameters.color_transfer_ring_width_px,
                enable_dense_mask=req.parameters.enable_dense_mask,
                swapper_model=req.parameters.swapper_model,
                allow_research_models=req.parameters.allow_research_models,
                manual_mask=manual_mask_arr,
                enable_face_restoration=req.parameters.enable_face_restoration,
                restoration_fidelity_weight=req.parameters.restoration_fidelity_weight,
                restoration_strength=req.parameters.restoration_strength,
            )
        else:
            # 1. Load source references
            source_locations = resolve_source_locations(
                req.source_identity_id, req.source_asset_ids, req.source_manual_images
            )
            source_refs = build_source_references(self.pipeline, source_locations)

            if not source_refs:
                raise ValueError("No valid faces detected in source reference photos.")

            gallery_embedding = self.pipeline.recognizer.build_gallery_embedding(
                [r["embedding"] for r in source_refs],
                [r.get("quality_score", 0.5) for r in source_refs],
            )

            # 2. Target embedding
            target_img = load_image_from_source(req.target_image_url)
            require_swap_memory(target_img)
            target_emb = None
            if req.target_reference_url:
                tgt_ref_img = load_image_from_source(req.target_reference_url)
                tgt_faces = self.pipeline.detector.detect_faces(tgt_ref_img, confidence_threshold=0.25)
                if tgt_faces:
                    tgt_faces.sort(key=lambda f: f.bbox[0])
                    idx = (req.person_index - 1) if (req.person_index and req.person_index <= len(tgt_faces)) else 0
                    target_emb = self.pipeline.recognizer.extract_embedding(tgt_ref_img, tgt_faces[idx])
            elif req.person_index is not None:
                detected_group_faces = self.pipeline.detector.detect_faces(target_img)
                if len(detected_group_faces) >= req.person_index:
                    detected_group_faces.sort(key=lambda f: f.bbox[0])
                    cand = detected_group_faces[req.person_index - 1]
                    target_emb = self.pipeline.recognizer.extract_embedding(target_img, cand)

            # 3. Process
            swap_res = self.pipeline.process_image(
                image=target_img,
                source_references=source_refs,
                gallery_embedding=gallery_embedding,
                gallery_reference_weights=[r.get("quality_score", 0.5) for r in source_refs],
                target_embedding=target_emb,
                similarity_threshold=req.parameters.similarity_threshold,
                identity_blend_strength=req.parameters.identity_blend_strength,
                source_mode=req.parameters.source_mode,
                enable_color_transfer=req.parameters.enable_color_transfer,
                color_transfer_mode=req.parameters.color_transfer_mode,
                color_transfer_ring_strength=req.parameters.color_transfer_ring_strength,
                color_transfer_ring_width_px=req.parameters.color_transfer_ring_width_px,
                enable_dense_mask=req.parameters.enable_dense_mask,
                swapper_model=req.parameters.swapper_model,
                allow_research_models=req.parameters.allow_research_models,
                manual_mask=manual_mask_arr,
                enable_face_restoration=req.parameters.enable_face_restoration,
                restoration_fidelity_weight=req.parameters.restoration_fidelity_weight,
                restoration_strength=req.parameters.restoration_strength,
            )

        if swap_res.faces_swapped == 0:
            detail = describe_skipped_people(swap_res.person_outcomes)
            raise ValueError(
                detail or f"No visible face swap was produced from {swap_res.faces_detected} detected faces"
            )

        cost_ms = round((time.time() - t_start) * 1000, 2)

        # Save result to sync output directory
        sync_dir = os.path.join(self.output_base_dir, "sync")
        os.makedirs(sync_dir, exist_ok=True)
        out_name = f"sync_{uuid.uuid4().hex[:8]}.jpg"
        out_file = os.path.join(sync_dir, out_name)
        if not write_image(out_file, swap_res.output_image):
            raise OSError(f"Failed to write output image: {out_file}")

        b64_data = encode_image_to_base64(swap_res.output_image) if req.return_base64 else None

        return {
            "processed_url": f"/api/v1/files/sync/{out_name}",
            "processed_base64": b64_data,
            "faces_detected": swap_res.faces_detected,
            "faces_swapped": swap_res.faces_swapped,
            "swapped_person_indices": swap_res.swapped_person_indices,
            "requested_person_indices": swap_res.requested_person_indices,
            "person_outcomes": swap_res.person_outcomes,
            "matched_similarities": [round(float(s), 4) for s in swap_res.matched_similarities],
            "model_routing": swap_res.model_routing,
            "warning": describe_skipped_people(swap_res.person_outcomes),
            "cost_ms": cost_ms,
        }
