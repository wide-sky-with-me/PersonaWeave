"""
Asynchronous Batch Task Manager for PersonaWeave API.
Coordinates background queueing, status tracking, image downloading/decoding,
and webhook callbacks.
"""

import asyncio
import base64
import os
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import cv2
import httpx
import numpy as np

from api.inference_limiter import InferenceLimiter
from api.portrait_library import resolve_asset_ids, resolve_identity_asset_ids
from api.schemas import (
    BatchTaskCreateRequest,
    BatchTaskSummary,
    ImageSwapResult,
    SyncSwapRequest,
    TaskDetailData,
    TaskStatus,
)
from api.task_persistence import SQLiteTaskStore, default_task_db_path
from core.face_metrics import calculate_reference_quality, calculate_yaw_ratio
from core.image_security import decode_data_uri, download_image, resolve_managed_url, validate_public_url
from core.pipeline import PersonaWeavePipeline


class TrustedImagePath(str):
    """Marker for an internal path resolved from an opaque managed asset ID."""


def load_image_from_source(source_str: str, *, trusted_local: bool = False) -> np.ndarray:
    """Load an external image without allowing arbitrary local filesystem reads."""
    if source_str.startswith("data:"):
        return decode_data_uri(source_str)

    managed_path = resolve_managed_url(source_str)
    if managed_path is not None:
        source_str = str(managed_path)
        trusted_local = True

    if source_str.startswith(("http://", "https://")):
        return download_image(source_str)

    if not (trusted_local or isinstance(source_str, TrustedImagePath)):
        raise ValueError("Local filesystem image paths are not accepted by the external API")
    img = cv2.imread(source_str)
    if img is None:
        raise ValueError("Failed to read managed local image")
    if img.shape[0] * img.shape[1] > 40_000_000:
        raise ValueError("Image exceeds the pixel limit")
    return img


def encode_image_to_base64(img: np.ndarray, ext: str = ".jpg") -> str:
    """Encodes BGR numpy image to base64 data URI."""
    success, buffer = cv2.imencode(ext, img)
    if not success:
        raise ValueError("Failed to encode image to buffer.")
    b64_str = base64.b64encode(buffer).decode("utf-8")
    mime = "image/jpeg" if ext.lower() in [".jpg", ".jpeg"] else "image/png"
    return f"data:{mime};base64,{b64_str}"


def describe_skipped_people(person_outcomes: list[dict]) -> str | None:
    """Turn stable per-person reason codes into one concise user-facing warning."""
    reason_text = {
        "no_faces_detected": "未检测到人脸",
        "identity_match_below_threshold": "未找到达到身份阈值的目标脸",
        "source_identity_unavailable": "源身份素材不可用",
        "extreme_pose_without_matching_reference": "目标为极端侧脸，但素材库缺少同方向侧脸参考",
        "no_visible_change": "模型输出未产生足够可见变化",
    }
    skipped = [item for item in person_outcomes if item.get("status") != "SWAPPED"]
    if not skipped:
        return None
    details = [
        f"人物 #{item.get('person_index')}：{reason_text.get(item.get('reason'), item.get('reason', '未完成'))}"
        for item in skipped
    ]
    return "；".join(details)


def resolve_source_locations(
    source_identity_id: str | None,
    source_asset_ids: list[str],
    source_manual_images: list[str],
) -> list[str]:
    """Resolves a person's configured source into concrete loadable locations.

    Managed portrait-library identity/asset IDs resolve to on-disk asset
    paths via SQLite; manual images pass through untouched (URL/base64/local
    path), since only managed library sources are restricted to opaque IDs.
    """
    locations: list[str] = []
    if source_identity_id:
        asset_ids = resolve_identity_asset_ids(source_identity_id)
        if asset_ids is None:
            raise ValueError(f"Unknown portrait library identity id: {source_identity_id}")
        locations.extend(TrustedImagePath(path) for path in resolve_asset_ids(asset_ids))
    if source_asset_ids:
        locations.extend(TrustedImagePath(path) for path in resolve_asset_ids(source_asset_ids))
    locations.extend(source_manual_images)
    return locations


def build_source_references(pipeline: PersonaWeavePipeline, source_locations: list[str]) -> list[dict]:
    """Loads each source location, extracts its face embedding, and packages
    the reference metadata consumed by the swap pipeline."""
    refs = []
    for location in source_locations:
        img = load_image_from_source(location)
        faces = pipeline.detector.detect_faces(img, max_num=1, confidence_threshold=0.25)
        if not faces:
            continue
        emb = pipeline.recognizer.extract_embedding(img, faces[0])
        refs.append(
            {
                "path": location,
                "image": img,
                "face": faces[0],
                "embedding": emb,
                "yaw": calculate_yaw_ratio(faces[0].kps),
                "quality_score": calculate_reference_quality(img, faces[0]),
            }
        )
    return refs


def decode_manual_mask(value: str | None) -> np.ndarray | None:
    """Decode a nonempty manual mask, rejecting malformed or non-image payloads."""
    if not value:
        return None
    encoded = value.split(",", 1)[1] if "," in value else value
    try:
        mask_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("manual_mask must be a valid base64-encoded image") from exc
    mask = cv2.imdecode(np.frombuffer(mask_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None or mask.size == 0:
        raise ValueError("manual_mask must decode to a nonempty image")
    return mask


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
        self._background_tasks: set[asyncio.Task] = set()
        os.makedirs(self.output_base_dir, exist_ok=True)
        self._store = SQLiteTaskStore(db_path or default_task_db_path(output_base_dir))
        self._load_persisted_tasks()

    def _persist(self, task_id: str):
        request = self._requests.get(task_id)
        self._store.save(
            "batch",
            task_id,
            self.tasks[task_id].model_dump(mode="json"),
            request.model_dump(mode="json") if request is not None else None,
        )

    def _load_persisted_tasks(self):
        for state, request_data in self._store.load("batch"):
            try:
                task = TaskDetailData.model_validate(state)
                request = BatchTaskCreateRequest.model_validate(request_data) if request_data else None
            except (TypeError, ValueError):
                continue
            self.tasks[task.task_id] = task
            if request is not None:
                self._requests[task.task_id] = request
            if task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
                task.status = TaskStatus.FAILED
                task.error = "Batch task was interrupted by an application restart; submit it again to retry"
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

        # Run task in background asyncio loop
        t = asyncio.create_task(self._run_batch_task(task_id, req))
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

    def get_task(self, task_id: str) -> TaskDetailData | None:
        """Retrieves task state by ID."""
        return self.tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[TaskDetailData]:
        """List complete recent tasks for backward-compatible API consumers."""
        all_tasks = list(self.tasks.values())
        all_tasks.reverse()
        return all_tasks[:limit]

    def list_task_summaries(self, limit: int = 50) -> list[BatchTaskSummary]:
        """List recent task metadata without returning image URLs or embedded payloads."""
        all_tasks = self.list_tasks(limit)
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

    async def _run_batch_task(self, task_id: str, req: BatchTaskCreateRequest):
        """Worker executing the batch task asynchronously."""
        task = self.tasks[task_id]
        task.status = TaskStatus.PROCESSING
        self._persist(task_id)

        task_output_dir = os.path.join(self.output_base_dir, task_id)
        os.makedirs(task_output_dir, exist_ok=True)

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
                    source_refs = build_source_references(self.pipeline, source_locations)
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
            ) = await self.inference_limiter.run(prepare_sources)

            # 3. Process each image sequentially in thread pool to avoid blocking async event loop
            for idx, batch_image in enumerate(req.normalized_batch_images):
                t_start = time.time()
                img_url = batch_image.image_url
                try:
                    manual_mask_arr = decode_manual_mask(batch_image.manual_mask)
                    target_img = await asyncio.to_thread(load_image_from_source, img_url)

                    # Execute pipeline swap
                    def do_process(curr_img, curr_manual_mask=manual_mask_arr):
                        if prepared_mappings:
                            return self.pipeline.process_image_multi_mapping(
                                image=curr_img,
                                mappings=prepared_mappings,
                                identity_blend_strength=req.parameters.identity_blend_strength,
                                enable_color_transfer=req.parameters.enable_color_transfer,
                                color_transfer_mode=req.parameters.color_transfer_mode,
                                enable_dense_mask=req.parameters.enable_dense_mask,
                                swapper_model=req.parameters.swapper_model,
                                allow_research_models=req.parameters.allow_research_models,
                                manual_mask=curr_manual_mask,
                            )
                        return self.pipeline.process_image(
                            image=curr_img,
                            source_references=source_references,
                            gallery_embedding=gallery_embedding,
                            target_embedding=target_embedding,
                            similarity_threshold=req.parameters.similarity_threshold,
                            identity_blend_strength=req.parameters.identity_blend_strength,
                            source_mode=req.parameters.source_mode,
                            enable_color_transfer=req.parameters.enable_color_transfer,
                            color_transfer_mode=req.parameters.color_transfer_mode,
                            enable_dense_mask=req.parameters.enable_dense_mask,
                            swapper_model=req.parameters.swapper_model,
                            allow_research_models=req.parameters.allow_research_models,
                            manual_mask=curr_manual_mask,
                        )

                    swap_res = await self.inference_limiter.run(do_process, target_img)
                    if swap_res.faces_swapped == 0:
                        detail = describe_skipped_people(swap_res.person_outcomes)
                        raise ValueError(
                            detail or f"No visible face swap was produced from {swap_res.faces_detected} faces"
                        )
                    # Persist both canvases under the task so history remains reviewable
                    # after an external source URL or browser data URI disappears.
                    original_filename = f"original_{idx + 1:04d}.png"
                    original_path = os.path.join(task_output_dir, original_filename)
                    if not cv2.imwrite(original_path, target_img):
                        raise OSError(f"Failed to write original image: {original_path}")
                    out_filename = f"res_{idx + 1:04d}.jpg"
                    out_path = os.path.join(task_output_dir, out_filename)
                    if not cv2.imwrite(out_path, swap_res.output_image):
                        raise OSError(f"Failed to write output image: {out_path}")

                    cost_ms = round((time.time() - t_start) * 1000, 2)
                    warning = describe_skipped_people(swap_res.person_outcomes)
                    img_res = ImageSwapResult(
                        client_target_id=batch_image.client_target_id,
                        original_url=f"/api/v1/files/{task_id}/{original_filename}",
                        processed_url=f"/api/v1/files/{task_id}/{out_filename}",
                        status="PARTIAL" if warning else "SUCCESS",
                        faces_detected=swap_res.faces_detected,
                        faces_swapped=swap_res.faces_swapped,
                        swapped_person_indices=swap_res.swapped_person_indices,
                        requested_person_indices=swap_res.requested_person_indices,
                        person_outcomes=swap_res.person_outcomes,
                        matched_similarities=[round(float(s), 4) for s in swap_res.matched_similarities],
                        cost_ms=cost_ms,
                        model_routing=swap_res.model_routing,
                        warning=warning,
                    )
                except Exception as e:
                    cost_ms = round((time.time() - t_start) * 1000, 2)
                    img_res = ImageSwapResult(
                        client_target_id=batch_image.client_target_id,
                        original_url=img_url,
                        status="FAILED",
                        cost_ms=cost_ms,
                        error=str(e),
                    )

                task.results.append(img_res)
                task.processed_images += 1
                task.progress = round(task.processed_images / max(task.total_images, 1), 2)
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
            task.error = str(e)
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
            validate_public_url(callback_url)
            task_data = task.model_dump()
            payload = {"code": 200, "data": task_data}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(callback_url, json=payload)
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
                        "target_embedding": target_identity,
                        "similarity_threshold": req.parameters.similarity_threshold,
                        "source_mode": m_item.source_mode,
                    }
                )

            target_img = load_image_from_source(req.target_image_url)
            swap_res = self.pipeline.process_image_multi_mapping(
                image=target_img,
                mappings=prepared_mappings,
                identity_blend_strength=req.parameters.identity_blend_strength,
                enable_color_transfer=req.parameters.enable_color_transfer,
                color_transfer_mode=req.parameters.color_transfer_mode,
                enable_dense_mask=req.parameters.enable_dense_mask,
                swapper_model=req.parameters.swapper_model,
                allow_research_models=req.parameters.allow_research_models,
                manual_mask=manual_mask_arr,
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
                target_embedding=target_emb,
                similarity_threshold=req.parameters.similarity_threshold,
                identity_blend_strength=req.parameters.identity_blend_strength,
                source_mode=req.parameters.source_mode,
                enable_color_transfer=req.parameters.enable_color_transfer,
                color_transfer_mode=req.parameters.color_transfer_mode,
                enable_dense_mask=req.parameters.enable_dense_mask,
                swapper_model=req.parameters.swapper_model,
                allow_research_models=req.parameters.allow_research_models,
                manual_mask=manual_mask_arr,
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
        if not cv2.imwrite(out_file, swap_res.output_image):
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
