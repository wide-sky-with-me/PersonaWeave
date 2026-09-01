"""Tests for swapper metadata and reference selection policies."""

from types import SimpleNamespace

import numpy as np

from core.model_router import SwapperModelRouter
from core.pipeline import PersonaWeavePipeline
from core.swapper import (
    ARCFACE_112_V1_TEMPLATE,
    ARCFACE_128_TEMPLATE,
    GHOST_1_256_SPEC,
    HYPERSWAP_1C_256_SPEC,
    INSWAPPER_128_SPEC,
    SWAPPER_SPECS,
    HyperSwapAdapter,
)


def _unit(index: int) -> np.ndarray:
    value = np.zeros(4, dtype=np.float32)
    value[index] = 1.0
    return value


def test_inswapper_spec_documents_runtime_contract():
    assert INSWAPPER_128_SPEC.name == "inswapper_128"
    assert INSWAPPER_128_SPEC.input_size == (128, 128)
    assert INSWAPPER_128_SPEC.source_encoder == "arcface_w600k_r50"
    assert "commercial" in INSWAPPER_128_SPEC.license_status


def test_hyperswap_spec_and_alignment_contract():
    assert HYPERSWAP_1C_256_SPEC.input_size == (256, 256)
    assert HYPERSWAP_1C_256_SPEC.crop_template == "arcface_128"
    assert HYPERSWAP_1C_256_SPEC.sha256 == "5528c2d76fe9986c99d829278987ef9f3a630cb606db7628d02b57b330f406a5"
    assert SWAPPER_SPECS[HYPERSWAP_1C_256_SPEC.name] is HYPERSWAP_1C_256_SPEC
    assert ARCFACE_128_TEMPLATE.shape == (5, 2)
    assert np.all((ARCFACE_128_TEMPLATE > 0.0) & (ARCFACE_128_TEMPLATE < 1.0))


def test_hyperswap_get_preserves_two_dimensional_model_mask(monkeypatch):
    adapter = object.__new__(HyperSwapAdapter)
    adapter.infer_aligned = lambda crop, embedding: (
        np.full((256, 256, 3), 200, dtype=np.uint8),
        np.full((256, 256), 0.5, dtype=np.float32),
    )
    monkeypatch.setattr(adapter, "_affine_matrix", lambda kps: np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32))
    face = SimpleNamespace(kps=np.zeros((5, 2), dtype=np.float32), normed_embedding=np.ones(512, dtype=np.float32))

    result = adapter.get(np.zeros((256, 256, 3), dtype=np.uint8), face, face, paste_back=True)

    assert result.shape == (256, 256, 3)
    assert 80 < result[128, 128, 0] < 120


def test_ghost_spec_has_clear_license_and_converter_contract():
    assert GHOST_1_256_SPEC.input_size == (256, 256)
    assert GHOST_1_256_SPEC.crop_template == "arcface_112_v1"
    assert GHOST_1_256_SPEC.license_status == "Apache-2.0"
    assert GHOST_1_256_SPEC.source_encoder == "crossface_ghost"
    assert SWAPPER_SPECS[GHOST_1_256_SPEC.name] is GHOST_1_256_SPEC
    assert ARCFACE_112_V1_TEMPLATE.shape == (5, 2)


def test_auto_router_prefers_robust_backend_for_small_or_turned_faces():
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256", "hyperswap_1c_256"})
    small = router.select("auto", face_size=72, yaw_ratio=0.1, identity_confidence=0.9, allow_research_models=True)
    turned = router.select("auto", face_size=220, yaw_ratio=0.7, identity_confidence=0.9, allow_research_models=True)
    assert small.selected == "inswapper_128"
    assert turned.selected == "inswapper_128"


def test_auto_router_enforces_license_and_uses_stable_baseline_without_research_opt_in():
    router = SwapperModelRouter({"inswapper_128", "ghost_1_256", "hyperswap_1c_256"})
    safe = router.select("auto", face_size=200, yaw_ratio=0.1, identity_confidence=0.8)
    research = router.select("auto", face_size=200, yaw_ratio=0.1, identity_confidence=0.8, allow_research_models=True)
    blocked_explicit = router.select("hyperswap_1c_256", face_size=200, yaw_ratio=0.1, identity_confidence=0.8)
    assert safe.selected == "inswapper_128"
    assert research.selected == "hyperswap_1c_256"
    assert blocked_explicit.selected == "inswapper_128"


def test_router_reports_asset_fallback():
    router = SwapperModelRouter({"inswapper_128"})
    decision = router.select("ghost_1_256", face_size=180, yaw_ratio=0.1, identity_confidence=0.8)
    assert decision.selected == "inswapper_128"
    assert decision.fallback is True


def test_best_match_uses_pose_not_target_identity_similarity():
    pipeline = object.__new__(PersonaWeavePipeline)
    target = SimpleNamespace(
        kps=np.array([[0, 0], [10, 0], [8, 5], [3, 9], [7, 9]], dtype=np.float32),
        embedding=_unit(0),
    )
    pose_compatible = {"yaw": 0.6, "embedding": _unit(1)}
    target_lookalike = {"yaw": -0.7, "embedding": _unit(0)}
    gallery = (_unit(1) + _unit(2)) / np.sqrt(2.0)

    selected = pipeline.select_best_reference_embedding(
        target,
        [target_lookalike, pose_compatible],
        gallery,
        mode="best-match",
    )

    assert selected[1] > selected[0]
    assert np.isclose(np.linalg.norm(selected), 1.0, atol=1e-6)


def test_gallery_mode_is_stable_across_targets():
    pipeline = object.__new__(PersonaWeavePipeline)
    gallery = _unit(2)
    refs = [{"yaw": -0.5, "embedding": _unit(0)}, {"yaw": 0.5, "embedding": _unit(1)}]
    left = SimpleNamespace(kps=np.array([[0, 0], [10, 0], [2, 5], [3, 9], [7, 9]], dtype=np.float32))
    right = SimpleNamespace(kps=np.array([[0, 0], [10, 0], [8, 5], [3, 9], [7, 9]], dtype=np.float32))

    assert np.array_equal(pipeline.select_best_reference_embedding(left, refs, gallery, "gallery"), gallery)
    assert np.array_equal(pipeline.select_best_reference_embedding(right, refs, gallery, "gallery"), gallery)
