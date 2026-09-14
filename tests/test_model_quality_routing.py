import numpy as np
import pytest

from core.detector import DetectedFace
from core.face_metrics import calculate_reference_quality, describe_reference_quality
from core.model_router import SwapperModelRouter


def _face(size: int, score: float = 0.9) -> DetectedFace:
    return DetectedFace(
        bbox=np.asarray([0.0, 0.0, float(size), float(size)]),
        kps=np.asarray([[30, 40], [70, 40], [50, 58], [35, 75], [65, 75]], dtype=np.float32),
        det_score=score,
    )


@pytest.mark.parametrize(
    ("requested", "allow_research", "size", "yaw", "confidence", "expected"),
    [
        pytest.param("auto", False, 256, 0, 0.9, "inswapper_128", id="default-license"),
        pytest.param("auto", True, 256, 0, 0.9, "hyperswap_1c_256", id="research-opt-in"),
        pytest.param("hyperswap_1c_256", False, 256, 0, 0.9, "inswapper_128", id="explicit-blocked"),
        pytest.param("hyperswap_1c_256", True, 72, 0.7, 0.3, "hyperswap_1c_256", id="explicit-bypasses-geometry"),
        pytest.param("auto", True, 72, 0.1, 0.9, "inswapper_128", id="small-face"),
        pytest.param("auto", True, 220, 0.7, 0.9, "inswapper_128", id="turned-face"),
        pytest.param("auto", True, 160, 0.45, 0.55, "hyperswap_1c_256", id="inclusive-boundaries"),
        pytest.param("auto", True, 160, -0.45, 0.55, "hyperswap_1c_256", id="negative-yaw-boundary"),
        pytest.param("auto", True, 159, 0.45, 0.55, "inswapper_128", id="below-size"),
        pytest.param("auto", True, 160, 0.451, 0.55, "inswapper_128", id="above-yaw"),
        pytest.param("auto", True, 160, -0.451, 0.55, "inswapper_128", id="below-negative-yaw"),
        pytest.param("auto", True, 160, 0.45, 0.549, "inswapper_128", id="below-confidence"),
    ],
)
def test_routing_license_and_quality_boundaries(requested, allow_research, size, yaw, confidence, expected):
    """License opt-in gates explicit use; auto selection also enforces each geometry boundary."""
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256", "hyperswap_1c_256"})
    decision = router.select(
        requested,
        face_size=size,
        yaw_ratio=yaw,
        identity_confidence=confidence,
        allow_research_models=allow_research,
    )
    assert decision.requested == requested
    assert decision.selected == expected


def test_ghost_remains_available_only_as_explicit_request():
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256"})

    decision = router.select(
        "ghost_1_256",
        face_size=256,
        yaw_ratio=0.0,
        identity_confidence=0.9,
    )

    assert decision.selected == "ghost_1_256"


def test_reference_quality_rewards_face_detail_and_size():
    detailed = np.zeros((320, 320, 3), dtype=np.uint8)
    checker = np.indices((256, 256)).sum(axis=0) % 2
    detailed[:256, :256] = (checker[..., None] * 255).astype(np.uint8)
    blurred = np.full((128, 128, 3), 127, dtype=np.uint8)

    high_score = calculate_reference_quality(detailed, _face(256))
    low_score = calculate_reference_quality(blurred, _face(80, score=0.65))

    assert high_score > low_score
    assert describe_reference_quality(high_score) == "excellent"
    assert describe_reference_quality(low_score) == "low"
