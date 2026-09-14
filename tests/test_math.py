"""
Unit tests for mathematical operations in PersonaWeave:
- Color harmonization (Reinhard LAB transfer)
- Multi-view embedding aggregation
"""

import numpy as np
import pytest

from core.color_matcher import reinhard_color_transfer
from core.recognizer import FaceRecognizer


def test_reinhard_color_transfer():
    """Constant warm colors have an analytical target; allow only color-space rounding."""
    source = np.full((16, 16, 3), (100, 150, 200), dtype=np.uint8)
    target = np.full((16, 16, 3), (80, 120, 170), dtype=np.uint8)
    mask = np.ones((16, 16), dtype=np.float32)

    harmonized = reinhard_color_transfer(source, target, blend_ratio=1.0, face_mask=mask)
    assert harmonized.shape == source.shape
    assert harmonized.dtype == np.uint8
    np.testing.assert_allclose(harmonized, target, atol=3, rtol=0)
    np.testing.assert_array_equal(reinhard_color_transfer(source, target, blend_ratio=0, face_mask=mask), source)
    halfway = reinhard_color_transfer(source, target, blend_ratio=0.5, face_mask=mask)
    np.testing.assert_allclose(halfway, (source.astype(float) + harmonized) / 2, atol=0.5, rtol=0)


def test_reinhard_color_transfer_returns_source_for_empty_or_tiny_mask():
    source = np.full((16, 16, 3), (10, 20, 30), dtype=np.uint8)
    target = np.full((16, 16, 3), (200, 150, 100), dtype=np.uint8)
    empty_mask = np.zeros((16, 16), dtype=np.float32)
    tiny_mask = empty_mask.copy()
    tiny_mask[3, 4] = 1.0

    assert np.array_equal(reinhard_color_transfer(source, target, face_mask=empty_mask), source)
    assert np.array_equal(reinhard_color_transfer(source, target, face_mask=tiny_mask), source)


@pytest.mark.parametrize(
    ("weights", "expected_components"),
    [([3, 4], [0.6, 0.8]), ([4, 3], [0.8, 0.6]), ([1, 0], [1, 0]), ([0, 1], [0, 1]), (None, [2**-0.5, 2**-0.5])],
)
def test_gallery_embedding_aggregation(weights, expected_components):
    """Orthogonal inputs expose ignored/reversed weights and missing L2 normalization."""
    vectors = np.eye(2, 512, dtype=np.float32)
    original = vectors.copy()
    expected = np.zeros(512, dtype=np.float32)
    expected[:2] = expected_components

    gallery = FaceRecognizer.build_gallery_embedding(list(vectors), weights=weights)
    np.testing.assert_allclose(gallery, expected, atol=1e-6, rtol=0)
    assert np.isclose(np.linalg.norm(gallery), 1.0, atol=1e-6)
    np.testing.assert_array_equal(vectors, original)
