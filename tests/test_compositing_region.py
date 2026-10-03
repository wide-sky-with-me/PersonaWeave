"""Bounded pasteback preserves canvas, support and tightly bounded affine rounding."""

import cv2
import numpy as np
import pytest

from core.compositing import region_bbox_mean, warp_face_region
from core.pipeline import composite_in_rows


@pytest.mark.parametrize(
    "angle,scale,offset", [(0, 1, (33, 27)), (27, 2, (230, 93)), (-41, 0.6, (-30, 17)), (0, 1, (900, 900))]
)
def test_roi_matches_full_canvas_without_clipping_interpolation_support(angle, scale, offset):
    rng = np.random.default_rng(7)
    canvas = rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)
    face = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
    mask = cv2.GaussianBlur(np.pad(np.ones((96, 96), np.float32), 16), (31, 31), 5)
    transform = cv2.getRotationMatrix2D((64, 64), angle, scale)
    transform[:, 2] += offset
    full_face = cv2.warpAffine(face, transform, (800, 600), borderMode=cv2.BORDER_REPLICATE)
    full_mask = cv2.warpAffine(mask, transform, (800, 600)).clip(0, 1)
    expected = composite_in_rows(canvas, full_face, full_mask)
    roi = warp_face_region(face, mask, transform, canvas.shape)
    got = canvas.copy()
    if roi is not None:
        region, warped_face, warped_mask = roi
        left, top, right, bottom = region
        got[top:bottom, left:right] = composite_in_rows(canvas[top:bottom, left:right], warped_face, warped_mask)
        outside = full_mask.copy()
        outside[top:bottom, left:right] = 0
        assert not outside.any()
        assert warped_mask.size < full_mask.size / 2
        # A box outside the footprint includes real zero alpha, not a cropped mean.
        assert region_bbox_mean(warped_mask, region, np.array([0, 0, 800, 600]), canvas.shape) == pytest.approx(
            float(full_mask.mean()), abs=1e-6
        )
    difference = np.abs(expected.astype(np.int16) - got.astype(np.int16))
    assert difference.max() <= 8  # INTER_LINEAR's 1/32-pixel quantization; platform-dependent OpenCV paths.
    assert np.count_nonzero(difference) / difference.size < 0.002
    assert np.array_equal(got[full_mask == 0], canvas[full_mask == 0])
