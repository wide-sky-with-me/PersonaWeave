import asyncio
import base64
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageOps

from personaweave.assets import AssetResolver
from personaweave.domain import (
    AnalysisRequest,
    AnalysisResult,
    BatchStatus,
    JobPhase,
    JobStage,
    JobStatus,
    FaceObservation,
    NormalizedBoundingBox,
    PersonDetectionAudit,
    SwapRequest,
    SwapResult,
    ValidationRequest,
    ValidationResult,
    ValidationReviewerVerdict,
)
from personaweave.formal import validate_swap_request_scope
from personaweave.providers.gateway import InferenceGateway, ProviderNotConfigured
from personaweave.providers.base import ValidationProvider
from personaweave.storage import Repository
from personaweave.workflow import InvalidTransition, WorkflowService
from personaweave.validation import compare_source_result
from personaweave.provider_errors import ProviderExecutionError


class StageExecutionError(RuntimeError):
    pass


class PipelineExecutor:
    def __init__(
        self,
        repository: Repository,
        workflow: WorkflowService,
        gateway: InferenceGateway,
        assets: AssetResolver,
        concurrency: int = 2,
        advisory_validator: ValidationProvider | None = None,
        quality_validators: tuple[ValidationProvider, ValidationProvider] | None = None,
        advisory_preview_max_edge: int = 1600,
    ):
        self.repository = repository
        self.workflow = workflow
        self.gateway = gateway
        self.assets = assets
        self.semaphore = asyncio.Semaphore(concurrency)
        self.advisory_validator = advisory_validator
        if quality_validators is not None and quality_validators[0] is quality_validators[1]:
            raise ValueError("quality validators must be independent provider instances")
        self.quality_validators = quality_validators
        self.advisory_preview_max_edge = advisory_preview_max_edge


    async def execute_stage(self, batch_id: str, stage: JobStage) -> dict[str, Any]:
        expected_status = {
            JobStage.ANALYZE: BatchStatus.ANALYZING,
            JobStage.SWAP: BatchStatus.SWAPPING,
            JobStage.VALIDATE: BatchStatus.VALIDATING,
        }[stage]
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != expected_status:
            raise InvalidTransition(f"cannot execute {stage} from {batch['status']}")
        if not self.gateway.configured:
            raise ProviderNotConfigured("remote inference gateway is not configured")

        jobs = self.repository.current_contract_stage_jobs(batch_id, stage)
        queued = [job for job in jobs if job["status"] == JobStatus.QUEUED]
        if not queued and any(job["status"] == JobStatus.FAILED for job in jobs):
            raise StageExecutionError("stage has failed jobs; retry them before execution")
        await asyncio.gather(*(self._execute_job(job, stage) for job in queued))

        latest = self.repository.current_contract_stage_jobs(batch_id, stage)
        failed = [job for job in latest if job["status"] == JobStatus.FAILED]
        needs_review = [job for job in latest if job["status"] == JobStatus.NEEDS_REVIEW]
        if failed:
            self.repository.set_batch_status(batch_id, BatchStatus.FAILED)
            return {
                "batch_id": batch_id,
                "stage": stage,
                "completed": False,
                "failed_job_ids": [job["id"] for job in failed],
            }
        if needs_review:
            if stage in {JobStage.SWAP, JobStage.VALIDATE}:
                self.repository.set_batch_status(batch_id, BatchStatus.REVIEW)
            return {
                "batch_id": batch_id,
                "stage": stage,
                "completed": False,
                "requires_review": True,
                "needs_review_job_ids": [job["id"] for job in needs_review],
            }
        if not latest or any(job["status"] != JobStatus.SUCCEEDED for job in latest):
            raise StageExecutionError("stage did not reach a terminal state")

        if stage is JobStage.ANALYZE:
            analysis_outputs = [job["output"] for job in latest]
            await self._cluster_analysis_people(analysis_outputs)
            self.workflow.complete_analysis(batch_id, analysis_outputs)
        elif stage is JobStage.SWAP:
            self.workflow.complete_swap(
                batch_id,
                [SwapResult.model_validate(job["output"]) for job in latest],
            )
        else:
            self.workflow.complete_validation(
                batch_id,
                [ValidationResult.model_validate(job["output"]) for job in latest],
            )
        return {"batch_id": batch_id, "stage": stage, "completed": True}

    async def fuse_existing_people(self, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
            raise InvalidTransition(
                f"cannot fuse extracted people from {batch['status']}"
            )
        people = self.repository.list_persons(batch_id)
        before_count = len(people)
        if before_count < 2:
            return {
                "batch_id": batch_id,
                "before_count": before_count,
                "after_count": before_count,
                "merged_count": 0,
            }

        results_by_asset: dict[str, dict[str, Any]] = {}
        face_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for person in people:
            for face in person["faces"]:
                face_copy = {
                    **face,
                    "person_id": person["id"],
                    "external_face_id": face["external_face_id"],
                    "bbox": face["bbox"],
                }
                results_by_asset.setdefault(
                    face["asset_id"],
                    {"asset_id": face["asset_id"], "faces": []},
                )["faces"].append(face_copy)
                face_lookup[(face["asset_id"], face["external_face_id"])] = face_copy

        results = list(results_by_asset.values())
        await self._cluster_analysis_people(results)
        groups: dict[str, set[str]] = {}
        person_groups: dict[str, set[str]] = {}
        metadata_updates: list[tuple[str, float]] = []
        for face in face_lookup.values():
            group_key = face.get("suggested_person_key")
            confidence = face.get("identity_confidence")
            if not group_key or confidence is None:
                raise StageExecutionError("person fusion returned incomplete face metadata")
            groups.setdefault(group_key, set()).add(face["person_id"])
            person_groups.setdefault(face["person_id"], set()).add(group_key)
            metadata_updates.append((face["id"], confidence))

        ambiguous_people = {
            person_id for person_id, group_keys in person_groups.items() if len(group_keys) > 1
        }
        merged_count = 0
        for person_ids in groups.values():
            merge_ids = sorted(person_ids - ambiguous_people)
            if len(merge_ids) > 1:
                self.repository.merge_persons(batch_id, merge_ids)
                merged_count += len(merge_ids) - 1
        self.repository.update_face_fusion_metadata(metadata_updates)
        after_count = len(self.repository.list_persons(batch_id))
        return {
            "batch_id": batch_id,
            "before_count": before_count,
            "after_count": after_count,
            "merged_count": merged_count,
            "ambiguous_person_ids": sorted(ambiguous_people),
        }

    async def _cluster_analysis_people(self, results: list[dict[str, Any]]) -> None:
        appearance_analyzer = getattr(
            self.advisory_validator,
            "analyze_person_appearances",
            None,
        )
        if (
            sum(len(result.get("faces", [])) for result in results) < 2
            and appearance_analyzer is None
        ):
            return
        observations = []
        observation_faces: dict[str, dict[str, Any]] = {}
        appearance_requests: list[tuple[str, list[dict[str, Any]]]] = []
        for result in results:
            asset = self.repository.get_asset(result["asset_id"])
            try:
                path = self.assets.local_path(asset["storage_uri"])
            except ValueError:
                path = None
            if path is None or not path.is_file():
                continue
            with Image.open(path) as image:
                image.seek(0)
                source = ImageOps.exif_transpose(image).convert("RGB")
            per_image_observations = []
            for index, face in enumerate(result.get("faces", []), start=1):
                bbox = face["bbox"]
                padding_x = round(bbox["width"] * 0.35)
                padding_y = round(bbox["height"] * 0.35)
                crop = source.crop((
                    max(0, bbox["x"] - padding_x),
                    max(0, bbox["y"] - padding_y),
                    min(source.width, bbox["x"] + bbox["width"] + padding_x),
                    min(source.height, bbox["y"] + bbox["height"] + padding_y),
                ))
                crop.thumbnail((512, 512), Image.Resampling.LANCZOS)
                output = BytesIO()
                crop.save(output, format="JPEG", quality=88, optimize=True)
                code = f"{asset['display_code']}-F{index:02d}"
                per_image_observations.append({
                    "code": code,
                    "bbox": bbox,
                    "normalized_bbox": {
                        "x": round(bbox["x"] / source.width, 6),
                        "y": round(bbox["y"] / source.height, 6),
                        "width": round(bbox["width"] / source.width, 6),
                        "height": round(bbox["height"] / source.height, 6),
                    },
                    "source_dimensions": {"width": source.width, "height": source.height},
                })
                observations.append({
                    "code": code,
                    "crop_uri": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode(),
                })
                observation_faces[code] = face
            missing_appearance_observations = [
                observation
                for observation in per_image_observations
                if not any(
                    (observation_faces[observation["code"]].get("appearance") or {}).values()
                )
            ]
            if missing_appearance_observations and appearance_analyzer is not None:
                preview = source.copy()
                preview.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                preview_output = BytesIO()
                preview.save(preview_output, format="JPEG", quality=90, optimize=True)
                preview_uri = (
                    "data:image/jpeg;base64,"
                    + base64.b64encode(preview_output.getvalue()).decode()
                )
                appearance_requests.append((preview_uri, missing_appearance_observations))
        if appearance_requests and appearance_analyzer is not None:
            async def analyze_appearances(
                source_uri: str,
                missing_observations: list[dict[str, Any]],
            ) -> dict[str, Any]:
                try:
                    async with self.semaphore:
                        appearances = await appearance_analyzer(
                            source_uri=source_uri,
                            observations=missing_observations,
                        )
                except Exception as error:
                    detail = str(error).strip() or type(error).__name__
                    raise StageExecutionError(
                        f"source-person appearance analysis failed: {detail}"
                    ) from error
                if set(appearances) != {
                    observation["code"] for observation in missing_observations
                }:
                    raise StageExecutionError(
                        "source-person appearance analysis returned incomplete observations"
                    )
                return appearances

            appearance_results = await asyncio.gather(*(
                analyze_appearances(source_uri, missing_observations)
                for source_uri, missing_observations in appearance_requests
            ))
            for appearances in appearance_results:
                for code, appearance in appearances.items():
                    observation_faces[code]["appearance"] = (
                        appearance.model_dump(mode="json")
                        if hasattr(appearance, "model_dump")
                        else appearance
                    )
        if len(observations) < 2:
            return
        clusterer = getattr(self.advisory_validator, "cluster_person_observations", None)
        if clusterer is None:
            raise StageExecutionError(
                "cross-image person fusion is required but no multimodal fusion provider is configured"
            )
        try:
            assignments = await clusterer(observations)
        except Exception as error:
            raise StageExecutionError(f"cross-image person fusion failed: {error}") from error
        if set(assignments) != set(observation_faces):
            raise StageExecutionError("cross-image person fusion returned incomplete assignments")
        for code, (group_key, confidence) in assignments.items():
            face = observation_faces[code]
            face["suggested_person_key"] = group_key
            face["identity_confidence"] = confidence
            face["needs_confirmation"] = True

    async def _execute_job(self, job: dict[str, Any], stage: JobStage) -> None:
        async with self.semaphore:
            initial_progress = {
                JobStage.ANALYZE: (JobPhase.ANALYZING, "正在分析图片人物"),
                JobStage.SWAP: (JobPhase.PLANNING, "正在生成首轮编辑计划"),
                JobStage.VALIDATE: (JobPhase.VALIDATING, "正在校验生成结果"),
            }[stage]
            self.repository.mark_job_running(
                job["id"],
                phase=initial_progress[0],
                message=initial_progress[1],
            )
            try:
                output = await self._dispatch(stage, job["input"], job_id=job["id"])
                status = JobStatus.SUCCEEDED
                if stage is JobStage.SWAP:
                    generation_review = output.setdefault("generation_review", {})
                    review_reasons = tuple(
                        sorted(
                            {
                                str(reason)[:80]
                                for reason in job["input"].get("review_reasons", [])
                            }
                        )[:8]
                    )
                    if review_reasons:
                        generation_review["required_review_reasons"] = list(
                            review_reasons
                        )
                        if "reference_insufficiency" in review_reasons:
                            reviewer_passed = generation_review.get("passed") is True
                            reviewer_stop_reason = str(
                                generation_review.get("stop_reason") or "not_reported"
                            )[:80]
                            generation_review["stop_reason"] = "reference_insufficiency"
                            generation_review["reference_coverage"] = {
                                "requires_manual_review": True,
                                "reason": "reference_insufficiency",
                                "reviewer_passed": reviewer_passed,
                                "reviewer_stop_reason": reviewer_stop_reason,
                                "summary": (
                                    "The confirmed reference evidence was insufficient "
                                    "for at least one source occurrence."
                                ),
                            }
                        status = JobStatus.NEEDS_REVIEW
                    if generation_review.get("stop_reason") in {
                        "review_unavailable",
                        "review_incomplete",
                        "max_iterations_reached",
                    }:
                        status = JobStatus.NEEDS_REVIEW
                if stage is JobStage.VALIDATE and (
                    output.get("passed") is not True
                    or output.get("review_reasons")
                ):
                    status = JobStatus.NEEDS_REVIEW
                if stage is JobStage.SWAP:
                    candidate = self.repository.upsert_swap_result(
                        batch_id=job["batch_id"],
                        source_asset_id=job["item_key"],
                        storage_uri=output["output_uri"],
                        sha256=output["output_sha256"],
                    )
                    output["result_asset_id"] = candidate["id"]
                self.repository.complete_job(
                    job["id"],
                    status=status,
                    provider=output["provider"],
                    output=output,
                )
            except ProviderExecutionError as error:
                self.repository.fail_job(
                    job["id"],
                    error.error.code,
                    error.error.message,
                )
            except Exception as error:
                code = self._error_code(error)
                self.repository.fail_job(job["id"], code, str(error))

    async def _dispatch(
        self,
        stage: JobStage,
        payload: dict[str, Any],
        *,
        job_id: str,
    ) -> dict[str, Any]:
        if stage is JobStage.ANALYZE:
            request = AnalysisRequest.model_validate(payload)
            asset = self.repository.get_asset(request.asset_id)
            request.asset_uri = self.assets.for_remote(request.asset_uri, asset["media_type"])
            analysis_result = await self.gateway.analyze(request)
            if analysis_result.asset_id != request.asset_id:
                raise ValueError("analysis provider returned a mismatched asset_id")
            analysis_result = await self._audit_and_recover_faces(request, analysis_result)
            return analysis_result.model_dump(mode="json")
        if stage is JobStage.SWAP:
            request = SwapRequest.model_validate(payload)
            validate_swap_request_scope(request)
            source = self.repository.get_asset(request.asset_id)
            request.asset_uri = self.assets.for_remote(request.asset_uri, source["media_type"])
            for operation in request.replacements:
                operation.reference_uris = [
                    self.assets.for_remote(reference_uri) for reference_uri in operation.reference_uris
                ]
            advisory_source_uri = self._advisory_preview_uri(request.asset_uri)
            advisory_request = self._advisory_request_with_coordinate_aids(
                request,
                advisory_source_uri,
            )
            for operation in advisory_request.replacements:
                operation.reference_uris = [
                    self._advisory_preview_uri(reference_uri)
                    for reference_uri in operation.reference_uris
                ]
            planner = getattr(self.advisory_validator, "plan_numbered_image_edit", None)
            if planner is None:
                raise ProviderNotConfigured(
                    "multimodal numbered edit planner is required before image generation"
                )
            self.repository.update_job_progress(
                job_id,
                phase=JobPhase.PLANNING,
                message="正在由多模态模型规划首轮编辑",
                current_iteration=0,
                total_iterations=request.max_iterations,
            )
            try:
                plan = await planner(
                    request=advisory_request,
                    source_uri=advisory_source_uri,
                )
            except httpx.TimeoutException as error:
                raise httpx.ReadTimeout(
                    "多模态编辑规划超时，尚未开始本张图片的生图",
                ) from error
            request.adaptive_instruction = (
                "MULTIMODAL SOURCE-AWARE EDIT PLAN:\n"
                f"{plan.edit_prompt}"
            )
            original_adaptive_instruction = request.adaptive_instruction
            final_review: dict[str, Any] = {}
            result: SwapResult | None = None
            completed_iterations = 0
            for iteration in range(1, request.max_iterations + 1):
                request.iteration = iteration
                self.repository.update_job_progress(
                    job_id,
                    phase=JobPhase.GENERATING,
                    message=f"第 {iteration}/{request.max_iterations} 轮：云端生图",
                    current_iteration=iteration,
                    total_iterations=request.max_iterations,
                )
                result = await self.gateway.swap(request)
                completed_iterations = iteration
                if result.source_asset_id != request.asset_id:
                    raise ValueError("swap provider returned a mismatched source_asset_id")
                reviewer = getattr(self.advisory_validator, "review_numbered_image_edit", None)
                if reviewer is None:
                    final_review = {
                        "passed": False,
                        "summary": "生成后复核不可用：未配置多模态复核器",
                        "stop_reason": "review_unavailable",
                    }
                    break
                try:
                    self.repository.update_job_progress(
                        job_id,
                        phase=JobPhase.REVIEWING,
                        message=f"第 {iteration}/{request.max_iterations} 轮：多模态结果复核",
                        current_iteration=iteration,
                        total_iterations=request.max_iterations,
                    )
                    advisory_request.adaptive_instruction = request.adaptive_instruction
                    advisory_request.iteration = iteration
                    review = await reviewer(
                        request=advisory_request,
                        source_uri=advisory_source_uri,
                        result_uri=self._advisory_preview_uri(result.output_uri),
                        iteration=iteration,
                    )
                except Exception as error:
                    detail = (
                        "多模态结果复核超时，已停止本张图片的后续付费轮次"
                        if isinstance(error, httpx.TimeoutException)
                        else str(error).strip() or type(error).__name__
                    )
                    final_review = {
                        "passed": False,
                        "summary": f"生成后复核不可用：{detail}",
                        "stop_reason": "review_unavailable",
                    }
                    break
                final_review = {
                    **review.model_dump(mode="json"),
                    "stop_reason": "review_passed" if review.passed else "max_iterations_reached",
                }
                if review.passed or iteration >= request.max_iterations:
                    break
                if not review.revised_edit_prompt:
                    final_review["stop_reason"] = "review_incomplete"
                    break
                request.adaptive_instruction = (
                    f"{original_adaptive_instruction}\n\n"
                    "MULTIMODAL REVIEW REVISED INSTRUCTION:\n"
                    f"{review.revised_edit_prompt}"
                )

            if result is None:
                raise ValueError("swap provider returned no result")
            self.repository.update_job_progress(
                job_id,
                phase=JobPhase.MATERIALIZING,
                message="正在保存最终候选结果",
                current_iteration=completed_iterations,
                total_iterations=request.max_iterations,
            )
            uri, digest = self.assets.materialize_result(
                request.batch_id,
                request.asset_id,
                result.output_uri,
            )
            result.output_uri = uri
            if digest != "remote-unverified":
                if result.output_sha256 not in {"remote-unverified", digest}:
                    raise ValueError("swap provider output hash does not match returned image")
                result.output_sha256 = digest
            elif result.output_sha256 == "remote-unverified":
                raise ValueError("remote swap result requires a provider-supplied sha256")
            result.completed_iterations = completed_iterations
            result.generation_plan = plan.model_dump(mode="json")
            result.generation_review = final_review
            result.review_reasons = request.review_reasons
            return result.model_dump(mode="json")

        request = ValidationRequest.model_validate(payload)
        source_comparison = compare_source_result(request, self.assets, self.repository)
        request.source_uri = self.assets.for_remote(request.source_uri)
        request.result_uri = self.assets.for_remote(request.result_uri)
        for subject in request.expected_subjects:
            subject.reference_uris = [
                self.assets.for_remote(reference_uri) for reference_uri in subject.reference_uris
            ]
        for peer in request.peer_results:
            peer.result_uri = self.assets.for_remote(peer.result_uri)
        if self.quality_validators is not None:
            reviewer_results = await asyncio.gather(
                *(
                    validator.validate(request.model_copy(deep=True))
                    for validator in self.quality_validators
                )
            )
            for reviewer_result in reviewer_results:
                if reviewer_result.result_asset_id != request.result_asset_id:
                    raise ValueError(
                        "quality validator returned a mismatched result_asset_id"
                    )
            reviewer_verdicts = [
                ValidationReviewerVerdict(
                    reviewer_id=reviewer_id,
                    provider=reviewer_result.provider,
                    passed=reviewer_result.passed,
                    checks=reviewer_result.checks,
                    reasons=reviewer_result.reasons,
                )
                for reviewer_id, reviewer_result in zip(
                    ("reviewer_a", "reviewer_b"),
                    reviewer_results,
                    strict=True,
                )
            ]
            validation_result = ValidationResult(
                result_asset_id=request.result_asset_id,
                passed=all(verdict.passed for verdict in reviewer_verdicts),
                provider="+".join(
                    verdict.provider for verdict in reviewer_verdicts
                ),
                checks={
                    f"{verdict.reviewer_id}.{key}": value
                    for verdict in reviewer_verdicts
                    for key, value in verdict.checks.items()
                },
                reasons=[
                    f"{verdict.reviewer_id}: {reason}"
                    for verdict in reviewer_verdicts
                    for reason in verdict.reasons
                ],
                reviewer_verdicts=reviewer_verdicts,
            )
        else:
            raise ProviderNotConfigured(
                "two distinct quality reviewer models are required for validation"
            )
        validation_result = ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=validation_result.passed and source_comparison.passed,
            provider=f"{validation_result.provider}+{source_comparison.provider}",
            checks={**validation_result.checks, **source_comparison.checks},
            reasons=[*validation_result.reasons, *source_comparison.reasons],
            reviewer_verdicts=validation_result.reviewer_verdicts,
        )
        if validation_result.result_asset_id != request.result_asset_id:
            raise ValueError("validation provider returned a mismatched result_asset_id")
        validation_result.review_reasons = request.review_reasons
        return validation_result.model_dump(mode="json")

    def _advisory_preview_uri(self, uri: str) -> str:
        """Shrink image data sent to planning/review models without touching generation inputs."""
        if not uri.startswith("data:image/") or "," not in uri:
            return uri
        try:
            _, encoded = uri.split(",", 1)
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                preview = ImageOps.exif_transpose(image).convert("RGB")
                preview.thumbnail(
                    (self.advisory_preview_max_edge, self.advisory_preview_max_edge),
                    Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                preview.save(output, format="JPEG", quality=85, optimize=True)
        except Exception:
            return uri
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")

    async def _audit_and_recover_faces(
        self,
        request: AnalysisRequest,
        result: AnalysisResult,
    ) -> AnalysisResult:
        auditor = getattr(self.advisory_validator, "audit_person_detection", None)
        if auditor is None:
            result.detection_audit = PersonDetectionAudit(
                available=False,
                complete=False,
                detected_face_count=len(result.faces),
                visible_face_count=None,
                requires_manual_review=True,
                summary="未配置多模态人物完整性检查，请人工确认是否漏检。",
            )
            return result
        dimensions = self._data_image_dimensions(request.asset_uri)
        if dimensions is None:
            result.detection_audit = PersonDetectionAudit(
                available=False,
                complete=False,
                detected_face_count=len(result.faces),
                visible_face_count=None,
                requires_manual_review=True,
                summary="无法读取原图尺寸，人物检测完整性需要人工确认。",
            )
            return result
        width, height = dimensions
        detections = self._normalized_detection_payload(result.faces, width, height)
        source_preview = self._advisory_preview_uri(request.asset_uri)
        annotated_preview = self._annotated_detection_preview_uri(
            request.asset_uri,
            result.faces,
        )
        try:
            audit = await auditor(
                source_uri=source_preview,
                annotated_uri=annotated_preview,
                detections=detections,
            )
            audit = PersonDetectionAudit.model_validate(audit)
        except Exception as error:
            result.detection_audit = PersonDetectionAudit(
                available=False,
                complete=False,
                detected_face_count=len(result.faces),
                visible_face_count=None,
                requires_manual_review=True,
                summary=(
                    "多模态人物完整性检查不可用，请人工确认是否漏检："
                    f"{str(error).strip() or type(error).__name__}"
                )[:1000],
            )
            return result

        missing_regions = [region for region in audit.missing_regions if region.confidence >= 0.6]
        if audit.complete and not missing_regions:
            result.detection_audit = audit.model_copy(update={
                "detected_face_count": len(result.faces),
                "requires_manual_review": False,
            })
            return result
        if not missing_regions:
            result.detection_audit = audit.model_copy(update={
                "complete": False,
                "detected_face_count": len(result.faces),
                "requires_manual_review": True,
                "summary": (
                    audit.summary or "多模态检查无法确认人物完整性，且未返回可复检区域。"
                ),
            })
            return result

        targeted_request = AnalysisRequest(
            batch_id=request.batch_id,
            asset_id=request.asset_id,
            asset_uri=request.asset_uri,
            analysis_pass="targeted",
            region_hints=[region.bbox for region in missing_regions],
        )
        targeted = await self.gateway.analyze(targeted_request)
        if targeted.asset_id != request.asset_id:
            raise ValueError("targeted analysis provider returned a mismatched asset_id")
        primary_faces = list(result.faces)
        merged_faces = self._merge_face_observations(primary_faces, targeted.faces)
        recovered_faces = [
            face
            for face in merged_faces
            if all(self._bbox_iou(face.bbox, primary.bbox) < 0.35 for primary in primary_faces)
        ]
        recovered_regions = sum(
            1
            for region in missing_regions
            if any(self._normalized_region_matches_face(region.bbox, face, width, height) for face in recovered_faces)
        )
        complete = recovered_regions == len(missing_regions)
        result.faces = merged_faces
        result.provider = f"{result.provider}+{targeted.provider}"
        if targeted.model and targeted.model != result.model:
            result.model = "+".join(item for item in (result.model, targeted.model) if item)
        result.detection_audit = audit.model_copy(update={
            "complete": complete,
            "detected_face_count": len(merged_faces),
            "requires_manual_review": not complete,
            "summary": (
                f"多模态检查提出 {len(missing_regions)} 个漏检区域；"
                f"本地定向复检找回 {recovered_regions} 个。"
                + ("检测已补全。" if complete else "仍需人工补框。")
            ),
        })
        return result

    @staticmethod
    def _normalized_detection_payload(
        faces: list[FaceObservation],
        width: int,
        height: int,
    ) -> list[dict[str, object]]:
        return [
            {
                "code": f"D{index:02d}",
                "bbox": {
                    "x": round(face.bbox.x / width, 6),
                    "y": round(face.bbox.y / height, 6),
                    "width": round(face.bbox.width / width, 6),
                    "height": round(face.bbox.height / height, 6),
                },
                "confidence": round(face.detection_confidence, 6),
            }
            for index, face in enumerate(faces, start=1)
        ]

    def _annotated_detection_preview_uri(
        self,
        uri: str,
        faces: list[FaceObservation],
    ) -> str:
        if not uri.startswith("data:image/") or "," not in uri:
            return uri
        try:
            _, encoded = uri.split(",", 1)
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                source = ImageOps.exif_transpose(image).convert("RGB")
            source_width, source_height = source.size
            source.thumbnail(
                (self.advisory_preview_max_edge, self.advisory_preview_max_edge),
                Image.Resampling.LANCZOS,
            )
            scale_x = source.width / source_width
            scale_y = source.height / source_height
            drawing = ImageDraw.Draw(source)
            line_width = max(2, round(max(source.size) / 500))
            for index, face in enumerate(faces, start=1):
                left = round(face.bbox.x * scale_x)
                top = round(face.bbox.y * scale_y)
                right = round((face.bbox.x + face.bbox.width) * scale_x)
                bottom = round((face.bbox.y + face.bbox.height) * scale_y)
                drawing.rectangle((left, top, right, bottom), outline=(0, 230, 120), width=line_width)
                drawing.text((left + line_width, max(0, top - 12)), f"D{index:02d}", fill=(0, 255, 140))
            output = BytesIO()
            source.save(output, format="JPEG", quality=88, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except Exception:
            return self._advisory_preview_uri(uri)

    @classmethod
    def _merge_face_observations(
        cls,
        primary: list[FaceObservation],
        secondary: list[FaceObservation],
    ) -> list[FaceObservation]:
        selected: list[FaceObservation] = []
        for candidate in sorted(
            [*primary, *secondary],
            key=lambda face: face.detection_confidence,
            reverse=True,
        ):
            if all(cls._bbox_iou(candidate.bbox, kept.bbox) < 0.35 for kept in selected):
                selected.append(candidate.model_copy(deep=True))
        for index, face in enumerate(selected):
            face.external_face_id = f"face-{index}"
        return selected

    @staticmethod
    def _bbox_iou(left: Any, right: Any) -> float:
        left_x2 = left.x + left.width
        left_y2 = left.y + left.height
        right_x2 = right.x + right.width
        right_y2 = right.y + right.height
        intersection_width = max(0, min(left_x2, right_x2) - max(left.x, right.x))
        intersection_height = max(0, min(left_y2, right_y2) - max(left.y, right.y))
        intersection = intersection_width * intersection_height
        union = left.width * left.height + right.width * right.height - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _normalized_region_matches_face(
        region: NormalizedBoundingBox,
        face: FaceObservation,
        width: int,
        height: int,
    ) -> bool:
        center_x = (face.bbox.x + face.bbox.width / 2) / width
        center_y = (face.bbox.y + face.bbox.height / 2) / height
        padding_x = region.width * 0.35
        padding_y = region.height * 0.35
        return (
            max(0.0, region.x - padding_x)
            <= center_x
            <= min(1.0, region.x + region.width + padding_x)
            and max(0.0, region.y - padding_y)
            <= center_y
            <= min(1.0, region.y + region.height + padding_y)
        )

    def _advisory_request_with_coordinate_aids(
        self,
        request: SwapRequest,
        preview_uri: str,
    ) -> SwapRequest:
        advisory = request.model_copy(deep=True)
        source_dimensions = self._data_image_dimensions(request.asset_uri)
        preview_dimensions = self._data_image_dimensions(preview_uri)
        if source_dimensions is None or preview_dimensions is None:
            return advisory
        source_width, source_height = source_dimensions
        preview_width, preview_height = preview_dimensions
        scale_x = preview_width / source_width
        scale_y = preview_height / source_height
        coordinate_aids: list[str] = []
        for operation in advisory.replacements:
            bbox = operation.bbox
            preview_bbox = {
                "x": round(bbox.x * scale_x),
                "y": round(bbox.y * scale_y),
                "width": max(1, round(bbox.width * scale_x)),
                "height": max(1, round(bbox.height * scale_y)),
            }
            coordinate_aids.append(
                "Planner/reviewer coordinate aid only for face "
                f"{operation.face_instance_id}: the generator must retain the original bbox "
                f"({bbox.x},{bbox.y},{bbox.width},{bbox.height}) on the "
                f"{source_width}x{source_height} source; on the resized "
                f"{preview_width}x{preview_height} multimodal preview it appears at "
                f"({preview_bbox['x']},{preview_bbox['y']},{preview_bbox['width']},"
                f"{preview_bbox['height']})."
            )
        advisory.adaptive_instruction = "\n".join(
            item
            for item in (advisory.adaptive_instruction, *coordinate_aids)
            if item
        )
        return advisory

    @staticmethod
    def _data_image_dimensions(uri: str) -> tuple[int, int] | None:
        if not uri.startswith("data:image/") or "," not in uri:
            return None
        try:
            _, encoded = uri.split(",", 1)
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                oriented = ImageOps.exif_transpose(image)
                return oriented.size
        except Exception:
            return None

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, ProviderNotConfigured):
            return "provider_not_configured"
        if isinstance(error, httpx.TimeoutException):
            return "provider_timeout"
        if isinstance(error, httpx.HTTPStatusError):
            return f"provider_http_{error.response.status_code}"
        return "provider_error"
