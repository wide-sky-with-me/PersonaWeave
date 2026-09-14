"""Tests for the BiSeNet semantic region-protection pre/post-processing logic.

These exercise the pure numpy transforms directly (no ONNX session required), so they run
without the ~90MB bisenet_resnet_34.onnx weight being present.
"""

import numpy as np
import pytest

from core.region_parser import REGION_CLASSES, RegionFaceParser


def test_preprocess_produces_expected_nchw_shape_and_dtype():
    face = np.random.randint(0, 255, (200, 150, 3), dtype=np.uint8)

    tensor = RegionFaceParser.preprocess(face)

    assert tensor.shape == (1, 3, 512, 512)
    assert tensor.dtype == np.float32


def test_preprocess_converts_bgr_to_rgb_and_applies_imagenet_normalization():
    face = np.zeros((10, 10, 3), dtype=np.uint8)
    face[:, :, 0] = 30  # B
    face[:, :, 1] = 60  # G
    face[:, :, 2] = 90  # R

    tensor = RegionFaceParser.preprocess(face)

    expected_r = (90 / 255.0 - 0.485) / 0.229
    expected_g = (60 / 255.0 - 0.456) / 0.224
    expected_b = (30 / 255.0 - 0.406) / 0.225
    # NCHW: channel 0 must be R (post BGR->RGB flip), channel 2 must be B.
    assert tensor[0, 0, 0, 0] == pytest.approx(expected_r, abs=1e-4)
    assert tensor[0, 1, 0, 0] == pytest.approx(expected_g, abs=1e-4)
    assert tensor[0, 2, 0, 0] == pytest.approx(expected_b, abs=1e-4)


def test_postprocess_protects_only_the_requested_region():
    size = 128
    logits = np.zeros((19, size, size), dtype=np.float32)
    logits[REGION_CLASSES["skin"]] = 5.0  # skin baseline everywhere
    logits[REGION_CLASSES["glasses"], 40:90, 40:90] = 10.0  # glasses patch dominates in the center

    mask = RegionFaceParser.postprocess_protection_mask(logits, ("glasses",), (size, size))

    assert mask.shape == (size, size)
    assert mask[64, 64] < 0.1  # deep inside the glasses region: strongly protected
    assert mask[5, 5] > 0.9  # far corner, pure skin: fully swap-allowed


def test_postprocess_with_no_matching_region_leaves_mask_fully_open():
    size = 64
    logits = np.zeros((19, size, size), dtype=np.float32)
    logits[REGION_CLASSES["skin"]] = 5.0  # every pixel classified as skin, no glasses anywhere

    mask = RegionFaceParser.postprocess_protection_mask(logits, ("glasses",), (size, size))

    assert np.all(mask > 0.9)


def test_unknown_region_name_raises_key_error():
    logits = np.zeros((19, 32, 32), dtype=np.float32)
    with pytest.raises(KeyError):
        RegionFaceParser.postprocess_protection_mask(logits, ("not-a-real-region",), (32, 32))


def test_missing_model_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        RegionFaceParser(str(tmp_path / "missing.onnx"))


def test_glasses_feather_is_scale_equivalent_at_128_and_256():
    import cv2

    logits = np.zeros((19, 64, 64), dtype=np.float32)
    logits[REGION_CLASSES["skin"]] = 1
    logits[REGION_CLASSES["glasses"], 16:48, 16:48] = 2
    small = RegionFaceParser.postprocess_protection_mask(logits, ("glasses",), (128, 128))
    large = RegionFaceParser.postprocess_protection_mask(logits, ("glasses",), (256, 256))
    resized = cv2.resize(large, (128, 128), interpolation=cv2.INTER_AREA)
    assert float(np.abs(small - resized).mean()) < 0.003
    assert float(small.mean()) == pytest.approx(float(large.mean()), abs=0.003)
    assert small[64, 64] == large[128, 128] == 0
    assert small[0, 0] == pytest.approx(1)
    assert large[0, 0] == pytest.approx(1)


def test_glasses_256_baseline_is_exactly_preserved():
    import cv2

    logits = np.zeros((19, 64, 64), dtype=np.float32)
    logits[REGION_CLASSES["skin"]] = 1
    logits[REGION_CLASSES["glasses"], 16:48, 16:48] = 2
    keep = 1 - cv2.resize(
        (logits.argmax(0) == REGION_CLASSES["glasses"]).astype(np.float32), (256, 256), interpolation=cv2.INTER_NEAREST
    )
    original = (cv2.GaussianBlur(keep, (0, 0), 5).clip(0.5, 1) - 0.5) * 2
    result = RegionFaceParser.postprocess_protection_mask(logits, ("glasses",), (256, 256))
    np.testing.assert_array_equal(result, original)
