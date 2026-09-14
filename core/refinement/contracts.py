"""Evidence-grounded, score-free contracts for conversational whole-image refinement."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PEOPLE = 24
MAX_LOCATOR_CHARS = 300
MAX_MESSAGE_CHARS = 4000
MAX_EVIDENCE_PIXELS = 120_000_000

IssueCategory = Literal["identity", "anatomy", "occlusion", "blending", "lighting", "texture", "composition"]
Note = Annotated[str, Field(min_length=6, max_length=600)]


class Contract(BaseModel):
    """Reject extra tool/acceptance fields and whitespace-only evidence."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Issue(Contract):
    """A concrete visible defect, not a biometric identity verdict or aesthetic score."""

    category: IssueCategory
    person_id: int | None = Field(default=None, ge=1)
    region: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=12, max_length=400)
    evidence: str = Field(min_length=12, max_length=600)
    suggestion: str = Field(min_length=8, max_length=400)
    severity: Literal["low", "medium", "high"]
    confidence: Literal["low", "medium", "high"]
    uncertainty: str = Field(default="", max_length=400, description="Must be nonempty when confidence is low.")

    @model_validator(mode="after")
    def check_evidence(self):
        if self.category == "identity" and self.person_id is None:
            raise ValueError("Identity issues require a stable person_id")
        if self.confidence == "low" and not self.uncertainty:
            raise ValueError("Low confidence issues must explain uncertainty")
        return self


class PersonAssessment(Contract):
    """Keep target-person correspondence separate from visible identity similarity."""

    person_id: int = Field(ge=1)
    person_locator: str = Field(
        max_length=MAX_LOCATOR_CHARS,
        description="Concrete visible location; must be nonempty when mapping_confident is true.",
    )
    mapping_confident: bool
    identity_status: Literal["match", "mismatch", "uncertain", "unavailable"] = Field(
        description="Missing reference requires unavailable, even when person mapping is confident. Match/mismatch require mapping_confident=true."
    )
    evidence: Note
    uncertainty: str = Field(
        default="",
        max_length=600,
        description="Must be nonempty if mapping_confident=false OR identity_status is uncertain OR unavailable. Even confidently located people without references must explain unavailable identity evidence here.",
    )

    @model_validator(mode="after")
    def check_uncertainty(self):
        if not self.mapping_confident and self.identity_status in {"match", "mismatch"}:
            raise ValueError("Uncertain person mapping cannot support an identity comparison")
        if self.mapping_confident and not self.person_locator:
            raise ValueError("Confident mapping requires a visible person locator")
        if (
            not self.mapping_confident or self.identity_status in {"uncertain", "unavailable"}
        ) and not self.uncertainty:
            raise ValueError("Uncertain or unavailable assessments require an explanation")
        return self


class EditPlan(Contract):
    """Explicit requested changes and semantic preservation intent, never a pixel mask."""

    changes: list[Note] = Field(default_factory=list, max_length=32)
    preserve: list[Note] = Field(default_factory=list, max_length=32)
    instructions: str = Field(default="", max_length=4000)


class Diagnosis(Contract):
    """One diagnosis/discussion response; needs_edit is advice, not generation authorization."""

    assistant_message: str = Field(min_length=1, max_length=4000)
    needs_edit: bool = Field(
        description="True requires nonempty edit_plan.changes and instructions of at least six characters. False requires edit_plan.changes=[]. Discussion mode does not by itself mean false."
    )
    mapping_uncertain: bool = Field(
        description="Must equal whether ANY supplied person has mapping_confident=false. Missing identity references alone do not imply uncertain person mapping."
    )
    issues: list[Issue] = Field(default_factory=list, max_length=64)
    people: list[PersonAssessment] = Field(
        default_factory=list,
        max_length=MAX_PEOPLE,
        description="Exactly one assessment per supplied stable person_id. No missing, extra or duplicate IDs; issue person_ids must refer to these assessments.",
    )
    edit_plan: EditPlan
    uncertainties: list[Note] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_consistency(self):
        ids = [person.person_id for person in self.people]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate person assessments are not allowed")
        if self.mapping_uncertain != any(not person.mapping_confident for person in self.people):
            raise ValueError("mapping_uncertain must agree with person assessments")
        if any(issue.person_id is not None and issue.person_id not in ids for issue in self.issues):
            raise ValueError("Issues must refer to assessed stable person IDs")
        if self.needs_edit and (not self.edit_plan.changes or len(self.edit_plan.instructions) < 6):
            raise ValueError("An edit recommendation requires concrete changes and instructions")
        if not self.needs_edit and self.edit_plan.changes:
            raise ValueError("No-edit diagnosis cannot contain planned changes")
        return self


class CandidateReview(Contract):
    """Advisory evidence only: no score, acceptance field or automatic regeneration decision."""

    summary: Note
    improved: list[Note] = Field(default_factory=list, max_length=32)
    remaining: list[Issue] = Field(default_factory=list, max_length=64)
    new_issues: list[Issue] = Field(default_factory=list, max_length=64)
    uncertainties: list[Note] = Field(default_factory=list, max_length=32)
    people: list[PersonAssessment] = Field(
        default_factory=list,
        max_length=MAX_PEOPLE,
        description="Exactly one assessment per supplied stable person_id. No missing, extra or duplicate IDs; issue person_ids must refer to these assessments.",
    )

    @model_validator(mode="after")
    def check_person_ids(self):
        ids = [person.person_id for person in self.people]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate person assessments are not allowed")
        if any(
            issue.person_id is not None and issue.person_id not in ids for issue in self.remaining + self.new_issues
        ):
            raise ValueError("Review issues must refer to assessed stable person IDs")
        return self
