import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from personaweave.assets import AssetResolver, UnsafeAssetPath
from personaweave.domain import (
    AnalysisRequest,
    AnalysisResult,
    AttributePermission,
    CanonicalMaskEvidence,
    BatchStatus,
    BoundingBox,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledOperation,
    FaceObservation,
    JobPhase,
    JobStage,
    JobStatus,
    MissingFaceRegion,
    NormalizedBoundingBox,
    PersonAppearance,
    PersonDetectionAudit,
    SemanticMaskContour,
    SemanticMaskProposal,
    SourceAssetEvidence,
    SwapOperation,
    SwapRequest,
    SwapResult,
    ValidationRequest,
    ValidationResult,
)
from personaweave.executor import PipelineExecutor, StageExecutionError
from personaweave.formal_masks import (
    CompiledMaskArtifact,
    FormalCompilationResult,
    FormalMaskFaceScope,
    validate_and_rasterize_semantic_mask,
)
from personaweave.providers.yunwu import ImageEditPlan, ImageEditReview, ImageEditScores
from personaweave.provider_errors import ProviderExecutionError, provider_error
from personaweave.storage import Repository
from personaweave.workflow import InvalidTransition, WorkflowService


def test_connect_error_is_retryable_and_has_actionable_message() -> None:
    error = httpx.ConnectError("", request=httpx.Request("POST", "https://provider"))

    assert PipelineExecutor._error_code(error) == "provider_unavailable"
    assert PipelineExecutor._error_message(error) == (
        "云端模型连接失败，请检查 Docker 网络或代理后重试。"
    )


def test_empty_provider_error_uses_exception_type() -> None:
    assert PipelineExecutor._error_message(RuntimeError()) == "RuntimeError"


def test_timeout_error_has_actionable_message() -> None:
    error = httpx.ReadTimeout("", request=httpx.Request("POST", "https://provider"))

    assert PipelineExecutor._error_code(error) == "provider_timeout"
    assert PipelineExecutor._error_message(error) == (
        "云端模型在 600 秒执行窗口内未返回，请稍后重试。"
    )


def test_validation_error_does_not_persist_model_output() -> None:
    try:
        BoundingBox.model_validate({"x": "hidden model prose"})
    except ValidationError as error:
        message = PipelineExecutor._provider_response_error_message(error)
    else:
        raise AssertionError("invalid bounding box should fail validation")

    assert message == "云端模型返回的数据结构不符合要求，请重试。"
    assert "hidden model prose" not in message


def test_analysis_payload_uses_bounded_preview_and_original_coordinates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.jpg"
    Image.new("RGB", (3000, 2000), "blue").save(source, format="JPEG")
    executor = object.__new__(PipelineExecutor)
    executor.assets = AssetResolver(tmp_path)
    executor.advisory_preview_max_edge = 1600

    full_uri, preview_uri, width, height = executor._analysis_image_payload({
        "storage_uri": source.resolve().as_uri(),
        "display_code": "I01",
    })

    def dimensions(uri: str) -> tuple[int, int]:
        encoded = uri.split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            return image.size

    assert (width, height) == (3000, 2000)
    assert dimensions(full_uri) == (3000, 2000)
    assert dimensions(preview_uri) == (1600, 1067)

def confirm_edit_contract(
    repository: Repository,
    batch_id: str,
    source: dict,
) -> None:
    batch = repository.get_batch(batch_id)
    person = repository.list_persons(batch_id)[0]
    face = person["faces"][0]
    bbox = BoundingBox.model_validate(face["bbox"])
    permissions = AttributePermission(
        editable=["face_features"],
        protected=[
            "face_identity",
            "apparent_age",
            "hair",
            "eyewear",
            "accessories",
            "makeup",
        ],
    )
    source_path = AssetResolver(repository.database_path.parent).local_path(
        source["storage_uri"]
    )
    assert source_path is not None
    with Image.open(source_path) as source_image:
        width, height = source_image.size
    proposal = SemanticMaskProposal(
        source_asset_id=source["id"],
        source_asset_code=source["display_code"],
        contours=[
            SemanticMaskContour(
                operation="include",
                face_instance_id=face["id"],
                person_id=person["id"],
                person_code=person["display_code"],
                permissions=permissions,
                points=[
                    {"x": bbox.x / width, "y": bbox.y / height},
                    {"x": (bbox.x + bbox.width) / width, "y": bbox.y / height},
                    {
                        "x": (bbox.x + bbox.width) / width,
                        "y": (bbox.y + bbox.height) / height,
                    },
                    {"x": bbox.x / width, "y": (bbox.y + bbox.height) / height},
                ],
                confidence=1.0,
                evidence="deterministic editable-face test contour",
            )
        ],
        confidence=1.0,
        summary="Deterministic canonical mask for the editable source face.",
    )
    rasterized = validate_and_rasterize_semantic_mask(
        source_path=source_path,
        proposal=proposal,
        source_asset_id=source["id"],
        source_asset_code=source["display_code"],
        editable_faces=[
            FormalMaskFaceScope(
                face_instance_id=face["id"],
                person_id=person["id"],
                person_code=person["display_code"],
                bbox=bbox,
                permissions=permissions,
            )
        ],
        protected_faces=[],
    )
    mask_evidence = CanonicalMaskEvidence(
        source_asset_id=source["id"],
        source_asset_code=source["display_code"],
        width=rasterized.width,
        height=rasterized.height,
        sha256=rasterized.sha256,
        proposal=rasterized.proposal,
        review_reasons=rasterized.review_reasons,
    )
    contract = CompiledBatchContract(
        task_instruction=batch["task_instruction"],
        max_iterations=batch["max_iterations"],
        operations=[
            CompiledOperation(
                person_id=person["id"],
                person_code=person["display_code"],
                action="edit",
                permissions=permissions,
            )
        ],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[
                    CompiledFaceEvidence(
                        face_instance_id=face["id"],
                        person_id=person["id"],
                        person_code=person["display_code"],
                        bbox=bbox,
                        source_appearance=PersonAppearance.model_validate(
                            face.get("appearance") or {}
                        ),
                    )
                ],
                editable_face_instance_ids=[face["id"]],
                authorization_mask=mask_evidence,
            )
        ],
    )
    repository.store_compiled_contract(
        batch_id,
        FormalCompilationResult(
            contract=contract,
            masks=(
                CompiledMaskArtifact(
                    evidence=mask_evidence,
                    png_bytes=rasterized.png_bytes,
                ),
            ),
        ),
    )
    repository.confirm_compiled_contract(batch_id, contract.sha256())



class FakeGateway:
    configured = True

    def __init__(self, *, mismatched_asset: bool = False):
        self.mismatched_asset = mismatched_asset
        self.received_uri: str | None = None

    async def analyze(self, request):
        self.received_uri = request.asset_uri
        return AnalysisResult(
            asset_id="wrong" if self.mismatched_asset else request.asset_id,
            provider="fake-gateway",
            faces=[
                FaceObservation(
                    external_face_id="face-1",
                    bbox={"x": 1, "y": 2, "width": 30, "height": 40},
                    detection_confidence=0.99,
                    suggested_person_key="cluster-a",
                )
            ],
        )


class GatewayBackedBatchAnalyzer:
    def __init__(self, gateway: FakeGateway):
        self.gateway = gateway
        self.received_sources = []

    async def analyze_batch_people(self, sources):
        self.received_sources = sources
        return [
            await self.gateway.analyze(AnalysisRequest(
                batch_id="batch-analysis",
                asset_id=source["asset_id"],
                asset_uri=source["image_uri"],
            ))
            for source in sources
        ]

    async def refine_batch_face_boxes(self, _sources, results):
        return results


class UpstreamConfirmedGateway(FakeGateway):
    async def analyze(self, request):
        result = await super().analyze(request)
        result.faces[0].identity_confidence = 1.0
        result.faces[0].needs_confirmation = False
        return result


class FakeValidationGateway:
    configured = True

    async def validate(self, request):
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=True,
            provider="authoritative-gateway",
            checks={"outside_mask_unchanged": True},
            reasons=["pixel checks passed"],
        )


class FakeAdvisoryValidator:
    async def validate(self, request):
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=False,
            provider="visual-qa",
            checks={"obvious_face_distortion": True, "review_confidence": 0.91},
            reasons=["visible face distortion"],
        )


class CoordinatedQualityValidator:
    def __init__(
        self,
        state: dict[str, Any],
        provider: str,
        *,
        passed: bool,
    ):
        self.state = state
        self.provider = provider
        self.passed = passed

    async def validate(self, request):
        self.state["calls"].append(self.provider)
        if len(self.state["calls"]) == 2:
            self.state["ready"].set()
        await asyncio.wait_for(self.state["ready"].wait(), timeout=1)
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=self.passed,
            provider=self.provider,
            checks={"quality_passed": self.passed},
            reasons=[] if self.passed else ["quality failure"],
        )


class FakePersonFusionValidator:
    async def analyze_batch_people(self, sources):
        return [AnalysisResult(
            asset_id=source["asset_id"],
            provider="fake-multimodal-batch-analysis",
            model="fake-vision",
            faces=[FaceObservation(
                external_face_id=f"{source['source_code']}-V01",
                bbox={"x": 1, "y": 2, "width": 30, "height": 40},
                detection_confidence=0.99,
                suggested_person_key="same-source-person",
                identity_confidence=0.97,
                needs_confirmation=True,
                appearance=PersonAppearance(
                    apparent_age="young adult",
                    hair="short dark hair",
                    eyewear="none",
                    accessories="none",
                    body="medium build",
                    clothing="casual top",
                    pose="front-facing head and relaxed body",
                    lighting_occlusion="even light, no visible occlusion",
                ),
            )],
            detection_audit=PersonDetectionAudit(
                complete=True,
                detected_face_count=1,
                visible_face_count=1,
            ),
        ) for source in sources]

    async def refine_batch_face_boxes(self, _sources, results):
        return results

    async def analyze_person_appearances(self, *, source_uri, observations):
        assert source_uri.startswith("data:image/jpeg;base64,")
        return {
            observation["code"]: PersonAppearance(
                apparent_age="young adult",
                hair="short dark hair",
                eyewear="none",
                accessories="none",
                body="medium build",
                clothing="casual top",
                pose="front-facing head and relaxed body",
                lighting_occlusion="even light, no visible occlusion",
            )
            for observation in observations
        }

    async def cluster_person_observations(self, observations):
        return {
            observation["code"]: ("same-source-person", 0.97)
            for observation in observations
        }


class CoordinatedPersonFusionValidator(FakePersonFusionValidator):
    def __init__(self):
        self.calls: list[str] = []
        self.ready = asyncio.Event()

    async def analyze_person_appearances(self, *, source_uri, observations):
        self.calls.append(observations[0]["code"])
        if len(self.calls) == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        return await super().analyze_person_appearances(
            source_uri=source_uri,
            observations=observations,
        )

    async def analyze_batch_people(self, sources):
        self.calls = [source["source_code"] for source in sources]
        return await super().analyze_batch_people(sources)


class FlakyBatchPersonAnalyzer(FakePersonFusionValidator):
    def __init__(self):
        self.calls = 0

    async def analyze_batch_people(self, sources):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary batch analysis failure")
        return await super().analyze_batch_people(sources)


class UncertainPersonFusionValidator(FakePersonFusionValidator):
    async def analyze_batch_people(self, sources):
        results = await super().analyze_batch_people(sources)
        for index, result in enumerate(results):
            result.faces[0].suggested_person_key = f"vlm:{sources[index]['source_code']}"
            result.faces[0].identity_confidence = 0.0 if index == 0 else 0.5
        return results

    async def cluster_person_observations(self, observations):
        assert [observation["code"] for observation in observations] == [
            "I01-F01",
            "I02-F01",
        ]
        return {
            "I01-F01": ("vlm:I01-F01", 0.0),
            "I02-F01": ("vlm:I02-F01", 0.5),
        }


class FakeTargetedLocalizationValidator:
    model = "fake-vision"

    def __init__(self):
        self.broad_search = False
        self.source_count = 0

    async def refine_batch_face_boxes(self, sources, results, *, broad_search=False):
        self.broad_search = broad_search
        self.source_count = len(sources)
        for result in results:
            for face in result.faces:
                face.bbox = BoundingBox(x=8, y=9, width=20, height=22)
                face.detection_confidence = 0.93
        return results


class FakeSwapGateway:
    configured = True

    def __init__(self):
        self.requests = []

    async def swap(self, request):
        self.requests.append(request.model_copy(deep=True))
        output = BytesIO()
        Image.new("RGB", (64, 64), "blue").save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode()
        return SwapResult(
            source_asset_id=request.asset_id,
            output_uri=f"data:image/png;base64,{encoded}",
            output_sha256="remote-unverified",
            provider="fake-image-editor",
            model="gpt-image-2",
        )


class FakeTargetedAnalysisGateway:
    configured = True

    def __init__(self):
        self.requests = []

    async def analyze(self, request):
        self.requests.append(request.model_copy(deep=True))
        assert request.analysis_pass == "targeted"
        return AnalysisResult(
            asset_id=request.asset_id,
            provider="targeted-local",
            faces=[FaceObservation(
                external_face_id="targeted-0",
                bbox={"x": 62, "y": 22, "width": 18, "height": 20},
                detection_confidence=0.91,
            )],
        )


class FakeDetectionAuditValidator:
    async def audit_person_detection(self, **kwargs):
        assert kwargs["source_uri"].startswith("data:image/jpeg;base64,")
        assert kwargs["annotated_uri"].startswith("data:image/jpeg;base64,")
        assert len(kwargs["detections"]) == 1
        return PersonDetectionAudit(
            complete=False,
            detected_face_count=1,
            visible_face_count=2,
            missing_regions=[MissingFaceRegion(
                bbox=NormalizedBoundingBox(x=0.58, y=0.18, width=0.25, height=0.28),
                confidence=0.94,
                evidence="visible unboxed profile face",
            )],
            requires_manual_review=True,
            summary="one visible face is missing",
        )


class FakeNumberedEditValidator:
    def __init__(self):
        self.planned_source_uri: str | None = None
        self.reviewed_result_uri: str | None = None

    async def plan_numbered_image_edit(self, *, request, source_uri):
        assert source_uri.startswith("data:image/jpeg;base64,")
        self.planned_source_uri = source_uri
        assert request.replacements[0].source_appearance.apparent_age == "young adult"
        return ImageEditPlan(
            edit_prompt=(
                "Change only the requested facial traits and coordinate them with the source "
                "person's visible age, body, clothing, pose, accessories and lighting."
            ),
            observed_people=1,
            intended_face_edits=1,
            warnings=[],
        )

    async def review_numbered_image_edit(self, **kwargs):
        self.reviewed_result_uri = kwargs["result_uri"]
        return ImageEditReview(
            passed=True,
            scores=ImageEditScores(
                instruction_fulfillment=95,
                target_identity_consistency=95,
                face_naturalness=95,
                pose_expression_preservation=95,
                source_content_preservation=98,
                text_logo_preservation=100,
            ),
            issues=[],
            summary="通过",
            revised_edit_prompt=None,
        )

def prepare_analysis(tmp_path: Path, gateway: FakeGateway):
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    image_path = asset_root / "source.jpg"
    Image.new("RGB", (64, 64), "white").save(image_path, format="JPEG")
    repository = Repository(tmp_path / "executor.sqlite3")
    repository.initialize()
    batch = repository.create_batch("executor")
    repository.add_asset(
        batch_id=batch["id"],
        kind="source",
        filename="source.jpg",
        media_type="image/jpeg",
        storage_uri=image_path.resolve().as_uri(),
        sha256="input-hash",
    )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    analyzer = GatewayBackedBatchAnalyzer(gateway)
    executor = PipelineExecutor(
        repository,
        workflow,
        gateway,  # type: ignore[arg-type]
        AssetResolver(asset_root),
        concurrency=1,
        advisory_validator=analyzer,  # type: ignore[arg-type]
    )
    executor.test_batch_analyzer = analyzer  # type: ignore[attr-defined]
    return repository, batch, executor

def prepare_validation(
    tmp_path: Path,
    *,
    quality_validators=None,
    objective_validators=(),
):

    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    source_path = asset_root / "source.jpg"
    result_path = asset_root / "result.jpg"
    Image.new("RGB", (64, 64), "white").save(source_path, format="JPEG")
    Image.new("RGB", (64, 64), "white").save(result_path, format="JPEG")
    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()
    batch = repository.create_batch("validation")
    source = repository.add_asset(
        batch_id=batch["id"],
        kind="source",
        filename="source.jpg",
        media_type="image/jpeg",
        storage_uri=source_path.resolve().as_uri(),
        sha256="source-hash",
    )
    result = repository.add_asset(
        batch_id=batch["id"],
        parent_asset_id=source["id"],
        kind="result",
        filename="result.jpg",
        media_type="image/jpeg",
        storage_uri=result_path.resolve().as_uri(),
        sha256="result-hash",
    )
    request = ValidationRequest(
        batch_id=batch["id"],
        result_asset_id=result["id"],
        source_uri=source["storage_uri"],
        result_uri=result["storage_uri"],
        expected_face_instance_ids=["face-1"],
        protected_face_instance_ids=["face-2"],
    )
    repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        result["id"],
        request.model_dump(mode="json"),
    )
    repository.set_batch_status(batch["id"], BatchStatus.VALIDATING)
    workflow = WorkflowService(repository)
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeValidationGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        concurrency=1,
        quality_validators=quality_validators,
        objective_validators=objective_validators,
        advisory_validator=FakeAdvisoryValidator(),
    )
    return repository, batch, executor


def prepare_swap(tmp_path: Path, validator):
    asset_root = tmp_path / "swap-assets"
    asset_root.mkdir()
    source_path = asset_root / "source.jpg"
    Image.new("RGB", (64, 64), "white").save(source_path, format="JPEG")
    repository = Repository(tmp_path / "swap.sqlite3")
    repository.initialize()
    batch = repository.create_batch("planned swap")
    source = repository.add_asset(
        batch_id=batch["id"],
        kind="source",
        filename="source.jpg",
        media_type="image/jpeg",
        storage_uri=source_path.resolve().as_uri(),
        sha256="a" * 64,
    )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    workflow.complete_analysis(batch["id"], [{
        "asset_id": source["id"],
        "provider": "fake-analysis",
        "faces": [{
            "external_face_id": "face-1",
            "bbox": {"x": 10, "y": 8, "width": 20, "height": 24},
            "detection_confidence": 0.99,
            "suggested_person_key": "person-1",
            "needs_confirmation": False,
            "appearance": PersonAppearance(
                apparent_age="young adult",
                hair="short dark hair",
                body="medium build",
                clothing="white casual top",
                pose="front-facing",
                lighting_occlusion="soft even light",
            ).model_dump(),
        }],
    }])
    repository.update_batch_spec(batch["id"], "将 @P01 改成更成熟的面部形象", 3)
    workflow.compile_instruction_mappings(batch["id"])
    confirm_edit_contract(repository, batch["id"], source)
    workflow.start_swap(batch["id"])
    gateway = FakeSwapGateway()
    executor = PipelineExecutor(
        repository,
        workflow,
        gateway,  # type: ignore[arg-type]
        AssetResolver(tmp_path),
        concurrency=1,
        advisory_validator=validator,
    )
    return repository, batch, gateway, executor


def test_current_swap_jobs_ignore_failures_from_superseded_contract(
    tmp_path: Path,
) -> None:
    repository, batch, _, executor = prepare_swap(
        tmp_path,
        FakeNumberedEditValidator(),
    )
    current = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]
    stale = repository.create_job(
        batch["id"],
        JobStage.SWAP,
        "superseded-source",
        {**current["input"], "contract_hash": "0" * 64},
    )
    repository.fail_job(stale["id"], "provider_unavailable", "stale failure")
    active = repository.current_contract_stage_jobs(batch["id"], JobStage.SWAP)

    assert [job["id"] for job in active] == [current["id"]]


@pytest.mark.anyio
async def test_terminal_needs_review_jobs_resume_stage_aggregation(
    tmp_path: Path,
) -> None:
    repository, batch, _, executor = prepare_swap(
        tmp_path,
        FakeNumberedEditValidator(),
    )
    current = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]
    repository.complete_job(
        current["id"],
        status=JobStatus.NEEDS_REVIEW,
        provider="fake",
        output={},
    )

    result = await executor.execute_stage(batch["id"], JobStage.SWAP)

    assert result["requires_review"] is True
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW


@pytest.mark.anyio
async def test_executor_sends_local_assets_as_data_uris(tmp_path: Path) -> None:
    gateway = FakeGateway()
    repository, batch, executor = prepare_analysis(tmp_path, gateway)

    result = await executor.execute_stage(batch["id"], JobStage.ANALYZE)

    assert result["completed"] is True
    assert gateway.received_uri is not None
    assert gateway.received_uri.startswith("data:image/jpeg;base64,")
    analyzer = executor.test_batch_analyzer  # type: ignore[attr-defined]
    assert analyzer.received_sources[0]["width"] == 64
    assert analyzer.received_sources[0]["height"] == 64
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.AWAITING_CONFIRMATION


@pytest.mark.anyio
async def test_analysis_fuses_duplicate_people_across_uploaded_images(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "fusion.sqlite3")
    repository.initialize()
    batch = repository.create_batch("fusion")
    for index, color in enumerate(("red", "blue"), start=1):
        path = asset_root / f"source-{index}.jpg"
        Image.new("RGB", (64, 64), color).save(path, format="JPEG")
        repository.add_asset(
            batch_id=batch["id"],
            kind="source",
            filename=path.name,
            media_type="image/jpeg",
            storage_uri=path.resolve().as_uri(),
            sha256=f"hash-{index}",
        )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    validator = CoordinatedPersonFusionValidator()
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        concurrency=2,
        advisory_validator=validator,  # type: ignore[arg-type]
    )

    result = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    people = repository.list_persons(batch["id"])

    assert result["completed"] is True
    assert len(people) == 1
    assert len(people[0]["faces"]) == 2
    assert people[0]["cluster_key"] == "same-source-person"
    assert all(
        face["appearance"]["apparent_age"] == "young adult"
        for face in people[0]["faces"]
    )
    assert sorted(validator.calls) == ["I01", "I02"]


@pytest.mark.anyio
async def test_retrying_one_failed_analysis_job_resumes_the_whole_batch(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "batch-retry.sqlite3")
    repository.initialize()
    batch = repository.create_batch("batch retry")
    for index, color in enumerate(("red", "blue"), start=1):
        path = asset_root / f"source-{index}.jpg"
        Image.new("RGB", (64, 64), color).save(path, format="JPEG")
        repository.add_asset(
            batch_id=batch["id"],
            kind="source",
            filename=path.name,
            media_type="image/jpeg",
            storage_uri=path.resolve().as_uri(),
            sha256=f"hash-{index}",
        )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    analyzer = FlakyBatchPersonAnalyzer()
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=analyzer,  # type: ignore[arg-type]
    )

    first = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    failed_jobs = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)
    assert first["completed"] is False
    assert all(job["status"] == JobStatus.FAILED for job in failed_jobs)

    repository.retry_job(failed_jobs[0]["id"])
    repository.set_batch_status(batch["id"], BatchStatus.ANALYZING)
    second = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    latest = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)

    assert second["completed"] is True
    assert analyzer.calls == 2
    assert len(latest) == 2
    assert all(job["status"] == JobStatus.SUCCEEDED for job in latest)
    assert len(repository.list_persons(batch["id"])) == 1


@pytest.mark.anyio
async def test_existing_extracted_people_can_be_refused_without_reanalysis(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "refusion.sqlite3")
    repository.initialize()
    batch = repository.create_batch("refusion")
    assets = []
    for index, color in enumerate(("red", "blue"), start=1):
        path = asset_root / f"source-{index}.jpg"
        Image.new("RGB", (64, 64), color).save(path, format="JPEG")
        assets.append(repository.add_asset(
            batch_id=batch["id"],
            kind="source",
            filename=path.name,
            media_type="image/jpeg",
            storage_uri=path.resolve().as_uri(),
            sha256=f"hash-{index}",
        ))
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    workflow.complete_analysis(batch["id"], [
        AnalysisResult(
            asset_id=asset["id"],
            provider="legacy-analysis",
            faces=[FaceObservation(
                external_face_id=f"face-{index}",
                bbox={"x": 1, "y": 2, "width": 30, "height": 40},
                detection_confidence=0.99,
                suggested_person_key=f"legacy-person-{index}",
            )],
        ).model_dump(mode="json")
        for index, asset in enumerate(assets, start=1)
    ])
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=FakePersonFusionValidator(),  # type: ignore[arg-type]
    )

    result = await executor.fuse_existing_people(batch["id"])
    people = repository.list_persons(batch["id"])

    assert result["before_count"] == 2
    assert result["after_count"] == 1
    assert result["merged_count"] == 1
    assert len(people[0]["faces"]) == 2
    assert all(face["identity_confidence"] == 0.97 for face in people[0]["faces"])


@pytest.mark.anyio
async def test_targeted_person_relocalization_updates_every_occurrence_without_regrouping(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "targeted-localization.sqlite3")
    repository.initialize()
    batch = repository.create_batch("targeted-localization")
    assets = []
    for index, color in enumerate(("red", "blue"), start=1):
        path = asset_root / f"source-{index}.jpg"
        Image.new("RGB", (64, 64), color).save(path, format="JPEG")
        assets.append(repository.add_asset(
            batch_id=batch["id"],
            kind="source",
            filename=path.name,
            media_type="image/jpeg",
            storage_uri=path.resolve().as_uri(),
            sha256=f"hash-{index}",
        ))
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    workflow.complete_analysis(batch["id"], [
        AnalysisResult(
            asset_id=asset["id"],
            provider="initial-analysis",
            faces=[FaceObservation(
                external_face_id=f"I{index:02d}-V01",
                bbox=BoundingBox(x=1, y=2, width=10, height=12),
                detection_confidence=0.7,
                suggested_person_key="same-person",
                identity_confidence=0.95,
            )],
            detection_audit=PersonDetectionAudit(
                complete=True,
                detected_face_count=1,
                visible_face_count=1,
            ),
        ).model_dump(mode="json")
        for index, asset in enumerate(assets, start=1)
    ])
    validator = FakeTargetedLocalizationValidator()
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=validator,  # type: ignore[arg-type]
    )
    person = repository.list_persons(batch["id"])[0]

    result = await executor.relocalize_person(batch["id"], person["id"])
    updated = repository.list_persons(batch["id"])[0]

    assert result["person_code"] == person["display_code"]
    assert result["source_count"] == 2
    assert result["requires_manual_review"] is False
    assert result["person_review_issues"] == []
    assert result["batch_requires_manual_review"] is False
    assert result["analysis_review_issues"] == []
    assert validator.broad_search is True
    assert validator.source_count == 2
    assert updated["id"] == person["id"]
    assert [face["bbox"] for face in updated["faces"]] == [
        {"x": 8, "y": 9, "width": 20, "height": 22},
        {"x": 8, "y": 9, "width": 20, "height": 22},
    ]


@pytest.mark.anyio
async def test_targeted_relocalization_preserves_previous_bbox_on_person_collision(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "localization-collision.sqlite3")
    repository.initialize()
    batch = repository.create_batch("localization collision")
    path = asset_root / "source.jpg"
    Image.new("RGB", (64, 64), "white").save(path, format="JPEG")
    asset = repository.add_asset(
        batch_id=batch["id"],
        kind="source",
        filename=path.name,
        media_type="image/jpeg",
        storage_uri=path.resolve().as_uri(),
        sha256="collision-hash",
    )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    workflow.complete_analysis(batch["id"], [AnalysisResult(
        asset_id=asset["id"],
        provider="initial-analysis",
        faces=[
            FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=1, y=2, width=10, height=12),
                detection_confidence=0.7,
                suggested_person_key="person-a",
                identity_confidence=0.95,
            ),
            FaceObservation(
                external_face_id="I01-V02",
                bbox=BoundingBox(x=8, y=9, width=20, height=22),
                detection_confidence=0.96,
                suggested_person_key="person-b",
                identity_confidence=0.97,
            ),
        ],
        detection_audit=PersonDetectionAudit(
            complete=True,
            detected_face_count=2,
            visible_face_count=2,
        ),
    ).model_dump(mode="json")])
    executor = PipelineExecutor(
        repository,
        workflow,
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=FakeTargetedLocalizationValidator(),  # type: ignore[arg-type]
    )
    person = next(
        item for item in repository.list_persons(batch["id"])
        if item["cluster_key"] == "person-a"
    )

    result = await executor.relocalize_person(batch["id"], person["id"])
    updated = next(
        item for item in repository.list_persons(batch["id"])
        if item["id"] == person["id"]
    )

    assert updated["faces"][0]["bbox"] == {"x": 1, "y": 2, "width": 10, "height": 12}
    assert updated["faces"][0]["latest_localization_attempt"]["accepted"] is False
    assert "I01-V02" in updated["faces"][0]["latest_localization_attempt"]["reason"]
    assert result["failed_occurrence_codes"] == ["I01-V01"]
    assert result["requires_manual_review"] is True


@pytest.mark.anyio
async def test_targeted_person_relocalization_rejects_concurrent_batch_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = Repository(tmp_path / "relocalization-lock.sqlite3")
    repository.initialize()
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    executor = PipelineExecutor(
        repository,
        WorkflowService(repository),
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_relocalization(batch_id: str, person_id: str) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"batch_id": batch_id, "person_id": person_id}

    monkeypatch.setattr(executor, "_relocalize_person", slow_relocalization)
    first = asyncio.create_task(executor.relocalize_person("batch-1", "person-1"))
    await started.wait()

    with pytest.raises(InvalidTransition, match="already being re-localized"):
        await executor.relocalize_person("batch-1", "person-2")

    release.set()
    assert await first == {"batch_id": "batch-1", "person_id": "person-1"}


@pytest.mark.anyio
async def test_fusion_persists_uncertain_and_singleton_confidence_without_confirmation(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "confidence.sqlite3")
    repository.initialize()
    batch = repository.create_batch("confidence")
    for index, color in enumerate(("red", "blue"), start=1):
        path = asset_root / f"source-{index}.jpg"
        Image.new("RGB", (64, 64), color).save(path, format="JPEG")
        repository.add_asset(
            batch_id=batch["id"],
            kind="source",
            filename=path.name,
            media_type="image/jpeg",
            storage_uri=path.resolve().as_uri(),
            sha256=f"hash-{index}",
        )
    workflow = WorkflowService(repository)
    workflow.start_analysis(batch["id"])
    executor = PipelineExecutor(
        repository,
        workflow,
        UpstreamConfirmedGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        concurrency=2,
        advisory_validator=UncertainPersonFusionValidator(),  # type: ignore[arg-type]
    )

    result = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    faces = [
        face
        for person in repository.list_persons(batch["id"])
        for face in person["faces"]
    ]

    assert result["completed"] is True
    assert sorted(face["identity_confidence"] for face in faces) == [0.0, 0.5]
    assert all(face["needs_confirmation"] == 1 for face in faces)


@pytest.mark.anyio
async def test_fusion_fails_when_a_detected_face_asset_is_not_local(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "unreadable.sqlite3")
    repository.initialize()
    batch = repository.create_batch("unreadable")
    asset = repository.add_asset(
        batch_id=batch["id"],
        kind="source",
        filename="remote.jpg",
        media_type="image/jpeg",
        storage_uri="https://assets.example.test/remote.jpg",
        sha256="remote-hash",
    )
    executor = PipelineExecutor(
        repository,
        WorkflowService(repository),
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=UncertainPersonFusionValidator(),  # type: ignore[arg-type]
    )

    with pytest.raises(StageExecutionError, match="could not read every detected face"):
        await executor._cluster_analysis_people([{
            "asset_id": asset["id"],
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 1, "y": 2, "width": 30, "height": 40},
                "detection_confidence": 0.99,
                "identity_confidence": 1.0,
                "needs_confirmation": False,
            }],
        }])


@pytest.mark.anyio
async def test_provider_cannot_complete_another_assets_job(tmp_path: Path) -> None:
    repository, batch, executor = prepare_analysis(tmp_path, FakeGateway(mismatched_asset=True))

    result = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    progress = repository.batch_progress(batch["id"])

    assert result["completed"] is False
    assert progress["stages"]["analyze"]["failed"] == 1
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.FAILED


def test_reconcile_terminal_failure_and_retry_restores_stage_status(tmp_path: Path) -> None:
    repository, batch, _ = prepare_analysis(tmp_path, FakeGateway())
    job = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]
    repository.fail_job(job["id"], "provider_error", "upstream failed")

    assert repository.reconcile_terminal_batch_failures() == 1
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.FAILED

    repository.retry_job(job["id"])
    repository.set_batch_status(batch["id"], BatchStatus.ANALYZING)
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.ANALYZING


def test_retry_can_refresh_mutable_provider_profile(tmp_path: Path) -> None:
    repository, batch, _ = prepare_analysis(tmp_path, FakeGateway())
    job = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]
    repository.fail_job(job["id"], "provider_error", "upstream failed")
    original_input = job["input"]
    with pytest.raises(ValueError, match="immutable fields=.*contract_hash"):
        repository.retry_job(
            job["id"],
            input_updates={"contract_hash": "different-contract"},
        )

    retried = repository.retry_job(
        job["id"],
        input_updates={
            "image_model": "future-image-model",
            "image_api_mode": "json_generations",
            "image_api_path": "/api/v3/images/generations",
            "image_output_size": "2048x2048",
            "image_response_format": "b64_json",
        },
    )

    assert retried["input"] == {
        **original_input,
        "image_model": "future-image-model",
        "image_api_mode": "json_generations",
        "image_api_path": "/api/v3/images/generations",
        "image_output_size": "2048x2048",
        "image_response_format": "b64_json",
    }


def test_retry_preserves_immutable_review_reasons(tmp_path: Path) -> None:
    repository, batch, _, _ = prepare_swap(
        tmp_path,
        FakeNumberedEditValidator(),
    )
    job = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]
    immutable_input = {
        **job["input"],
        "replacements": [
            {
                **replacement,
                "review_reasons": ["mask_uncertain"],
            }
            for replacement in job["input"]["replacements"]
        ],
        "review_reasons": ["mask_uncertain"],
    }
    with repository.connect() as connection:
        connection.execute(
            "UPDATE jobs SET input_json = ? WHERE id = ?",
            (json.dumps(immutable_input), job["id"]),
        )
    repository.fail_job(job["id"], "provider_error", "upstream failed")

    retried = repository.retry_job(job["id"])

    assert retried["input"]["review_reasons"] == ["mask_uncertain"]
    assert retried["input"]["replacements"][0]["review_reasons"] == [
        "mask_uncertain"
    ]


@pytest.mark.anyio
async def test_provider_error_dto_is_persisted_without_raw_prose(tmp_path: Path) -> None:
    class FailingGateway(FakeGateway):
        async def analyze(self, request):
            raise ProviderExecutionError(
                provider_error(
                    "provider_policy_rejected",
                    upstream_status=400,
                    provider_code="OutputImageSensitiveContentDetected.PolicyViolation",
                )
            ) from RuntimeError("raw provider body with prompt and sk-super-secret")

    repository, batch, executor = prepare_analysis(tmp_path, FailingGateway())

    result = await executor.execute_stage(batch["id"], JobStage.ANALYZE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]

    assert result["completed"] is False
    assert job["error_code"] == "provider_policy_rejected"
    assert job["error_message"] == "图片内容触发服务商安全策略，无法完成生成。"
    assert "raw provider body" not in job["error_message"]
    assert "sk-super-secret" not in job["error_message"]


@pytest.mark.anyio
async def test_swap_uses_source_appearance_in_initial_multimodal_plan(tmp_path: Path) -> None:
    validator = FakeNumberedEditValidator()
    repository, batch, gateway, executor = prepare_swap(tmp_path, validator)

    result = await executor.execute_stage(batch["id"], JobStage.SWAP)
    job = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]

    assert result["completed"] is True
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.VALIDATING
    assert len(gateway.requests) == 1
    assert gateway.requests[0].authorization_mask_uri.startswith(
        "data:image/png;base64,"
    )
    assert "MULTIMODAL SOURCE-AWARE EDIT PLAN" not in (
        gateway.requests[0].compiled_instruction
    )
    assert "MULTIMODAL SOURCE-AWARE EDIT PLAN" in (
        gateway.requests[0].adaptive_instruction
    )
    assert "visible age, body, clothing, pose" in (
        gateway.requests[0].adaptive_instruction
    )
    assert job["output"]["generation_plan"]["intended_face_edits"] == 1
    assert job["output"]["generation_review"]["passed"] is True
    assert validator.reviewed_result_uri is not None
    assert validator.reviewed_result_uri.startswith("data:image/jpeg;base64,")
    assert job["phase"] == "completed"
    assert job["current_iteration"] == 1
    assert job["total_iterations"] == 3


@pytest.mark.anyio
async def test_immutable_review_reasons_cannot_override_compiled_contract(
    tmp_path: Path,
) -> None:
    repository, batch, _, executor = prepare_swap(
        tmp_path,
        FakeNumberedEditValidator(),
    )
    job = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]
    immutable_input = {
        **job["input"],
        "replacements": [
            {
                **replacement,
                "review_reasons": ["mask_uncertain"],
            }
            for replacement in job["input"]["replacements"]
        ],
        "review_reasons": ["mask_uncertain"],
    }
    with repository.connect() as connection:
        connection.execute(
            "UPDATE jobs SET input_json = ? WHERE id = ?",
            (json.dumps(immutable_input), job["id"]),
        )

    result = await executor.execute_stage(batch["id"], JobStage.SWAP)
    completed = repository.get_job(job["id"])

    assert result["completed"] is False
    assert completed["status"] == JobStatus.FAILED
    assert completed["output"] is None
    assert "swap replacement does not match compiled operation evidence" in (
        completed["error_message"] or ""
    )


@pytest.mark.anyio
async def test_swap_review_failure_is_needs_review_not_success(tmp_path: Path) -> None:
    class UnavailableReviewValidator(FakeNumberedEditValidator):
        async def review_numbered_image_edit(self, **kwargs):
            raise TimeoutError()

    repository, batch, _, executor = prepare_swap(
        tmp_path,
        UnavailableReviewValidator(),
    )

    result = await executor.execute_stage(batch["id"], JobStage.SWAP)
    job = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]

    assert result["completed"] is False
    assert result["needs_review_job_ids"] == [job["id"]]
    assert job["status"] == JobStatus.NEEDS_REVIEW
    assert job["output"]["generation_review"]["stop_reason"] == "review_unavailable"
    assert "TimeoutError" in job["output"]["generation_review"]["summary"]
    assert job["phase"] == "needs_review"
    assert job["progress_message"] == "需要人工检查"
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW
    candidates = repository.list_assets(batch["id"], "result")
    assert len(candidates) == 1
    assert candidates[0]["parent_asset_id"] == job["item_key"]


@pytest.mark.anyio
async def test_swap_reaching_iteration_limit_requires_review(tmp_path: Path) -> None:
    class RejectingReviewValidator(FakeNumberedEditValidator):
        async def review_numbered_image_edit(self, **kwargs):
            self.reviewed_result_uri = kwargs["result_uri"]
            return ImageEditReview(
                passed=False,
                scores=ImageEditScores(
                    instruction_fulfillment=60,
                    target_identity_consistency=60,
                    face_naturalness=75,
                    pose_expression_preservation=70,
                    source_content_preservation=80,
                    text_logo_preservation=90,
                ),
                issues=[],
                summary="仍未达到验收标准",
                revised_edit_prompt="Keep every immutable constraint and correct only the requested face.",
            )

    repository, batch, gateway, executor = prepare_swap(
        tmp_path,
        RejectingReviewValidator(),
    )

    result = await executor.execute_stage(batch["id"], JobStage.SWAP)
    job = repository.latest_stage_jobs(batch["id"], JobStage.SWAP)[0]

    assert result["completed"] is False
    assert len(gateway.requests) == 3
    assert gateway.requests[1].adaptive_instruction == (
        "MULTIMODAL REVIEW REVISED EDIT PLAN:\n"
        "Keep every immutable constraint and correct only the requested face."
    )
    assert "SOURCE-AWARE EDIT PLAN" not in gateway.requests[1].adaptive_instruction
    assert job["status"] == JobStatus.NEEDS_REVIEW
    assert job["phase"] == "needs_review"
    assert job["current_iteration"] == 3
    assert job["output"]["generation_review"]["stop_reason"] == "max_iterations_reached"
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW
    assert len(repository.list_assets(batch["id"], "result")) == 1


def test_asset_resolver_rejects_file_outside_asset_root(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(UnsafeAssetPath):
        AssetResolver(asset_root).for_remote(outside.resolve().as_uri())


def test_asset_resolver_allows_explicit_read_only_legacy_root(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    legacy_root = tmp_path / "legacy-assets"
    asset_root.mkdir()
    legacy_root.mkdir()
    legacy = legacy_root / "source.jpg"
    legacy.write_bytes(b"legacy")

    resolver = AssetResolver(asset_root, (legacy_root,))

    assert resolver.local_path(legacy.resolve().as_uri()) == legacy.resolve()
    assert resolver.materialize_result(
        "batch-1",
        "source-1",
        "data:image/jpeg;base64,bmV3",
    )[0].startswith(asset_root.resolve().as_uri())


def test_only_latest_failed_attempt_can_be_retried(tmp_path: Path) -> None:
    repository, batch, _ = prepare_analysis(tmp_path, FakeGateway())
    first = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]
    repository.fail_job(first["id"], "test", "failed")
    second = repository.retry_job(first["id"])
    repository.fail_job(second["id"], "test", "failed again")

    with pytest.raises(ValueError, match="latest attempt"):
        repository.retry_job(first["id"])


def test_interrupted_running_job_becomes_retryable_after_worker_restart(tmp_path: Path) -> None:
    repository, batch, _ = prepare_analysis(tmp_path, FakeGateway())
    job = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]
    repository.mark_job_running(
        job["id"],
        phase=JobPhase.ANALYZING,
        message="正在分析图片人物",
    )

    recovered = repository.recover_interrupted_jobs()
    interrupted = repository.get_job(job["id"])
    retried = repository.retry_job(job["id"])

    assert recovered == 1
    assert interrupted["status"] == JobStatus.FAILED
    assert interrupted["phase"] == JobPhase.FAILED
    assert interrupted["error_code"] == "worker_restarted"
    assert retried["status"] == JobStatus.QUEUED
    assert retried["phase"] == JobPhase.QUEUED


def test_advisory_preview_adds_scaled_bbox_without_mutating_generation_request(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "coordinate-assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "coordinate.sqlite3")
    repository.initialize()
    executor = PipelineExecutor(
        repository,
        WorkflowService(repository),
        FakeGateway(),  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_preview_max_edge=1600,
    )
    output = BytesIO()
    Image.new("RGB", (3200, 1600), "white").save(output, format="JPEG")
    source_uri = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
    original_bbox = BoundingBox(x=1600, y=400, width=640, height=320)
    request = SwapRequest(
        batch_id="batch-1",
        asset_id="asset-1",
        asset_uri=source_uri,
        compiled_instruction="将 @P01 替换为 @T01",
        user_instruction="将 @P01 替换为 @T01",
        protected_face_instance_ids=[],
        replacements=[
            SwapOperation(
                face_instance_id="face-1",
                source_person_code="P01",
                target_identity_id="target-1",
                target_identity_code="T01",
                bbox=original_bbox,
            )
        ],
    )

    preview_uri = executor._advisory_preview_uri(source_uri)
    advisory = executor._advisory_request_with_coordinate_aids(request, preview_uri)

    assert executor._data_image_dimensions(preview_uri) == (1600, 800)
    assert request.replacements[0].instruction == ""
    assert "(1600,400,640,320) on the 3200x1600 source" in (
        advisory.adaptive_instruction
    )
    assert "(800,200,320,160)" in advisory.adaptive_instruction
    assert request.replacements[0].bbox == original_bbox


@pytest.mark.anyio
async def test_multimodal_audit_triggers_local_targeted_face_recovery(
    tmp_path: Path,
) -> None:
    asset_root = tmp_path / "hybrid-assets"
    asset_root.mkdir()
    repository = Repository(tmp_path / "hybrid.sqlite3")
    repository.initialize()
    gateway = FakeTargetedAnalysisGateway()
    executor = PipelineExecutor(
        repository,
        WorkflowService(repository),
        gateway,  # type: ignore[arg-type]
        AssetResolver(asset_root),
        advisory_validator=FakeDetectionAuditValidator(),  # type: ignore[arg-type]
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    source_uri = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
    request = AnalysisRequest(
        batch_id="batch-1",
        asset_id="asset-1",
        asset_uri=source_uri,
    )
    primary = AnalysisResult(
        asset_id="asset-1",
        provider="primary-local",
        faces=[FaceObservation(
            external_face_id="face-0",
            bbox={"x": 10, "y": 10, "width": 20, "height": 20},
            detection_confidence=0.96,
        )],
    )

    recovered = await executor._audit_and_recover_faces(request, primary)

    assert len(gateway.requests) == 1
    assert gateway.requests[0].region_hints[0].x == 0.58
    assert len(recovered.faces) == 2
    assert recovered.detection_audit is not None
    assert recovered.detection_audit.complete is True
    assert recovered.detection_audit.requires_manual_review is False
    assert "找回 1 个" in recovered.detection_audit.summary


@pytest.mark.anyio
async def test_validation_fails_when_two_quality_reviewers_are_not_configured(
    tmp_path: Path,
) -> None:
    repository, batch, executor = prepare_validation(tmp_path)

    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)[0]

    assert result["completed"] is False
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.FAILED
    assert job["status"] == JobStatus.FAILED
    assert job["error_code"] == "provider_not_configured"
    assert "two distinct quality reviewer models" in job["error_message"]


@pytest.mark.anyio
async def test_local_source_gate_skips_paid_reviewers_when_canvas_is_invalid(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class CountingValidator:
        def __init__(self, provider: str):
            self.provider = provider

        async def validate(self, request):
            calls.append(self.provider)
            raise AssertionError("hard local failures must not reach paid reviewers")

    repository, batch, executor = prepare_validation(
        tmp_path,
        quality_validators=(
            CountingValidator("review-model-a"),
            CountingValidator("review-model-b"),
        ),
    )
    result_asset = repository.list_assets(batch["id"], "result")[0]
    result_path = executor.assets.local_path(result_asset["storage_uri"])
    assert result_path is not None
    Image.new("RGB", (32, 32), "white").save(result_path, format="JPEG")

    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)[0]

    assert result["completed"] is False
    assert result["requires_review"] is True
    assert calls == []
    assert job["status"] == JobStatus.NEEDS_REVIEW
    assert job["output"]["provider"] == "local-source-comparison"
    assert job["output"]["checks"]["canvas_dimensions_match"] is False
    assert job["output"]["reviewer_verdicts"] == []


@pytest.mark.anyio
async def test_objective_gate_skips_paid_reviewers_when_identity_evidence_fails(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class ObjectiveValidator:
        async def validate(self, request):
            calls.append("objective")
            return ValidationResult(
                result_asset_id=request.result_asset_id,
                passed=False,
                provider="licensed-face-embedding",
                checks={"target_identity_similarity": 0.31},
                reasons=["target identity similarity is below the calibrated threshold"],
            )

    class PaidReviewer:
        def __init__(self, provider: str):
            self.provider = provider

        async def validate(self, request):
            calls.append(self.provider)
            raise AssertionError("objective failures must not reach paid reviewers")

    repository, batch, executor = prepare_validation(
        tmp_path,
        objective_validators=(ObjectiveValidator(),),
        quality_validators=(
            PaidReviewer("review-model-a"),
            PaidReviewer("review-model-b"),
        ),
    )

    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)[0]

    assert result["completed"] is False
    assert calls == ["objective"]
    assert job["status"] == JobStatus.NEEDS_REVIEW
    assert job["output"]["checks"][
        "objective_1.target_identity_similarity"
    ] == 0.31
    assert job["output"]["reviewer_verdicts"] == []


@pytest.mark.anyio
async def test_two_quality_reviewers_run_concurrently_and_store_separate_verdicts(
    tmp_path: Path,
) -> None:
    state = {"calls": [], "ready": asyncio.Event()}
    validators = (
        CoordinatedQualityValidator(state, "review-model-a", passed=True),
        CoordinatedQualityValidator(state, "review-model-b", passed=False),
    )
    repository, batch, executor = prepare_validation(
        tmp_path,
        quality_validators=validators,
    )

    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)[0]

    assert result["completed"] is False
    assert result["needs_review_job_ids"] == [job["id"]]
    assert state["calls"] == ["review-model-a", "review-model-b"]
    assert job["status"] == JobStatus.NEEDS_REVIEW
    assert job["output"]["passed"] is False
    assert job["output"]["checks"]["reviewer_a.quality_passed"] is True
    assert job["output"]["checks"]["reviewer_b.quality_passed"] is False
    assert job["output"]["checks"]["comparison.available"] is True
    assert job["output"]["reviewer_verdicts"] == [
        {
            "reviewer_id": "reviewer_a",
            "provider": "review-model-a",
            "passed": True,
            "checks": {"quality_passed": True},
            "reasons": [],
        },
        {
            "reviewer_id": "reviewer_b",
            "provider": "review-model-b",
            "passed": False,
            "checks": {"quality_passed": False},
            "reasons": ["quality failure"],
        },
    ]


@pytest.mark.anyio
async def test_failed_validation_is_isolated_before_peer_reviews_finish(
    tmp_path: Path,
) -> None:
    state = {"calls": [], "ready": asyncio.Event()}
    validators = (
        CoordinatedQualityValidator(state, "review-model-a", passed=True),
        CoordinatedQualityValidator(state, "review-model-b", passed=False),
    )
    repository, batch, executor = prepare_validation(
        tmp_path,
        quality_validators=validators,
    )
    current = repository.current_contract_stage_jobs(
        batch["id"],
        JobStage.VALIDATE,
    )[0]
    held_peer = repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        "held-peer-result",
        {
            **current["input"],
            "result_asset_id": "held-peer-result",
        },
    )
    repository.complete_job(
        held_peer["id"],
        status=JobStatus.NEEDS_REVIEW,
        provider="held-for-review",
        output={},
    )

    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    reviewed = repository.get_job(current["id"])

    assert result["completed"] is False
    assert set(result["needs_review_job_ids"]) == {
        current["id"],
        held_peer["id"],
    }
    assert reviewed["status"] == JobStatus.NEEDS_REVIEW
    assert reviewed["output"]["passed"] is False


@pytest.mark.anyio
async def test_validation_passes_only_when_both_quality_reviewers_pass(
    tmp_path: Path,
) -> None:
    state = {"calls": [], "ready": asyncio.Event()}
    validators = (
        CoordinatedQualityValidator(state, "review-model-a", passed=True),
        CoordinatedQualityValidator(state, "review-model-b", passed=True),
    )
    repository, batch, executor = prepare_validation(
        tmp_path,
        quality_validators=validators,
    )

    await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    job = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)[0]

    assert job["status"] == JobStatus.SUCCEEDED
    assert job["output"]["passed"] is True
    assert len(job["output"]["reviewer_verdicts"]) == 2


@pytest.mark.anyio
async def test_failed_validation_image_does_not_cancel_peer_image(
    tmp_path: Path,
) -> None:
    repository, batch, executor = prepare_validation(tmp_path)
    source = repository.list_assets(batch["id"], "source")[0]
    failed_path = tmp_path / "assets" / "failed-result.jpg"
    Image.new("RGB", (64, 64), "white").save(failed_path, format="JPEG")
    failed_result = repository.add_asset(
        batch_id=batch["id"],
        parent_asset_id=source["id"],
        kind="result",
        filename="failed-result.jpg",
        media_type="image/jpeg",
        storage_uri=failed_path.resolve().as_uri(),
        sha256="failed-result-hash",
    )
    repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        failed_result["id"],
        ValidationRequest(
            batch_id=batch["id"],
            result_asset_id=failed_result["id"],
            source_uri=source["storage_uri"],
            result_uri=failed_result["storage_uri"],
            expected_face_instance_ids=["face-1"],
            protected_face_instance_ids=[],
        ).model_dump(mode="json"),
    )

    class SelectiveQualityValidator:
        def __init__(self, provider: str):
            self.provider = provider

        async def validate(self, request):
            if request.result_asset_id == failed_result["id"]:
                raise RuntimeError("one image failed review")
            return ValidationResult(
                result_asset_id=request.result_asset_id,
                passed=True,
                provider=self.provider,
                checks={"quality_passed": True},
            )

    executor.quality_validators = (
        SelectiveQualityValidator("review-model-a"),
        SelectiveQualityValidator("review-model-b"),
    )
    result = await executor.execute_stage(batch["id"], JobStage.VALIDATE)
    jobs = repository.latest_stage_jobs(batch["id"], JobStage.VALIDATE)

    assert result["completed"] is False
    assert sorted(job["status"] for job in jobs) == [
        JobStatus.FAILED,
        JobStatus.SUCCEEDED,
    ]
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.FAILED
