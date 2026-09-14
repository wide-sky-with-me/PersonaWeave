"""Identity arbitration contract; refinement evidence contracts live in core.refinement.contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdentityArbitration(BaseModel):
    """Advisory VLM identity correspondence; numerical face recognition remains authoritative."""

    model_config = ConfigDict(extra="forbid")

    matched_person_index: int | None = Field(default=None, ge=1)
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    attributes_matched: list[str] = Field(default_factory=list)

    @field_validator("attributes_matched", mode="before")
    @classmethod
    def normalize_attributes(cls, value: Any):
        """Normalize the documented list/string provider shape without guessing identity judgments."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value
