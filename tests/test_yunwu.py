import json

import pytest
from pydantic import ValidationError

from personaweave.domain import (
    BoundingBox,
    ReferenceSufficiency,
    SourceOccurrenceEvidence,
    TargetIdentityProfile,
    ValidationPeer,
    ValidationRequest,
)

from personaweave.providers.yunwu import (
    ImageEditIssue,
    ImageEditPlan,
    ImageEditReview,
    YunwuMultimodalProvider,
    enforce_image_edit_review,
    image_edit_review_passes,
    _VisualValidationPayload,
    _parse_json_object,
)


def test_json_parser_accepts_fenced_json_with_gateway_suffix() -> None:
    assert _parse_json_object(
        '```json\n{"groups": []}\n``` trailing gateway text'
    ) == {"groups": []}


def test_json_parser_rejects_incomplete_object() -> None:
    with pytest.raises(ValueError, match="complete JSON object"):
        _parse_json_object('{"groups": [')


@pytest.mark.anyio
async def test_visual_provider_reuses_injected_http_client() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return __import__("httpx").Response(
            200,
            json={"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    httpx = __import__("httpx")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        client=client,
    )
    try:
        assert await provider._json_completion([]) == {"ok": True}
        assert await provider._json_completion([]) == {"ok": True}
        await provider.aclose()

        assert len(requests) == 2
        assert provider._client is client
        assert not client.is_closed
    finally:
        await client.aclose()


def test_visual_provider_uses_explicit_endpoint_configuration() -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.ai/v1",
        "test-key",
        "future-vision-model",
        api_mode="chat_completions",
        api_path="/custom/chat/completions",
    )

    assert provider.endpoint == "https://yunwu.ai/v1/custom/chat/completions"
    assert provider.model == "future-vision-model"


def test_visual_provider_accepts_an_absolute_endpoint() -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.ai/v1",
        "test-key",
        "future-vision-model",
        api_path="https://gateway.example.test/chat",
    )

    assert provider.endpoint == "https://gateway.example.test/chat"


@pytest.mark.anyio
async def test_large_person_clustering_preserves_unmentioned_faces_as_singletons(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    observations = [
        {"code": f"I{index:02d}-F01", "crop_uri": f"data:image/png;base64,{index}"}
        for index in range(1, 26)
    ]

    async def fake_completion(_content):
        return {
            "duplicate_groups": [{
                "members": ["I01-F01", "I25-F01"],
                "confidence": 0.92,
            }],
            "uncertain_codes": ["I02-F01"],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    assignments = await provider.cluster_person_observations(observations)

    assert set(assignments) == {item["code"] for item in observations}
    assert assignments["I01-F01"] == assignments["I25-F01"]
    assert assignments["I02-F01"] == ("vlm:I02-F01", 0.0)
    assert assignments["I03-F01"] == ("vlm:I03-F01", 0.5)


@pytest.mark.anyio
async def test_large_appearance_analysis_is_strictly_chunked(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    observations = [
        {"code": f"I01-F{index:02d}", "bbox": {"x": index, "y": 0, "width": 1, "height": 1}}
        for index in range(1, 26)
    ]
    chunk_sizes = []

    async def fake_completion(content):
        text = content[0]["text"]
        encoded = text.split("OBSERVATIONS: ", 1)[1].split("\nReturn JSON", 1)[0]
        chunk = json.loads(encoded)
        chunk_sizes.append(len(chunk))
        appearance = {
            "apparent_age": "adult",
            "face_features": "neutral",
            "hair": "unclear",
            "eyewear": "none",
            "accessories": "none",
            "body": "unclear",
            "clothing": "unclear",
            "pose": "front",
            "lighting_occlusion": "even",
        }
        return {
            "people": [
                {"code": item["code"], "appearance": appearance}
                for item in chunk
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    result = await provider.analyze_person_appearances(
        source_uri="data:image/png;base64,source",
        observations=observations,
    )

    assert chunk_sizes == [12, 12, 1]
    assert set(result) == {item["code"] for item in observations}


def test_visual_payload_accepts_exact_boolean_strings() -> None:
    payload = _VisualValidationPayload.model_validate(
        {
            "passed": "false",
            "checks": {
                "requested_edits_complete": "true",
                "unrequested_content_preserved": "false",
                "target_identity_matches_references": True,
                "cross_image_identity_consistent": True,
                "face_quality_acceptable": False,
                "review_confidence": 0.8,
            },
            "reasons": ["review required"],
        }
    )

    assert payload.passed is False
    assert payload.checks.requested_edits_complete is True


def test_visual_payload_rejects_ambiguous_boolean_values() -> None:
    with pytest.raises(ValidationError):
        _VisualValidationPayload.model_validate(
            {
                "passed": "yes",
                "checks": {
                    "requested_edits_complete": True,
                    "unrequested_content_preserved": True,
                    "target_identity_matches_references": True,
                    "cross_image_identity_consistent": True,
                    "face_quality_acceptable": True,
                    "review_confidence": 0.8,
                },
            }
        )


def test_image_edit_plan_requires_bounded_structured_counts() -> None:
    plan = ImageEditPlan.model_validate(
        {
            "edit_prompt": "Change only the requested facial identity and preserve the source photo.",
            "observed_people": 2,
            "intended_face_edits": 2,
            "warnings": [],
        }
    )

    assert plan.observed_people == 2
    assert plan.intended_face_edits == 2


def test_image_edit_review_requires_complete_scorecard() -> None:
    review = ImageEditReview.model_validate(
        {
            "passed": True,
            "scores": {
                "instruction_fulfillment": 95,
                "target_identity_consistency": 94,
                "face_naturalness": 90,
                "pose_expression_preservation": 92,
                "source_content_preservation": 98,
                "text_logo_preservation": 100,
            },
            "issues": [],
            "summary": "通过",
            "revised_edit_prompt": None,
        }
    )

    assert review.passed is True
    assert review.scores.text_logo_preservation == 100


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        (
            {
                "instruction_fulfillment": 0.92,
                "target_identity_consistency": 0.84,
                "face_naturalness": 0.88,
                "pose_expression_preservation": 0.94,
                "source_content_preservation": 0.98,
                "text_logo_preservation": 0.99,
            },
            (92, 84, 88, 94, 98, 99),
        ),
        (
            {
                "instruction_fulfillment": 9,
                "target_identity_consistency": 9,
                "face_naturalness": 9,
                "pose_expression_preservation": 9,
                "source_content_preservation": 9,
                "text_logo_preservation": 10,
            },
            (90, 90, 90, 90, 90, 100),
        ),
    ],
)
def test_image_edit_review_normalizes_common_score_scales(
    scores: dict,
    expected: tuple[int, ...],
) -> None:
    review = ImageEditReview.model_validate(
        {
            "passed": True,
            "scores": scores,
            "issues": [],
            "summary": "review",
            "revised_edit_prompt": None,
        }
    )

    assert tuple(review.scores.model_dump().values()) == expected


def test_image_edit_review_normalizes_terse_string_issues() -> None:
    review = ImageEditReview.model_validate(
        {
            "passed": False,
            "scores": {
                "instruction_fulfillment": 40,
                "target_identity_consistency": 30,
                "face_naturalness": 70,
                "pose_expression_preservation": 50,
                "source_content_preservation": 20,
                "text_logo_preservation": 60,
            },
            "issues": ["画布尺寸和整体构图发生变化"],
            "summary": "未通过",
            "revised_edit_prompt": "Edit only the requested face and retain the exact original canvas.",
        }
    )

    assert review.issues[0].severity == "major"
    assert review.issues[0].evidence == "画布尺寸和整体构图发生变化"
    assert review.issues[0].correction


def test_image_edit_review_cannot_pass_below_fixed_thresholds() -> None:
    review = ImageEditReview.model_validate(
        {
            "passed": True,
            "scores": {
                "instruction_fulfillment": 95,
                "target_identity_consistency": 94,
                "face_naturalness": 90,
                "pose_expression_preservation": 92,
                "source_content_preservation": 89,
                "text_logo_preservation": 100,
            },
            "issues": [],
            "summary": "模型自称通过，但原图保持度低于固定门槛",
            "revised_edit_prompt": None,
        }
    )

    assert image_edit_review_passes(review) is False
    enforced = enforce_image_edit_review(review)
    assert enforced.passed is False
    assert enforced.summary.startswith("固定阈值结论：未通过")


def test_image_edit_review_cannot_pass_with_critical_issue() -> None:
    review = ImageEditReview.model_validate(
        {
            "passed": True,
            "scores": {
                "instruction_fulfillment": 95,
                "target_identity_consistency": 94,
                "face_naturalness": 90,
                "pose_expression_preservation": 92,
                "source_content_preservation": 98,
                "text_logo_preservation": 100,
            },
            "issues": [
                ImageEditIssue(
                    category="instruction",
                    severity="critical",
                    evidence="one requested face was not edited",
                    correction="edit every requested face",
                ).model_dump()
            ],
            "summary": "存在关键问题",
            "revised_edit_prompt": "Edit every requested face and preserve all source content.",
        }
    )

    assert image_edit_review_passes(review) is False

@pytest.mark.anyio
async def test_target_compiler_allows_missing_references_with_manual_review() -> None:
    provider = YunwuMultimodalProvider("https://yunwu.example/v1", None, None)

    profile, assessment = await provider.compile_target_identity(
        identity_id="identity-1",
        identity_code="T01",
        references=[],
    )

    assert profile.identity_code == "T01"
    assert assessment.sufficient is False
    assert assessment.requires_manual_review is True


@pytest.mark.anyio
async def test_source_coverage_compiler_persists_pose_gap_per_occurrence(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    captured = {}

    async def fake_completion(content):
        captured["content"] = content
        return {
            "occurrences": [{
                "face_instance_id": "face-left",
                "reference_views_used": ["R01 frontal"],
                "missing_coverage": ["matching left profile"],
                "sufficient": False,
                "requires_manual_review": True,
                "summary": "The left-profile source pose is absent from R01.",
            }],
            "summary": "The selected references do not cover the source pose.",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    coverage = await provider.compile_source_reference_coverage(
        source_asset_id="source-1",
        source_asset_code="I01",
        source_uri="https://assets.example/source.jpg",
        person_id="person-1",
        person_code="P01",
        occurrences=[
            SourceOccurrenceEvidence(
                face_instance_id="face-left",
                bbox=BoundingBox(x=2, y=3, width=80, height=90),
                face_area_pixels=7200,
                pose="left profile",
                lighting_occlusion_blur="clear light, no occlusion or blur",
            )
        ],
        target_profile=TargetIdentityProfile(
            identity_id="identity-1",
            identity_code="T01",
        ),
        reference_assessment=ReferenceSufficiency(
            identity_id="identity-1",
            identity_code="T01",
            reference_count=1,
            visible_views=["frontal"],
            sufficient=True,
            requires_manual_review=False,
        ),
        references=[
            {"code": "R01", "uri": "https://assets.example/frontal.jpg"}
        ],
    )

    assert coverage.sufficient is False
    assert coverage.occurrence_assessments[0].missing_coverage == [
        "matching left profile"
    ]
    image_urls = [
        item["image_url"]["url"]
        for item in captured["content"]
        if item["type"] == "image_url"
    ]
    assert image_urls == [
        "https://assets.example/source.jpg",
        "https://assets.example/frontal.jpg",
    ]



@pytest.mark.anyio
async def test_final_review_fails_when_cross_image_identity_is_inconsistent(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "review-model-a",
    )
    captured = {}

    async def fake_completion(content):
        captured["content"] = content
        return {
            "passed": True,
            "checks": {
                "requested_edits_complete": True,
                "unrequested_content_preserved": True,
                "target_identity_matches_references": True,
                "cross_image_identity_consistent": False,
                "face_quality_acceptable": True,
                "review_confidence": 0.99,
            },
            "reasons": ["@T01 identity drifts from peer result"],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    result = await provider.validate(
        ValidationRequest(
            batch_id="batch-1",
            result_asset_id="result-1",
            source_uri="https://assets.example/I01.jpg",
            result_uri="https://assets.example/O01.jpg",
            expected_face_instance_ids=["face-1"],
            protected_face_instance_ids=[],
            source_image_code="I01",
            peer_results=[
                ValidationPeer(
                    result_asset_id="result-2",
                    result_uri="https://assets.example/O02.jpg",
                    source_image_code="I02",
                    target_identity_codes=["T01"],
                )
            ],
        )
    )

    image_urls = [
        item["image_url"]["url"]
        for item in captured["content"]
        if item["type"] == "image_url"
    ]
    assert result.passed is False
    assert result.provider == "yunwu:review-model-a"
    assert result.checks["cross_image_identity_consistent"] is False
    assert image_urls == [
        "https://assets.example/I01.jpg",
        "https://assets.example/O01.jpg",
        "https://assets.example/O02.jpg",
    ]

@pytest.mark.anyio
async def test_identity_permissions_include_identity_bearing_face_geometry(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )

    async def fake_completion(_content):
        return {
            "operations": [{
                "person_code": "P01",
                "permissions": {
                    "editable": ["face_identity"],
                    "protected": [
                        "apparent_age",
                        "face_features",
                        "hair",
                        "eyewear",
                        "accessories",
                        "makeup",
                    ],
                },
            }]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)

    permissions = await provider.compile_attribute_permissions(
        task_instruction="replace @P01",
        operations=[
            {
                "person_code": "P01",
                "action": "replace",
                "target_identity_code": "T01",
            }
        ],
    )

    assert permissions["P01"].editable == ["face_identity", "face_features"]
    assert "face_features" not in permissions["P01"].protected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "permissions",
    [
        {
            "editable": ["face_identity", "voice"],
            "protected": [
                "apparent_age",
                "face_features",
                "hair",
                "eyewear",
                "accessories",
                "makeup",
            ],
        },
        {
            "editable": ["face_identity"],
            "protected": [
                "apparent_age",
                "face_features",
                "hair",
                "eyewear",
                "accessories",
            ],
        },
    ],
)
async def test_permission_compiler_rejects_unknown_or_incomplete_attributes(
    monkeypatch,
    permissions: dict,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )

    async def fake_completion(_content):
        return {
            "operations": [
                {"person_code": "P01", "permissions": permissions}
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)

    with pytest.raises(ValidationError):
        await provider.compile_attribute_permissions(
            task_instruction="replace @P01",
            operations=[
                {
                    "person_code": "P01",
                    "action": "replace",
                    "target_identity_code": "T01",
                }
            ],
        )
