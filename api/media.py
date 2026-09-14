"""Controlled image loading and encoding shared by API capabilities."""

import base64

import cv2
import numpy as np

from core.image_security import (
    decode_data_uri,
    download_image,
    resolve_managed_url,
)
from utils.image_io import read_image


class TrustedImagePath(str):
    """Marker for an internal path resolved from an opaque managed asset ID."""


def load_image_from_source(source_str: str, *, trusted_local: bool = False) -> np.ndarray:
    """Load an external image without allowing arbitrary local filesystem reads."""
    if source_str.startswith("data:"):
        return decode_data_uri(source_str)

    managed_path = resolve_managed_url(source_str)
    if managed_path is not None:
        source_str = str(managed_path)
        trusted_local = True

    if source_str.startswith(("http://", "https://")):
        return download_image(source_str)

    if not (trusted_local or isinstance(source_str, TrustedImagePath)):
        raise ValueError("Local filesystem image paths are not accepted by the external API")
    img = read_image(source_str)
    if img is None:
        raise ValueError("Failed to read managed local image")
    if img.shape[0] * img.shape[1] > 40_000_000:
        raise ValueError("Image exceeds the pixel limit")
    return img


def encode_image_to_base64(img: np.ndarray, ext: str = ".jpg") -> str:
    """Encodes BGR numpy image to base64 data URI."""
    success, buffer = cv2.imencode(ext, img)
    if not success:
        raise ValueError("Failed to encode image to buffer.")
    b64_str = base64.b64encode(buffer).decode("utf-8")
    mime = "image/jpeg" if ext.lower() in [".jpg", ".jpeg"] else "image/png"
    return f"data:{mime};base64,{b64_str}"


def decode_manual_mask(value: str | None) -> np.ndarray | None:
    """Decode a nonempty manual mask, rejecting malformed or non-image payloads."""
    if not value:
        return None
    encoded = value.split(",", 1)[1] if "," in value else value
    try:
        mask_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("manual_mask must be a valid base64-encoded image") from exc
    mask = cv2.imdecode(np.frombuffer(mask_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None or mask.size == 0:
        raise ValueError("manual_mask must decode to a nonempty image")
    return mask
