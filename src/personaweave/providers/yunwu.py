import asyncio
import base64
import hashlib
import json
from io import BytesIO
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw
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
    AnalysisResult,
    AttributePermission,
    BoundingBox,
    FaceObservation,
    FaceLocalizationAttempt,
    HEAD_ATTRIBUTES,
    NormalizedBoundingBox,
    PersonAppearance,
    PersonDetectionAudit,
    ReferenceSufficiency,
    SemanticMaskProposal,
    SourceFaceCondition,
    SourceOccurrenceEvidence,
    SourceOccurrenceReferenceAssessment,
    SourceOperationReferenceCoverage,
    SwapRequest,
    TargetIdentityProfile,
    ValidationRequest,
    ValidationResult,
    source_face_condition_requires_review,
)
from personaweave.providers.gateway import ProviderNotConfigured
from personaweave.provider_errors import ProviderExecutionError, provider_error


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


class LargePersonClustering(BaseModel):
    duplicate_groups: list[PersonCluster] = Field(default_factory=list)
    uncertain_codes: list[str] = Field(default_factory=list)


class PersonAppearanceObservation(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    appearance: PersonAppearance


class PersonAppearanceAnalysis(BaseModel):
    people: list[PersonAppearanceObservation]


class BatchPersonOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_code: str = Field(min_length=1, max_length=80)
    bbox: NormalizedBoundingBox
    detection_confidence: Annotated[float, Field(ge=0, le=1)]
    person_group_key: str = Field(min_length=1, max_length=120)
    identity_confidence: Annotated[float, Field(ge=0, le=1)]
    grouping_uncertain: ExactBool
    appearance: PersonAppearance


class BatchSourcePeople(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_code: str = Field(pattern=r"^I\d+$", max_length=40)
    complete: ExactBool
    visible_person_count: int = Field(ge=0, le=128)
    people: list[BatchPersonOccurrence] = Field(default_factory=list, max_length=128)
    summary: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_count(self) -> "BatchSourcePeople":
        if self.visible_person_count != len(self.people):
            raise ValueError("visible_person_count must equal the returned people count")
        return self


class BatchPersonAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[BatchSourcePeople] = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def lift_misnested_source_objects(cls, value: object) -> object:
        """Repair an unambiguous JSON nesting slip without changing detections."""
        if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
            return value
        pending = list(value["sources"])
        flattened: list[object] = []
        source_keys = {"source_code", "complete", "visible_person_count", "people"}
        while pending:
            item = pending.pop(0)
            if not isinstance(item, dict) or not isinstance(item.get("people"), list):
                flattened.append(item)
                continue
            people: list[object] = []
            nested_sources: list[object] = []
            for person in item["people"]:
                if isinstance(person, dict) and source_keys.issubset(person):
                    nested_sources.append(person)
                else:
                    people.append(person)
            normalized = dict(item)
            normalized["people"] = people
            flattened.append(normalized)
            pending[0:0] = nested_sources
        return {**value, "sources": flattened}


class TileBoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, lt=1000)
    y: int = Field(ge=0, lt=1000)
    width: int = Field(gt=0, le=1000)
    height: int = Field(gt=0, le=1000)

    @model_validator(mode="after")
    def validate_grid_bounds(self) -> "TileBoundingBox":
        if self.x + self.width > 1000 or self.y + self.height > 1000:
            raise ValueError("tile bbox must remain inside the 0-1000 canvas")
        return self


class PreciseFaceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_code: str = Field(min_length=1, max_length=80)
    bbox: TileBoundingBox
    confidence: Annotated[float, Field(ge=0, le=1)]


class PreciseSourceLocations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_code: str = Field(pattern=r"^I\d+$", max_length=40)
    locations: list[PreciseFaceLocation] = Field(max_length=128)


class FaceCropReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_code: str = Field(min_length=1, max_length=80)
    full_face_visible: ExactBool
    crop_contains_all_source_visible_face: ExactBool
    matches_expected_occurrence: ExactBool = False
    source_face_condition: SourceFaceCondition
    face_centered: ExactBool
    tight_face_crop: ExactBool
    boundary_issue: Literal[
        "none",
        "top_clipped",
        "bottom_clipped",
        "left_clipped",
        "right_clipped",
        "multiple_clipped",
    ] = "none"
    unrecoverable_occlusion: ExactBool = False
    reason: str = Field(default="", max_length=300)

    @field_validator("source_face_condition", mode="before")
    @classmethod
    def normalize_source_face_condition(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return {
            "minor": "minor_occlusion",
            "mild_occlusion": "minor_occlusion",
            "natural_occlusion": "minor_occlusion",
            "major": "major_occlusion",
            "severe_occlusion": "major_occlusion",
            "boundary": "source_boundary",
            "source_clipped": "source_boundary",
            "unclear": "unusable",
            "too_unclear": "unusable",
        }.get(normalized, normalized)

    @model_validator(mode="after")
    def canonicalize_legacy_occlusion_flag(self) -> "FaceCropReviewItem":
        self.unrecoverable_occlusion = source_face_condition_requires_review(
            self.source_face_condition
        )
        return self

    @field_validator("boundary_issue", mode="before")
    @classmethod
    def normalize_boundary_issue(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "top": "top_clipped",
            "bottom": "bottom_clipped",
            "left": "left_clipped",
            "right": "right_clipped",
            "multiple": "multiple_clipped",
            "no": "none",
            "no_issue": "none",
            "no_boundary_issue": "none",
            "not_clipped": "none",
            "fully_visible": "none",
        }
        if normalized in aliases:
            return aliases[normalized]
        directions = {
            direction
            for direction in ("top", "bottom", "left", "right")
            if direction in normalized.split("_")
        }
        if len(directions) > 1:
            return "multiple_clipped"
        if len(directions) == 1 and any(
            marker in normalized for marker in ("clip", "cut", "crop", "edge")
        ):
            return f"{directions.pop()}_clipped"
        return normalized


class FaceCropReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_code: str = Field(pattern=r"^I\d+$", max_length=40)
    items: list[FaceCropReviewItem] = Field(max_length=128)


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
        closing_fence = content.find("```", first_newline + 1) if first_newline != -1 else -1
        if first_newline == -1 or closing_fence == -1:
            raise ValueError("Yunwu returned an incomplete fenced JSON response")
        content = content[first_newline + 1 : closing_fence].strip()
    if len(content) > 512_000:
        raise ValueError("Yunwu response content exceeds the bounded JSON limit")
    start = content.find("{")
    if start < 0:
        raise ValueError("Yunwu response did not contain a JSON object")
    try:
        # Some OpenAI-compatible gateways append a second JSON object or a
        # short explanatory suffix despite response_format=json_object. The
        # contract validators below still require the complete expected
        # schema, IDs, and permission partitions before accepting this object.
        parsed, _ = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError as error:
        raise ValueError("Yunwu response did not contain a complete JSON object") from error
    if not isinstance(parsed, dict):
        raise ValueError("Yunwu response must be a JSON object")
    return parsed


_JSON_COMPLETION_MAX_TOKENS = 16_384


def _configured_endpoint(base_url: str, api_path: str) -> str:
    if api_path.startswith(("http://", "https://")):
        parsed = urlparse(api_path)
        if not parsed.netloc:
            raise ValueError("Yunwu API path must be an absolute HTTP(S) URL")
        return api_path.rstrip("/")
    normalized = f"/{api_path.lstrip('/')}"
    return f"{base_url.rstrip('/')}{normalized}"


class YunwuMultimodalProvider:
    """OpenAI-compatible multimodal analysis, planning, and visual QA adapter.

    Same-person grouping is limited to visual continuity within one user batch.
    It never names people or verifies real-world identity.
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
            # Batch person analysis can describe many occurrences across several
            # source images. OpenAI-compatible reasoning models otherwise apply
            # a small/default completion budget and may stop before closing the
            # required JSON object.
            "max_completion_tokens": _JSON_COMPLETION_MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        body = await self._post_json_payload(payload)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("Yunwu response did not contain a completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("Yunwu response did not contain a completion message")
        try:
            return _parse_json_object(message.get("content"))
        except ValueError as error:
            if choice.get("finish_reason") in {"length", "max_tokens"}:
                raise ValueError(
                    "Yunwu JSON response was truncated at the output-token limit"
                ) from error
            raise

    async def _post_json_payload(self, payload: dict[str, object]) -> dict:
        request_digest = hashlib.sha256(
            (
                self.endpoint
                + "\n"
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode("utf-8")
        ).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": f"personaweave-v1-{request_digest}",
        }
        response: httpx.Response | None = None
        for attempt in range(2):
            response = await self._client.post(
                self.endpoint,
                headers=headers,
                json=payload,
            )
            retryable_status = response.status_code == 429 or response.status_code >= 500
            if not retryable_status or attempt == 1:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = min(30, int(retry_after)) if retry_after.isdigit() else 2
            await asyncio.sleep(delay)
        assert response is not None
        if response.status_code == 429:
            raise ProviderExecutionError(provider_error(
                "provider_rate_limited",
                upstream_status=response.status_code,
            ))
        if response.status_code >= 500:
            raise ProviderExecutionError(provider_error(
                "provider_unavailable",
                upstream_status=response.status_code,
            ))
        if response.status_code >= 400:
            raise ProviderExecutionError(provider_error(
                "provider_request_rejected",
                upstream_status=response.status_code,
            ))
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Yunwu response must be a JSON object")
        return body

    async def analyze_batch_people(
        self,
        sources: list[dict[str, Any]],
    ) -> list[AnalysisResult]:
        """Discover and group visible people jointly across a complete image batch."""
        if not sources or len(sources) > 128:
            raise ValueError("batch person analysis requires 1 to 128 source images")
        source_codes = [str(source["source_code"]) for source in sources]
        if len(source_codes) != len(set(source_codes)):
            raise ValueError("source codes must be unique")
        shape = {
            "sources": [{
                "source_code": source_codes[0],
                "complete": True,
                "visible_person_count": 1,
                "people": [{
                    "occurrence_code": f"{source_codes[0]}-V01",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.3},
                    "detection_confidence": 0.9,
                    "person_group_key": "G01",
                    "identity_confidence": 0.9,
                    "grouping_uncertain": False,
                    "appearance": {
                        "apparent_age": "visible age impression or uncertain",
                        "face_features": "visible facial traits or uncertain",
                        "hair": "visible hair or uncertain",
                        "eyewear": "visible eyewear or none/uncertain",
                        "accessories": "visible accessories or none/uncertain",
                        "body": "visible build/context or uncertain",
                        "clothing": "visible clothing or uncertain",
                        "pose": "visible pose/gaze or uncertain",
                        "lighting_occlusion": "lighting, blur and occlusion context",
                    },
                }],
                "summary": "short completeness and uncertainty note",
            }]
        }
        content: list[dict[str, object]] = [{
            "type": "text",
            "text": (
                "Analyze ALL supplied original images jointly as one batch. Enumerate every visible "
                "real person whose face is discernible enough to be selected for a face edit, including "
                "profiles, small faces and partially occluded faces. Exclude printed images, screens, "
                "statues and reflections. Return one conservative normalized face bbox per occurrence "
                "in the displayed, EXIF-oriented image. Group occurrences across images only when they "
                "visually depict the same source person. Never put two people from the same source image "
                "in one group. Do not name anyone or infer sensitive attributes. If a cross-image match "
                "is uncertain, keep it as a singleton group, set grouping_uncertain=true, and lower "
                "identity_confidence; never guess. Describe only visible appearance and explicitly write "
                "'uncertain' when evidence is insufficient. Set complete=true only after checking the "
                "entire image and make visible_person_count exactly match people. Return every supplied "
                "source_code exactly once and return JSON only. Every source object must be a direct item "
                "of the top-level sources array; close its people array before starting the next source. "
                "Never put a source object inside another source's people array. Coordinates are fractions "
                "in [0,1]. "
                f"Exact source codes: {json.dumps(source_codes)}. Exact shape: "
                f"{json.dumps(shape, ensure_ascii=False)}"
            ),
        }]
        for source in sources:
            content.extend([
                {"type": "text", "text": f"SOURCE {source['source_code']}"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": source.get("analysis_image_uri", source["image_uri"]),
                        "detail": "original",
                    },
                },
            ])
        compiled = BatchPersonAnalysis.model_validate(
            await self._json_completion(content)
        )
        returned_codes = [source.source_code for source in compiled.sources]
        if (
            len(returned_codes) != len(set(returned_codes))
            or set(returned_codes) != set(source_codes)
        ):
            raise ValueError("batch person analysis must cover every source exactly once")
        by_code = {source.source_code: source for source in compiled.sources}
        results: list[AnalysisResult] = []
        for source in sources:
            source_code = str(source["source_code"])
            analysis = by_code[source_code]
            if not analysis.complete:
                raise ValueError(f"batch person analysis was incomplete for {source_code}")
            occurrence_codes = [person.occurrence_code for person in analysis.people]
            if len(occurrence_codes) != len(set(occurrence_codes)):
                raise ValueError(f"duplicate occurrence codes returned for {source_code}")
            group_keys = [person.person_group_key for person in analysis.people]
            if len(group_keys) != len(set(group_keys)):
                raise ValueError(f"two people in {source_code} cannot share one group")
            width = int(source["width"])
            height = int(source["height"])
            if width <= 0 or height <= 0:
                raise ValueError("source dimensions must be positive")
            faces: list[FaceObservation] = []
            requires_review = False
            for index, person in enumerate(analysis.people, start=1):
                bbox = person.bbox
                x = min(width - 1, max(0, round(bbox.x * width)))
                y = min(height - 1, max(0, round(bbox.y * height)))
                box_width = min(width - x, max(1, round(bbox.width * width)))
                box_height = min(height - y, max(1, round(bbox.height * height)))
                uncertain = person.grouping_uncertain or person.identity_confidence < 0.8
                group_key = (
                    f"vlm-uncertain:{source_code}:{person.occurrence_code}"
                    if uncertain
                    else f"vlm:{person.person_group_key}"
                )
                requires_review = requires_review or uncertain
                faces.append(FaceObservation(
                    external_face_id=person.occurrence_code or f"{source_code}-V{index:02d}",
                    bbox={"x": x, "y": y, "width": box_width, "height": box_height},
                    detection_confidence=person.detection_confidence,
                    suggested_person_key=group_key,
                    identity_confidence=person.identity_confidence,
                    needs_confirmation=True,
                    appearance=person.appearance,
                ))
            results.append(AnalysisResult(
                asset_id=str(source["asset_id"]),
                faces=faces,
                provider="yunwu-multimodal-batch-analysis",
                model=self.model,
                detection_audit=PersonDetectionAudit(
                    available=True,
                    complete=True,
                    detected_face_count=len(faces),
                    visible_face_count=analysis.visible_person_count,
                    requires_manual_review=requires_review,
                    summary=analysis.summary,
                ),
            ))
        return results

    async def refine_batch_face_boxes(
        self,
        sources: list[dict[str, Any]],
        results: list[AnalysisResult],
        *,
        broad_search: bool = False,
    ) -> list[AnalysisResult]:
        """Refine coarse batch boxes per image and verify deterministic face crops."""
        source_by_id = {str(source["asset_id"]): source for source in sources}
        if set(source_by_id) != {result.asset_id for result in results}:
            raise ValueError("precise localization inputs do not cover the same source set")
        semaphore = asyncio.Semaphore(2)

        async def refine_one(result: AnalysisResult) -> AnalysisResult:
            async with semaphore:
                return await self._refine_source_face_boxes(
                    source_by_id[result.asset_id],
                    result,
                    broad_search=broad_search,
                )

        return list(await asyncio.gather(*(refine_one(result) for result in results)))

    async def _refine_source_face_boxes(
        self,
        source: dict[str, Any],
        original_result: AnalysisResult,
        *,
        broad_search: bool = False,
    ) -> AnalysisResult:
        result = original_result.model_copy(deep=True)
        if not result.faces:
            return result
        faces_by_code = {face.external_face_id: face for face in result.faces}
        anchor_bbox_by_code = {
            code: face.bbox.model_copy(deep=True)
            for code, face in faces_by_code.items()
        }
        pending_codes = list(faces_by_code)
        feedback_by_code: dict[str, str] = {}
        last_review_by_code: dict[str, FaceCropReviewItem] = {}
        last_confidence_by_code: dict[str, float] = {}
        last_candidate_by_code: dict[str, FaceObservation] = {}
        source_occlusion_by_code: dict[str, str] = {}
        result.localization_attempts = []
        for attempt in range(1, 3):
            target_faces = [faces_by_code[code] for code in pending_codes]
            localization_faces = [
                face.model_copy(update={
                    "bbox": anchor_bbox_by_code[face.external_face_id],
                })
                for face in target_faces
            ]
            locations, tile_by_code = await self._localize_source_faces(
                source=source,
                faces=localization_faces,
                feedback_by_code=feedback_by_code,
                attempt=attempt,
                expansion=(
                    10.0
                    if broad_search
                    else (3.0 if attempt == 1 else 6.0)
                ),
            )
            location_by_code = {
                location.occurrence_code: location for location in locations.locations
            }
            if (
                len(location_by_code) != len(locations.locations)
                or set(location_by_code) != set(pending_codes)
            ):
                raise ValueError(
                    f"tile localization must cover every pending face in {source['source_code']}"
                )
            width = int(source["width"])
            height = int(source["height"])
            candidate_by_code: dict[str, FaceObservation] = {}
            for code in pending_codes:
                face = faces_by_code[code]
                location = location_by_code[code]
                tile = tile_by_code[code]
                box = location.bbox
                x = min(width - 1, tile.x + round(box.x / 1000 * tile.width))
                y = min(height - 1, tile.y + round(box.y / 1000 * tile.height))
                box_width = min(
                    width - x,
                    max(1, round(box.width / 1000 * tile.width)),
                )
                box_height = min(
                    height - y,
                    max(1, round(box.height / 1000 * tile.height)),
                )
                if self._precise_location_is_plausible(location):
                    candidate_bbox = BoundingBox(
                        x=x, y=y, width=box_width, height=box_height
                    )
                    candidate_confidence = location.confidence
                else:
                    candidate_bbox = anchor_bbox_by_code[code].model_copy(deep=True)
                    candidate_confidence = face.detection_confidence
                candidate = face.model_copy(update={
                    "bbox": candidate_bbox,
                    "detection_confidence": candidate_confidence,
                })
                candidate_by_code[code] = candidate
                last_candidate_by_code[code] = candidate
                last_confidence_by_code[code] = location.confidence
            review = await self._review_source_face_crops(
                source,
                [candidate_by_code[code] for code in pending_codes],
            )
            review_by_code = {item.occurrence_code: item for item in review.items}
            if (
                len(review_by_code) != len(review.items)
                or set(review_by_code) != set(pending_codes)
            ):
                raise ValueError(
                    f"crop review must cover every pending face in {source['source_code']}"
                )
            next_pending: list[str] = []
            feedback_by_code = {}
            for code in pending_codes:
                item = review_by_code[code]
                last_review_by_code[code] = item
                accepted = bool(
                    item.crop_contains_all_source_visible_face
                    and item.matches_expected_occurrence
                    and item.face_centered
                    and item.tight_face_crop
                )
                candidate = candidate_by_code[code]
                result.localization_attempts.append(FaceLocalizationAttempt(
                    occurrence_code=code,
                    attempt=attempt,
                    bbox=candidate.bbox,
                    confidence=location_by_code[code].confidence,
                    full_face_visible=item.full_face_visible,
                    crop_contains_all_source_visible_face=(
                        item.crop_contains_all_source_visible_face
                    ),
                    matches_expected_occurrence=item.matches_expected_occurrence,
                    source_face_condition=item.source_face_condition,
                    face_centered=item.face_centered,
                    tight_face_crop=item.tight_face_crop,
                    accepted=accepted,
                    boundary_issue=item.boundary_issue,
                    unrecoverable_occlusion=item.unrecoverable_occlusion,
                    reason=item.reason,
                ))
                if accepted:
                    faces_by_code[code].bbox = candidate.bbox.model_copy(deep=True)
                    faces_by_code[code].detection_confidence = (
                        candidate.detection_confidence
                    )
                    if source_face_condition_requires_review(
                        item.source_face_condition
                    ):
                        source_occlusion_by_code[code] = (
                            item.reason or "source image hides core facial features"
                        )
                else:
                    next_pending.append(code)
                    feedback_by_code[code] = item.reason or "crop geometry failed"
            pending_codes = next_pending
            if not pending_codes:
                break
        for repair_attempt in range(3, 5):
            repair_codes = [
                code
                for code in pending_codes
                if last_review_by_code[code].boundary_issue != "none"
            ]
            if not repair_codes:
                break
            width = int(source["width"])
            height = int(source["height"])
            repair_by_code: dict[str, FaceObservation] = {}
            for code in repair_codes:
                candidate = last_candidate_by_code[code]
                repaired_bbox = self._apply_directional_boundary_repair(
                    candidate.bbox,
                    image_width=width,
                    image_height=height,
                    issue=last_review_by_code[code].boundary_issue,
                )
                repair_by_code[code] = candidate.model_copy(
                    update={"bbox": repaired_bbox}
                )
            repair_faces = [repair_by_code[code] for code in repair_codes]
            review = await self._review_source_face_crops(source, repair_faces)
            review_by_code = {item.occurrence_code: item for item in review.items}
            if (
                len(review_by_code) != len(review.items)
                or set(review_by_code) != set(repair_codes)
            ):
                raise ValueError(
                    f"boundary repair review must cover every repaired face in {source['source_code']}"
                )
            for code in repair_codes:
                item = review_by_code[code]
                last_review_by_code[code] = item
                accepted = bool(
                    item.crop_contains_all_source_visible_face
                    and item.matches_expected_occurrence
                    and item.face_centered
                    and item.tight_face_crop
                )
                candidate = repair_by_code[code]
                result.localization_attempts.append(FaceLocalizationAttempt(
                    occurrence_code=code,
                    attempt=repair_attempt,
                    bbox=candidate.bbox,
                    confidence=last_confidence_by_code[code],
                    full_face_visible=item.full_face_visible,
                    crop_contains_all_source_visible_face=(
                        item.crop_contains_all_source_visible_face
                    ),
                    matches_expected_occurrence=item.matches_expected_occurrence,
                    source_face_condition=item.source_face_condition,
                    face_centered=item.face_centered,
                    tight_face_crop=item.tight_face_crop,
                    accepted=accepted,
                    boundary_issue=item.boundary_issue,
                    unrecoverable_occlusion=item.unrecoverable_occlusion,
                    reason=item.reason,
                ))
                last_candidate_by_code[code] = candidate
                if accepted:
                    faces_by_code[code].bbox = candidate.bbox.model_copy(deep=True)
                    faces_by_code[code].detection_confidence = (
                        candidate.detection_confidence
                    )
                    if source_face_condition_requires_review(
                        item.source_face_condition
                    ):
                        source_occlusion_by_code[code] = (
                            item.reason or "source image hides core facial features"
                        )
                    pending_codes.remove(code)
                    feedback_by_code.pop(code, None)
                else:
                    feedback_by_code[code] = item.reason or "boundary repair failed"
        geometry_verified = not pending_codes
        audit = result.detection_audit or PersonDetectionAudit(
            complete=True,
            detected_face_count=len(result.faces),
            visible_face_count=len(result.faces),
        )
        existing_summary = audit.summary.strip()
        geometry_summary = (
            "逐图面部裁剪复核通过。"
            if geometry_verified
            else "逐图面部裁剪复核未通过：" + "；".join(
                f"{code}: {feedback_by_code.get(code, 'crop geometry failed')}"
                for code in pending_codes
            )
        )
        source_summary = (
            " 原图面部条件需人工确认：" + "；".join(
                f"{code}: {reason}"
                for code, reason in source_occlusion_by_code.items()
            )
            if source_occlusion_by_code
            else ""
        )
        result.detection_audit = audit.model_copy(update={
            "requires_manual_review": bool(
                audit.requires_manual_review
                or not geometry_verified
                or source_occlusion_by_code
            ),
            "summary": (
                f"{existing_summary} {geometry_summary}{source_summary}"
            ).strip()[:1000],
        })
        return result

    @staticmethod
    def _precise_location_is_plausible(location: PreciseFaceLocation) -> bool:
        box = location.bbox
        aspect_ratio = box.width / box.height
        return bool(
            location.confidence >= 0.45
            and min(box.width, box.height) >= 40
            and 0.3 <= aspect_ratio <= 2.5
        )

    @staticmethod
    def _face_anchor_is_unreliable(
        face: FaceObservation,
        *,
        image_width: int,
        image_height: int,
    ) -> bool:
        bbox = face.bbox
        aspect_ratio = bbox.width / bbox.height
        minimum_dimension = max(12, round(min(image_width, image_height) * 0.003))
        return bool(
            face.detection_confidence < 0.45
            or min(bbox.width, bbox.height) < minimum_dimension
            or not 0.3 <= aspect_ratio <= 2.5
        )

    @staticmethod
    def _apply_directional_boundary_repair(
        bbox: BoundingBox,
        *,
        image_width: int,
        image_height: int,
        issue: Literal[
            "top_clipped",
            "bottom_clipped",
            "left_clipped",
            "right_clipped",
            "multiple_clipped",
        ],
    ) -> BoundingBox:
        expansion_ratio = 0.18 if issue == "multiple_clipped" else 0.6
        horizontal = max(1, round(bbox.width * expansion_ratio))
        vertical = max(1, round(bbox.height * expansion_ratio))
        left = bbox.x
        top = bbox.y
        right = bbox.x + bbox.width
        bottom = bbox.y + bbox.height
        if issue in {"left_clipped", "multiple_clipped"}:
            left -= horizontal
        if issue in {"right_clipped", "multiple_clipped"}:
            right += horizontal
        if issue in {"top_clipped", "multiple_clipped"}:
            top -= vertical
        if issue in {"bottom_clipped", "multiple_clipped"}:
            bottom += vertical
        left = max(0, left)
        top = max(0, top)
        right = min(image_width, right)
        bottom = min(image_height, bottom)
        return BoundingBox(
            x=left,
            y=top,
            width=max(1, right - left),
            height=max(1, bottom - top),
        )

    async def _localize_source_faces(
        self,
        *,
        source: dict[str, Any],
        faces: list[FaceObservation],
        feedback_by_code: dict[str, str],
        attempt: int,
        expansion: float,
    ) -> tuple[PreciseSourceLocations, dict[str, BoundingBox]]:
        source_code = str(source["source_code"])
        tile_uri_by_code, tile_by_code = self._localization_tile_images(
            source["image_uri"],
            faces,
            expansion=expansion,
        )
        occurrences = [
            {
                "occurrence_code": face.external_face_id,
                "visible_appearance": face.appearance.model_dump(),
                "expected_person_appearances": source.get(
                    "expected_person_appearances", []
                ),
                "previous_review_failure": feedback_by_code.get(face.external_face_id, ""),
            }
            for face in faces
        ]
        instruction = (
            "Each labeled panel is an independently enlarged source-image tile for exactly one "
            "occurrence. Precisely localize the FACE inside each matching tile. Coordinates are "
            "relative to that tile's own 0-1000 cyan grid, never the whole source image. Return a "
            "tight face rectangle: include the complete visible "
            "forehead, eyes, nose, mouth, cheeks and chin, plus only minimal surrounding hair. Exclude "
            "neck, shoulders, chest, arms and clothing. For a profile or occluded face, bound only the "
            "visible face while retaining the full visible chin and forehead. Use integer coordinates "
            "on each tile's 0-1000 grid. If a tile contains multiple faces, use the supplied appearance "
            "and the face closest to the tile center; never switch to a neighboring person. Do not "
            "The supplied appearance evidence is authoritative: do not choose the face nearest the old "
            "anchor when it conflicts with that evidence. Use proximity only to break a tie between "
            "appearance-compatible faces. Do not change occurrence codes, add people, remove people, or "
            "group identities. Return every "
            "supplied tile exactly once as JSON."
            f"\nSOURCE: {source_code}"
            f"\nOCCURRENCES: {json.dumps(occurrences, ensure_ascii=False)}"
            + (
                "\nRETURN SHAPE: {\"source_code\":\""
                + source_code
                + "\",\"locations\":[{\"occurrence_code\":\"exact code\","
                "\"bbox\":{\"x\":\"integer 0-999\",\"y\":\"integer 0-999\","
                "\"width\":\"positive integer\",\"height\":\"positive integer\"},"
                "\"confidence\":0.0}]}"
            )
        )
        content: list[dict[str, object]] = [{"type": "text", "text": instruction}]
        for face in faces:
            code = face.external_face_id
            content.extend([
                {"type": "text", "text": f"INDEPENDENT TILE: {code}"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": tile_uri_by_code[code],
                        "detail": "original",
                    },
                },
            ])
        raw_location = await self._json_completion(content)
        raw_locations = raw_location.get("locations")
        if isinstance(raw_locations, list):
            raw_locations = [
                {
                    "occurrence_code": item.get("occurrence_code"),
                    "bbox": (
                        {
                            "x": item["bbox"].get("x"),
                            "y": item["bbox"].get("y"),
                            "width": item["bbox"].get("width"),
                            "height": item["bbox"].get("height"),
                        }
                        if isinstance(item.get("bbox"), dict)
                        else item.get("bbox")
                    ),
                    "confidence": item.get("confidence"),
                }
                if isinstance(item, dict)
                else item
                for item in raw_locations
            ]
        located = PreciseSourceLocations.model_validate({
            "source_code": raw_location.get("source_code"),
            "locations": raw_locations,
        })
        if located.source_code != source_code:
            raise ValueError("precise localization returned a mismatched source code")
        return located, tile_by_code

    async def _review_source_face_crops(
        self,
        source: dict[str, Any],
        faces: list[FaceObservation],
    ) -> FaceCropReview:
        source_code = str(source["source_code"])
        board_uri = self._face_crop_review_board(source["image_uri"], faces)
        codes = [face.external_face_id for face in faces]
        expected_appearances = {
            face.external_face_id: {
                "occurrence_appearance": face.appearance.model_dump(),
                "same_person_cross_image_appearances": source.get(
                    "expected_person_appearances", []
                ),
            }
            for face in faces
        }
        shape = {
            "source_code": source_code,
            "items": [{
                "occurrence_code": codes[0],
                "full_face_visible": True,
                "crop_contains_all_source_visible_face": True,
                "matches_expected_occurrence": True,
                "source_face_condition": "complete",
                "face_centered": True,
                "tight_face_crop": True,
                "boundary_issue": "none",
                "unrecoverable_occlusion": False,
                "reason": "short visible-only explanation",
            }],
        }
        raw_review = await self._json_completion([
            {
                "type": "text",
                "text": (
                    "Compare each labeled deterministic FACE crop with the full source context supplied "
                    "before it. The full source is authoritative for deciding whether a missing facial "
                    "part was cut off by the crop or is already hidden in the source photograph. "
                    "Judge source condition and crop geometry independently. full_face_visible describes "
                    "whether the face is complete in the full source itself. "
                    "Classify source_face_condition as exactly one of: complete; minor_occlusion for small "
                    "natural overlaps such as glasses, hair strands, a lollipop, a straw, or an object that "
                    "does not hide core identity features; major_occlusion when a hand, phone, object, or person "
                    "hides core eyes, nose, mouth, or most of one facial side; source_boundary when the original "
                    "image edge cuts the face; unusable when blur or distance prevents reliable inspection. "
                    "Only major_occlusion, source_boundary, and unusable require manual review. "
                    "Also verify identity localization against EXPECTED_APPEARANCES. "
                    "matches_expected_occurrence must be false when the crop shows a different source person, "
                    "including conflicts in stable traits such as eyewear, hair, face shape, or accessories. "
                    "Do not demand identical pose, expression, lighting, or occlusion across images. "
                    "crop_contains_all_source_visible_face must be true only when the crop contains every facial "
                    "pixel that is actually visible in the full source, even if other pixels are inherently "
                    "occluded there. The crop must also keep the intended face dominant and reasonably centered. "
                    "Hair, hats, glasses, natural occluders, and a small amount of collar, shoulder, or surrounding "
                    "context are allowed. Set crop_contains_all_source_visible_face=false when the crop contains "
                    "the wrong person, omits source-visible forehead, chin, or an essential side, or torso/background "
                    "clearly dominates. Set boundary_issue to exactly one of top_clipped, bottom_clipped, "
                    "left_clipped, right_clipped, multiple_clipped, or none. Use multiple_clipped whenever "
                    "more than one edge clips visible facial pixels. A "
                    "boundary issue means facial pixels visible in the full source were excluded by the crop. "
                    "When source_face_condition is major_occlusion, source_boundary, or unusable, do not request "
                    "a wider crop to recover source-missing pixels; set unrecoverable_occlusion=true. Keep it "
                    "false for complete and minor_occlusion. Only also "
                    "set boundary_issue when the crop independently excludes visible facial pixels. "
                    "Do not fail merely because hair, a hat, a little neck/clothing, or normal photographic "
                    "context is visible. Do not "
                    "identify or regroup people. Return every exact occurrence code once as JSON. "
                    f"SOURCE: {source_code}. CODES: {json.dumps(codes)}. "
                    f"EXPECTED_APPEARANCES: {json.dumps(expected_appearances, ensure_ascii=False)}. "
                    f"SHAPE: {json.dumps(shape, ensure_ascii=False)}"
                ),
            },
            {
                "type": "text",
                "text": "FULL SOURCE CONTEXT (authoritative occlusion evidence)",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": source.get("analysis_image_uri", source["image_uri"]),
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": "DETERMINISTIC FACE CROPS TO REVIEW",
            },
            {
                "type": "image_url",
                "image_url": {"url": board_uri, "detail": "high"},
            },
        ])
        raw_review = self._normalize_face_crop_review_payload(
            raw_review,
            source_code=source_code,
            occurrence_codes=codes,
        )
        raw_items = raw_review.get("items")
        if isinstance(raw_items, list):
            raw_items = [
                {
                    "occurrence_code": item.get("occurrence_code"),
                    "full_face_visible": item.get("full_face_visible"),
                    "crop_contains_all_source_visible_face": item.get(
                        "crop_contains_all_source_visible_face",
                        bool(
                            item.get("boundary_issue", "none") == "none"
                            and item.get("face_centered")
                            and item.get("tight_face_crop")
                            and (
                                item.get("full_face_visible")
                                or item.get("unrecoverable_occlusion")
                            )
                        ),
                    ),
                    "matches_expected_occurrence": item.get(
                        "matches_expected_occurrence", False
                    ),
                    "source_face_condition": item.get(
                        "source_face_condition",
                        (
                            "major_occlusion"
                            if item.get("unrecoverable_occlusion")
                            else "complete"
                        ),
                    ),
                    "face_centered": item.get("face_centered"),
                    "tight_face_crop": item.get("tight_face_crop"),
                    "boundary_issue": item.get("boundary_issue", "none"),
                    "unrecoverable_occlusion": item.get(
                        "unrecoverable_occlusion", False
                    ),
                    "reason": item.get("reason", ""),
                }
                if isinstance(item, dict)
                else item
                for item in raw_items
            ]
        reviewed = FaceCropReview.model_validate({
            "source_code": raw_review.get("source_code"),
            "items": raw_items,
        })
        if reviewed.source_code != source_code:
            raise ValueError("crop review returned a mismatched source code")
        return reviewed

    @staticmethod
    def _normalize_face_crop_review_payload(
        raw_review: dict[str, Any],
        *,
        source_code: str,
        occurrence_codes: list[str],
    ) -> dict[str, Any]:
        candidate: object = raw_review
        if isinstance(candidate.get(source_code), dict):
            candidate = candidate[source_code]
        for wrapper in (
            "face_crop_review",
            "crop_review",
            "review",
            "result",
            "output",
            "data",
        ):
            if isinstance(candidate, dict) and isinstance(candidate.get(wrapper), dict):
                candidate = candidate[wrapper]
                break
        if not isinstance(candidate, dict):
            raise ValueError("crop review response must be a JSON object")

        items: object = candidate.get("items")
        if not isinstance(items, list):
            for alias in ("reviews", "results", "faces", "occurrences"):
                if isinstance(candidate.get(alias), list):
                    items = candidate[alias]
                    break
        if not isinstance(items, list) and isinstance(candidate.get("occurrence_code"), str):
            items = [candidate]
        if not isinstance(items, list):
            keyed_items = []
            for code in occurrence_codes:
                item = candidate.get(code)
                if not isinstance(item, dict):
                    keyed_items = []
                    break
                keyed_items.append({"occurrence_code": code, **item})
            if keyed_items:
                items = keyed_items
        if not isinstance(items, list):
            keys = ",".join(sorted(str(key) for key in candidate)[:12])
            raise ValueError(
                f"crop review response has no bounded item list (keys: {keys})"
            )

        returned_source = candidate.get(
            "source_code",
            raw_review.get("source_code", source_code),
        )
        return {"source_code": returned_source, "items": items}

    def _localization_tile_images(
        self,
        image_uri: str,
        faces: list[FaceObservation],
        *,
        expansion: float,
    ) -> tuple[dict[str, str], dict[str, BoundingBox]]:
        _, encoded = image_uri.split(",", 1)
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            source = image.convert("RGB")
        cell_size = 384
        label_height = 28
        tile_uri_by_code: dict[str, str] = {}
        tile_by_code: dict[str, BoundingBox] = {}
        for face in faces:
            bbox = face.bbox
            unreliable_anchor = self._face_anchor_is_unreliable(
                face,
                image_width=source.width,
                image_height=source.height,
            )
            minimum_ratio = 0.25 if unreliable_anchor else 0.1
            minimum_side = max(96, round(min(source.size) * minimum_ratio))
            side = min(
                min(source.size),
                max(minimum_side, round(max(bbox.width, bbox.height) * expansion)),
            )
            center_x = bbox.x + bbox.width / 2
            center_y = bbox.y + bbox.height / 2
            left = min(max(0, round(center_x - side / 2)), max(0, source.width - side))
            top = min(max(0, round(center_y - side / 2)), max(0, source.height - side))
            right = min(source.width, left + side)
            bottom = min(source.height, top + side)
            tile = BoundingBox(
                x=left,
                y=top,
                width=right - left,
                height=bottom - top,
            )
            tile_by_code[face.external_face_id] = tile
            content_size = cell_size - label_height
            crop = source.crop((left, top, right, bottom)).resize(
                (content_size, content_size),
                Image.Resampling.LANCZOS,
            )
            cell = Image.new("RGB", (cell_size, cell_size), "white")
            content_left = (cell_size - content_size) // 2
            cell.paste(crop, (content_left, label_height))
            draw = ImageDraw.Draw(cell)
            draw.text((8, 7), face.external_face_id, fill=(0, 0, 0))
            for step in range(0, 1001, 250):
                x = content_left + round(step / 1000 * (content_size - 1))
                y = label_height + round(
                    step / 1000 * (content_size - 1)
                )
                draw.line((x, label_height, x, cell_size), fill=(0, 220, 220), width=1)
                draw.line(
                    (content_left, y, content_left + content_size, y),
                    fill=(0, 220, 220),
                    width=1,
                )
            output = BytesIO()
            cell.save(output, format="JPEG", quality=92, optimize=True)
            tile_uri_by_code[face.external_face_id] = (
                "data:image/jpeg;base64,"
                + base64.b64encode(output.getvalue()).decode("ascii")
            )
        return tile_uri_by_code, tile_by_code

    def _face_crop_review_board(
        self,
        image_uri: str,
        faces: list[FaceObservation],
    ) -> str:
        _, encoded = image_uri.split(",", 1)
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            source = image.convert("RGB")
        cell_size = 300
        label_height = 28
        columns = min(5, max(1, len(faces)))
        rows = (len(faces) + columns - 1) // columns
        board = Image.new("RGB", (columns * cell_size, rows * cell_size), "white")
        for index, face in enumerate(faces):
            bbox = face.bbox
            crop = source.crop((
                bbox.x,
                bbox.y,
                min(source.width, bbox.x + bbox.width),
                min(source.height, bbox.y + bbox.height),
            ))
            crop.thumbnail(
                (cell_size - 12, cell_size - label_height - 12),
                Image.Resampling.LANCZOS,
            )
            cell = Image.new("RGB", (cell_size, cell_size), "white")
            cell.paste(crop, ((cell_size - crop.width) // 2, label_height + 4))
            ImageDraw.Draw(cell).text(
                (8, 7), face.external_face_id, fill=(0, 0, 0)
            )
            board.paste(cell, ((index % columns) * cell_size, (index // columns) * cell_size))
        output = BytesIO()
        board.save(output, format="JPEG", quality=92, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(
            output.getvalue()
        ).decode("ascii")

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
        """Request bounded mask geometry; local compilation remains authoritative."""
        if not self.configured:
            raise ProviderNotConfigured("Yunwu API key and vision model are required")
        if len(faces) > 128 or len(identity_evidence) > 128:
            raise ValueError("semantic mask proposal context exceeds bounded limits")
        shape = {
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
            "Propose authorization mask geometry for this exact source image. Return arbitrary "
            "normalized polygon contours, not ellipses or bounding boxes. Coordinates must be finite "
            "and inside [0,1]. Include only visible head pixels needed by editable confirmed faces; "
            "return at least one include contour for every editable face and ensure its normalized "
            "polygon contains the exact center of that face's supplied bbox. "
            "Return no contour at all for a face whose action is keep. "
            "use exclude contours for protected attributes, occluders, and neighboring people. Never "
            "add faces, change mappings, authorize protected people or attributes, or widen ambiguous "
            "scope. Repeat exact supplied IDs and permission partitions. Return JSON only, at most 64 "
            "contours and 64 points per contour. Empty editable scope requires empty contours.\n"
            f"EXACT FACE BBOXES/CODES/AUTHORIZATION: {json.dumps(faces, ensure_ascii=False)}\n"
            f"PROTECTED PEOPLE: {json.dumps(protected_people, ensure_ascii=False)}\n"
            f"IDENTITY PROFILE/REFERENCE COVERAGE: {json.dumps(identity_evidence, ensure_ascii=False)}\n"
            f"RETURN SHAPE: {json.dumps(shape, ensure_ascii=False)}"
        )
        compiled = SemanticMaskProposal.model_validate(await self._json_completion([
            {"type": "text", "text": prompt},
            {"type": "text", "text": f"SOURCE IMAGE {source_asset_code}"},
            {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
        ]))
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
        request_digest = hashlib.sha256(
            (
                self.endpoint
                + "\n"
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode("utf-8")
        ).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": f"personaweave-v1-{request_digest}",
        }
        response: httpx.Response | None = None
        for attempt in range(2):
            response = await self._client.post(
                self.endpoint,
                headers=headers,
                json=payload,
            )
            retryable_status = response.status_code == 429 or response.status_code >= 500
            if not retryable_status or attempt == 1:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = min(30, int(retry_after)) if retry_after.isdigit() else 2
            await asyncio.sleep(delay)
        assert response is not None
        if response.status_code == 429:
            raise ProviderExecutionError(provider_error(
                "provider_rate_limited",
                upstream_status=response.status_code,
            ))
        if response.status_code >= 500:
            raise ProviderExecutionError(provider_error(
                "provider_unavailable",
                upstream_status=response.status_code,
            ))
        if response.status_code >= 400:
            raise ProviderExecutionError(provider_error(
                "provider_request_rejected",
                upstream_status=response.status_code,
            ))
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
        if len(observations) > 24:
            return await self._cluster_large_observation_set(observations, codes)
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
        response = await self._client.post(
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

    async def _cluster_large_observation_set(
        self,
        observations: list[dict[str, str]],
        codes: list[str],
    ) -> dict[str, tuple[str, float]]:
        schema_hint = {
            "duplicate_groups": [{"members": [codes[0], codes[-1]], "confidence": 0.0}],
            "uncertain_codes": [],
        }
        content: list[dict[str, object]] = [{
            "type": "text",
            "text": (
                "Find only repeated appearances of the same visible source person across this "
                "large multi-image observation set. Return duplicate_groups only when at least two "
                "codes from different Ixx source images are a high-confidence same-person match. "
                "Never put two codes with the same Ixx prefix in one group. Put ambiguous possible "
                "duplicates in uncertain_codes. Omit a code from both lists when no duplicate match "
                "was found; the application will preserve it as a separate person pending mandatory "
                "human confirmation. Never identify or name a real person. Every returned code must "
                "exactly match a supplied code and may appear at most once. Return JSON only: "
                f"{json.dumps(schema_hint, ensure_ascii=False)}"
            ),
        }]
        for observation in observations:
            content.extend([
                {"type": "text", "text": f"OBSERVATION {observation['code']}"},
                {"type": "image_url", "image_url": {
                    "url": observation["crop_uri"],
                    "detail": "high",
                }},
            ])
        clustering = LargePersonClustering.model_validate(
            await self._json_completion(content)
        )
        known = set(codes)
        duplicate_members = [
            member
            for group in clustering.duplicate_groups
            for member in group.members
        ]
        returned = [*duplicate_members, *clustering.uncertain_codes]
        if len(returned) != len(set(returned)) or not set(returned).issubset(known):
            raise ValueError("large person clustering returned duplicate or unknown observations")
        for group in clustering.duplicate_groups:
            if len(group.members) < 2:
                raise ValueError("large person duplicate groups require at least two observations")
            source_codes = {member.split("-F", 1)[0] for member in group.members}
            if len(source_codes) != len(group.members):
                raise ValueError("large person clustering cannot merge faces from the same source")

        assignments: dict[str, tuple[str, float]] = {}
        for group in clustering.duplicate_groups:
            group_key = "vlm:" + "|".join(sorted(group.members))
            for member in group.members:
                assignments[member] = (group_key, group.confidence)
        uncertain = set(clustering.uncertain_codes)
        for code in codes:
            if code not in assignments:
                confidence = 0.0 if code in uncertain else 0.5
                assignments[code] = (f"vlm:{code}", confidence)
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
        if len(observations) > 12:
            combined: dict[str, PersonAppearance] = {}
            chunks = [
                observations[start : start + 12]
                for start in range(0, len(observations), 12)
            ]
            chunk_semaphore = asyncio.Semaphore(4)

            async def analyze_chunk(
                chunk: list[dict[str, object]],
            ) -> dict[str, PersonAppearance]:
                async with chunk_semaphore:
                    return await self.analyze_person_appearances(
                        source_uri=source_uri,
                        observations=chunk,
                    )

            chunk_results = await asyncio.gather(*(
                analyze_chunk(chunk) for chunk in chunks
            ))
            for chunk_result in chunk_results:
                overlap = set(combined).intersection(chunk_result)
                if overlap:
                    raise ValueError("appearance analysis returned duplicate observations")
                combined.update(chunk_result)
            if set(combined) != {str(observation["code"]) for observation in observations}:
                raise ValueError("appearance analysis did not cover every observation exactly once")
            return combined
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
        analysis = PersonAppearanceAnalysis.model_validate(
            await self._json_completion([
                {"type": "text", "text": instructions},
                {"type": "image_url", "image_url": {"url": source_uri, "detail": "high"}},
            ])
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
            "face_instance_id": operation.face_instance_id,
            "source_person": operation.source_person_code,
            "target_identity": operation.target_identity_code,
            "bbox": operation.bbox.model_dump(),
            "source_appearance": operation.source_appearance.model_dump(),
            "additional_requirement": operation.instruction,
            "editable_attributes": (
                list(operation.permissions.editable) if operation.permissions else []
            ),
            "protected_attributes": (
                list(operation.permissions.protected) if operation.permissions else []
            ),
            "target_reference_count": len(operation.reference_uris),
            "reference_coverage_sufficient": (
                operation.source_reference_coverage.sufficient
                if operation.source_reference_coverage is not None
                else None
            ),
        } for operation in request.replacements]
        schema_hint = {
            "edit_prompt": "complete standalone generator-ready instruction",
            "observed_people": 0,
            "intended_face_edits": len(operations),
            "warnings": [],
        }
        instructions = (
            "ROLE: Source-aware planner for one mask-bounded numbered face edit. Do not identify "
            "real people.\n"
            f"IMMUTABLE USER TASK: {json.dumps(request.task_instruction, ensure_ascii=False)}\n"
            f"IMMUTABLE COMPILED SCOPE: {json.dumps(request.compiled_instruction, ensure_ascii=False)}\n"
            f"SELECTION MODE: {request.selection_mode}\n"
            f"EXACT SOURCE OPERATIONS: {json.dumps(operations, ensure_ascii=False)}\n"
            f"AUTHORIZATION MASK PRESENT: {request.authorization_mask_uri is not None}.\n"
            "Inspect the original image, then write one generator-ready edit_prompt. Follow this "
            "priority order: (1) edit only the exact numbered operations that satisfy the selection "
            "mode; (2) apply only the requested facial identity or facial attributes inside each "
            "authorized face region; (3) retain each source subject's head angle, expression, gaze, "
            "mouth state, age impression, skin lighting, sharpness, noise, occlusion and face-to-body "
            "proportions; (4) preserve every protected attribute and all unrequested people; (5) "
            "preserve exact dimensions, crop, composition, bodies, clothing, hands, background, "
            "objects, text, logos, color and photographic style. Reference images supply facial "
            "identity evidence only, never composition, pose, clothing or hairstyle. Never request a "
            "whole-image redraw, beautification, reframing, added detail or unrelated cleanup. Every "
            "round edits the immutable original, not the previous candidate. Explicit user wording wins "
            "only for the named editable trait; uncertainty goes into warnings and must not broaden "
            "scope. Keep edit_prompt concise and operational, preferably under 2400 characters; do not "
            "repeat raw JSON or add commentary. Return JSON only: "
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
        response = await self._client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        plan = ImageEditPlan.model_validate(
            _parse_json_object(body["choices"][0]["message"]["content"])
        )
        if (
            request.selection_mode != "prompt"
            and plan.intended_face_edits != len(operations)
        ):
            raise ValueError(
                "numbered edit planner changed the confirmed face-edit count"
            )
        return plan

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
        response = await self._client.post(
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
        response = await self._client.post(
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
        prompt_used = (
            request.adaptive_instruction.strip()
            or request.compiled_instruction.strip()
        )
        instructions = (
            "ROLE: Strict visual reviewer and next-prompt author for one mask-bounded numbered "
            "face edit. Do not identify real people.\n"
            f"IMAGE 1: immutable original @{request.source_image_code or 'source'}. "
            "IMAGE 2: current source-composited result. Pixels outside the authorization mask have "
            "already been restored from the original. "
            + (
                "LATER IMAGES: target identity references in mapping order; use them only for face identity.\n"
                if has_target_references
                else "No target identity reference was supplied; judge the requested appearance from text.\n"
            )
            + f"IMMUTABLE TASK: {json.dumps(request.task_instruction, ensure_ascii=False)}\n"
            + f"CONFIRMED MAPPINGS: {json.dumps(mappings, ensure_ascii=False)}\n"
            + f"SOURCE CONTEXTS: {json.dumps(source_appearances, ensure_ascii=False)}\n"
            + f"PER-OPERATION REQUIREMENTS: {json.dumps(operation_requirements, ensure_ascii=False)}\n"
            + f"SELECTION MODE: {request.selection_mode}. In prompt mode, listed people are candidates; "
            + "only a visible match to the immutable task is an intended edit.\n"
            + f"ACTUAL PROMPT USED THIS ROUND: {json.dumps(prompt_used, ensure_ascii=False)}\n"
            + f"ITERATION: {iteration} of {request.max_iterations}.\n"
            + "First verify exact target count and mapping. Then inspect only visible evidence: target "
            + "identity or requested traits, natural facial geometry, source expression/pose/gaze, "
            + "hairline/ears/jaw/neck boundaries, lighting, sharpness, noise, depth and occlusion. Also "
            + "check protected faces, bodies, clothing, background, objects, framing, text, logos and "
            + "global color. Do not penalize intended facial identity changes or harmless encoding-level "
            + "differences. All six scores are integer 0-100. passed=true only when every fixed threshold "
            + "is met and there is no critical issue. List only concrete visible issues that a new edit "
            + "of the original could correct; avoid vague advice and duplicate issues. If failed and a "
            + "round remains, revised_edit_prompt is mandatory and must be a concise, complete, standalone "
            + "instruction for editing the immutable original. Preserve exact mappings and successful "
            + "constraints, then add corrections for every major or critical issue. Never reference or "
            + "edit the failed candidate. On pass or final iteration return null. Return JSON only: "
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
        response = await self._client.post(
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
        seen_references: set[str] = set()
        for subject in request.expected_subjects:
            for reference_uri in subject.reference_uris:
                if reference_uri in seen_references:
                    continue
                seen_references.add(reference_uri)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": reference_uri},
                })
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
