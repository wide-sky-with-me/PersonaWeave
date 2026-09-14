"""Local image I/O that preserves Unicode filesystem paths on Windows."""

from pathlib import Path

import cv2
import numpy as np


def read_image(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """Read a local image, returning None for unreadable, empty or invalid files.

    This helper does not authorize paths; external callers must first resolve a
    managed asset. Python opens the Unicode path, while OpenCV only decodes bytes.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags)


def write_image(path: str | Path, image: np.ndarray) -> bool:
    """Encode by file extension and write a Unicode path; return False on I/O failure.

    Invalid images or unsupported codecs retain OpenCV's encoding exceptions.
    Parent directories remain the caller's responsibility.
    """
    path = Path(path)
    success, encoded = cv2.imencode(path.suffix, image)
    if not success:
        return False
    try:
        path.write_bytes(encoded.tobytes())
    except OSError:
        return False
    return True
