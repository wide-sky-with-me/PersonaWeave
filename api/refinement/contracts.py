"""HTTP commands and public projections for the refinement session boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.refinement.contracts import MAX_LOCATOR_CHARS, MAX_MESSAGE_CHARS, MAX_PEOPLE


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionPersonInput(Command):
    person_id: int = Field(ge=1)
    face_index: int = Field(ge=1)
    person_locator: str = Field(default="", max_length=MAX_LOCATOR_CHARS)
    source_identity_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    source_manual_images: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def one_reference_source(self):
        if self.source_identity_id and (self.source_asset_ids or self.source_manual_images):
            raise ValueError("Identity selection cannot be mixed with explicit references")
        if len(self.source_asset_ids) + len(self.source_manual_images) > 8:
            raise ValueError("At most eight explicit references are allowed per person")
        return self


class SessionCreateRequest(Command):
    request_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=160)
    original_image_url: str = Field(min_length=1)
    baseline_image_url: str = Field(min_length=1)
    people: list[SessionPersonInput] = Field(min_length=1, max_length=MAX_PEOPLE)

    @model_validator(mode="after")
    def unique_people(self):
        if len({person.person_id for person in self.people}) != len(self.people):
            raise ValueError("Person IDs must be unique")
        if len({person.face_index for person in self.people}) != len(self.people):
            raise ValueError("Face indices must be unique")
        return self


class TurnCreateRequest(Command):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    base_version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    mode: Literal["discuss", "generate", "auto"]
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    model_alias: str | None = Field(default=None, min_length=1, max_length=200)
    diagnosis_turn_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def confirmed_analysis_mode(self):
        """Only a generation command can bind a human-confirmed analysis."""
        if self.diagnosis_turn_id is not None and self.mode != "generate":
            raise ValueError("Confirmed diagnosis is only valid for generate")
        return self


class CancelRequest(Command):
    expected_revision: int = Field(ge=1)


class AcceptRequest(CancelRequest):
    version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class SessionPerson(BaseModel):
    person_id: int
    face_index: int
    person_locator: str
    reference_urls: list[str]
    reference_status: Literal["available", "missing"]


class ImageVersion(BaseModel):
    id: str
    parent_version_id: str | None
    digest: str
    image_url: str
    kind: Literal["baseline", "candidate"]
    created_at: str
    model: str | None = None
    task_id: str | None = None


class SessionTurn(BaseModel):
    id: str
    request_id: str
    base_version_id: str
    mode: Literal["discuss", "generate", "auto"]
    message: str
    status: Literal[
        "queued",
        "analyzing",
        "generating",
        "reviewing",
        "review_required",
        "completed",
        "failed",
        "cancel_requested",
        "cancelled",
    ]
    diagnosis_turn_id: str | None = None
    diagnosis: dict | None = None
    plan: dict | None = None
    assistant_message: str | None = None
    candidate_version_id: str | None = None
    generation_metadata: dict | None = None
    review: dict | None = None
    error: str | None = None
    review_error: str | None = None
    created_at: str
    updated_at: str


class SessionDetail(BaseModel):
    id: str
    target_id: str
    title: str
    revision: int
    status: Literal["idle", "running"]
    original_url: str
    baseline_version_id: str
    accepted_version_id: str | None
    accepted_image_url: str | None = None
    active_turn_id: str | None
    people: list[SessionPerson]
    versions: list[ImageVersion]
    turns: list[SessionTurn]
    created_at: str
    updated_at: str
    event_cursor: int = 0


class SessionSummary(BaseModel):
    id: str
    target_id: str
    title: str
    revision: int
    status: Literal["idle", "running"]
    accepted_version_id: str | None
    active_turn_id: str | None
    version_count: int
    turn_count: int
    created_at: str
    updated_at: str


class SessionEnvelope(BaseModel):
    code: int = 200
    data: SessionDetail


class SummaryEnvelope(BaseModel):
    code: int = 200
    data: list[SessionSummary]


class SessionNotFound(LookupError):
    """A requested session or session-owned resource does not exist."""


class SessionConflict(ValueError):
    """A command targets stale state, a busy session or a conflicting idempotency key."""


class ArtifactUnavailable(ValueError):
    """Referenced immutable bytes are missing, corrupt or outside the managed root."""


class ModelUnavailable(RuntimeError):
    """Only model actions depend on optional AI service configuration."""
