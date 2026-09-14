"""Exercise real mask intersection and paste-back when the landmark runtime raises."""

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.face_parser import DenseFaceParser
from core.pipeline import PersonaWeavePipeline
from core.swapper import ARCFACE_128_TEMPLATE, create_facefusion_box_mask


@pytest.mark.parametrize(("crop_size", "face_size"), [(128, 80), (256, 140)])
@pytest.mark.parametrize("with_occlusion", [False, True])
def test_landmark_exception_keeps_ellipse_protection_and_failure_evidence(
    monkeypatch, capsys, crop_size, face_size, with_occlusion
):
    """Both native and pixel-boosted crops must intersect the real canonical fallback."""
    canvas = np.full((crop_size, crop_size, 3), 100, dtype=np.uint8)
    target = SimpleNamespace(
        bbox=np.array([0, 0, face_size, face_size], dtype=np.float32),
        kps=ARCFACE_128_TEMPLATE * crop_size,
    )
    pipeline = object.__new__(PersonaWeavePipeline)
    parser = object.__new__(DenseFaceParser)
    extraction_calls = []

    def broken_landmarks(image, face):
        extraction_calls.append((image.shape, face.bbox))
        raise RuntimeError("private-model-path secret-token")

    parser.model = SimpleNamespace(get=broken_landmarks)
    generated_masks = []

    def record_default_mask(points=None):
        assert points is None
        mask = DenseFaceParser.generate_anatomical_mask_512(parser, points)
        generated_masks.append(mask)
        return mask

    monkeypatch.setattr(parser, "generate_anatomical_mask_512", record_default_mask)
    pipeline.parser = parser
    pipeline.region_parser = None
    occlusion = np.full((crop_size, crop_size), 0.7, dtype=np.float32)
    occlusion[:, crop_size // 2 : crop_size * 3 // 4] = 0.0
    occlusion_shapes = []

    def occlusion_mask(crop):
        occlusion_shapes.append(crop.shape)
        return occlusion

    pipeline.occluder = SimpleNamespace(create_occlusion_mask=occlusion_mask) if with_occlusion else None
    inferred_shapes = []

    def infer_aligned(crop, _embedding):
        inferred_shapes.append(crop.shape)
        return np.full_like(crop, 200)

    swapper = SimpleNamespace(
        spec=SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128"),
        infer_aligned=infer_aligned,
    )
    diagnostics = {}
    result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=swapper,
        enable_dense_mask=True,
        manual_mask=None,
        enable_color_transfer=False,
        identity_blend_strength=1.0,
        diagnostics=diagnostics,
    )

    # Independent canonical ellipse oracle: no model weights and no fallback helper
    # in the expected result, so skipping or incorrectly sizing it fails pixel checks.
    canonical = np.zeros((512, 512), dtype=np.float32)
    cv2.ellipse(canonical, (256, 265), (195, 230), 0, 0, 360, 1.0, -1)
    canonical = cv2.GaussianBlur(canonical, (31, 31), 0).clip(0.0, 1.0)
    ellipse = cv2.resize(canonical, (crop_size, crop_size), interpolation=cv2.INTER_LINEAR)
    box = create_facefusion_box_mask((crop_size, crop_size), blur=0.3)
    expected_mask = np.minimum(box, ellipse)
    if with_occlusion:
        expected_mask = np.minimum(expected_mask, occlusion)
    # Canonical keypoints make alignment the identity transform. The small-face
    # strength cap is independent of the fallback and remains part of paste-back.
    strength = 0.45 / 0.72 if face_size < 96 else 1.0
    expected = (100.0 + 100.0 * expected_mask * strength).astype(np.uint8)
    assert result.shape == canvas.shape
    assert result.dtype == np.uint8
    np.testing.assert_allclose(result, np.repeat(expected[:, :, None], 3, axis=2), atol=1)
    protected_by_ellipse = (ellipse == 0) & (box > 0.1)
    assert np.any(protected_by_ellipse)
    np.testing.assert_array_equal(result[protected_by_ellipse], canvas[protected_by_ellipse])
    assert np.any(result > canvas)
    if with_occlusion:
        np.testing.assert_array_equal(result[:, crop_size // 2 + 2 : crop_size * 3 // 4 - 2], 100)
        assert occlusion_shapes == [canvas.shape]
    assert len(extraction_calls) == 1
    assert inferred_shapes == [(128, 128, 3)] * (crop_size // 128) ** 2
    assert len(generated_masks) == 1 and generated_masks[0].shape == (512, 512)
    np.testing.assert_array_equal(generated_masks[0], canonical)
    components = {item["name"]: item for item in diagnostics["components"]}
    assert components["dense"] == {"name": "dense", "status": "failed", "failure_code": "dense_mask_failed"}
    assert components["dense_fallback"]["status"] == "applied"
    assert components["dense_fallback"]["mean"] == pytest.approx(float(ellipse.mean()))
    assert diagnostics["intersection_mean"] == pytest.approx(float(expected_mask.mean()))
    assert "secret-token" not in json.dumps(diagnostics) + capsys.readouterr().out
