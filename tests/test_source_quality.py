"""Offline source health is pure, conservative evidence, never a deletion policy."""

import copy
import json

import numpy as np
import pytest

from scripts.source_quality import inspect_source_quality


def test_low_quality_duplicate_and_unknown_are_explicit_without_mutation():
    refs = [
        {"quality_score": 0.2, "sharpness": 2, "sha256": "same", "embedding": [1, 0]},
        {"quality_score": 0.9, "sharpness": 80, "sha256": "same", "embedding": [1, 0]},
        {},
    ]
    before = copy.deepcopy(refs)
    report = inspect_source_quality(refs)
    assert refs == before
    assert report["reference_count"] == 3
    first, duplicate, unknown = report["references"]
    assert {"low_quality", "low_sharpness"} <= set(first["warnings"])
    assert duplicate["duplicate_of"] == 1
    assert "exact_duplicate" in duplicate["warnings"]
    assert unknown["suspected_identity_outlier"] is None
    assert set(unknown["unknown_fields"]) == {"quality_score", "sharpness", "embedding", "hash"}
    assert report["advisory_only"]
    json.dumps(report, allow_nan=False)


def test_suspected_outlier_requires_coherent_independent_peers():
    refs = [{"embedding": vector, "sha256": str(i)} for i, vector in enumerate(([1, 0], [0.99, 0.01], [0, 1]))]
    report = inspect_source_quality(refs)
    assert report["references"][2]["suspected_identity_outlier"] is True
    assert all(row["suspected_identity_outlier"] is not True for row in report["references"][:2])
    assert report["references"][2]["other_peers_median_similarity"] > 0.99
    two = inspect_source_quality(refs[:1] + refs[2:])
    assert all(row["suspected_identity_outlier"] is None for row in two["references"])


def test_duplicates_cannot_manufacture_peer_consensus():
    refs = [
        {"embedding": [1, 0], "pixel_sha256": "duplicate"},
        {"embedding": [1, 0], "pixel_sha256": "duplicate"},
        {"embedding": [0, 1]},
    ]
    report = inspect_source_quality(refs)
    assert report["independent_valid_embedding_count"] == 2
    assert all(row["suspected_identity_outlier"] is None for row in report["references"])


def test_invalid_embeddings_and_dimensions_stay_unknown():
    refs = [
        {"embedding": vector, "quality_score": float("nan")}
        for vector in ([0, 0], [float("nan"), 1], [[1]], "bad", [1, 0, 0], [1, 0])
    ]
    result = inspect_source_quality(refs)
    assert all(row["quality_score"] is None for row in result["references"])
    assert all(row["suspected_identity_outlier"] is None for row in result["references"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "thresholds",
    [{"minimum_quality": -1}, {"minimum_sharpness": float("nan")}, {"outlier_similarity": 2}, {"peer_cohesion": True}],
)
def test_experimental_thresholds_are_validated(thresholds):
    with pytest.raises(ValueError, match="thresholds"):
        inspect_source_quality([], **thresholds)


def test_thresholds_are_recorded_and_no_automatic_filtering():
    result = inspect_source_quality([{"quality_score": 0.7}], minimum_quality=0.8)
    assert result["thresholds"]["minimum_quality"] == 0.8
    assert result["reference_count"] == 1
    assert result["references"][0]["warnings"][0] == "low_quality"
    assert not any(isinstance(value, np.ndarray) for value in result.values())
