"""Historical HTTP projections and byte publication remain available with AI disabled."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from api.refinement.legacy import LegacyRefinementArchive
from api.schemas import RefinementTaskData
from api.tasks.persistence import SQLiteTaskStore


@pytest.fixture
def history(tmp_path, monkeypatch):
    from api.app import app

    content = b"historical-candidate-bytes"
    digest = hashlib.sha256(content).hexdigest()
    store = SQLiteTaskStore(tmp_path / "tasks.sqlite3")
    for number in range(3):
        task = RefinementTaskData(
            task_id=f"old-{number}",
            status="REVIEW_REQUIRED",
            created_at=f"2026-01-0{number + 1}T00:00:00Z",
            candidate_digest=digest,
            candidate_url="data:image/png;base64,PRIVATE",
            quality_provenance={"private_path": "/private/evidence"},
        )
        path = tmp_path / "staging" / task.task_id / "candidate.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        store.save("refinement", task.task_id, task.model_dump(mode="json"), {"private": "request-body"})
    archive = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(app.state, "legacy_refinement_archive", archive, raising=False)
    monkeypatch.setattr(app.state, "refinement_service", None, raising=False)
    return TestClient(app), archive, content


def test_history_summary_is_bounded_and_never_contains_image_or_request_payloads(history):
    client, _, _ = history
    first = client.get("/api/v1/refinement/legacy-tasks", params={"limit": 1}).json()["data"]
    second = client.get("/api/v1/refinement/legacy-tasks", params={"limit": 1, "offset": 1}).json()["data"]
    assert len(first) == len(second) == 1
    assert first[0]["task_id"] != second[0]["task_id"]
    assert set(first[0]) == {
        "task_id",
        "status",
        "created_at",
        "completed_at",
        "revision",
        "has_candidate",
        "has_accepted",
    }
    assert first[0]["has_candidate"] is True and first[0]["has_accepted"] is False
    assert not any(
        text in str(first) for text in ("data:image", "PRIVATE", "private_path", "request-body", "candidate_url")
    )
    assert client.get("/api/v1/refinement/legacy-tasks", params={"offset": 99}).json()["data"] == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_history_rejects_unbounded_or_negative_pagination(history, params):
    client, _, _ = history
    assert client.get("/api/v1/refinement/legacy-tasks", params=params).status_code == 422


def test_historical_candidate_accepts_only_matching_revision_and_digest(history):
    client, archive, content = history
    task = archive.get("old-0")
    assert client.get("/api/v1/refinement/tasks/old-0").status_code == 200
    candidate = client.get("/api/v1/refinement/tasks/old-0/candidate")
    assert candidate.status_code == 200 and candidate.content == content
    url = "/api/v1/refinement/tasks/old-0/accept"
    body = {"expected_revision": task.revision, "candidate_digest": task.candidate_digest}
    assert client.post(url, json={**body, "expected_revision": 999}).status_code == 409
    assert not list(archive.accepted_dir.iterdir())
    accepted = client.post(url, json=body)
    assert accepted.status_code == 200 and accepted.json()["data"]["status"] == "ACCEPTED"
    assert (archive.accepted_dir / "old-0.png").read_bytes() == content
    assert client.post(url, json=body).status_code == 200
    assert client.get("/api/v1/refinement/tasks/old-0/candidate").status_code == 404


def test_missing_or_changed_artifacts_are_not_reported_as_readable(history):
    client, archive, _ = history
    archive.candidate_path("old-0").write_bytes(b"tampered")
    assert client.get("/api/v1/refinement/tasks/old-0/candidate").status_code == 404
    summary = client.get("/api/v1/refinement/legacy-tasks").json()["data"]
    assert next(item for item in summary if item["task_id"] == "old-0")["has_candidate"] is False
    assert client.get("/api/v1/refinement/tasks/unknown").status_code == 404
    assert client.get("/api/v1/refinement/tasks/unknown/candidate").status_code == 404


def test_escaping_candidate_does_not_crash_history_list_or_download(history, tmp_path):
    client, archive, content = history
    external = tmp_path / "outside.png"
    external.write_bytes(content)
    candidate = archive.candidate_path("old-0")
    candidate.unlink()
    candidate.symlink_to(external)
    response = client.get("/api/v1/refinement/legacy-tasks")
    assert response.status_code == 200
    assert next(item for item in response.json()["data"] if item["task_id"] == "old-0")["has_candidate"] is False
    assert client.get("/api/v1/refinement/tasks/old-0/candidate").status_code == 404


def test_legacy_delete_endpoint_requires_current_revision_and_removes_detail(history):
    client, archive, _ = history
    task = archive.get("old-0")
    url = "/api/v1/refinement/tasks/old-0"
    assert client.delete(url).status_code == 422
    assert client.delete(url, params={"expected_revision": 999}).status_code == 409
    assert client.delete(url, params={"expected_revision": task.revision}).status_code == 204
    assert client.get(url).status_code == 404
    assert client.delete(url, params={"expected_revision": task.revision}).status_code == 404
    assert client.get("/api/v1/refinement/tasks/old-1").status_code == 200
