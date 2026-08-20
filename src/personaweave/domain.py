from __future__ import annotations
import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class BatchStatus(StrEnum):
    DRAFT = "draft"
    ANALYZING = "analyzing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY = "ready"
    SWAPPING = "swapping"
    VALIDATING = "validating"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"


class AssetKind(StrEnum):
    SOURCE = "source"
    REFERENCE = "reference"
    RESULT = "result"


class MappingAction(StrEnum):
    REPLACE = "replace"
    EDIT = "edit"
    KEEP = "keep"


def bbox_iou(left: Any, right: Any) -> float:
    """Return intersection-over-union for bbox models or JSON-style mappings."""
    def value(box: Any, key: str) -> float:
        return float(box[key] if isinstance(box, dict) else getattr(box, key))

    left_x = value(left, "x")
    left_y = value(left, "y")
    left_width = value(left, "width")
    left_height = value(left, "height")
    right_x = value(right, "x")
    right_y = value(right, "y")
    right_width = value(right, "width")
    right_height = value(right, "height")
    intersection_width = max(
        0.0,
        min(left_x + left_width, right_x + right_width) - max(left_x, right_x),
    )
    intersection_height = max(
        0.0,
        min(left_y + left_height, right_y + right_height) - max(left_y, right_y),
    )
    intersection = intersection_width * intersection_height
    union = left_width * left_height + right_width * right_height - intersection
    return intersection / union if union > 0 else 0.0

class ReviewReason(StrEnum):
    REFERENCE_INSUFFICIENCY = "reference_insufficiency"
    MASK_UNCERTAIN = "mask_uncertain"


ReviewReasonSet: TypeAlias = frozenset[ReviewReason]

SourceFaceCondition: TypeAlias = Literal[
    "complete",
    "minor_occlusion",
    "major_occlusion",
    "source_boundary",
    "unusable",
]
SOURCE_FACE_CONDITIONS_REQUIRING_REVIEW = frozenset({
    "major_occlusion",
    "source_boundary",
    "unusable",
})


def source_face_condition_requires_review(
    condition: object,
    *,
    legacy_unrecoverable_occlusion: bool = False,
) -> bool:
    if condition is None:
        return legacy_unrecoverable_occlusion
    return condition in SOURCE_FACE_CONDITIONS_REQUIRING_REVIEW



class JobStage(StrEnum):
    ANALYZE = "analyze"
    SWAP = "swap"
    VALIDATE = "validate"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class JobPhase(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    MATERIALIZING = "materializing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class BoundingBox(BaseModel):
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]


class NormalizedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    y: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class NormalizedBoundingBox(BaseModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def validate_canvas_bounds(self) -> "NormalizedBoundingBox":
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("normalized bbox must remain inside the image canvas")
        return self


class MissingFaceRegion(BaseModel):
    bbox: NormalizedBoundingBox
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence: str = Field(default="", max_length=300)


class PersonDetectionAudit(BaseModel):
    available: bool = True
    complete: bool
    detected_face_count: Annotated[int, Field(ge=0, le=128)]
    visible_face_count: Annotated[int | None, Field(default=None, ge=0, le=128)]
    missing_regions: list[MissingFaceRegion] = Field(default_factory=list, max_length=32)
    requires_manual_review: bool = False
    summary: str = Field(default="", max_length=1000)


class PersonAppearance(BaseModel):
    """Visible source traits used to keep a facial edit coherent with the body."""

    apparent_age: str = Field(default="", max_length=120)
    face_features: str = Field(default="", max_length=500)
    hair: str = Field(default="", max_length=300)
    eyewear: str = Field(default="", max_length=200)
    accessories: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=400)
    clothing: str = Field(default="", max_length=500)
    pose: str = Field(default="", max_length=400)
    lighting_occlusion: str = Field(default="", max_length=400)


class FaceObservation(BaseModel):
    external_face_id: str
    bbox: BoundingBox
    detection_confidence: Annotated[float, Field(ge=0, le=1)]
    suggested_person_key: str | None = None
    identity_confidence: Annotated[float | None, Field(default=None, ge=0, le=1)]
    needs_confirmation: bool = True
    appearance: PersonAppearance = Field(default_factory=PersonAppearance)


class MappingDecision(BaseModel):
    person_id: str
    action: MappingAction
    target_identity_id: str | None = None
    instruction: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_target_identity(self) -> "MappingDecision":
        if self.action is MappingAction.REPLACE and not self.target_identity_id:
            raise ValueError("replace mappings require target_identity_id")
        if self.action in {MappingAction.EDIT, MappingAction.KEEP} and self.target_identity_id:
            raise ValueError("edit and keep mappings cannot have target_identity_id")
        return self

HeadAttribute: TypeAlias = Literal[
    "face_identity",
    "apparent_age",
    "face_features",
    "hair",
    "eyewear",
    "accessories",
    "makeup",
]
HEAD_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "face_identity",
        "apparent_age",
        "face_features",
        "hair",
        "eyewear",
        "accessories",
        "makeup",
    }
)


class TargetIdentityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: str = Field(min_length=1, max_length=128)
    identity_code: str = Field(pattern=r"^T\d+$", max_length=40)
    apparent_age: str = Field(default="unclear", max_length=120)
    face_features: str = Field(default="unclear", max_length=500)
    hair: str = Field(default="unclear", max_length=300)
    eyewear: str = Field(default="unclear", max_length=200)
    accessories: str = Field(default="unclear", max_length=300)
    makeup: str = Field(default="unclear", max_length=300)
    stable_identity_anchors: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=16)
    variable_traits: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=16)
    unknown_evidence: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=16)
    unsupported_evidence: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=16)
    reference_board_codes: list[
        Annotated[str, Field(pattern=r"^R\d+$", max_length=40)]
    ] = Field(default_factory=list, max_length=32)
    reference_board_digest: str = Field(
        default="4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e37b544091d86d98b9ed",
        pattern=r"^[0-9a-f]{64}$",
    )


class ReferenceAssetEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^R\d+$", max_length=40)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

class CompiledFaceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_instance_id: str = Field(min_length=1, max_length=128)
    person_id: str = Field(min_length=1, max_length=128)
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    bbox: BoundingBox
    source_appearance: PersonAppearance
    analysis_resolution: Literal[
        "manual_bbox",
        "keep_original",
        "accept_occlusion",
    ] | None = None


class SourceAssetEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^I\d+$", max_length=40)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    faces: list[CompiledFaceEvidence] = Field(max_length=128)
    editable_face_instance_ids: list[str] = Field(default_factory=list, max_length=128)
    authorization_mask: "CanonicalMaskEvidence | None" = None

    @computed_field
    @property
    def requires_authorization_mask(self) -> bool:
        return bool(self.editable_face_instance_ids)

    @model_validator(mode="after")
    def validate_editable_projection(self) -> "SourceAssetEvidence":
        face_ids = [face.face_instance_id for face in self.faces]
        if len(face_ids) != len(set(face_ids)):
            raise ValueError("compiled face evidence must be unique per source")
        if len(self.editable_face_instance_ids) != len(set(self.editable_face_instance_ids)):
            raise ValueError("editable face instance IDs must be unique per source")
        if any(not face_id for face_id in self.editable_face_instance_ids):
            raise ValueError("editable face instance IDs cannot be empty")
        if not set(self.editable_face_instance_ids).issubset(face_ids):
            raise ValueError("every editable face instance ID must resolve to compiled face evidence")
        return self


class ReferenceSufficiency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: str = Field(min_length=1, max_length=128)
    identity_code: str = Field(pattern=r"^T\d+$", max_length=40)
    reference_count: Annotated[int, Field(ge=0, le=32)]
    visible_views: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=16
    )
    missing_views: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=16
    )
    confidence: Annotated[float, Field(ge=0, le=1)] = 0
    sufficient: bool = False
    requires_manual_review: bool = True
    summary: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def require_review_when_insufficient(self) -> "ReferenceSufficiency":
        if not self.sufficient and not self.requires_manual_review:
            raise ValueError("insufficient references must require manual review")
        return self


class SourceOccurrenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_instance_id: str = Field(min_length=1, max_length=128)
    bbox: BoundingBox
    face_area_pixels: Annotated[int, Field(gt=0)]
    pose: str = Field(default="unclear", max_length=400)
    lighting_occlusion_blur: str = Field(default="unclear", max_length=400)


class SourceOccurrenceReferenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_instance_id: str = Field(min_length=1, max_length=128)
    reference_views_used: list[
        Annotated[str, Field(min_length=1, max_length=80)]
    ] = Field(default_factory=list, max_length=16)
    missing_coverage: list[
        Annotated[str, Field(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=16)
    sufficient: bool = False
    requires_manual_review: bool = True
    summary: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def require_review_when_insufficient(self) -> "SourceOccurrenceReferenceAssessment":
        if not self.sufficient and not self.requires_manual_review:
            raise ValueError("insufficient occurrence coverage must require manual review")
        return self


class SourceOperationReferenceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=128)
    source_asset_code: str = Field(pattern=r"^I\d+$", max_length=40)
    person_id: str = Field(min_length=1, max_length=128)
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    occurrences: list[SourceOccurrenceEvidence] = Field(min_length=1, max_length=32)
    occurrence_assessments: list[SourceOccurrenceReferenceAssessment] = Field(
        min_length=1, max_length=32
    )
    sufficient: bool = False
    summary: str = Field(default="", max_length=1000)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return (
            not self.sufficient
            or any(
                item.requires_manual_review
                for item in self.occurrence_assessments
            )
        )

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value

    @model_validator(mode="after")
    def validate_occurrence_coverage(self) -> "SourceOperationReferenceCoverage":
        occurrence_ids = [item.face_instance_id for item in self.occurrences]
        assessment_ids = [
            item.face_instance_id for item in self.occurrence_assessments
        ]
        if (
            len(assessment_ids) != len(set(assessment_ids))
            or set(assessment_ids) != set(occurrence_ids)
        ):
            raise ValueError("reference coverage must assess every source occurrence exactly once")
        expected_sufficient = all(
            item.sufficient for item in self.occurrence_assessments
        )
        if self.sufficient != expected_sufficient:
            raise ValueError("source coverage sufficiency must match occurrence assessments")
        return self

class AttributePermission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    editable: list[HeadAttribute] = Field(default_factory=list, max_length=7)
    protected: list[HeadAttribute] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def validate_complete_partition(self) -> "AttributePermission":
        editable = list(self.editable)
        protected = list(self.protected)
        if len(editable) != len(set(editable)) or len(protected) != len(set(protected)):
            raise ValueError("attribute permissions cannot contain duplicates")
        if set(editable) & set(protected):
            raise ValueError("editable and protected attributes must be disjoint")
        if set(editable) | set(protected) != HEAD_ATTRIBUTES:
            raise ValueError("attribute permissions must partition every supported head attribute")
        return self


class CompiledIdentityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: TargetIdentityProfile
    reference_assessment: ReferenceSufficiency
    reference_assets: list[ReferenceAssetEvidence] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_identity_consistency(self) -> "CompiledIdentityEvidence":
        if (
            self.profile.identity_id != self.reference_assessment.identity_id
            or self.profile.identity_code != self.reference_assessment.identity_code
        ):
            raise ValueError("identity profile and reference assessment must describe the same identity")
        if self.reference_assessment.reference_count != len(self.reference_assets):
            raise ValueError("reference assessment count must match reference asset evidence")
        board = [
            {"code": asset.code, "sha256": asset.sha256}
            for asset in self.reference_assets
        ]
        if self.profile.reference_board_codes != [item["code"] for item in board]:
            raise ValueError("target profile reference-board order is stale")
        digest = hashlib.sha256(
            json.dumps(
                board,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.profile.reference_board_digest != digest:
            raise ValueError("target profile reference-board digest is stale")
        return self


class CompiledOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1, max_length=128)
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    action: MappingAction
    target_identity_id: str | None = Field(default=None, max_length=128)
    target_identity_code: str | None = Field(default=None, pattern=r"^T\d+$", max_length=40)
    instruction: str = Field(default="", max_length=2000)
    permissions: AttributePermission
    target_profile: TargetIdentityProfile | None = None
    reference_assessment: ReferenceSufficiency | None = None
    reference_assets: list[ReferenceAssetEvidence] = Field(default_factory=list, max_length=32)
    source_reference_coverage: list[SourceOperationReferenceCoverage] = Field(
        default_factory=list, max_length=128
    )
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value

    @model_validator(mode="after")
    def validate_operation(self) -> CompiledOperation:
        if self.review_reasons:
            raise ValueError("compiled operations cannot carry source-varying review reasons")
        if self.action is MappingAction.REPLACE:
            if not self.target_identity_id or not self.target_identity_code:
                raise ValueError("replace operations require a target identity")
            if not self.target_profile or not self.reference_assessment:
                raise ValueError("replace operations require profile and reference evidence")
            if "face_identity" not in self.permissions.editable:
                raise ValueError("replace operations must authorize face_identity")
            if self.reference_assessment.reference_count != len(self.reference_assets):
                raise ValueError("operation reference evidence is incomplete")
        elif any(
            (
                self.target_identity_id,
                self.target_identity_code,
                self.target_profile,
                self.reference_assessment,
                self.reference_assets,
                self.source_reference_coverage,
            )
        ):
            raise ValueError("non-replace operations cannot carry target identity evidence")
        return self


class CompiledBatchContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = "1"
    task_instruction: str = Field(max_length=4000)
    max_iterations: Annotated[int, Field(ge=1, le=10)]
    operations: list[CompiledOperation] = Field(max_length=128)
    identities: list[CompiledIdentityEvidence] = Field(default_factory=list, max_length=64)
    source_assets: list[SourceAssetEvidence] = Field(min_length=1, max_length=128)

    @computed_field
    @property
    def review_reasons(self) -> ReviewReasonSet:
        return frozenset(
            {ReviewReason.REFERENCE_INSUFFICIENCY}
            if any(
                coverage.requires_manual_review
                for operation in self.operations
                for coverage in operation.source_reference_coverage
            )
            else set()
        )

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self) -> "CompiledBatchContract":
        person_ids = [operation.person_id for operation in self.operations]
        if len(person_ids) != len(set(person_ids)):
            raise ValueError("compiled operations must cover each person once")
        operations_by_person = {
            operation.person_id: operation for operation in self.operations
        }
        identity_ids = [item.profile.identity_id for item in self.identities]
        if len(identity_ids) != len(set(identity_ids)):
            raise ValueError("compiled identity evidence must be unique")
        coverage_keys = [
            (coverage.source_asset_id, coverage.person_id)
            for operation in self.operations
            for coverage in operation.source_reference_coverage
        ]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError("source-operation reference coverage must be unique")
        source_ids = [item.asset_id for item in self.source_assets]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("compiled source asset evidence must be unique")
        for source in self.source_assets:
            for face in source.faces:
                operation = operations_by_person.get(face.person_id)
                if operation is None or operation.person_code != face.person_code:
                    raise ValueError("compiled face evidence does not match a person operation")
            expected_editable = [
                face.face_instance_id
                for face in source.faces
                if (
                    operations_by_person[face.person_id].action is not MappingAction.KEEP
                    and face.analysis_resolution != "keep_original"
                )
            ]
            if source.editable_face_instance_ids != expected_editable:
                raise ValueError(
                    "editable face instance IDs do not match compiled face operations"
                )
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude_computed_fields=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class AnalysisRequest(BaseModel):
    batch_id: str
    asset_id: str
    asset_uri: str
    operation: Literal["analyze_faces"] = "analyze_faces"
    analysis_pass: Literal["primary", "targeted"] = "primary"
    region_hints: list[NormalizedBoundingBox] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_targeted_hints(self) -> "AnalysisRequest":
        if self.analysis_pass == "targeted" and not self.region_hints:
            raise ValueError("targeted analysis requires at least one region hint")
        if self.analysis_pass == "primary" and self.region_hints:
            raise ValueError("primary analysis cannot include region hints")
        return self


class FaceLocalizationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_code: str = Field(min_length=1, max_length=80)
    attempt: Annotated[int, Field(ge=1, le=4)]
    bbox: BoundingBox
    confidence: Annotated[float, Field(ge=0, le=1)]
    full_face_visible: bool
    crop_contains_all_source_visible_face: bool = True
    matches_expected_occurrence: bool = True
    source_face_condition: SourceFaceCondition = "complete"
    face_centered: bool
    tight_face_crop: bool
    accepted: bool
    boundary_issue: Literal[
        "none",
        "top_clipped",
        "bottom_clipped",
        "left_clipped",
        "right_clipped",
        "multiple_clipped",
    ] = "none"
    unrecoverable_occlusion: bool = False
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_source_face_condition(cls, value: object) -> object:
        if not isinstance(value, dict) or "source_face_condition" in value:
            return value
        return {
            **value,
            "source_face_condition": (
                "major_occlusion"
                if value.get("unrecoverable_occlusion")
                else "complete"
            ),
        }

    @model_validator(mode="after")
    def canonicalize_source_review_flag(self) -> "FaceLocalizationAttempt":
        self.unrecoverable_occlusion = source_face_condition_requires_review(
            self.source_face_condition
        )
        return self


class AnalysisResult(BaseModel):
    asset_id: str
    faces: list[FaceObservation]
    provider: str
    model: str | None = None
    detection_audit: PersonDetectionAudit | None = None
    localization_attempts: list[FaceLocalizationAttempt] = Field(
        default_factory=list,
        max_length=256,
    )


class SwapOperation(BaseModel):
    face_instance_id: str
    bbox: BoundingBox
    target_identity_id: str | None = None
    source_person_id: str | None = None
    reference_uris: list[str] = Field(default_factory=list)
    source_person_code: str | None = None
    target_identity_code: str | None = None
    reference_sha256s: list[
        Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ] = Field(default_factory=list)
    instruction: str = Field(default="", max_length=2000)
    source_appearance: PersonAppearance = Field(default_factory=PersonAppearance)
    permissions: AttributePermission | None = None
    target_profile: TargetIdentityProfile | None = None
    reference_assessment: ReferenceSufficiency | None = None
    source_reference_coverage: SourceOperationReferenceCoverage | None = None
    contract_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value

    @model_validator(mode="after")
    def validate_review_reasons(self) -> SwapOperation:
        expected_reference = (
            frozenset({ReviewReason.REFERENCE_INSUFFICIENCY})
            if self.source_reference_coverage is not None
            and self.source_reference_coverage.requires_manual_review
            else frozenset()
        )
        if self.review_reasons and ReviewReason.MASK_UNCERTAIN in self.review_reasons:
            expected_reference = expected_reference | frozenset({ReviewReason.MASK_UNCERTAIN})
        if self.review_reasons != expected_reference:
            raise ValueError("swap operation review reasons do not match source evidence")
        return self

_ALL_PEOPLE_FORMAL_EDIT_PATTERN = re.compile(
    r"(?:把|将|对|让)?\s*(?:所有|全部|每个|全体|全员)(?:可见)?"
    r"(?:人物|人脸|人员|人)(?:的脸|的面部|面部)?\s*(?:都|全部|全都)?\s*"
    r"(?:替换(?:为|成)?|换成|变成|改成|修改|编辑)"
)


def instruction_requests_all_people_edit(instruction: str) -> bool:
    return bool(_ALL_PEOPLE_FORMAL_EDIT_PATTERN.search(instruction.upper()))


def formal_selection_mode(
    task_instruction: str,
) -> Literal["numbered", "all", "prompt"]:
    if re.search(r"@(P\d+)\b", task_instruction.upper()):
        return "numbered"
    if instruction_requests_all_people_edit(task_instruction):
        return "all"
    return "prompt"


def project_formal_swap_operations(
    contract: CompiledBatchContract,
    source: SourceAssetEvidence,
) -> list[SwapOperation]:
    """Project immutable per-face operations, deliberately excluding runtime URIs."""
    contract_hash = contract.sha256()
    operations_by_person = {
        operation.person_id: operation for operation in contract.operations
    }
    editable_ids = set(source.editable_face_instance_ids)
    projected: list[SwapOperation] = []
    for face in source.faces:
        if face.face_instance_id not in editable_ids:
            continue
        compiled = operations_by_person[face.person_id]
        coverage = next(
            (
                item
                for item in compiled.source_reference_coverage
                if item.source_asset_id == source.asset_id
            ),
            None,
        )
        review_reasons = frozenset(
            {
                *(
                    {ReviewReason.REFERENCE_INSUFFICIENCY}
                    if coverage is not None and coverage.requires_manual_review
                    else set()
                ),
                *(
                    {ReviewReason.MASK_UNCERTAIN}
                    if source.authorization_mask is not None
                    and ReviewReason.MASK_UNCERTAIN
                    in source.authorization_mask.review_reasons
                    else set()
                ),
            }
        )
        projected.append(
            SwapOperation(
                face_instance_id=face.face_instance_id,
                source_person_id=face.person_id,
                source_person_code=face.person_code,
                bbox=face.bbox,
                source_appearance=face.source_appearance,
                target_identity_id=compiled.target_identity_id,
                target_identity_code=compiled.target_identity_code,
                reference_sha256s=[
                    asset.sha256 for asset in compiled.reference_assets
                ],
                instruction=compiled.instruction,
                permissions=compiled.permissions,
                target_profile=compiled.target_profile,
                reference_assessment=compiled.reference_assessment,
                source_reference_coverage=coverage,
                contract_hash=contract_hash,
                review_reasons=review_reasons,
            )
        )
    return projected


def compile_formal_instruction(
    contract: CompiledBatchContract,
    source: SourceAssetEvidence,
) -> str:
    """Compile the immutable provider instruction solely from contract evidence."""
    contract_hash = contract.sha256()
    operations_by_person = {
        operation.person_id: operation for operation in contract.operations
    }
    editable_ids = set(source.editable_face_instance_ids)
    selection_mode = formal_selection_mode(contract.task_instruction)
    replacement_lines: list[str] = []
    protected_person_codes: list[str] = []
    for face in source.faces:
        operation = operations_by_person[face.person_id]
        if face.face_instance_id not in editable_ids:
            protected_person_codes.append(face.person_code)
            continue
        prefix = (
            f"- Replace @{face.person_code} with target identity "
            f"@{operation.target_identity_code} only at bbox "
            if operation.target_identity_code
            else (
                f"- Treat @{face.person_code} at bbox as a candidate "
                "selected by the immutable task: "
                if selection_mode == "prompt"
                else (
                    f"- Apply the immutable text-defined edit to "
                    f"@{face.person_code} only at bbox "
                )
            )
        )
        line = (
            prefix
            + (
                f"({face.bbox.x},{face.bbox.y},"
                f"{face.bbox.width},{face.bbox.height})."
            )
            + " Editable head attributes: "
            + ", ".join(operation.permissions.editable)
            + ". Protected head attributes: "
            + ", ".join(operation.permissions.protected)
            + "."
        )
        if operation.instruction:
            line += f" Additional requirement: {operation.instruction}"
        replacement_lines.append(line)
    protected_line = (
        "- Preserve these source people without identity or appearance changes: "
        + ", ".join(f"@{code}" for code in sorted(set(protected_person_codes)))
        + ". Also preserve every other person not explicitly listed for replacement."
        if protected_person_codes
        else "- Do not change any person not explicitly listed for replacement."
    )
    return "\n".join(
        [
            f"SOURCE IMAGE: @{source.code}. Always edit this original image.",
            f"CONFIRMED CONTRACT SHA-256: {contract_hash}.",
            (
                "IMMUTABLE TASK REQUIREMENT: "
                f"{contract.task_instruction or 'Apply only the confirmed identity mappings.'}"
            ),
            "NUMBERED EDIT TARGETS OR CANDIDATES:",
            *replacement_lines,
            *(
                [
                    "Select and edit only candidates that visibly match the immutable "
                    "natural-language requirement; do not edit every candidate by default."
                ]
                if selection_mode == "prompt"
                else []
            ),
            protected_line,
            (
                "Preserve the exact canvas dimensions, crop, composition, bodies, "
                "clothes, pose, expression, gaze, occlusions, background, objects, "
                "text, logos, lighting, color, sharpness, noise and depth of field, "
                "plus every protected head attribute."
            ),
        ]
    )


class SwapRequest(BaseModel):
    batch_id: str
    asset_id: str
    asset_uri: str
    replacements: list[SwapOperation]
    protected_face_instance_ids: list[str]
    source_image_code: str | None = None
    task_instruction: str = ""
    compiled_instruction: str = ""
    adaptive_instruction: str = ""
    selection_mode: Literal["numbered", "all", "prompt"] = "numbered"
    max_iterations: Annotated[int, Field(ge=1, le=10)] = 3
    iteration: Annotated[int, Field(ge=1, le=10)] = 1
    image_model: str = "gpt-image-2"
    image_api_mode: Literal["json_generations", "multipart_edits"] = "multipart_edits"
    image_api_path: str = "/images/edits"
    image_output_size: str = "1024x1024"
    image_response_format: Literal["url", "b64_json"] | None = None
    operation: Literal["swap_face"] = "swap_face"
    compiled_contract: CompiledBatchContract | None = None
    contract_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)
    authorization_mask_uri: str | None = None
    authorization_mask_sha256: str | None = None
    authorization_mask_width: int | None = None
    authorization_mask_height: int | None = None
    preserve_outside_mask: bool = False

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value


    @model_validator(mode="after")
    def validate_contract_hash(self) -> SwapRequest:
        if self.compiled_contract is None:
            if self.contract_hash is not None:
                raise ValueError("contract_hash requires compiled_contract")
            return self
        contract = self.compiled_contract
        expected_hash = contract.sha256()
        if self.contract_hash != expected_hash:
            raise ValueError("contract_hash does not match compiled_contract")
        if self.image_api_mode != "multipart_edits":
            raise ValueError("formal generation requires multipart_edits")
        source_evidence = next(
            (
                source
                for source in contract.source_assets
                if source.asset_id == self.asset_id
            ),
            None,
        )
        if source_evidence is None or not source_evidence.editable_face_instance_ids:
            raise ValueError("formal generation source is not editable")
        if self.source_image_code != source_evidence.code:
            raise ValueError("source image code does not match compiled source evidence")
        if self.task_instruction != contract.task_instruction:
            raise ValueError("task instruction does not match compiled contract")
        if self.max_iterations != contract.max_iterations:
            raise ValueError("max iterations do not match compiled contract")
        expected_selection_mode = formal_selection_mode(contract.task_instruction)
        if self.selection_mode != expected_selection_mode:
            raise ValueError("selection mode does not match compiled task instruction")
        expected_instruction = compile_formal_instruction(contract, source_evidence)
        if self.compiled_instruction != expected_instruction:
            raise ValueError("compiled instruction does not match compiled contract")

        expected_replacements = project_formal_swap_operations(
            contract,
            source_evidence,
        )
        replacement_ids = [operation.face_instance_id for operation in self.replacements]
        if replacement_ids != [
            operation.face_instance_id for operation in expected_replacements
        ]:
            raise ValueError("swap replacements do not match the canonical editable-face projection")
        editable_ids = set(source_evidence.editable_face_instance_ids)
        expected_protected_ids = [
            face.face_instance_id for face in source_evidence.faces
            if face.face_instance_id not in editable_ids
        ]
        if self.protected_face_instance_ids != expected_protected_ids:
            raise ValueError("protected faces do not match compiled source evidence")

        for replacement, expected_replacement in zip(
            self.replacements,
            expected_replacements,
            strict=True,
        ):
            replacement_evidence = replacement.model_dump(
                mode="json",
                exclude={"reference_uris"},
                exclude_computed_fields=True,
            )
            expected_evidence = expected_replacement.model_dump(
                mode="json",
                exclude={"reference_uris"},
                exclude_computed_fields=True,
            )
            if (
                replacement_evidence != expected_evidence
                or len(replacement.reference_uris)
                != len(expected_replacement.reference_sha256s)
            ):
                raise ValueError("swap replacement does not match compiled operation evidence")
        expected_reasons = frozenset(
            reason
            for operation in self.replacements
            for reason in operation.review_reasons
        )
        if (
            source_evidence.authorization_mask is not None
            and ReviewReason.MASK_UNCERTAIN
            in source_evidence.authorization_mask.review_reasons
        ):
            expected_reasons = expected_reasons | frozenset({ReviewReason.MASK_UNCERTAIN})
        if self.review_reasons != expected_reasons:
            raise ValueError("swap request review reasons do not match source operations")
        return self


class SwapResult(BaseModel):
    source_asset_id: str
    output_uri: str
    output_sha256: str
    provider: str
    model: str | None = None
    mask_uris: list[str] = Field(default_factory=list)
    completed_iterations: int = 1
    generation_plan: dict[str, object] = Field(default_factory=dict)
    generation_review: dict[str, object] = Field(default_factory=dict)
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value


class ValidationSubject(BaseModel):
    face_instance_id: str
    source_person_code: str
    target_identity_code: str | None = None
    bbox: BoundingBox
    reference_uris: list[str] = Field(default_factory=list)
    permissions: AttributePermission | None = None
    target_profile: TargetIdentityProfile | None = None
    reference_assessment: ReferenceSufficiency | None = None
    source_reference_coverage: SourceOperationReferenceCoverage | None = None
    contract_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value

    @model_validator(mode="after")
    def validate_review_reasons(self) -> ValidationSubject:
        expected_reference = (
            frozenset({ReviewReason.REFERENCE_INSUFFICIENCY})
            if self.source_reference_coverage is not None
            and self.source_reference_coverage.requires_manual_review
            else frozenset()
        )
        if self.review_reasons != expected_reference:
            raise ValueError("validation subject reasons do not match source evidence")
        return self


class ValidationPeer(BaseModel):
    result_asset_id: str
    result_uri: str
    source_image_code: str
    target_identity_codes: list[str] = Field(default_factory=list)


class ValidationRequest(BaseModel):
    batch_id: str
    contract_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_asset_id: str
    source_uri: str
    result_uri: str
    expected_face_instance_ids: list[str]
    protected_face_instance_ids: list[str]
    source_image_code: str | None = None
    task_instruction: str = ""
    compiled_instruction: str = ""
    expected_subjects: list[ValidationSubject] = Field(default_factory=list)
    peer_results: list[ValidationPeer] = Field(default_factory=list)
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)
    authorization_mask_uri: str | None = None
    authorization_mask_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    authorization_mask_width: int | None = Field(default=None, gt=0)
    authorization_mask_height: int | None = Field(default=None, gt=0)
    preserve_outside_mask: bool = False
    operation: Literal["validate_result"] = "validate_result"

    @model_validator(mode="after")
    def validate_review_reasons(self) -> ValidationRequest:
        expected = frozenset(
            reason
            for subject in self.expected_subjects
            for reason in subject.review_reasons
        )
        if self.review_reasons != expected:
            raise ValueError("validation request reasons do not match subjects")
        return self


class ValidationReviewerVerdict(BaseModel):
    reviewer_id: Literal["reviewer_a", "reviewer_b"]
    provider: str = Field(min_length=1, max_length=160)
    passed: bool
    checks: dict[str, bool | float | str]
    reasons: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    result_asset_id: str
    passed: bool
    provider: str
    checks: dict[str, bool | float | str]
    reasons: list[str] = Field(default_factory=list)
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)
    reviewer_verdicts: list[ValidationReviewerVerdict] = Field(
        default_factory=list,
        max_length=2,
    )

    @model_validator(mode="after")
    def validate_reviewer_verdicts(self) -> ValidationResult:
        if not self.reviewer_verdicts:
            return self
        reviewer_ids = {verdict.reviewer_id for verdict in self.reviewer_verdicts}
        if reviewer_ids != {"reviewer_a", "reviewer_b"}:
            raise ValueError("validation requires distinct reviewer A and B verdicts")
        reviewer_providers = {
            verdict.provider for verdict in self.reviewer_verdicts
        }
        if len(reviewer_providers) != 2:
            raise ValueError("validation reviewers must use distinct providers")
        if self.passed and not all(
            verdict.passed for verdict in self.reviewer_verdicts
        ):
            raise ValueError("validation cannot pass when either reviewer failed")
        return self

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)

    @model_validator(mode="before")
    @classmethod
    def discard_manual_review_projection(cls, value: object) -> object:
        if isinstance(value, dict) and "requires_manual_review" in value:
            value = {**value}
            value.pop("requires_manual_review")
        return value


class SemanticMaskContour(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["include", "exclude"]
    face_instance_id: str = Field(min_length=1, max_length=128)
    person_id: str = Field(min_length=1, max_length=128)
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    permissions: AttributePermission
    points: list[NormalizedPoint] = Field(min_length=3, max_length=256)
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 1.0
    evidence: str = Field(default="", max_length=2000)


class SemanticMaskProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=128)
    source_asset_code: str = Field(pattern=r"^I\d+$", max_length=40)
    contours: list[SemanticMaskContour] = Field(default_factory=list, max_length=128)
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 1.0
    summary: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=2000)
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)


class CanonicalMaskEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=128)
    source_asset_code: str = Field(pattern=r"^I\d+$", max_length=40)
    width: Annotated[int, Field(ge=1, le=65535)]
    height: Annotated[int, Field(ge=1, le=65535)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    proposal: SemanticMaskProposal
    status: Literal["proposed", "confirmed", "validated"] = "proposed"
    review_reasons: ReviewReasonSet = Field(default_factory=frozenset)

    @computed_field
    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_reasons)
