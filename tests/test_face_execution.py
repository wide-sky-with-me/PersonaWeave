"""Single-face execution can be verified with explicit runtimes and synthetic pixels only."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core import face_execution
from core.swapper import ARCFACE_112_V1_TEMPLATE, ARCFACE_128_TEMPLATE, create_facefusion_box_mask


@pytest.mark.parametrize(
    "model,size,face_size,template",
    [
        ("inswapper_128", 128, 80, "inswapper_128"),
        ("inswapper_128", 128, 140, "inswapper_128"),
        ("hyperswap_1c_256", 256, 140, "arcface_128"),
        ("ghost_1_256", 256, 140, "arcface_112_v1"),
    ],
)
def test_explicit_runtimes_preserve_mask_intersection_restoration_and_manual_pixels(model, size, face_size, template):
    boosted_size = 256 if face_size >= 96 else size
    canonical = ARCFACE_112_V1_TEMPLATE if template == "arcface_112_v1" else ARCFACE_128_TEMPLATE
    before_template = canonical.copy()
    canvas = np.full((boosted_size, boosted_size, 3), 100, np.uint8)
    before = canvas.copy()
    target = SimpleNamespace(
        bbox=np.array([0, 0, face_size, face_size], np.float32),
        kps=canonical * boosted_size,
    )
    occlusion = np.full(canvas.shape[:2], 0.8, np.float32)
    occlusion[:, boosted_size // 4 : boosted_size // 3] = 0
    regions = np.full(canvas.shape[:2], 0.6, np.float32)
    regions[boosted_size // 4 : boosted_size // 3] = 0
    manual = np.zeros(canvas.shape[:2], np.uint8)
    manual[:, boosted_size * 3 // 4 :] = 255
    calls = []

    def infer(crop, embedding):
        assert embedding.shape == (2,)
        calls.append(crop.shape)
        pixels = np.full_like(crop, 150)
        return (pixels, np.full((size, size), 0.5, np.float32)) if model == "hyperswap_1c_256" else pixels

    parser = SimpleNamespace(
        extract_106_landmarks=lambda *_: np.zeros((106, 2), np.float32),
        generate_anatomical_mask_512=lambda _: np.full((512, 512), 0.7, np.float32),
    )
    diagnostics = {}
    output = face_execution.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(2, np.float32),
        swapper=SimpleNamespace(
            spec=SimpleNamespace(name=model, input_size=(size, size), crop_template=template), infer_aligned=infer
        ),
        enable_dense_mask=True,
        manual_mask=manual,
        enable_color_transfer=False,
        enable_face_restoration=True,
        restoration_strength=0.5,
        parser=parser,
        occluder=SimpleNamespace(create_occlusion_mask=lambda _: occlusion),
        region_parser=SimpleNamespace(create_region_protection_mask=lambda _, **__: regions),
        restorer=SimpleNamespace(restore=lambda crop, **_: np.full_like(crop, 230)),
        diagnostics=diagnostics,
    )
    mask = np.minimum.reduce(
        [create_facefusion_box_mask(canvas.shape[:2]), np.full_like(occlusion, 0.7), occlusion, regions]
    )
    if model == "hyperswap_1c_256":
        mask = np.minimum(mask, 0.5)
    restoration_alpha = mask * np.float32(0.5)
    restored_swap = (restoration_alpha * np.float32(230) + (1.0 - restoration_alpha) * np.float32(150)).astype(np.uint8)
    alpha = mask.copy()
    if model == "inswapper_128":
        alpha *= 0.9 * (0.45 / 0.72 if face_size < 96 else 1.0)
    alpha *= 1.0 - manual.astype(np.float32) / 255.0
    expected = (np.float32(100) * (1.0 - alpha) + restored_swap.astype(np.float32) * alpha).astype(np.uint8)
    np.testing.assert_allclose(output, np.repeat(expected[:, :, None], 3, axis=2), atol=1)
    np.testing.assert_array_equal(canvas, before)
    np.testing.assert_array_equal(canonical, before_template)
    protected = (occlusion == 0) | (regions == 0) | (manual == 255)
    np.testing.assert_array_equal(output[protected], canvas[protected])
    assert calls == [(size, size, 3)] * (boosted_size // size) ** 2
    assert diagnostics["intersection_mean"] == pytest.approx(float(mask.mean()))
    assert diagnostics["components"][-1]["name"] == "manual"


@pytest.mark.parametrize("scale,interpolation", [(0.75, cv2.INTER_AREA), (1.5, cv2.INTER_CUBIC)])
def test_alignment_interpolation_uses_transform_scale_not_model_size(monkeypatch, scale, interpolation):
    matrix = np.array([[scale, 0, 0], [0, scale, 0]], np.float64)
    monkeypatch.setattr(face_execution.cv2, "estimateAffinePartial2D", lambda *_args, **_kwargs: (matrix, None))
    warp = face_execution.cv2.warpAffine
    calls = []

    def recording_warp(*args, **kwargs):
        calls.append(kwargs["flags"])
        return warp(*args, **kwargs)

    monkeypatch.setattr(face_execution.cv2, "warpAffine", recording_warp)
    canvas = np.full((256, 256, 3), 100, np.uint8)
    face_execution.swap_single_face_facefusion(
        canvas,
        SimpleNamespace(bbox=np.array([0, 0, 140, 140]), kps=ARCFACE_128_TEMPLATE * 256),
        np.ones(2),
        swapper=SimpleNamespace(
            spec=SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128"),
            infer_aligned=lambda crop, _: crop,
        ),
        enable_dense_mask=False,
        manual_mask=None,
        enable_color_transfer=False,
    )
    assert calls[0] == interpolation
