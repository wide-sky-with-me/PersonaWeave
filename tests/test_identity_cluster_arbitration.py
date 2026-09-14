"""Offline VLM suggestions cannot override the request's biometric identity threshold."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from core.ai.arbiter import VLMArbiterResult, VLMIdentityArbiter


def face(embedding):
    return SimpleNamespace(embedding=embedding, bbox=np.array([0, 0, 8, 8]), det_score=0.9, kps=None)


def arbitrate_clusters(source, reference, threshold, **suggestion):
    """Use genuine cosine math on synthetic embeddings; no gateway or model is initialized."""
    orphan_face, reference_face = face(source), face(reference)
    target = {"faces": [(0, 0, reference_face), (1, 0, face(reference))]}
    orphan = {"faces": [(2, 0, orphan_face)]}
    clusters = [target, orphan]
    arbiter = VLMIdentityArbiter.__new__(VLMIdentityArbiter)
    payload = {"is_success": True, "matched_person_index": 1, "confidence": 0.99, **suggestion}
    arbiter.arbitrate = AsyncMock(return_value=VLMArbiterResult(**payload))
    images = [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(3)]
    result = asyncio.run(
        arbiter.refine_clusters(
            images, [[reference_face], [reference_face], [orphan_face]], clusters, similarity_threshold=threshold
        )
    )
    arbiter.arbitrate.assert_awaited_once()
    return result, target, orphan


@pytest.mark.parametrize(
    ("threshold", "merged"),
    [(0.55, True), (0.65, False), (0.95, False)],
)
def test_high_vlm_confidence_requires_request_arcface_threshold(threshold, merged):
    # Non-unit vectors prevent an accidental raw dot product from passing as cosine.
    result, target, orphan = arbitrate_clusters(np.array([6.0, 8.0]), np.array([10.0, 0.0]), threshold)
    assert len(result) == (1 if merged else 2)
    assert len(target["faces"]) == (3 if merged else 2)
    assert any(item is orphan for item in result) is not merged


@pytest.mark.parametrize("missing_side", ["source", "reference"])
@pytest.mark.parametrize("invalid", [None, [], [0.0, 0.0], [np.nan, 0.0], [np.inf, 0.0], [1.0], [[1.0, 0.0]]])
def test_missing_or_invalid_embedding_cannot_be_replaced_by_vlm(invalid, missing_side):
    source, reference = np.array([1.0, 0.0]), np.array([1.0, 0.0])
    if missing_side == "source":
        source = invalid
    else:
        reference = invalid
    result, target, _ = arbitrate_clusters(source, reference, 0.0)
    assert len(result) == 2
    assert len(target["faces"]) == 2


@pytest.mark.parametrize("suggestion", [{"confidence": 0.6}, {"is_success": False}, {"matched_person_index": 2}])
def test_arcface_match_does_not_bypass_advisory_result_protocol(suggestion):
    result, target, _ = arbitrate_clusters(np.array([1.0, 0.0]), np.array([1.0, 0.0]), 0.8, **suggestion)
    assert len(result) == 2
    assert len(target["faces"]) == 2


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_arcface_threshold_is_rejected(threshold):
    arbiter = VLMIdentityArbiter.__new__(VLMIdentityArbiter)
    with pytest.raises(ValueError, match="ArcFace similarity_threshold"):
        asyncio.run(arbiter.refine_clusters([], [], [], similarity_threshold=threshold))


@pytest.mark.parametrize("threshold", [0.23, 0.83])
def test_cluster_route_forwards_request_threshold_without_loading_models(monkeypatch, threshold):
    from api.inference_limiter import InferenceLimiter
    from api.routes import faces
    from api.schemas import FaceClusterRequest

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    pipeline = SimpleNamespace(detector=SimpleNamespace(detect_faces=lambda *args, **kwargs: []))
    arbiter = SimpleNamespace(refine_clusters=AsyncMock(return_value=[]))
    monkeypatch.setattr("core.ai.arbiter.VLMIdentityArbiter", lambda: arbiter)
    monkeypatch.setattr(faces, "load_image_from_source", lambda source: image)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pipeline=pipeline, inference_limiter=InferenceLimiter(1)))
    )
    result = asyncio.run(
        faces.cluster_batch_faces(
            FaceClusterRequest(image_urls=["offline-a", "offline-b"], similarity_threshold=threshold), request
        )
    )
    assert result.code == 200
    assert arbiter.refine_clusters.await_args.kwargs["similarity_threshold"] == threshold
