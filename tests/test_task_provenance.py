"""Active batch provenance remains independent of retired quality evidence production."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from api.tasks import provenance


def result(status="SUCCESS", **changes):
    values = {
        "status": status,
        "processed_url": "/api/v1/files/batch/result.png",
        "original_url": "/api/v1/files/batch/original.png",
        "client_target_id": "image-7",
        "model_routing": [],
    }
    values.update(changes)
    return SimpleNamespace(**values)


def manager(*results):
    batch = SimpleNamespace(task_id="batch", results=list(results))
    return SimpleNamespace(tasks={"batch": batch}, _requests={})


@pytest.mark.parametrize("status", ["SUCCESS", "PARTIAL"])
def test_missing_original_request_keeps_authoritative_batch_identity(status):
    output = result(status)
    evidence = provenance.batch_provenance(manager(output), output.processed_url)
    assert evidence["original_url"] == output.original_url
    assert evidence["batch_task_id"] == "batch" and evidence["client_target_id"] == "image-7"
    assert evidence["lineage"] == "batch-local"
    assert evidence["parameters"] == {} and evidence["sources"] == []


def test_controlled_alias_matches_same_file_after_unrelated_cleared_result(monkeypatch):
    output = result()
    cleared = result(processed_url="/api/v1/files/cleared/result.png")
    calls = []

    def resolve(url):
        calls.append(url)
        if url == cleared.processed_url:
            raise ValueError("Historical result was cleared")
        assert url in {output.processed_url, "http://localhost:3000/api/v1/files/batch/result.png"}
        return Path("controlled/batch/result.png")

    monkeypatch.setattr(provenance, "resolve_managed_url", resolve)
    evidence = provenance.batch_provenance(
        manager(cleared, output), "http://localhost:3000/api/v1/files/batch/result.png"
    )
    assert evidence["original_url"] == output.original_url
    assert calls[1] == cleared.processed_url and calls[2] == output.processed_url


def test_untrusted_original_payload_is_not_exposed():
    output = result(original_url="data:image/png;base64,PRIVATE")
    evidence = provenance.batch_provenance(manager(output), output.processed_url)
    assert evidence["original_url"] is None and "PRIVATE" not in str(evidence)


def test_failed_result_cannot_claim_successful_batch_lineage(monkeypatch):
    monkeypatch.setattr(provenance, "resolve_managed_url", lambda url: Path("controlled/file.png"))
    output = result("FAILED")
    assert provenance.batch_provenance(manager(output), output.processed_url) is None


def test_invalid_requested_alias_is_rejected_instead_of_guessing_a_batch(monkeypatch):
    def reject(url):
        raise ValueError("Invalid controlled path")

    monkeypatch.setattr(provenance, "resolve_managed_url", reject)
    with pytest.raises(ValueError, match="Invalid controlled path"):
        provenance.batch_provenance(manager(result()), "/api/v1/files/../private.png")


def test_unmanaged_canvas_has_no_invented_batch_origin(monkeypatch):
    monkeypatch.setattr(provenance, "resolve_managed_url", lambda url: None)
    assert provenance.batch_provenance(manager(result()), "data:image/png;base64,MANUAL") is None
    assert provenance.batch_provenance(None, "data:image/png;base64,MANUAL") is None
