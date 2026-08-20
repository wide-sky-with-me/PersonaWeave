import base64
import asyncio
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from personaweave.domain import (
    AnalysisResult,
    BoundingBox,
    FaceObservation,
    PersonDetectionAudit,
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


@pytest.mark.anyio
async def test_json_completion_retries_5xx_with_stable_idempotency_key(
    monkeypatch,
) -> None:
    requests = []
    httpx = __import__("httpx")

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("personaweave.providers.yunwu.asyncio.sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        client=client,
    )
    try:
        assert await provider._json_completion([]) == {"ok": True}
        assert len(requests) == 2
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers[
            "Idempotency-Key"
        ]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_json_completion_retries_a_transport_disconnect(
    monkeypatch,
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError(
                "server disconnected",
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("personaweave.providers.yunwu.asyncio.sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        client=client,
    )
    try:
        assert await provider._json_completion([]) == {"ok": True}
        assert len(requests) == 2
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers[
            "Idempotency-Key"
        ]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_json_completion_enforces_a_wall_clock_timeout() -> None:
    class HangingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    client = httpx.AsyncClient(transport=HangingTransport(), timeout=None)
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        timeout=0.01,
        client=client,
    )
    try:
        with pytest.raises(httpx.ReadTimeout, match="wall-clock limit"):
            await provider._json_completion([])
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_batch_refinement_runs_five_sources_concurrently(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        timeout=1,
    )
    active = 0
    maximum_active = 0
    all_started = asyncio.Event()
    release = asyncio.Event()
    completed = []

    async def fake_refine(source, result, *, broad_search=False):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 5:
            all_started.set()
        await release.wait()
        active -= 1
        return result

    monkeypatch.setattr(provider, "_refine_source_face_boxes", fake_refine)
    sources = [{"asset_id": f"asset-{index}"} for index in range(5)]
    results = [
        AnalysisResult(asset_id=source["asset_id"], faces=[], provider="test")
        for source in sources
    ]
    task = asyncio.create_task(provider.refine_batch_face_boxes(
        sources,
        results,
        progress_callback=completed.append,
    ))
    await asyncio.wait_for(all_started.wait(), timeout=0.5)
    release.set()
    refined = await task

    assert maximum_active == 5
    assert {result.asset_id for result in refined} == {
        source["asset_id"] for source in sources
    }
    assert set(completed) == {source["asset_id"] for source in sources}


@pytest.mark.anyio
async def test_batch_refinement_enforces_per_image_wall_clock_timeout(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
        timeout=0.01,
    )

    async def fake_refine(source, result, *, broad_search=False):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(provider, "_refine_source_face_boxes", fake_refine)
    sources = [{"asset_id": "asset-1"}]
    results = [AnalysisResult(asset_id="asset-1", faces=[], provider="test")]

    with pytest.raises(httpx.ReadTimeout, match="per-image.*wall-clock limit"):
        await provider.refine_batch_face_boxes(sources, results)


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
async def test_batch_person_analysis_groups_sources_and_uses_original_detail(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    captured = {}
    appearance = {
        "apparent_age": "adult",
        "face_features": "oval face",
        "hair": "dark hair",
        "eyewear": "none",
        "accessories": "none",
        "body": "medium build",
        "clothing": "dark top",
        "pose": "front-facing",
        "lighting_occlusion": "even light",
    }

    async def fake_completion(content):
        captured["content"] = content
        return {"sources": [
            {
                "source_code": code,
                "complete": True,
                "visible_person_count": 1,
                "people": [{
                    "occurrence_code": f"{code}-V01",
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                    "detection_confidence": 0.96,
                    "person_group_key": "G01",
                    "identity_confidence": 0.93,
                    "grouping_uncertain": False,
                    "appearance": appearance,
                }],
                "summary": "complete",
            }
            for code in ("I01", "I02")
        ]}

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    results = await provider.analyze_batch_people([
        {
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": "data:image/jpeg;base64,one",
            "analysis_image_uri": "data:image/jpeg;base64,preview-one",
            "width": 100,
            "height": 200,
        },
        {
            "asset_id": "asset-2",
            "source_code": "I02",
            "image_uri": "data:image/jpeg;base64,two",
            "width": 200,
            "height": 100,
        },
    ])

    image_items = [item for item in captured["content"] if item["type"] == "image_url"]
    assert [item["image_url"]["detail"] for item in image_items] == ["original", "original"]
    assert [item["image_url"]["url"] for item in image_items] == [
        "data:image/jpeg;base64,preview-one",
        "data:image/jpeg;base64,two",
    ]
    assert [result.asset_id for result in results] == ["asset-1", "asset-2"]
    assert results[0].faces[0].suggested_person_key == "vlm:G01"
    assert results[1].faces[0].suggested_person_key == "vlm:G01"
    assert results[0].faces[0].bbox.model_dump() == {
        "x": 10, "y": 40, "width": 30, "height": 80
    }
    assert all(result.faces[0].needs_confirmation for result in results)


@pytest.mark.anyio
async def test_batch_person_analysis_ignores_auxiliary_model_fields(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )

    async def fake_completion(_content):
        return {
            "recap": "auxiliary top-level prose",
            "sources": [{
                "source_code": "I01",
                "complete": " TRUE ",
                "visible_person_count": 1,
                "summary": "complete",
                "analysis": "auxiliary source prose",
                "people": [{
                    "occurrence_code": "I01-V01",
                    "bbox": {
                        "x": 0.1,
                        "y": 0.2,
                        "width": 0.3,
                        "height": 0.4,
                        "commentary": "auxiliary bbox prose",
                    },
                    "detection_confidence": 0.96,
                    "person_group_key": "G01",
                    "identity_confidence": 0.93,
                    "grouping_uncertain": "FALSE",
                    "appearance": {
                        "hair": "dark hair",
                        "commentary": "auxiliary appearance prose",
                    },
                    "explanation": "auxiliary occurrence prose",
                }],
            }],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    result = (await provider.analyze_batch_people([{
        "asset_id": "asset-1",
        "source_code": "I01",
        "image_uri": "data:image/jpeg;base64,one",
        "width": 100,
        "height": 200,
    }]))[0]

    assert result.faces[0].external_face_id == "I01-V01"
    assert result.faces[0].bbox == BoundingBox(x=10, y=40, width=30, height=80)
    assert result.faces[0].appearance.hair == "dark hair"


@pytest.mark.anyio
async def test_batch_person_analysis_keeps_person_count_strict(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )

    async def fake_completion(_content):
        return {
            "sources": [{
                "source_code": "I01",
                "complete": True,
                "visible_person_count": 1,
                "people": [],
                "summary": "incorrect count",
            }]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    with pytest.raises(ValidationError, match="visible_person_count"):
        await provider.analyze_batch_people([{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": "data:image/jpeg;base64,one",
            "width": 100,
            "height": 100,
        }])


@pytest.mark.anyio
async def test_batch_person_analysis_isolates_uncertain_grouping(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )

    async def fake_completion(_content):
        return {"sources": [{
            "source_code": "I01",
            "complete": True,
            "visible_person_count": 1,
            "people": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                "detection_confidence": 0.8,
                "person_group_key": "G01",
                "identity_confidence": 0.79,
                "grouping_uncertain": False,
                "appearance": {},
            }],
            "summary": "uncertain match",
        }]}

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    result = (await provider.analyze_batch_people([{
        "asset_id": "asset-1",
        "source_code": "I01",
        "image_uri": "data:image/jpeg;base64,one",
        "width": 100,
        "height": 100,
    }]))[0]

    assert result.faces[0].suggested_person_key == "vlm-uncertain:I01:I01-V01"
    assert result.detection_audit.requires_manual_review is True


def test_face_crop_review_board_uses_the_exact_bbox_without_padding(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    crop_boxes = []
    original_crop = Image.Image.crop

    def recording_crop(image, box=None):
        crop_boxes.append(box)
        return original_crop(image, box)

    monkeypatch.setattr(Image.Image, "crop", recording_crop)
    provider._face_crop_review_board(
        image_uri,
        [FaceObservation(
            external_face_id="I01-V01",
            bbox=BoundingBox(x=20, y=30, width=40, height=50),
            detection_confidence=0.99,
        )],
    )

    assert crop_boxes == [(20, 30, 60, 80)]


def test_low_confidence_anchor_uses_broader_localization_context() -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (1000, 800), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    bbox = BoundingBox(x=400, y=300, width=20, height=20)

    _, reliable_tiles = provider._localization_tile_images(
        image_uri,
        [FaceObservation(
            external_face_id="I01-V01",
            bbox=bbox,
            detection_confidence=0.95,
        )],
        expansion=3.0,
    )
    _, unreliable_tiles = provider._localization_tile_images(
        image_uri,
        [FaceObservation(
            external_face_id="I01-V01",
            bbox=bbox,
            detection_confidence=0.2,
        )],
        expansion=3.0,
    )

    assert reliable_tiles["I01-V01"].width == 96
    assert unreliable_tiles["I01-V01"].width == 200


def test_anchor_reliability_is_independent_of_manual_broad_search() -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    reliable = FaceObservation(
        external_face_id="I01-V01",
        bbox=BoundingBox(x=100, y=100, width=120, height=140),
        detection_confidence=0.95,
    )
    unreliable = reliable.model_copy(update={"detection_confidence": 0.2})

    assert provider._face_anchor_is_unreliable(
        reliable, image_width=4000, image_height=6000
    ) is False
    assert provider._face_anchor_is_unreliable(
        unreliable, image_width=4000, image_height=6000
    ) is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bottom edge clipped", "bottom_clipped"),
        ("CLIPPED-LEFT", "left_clipped"),
        ("no issue", "none"),
        ("multiple edges", "multiple_clipped"),
    ],
)
def test_boundary_issue_aliases_are_normalized(raw: str, expected: str) -> None:
    assert YunwuMultimodalProvider._normalize_boundary_issue(raw) == (
        expected,
        False,
    )


@pytest.mark.anyio
async def test_unknown_boundary_issue_fails_closed_for_crop_coverage(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()

    async def fake_completion(_content):
        return {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "crop_contains_all_source_visible_face": True,
                "matches_expected_occurrence": True,
                "source_face_condition": "complete",
                "face_centered": True,
                "tight_face_crop": True,
                "boundary_issue": "possibly clipped somewhere",
                "unrecoverable_occlusion": False,
                "reason": "unclear boundary description",
            }],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    review = await provider._review_source_face_crops(
        {
            "source_code": "I01",
            "image_uri": image_uri,
            "analysis_image_uri": image_uri,
        },
        [FaceObservation(
            external_face_id="I01-V01",
            bbox=BoundingBox(x=20, y=20, width=30, height=30),
            detection_confidence=0.9,
        )],
    )

    assert review.items[0].boundary_issue == "multiple_clipped"
    assert review.items[0].crop_contains_all_source_visible_face is False


@pytest.mark.anyio
async def test_manual_broad_search_uses_ten_times_context_even_for_reliable_anchor(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (1000, 800), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "recap": "extra model commentary must be ignored",
            "analysis": "not part of the response contract",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {
                    "x": 300,
                    "y": 300,
                    "width": 300,
                    "height": 350,
                    "commentary": "ignore nested bbox prose",
                },
                "confidence": 0.9,
                "explanation": "ignore nested location prose",
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "matches_expected_occurrence": True,
                "face_centered": True,
                "tight_face_crop": True,
                "reason": "pass",
                "explanation": "ignore nested review prose",
            }],
            "recap": "ignore top-level review prose",
        },
    ])
    expansions = []
    original_tile_images = provider._localization_tile_images

    def recording_tile_images(image_uri, faces, *, expansion):
        expansions.append(expansion)
        return original_tile_images(image_uri, faces, expansion=expansion)

    async def fake_completion(_content):
        return next(responses)

    monkeypatch.setattr(provider, "_localization_tile_images", recording_tile_images)
    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 1000,
            "height": 800,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=400, y=300, width=20, height=20),
                detection_confidence=0.95,
            )],
        )],
        broad_search=True,
    )

    assert expansions == [10.0]


@pytest.mark.anyio
async def test_precise_localization_repairs_a_failed_crop_once(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 100, "y": 400, "width": 300, "height": 400},
                "confidence": 0.91,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": False,
                "face_centered": False,
                "tight_face_crop": False,
                "reason": "crop shows torso instead of the face",
            }],
        },
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 200, "y": 200, "width": 200, "height": 200},
                "confidence": 0.88,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "matches_expected_occurrence": True,
                "face_centered": True,
                "tight_face_crop": True,
                "reason": "face is tightly centered",
            }],
        },
    ])
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "grid_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=10, y=40, width=30, height=40),
                detection_confidence=0.99,
                suggested_person_key="vlm:G01",
                identity_confidence=0.98,
            )],
            detection_audit=PersonDetectionAudit(
                complete=True,
                detected_face_count=1,
                visible_face_count=1,
            ),
        )],
    )

    face = refined[0].faces[0]
    assert face.bbox == BoundingBox(x=20, y=20, width=20, height=20)
    assert face.detection_confidence == 0.88
    assert refined[0].detection_audit.requires_manual_review is False
    assert "复核通过" in refined[0].detection_audit.summary
    assert len(calls) == 4
    assert "crop shows torso instead of the face" in calls[2][0]["text"]
    assert [attempt.accepted for attempt in refined[0].localization_attempts] == [
        False,
        True,
    ]
    assert refined[0].localization_attempts[0].bbox != face.bbox


@pytest.mark.anyio
async def test_crop_geometry_does_not_pass_when_candidate_is_wrong_person(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 100, "y": 100, "width": 300, "height": 400},
                "confidence": 0.94,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "crop_contains_all_source_visible_face": True,
                "matches_expected_occurrence": False,
                "face_centered": True,
                "tight_face_crop": True,
                "reason": "crop is a different person with glasses",
            }],
        },
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 500, "y": 200, "width": 250, "height": 350},
                "confidence": 0.91,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "crop_contains_all_source_visible_face": True,
                "matches_expected_occurrence": True,
                "face_centered": True,
                "tight_face_crop": True,
                "reason": "crop matches the expected person",
            }],
        },
    ])
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
            "expected_person_appearances": [{"eyewear": "none"}],
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=10, y=10, width=30, height=40),
                detection_confidence=0.95,
                appearance={"eyewear": "none"},
            )],
        )],
    )

    attempts = refined[0].localization_attempts
    assert [attempt.matches_expected_occurrence for attempt in attempts] == [False, True]
    assert [attempt.accepted for attempt in attempts] == [False, True]
    assert "EXPECTED_APPEARANCES" in calls[1][0]["text"]
    assert "different person with glasses" in calls[2][0]["text"]


@pytest.mark.anyio
async def test_second_localization_freezes_faces_that_already_passed(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (200, 200), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "locations": [
                {"occurrence_code": "I01-V01", "bbox": {"x": 300, "y": 300, "width": 250, "height": 250}, "confidence": 0.95},
                {"occurrence_code": "I01-V02", "bbox": {"x": 250, "y": 350, "width": 300, "height": 350}, "confidence": 0.9},
            ],
        },
        {
            "source_code": "I01",
            "items": [
                {"occurrence_code": "I01-V01", "full_face_visible": True, "matches_expected_occurrence": True, "face_centered": True, "tight_face_crop": True, "reason": "pass"},
                {"occurrence_code": "I01-V02", "full_face_visible": True, "matches_expected_occurrence": False, "face_centered": False, "tight_face_crop": False, "reason": "too much torso"},
            ],
        },
        {
            "source_code": "I01",
            "locations": [
                {"occurrence_code": "I01-V02", "bbox": {"x": 300, "y": 250, "width": 250, "height": 250}, "confidence": 0.88},
            ],
        },
        {
            "source_code": "I01",
            "items": [
                {"occurrence_code": "I01-V02", "full_face_visible": True, "matches_expected_occurrence": True, "face_centered": True, "tight_face_crop": True, "reason": "pass"},
            ],
        },
    ])
    calls = []
    tile_inputs = []
    original_tile_images = provider._localization_tile_images

    def recording_tile_images(image_uri, faces, *, expansion):
        tile_inputs.append((
            {face.external_face_id: face.bbox for face in faces},
            expansion,
        ))
        return original_tile_images(image_uri, faces, expansion=expansion)

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_localization_tile_images", recording_tile_images)
    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{"asset_id": "asset-1", "source_code": "I01", "image_uri": image_uri, "width": 200, "height": 200}],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[
                FaceObservation(external_face_id="I01-V01", bbox=BoundingBox(x=20, y=20, width=30, height=30), detection_confidence=0.99),
                FaceObservation(external_face_id="I01-V02", bbox=BoundingBox(x=120, y=120, width=30, height=30), detection_confidence=0.99),
            ],
        )],
        broad_search=True,
    ))[0]

    attempts_by_code = {
        code: [attempt for attempt in refined.localization_attempts if attempt.occurrence_code == code]
        for code in ("I01-V01", "I01-V02")
    }
    assert len(attempts_by_code["I01-V01"]) == 1
    assert attempts_by_code["I01-V01"][0].accepted is True
    assert len(attempts_by_code["I01-V02"]) == 2
    assert len([item for item in calls[0] if item["type"] == "image_url"]) == 2
    assert "I01-V01" not in calls[2][0]["text"]
    assert "I01-V02" in calls[2][0]["text"]
    assert tile_inputs[0][1] == 10.0
    assert tile_inputs[1] == ({
        "I01-V02": BoundingBox(x=120, y=120, width=30, height=30),
    }, 10.0)


@pytest.mark.anyio
async def test_degenerate_low_confidence_location_keeps_reviewable_coarse_box(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 450, "y": 500, "width": 100, "height": 2},
                "confidence": 0.02,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "matches_expected_occurrence": True,
                "face_centered": True,
                "tight_face_crop": True,
                "reason": "coarse box still contains the complete face",
            }],
        },
    ])

    async def fake_completion(_content):
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=10, y=10, width=30, height=30),
                detection_confidence=0.94,
            )],
        )],
    ))[0]

    assert refined.faces[0].bbox == BoundingBox(x=10, y=10, width=30, height=30)
    assert refined.faces[0].detection_confidence == 0.94
    assert refined.localization_attempts[0].confidence == 0.02
    assert refined.localization_attempts[0].accepted is True


@pytest.mark.anyio
async def test_precise_localization_fails_closed_after_second_crop_rejection(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    location = {
        "source_code": "I01",
        "locations": [{
            "occurrence_code": "I01-V01",
            "bbox": {"x": 100, "y": 400, "width": 300, "height": 400},
            "confidence": 0.9,
        }],
    }
    rejected = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": False,
            "face_centered": False,
            "tight_face_crop": False,
            "reason": "crop still shows clothing instead of a centered face",
        }],
    }
    responses = iter([location, rejected, location, rejected])

    async def fake_completion(_content):
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    coarse_bbox = BoundingBox(x=10, y=40, width=30, height=40)
    refined = await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "grid_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=coarse_bbox,
                detection_confidence=0.99,
            )],
        )],
    )

    assert refined[0].faces[0].bbox == coarse_bbox
    assert all(
        not attempt.accepted for attempt in refined[0].localization_attempts
    )
    assert refined[0].detection_audit.requires_manual_review is True
    assert "复核未通过" in refined[0].detection_audit.summary


@pytest.mark.anyio
async def test_boundary_clipping_gets_one_directional_repair_and_final_review(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    responses = iter([
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 200, "y": 200, "width": 400, "height": 400},
                "confidence": 0.92,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": False,
                "face_centered": True,
                "tight_face_crop": False,
                "reason": "chin is clipped",
            }],
        },
        {
            "source_code": "I01",
            "locations": [{
                "occurrence_code": "I01-V01",
                "bbox": {"x": 250, "y": 250, "width": 400, "height": 350},
                "confidence": 0.89,
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": False,
                "face_centered": True,
                "tight_face_crop": False,
                "boundary_issue": "bottom",
                "unrecoverable_occlusion": False,
                "reason": "chin is still clipped",
            }],
        },
        {
            "source_code": "I01",
            "items": [{
                "occurrence_code": "I01-V01",
                "full_face_visible": True,
                "matches_expected_occurrence": True,
                "face_centered": True,
                "tight_face_crop": True,
                "boundary_issue": "none",
                "unrecoverable_occlusion": False,
                "reason": "complete face after downward expansion",
            }],
        },
    ])
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=20, y=20, width=40, height=40),
                detection_confidence=0.99,
            )],
        )],
    ))[0]

    assert len(calls) == 5
    assert [attempt.attempt for attempt in refined.localization_attempts] == [1, 2, 3]
    assert [attempt.accepted for attempt in refined.localization_attempts] == [
        False,
        False,
        True,
    ]
    second_box = refined.localization_attempts[1].bbox
    final_box = refined.localization_attempts[2].bbox
    assert refined.localization_attempts[1].boundary_issue == "bottom_clipped"
    assert final_box.x == second_box.x
    assert final_box.y == second_box.y
    assert final_box.width == second_box.width
    assert final_box.height > second_box.height
    assert refined.detection_audit.requires_manual_review is False


@pytest.mark.anyio
async def test_boundary_clipping_allows_one_more_bounded_repair(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    location = {
        "source_code": "I01",
        "locations": [{
            "occurrence_code": "I01-V01",
            "bbox": {"x": 250, "y": 100, "width": 300, "height": 300},
            "confidence": 0.9,
        }],
    }
    rejected = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": False,
            "face_centered": True,
            "tight_face_crop": False,
            "boundary_issue": "bottom_clipped",
            "unrecoverable_occlusion": False,
            "reason": "lower face remains clipped",
        }],
    }
    accepted = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": True,
            "matches_expected_occurrence": True,
            "face_centered": True,
            "tight_face_crop": True,
            "boundary_issue": "none",
            "unrecoverable_occlusion": False,
            "reason": "complete face after the second bounded repair",
        }],
    }
    responses = iter([
        location,
        rejected,
        location,
        rejected,
        rejected,
        accepted,
    ])

    async def fake_completion(_content):
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=25, y=10, width=30, height=30),
                detection_confidence=0.99,
            )],
        )],
    ))[0]

    assert [attempt.attempt for attempt in refined.localization_attempts] == [1, 2, 3, 4]
    assert [attempt.accepted for attempt in refined.localization_attempts] == [
        False,
        False,
        False,
        True,
    ]
    assert refined.localization_attempts[3].bbox.height > refined.localization_attempts[2].bbox.height
    assert refined.detection_audit.requires_manual_review is False


@pytest.mark.anyio
@pytest.mark.parametrize(("condition", "reason", "requires_review"), [
    ("major_occlusion", "phone hides core facial features", True),
    ("minor_occlusion", "lollipop overlaps a small part of the mouth", False),
])
async def test_source_condition_with_valid_crop_does_not_trigger_relocalization(
    monkeypatch,
    condition: str,
    reason: str,
    requires_review: bool,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    location = {
        "source_code": "I01",
        "locations": [{
            "occurrence_code": "I01-V01",
            "bbox": {"x": 200, "y": 200, "width": 400, "height": 400},
            "confidence": 0.9,
        }],
    }
    source_occluded = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": False,
            "crop_contains_all_source_visible_face": True,
            "matches_expected_occurrence": True,
            "source_face_condition": condition,
            "face_centered": True,
            "tight_face_crop": True,
            "boundary_issue": "none",
            "unrecoverable_occlusion": True,
            "reason": reason,
        }],
    }
    responses = iter([location, source_occluded])
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=20, y=20, width=40, height=40),
                detection_confidence=0.99,
            )],
        )],
    ))[0]

    assert len(calls) == 2
    assert len([item for item in calls[1] if item["type"] == "image_url"]) == 2
    assert len(refined.localization_attempts) == 1
    assert refined.localization_attempts[-1].source_face_condition == condition
    assert (
        refined.localization_attempts[-1].unrecoverable_occlusion
        is requires_review
    )
    assert refined.localization_attempts[-1].accepted is True
    assert refined.localization_attempts[-1].crop_contains_all_source_visible_face is True
    assert refined.detection_audit.requires_manual_review is requires_review
    assert (
        "原图面部条件需人工确认" in refined.detection_audit.summary
    ) is requires_review


@pytest.mark.anyio
async def test_source_occlusion_still_retries_when_crop_misses_visible_face(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1", "test-key", "vision-model"
    )
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="JPEG")
    image_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    location = {
        "source_code": "I01",
        "locations": [{
            "occurrence_code": "I01-V01",
            "bbox": {"x": 200, "y": 200, "width": 400, "height": 400},
            "confidence": 0.9,
        }],
    }
    invalid_crop = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": False,
            "crop_contains_all_source_visible_face": False,
            "source_face_condition": "major_occlusion",
            "face_centered": True,
            "tight_face_crop": False,
            "boundary_issue": "left_clipped",
            "unrecoverable_occlusion": True,
            "reason": "crop misses source-visible cheek while phone hides the mouth",
        }],
    }
    valid_crop = {
        "source_code": "I01",
        "items": [{
            "occurrence_code": "I01-V01",
            "full_face_visible": False,
            "crop_contains_all_source_visible_face": True,
            "matches_expected_occurrence": True,
            "source_face_condition": "major_occlusion",
            "face_centered": True,
            "tight_face_crop": True,
            "boundary_issue": "none",
            "unrecoverable_occlusion": True,
            "reason": "all source-visible face pixels are now inside the crop",
        }],
    }
    responses = iter([location, invalid_crop, location, valid_crop])
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    refined = (await provider.refine_batch_face_boxes(
        [{
            "asset_id": "asset-1",
            "source_code": "I01",
            "image_uri": image_uri,
            "width": 100,
            "height": 100,
        }],
        [AnalysisResult(
            asset_id="asset-1",
            provider="batch-analysis",
            faces=[FaceObservation(
                external_face_id="I01-V01",
                bbox=BoundingBox(x=20, y=20, width=40, height=40),
                detection_confidence=0.99,
            )],
        )],
    ))[0]

    assert len(calls) == 4
    assert [attempt.accepted for attempt in refined.localization_attempts] == [
        False,
        True,
    ]
    assert all(
        attempt.unrecoverable_occlusion
        for attempt in refined.localization_attempts
    )
    assert refined.detection_audit.requires_manual_review is True


def test_crop_review_normalizes_bounded_provider_wrappers() -> None:
    item = {
        "occurrence_code": "I01-V01",
        "full_face_visible": True,
        "matches_expected_occurrence": False,
    }

    wrapped = YunwuMultimodalProvider._normalize_face_crop_review_payload(
        {"review": {"faces": [item]}},
        source_code="I01",
        occurrence_codes=["I01-V01"],
    )
    keyed = YunwuMultimodalProvider._normalize_face_crop_review_payload(
        {"I01": {"I01-V01": {"full_face_visible": True, "matches_expected_occurrence": False}}},
        source_code="I01",
        occurrence_codes=["I01-V01"],
    )

    assert wrapped == {"source_code": "I01", "items": [item]}
    assert keyed == {"source_code": "I01", "items": [item]}


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
async def test_large_person_clustering_quarantines_same_source_conflicts(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    observations = [
        {"code": "I01-F01", "crop_uri": "data:image/png;base64,1"},
        {"code": "I01-F02", "crop_uri": "data:image/png;base64,2"},
        {"code": "I02-F01", "crop_uri": "data:image/png;base64,3"},
        {"code": "I03-F01", "crop_uri": "data:image/png;base64,4"},
        *[
            {"code": f"I{index:02d}-F01", "crop_uri": f"data:image/png;base64,{index}"}
            for index in range(4, 26)
        ],
    ]

    async def fake_completion(_content):
        return {
            "duplicate_groups": [{
                "members": ["I01-F01", "I01-F02", "I02-F01", "I03-F01"],
                "confidence": 0.91,
            }],
            "uncertain_codes": [],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    assignments = await provider.cluster_person_observations(observations)

    assert assignments["I01-F01"] == ("vlm:I01-F01", 0.0)
    assert assignments["I01-F02"] == ("vlm:I01-F02", 0.0)
    assert assignments["I02-F01"] == assignments["I03-F01"]
    assert assignments["I02-F01"][1] == 0.91


@pytest.mark.anyio
async def test_large_person_clustering_asks_model_to_correct_same_source_conflicts(
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
    responses = iter([
        {
            "duplicate_groups": [{
                "members": ["I01-F01", "I01-F02", "I02-F01"],
                "confidence": 0.82,
            }],
            "uncertain_codes": [],
        },
        {
            "duplicate_groups": [{
                "members": ["I01-F01", "I02-F01"],
                "confidence": 0.94,
            }],
            "uncertain_codes": [],
        },
    ])
    observations.insert(1, {
        "code": "I01-F02",
        "crop_uri": "data:image/png;base64,extra",
    })
    calls = []

    async def fake_completion(content):
        calls.append(content)
        return next(responses)

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    assignments = await provider.cluster_person_observations(observations)

    assert len(calls) == 2
    assert "CORRECTION REQUIRED" in calls[1][-1]["text"]
    assert assignments["I01-F01"] == assignments["I02-F01"]
    assert assignments["I01-F01"][1] == 0.94
    assert assignments["I01-F02"] == ("vlm:I01-F02", 0.5)


@pytest.mark.anyio
async def test_cluster_review_corrects_a_reciprocal_swap(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")
    crop_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    observations = [
        {"code": code, "crop_uri": crop_uri}
        for code in ("I01-F01", "I01-F02", "I02-F01", "I02-F02")
    ]
    proposed = {
        "I01-F01": ("wrong-a", 0.99),
        "I02-F02": ("wrong-a", 0.99),
        "I01-F02": ("wrong-b", 0.98),
        "I02-F01": ("wrong-b", 0.98),
    }
    captured = {}

    async def fake_completion(content):
        captured["content"] = content
        return {
            "swaps": [
                {
                    "left_code": "I01-F01",
                    "right_code": "I01-F02",
                    "confidence": 0.96,
                },
            ],
            "uncertain_codes": [],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    reviewed = await provider.review_person_cluster_assignments(
        observations,
        proposed,
    )

    assert reviewed["I01-F01"] == reviewed["I02-F01"]
    assert reviewed["I01-F02"] == reviewed["I02-F02"]
    assert reviewed["I01-F01"] != reviewed["I01-F02"]
    assert "clear reciprocal swap" in captured["content"][0]["text"]
    assert captured["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


@pytest.mark.anyio
async def test_cluster_review_rejects_a_cross_source_swap(monkeypatch) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    output = BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, format="JPEG")
    crop_uri = "data:image/jpeg;base64," + base64.b64encode(
        output.getvalue()
    ).decode()
    observations = [
        {"code": "I01-F01", "crop_uri": crop_uri},
        {"code": "I02-F01", "crop_uri": crop_uri},
    ]

    async def fake_completion(_content):
        return {
            "swaps": [{
                "left_code": "I01-F01",
                "right_code": "I02-F01",
                "confidence": 0.99,
            }],
            "uncertain_codes": [],
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    with pytest.raises(ValueError, match="same source"):
        await provider.review_person_cluster_assignments(
            observations,
            {
                "I01-F01": ("group", 0.9),
                "I02-F01": ("group", 0.9),
            },
        )


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
async def test_permission_compiler_accepts_person_code_keyed_payload(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )

    async def fake_completion(_content):
        return {
            "P01": {
                "editable": ["face_identity", "face_features"],
                "protected": [
                    "apparent_age",
                    "hair",
                    "eyewear",
                    "accessories",
                    "makeup",
                ],
            }
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)

    permissions = await provider.compile_attribute_permissions(
        task_instruction="replace @P01",
        operations=[{
            "person_code": "P01",
            "action": "replace",
            "target_identity_code": "T01",
        }],
    )

    assert permissions["P01"].editable == ["face_identity", "face_features"]


@pytest.mark.anyio
async def test_semantic_mask_rebinds_a_protected_exclusion_to_the_only_editable_face(
    monkeypatch,
) -> None:
    provider = YunwuMultimodalProvider(
        "https://yunwu.example/v1",
        "test-key",
        "vision-model",
    )
    editable = ["face_identity", "face_features"]
    protected = [
        "apparent_age",
        "hair",
        "eyewear",
        "accessories",
        "makeup",
    ]

    async def fake_completion(_content):
        return {
            "source_asset_id": "source-1",
            "source_asset_code": "I01",
            "contours": [{
                "operation": "exclude",
                "face_instance_id": "face-keep",
                "person_id": "person-keep",
                "person_code": "P02",
                "permissions": {
                    "editable": [],
                    "protected": [*editable, *protected],
                },
                "points": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.2, "y": 0.1},
                    {"x": 0.2, "y": 0.2},
                ],
                "confidence": 0.9,
                "evidence": "protect neighboring face",
            }],
            "confidence": 0.9,
            "summary": "bounded exclusion",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_completion)
    proposal = await provider.compile_semantic_mask_proposal(
        source_asset_id="source-1",
        source_asset_code="I01",
        source_uri="data:image/jpeg;base64,unused",
        faces=[
            {
                "face_instance_id": "face-edit",
                "person_id": "person-edit",
                "person_code": "P01",
                "action": "replace",
                "editable_attributes": editable,
                "protected_attributes": protected,
            },
            {
                "face_instance_id": "face-keep",
                "person_id": "person-keep",
                "person_code": "P02",
                "action": "keep",
                "editable_attributes": [],
                "protected_attributes": [*editable, *protected],
            },
        ],
        protected_people=[{"person_id": "person-keep", "person_code": "P02"}],
        identity_evidence=[],
    )

    assert proposal.contours[0].face_instance_id == "face-edit"
    assert proposal.contours[0].person_code == "P01"
    assert proposal.contours[0].permissions.editable == editable


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
