"""Pydantic contracts for every structured VLM response."""

import json
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class VLMContract(BaseModel):
    """Forbid invented fields while allowing explicit provider-shape normalization."""

    model_config = ConfigDict(extra="forbid")


IssueCategory = Literal[
    "anatomy_geometry",
    "bilateral_symmetry_gaze",
    "skin_texture",
    "boundary_blending",
    "lighting_color",
    "pose_perspective_scale",
    "synthesis_artifact",
]


class ReviewIssue(VLMContract):
    category: IssueCategory
    severity: Literal["low", "medium", "high"] = Field(
        description="Required visible impact level: low, medium, or high. Never omit this field."
    )
    # Forces a precise anatomical anchor (e.g. "右眼内眼角", "下颌与颈部交界左侧") instead of a
    # vague catch-all like "脸部" or "这里"; the min length alone can't guarantee specificity but
    # blocks the trivially empty/one-word answers that were the most common failure mode.
    region: str = Field(
        min_length=3,
        max_length=60,
        description=(
            "Precise Chinese anatomical anchor. Vague values such as 双眼, 面部, 脸部, or 皮肤 are invalid; "
            "use a description-specific anchor such as 双眼瞳孔与视线方向 or 右眼内眼角."
        ),
    )
    description: str = Field(
        min_length=12,
        max_length=200,
        description="Specific Chinese visual evidence including appearance, approximate extent, and comparison target.",
    )
    suggestion: str = Field(
        default="",
        max_length=150,
        description="Concrete Chinese correction operation naming the same anatomical anchor.",
    )


class FaceReviewReport(VLMContract):
    person_id: int = Field(ge=1)
    overall_score: float = Field(ge=0, le=100)
    anatomy_score: float = Field(ge=0, le=100)
    coherence_score: float = Field(ge=0, le=100)
    naturalness_score: float = Field(ge=0, le=100)
    lighting_score: float = Field(ge=0, le=100)
    person_locator: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=6)
    strengths: list[str] = Field(default_factory=list)
    recommended_prompt: str = ""
    should_refine: bool = True

    @field_validator("person_id", mode="before")
    @classmethod
    def normalize_person_id(cls, value: Any):
        """Accept only an integer or the provider's explicit 'PERSON #N' label."""
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.upper().startswith("PERSON #"):
                suffix = normalized.split("#", 1)[1].strip()
                if suffix.isdigit():
                    return int(suffix)
        return value

    @field_validator("strengths", mode="before")
    @classmethod
    def normalize_strengths(cls, value: Any):
        """Some OpenAI-compatible VLMs collapse a one-item string array."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @model_validator(mode="after")
    def enforce_conservative_decision(self):
        """Only a clean report with strong scores may recommend no refinement."""
        if (
            self.issues
            or min(
                self.anatomy_score,
                self.coherence_score,
                self.naturalness_score,
                self.lighting_score,
            )
            < 92
        ):
            self.should_refine = True
        return self


class BatchFaceReview(VLMContract):
    reports: list[FaceReviewReport] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_root_array(cls, value: Any):
        """Accept the common provider deviation of returning reports directly."""
        return {"reports": value} if isinstance(value, list) else value


class WholeImagePersonAssessment(VLMContract):
    """Advisory assessment of one requested person's visible correction."""

    person_id: int = Field(ge=1)
    status: Literal["resolved", "improved", "unchanged", "worsened", "unverifiable"]
    evidence: str = Field(min_length=6, max_length=300)


class WholeImageQualityReview(VLMContract):
    """One final full-canvas review; the human remains the acceptance authority."""

    overall_score: float = Field(ge=0, le=100)
    coherence_score: float = Field(ge=0, le=100)
    naturalness_score: float = Field(ge=0, le=100)
    people: list[WholeImagePersonAssessment] = Field(min_length=1)
    unrequested_changes_detected: bool
    new_high_severity_issue: bool
    recommendation: Literal["accept", "review", "regenerate"]
    summary: str = Field(min_length=6, max_length=500)
    review_error: str | None = None
    issues: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)

    @field_validator("issues", "strengths", mode="before")
    @classmethod
    def normalize_text_lists(cls, value: Any):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item for item in value]
        return value

    @model_validator(mode="after")
    def validate_people(self):
        person_ids = [item.person_id for item in self.people]
        if len(person_ids) != len(set(person_ids)):
            raise ValueError("whole-image review contains duplicate person_id values")
        unsafe_status = any(item.status in {"unchanged", "worsened", "unverifiable"} for item in self.people)
        cannot_accept = (
            unsafe_status
            or self.unrequested_changes_detected
            or self.new_high_severity_issue
            or bool(self.issues)
            or min(self.overall_score, self.coherence_score, self.naturalness_score) < 92
        )
        if cannot_accept:
            must_regenerate = self.new_high_severity_issue or any(item.status == "worsened" for item in self.people)
            if must_regenerate:
                self.recommendation = "regenerate"
            elif self.recommendation == "accept":
                self.recommendation = "review"
        return self


class IdentityArbitration(VLMContract):
    matched_person_index: int | None = Field(default=None, ge=1)
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    attributes_matched: list[str] = Field(default_factory=list)

    @field_validator("attributes_matched", mode="before")
    @classmethod
    def normalize_attributes(cls, value: Any):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value
