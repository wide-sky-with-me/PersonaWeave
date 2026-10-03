"""Bounded affine pasteback helpers for the production face-swap path."""

import cv2
import numpy as np


def warp_face_region(face, mask, crop_to_canvas, canvas_shape):
    """Warp only the crop's canvas footprint, including bilinear interpolation support.

    The source margin includes zero-border interpolation and rounding. Destination
    clipping keeps out-of-frame faces safe. Moving the destination origin can change
    OpenCV subpixel rounding very slightly; regression tests bound that difference.
    """
    height, width = canvas_shape[:2]
    crop_height, crop_width = mask.shape
    corners = np.array(
        [[-2, -2], [crop_width + 2, -2], [crop_width + 2, crop_height + 2], [-2, crop_height + 2]], dtype=np.float64
    )
    transformed = corners @ crop_to_canvas[:, :2].T + crop_to_canvas[:, 2]
    left, top = np.maximum(np.floor(transformed.min(axis=0)).astype(int) - 2, 0)
    right, bottom = np.minimum(np.ceil(transformed.max(axis=0)).astype(int) + 2, [width, height])
    if right <= left or bottom <= top:
        return None
    local_transform = crop_to_canvas.astype(np.float64).copy()
    local_transform[:, 2] -= [left, top]
    size = (int(right - left), int(bottom - top))
    warped_face = cv2.warpAffine(face, local_transform, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    warped_mask = cv2.warpAffine(mask, local_transform, size, flags=cv2.INTER_LINEAR).clip(0.0, 1.0)
    return (int(left), int(top), int(right), int(bottom)), warped_face, warped_mask


def region_bbox_mean(mask, region, bbox, canvas_shape):
    """Compute the full-canvas face-box alpha mean, treating pixels outside the ROI as zero."""
    height, width = canvas_shape[:2]
    x1, y1 = np.maximum(np.floor(bbox[:2]).astype(int), 0)
    x2, y2 = np.minimum(np.ceil(bbox[2:]).astype(int), [width, height])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    left, top, right, bottom = region
    sx, sy, ex, ey = max(x1, left), max(y1, top), min(x2, right), min(y2, bottom)
    if ex <= sx or ey <= sy:
        return 0.0
    return float(mask[sy - top : ey - top, sx - left : ex - left].sum() / ((x2 - x1) * (y2 - y1)))
