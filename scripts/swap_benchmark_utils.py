"""Small non-production helpers shared by the swap benchmark CLIs."""

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def file_sha256(path: Path) -> str:
    """Hash exact input/output file bytes, not a re-encoded preview."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value) -> None:
    """Write strict JSON atomically, rejecting non-finite measurement values."""
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def image_paths(directory: Path) -> dict[str, Path]:
    """Index supported images by stem, refusing ambiguous same-stem files."""
    images = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            if path.stem in images:
                raise ValueError(f"Ambiguous image stem {path.stem!r} in {directory}")
            images[path.stem] = path
    return images


def cosine(left, right) -> float:
    """Compute finite normalized cosine, refusing invalid embeddings."""
    left, right = np.asarray(left).ravel(), np.asarray(right).ravel()
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if left.shape != right.shape or not np.isfinite(denominator) or denominator <= 0:
        raise ValueError("Invalid benchmark embedding")
    return float(np.clip(np.dot(left, right) / denominator, -1, 1))


def match_face_boxes(original_boxes, result_boxes, minimum_iou: float = 0.3) -> dict[int, tuple[int, float]]:
    """Globally match same-canvas geometry; dummy columns retain unmatched originals.

    Only IoU-qualified pairs enter optimization. Maximize match count first, then total
    IoU, without using identity scores (which would bias the subsequent identity audit).
    Returned indices are zero-based; no positional fallback is permitted.
    """
    if not 0 < minimum_iou <= 1:
        raise ValueError("minimum_iou must be in (0, 1]")
    original = np.asarray(original_boxes, dtype=float).reshape(-1, 4)
    result = np.asarray(result_boxes, dtype=float).reshape(-1, 4)
    for boxes in (original, result):
        if not np.isfinite(boxes).all() or np.any(boxes[:, 2:] <= boxes[:, :2]):
            raise ValueError("Invalid face bounding box")
    if not len(original) or not len(result):
        return {}
    intersection_size = np.maximum(
        0,
        np.minimum(original[:, None, 2:], result[None, :, 2:]) - np.maximum(original[:, None, :2], result[None, :, :2]),
    )
    intersection = intersection_size.prod(axis=2)
    area_original = (original[:, 2:] - original[:, :2]).prod(axis=1)
    area_result = (result[:, 2:] - result[:, :2]).prod(axis=1)
    iou = intersection / (area_original[:, None] + area_result[None, :] - intersection)
    valid = iou >= minimum_iou
    # A cardinality bonus larger than any possible IoU total prevents a strong
    # single pair from discarding several valid, slightly weaker associations.
    scores = np.where(valid, min(len(original), len(result)) + 1 + iou, -1)
    scores = np.concatenate((scores, np.zeros((len(original), len(original)))), axis=1)
    rows, columns = linear_sum_assignment(scores, maximize=True)
    return {
        int(i): (int(j), float(iou[i, j]))
        for i, j in zip(rows, columns, strict=True)
        if j < len(result) and valid[i, j]
    }
