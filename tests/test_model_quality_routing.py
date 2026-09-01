import numpy as np

from core.detector import DetectedFace
from core.face_metrics import calculate_reference_quality, describe_reference_quality
from core.model_router import SwapperModelRouter


def _face(size: int, score: float = 0.9) -> DetectedFace:
    return DetectedFace(
        bbox=np.asarray([0.0, 0.0, float(size), float(size)]),
        kps=np.asarray([[30, 40], [70, 40], [50, 58], [35, 75], [65, 75]], dtype=np.float32),
        det_score=score,
    )


def test_auto_routing_uses_inswapper_without_research_opt_in():
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256", "hyperswap_1c_256"})

    decision = router.select(
        "auto",
        face_size=256,
        yaw_ratio=0.0,
        identity_confidence=0.9,
        allow_research_models=False,
    )

    assert decision.selected == "inswapper_128"


def test_auto_routing_uses_hyperswap_only_with_research_opt_in():
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256", "hyperswap_1c_256"})

    decision = router.select(
        "auto",
        face_size=256,
        yaw_ratio=0.0,
        identity_confidence=0.9,
        allow_research_models=True,
    )

    assert decision.selected == "hyperswap_1c_256"


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
