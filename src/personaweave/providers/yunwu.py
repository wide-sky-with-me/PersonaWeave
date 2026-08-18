import json
from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlparse

import httpx
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from personaweave.domain import (
    AttributePermission,
    HEAD_ATTRIBUTES,
    PersonAppearance,
    PersonDetectionAudit,
    ReferenceSufficiency,
    SourceOccurrenceEvidence,
    SourceOccurrenceReferenceAssessment,
    SourceOperationReferenceCoverage,
    SemanticMaskProposal,
    SwapRequest,
    TargetIdentityProfile,
    ValidationRequest,
    ValidationResult,
)
from personaweave.providers.gateway import ProviderNotConfigured


def _normalize_exact_bool(value: object) -> object:
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("boolean value must be true or false")


ExactBool: TypeAlias = Annotated[StrictBool, BeforeValidator(_normalize_exact_bool)]


class _VisualChecks(BaseModel):
    requested_edits_complete: ExactBool
    unrequested_content_preserved: ExactBool
    target_identity_matches_references: ExactBool
    cross_image_identity_consistent: ExactBool
    face_quality_acceptable: ExactBool
    review_confidence: Annotated[float, Field(ge=0, le=1)]


class _VisualValidationPayload(BaseModel):
    passed: ExactBool
    checks: _VisualChecks
    reasons: list[str] = Field(default_factory=list)


class ImageEditPlan(BaseModel):
    edit_prompt: str = Field(min_length=20, max_length=8000)
    observed_people: int = Field(ge=0, le=64)
    intended_face_edits: int = Field(ge=0, le=64)
    warnings: list[str] = Field(default_factory=list)


class ImageEditScores(BaseModel):
    instruction_fulfillment: int = Field(ge=0, le=100)
    target_identity_consistency: int = Field(ge=0, le=100)
    face_naturalness: int = Field(ge=0, le=100)
    pose_expression_preservation: int = Field(ge=0, le=100)
    source_content_preservation: int = Field(ge=0, le=100)
    text_logo_preservation: int = Field(ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def normalize_common_model_scales(cls, value: object) -> object:
        """Normalize complete 0-1 or 0-10 scorecards to the 0-100 contract."""
        if not isinstance(value, dict):
            return value
        field_names = tuple(cls.model_fields)
        raw_values = [value.get(name) for name in field_names]
        if not all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in raw_values
        ):
            return value
        numeric = [float(item) for item in raw_values]
        if not all(item >= 0 for item in numeric):
            return value
        maximum = max(numeric)
        scale = 100 if maximum <= 1 else 10 if maximum <= 10 else 1
        return {
            **value,
            **{
                name: round(float(value[name]) * scale)
                for name in field_names
            },
        }


class ImageEditIssue(BaseModel):
    category: Literal[
        "instruction",
        "identity",
        "face_quality",
        "pose_expression",
        "source_content",
        "text_logo",
    ]
    severity: Literal["critical", "major", "minor"]
    evidence: str = Field(min_length=1, max_length=500)
    correction: str = Field(min_length=1, max_length=500)


class ImageEditReview(BaseModel):
    passed: ExactBool
    scores: ImageEditScores
    issues: list[ImageEditIssue] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=1000)
    revised_edit_prompt: str | None = Field(default=None, min_length=20, max_length=8000)

    @field_validator("issues", mode="before")
    @classmethod
    def normalize_issue_items(cls, value: object) -> object:
        """Accept terse string issues from otherwise valid JSON-mode model responses."""
        if not isinstance(value, list):
            return value
        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append({
                    "category": "instruction",
                    "severity": "major",
                    "evidence": item[:500],
                    "correction": "Correct this visible problem while retaining every immutable task and source-preservation constraint.",
                })
            elif isinstance(item, dict):
                enriched = dict(item)
                enriched.setdefault("category", "instruction")
                enriched.setdefault("severity", "major")
                enriched.setdefault("evidence", "The reviewer reported an unspecified visible problem.")
                enriched.setdefault(
                    "correction",
                    "Correct the reported problem while retaining every immutable constraint.",
                )
                normalized.append(enriched)
            else:
                normalized.append(item)
        return normalized


class PersonCluster(BaseModel):
    members: list[str] = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0, le=1)]


class PersonClustering(BaseModel):
    groups: list[PersonCluster]


class PersonAppearanceObservation(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    appearance: PersonAppearance


class PersonAppearanceAnalysis(BaseModel):
    people: list[PersonAppearanceObservation]


class _CompiledTargetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: dict[str, object]
    reference_assessment: dict[str, object]


class _CompiledPermissionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    permissions: AttributePermission


class _CompiledPermissionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[_CompiledPermissionItem] = Field(max_length=128)


class _CompiledSourceCoveragePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurrences: list[SourceOccurrenceReferenceAssessment] = Field(
        min_length=1, max_length=32
    )
    summary: str = Field(default="", max_length=1000)

IMAGE_EDIT_ACCEPTANCE_THRESHOLDS = {
    "instruction_fulfillment": 85,
    "target_identity_consistency": 85,
    "face_naturalness": 80,
    "pose_expression_preservation": 85,
    "source_content_preservation": 90,
    "text_logo_preservation": 95,
}


def image_edit_review_passes(review: ImageEditReview) -> bool:
    scores = review.scores.model_dump()
    thresholds_met = all(
        scores[name] >= threshold
        for name, threshold in IMAGE_EDIT_ACCEPTANCE_THRESHOLDS.items()
    )
    has_critical_issue = any(issue.severity == "critical" for issue in review.issues)
    return bool(review.passed and thresholds_met and not has_critical_issue)


def enforce_image_edit_review(review: ImageEditReview) -> ImageEditReview:
    passed = image_edit_review_passes(review)
    summary = review.summary
    if not passed:
        summary = f"固定阈值结论：未通过。模型原始摘要：{summary}"
    return review.model_copy(update={"passed": passed, "summary": summary[:1000]})


def _parse_json_object(raw: object) -> dict:
    if not isinstance(raw, str):
        raise ValueError("Yunwu response content must be a JSON string")
    content = raw.strip()
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline == -1 or not content.endswith("```"):
            raise ValueError("Yunwu returned an incomplete fenced JSON response")
        content = content[first_newline + 1 : -3].strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Yunwu response must be a JSON object")
    return parsed


def _configured_endpoint(base_url: str, api_path: str) -> str:
    if api_path.startswith(("http://", "https://")):
        parsed = urlparse(api_path)
        if not parsed.netloc:
            raise ValueError("Yunwu API path must be an absolute HTTP(S) URL")
        return api_path.rstrip("/")
    normalized = f"/{api_path.lstrip('/')}"
    return f"{base_url.rstrip('/')}{normalized}"


class YunwuMultimodalProvider:
    """OpenAI-compatible visual QA adapter.

    This adapter is deliberately excluded from biometric identity assignment.
    It reports visual anomalies but cannot confirm that two faces are the same
    person.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str | None,
        timeout: float = 120.0,
        *,
        api_mode: str = "chat_completions",
        api_path: str = "/chat/completions",
        client: httpx.AsyncClient | None = None,
    ):
        if api_mode != "chat_completions":
            raise ValueError(f"unsupported Yunwu vision API mode: {api_mode}")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.api_mode = api_mode
        self.api_path = api_path
        self.endpoint = _configured_endpoint(self.base_url, api_path)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self.timeout)
        self._closed = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def list_model_records(self) -> list[dict[str, object]]:
        if not self.api_key:
            raise ProviderNotConfigured("Yunwu API key is required")
        response = await self._client.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        body = response.json()
        models = body.get("data", [])
        return sorted(
            (
                {
                    "id": model_id,
                    "owned_by": item.get("owned_by"),
                    "supported_endpoint_types": [
                        endpoint
                        for endpoint in item.get("supported_endpoint_types", [])
                        if isinstance(endpoint, str)
                    ],
                }
                for item in models
                if isinstance(item, dict) and isinstance((model_id := item.get("id")), str)
            ),
            key=lambda item: str(item["id"]),
        )

    async def list_models(self) -> list[str]:
        return [str(item["id"]) for item in await self.list_model_records()]

    async def _json_completion(self, content: list[dict[str, object]]) -> dict:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        response = await self._client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        return _parse_json_object(body["choices"][0]["message"]["content"])

    async def compile_target_identity(
        self,
        *,
        identity_id: str,
        identity_code: str,
        references: list[dict[str, str]],
    ) -> tuple[TargetIdentityProfile, ReferenceSufficiency]:
        """Compile bounded visible evidence; never infer or verify a real-world identity."""
        if len(references) > 32:
            raise ValueError("at most 32 references can be compiled for one identity")
        if not references:
            return (
                TargetIdentityProfile(
                    identity_id=identity_id,
                    identity_code=identity_code,
                ),
                ReferenceSufficiency(
                    identity_id=identity_id,
                    identity_code=identity_code,
                    reference_count=0,
                    sufficient=False,
                    requires_manual_review=True,
                    summary="No reference image is available.",
                ),
            )
        schema_hint = {
            "profile": {
                "apparent_age": "bounded visible age impression or unclear",
                "face_features": "visible facial geometry only",
                "hair": "visible hair or unclear",
                "eyewear": "visible eyewear or none/unclear",
                "accessories": "visible head or face accessories or none/unclear",
                "makeup": "visible makeup or none/unclear",
                "stable_identity_anchors": ["stable visible geometric anchor"],
                "variable_traits": ["reference-dependent styling or expression"],
                "unknown_evidence": ["unsupported or obscured visible detail"],
                "unsupported_evidence": ["claim not supported by references"],
            },
            "reference_assessment": {
                "visible_views": ["frontal"],
                "missing_views": ["left profile"],
                "confidence": 0.0,
                "sufficient": False,
                "requires_manual_review": True,
                "summary": "short evidence-only assessment",
            },
        }
        content: list[dict[str, object]] = [{
            "type": "text",
            "text": (
                "Compile visible target-reference evidence for a bounded face edit. Do not name, "
                "identify, compare against external people, or infer sensitive traits. Describe only "
                "what is visibly supported across the supplied references. Assess whether the views "
                "are sufficient to guide identity appearance across ordinary pose/angle variation. "
                "Insufficient or contradictory evidence is allowed but must set sufficient=false and "
                "requires_manual_review=true. Use 'unclear' rather than guessing. Return JSON only in "
                f"this exact shape: {json.dumps(schema_hint, ensure_ascii=False)}"
            ),
        }]
        for reference in references:
            content.extend([
                {"type": "text", "text": f"REFERENCE {reference['code']}"},
                {
                    "type": "image_url",
                    "image_url": {"url": reference["uri"], "detail": "high"},
                },
            ])
        compiled = _CompiledTargetPayload.model_validate(
            await self._json_completion(content)
        )
        profile = TargetIdentityProfile.model_validate({
            "identity_id": identity_id,
            "identity_code": identity_code,
            **compiled.profile,
        })
        assessment = ReferenceSufficiency.model_validate({
            "identity_id": identity_id,
            "identity_code": identity_code,
            "reference_count": len(references),
            **compiled.reference_assessment,
        })
        return profile, assessment

    async def compile_source_reference_coverage(
        self,
        *,
        source_asset_id: str,
        source_asset_code: str,
        source_uri: str,
        person_id: str,
        person_code: str,
        occurrences: list[SourceOccurrenceEvidence],
        target_profile: TargetIdentityProfile,
        reference_assessment: ReferenceSufficiency,
        references: list[dict[str, str]],
    ) -> SourceOperationReferenceCoverage:
        """Assess only this source person against the selected target's references."""
        if not occurrences:
            raise ValueError("source reference coverage requires at least one occurrence")
        if len(occurrences) > 32 or len(references) > 32:
            raise ValueError("source reference coverage exceeds the bounded evidence limit")

        def fail_closed(reason: str) -> SourceOperationReferenceCoverage:
            return SourceOperationReferenceCoverage(
                source_asset_id=source_asset_id,
                source_asset_code=source_asset_code,
                person_id=person_id,
                person_code=person_code,
                occurrences=occurrences,
                occurrence_assessments=[
                    SourceOccurrenceReferenceAssessment(
                        face_instance_id=occurrence.face_instance_id,
                        missing_coverage=[reason],
                        sufficient=False,
                        requires_manual_review=True,
                        summary=(
                            "The selected target references do not provide certain "
                            "coverage for this source occurrence."
                        ),
                    )
                    for occurrence in occurrences
                ],
                sufficient=False,
                summary=reason,
            )

        if not references:
            return fail_closed("No selected target reference image is available.")
        if not reference_assessment.sufficient:
            return fail_closed(
                "The selected target identity profile has missing or uncertain view coverage."
            )

        schema_hint = {
            "occurrences": [
                {
                    "face_instance_id": occurrence.face_instance_id,
                    "reference_views_used": ["R01 frontal"],
                    "missing_coverage": ["matching left profile"],
                    "sufficient": False,
                    "requires_manual_review": True,
                    "summary": "bounded comparison of this occurrence to selected references",
                }
                for occurrence in occurrences
            ],
            "summary": "bounded source-person coverage conclusion",
        }
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    "Assess reference sufficiency separately for every listed occurrence "
                    "of one source person. Compare only the supplied source image/bboxes "
                    "against the supplied selected-target profile and reference images. "
                    "Consider bbox face size, pose/view, lighting, occlusion, and blur. "
                    "Do not identify anyone, compare unrelated identities, or infer "
                    "sensitive traits. Set sufficient=true only when the selected target "
                    "references visibly cover that occurrence's requirements with "
                    "certainty. Missing, contradictory, unclear, small, occluded, or "
                    "blurred evidence must set sufficient=false and "
                    "requires_manual_review=true. Return every face_instance_id exactly "
                    "once and no other IDs. Keep all evidence short and visible-only. "
                    f"TARGET PROFILE: {target_profile.model_dump_json()}\n"
                    "TARGET REFERENCE ASSESSMENT: "
                    f"{reference_assessment.model_dump_json()}\n"
                    "SOURCE OCCURRENCES: "
                    f"{json.dumps([item.model_dump(mode='json') for item in occurrences], ensure_ascii=False)}\n"
                    f"RETURN SHAPE: {json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
            {"type": "text", "text": f"SOURCE IMAGE {source_asset_code}"},
            {
                "type": "image_url",
                "image_url": {"url": source_uri, "detail": "high"},
            },
        ]
        for reference in references:
            content.extend([
                {
                    "type": "text",
                    "text": f"SELECTED TARGET REFERENCE {reference['code']}",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": reference["uri"], "detail": "high"},
                },
            ])
        compiled = _CompiledSourceCoveragePayload.model_validate(
            await self._json_completion(content)
        )
        expected_ids = [item.face_instance_id for item in occurrences]
        returned_ids = [item.face_instance_id for item in compiled.occurrences]
        if (
            len(returned_ids) != len(set(returned_ids))
            or set(returned_ids) != set(expected_ids)
        ):
            raise ValueError(
                "reference coverage compiler must assess every occurrence exactly once"
            )
        sufficient = all(item.sufficient for item in compiled.occurrences)
        return SourceOperationReferenceCoverage(
            source_asset_id=source_asset_id,
            source_asset_code=source_asset_code,
            person_id=person_id,
            person_code=person_code,
            occurrences=occurrences,
            occurrence_assessments=compiled.occurrences,
            sufficient=sufficient,
            summary=compiled.summary,
        )

    async def compile_attribute_permissions(
        self,
        *,
        task_instruction: str,
        operations: list[dict[str, str | None]],
    ) -> dict[str, AttributePermission]:
        """Compile a complete fail-closed permission partition for each person operation."""
        if not operations:
            return {}
        attributes = sorted(HEAD_ATTRIBUTES)
        schema_hint = {
            "operations": [{
                "person_code": str(operations[0]["person_code"]),
                "permissions": {
                    "editable": ["face_identity", "face_features"],
                    "protected": [
                        attribute
                        for attribute in attributes
                        if attribute not in {"face_identity", "face_features"}
                    ],
                },
            }]
        }
        compiled = _CompiledPermissionsPayload.model_validate(
            await self._json_completion([{
                "type": "text",
                "text": (
                    "Compile immutable per-person head-attribute permissions from the user's exact "
                    "task and supplied mapping topology. Supported attributes are exactly: "
                    f"{json.dumps(attributes)}. Every attribute must appear exactly once in either "
                    "editable or protected for every operation. Unknown attributes are forbidden. "
                    "Changing face identity necessarily authorizes both face_identity and "
                    "face_features because visible facial geometry carries identity; this does not "
                    "authorize hair, pose, expression, gaze, body, or scene changes. Every other "
                    "head attribute remains protected unless the immutable task explicitly requests "
                    "changing it for that person. Keep operations must protect every attribute. "
                    "Never broaden ambiguous wording; fail closed by protecting the attribute. "
                    "Return every person exactly once as JSON only.\n"
                    f"TASK: {json.dumps(task_instruction, ensure_ascii=False)}\n"
                    f"OPERATIONS: {json.dumps(operations, ensure_ascii=False)}\n"
                    f"SHAPE: {json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            }])
        )
        expected_codes = [str(operation["person_code"]) for operation in operations]
        returned_codes = [item.person_code for item in compiled.operations]
        if (
            len(returned_codes) != len(set(returned_codes))
            or set(returned_codes) != set(expected_codes)
        ):
            raise ValueError("permission compiler must cover every person exactly once")
        action_by_code = {
            str(operation["person_code"]): operation["action"] for operation in operations
        }
        result = {item.person_code: item.permissions for item in compiled.operations}
        for code, permissions in list(result.items()):
            if (
                "face_identity" in permissions.editable
                and "face_features" not in permissions.editable
            ):
                result[code] = permissions.model_copy(
                    update={
                        "editable": [*permissions.editable, "face_features"],
                        "protected": [
                            attribute
                            for attribute in permissions.protected
                            if attribute != "face_features"
                        ],
                    }
                )
        for code, permissions in result.items():
            action = action_by_code[code]
            if action == "keep" and permissions.editable:
                raise ValueError("keep operations cannot authorize editable attributes")
            if action == "replace" and "face_identity" not in permissions.editable:
                raise ValueError("replace permissions must authorize face_identity")
        return result

    async def compile_semantic_mask_proposal(
        self,
        *,
        source_asset_id: str,
        source_asset_code: str,
        source_uri: str,
        faces: list[dict[str, object]],
        protected_people: list[dict[str, str]],
        identity_evidence: list[dict[str, object]],
    ) -> SemanticMaskProposal:
        """Propose geometry only; local code remains the authorization authority."""
        if len(faces) > 128 or len(identity_evidence) > 128:
            raise ValueError("semantic mask proposal context exceeds bounded limits")
        schema_hint = {
            "source_asset_id": source_asset_id,
            "source_asset_code": source_asset_code,
            "contours": [{
                "operation": "include",
                "face_instance_id": "exact supplied face_instance_id",
                "person_id": "exact supplied person_id",
                "person_code": "exact supplied person_code",
                "permissions": {
                    "editable": ["exact supplied editable attributes"],
                    "protected": ["exact supplied protected attributes"],
                },
                "points": [{"x": 0.0, "y": 0.0}],
                "confidence": 0.0,
                "evidence": "short visible-only reason",
            }],
            "confidence": 0.0,
            "summary": "short geometry assessment",
        }
        prompt = (
            "Propose the authorization mask for this exact source image. Return arbitrary normalized "
            "polygon contours, not ellipses or bounding boxes. Coordinates must be finite and inside "
            "[0,1]. Use include contours for only the visible head pixels needed by each editable "
            "confirmed face. Use exclude contours as holes for protected attributes, occluders, or "
            "nearby people inside an include. Every contour must repeat one supplied editable "
            "face_instance_id, person_id, person_code, and its exact complete permission partition. "
            "Never add a face/person, change a mapping, authorize a protected person, authorize a "
            "protected attribute, bridge neighboring heads, or expand ambiguous scope. Include every "
            "editable face center and meaningful face area while keeping body/background/protected "
            "pixels excluded. Low visual certainty is allowed: lower confidence rather than widening. "
            "Return JSON only, at most 64 contours and 64 points per contour. Empty editable scope "
            "requires an empty contours list.\n"
            "The bbox field uses exact source pixels; normalized_bbox is the same exact geometry in "
            "[0,1] contour coordinates and source_dimensions defines that conversion. "
            f"EXACT FACE BBOXES/CODES/AUTHORIZATION: {json.dumps(faces, ensure_ascii=False)}\n"
            f"PROTECTED PEOPLE: {json.dumps(protected_people, ensure_ascii=False)}\n"
            "IDENTITY PROFILE/REFERENCE COVERAGE: "
            f"{json.dumps(identity_evidence, ensure_ascii=False)}\n"
            f"RETURN SHAPE: {json.dumps(schema_hint, ensure_ascii=False)}"
        )
        compiled = SemanticMaskProposal.model_validate(
            await self._json_completion([
                {"type": "text", "text": prompt},
                {"type": "text", "text": f"SOURCE IMAGE {source_asset_code}"},
                {
                    "type": "image_url",
                    "image_url": {"url": source_uri, "detail": "high"},
                },
            ])
        )
        if (
            compiled.source_asset_id != source_asset_id
            or compiled.source_asset_code != source_asset_code
        ):
            raise ValueError("semantic mask compiler returned mismatched source identity")
        return compiled

    async def audit_person_detection(
        self,
        *,
        source_uri: str,
        annotated_uri: str,
        detections: list[dict[str, object]],
    ) -> PersonDetectionAudit:
        """Check detection completeness without treating VLM boxes as authoritative faces."""
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        schema_hint = {
            "available": True,
            "complete": False,
            "detected_face_count": len(detections),
            "visible_face_count": len(detections),
            "missing_regions": [{
                "bbox": {"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1},
                "confidence": 0.0,
                "evidence": "short visual evidence",
            }],
            "requires_manual_review": True,
            "summary": "short conclusion",
        }
        instruction = (
            "Audit whether every visible human face in the ORIGINAL image has a corresponding green "
            "detection rectangle in the ANNOTATED image. Include frontal, profile, small, partially "
            "occluded, sunglass-covered and hat-covered faces that are still visibly human. Exclude "
            "printed photos, screens, statues, reflections and body parts. Do not identify or name "
            "any person. The supplied detections use normalized source-image coordinates: "
            f"{json.dumps(detections, ensure_ascii=False)}. "
            "For each genuinely missing face, return one conservative normalized bbox in [0,1] "
            "covering the visible face, plus confidence and evidence. Do not repeat an already boxed "
            "face. Set complete=true only when no visible face is missing. Set requires_manual_review "
            "when any face is missing or the image is too ambiguous to verify. Return JSON only with "
            f"this exact shape: {json.dumps(schema_hint, ensure_ascii=False)}"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "text", "text": "ORIGINAL IMAGE"},
                    {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
                    {"type": "text", "text": "ANNOTATED IMAGE WITH CURRENT GREEN BOXES"},
                    {"type": "image_url", "image_url": {"url": annotated_uri, "detail": "high"}},
                ],
            }],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        audit = PersonDetectionAudit.model_validate(
            _parse_json_object(body["choices"][0]["message"]["content"])
        )
        missing = [region for region in audit.missing_regions if region.confidence >= 0.6]
        count_consistent = (
            audit.visible_face_count is None
            or audit.visible_face_count <= len(detections)
        )
        complete = bool(audit.complete and not missing and count_consistent)
        return audit.model_copy(update={
            "detected_face_count": len(detections),
            "complete": complete,
            "missing_regions": missing,
            "requires_manual_review": bool(audit.requires_manual_review or not complete),
        })

    async def cluster_person_observations(
        self,
        observations: list[dict[str, str]],
    ) -> dict[str, tuple[str, float]]:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        if not observations:
            return {}
        codes = [observation["code"] for observation in observations]
        schema_hint = {
            "groups": [
                {"members": [codes[0]], "confidence": 0.0}
            ]
        }
        content = [{
            "type": "text",
            "text": (
                "Fuse duplicate cropped face observations into source-person groups across the "
                "complete uploaded image set. One group represents one real source person, and may "
                "contain appearances from multiple photos. Each following image is preceded by its "
                "observation code, whose Ixx prefix identifies the source image. "
                "Every supplied code must appear exactly once in groups. Put uncertain observations "
                "in separate one-member groups; never guess, name, or identify a real person. "
                "confidence measures only visual same-person grouping confidence. Different pose, "
                "expression, angle, distance, partial occlusion and lighting may still be the same "
                "person. Normally two different faces in the same source image are different people; "
                "do not merge lookalikes merely because of age, gender or styling. Return JSON only: "
                f"{json.dumps(schema_hint, ensure_ascii=False)}"
            ),
        }]
        for observation in observations:
            content.extend([
                {"type": "text", "text": f"OBSERVATION {observation['code']}"},
                {"type": "image_url", "image_url": {"url": observation["crop_uri"], "detail": "high"}},
            ])
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        clustering = PersonClustering.model_validate(
            _parse_json_object(body["choices"][0]["message"]["content"])
        )
        returned = [member for group in clustering.groups for member in group.members]
        if len(returned) != len(set(returned)) or set(returned) != set(codes):
            raise ValueError("multimodal person clustering did not cover every observation exactly once")
        assignments = {}
        for group in clustering.groups:
            group_key = "vlm:" + "|".join(sorted(group.members))
            for member in group.members:
                assignments[member] = (group_key, group.confidence)
        return assignments

    async def analyze_person_appearances(
        self,
        *,
        source_uri: str,
        observations: list[dict[str, object]],
    ) -> dict[str, PersonAppearance]:
        """Describe visible source traits without identifying or naming a person."""
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        if not observations:
            return {}
        codes = [str(observation["code"]) for observation in observations]
        schema_hint = {
            "people": [{
                "code": codes[0],
                "appearance": {
                    "apparent_age": "broad visible age impression, never an exact age",
                    "face_features": "visible non-identity facial geometry and expression context",
                    "hair": "visible hairstyle and hairline",
                    "eyewear": "visible eyewear or none",
                    "accessories": "visible face/head/neck accessories or none",
                    "body": "visible build and proportions relevant to facial harmony",
                    "clothing": "visible neckline and clothing style/color",
                    "pose": "head angle, gaze, expression and body pose",
                    "lighting_occlusion": "lighting, blur, depth and occlusions around the face",
                },
            }]
        }
        instructions = (
            "ROLE: Source-person appearance analyst for a later, tightly scoped facial edit. "
            "Do not identify, name, compare, or infer the real identity, ethnicity, health, or other "
            "sensitive traits of any person.\n"
            "The supplied image is the immutable source. Each observation code has an original-pixel "
            "face bbox. Return one entry for every code exactly once. Describe only visibly supported "
            "traits that a later image editor needs to keep the new face coherent with the existing "
            "body and scene: broad apparent age impression, facial geometry/expression context, hair, "
            "eyewear, accessories, build/proportions, clothing/neckline, pose, lighting, blur and "
            "occlusion. Use 'unclear' when evidence is insufficient. Do not propose edits and do not "
            "change clothing, body, hair, accessories or pose.\n"
            f"OBSERVATIONS: {json.dumps(observations, ensure_ascii=False)}\n"
            f"Return JSON only using this shape: {json.dumps(schema_hint, ensure_ascii=False)}"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": instructions},
                {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
            ]}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        analysis = PersonAppearanceAnalysis.model_validate(
            _parse_json_object(body["choices"][0]["message"]["content"])
        )
        returned = [person.code for person in analysis.people]
        if len(returned) != len(set(returned)) or set(returned) != set(codes):
            raise ValueError("appearance analysis did not cover every observation exactly once")
        return {person.code: person.appearance for person in analysis.people}

    async def plan_numbered_image_edit(
        self,
        *,
        request: SwapRequest,
        source_uri: str,
    ) -> ImageEditPlan:
        """Create the first generator prompt from the immutable numbered task and source traits."""
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        operations = [{
            "source_person": operation.source_person_code,
            "target_identity": operation.target_identity_code,
            "bbox": operation.bbox.model_dump(),
            "source_appearance": operation.source_appearance.model_dump(),
            "additional_requirement": operation.instruction,
            "has_reference": bool(operation.reference_uris),
        } for operation in request.replacements]
        schema_hint = {
            "edit_prompt": "complete standalone generator-ready instruction",
            "observed_people": 0,
            "intended_face_edits": len(operations),
            "warnings": [],
        }
        instructions = (
            "ROLE: Initial multimodal planner for a bounded numbered people-edit loop. You are not a "
            "biometric verifier.\n"
            f"Immutable user task: {json.dumps(request.task_instruction, ensure_ascii=False)}\n"
            f"Immutable compiled scope: {json.dumps(request.compiled_instruction, ensure_ascii=False)}\n"
            f"Numbered operations and source appearance context: {json.dumps(operations, ensure_ascii=False)}\n"
            "Inspect the immutable original source and produce one complete standalone prompt for the "
            "image-edit model. Keep every numbered mapping and bbox unchanged. Adapt each requested new "
            "facial identity or facial trait to the source person's broad visible age impression, face-to-body "
            "proportions, neckline, pose, expression, gaze, lighting, blur, depth and occlusion so the whole "
            "person remains visually coherent. Preserve the source hair, eyewear, accessories, body, hands, "
            "clothing and pose unless the immutable user task explicitly requests changing one of them. "
            "Never invent an exact age or alter an unrequested person. Require the exact original dimensions, "
            "crop, composition, background, objects, text and logos. Every generation round edits the original, "
            "never a previous candidate. If text and an observed trait conflict, the explicit immutable user "
            "task wins only for the requested trait; all remaining source traits stay protected. Record ambiguity "
            "in warnings instead of guessing. Do not name or identify anyone beyond literal target wording already "
            "present in the immutable user task. Return JSON only: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )
        content = [
            {"type": "text", "text": instructions},
            {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
        ]
        seen_references: set[str] = set()
        for operation in request.replacements:
            for reference_uri in operation.reference_uris:
                if reference_uri in seen_references:
                    continue
                seen_references.add(reference_uri)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": reference_uri, "detail": "high"},
                })
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        return ImageEditPlan.model_validate(
            _parse_json_object(body["choices"][0]["message"]["content"])
        )

    async def plan_image_edit(
        self,
        *,
        source_uri: str,
        description: str,
        reference_uri: str | None = None,
    ) -> ImageEditPlan:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        schema_hint = {
            "edit_prompt": "complete image-edit instruction",
            "observed_people": 0,
            "intended_face_edits": 0,
            "warnings": [],
        }
        instructions = (
            "ROLE: You are the initial prompt planner in a bounded image-edit loop. You are not a "
            "biometric verifier.\n\n"
            "INPUT CONTRACT:\n"
            "- Image 1 is the immutable original source and the only editing canvas.\n"
            f"- Immutable user requirement: {json.dumps(description, ensure_ascii=False)}\n"
            "- Your output is consumed by an image-edit model, not by a human operator.\n\n"
            "TASK:\n"
            "1. Inspect the original and translate the immutable requirement without narrowing, "
            "expanding, or reinterpreting its scope.\n"
            "2. If the user says all/every visible person, cover every visible real-human face. If "
            "the user specifies subjects, edit only those subjects.\n"
            "3. Produce one complete, standalone, generator-ready edit_prompt. It must repeat the "
            "requested scope and target identity or appearance, require consistent completion of "
            "every requested face, and forbid changes to unrequested faces or content.\n\n"
            "PRESERVATION CONTRACT FOR edit_prompt:\n"
            "- Change facial identity/appearance only as requested.\n"
            "- Preserve each source person's distinct head angle, expression, gaze, mouth state, "
            "eyewear, hairstyle, occlusion, body, hands, clothing, and pose.\n"
            "- Preserve exact image dimensions, aspect ratio, crop, composition, geometry, "
            "background, objects, text, logos, lighting, color, sharpness, noise, and depth of field.\n"
            "- Do not add, remove, move, merge, beautify, or redraw unrelated subjects or objects.\n"
            "- Do not redesign the scene, reframe the image, or use a newly generated composition.\n"
            "Record concrete visual ambiguity in warnings; do not silently guess."
        )
        if reference_uri:
            instructions += (
                " The second image is the target-identity reference and must be used only for facial "
                "identity, never as a replacement composition."
            )
        instructions += (
            "\nDo not name or identify any real person. Count visible people and intended face "
            "edits. Return JSON only using this shape: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )
        content = [
            {"type": "text", "text": instructions},
            {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
        ]
        if reference_uri:
            content.append(
                {"type": "image_url", "image_url": {"url": reference_uri, "detail": "high"}}
            )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        raw = body["choices"][0]["message"]["content"]
        return ImageEditPlan.model_validate(_parse_json_object(raw))

    async def review_image_edit(
        self,
        *,
        source_uri: str,
        result_uri: str,
        description: str,
        current_edit_prompt: str,
        iteration: int,
        max_iterations: int,
        reference_uri: str | None = None,
    ) -> ImageEditReview:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        schema_hint = {
            "passed": False,
            "scores": {
                "instruction_fulfillment": 0,
                "target_identity_consistency": 0,
                "face_naturalness": 0,
                "pose_expression_preservation": 0,
                "source_content_preservation": 0,
                "text_logo_preservation": 0,
            },
            "issues": [
                {
                    "category": "source_content",
                    "severity": "major",
                    "evidence": "specific visible difference",
                    "correction": "specific instruction for the next generation",
                }
            ],
            "summary": "short Chinese verdict",
            "revised_edit_prompt": "complete standalone next-round prompt, or null when passed",
        }
        instructions = (
            "ROLE: You are the strict visual reviewer and next-prompt author in a bounded image-edit "
            "loop. You are not a biometric verifier.\n\n"
            "INPUT CONTRACT:\n"
            "- Image 1 is the immutable original source.\n"
            "- The last image is the current generated candidate.\n"
            + (
                "- Image 2 is the target-identity reference and is evidence only for facial identity.\n"
                if reference_uri
                else "- There is no target reference image; judge the text-defined appearance.\n"
            )
            + f"- Immutable user requirement: {json.dumps(description, ensure_ascii=False)}\n"
            f"- Prompt used for this candidate: {json.dumps(current_edit_prompt, ensure_ascii=False)}\n"
            f"- Iteration: {iteration} of {max_iterations}.\n\n"
            "REVIEW TASK:\n"
            "1. Compare candidate against the original at high detail. Requested facial identity "
            "changes are intentional and are not source damage. If multiple faces should share one "
            "identity, their similarity is intentional; their original poses and expressions must "
            "still remain distinct.\n"
            "2. Score all six dimensions independently from visible evidence. Thresholds are: "
            "instruction_fulfillment >=85, target_identity_consistency >=85, face_naturalness >=80, "
            "pose_expression_preservation >=85, source_content_preservation >=90, and "
            "text_logo_preservation >=95.\n"
            "All six scores must be integers from 0 through 100. Never return 0-1 decimals or a "
            "0-10 score scale.\n"
            "3. Inspect missed or extra face edits, identity drift, facial artifacts, changed "
            "expression/angle/gaze/eyewear, and any body/background/object/layout/text/logo change. "
            "Every issue must cite a specific visible difference and a concrete correction.\n"
            "4. passed may be true only if every threshold is met and there is no critical issue.\n\n"
            "NEXT-PROMPT TASK:\n"
            "- If failed and another round remains, revised_edit_prompt is mandatory. It must be a "
            "complete standalone instruction for editing the original source again, never a delta, "
            "never a request to fix the failed candidate, and never a shortened replacement for the "
            "immutable user requirement.\n"
            "- Repeat the complete requested subject scope, target identity/appearance, and all "
            "source-preservation constraints. Add concrete corrections for every major or critical "
            "issue while retaining constraints that already succeeded.\n"
            "- If passed, or if this is the final allowed iteration, set revised_edit_prompt to null.\n"
            "Return JSON only using this shape: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )
        content = [
            {"type": "text", "text": instructions},
            {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
        ]
        if reference_uri:
            content.append(
                {"type": "image_url", "image_url": {"url": reference_uri, "detail": "high"}}
            )
        content.append(
            {"type": "image_url", "image_url": {"url": result_uri, "detail": "high"}}
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        raw = body["choices"][0]["message"]["content"]
        review = ImageEditReview.model_validate(_parse_json_object(raw))
        return enforce_image_edit_review(review)

    async def review_numbered_image_edit(
        self,
        *,
        request: SwapRequest,
        source_uri: str,
        result_uri: str,
        iteration: int,
    ) -> ImageEditReview:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        schema_hint = {
            "passed": False,
            "scores": {
                "instruction_fulfillment": 0,
                "target_identity_consistency": 0,
                "face_naturalness": 0,
                "pose_expression_preservation": 0,
                "source_content_preservation": 0,
                "text_logo_preservation": 0,
            },
            "issues": [],
            "summary": "short Chinese verdict",
            "revised_edit_prompt": "complete standalone next-round prompt or null",
        }
        mappings = [
            (
                f"@{operation.source_person_code} -> @{operation.target_identity_code} at "
                if operation.target_identity_code
                else f"text-directed edit candidate @{operation.source_person_code} at "
            )
            + f"({operation.bbox.x},{operation.bbox.y},{operation.bbox.width},{operation.bbox.height})"
            for operation in request.replacements
        ]
        source_appearances = {
            operation.source_person_code or operation.face_instance_id:
                operation.source_appearance.model_dump()
            for operation in request.replacements
        }
        operation_requirements = {
            operation.source_person_code or operation.face_instance_id: operation.instruction
            for operation in request.replacements
            if operation.instruction
        }
        has_target_references = any(
            operation.reference_uris for operation in request.replacements
        )
        instructions = (
            "ROLE: Strict reviewer and next-prompt author for a numbered image-edit task.\n"
            f"Image 1 is immutable original @{request.source_image_code or 'source'}. "
            "Image 2 is the current result. "
            + (
                "Later images are optional target identity references in mapping order.\n"
                if has_target_references
                else "No target identity reference was supplied; judge the requested appearance from text.\n"
            )
            + f"Immutable task requirement: {json.dumps(request.task_instruction, ensure_ascii=False)}\n"
            + f"Confirmed mappings: {json.dumps(mappings, ensure_ascii=False)}\n"
            + f"Source appearance contexts: {json.dumps(source_appearances, ensure_ascii=False)}\n"
            + f"Operation requirements and preview coordinate aids: {json.dumps(operation_requirements, ensure_ascii=False)}\n"
            + f"Subject selection mode: {request.selection_mode}. In prompt mode, listed people are candidates; "
            + "only people visibly matching the immutable requirement should be changed.\n"
            + f"Prompt used: {json.dumps(request.compiled_instruction, ensure_ascii=False)}\n"
            + f"Iteration: {iteration} of {request.max_iterations}.\n"
            + "Check every requested edit, target-reference similarity only when references exist, natural face quality, preservation "
            + "of source pose/expression/gaze, and preservation of all unrequested people, bodies, clothing, "
            + "background, objects, framing, lighting, text and logos. Compare the complete canvases and reject "
            + "any obvious whole-image color cast, white-balance, exposure, contrast, saturation or gamma shift. "
            + "Inspect the full head, hairline, ears, jaw, neck and nearby skin/clothing for seams, pasted-face "
            + "boundaries, halos, mismatched sharpness, noise, depth of field or local lighting. Also check that edited faces remain "
            + "coherent with the source person's visible age impression, face-to-body proportions, hair, "
            + "eyewear, accessories, clothing neckline, pose, lighting and occlusion without changing those "
            + "protected source traits unless explicitly requested. Do not name real people or claim "
            + "biometric verification. All six scores must be integers from 0 through 100; never use 0-1 "
            + "decimals or a 0-10 scale. passed may be true only when all score thresholds are met. If failed "
            + "and another iteration remains, revised_edit_prompt must be a complete standalone instruction "
            + "for editing the immutable original again. It must retain every numbered mapping and all "
            + "preservation constraints, then add concrete corrections. Never instruct editing the failed "
            + "candidate. On the final iteration or when passed, return null. Return JSON only: "
            + f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )
        content = [
            {"type": "text", "text": instructions},
            {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
            {"type": "image_url", "image_url": {"url": result_uri, "detail": "high"}},
        ]
        seen_references: set[str] = set()
        for operation in request.replacements:
            for reference_uri in operation.reference_uris:
                if reference_uri in seen_references:
                    continue
                seen_references.add(reference_uri)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": reference_uri, "detail": "high"},
                })
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        raw = body["choices"][0]["message"]["content"]
        review = ImageEditReview.model_validate(_parse_json_object(raw))
        return enforce_image_edit_review(review)

    async def validate(
        self,
        request: ValidationRequest,
        expected_edit: str | None = None,
    ) -> ValidationResult:
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")

        schema_hint = {
            "passed": False,
            "checks": {
                "requested_edits_complete": False,
                "unrequested_content_preserved": False,
                "target_identity_matches_references": False,
                "cross_image_identity_consistent": False,
                "face_quality_acceptable": False,
                "review_confidence": 0.0,
            },
            "reasons": ["short Chinese explanations"],
        }
        immutable_requirement = expected_edit or request.task_instruction
        compiled_mapping = request.compiled_instruction
        edit_context = (
            f" Immutable user requirement: {immutable_requirement[:4000]}."
            f" Compiled structured instruction: {compiled_mapping[:4000]}."
            " The immutable user requirement has priority. If the compiled mapping expands the set of "
            "people requested by the user, mark requested_edits_complete=false and "
            "unrequested_content_preserved=false, and report the extra person codes. Treat only changes "
            "explicitly requested by the immutable requirement as intentional. If it asks multiple faces "
            "to share one identity, their similarity is expected and must not by itself be flagged as an "
            "identity mixup."
            if immutable_requirement or compiled_mapping
            else ""
        )
        subject_lines = [
            (
                f"- @{subject.source_person_code} must become @{subject.target_identity_code} "
                if subject.target_identity_code
                else f"- @{subject.source_person_code} is a text-directed edit subject or candidate "
            )
            + f"inside bbox ({subject.bbox.x},{subject.bbox.y},{subject.bbox.width},{subject.bbox.height})."
            for subject in request.expected_subjects
        ]
        has_target_references = any(
            subject.reference_uris for subject in request.expected_subjects
        )
        peer_lines = [
            f"- Peer result @{peer.source_image_code} contains target identities "
            f"{', '.join('@' + code for code in peer.target_identity_codes)}."
            for peer in request.peer_results
        ]
        content = [
            {
                "type": "text",
                "text": (
                    "You are the final visual QA reviewer for a numbered image-edit task. "
                    f"Image 1 is the immutable original @{request.source_image_code or 'source'}; "
                    "Image 2 is its generated result. Compare them directly. Requested face identity "
                    "changes are intentional; all other people, pixels, composition, text, logos, "
                    "objects, lighting and style should remain as close to the original as possible. "
                    "The following numbered mappings are authoritative:\n"
                    + ("\n".join(subject_lines) or "- No structured subject mapping was supplied.")
                    + (
                        "\nReference images follow the result image in the same order as the mappings. "
                        "Judge whether each edited subject visually matches its numbered target references. "
                        if has_target_references
                        else "\nNo target identity reference was supplied. Treat target_identity_matches_references "
                        "as true when the visible result fulfills the text-defined appearance without unintended identity changes. "
                    )
                    + "Peer results follow the references; compare only people sharing the same target "
                    "identity code and require that identity to stay visually consistent across images, "
                    "while allowing source pose, expression, angle and lighting to differ.\n"
                    + ("\n".join(peer_lines) or "- No peer result exists; mark cross-image consistency true.")
                    + "\nDo not name real people or claim biometric verification. Set passed=true only "
                    "when all five boolean checks are true and review_confidence is at least 0.75. "
                    "Use JSON booleans, not strings."
                    f"{edit_context} Return JSON only using this shape: "
                    f"{json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
            {"type": "image_url", "image_url": {"url": request.source_uri}},
            {"type": "image_url", "image_url": {"url": request.result_uri}},
        ]
        for subject in request.expected_subjects:
            for reference_uri in subject.reference_uris:
                content.append({"type": "image_url", "image_url": {"url": reference_uri}})
        for peer in request.peer_results:
            content.append({"type": "image_url", "image_url": {"url": peer.result_uri}})
        parsed = _VisualValidationPayload.model_validate(
            await self._json_completion(content)
        )
        checks = parsed.checks.model_dump()
        enforced_pass = bool(
            parsed.passed
            and all(
                checks[name]
                for name in (
                    "requested_edits_complete",
                    "unrequested_content_preserved",
                    "target_identity_matches_references",
                    "cross_image_identity_consistent",
                    "face_quality_acceptable",
                )
            )
            and parsed.checks.review_confidence >= 0.75
        )
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=enforced_pass,
            provider=f"yunwu:{self.model}",
            checks=checks,
            reasons=parsed.reasons,
        )
