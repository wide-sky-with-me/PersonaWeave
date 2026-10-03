"""
Pydantic Request and Response Schemas for PersonaWeave RESTful API.
"""

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from core.image_security import validate_public_url


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class PersonMappingItem(BaseModel):
    """Mapping from a target person index to a specific source identity gallery."""

    person_index: int = Field(
        ...,
        ge=1,
        le=20,
        description="Stable task-level target person ID across the complete image batch.",
    )
    target_reference_url: str = Field(
        ...,
        description="Representative batch image used to identify this target person in every task image.",
    )
    target_reference_person_index: int = Field(
        ...,
        ge=1,
        le=20,
        description="1-based face position of the target person in the representative image.",
    )
    source_identity_id: str | None = Field(
        default=None,
        description="Managed portrait-library identity ID; its full asset gallery is used as this person's source.",
    )
    source_asset_ids: list[str] = Field(
        default_factory=list,
        description="Specific managed portrait-library asset IDs to use as this person's source.",
    )
    source_manual_images: list[str] = Field(
        default_factory=list,
        description="Ad-hoc source reference as a managed API URL, public HTTP(S) URL, or capped data URI.",
    )
    source_mode: Literal["best-match", "gallery"] = Field(
        default="best-match",
        description="Strategy for multi-reference identity: 'best-match' or 'gallery'.",
    )

    @model_validator(mode="after")
    def _validate_source_evidence(self) -> "PersonMappingItem":
        has_explicit_sources = bool(self.source_asset_ids or self.source_manual_images)
        if self.source_identity_id and has_explicit_sources:
            raise ValueError(
                "source_identity_id selects the whole identity and cannot be combined with explicit source assets/images"
            )
        if not self.source_identity_id and not has_explicit_sources:
            raise ValueError(
                "PersonMappingItem requires one of source_identity_id, source_asset_ids, or source_manual_images"
            )
        return self


SwapperModelName = Literal["auto", "inswapper_128", "hyperswap_1c_256", "ghost_1_256"]


class SwapParameters(BaseModel):
    """Configuration parameters for face swapping and enhancement."""

    similarity_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Cosine similarity threshold for targeted face identification.",
    )
    identity_blend_strength: float = Field(
        default=0.9,
        ge=0.4,
        le=0.95,
        description="Visible source-identity blend strength for the InSwapper production path.",
    )
    enable_color_transfer: bool = Field(
        default=True,
        description="Backward-compatible master switch for color adaptation.",
    )
    color_transfer_mode: Literal["off", "boundary"] = Field(
        default="boundary",
        description="Apply no color transfer or production-safe boundary-ring adaptation.",
    )
    color_transfer_ring_strength: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description=(
            "Boundary-ring color adaptation opacity. Defaults to the safe value a prior full-face/"
            "wide-ring regression narrowed this down to; raising it revives that darkening/"
            "desaturation risk and is an explicit opt-in, not a scene-based auto-adjustment."
        ),
    )
    color_transfer_ring_width_px: float = Field(
        default=15.0,
        ge=1.0,
        le=64.0,
        description="Boundary-ring width in 512px-crop-equivalent pixels, scaled to the working crop size.",
    )
    enable_dense_mask: bool = Field(
        default=True,
        description="Whether to use 106-point dense landmark anatomical contour masking.",
    )
    manual_mask: str | None = Field(
        default=None,
        description="Optional base64-encoded PNG binary mask to protect user-painted regions.",
    )
    source_mode: Literal["best-match", "gallery"] = Field(
        default="best-match",
        description="Strategy for multi-reference identity: 'best-match' (dynamic pose matching) or 'gallery' (fused mean).",
    )
    swapper_model: SwapperModelName = Field(
        default="auto",
        description="Explicit swapper backend or per-face automatic routing.",
    )
    allow_research_models: bool = Field(
        default=False,
        description="Allow automatic routing to ResearchRAIL HyperSwap. Explicit requests still require this opt-in.",
    )
    enable_face_restoration: bool = Field(
        default=False,
        description=(
            "Opt-in CodeFormer detail restoration on the swapped region only, blended through the same "
            "mask that gates the swap itself. Off by default: this is a scoped reintroduction of "
            "restoration capability the project previously retired for risking identity/protected-pixel "
            "drift when applied full-face."
        ),
    )
    restoration_fidelity_weight: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="CodeFormer fidelity weight: 1.0 favors the input (swapped) identity, 0.0 favors generative restoration.",
    )
    restoration_strength: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Blend ratio between the raw swap output and the restored output within the swap mask.",
    )


class BatchImageItem(BaseModel):
    """One batch target with stable client correlation and its own protection mask."""

    client_target_id: str = Field(..., min_length=1, max_length=200)
    image_url: str
    manual_mask: str | None = Field(
        default=None,
        description="Optional base64-encoded PNG protection mask applied only to this image.",
    )


class BatchTaskCreateRequest(BaseModel):
    """Payload for submitting a new batch face swapping task."""

    ai_refinement: bool = Field(
        default=False,
        description="Explicitly authorize one evidence-led AI refinement turn after each successful local result.",
    )

    source_identity_id: str | None = Field(
        default=None,
        description="Managed portrait-library identity ID; its full asset gallery is used as the source.",
    )
    source_asset_ids: list[str] = Field(
        default_factory=list,
        description="Specific managed portrait-library asset IDs to use as the source.",
    )
    source_manual_images: list[str] = Field(
        default_factory=list,
        description="Ad-hoc source reference image: managed API URL, public HTTP(S) URL, or capped data URI.",
    )
    person_mappings: list[PersonMappingItem] = Field(
        default_factory=list,
        description="Optional multi-person mapping matrix for 1-to-1 role assignment in group photos.",
    )
    target_reference_url: str | None = Field(
        default=None,
        description="Optional managed API URL, public HTTP(S) URL, or capped data URI identifying the target person.",
    )
    person_index: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Optional 1-based index (left-to-right) of the person to replace in group photos.",
    )
    batch_images: list[BatchImageItem] = Field(
        default_factory=list,
        description="Typed batch targets with stable client IDs and optional per-image masks.",
    )
    batch_image_urls: list[str] = Field(
        default_factory=list,
        description="Deprecated URL-only batch input; accepted only when URLs are unique.",
        deprecated=True,
    )
    parameters: SwapParameters = Field(
        default_factory=SwapParameters,
        description="Algorithm hyperparameters and restoration settings.",
    )
    callback_url: str | None = Field(
        default=None,
        description="Optional webhook URL to receive HTTP POST notification upon task completion.",
    )

    @model_validator(mode="after")
    def validate_batch_images(self):
        legacy_source = bool(self.source_identity_id or self.source_asset_ids or self.source_manual_images)
        legacy_target = self.target_reference_url is not None or self.person_index is not None
        if self.person_mappings and (legacy_source or legacy_target):
            raise ValueError("person_mappings cannot be combined with legacy source or target selection fields")
        if self.source_identity_id and (self.source_asset_ids or self.source_manual_images):
            raise ValueError("source_identity_id cannot be combined with explicit source assets/images")
        # Pydantic emits the field's deprecation warning whenever normal attribute
        # access is used, including from inside this model. Read the raw model storage
        # for compatibility normalization so callers are warned only when they directly
        # use the retired attribute, not during every validation/polling operation.
        batch_images = self.__dict__.get("batch_images", [])
        legacy_urls = self.__dict__.get("batch_image_urls", [])
        if batch_images and legacy_urls:
            raise ValueError("Provide batch_images or deprecated batch_image_urls, not both")
        if not batch_images and not legacy_urls:
            raise ValueError("At least one batch image is required")
        if batch_images:
            ids = [item.client_target_id for item in batch_images]
            if len(ids) != len(set(ids)):
                raise ValueError("batch_images client_target_id values must be unique")
        elif len(legacy_urls) != len(set(legacy_urls)):
            raise ValueError("Deprecated batch_image_urls requires unique URLs for unambiguous correlation")
        if self.parameters.manual_mask and self.total_images != 1:
            raise ValueError("parameters.manual_mask is only compatible with a single batch image")
        return self

    @property
    def normalized_batch_images(self) -> list[BatchImageItem]:
        batch_images = self.__dict__.get("batch_images", [])
        if batch_images:
            return batch_images
        legacy_urls = self.__dict__.get("batch_image_urls", [])
        return [
            BatchImageItem(
                client_target_id=f"legacy-{hashlib.sha256(image_url.encode()).hexdigest()[:20]}",
                image_url=image_url,
                manual_mask=self.parameters.manual_mask,
            )
            for image_url in legacy_urls
        ]

    @property
    def total_images(self) -> int:
        return len(self.__dict__.get("batch_images", []) or self.__dict__.get("batch_image_urls", []))

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, value: str | None) -> str | None:
        if value is not None:
            validate_public_url(value)
        return value


class BatchTaskCreateData(BaseModel):
    task_id: str
    total_images: int
    status: TaskStatus
    estimated_seconds: float = Field(
        ge=0,
        description="Estimate derived from recent completed images; 0 when no reliable history exists.",
    )


class BatchTaskCreateResponse(BaseModel):
    code: int = 200
    message: str = "Batch task accepted successfully"
    data: BatchTaskCreateData


class ImageSwapResult(BaseModel):
    client_target_id: str
    original_url: str
    processed_url: str | None = None
    status: str = "SUCCESS"  # SUCCESS, PARTIAL, FAILED, SKIPPED
    faces_detected: int = 0
    faces_swapped: int = 0
    swapped_person_indices: list[int] = Field(default_factory=list)
    requested_person_indices: list[int] = Field(default_factory=list)
    person_outcomes: list[dict] = Field(default_factory=list)
    matched_similarities: list[float] = Field(default_factory=list)
    cost_ms: float = 0.0
    model_routing: list[dict] = Field(default_factory=list)
    warning: str | None = None
    error: str | None = None
    refinement_session_id: str | None = None
    refinement_error: str | None = None


class TaskDetailData(BaseModel):
    task_id: str
    status: TaskStatus
    progress: float = 0.0  # 0.0 to 1.0
    total_images: int = 0
    processed_images: int = 0
    success_count: int = 0
    failure_count: int = 0
    has_partial_failure: bool = False
    created_at: str
    completed_at: str | None = None
    results: list[ImageSwapResult] = Field(default_factory=list)
    error: str | None = None
    waiting_reason: str | None = None


class TaskDetailResponse(BaseModel):
    code: int = 200
    data: TaskDetailData


class BatchTaskSummary(BaseModel):
    """Lightweight history row; complete results are fetched only when opened."""

    task_id: str
    status: TaskStatus
    progress: float = 0.0
    total_images: int = 0
    processed_images: int = 0
    success_count: int = 0
    failure_count: int = 0
    has_partial_failure: bool = False
    viewable_results: int = 0
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class BatchTaskListResponse(BaseModel):
    code: int = 200
    data: list[BatchTaskSummary] = Field(default_factory=list)


class SyncSwapRequest(BaseModel):
    """Payload for synchronous single-image face swapping."""

    source_identity_id: str | None = Field(
        default=None,
        description="Managed portrait-library identity ID; its full asset gallery is used as the source.",
    )
    source_asset_ids: list[str] = Field(
        default_factory=list,
        description="Specific managed portrait-library asset IDs to use as the source.",
    )
    source_manual_images: list[str] = Field(
        default_factory=list,
        description="Ad-hoc source reference as a managed API URL, public HTTP(S) URL, or capped data URI.",
    )
    person_mappings: list[PersonMappingItem] = Field(
        default_factory=list,
        description="Optional multi-person mapping matrix for 1-to-1 role assignment in group photos.",
    )
    target_image_url: str = Field(
        ...,
        description="Target image as a managed API URL, public HTTP(S) URL, or capped data URI.",
    )
    target_reference_url: str | None = Field(
        default=None,
        description="Optional managed API URL, public HTTP(S) URL, or capped data URI identifying the target person.",
    )
    person_index: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Optional 1-based index (left-to-right) of person to replace.",
    )
    parameters: SwapParameters = Field(
        default_factory=SwapParameters,
        description="Algorithm parameters.",
    )
    return_base64: bool = Field(
        default=False,
        description="Whether to return image as base64 data URI in response.",
    )

    @model_validator(mode="after")
    def _validate_mapping_precedence(self) -> "SyncSwapRequest":
        legacy_source = bool(self.source_identity_id or self.source_asset_ids or self.source_manual_images)
        legacy_target = self.target_reference_url is not None or self.person_index is not None
        if self.person_mappings and (legacy_source or legacy_target):
            raise ValueError("person_mappings cannot be combined with legacy source or target selection fields")
        if self.source_identity_id and (self.source_asset_ids or self.source_manual_images):
            raise ValueError("source_identity_id cannot be combined with explicit source assets/images")
        return self


class SyncSwapData(BaseModel):
    processed_url: str | None = None
    processed_base64: str | None = None
    faces_detected: int
    faces_swapped: int
    swapped_person_indices: list[int] = Field(default_factory=list)
    requested_person_indices: list[int] = Field(default_factory=list)
    person_outcomes: list[dict] = Field(default_factory=list)
    matched_similarities: list[float]
    model_routing: list[dict] = Field(default_factory=list)
    warning: str | None = None
    cost_ms: float


class SyncSwapResponse(BaseModel):
    code: int = 200
    message: str = "Image swapped successfully"
    data: SyncSwapData


class LocalInferenceActivity(BaseModel):
    operation: str
    phase: str
    elapsed_seconds: float
    waiting_reason: str | None = None


class LocalInferenceStatus(BaseModel):
    queued: int = 0
    active: list[LocalInferenceActivity] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    app_name: str = "PersonaWeave"
    version: str = "0.1.0"
    pipeline_ready: bool = False
    providers: list[str] = Field(default_factory=list)
    gpu_available: bool = False
    ai_refinement_ready: bool = False
    model_providers: dict[str, list[str]] = Field(default_factory=dict)
    local_inference: LocalInferenceStatus | None = None


class FaceDetectionRequest(BaseModel):
    image_url: str = Field(..., description="Managed API URL, public HTTP(S) URL, or capped data URI")
    confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)


class DetectedFaceItem(BaseModel):
    index: int  # 1-based spatial index (left-to-right) on this image
    global_person_id: int | None = None  # Global consistent person ID across batch
    bbox: list[float]  # [x1, y1, x2, y2]
    kps: list[list[float]] | None = None
    score: float
    yaw: float = 0.0
    quality_score: float | None = None
    quality_tier: Literal["excellent", "usable", "low"] | None = None
    quality_notes: list[str] = Field(default_factory=list)


class FaceDetectionData(BaseModel):
    image_width: int
    image_height: int
    faces: list[DetectedFaceItem]


class FaceDetectionResponse(BaseModel):
    code: int = 200
    data: FaceDetectionData


class ClusterOccurrence(BaseModel):
    image_index: int
    face_index: int
    bbox: list[float]


class IdentityClusterItem(BaseModel):
    global_person_id: int
    frequency: int
    occurrences: list[ClusterOccurrence]


class ImageFacesMapping(BaseModel):
    image_index: int
    image_width: int
    image_height: int
    faces: list[DetectedFaceItem]


class FaceClusterRequest(BaseModel):
    image_urls: list[str] = Field(..., description="List of image URLs or base64 strings to cluster")
    similarity_threshold: float = Field(default=0.35, ge=0.0, le=1.0)


class FaceClusterData(BaseModel):
    total_clusters: int
    clusters: list[IdentityClusterItem]
    images: list[ImageFacesMapping]


class FaceClusterResponse(BaseModel):
    code: int = 200
    data: FaceClusterData


class VLMArbitrateRequest(BaseModel):
    reference_image_url: str = Field(..., description="Reference image containing the target person")
    reference_person_index: int | None = Field(default=1, ge=1, le=20, description="Person index in reference image")
    target_image_url: str = Field(..., description="Target image containing candidates to identify")


class VLMArbitrateData(BaseModel):
    matched_person_index: int | None
    confidence: float
    reasoning: str
    attributes_matched: list[str]
    model_used: str
    is_success: bool


class VLMArbitrateResponse(BaseModel):
    code: int = 200
    data: VLMArbitrateData


class RefinementIssue(BaseModel):
    category: str
    severity: Literal["low", "medium", "high"]
    region: str = ""
    description: str
    suggestion: str = ""


class RefinementReviewData(BaseModel):
    overall_score: float = Field(ge=0, le=100)
    anatomy_score: float = Field(ge=0, le=100)
    coherence_score: float = Field(ge=0, le=100)
    naturalness_score: float = Field(ge=0, le=100)
    person_locator: str = ""
    lighting_score: float = Field(ge=0, le=100)
    issues: list[RefinementIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    recommended_prompt: str = ""
    should_refine: bool = True
    model_used: str = ""


class RefinementTaskStatus(StrEnum):
    """Lifecycle for generated candidates that require explicit human acceptance."""

    QUEUED = "QUEUED"
    LOCALIZING = "LOCALIZING"
    REFINING = "REFINING"
    REVIEWING = "REVIEWING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ACCEPTING = "ACCEPTING"
    ACCEPTED = "ACCEPTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class QualityPersonEvaluation(BaseModel):
    person_id: int = Field(ge=1, le=1000)
    identity_satisfied: bool | None = None
    naturalness_satisfied: bool | None = None


class QualityEvaluationRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    candidate_digest: str = Field(pattern="^[a-f0-9]{64}$")
    verdict: Literal["ready", "needs_work", "unusable"]
    failure_stages: list[Literal["identity", "local", "refinement"]] = Field(default_factory=list, max_length=3)
    people: list[QualityPersonEvaluation] = Field(min_length=1, max_length=20)
    unrelated_changes: bool | None = None
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def unique_people_and_stages(self):
        if len({person.person_id for person in self.people}) != len(self.people):
            raise ValueError("Evaluation person IDs must be unique")
        self.failure_stages = list(dict.fromkeys(self.failure_stages))
        return self


class RefinementFinalPersonAssessment(BaseModel):
    person_id: int = Field(ge=1)
    status: Literal["resolved", "improved", "unchanged", "worsened", "unverifiable"]
    evidence: str


class RefinementFinalReview(BaseModel):
    overall_score: float = Field(ge=0, le=100)
    coherence_score: float = Field(ge=0, le=100)
    naturalness_score: float = Field(ge=0, le=100)
    people: list[RefinementFinalPersonAssessment] = Field(default_factory=list)
    unrequested_changes_detected: bool
    new_high_severity_issue: bool
    recommendation: Literal["accept", "review", "regenerate"]
    summary: str
    issues: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    model_used: str = ""
    review_error: str | None = None


class RefinementPersonProgress(BaseModel):
    person_id: int
    face_index: int
    status: str = "PENDING"
    progress: float = Field(default=0, ge=0, le=1)
    attempt: int = 1
    report: dict | None = None
    audit: dict | None = None
    error: str | None = None


class RefinementTaskData(BaseModel):
    task_id: str
    status: RefinementTaskStatus
    revision: int = 1
    generation_count: int | None = Field(default=None, ge=0)
    quality_provenance: dict | None = None
    progress: float = Field(default=0, ge=0, le=1)
    created_at: str
    completed_at: str | None = None
    people: list[RefinementPersonProgress] = Field(default_factory=list)
    final_review: RefinementFinalReview | None = None
    candidate_url: str | None = None
    candidate_digest: str | None = None
    accepted_digest: str | None = None
    accepted_url: str | None = None
    error: str | None = None


class RefinementTaskResponse(BaseModel):
    code: int = 200
    data: RefinementTaskData


class AIImageModelItem(BaseModel):
    alias: str
    provider: str
    model: str
    display_name: str
    description: str
    is_default: bool = False


class AIImageModelCatalog(BaseModel):
    default_alias: str
    models: list[AIImageModelItem]


class RefinementAcceptRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    candidate_digest: str


class ProjectSaveRequest(BaseModel):
    """A bounded editor document; media bytes are uploaded separately and referenced by digest."""

    expected_revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    document: dict[str, JsonValue]
    batch_ids: list[str] = Field(default_factory=list, max_length=100)
    session_ids: list[str] = Field(default_factory=list, max_length=200)


class ProjectAssetRequest(BaseModel):
    """Freeze exactly one source selection, including original managed portrait assets."""

    image: str | None = Field(default=None, min_length=1, max_length=28 * 1024 * 1024)
    asset_id: str | None = Field(default=None, min_length=1, max_length=100)
    identity_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_source(self):
        if sum(value is not None for value in (self.image, self.asset_id, self.identity_id)) != 1:
            raise ValueError("Provide exactly one image, asset_id or identity_id")
        return self
