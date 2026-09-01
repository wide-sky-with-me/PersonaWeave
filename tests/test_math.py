"""
Unit tests for mathematical operations in PersonaWeave:
- Landmark affine transformation
- Color harmonization (Reinhard LAB transfer)
- Multi-view embedding aggregation
"""

import numpy as np

from core.color_matcher import reinhard_color_transfer
from core.recognizer import FaceRecognizer
from utils.affine import (
    ARCFACE_DST_112,
    INSWAPPER_DST_128,
    create_smooth_face_mask,
    estimate_affine_matrix,
    paste_back_face,
    warp_face,
)


def test_estimate_affine_matrix():
    """Test 2x3 affine matrix estimation on canonical template."""
    src = ARCFACE_DST_112.copy()
    dst = INSWAPPER_DST_128.copy()
    matrix = estimate_affine_matrix(src, dst)
    assert matrix.shape == (2, 3)
    assert matrix.dtype == np.float32


def test_warp_and_paste_back():
    """Test full warping and inverse paste back round-trip."""
    orig_img = np.full((300, 300, 3), 128, dtype=np.uint8)
    src_kps = np.array([[100, 100], [200, 100], [150, 150], [120, 200], [180, 200]], dtype=np.float32)

    matrix = estimate_affine_matrix(src_kps, INSWAPPER_DST_128)
    crop = warp_face(orig_img, matrix, (128, 128))
    assert crop.shape == (128, 128, 3)

    mask = create_smooth_face_mask((128, 128), feather_radius=5)
    assert mask.shape == (128, 128)
    assert mask.min() >= 0.0 and mask.max() <= 1.0

    pasted = paste_back_face(crop, orig_img, matrix, mask)
    assert pasted.shape == orig_img.shape
    assert pasted.dtype == np.uint8


def test_reinhard_color_transfer():
    """Test Reinhard LAB color transfer between two synthetic face images."""
    src_face = np.full((128, 128, 3), (100, 150, 200), dtype=np.uint8)  # Orange/warm
    tgt_face = np.full((128, 128, 3), (200, 150, 100), dtype=np.uint8)  # Blue/cold

    harmonized = reinhard_color_transfer(src_face, tgt_face, blend_ratio=1.0)
    assert harmonized.shape == (128, 128, 3)
    assert harmonized.dtype == np.uint8


def test_reinhard_color_transfer_returns_source_for_empty_or_tiny_mask():
    source = np.full((16, 16, 3), (10, 20, 30), dtype=np.uint8)
    target = np.full((16, 16, 3), (200, 150, 100), dtype=np.uint8)
    empty_mask = np.zeros((16, 16), dtype=np.float32)
    tiny_mask = empty_mask.copy()
    tiny_mask[3, 4] = 1.0

    assert np.array_equal(reinhard_color_transfer(source, target, face_mask=empty_mask), source)
    assert np.array_equal(reinhard_color_transfer(source, target, face_mask=tiny_mask), source)


def test_gallery_embedding_aggregation():
    """Test multi-view embedding weighted aggregation and normalization."""
    v1 = np.random.randn(512).astype(np.float32)
    v1 /= np.linalg.norm(v1)

    v2 = np.random.randn(512).astype(np.float32)
    v2 /= np.linalg.norm(v2)

    gallery = FaceRecognizer.build_gallery_embedding([v1, v2], weights=[0.6, 0.4])
    assert gallery.shape == (512,)
    # Norm must be exactly 1.0
    assert np.isclose(np.linalg.norm(gallery), 1.0, atol=1e-5)
