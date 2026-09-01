"""Global assignment helpers for multi-person identity mapping."""

import numpy as np
from scipy.optimize import linear_sum_assignment


def resolve_person_mapping_assignment(
    similarity_matrix: np.ndarray, thresholds: list[float]
) -> dict[int, tuple[int, float]]:
    """Return the globally optimal accepted mapping-to-face pairs.

    Below-threshold pairs are masked before Hungarian optimization and checked
    again afterward. This prevents an early mapping from greedily claiming a
    face that is a much stronger match for another identity.
    """
    assignment: dict[int, tuple[int, float]] = {}
    if similarity_matrix.size == 0:
        return assignment
    masked = similarity_matrix.copy()
    for row, threshold in enumerate(thresholds):
        masked[row, masked[row] < threshold] = -1.0
    row_indices, col_indices = linear_sum_assignment(-masked)
    for row, col in zip(row_indices, col_indices, strict=True):
        similarity = float(similarity_matrix[row, col])
        if similarity >= thresholds[row]:
            assignment[int(row)] = (int(col), similarity)
    return assignment
