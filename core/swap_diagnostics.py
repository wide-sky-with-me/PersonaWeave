"""Small, array-free diagnostics for the production local swap pipeline.

Metadata describes execution evidence, not identity verification or aesthetic quality.
No reference locations, images, model exceptions or embeddings cross this boundary.
"""

import numpy as np


def normalized_gallery_weights(reference_count: int, weights: list[float] | None) -> list[float] | None:
    """Mirror FaceRecognizer's float32 sum-normalization, without clipping weights.

    An externally supplied gallery cannot reveal its provenance. None explicitly means
    unknown rather than inventing uniform or quality-based weights for that vector.
    """
    if weights is None:
        return None
    # The recognizer returns a lone embedding directly, ignoring supplied weights.
    if reference_count == 1:
        return [1.0]
    values = np.asarray(weights, dtype=np.float32)
    if values.shape != (reference_count,) or not np.isfinite(values).all():
        raise ValueError("Invalid gallery diagnostic weights")
    total = values.sum()
    if total <= 0 or np.any(values < 0):
        raise ValueError("Invalid gallery diagnostic weights")
    return (values / total).tolist()


def select_source_embedding(
    target_yaw: float,
    source_references: list[dict],
    gallery_embedding: np.ndarray,
    mode: str = "best-match",
    gallery_reference_weights: list[float] | None = None,
) -> tuple[np.ndarray, dict]:
    """Return the unchanged production embedding and its truthful source recipe.

    Selected reference indices are 1-based within this mapping's source references.
    Gallery weights describe the pre-L2 weighted sum, not linear final pixel influence.
    """
    count = len(source_references)
    weights = normalized_gallery_weights(count, gallery_reference_weights)
    metadata = {
        "mode": mode,
        "reference_count": count,
        "selected_reference_index": None,
        "gallery_weight": 1.0,
        "best_reference_weight": 0.0,
        "gallery_reference_weights": weights,
        "gallery_weights_known": weights is not None,
    }
    if mode != "best-match" or count <= 1:
        return gallery_embedding, metadata
    qualities = np.clip(
        np.asarray([float(ref.get("quality_score", 0.5)) for ref in source_references], dtype=np.float32),
        0.05,
        1.0,
    )
    scores = [
        0.7 * float(quality) + 0.3 * max(0.0, 1.0 - abs(target_yaw - float(ref.get("yaw", 0.0))) / 0.8)
        for ref, quality in zip(source_references, qualities, strict=True)
    ]
    selected = max(range(count), key=scores.__getitem__)
    gallery_weight = 0.80
    blended = gallery_weight * gallery_embedding + (1.0 - gallery_weight) * source_references[selected]["embedding"]
    metadata.update(
        selected_reference_index=selected + 1,
        gallery_weight=gallery_weight,
        best_reference_weight=1.0 - gallery_weight,
    )
    return blended / max(np.linalg.norm(blended), 1e-6), metadata


def validated_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Reject malformed optional masks inside their existing safe-fallback boundary."""
    value = np.asarray(mask)
    if value.shape != shape or not np.isfinite(value).all():
        raise ValueError("Invalid mask output")
    return value


def mask_component(name: str, status: str, mask: np.ndarray | None = None, failure_code: str | None = None) -> dict:
    """Summarize a known mask component without retaining pixels or exception text."""
    result = {"name": name, "status": status}
    if mask is not None:
        result["mean"] = float(np.mean(mask))
    if failure_code is not None:
        result["failure_code"] = failure_code
    return result


def bbox_mask_mean(mask: np.ndarray, bbox: np.ndarray) -> float:
    """Measure final alpha on the same clipped face rectangle as visible-change checks."""
    height, width = mask.shape[:2]
    x1, y1 = np.floor(bbox[:2]).astype(int)
    x2, y2 = np.ceil(bbox[2:]).astype(int)
    x1, x2 = np.clip([x1, x2], 0, width)
    y1, y2 = np.clip([y1, y2], 0, height)
    return float(mask[y1:y2, x1:x2].mean()) if x2 > x1 and y2 > y1 else 0.0
