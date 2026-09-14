"""Unicode paths must work even when OpenCV's native filename API cannot open them."""

import cv2
import numpy as np
import pytest

from api.media import TrustedImagePath, load_image_from_source
from utils.image_io import read_image, write_image


def test_unicode_roundtrip_preserves_pixels_and_managed_access(tmp_path, monkeypatch):
    folder = tmp_path / "材料 与图片"
    folder.mkdir()
    path = folder / "人物参考.png"
    pixels = np.arange(18 * 23 * 3, dtype=np.uint8).reshape(18, 23, 3)

    def unsupported(*args, **kwargs):
        raise AssertionError("Native OpenCV filename I/O must not be used")

    monkeypatch.setattr(cv2, "imread", unsupported)
    monkeypatch.setattr(cv2, "imwrite", unsupported)
    assert write_image(path, pixels)
    np.testing.assert_array_equal(read_image(path), pixels)
    np.testing.assert_array_equal(load_image_from_source(TrustedImagePath(path)), pixels)
    with pytest.raises(ValueError, match="Local filesystem"):
        load_image_from_source(str(path))


@pytest.mark.parametrize("data", [None, b"", b"not an image"])
def test_unreadable_images_keep_failure_semantics(tmp_path, data):
    path = tmp_path / "无效图片.png"
    if data is not None:
        path.write_bytes(data)
    assert read_image(path) is None
    with pytest.raises(ValueError, match="Failed to read managed"):
        load_image_from_source(TrustedImagePath(path))


def test_image_write_failure_is_reported(tmp_path):
    assert not write_image(tmp_path / "不存在" / "结果.png", np.zeros((2, 3, 3), np.uint8))


def test_unchanged_decode_preserves_alpha(tmp_path):
    path = tmp_path / "透明图片.png"
    pixels = np.full((3, 4, 4), [1, 2, 3, 127], dtype=np.uint8)
    assert write_image(path, pixels)
    np.testing.assert_array_equal(read_image(path, cv2.IMREAD_UNCHANGED), pixels)
