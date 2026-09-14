"""Pure advisory source checks for offline experiments, not identity authentication.

No image loading, model inference, reference removal, or production policy change.
Thresholds are experimental and should be calibrated on the caller's own samples.
"""

from itertools import combinations

import numpy as np


def _number(value, minimum=0.0, maximum=float("inf")):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        return None
    value = float(value)
    return value if np.isfinite(value) and minimum <= value <= maximum else None


def _unit_embedding(value):
    if value is None:
        return None
    try:
        vector = np.asarray(value, dtype=np.float64)
    except (ValueError, TypeError):
        return None
    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
        return None
    norm = np.linalg.norm(vector)
    return vector / norm if np.isfinite(norm) and norm > 1e-8 else None


def inspect_source_quality(
    references: list[dict],
    *,
    minimum_quality: float = 0.48,
    minimum_sharpness: float = 30.0,
    outlier_similarity: float = 0.35,
    peer_cohesion: float = 0.55,
) -> dict:
    """Report duplicates, low measurements, and tentative leave-one-out identity outliers.

    Inputs use existing quality_score/embedding fields plus optional sharpness
    (face-crop Laplacian variance), sha256 and pixel_sha256. Missing or invalid
    evidence stays unknown. At least three independent usable references are
    needed to suggest which reference is an outlier. Exact duplicates do not vote
    twice. A low similarity is advisory only: pose/blur can also cause it.
    """
    thresholds = dict(
        minimum_quality=minimum_quality,
        minimum_sharpness=minimum_sharpness,
        outlier_similarity=outlier_similarity,
        peer_cohesion=peer_cohesion,
    )
    if (
        _number(minimum_quality, 0, 1) is None
        or _number(minimum_sharpness) is None
        or _number(outlier_similarity, -1, 1) is None
        or _number(peer_cohesion, -1, 1) is None
    ):
        raise ValueError("Invalid experimental source-quality thresholds")
    seen_hashes = {}
    rows, vectors, representatives = [], [], []
    for index, ref in enumerate(references, 1):
        quality = _number(ref.get("quality_score"), 0, 1)
        sharpness = _number(ref.get("sharpness"))
        vector = _unit_embedding(ref.get("embedding"))
        hashes = [(key, ref[key]) for key in ("sha256", "pixel_sha256") if isinstance(ref.get(key), str) and ref[key]]
        prior = [seen_hashes[key] for key in hashes if key in seen_hashes]
        duplicate_of = min(prior) if prior else None
        for key in hashes:
            seen_hashes.setdefault(key, duplicate_of or index)
        unknown = []
        if quality is None:
            unknown.append("quality_score")
        if sharpness is None:
            unknown.append("sharpness")
        if vector is None:
            unknown.append("embedding")
        if not hashes:
            unknown.append("hash")
        warnings = []
        if quality is not None and quality < minimum_quality:
            warnings.append("low_quality")
        if sharpness is not None and sharpness < minimum_sharpness:
            warnings.append("low_sharpness")
        if duplicate_of is not None:
            warnings.append("exact_duplicate")
        rows.append(
            dict(
                reference_index=index,
                quality_score=quality,
                sharpness=sharpness,
                duplicate_of=duplicate_of,
                unknown_fields=unknown,
                warnings=warnings,
                peer_median_similarity=None,
                other_peers_median_similarity=None,
                suspected_identity_outlier=None,
            )
        )
        vectors.append(vector)
        if vector is not None and duplicate_of is None:
            representatives.append(index - 1)
    for index, (row, vector) in enumerate(zip(rows, vectors, strict=True)):
        if vector is None or row["duplicate_of"] is not None:
            continue
        peers = [other for other in representatives if other != index and vectors[other].shape == vector.shape]
        if len(peers) < 2:
            row["warnings"].append("insufficient_identity_evidence")
            continue
        peer_similarity = float(np.median([np.clip(vector @ vectors[p], -1, 1) for p in peers]))
        cohesion = float(np.median([np.clip(vectors[a] @ vectors[b], -1, 1) for a, b in combinations(peers, 2)]))
        row["peer_median_similarity"] = peer_similarity
        row["other_peers_median_similarity"] = cohesion
        # Disagreement without a coherent peer group cannot identify the bad source.
        if cohesion < peer_cohesion:
            row["warnings"].append("inconclusive_peer_group")
            continue
        row["suspected_identity_outlier"] = peer_similarity < outlier_similarity
        if row["suspected_identity_outlier"]:
            row["warnings"].append("suspected_identity_outlier")
    return {
        "advisory_only": True,
        "thresholds": thresholds,
        "reference_count": len(references),
        "independent_valid_embedding_count": len(representatives),
        "references": rows,
        "note": "No references removed. Similarity is not identity authentication; unknown is not a pass.",
    }
