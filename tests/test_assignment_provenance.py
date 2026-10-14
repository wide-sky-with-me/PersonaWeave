"""Recorded person/source facts have one executable home across tasks, refinement and recovery."""

from types import SimpleNamespace

import pytest

from api.projects import recovery
from api.refinement import batch as refinement_batch
from api.schemas import BatchTaskCreateRequest
from api.tasks import batch, provenance


def test_consumers_share_assignment_and_warning_rules_without_forwarding_wrappers():
    assert refinement_batch.assigned_outcomes is recovery.assigned_outcomes is provenance.assigned_outcomes
    assert batch.describe_skipped_people is provenance.describe_skipped_people


def test_assignment_rule_keeps_confirmed_skips_and_does_not_renumber_people():
    outcomes = [
        {"person_index": 42, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": 3}}},
        {"person_index": 7, "status": "SKIPPED", "diagnostics": {"matching": {"face_index": 1}}},
        {"person_index": 99, "status": "SKIPPED", "diagnostics": {"matching": {"face_index": None}}},
    ]
    selected = provenance.assigned_outcomes(SimpleNamespace(person_outcomes=outcomes))
    assert selected == outcomes[:2]
    assert selected[0] is outcomes[0] and selected[1] is outcomes[1]
    assert outcomes[2]["person_index"] == 99


@pytest.mark.parametrize("face_index", [None, True, False, 0, -1, 1.0, "1"])
def test_invalid_assignments_are_not_coerced_into_face_indices(face_index):
    outcome = {"person_index": 7, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": face_index}}}
    with pytest.raises(ValueError, match="confirmed face assignment"):
        provenance.assigned_outcomes(SimpleNamespace(person_outcomes=[outcome]))


@pytest.mark.parametrize("outcomes", [[], [{"person_index": 7, "status": "SKIPPED"}]])
def test_empty_confirmed_assignment_set_never_creates_a_person(outcomes):
    with pytest.raises(ValueError, match="no confirmed face assignments"):
        provenance.assigned_outcomes(SimpleNamespace(person_outcomes=outcomes))


def test_request_source_rule_keeps_single_source_scope_and_mapping_order():
    single = BatchTaskCreateRequest(
        source_manual_images=["fixture"], batch_images=[{"client_target_id": "target", "image_url": "fixture"}]
    )
    assert single.person_index is None
    assert provenance.request_person_sources(single) == [single]
    assert provenance.request_person_sources(single)[0] is single
    assert provenance.request_person_sources(None) == []
    mapped = BatchTaskCreateRequest(
        person_mappings=[
            {
                "person_index": person_id,
                "target_reference_url": "fixture",
                "target_reference_person_index": 2,
                "source_manual_images": [f"source-{person_id}"],
            }
            for person_id in (17, 7)
        ],
        batch_images=[{"client_target_id": "target", "image_url": "fixture"}],
    )
    assert provenance.request_person_sources(mapped) is mapped.person_mappings
    assert [item.person_index for item in provenance.request_person_sources(mapped)] == [17, 7]
