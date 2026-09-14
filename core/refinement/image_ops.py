"""Pure image encoding and declared output sizing shared by refinement workflows."""

import base64

import cv2
import numpy as np

from core.image_security import decode_image_bytes


def encode_image(image, max_dim=1600, lossless=False):
    """Encode a BGR canvas; only diagnostic previews may be downscaled or JPEG-compressed."""
    canvas = image
    if not lossless and max(image.shape[:2]) > max_dim:
        scale = max_dim / max(image.shape[:2])
        canvas = cv2.resize(
            image,
            (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    extension = ".png" if lossless else ".jpg"
    options = [] if lossless else [cv2.IMWRITE_JPEG_QUALITY, 92]
    ok, encoded = cv2.imencode(extension, canvas, options)
    if not ok:
        raise ValueError("Could not encode refinement image")
    mime = "png" if lossless else "jpeg"
    return f"data:image/{mime};base64," + base64.b64encode(encoded).decode("ascii")


def decode_image(image_bytes):
    """Validate encoded image limits, orientation and color before returning a BGR canvas."""
    return decode_image_bytes(image_bytes)


def normalize_candidate(candidate, target_shape):
    """Restore the source aspect ratio and canvas dimensions without local pixel compositing."""
    target_height, target_width = target_shape[:2]
    source_height, source_width = candidate.shape[:2]
    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_width = max(1, round(source_height * target_ratio))
        left = max(0, (source_width - crop_width) // 2)
        cropped = candidate[:, left : left + crop_width]
        operation = "center-crop-width"
    elif source_ratio < target_ratio:
        crop_height = max(1, round(source_width / target_ratio))
        top = max(0, (source_height - crop_height) // 2)
        cropped = candidate[top : top + crop_height, :]
        operation = "center-crop-height"
    else:
        cropped = candidate
        operation = "resize-only"
    normalized = cv2.resize(cropped, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
    return normalized, {
        "provider_output_size": [source_width, source_height],
        "target_canvas_size": [target_width, target_height],
        "canvas_normalization": operation,
    }


def generation_parameters(gateway, model_alias, image_shape):
    """Use explicit configuration capabilities, never provider names or inferred model limits."""
    resolved_alias = model_alias or gateway.default_image_alias
    config = gateway.image_edits.get(resolved_alias)
    if config is None:
        raise ValueError(f"Unknown image-edit model alias: {resolved_alias}")
    policy = config.generation
    height, width = image_shape[:2]
    if policy.get("size") == "auto":
        return {"size": "auto"}, None
    if "sizes" in policy:
        dimensions = [tuple(map(int, size.split("x"))) for size in policy["sizes"]]
        output_width, output_height = min(
            dimensions, key=lambda size: abs(np.log((size[0] / size[1]) / (width / height)))
        )
    else:
        max_side, min_side, grid = (int(policy[key]) for key in ("max_side", "min_side", "grid"))
        limits = [max_side / max(height, width), 1.0]
        if policy.get("max_pixels"):
            limits.append((policy["max_pixels"] / (height * width)) ** 0.5)
        scale = min(limits)
        output_width = max(min_side, int(width * scale) // grid * grid)
        output_height = max(min_side, int(height * scale) // grid * grid)
        if max(output_width, output_height) > max_side or (
            policy.get("max_pixels") and output_width * output_height > policy["max_pixels"]
        ):
            raise ValueError("Image aspect ratio cannot satisfy the configured generation limits")
    return {"size": f"{output_width}x{output_height}"}, [output_width, output_height]
