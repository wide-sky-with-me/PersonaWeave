"""Regression tests for optimal (Hungarian) multi-person mapping assignment."""

import numpy as np
import pytest

from core.face_assignment import resolve_person_mapping_assignment


def test_optimal_assignment_avoids_greedy_first_come_face_theft():
    # mapping 0's best face (0) is only a mediocre 0.50 match; mapping 1's best face is the
    # SAME face 0, but at a near-perfect 0.90. A greedy pick processing mapping 0 first would
    # steal face 0 for its weaker 0.50 match, leaving mapping 1 with only face 1 at 0.20 --
    # below threshold, so mapping 1 would end up completely unmatched despite the strongest
    # pairing in the whole matrix belonging to it.
    similarity_matrix = np.array(
        [
            [0.50, 0.36],  # mapping 0: face 0 = 0.50 (best), face 1 = 0.36
            [0.90, 0.20],  # mapping 1: face 0 = 0.90 (best, near-perfect), face 1 = 0.20
        ],
        dtype=np.float32,
    )
    thresholds = [0.35, 0.35]

    assignment = resolve_person_mapping_assignment(similarity_matrix, thresholds)

    face1, sim1 = assignment[1]
    face0, sim0 = assignment[0]
    assert face1 == 0 and sim1 == pytest.approx(0.90, abs=1e-4)
    assert face0 == 1 and sim0 == pytest.approx(0.36, abs=1e-4)


def test_below_threshold_pairs_are_never_returned_even_when_forced():
    # A single mapping with only one face available, but the similarity is below threshold.
    similarity_matrix = np.array([[0.20]], dtype=np.float32)
    assignment = resolve_person_mapping_assignment(similarity_matrix, [0.35])
    assert assignment == {}


def test_empty_matrix_returns_no_assignment():
    assert resolve_person_mapping_assignment(np.zeros((0, 0), dtype=np.float32), []) == {}
