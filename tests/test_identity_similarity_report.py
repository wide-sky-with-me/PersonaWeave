"""Pure scoring logic of the non-production identity-transfer audit (no model inference)."""

import numpy as np
import pytest

from scripts.evaluate_identity_similarity import (
    aggregate,
    calibration,
    far_threshold,
    identify,
    mean_ref_calibration,
    mean_ref_scores,
    summarize,
)


def mean_gallery(embeddings, weights=None):
    return np.average(embeddings, axis=0, weights=weights)


def synthetic_library():
    rng = np.random.default_rng(0)
    refs, genders = {}, {}
    for index, name in enumerate(("a", "b", "c", "d")):
        center = rng.normal(size=32)
        refs[name] = [(center + rng.normal(scale=0.2, size=32), 1.0) for _ in range(5)]
        genders[name] = "male" if index < 2 else "female"
    return refs, genders


def test_calibration_separates_genuine_from_impostor_and_pools_by_gender():
    refs, genders = synthetic_library()
    centroids, genuine, impostor, impostor_same = calibration(refs, mean_gallery, genders)
    assert len(genuine) == 20 and len(impostor) == 4 * 5 * 3
    assert len(impostor_same) == 4 * 5 * 1  # one same-gender other identity per identity
    assert genuine.mean() > impostor.mean() + 0.3
    assert all(abs(np.linalg.norm(c) - 1) < 1e-9 for c in centroids.values())


def test_far_threshold_accepts_at_most_the_requested_impostor_fraction():
    impostor = np.linspace(0, 1, 1001)
    threshold = far_threshold(impostor, 0.01)
    assert (impostor >= threshold).mean() <= 0.011


def test_identify_ranks_ties_against_the_intended_identity():
    centroids = {"x": np.array([1.0, 0.0]), "y": np.array([1.0, 0.0]), "z": np.array([0.0, 1.0])}
    score, rank, margin = identify(np.array([1.0, 0.0]), centroids, "x")
    assert score == pytest.approx(1.0) and rank == 2 and margin == pytest.approx(0.0)
    _score, rank, margin = identify(np.array([0.0, 1.0]), centroids, "z")
    assert rank == 1 and margin == pytest.approx(1.0)


def test_aggregate_keeps_unscored_rows_in_every_rate_denominator():
    scored = {
        "status": "scored",
        "rank": 1,
        "margin": 0.1,
        "sim_result_intended": 0.8,
        "sim_original_intended": 0.1,
        "sim_result_original": 0.2,
    }
    rows = [
        scored,
        {**scored, "rank": 7, "sim_result_intended": 0.3},
        {"status": "swap_skipped"},
        {"status": "swap_skipped"},
    ]
    result = aggregate(rows, {"any@0.01": 0.5})
    assert result["attempted"] == 4 and result["scored"] == 2 and result["not_scored"] == {"swap_skipped": 2}
    assert result["rank1_rate"] == pytest.approx(0.25)
    assert result["top5_rate"] == pytest.approx(0.25)
    assert result["accepted_at_any@0.01"] == pytest.approx(0.25)
    assert summarize([]) is None


def test_mean_reference_score_and_calibration_use_individual_references():
    refs, genders = synthetic_library()
    units = {name: np.stack([e / np.linalg.norm(e) for e, _ in pairs]) for name, pairs in refs.items()}
    genuine, impostor, impostor_same = mean_ref_calibration(units, genders)
    assert len(genuine) == 20 and len(impostor) == 4 * 5 * 3 and len(impostor_same) == 4 * 5
    assert genuine.mean() > impostor.mean() + 0.3
    scores = mean_ref_scores(refs["a"][0][0], units)
    assert max(scores, key=scores.get) == "a"
