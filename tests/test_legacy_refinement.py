"""Historical candidates remain safe to read and accept without model services or old workers."""

import asyncio
import hashlib

import pytest

from api.refinement.legacy import LegacyRefinementArchive
from api.schemas import RefinementTaskData, RefinementTaskStatus
from api.tasks.persistence import SQLiteTaskStore


@pytest.fixture
def archived(tmp_path):
    content = b"historical-normalized-candidate"
    digest = hashlib.sha256(content).hexdigest()
    task = RefinementTaskData(
        task_id="old-task",
        status="REVIEW_REQUIRED",
        created_at="now",
        revision=7,
        candidate_digest=digest,
        candidate_url="/api/v1/refinement/tasks/old-task/candidate",
        people=[{"person_id": 1, "face_index": 1, "report": {"review_error": "Advisory review unavailable"}}],
    )
    staging = tmp_path / "staging"
    path = staging / task.task_id / "candidate.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    db_path = tmp_path / "tasks.sqlite3"
    SQLiteTaskStore(db_path).save(
        "refinement", task.task_id, task.model_dump(mode="json"), {"people": [{"person_id": 1}]}
    )
    archive = LegacyRefinementArchive(staging, tmp_path / "accepted", db_path=db_path)
    return archive, archive.get(task.task_id), content


def test_review_error_does_not_prevent_explicit_human_acceptance(archived):
    archive, task, content = archived
    assert task.people[0].report["review_error"] == "Advisory review unavailable"
    assert archive.candidate_path(task.task_id).read_bytes() == content
    accepted = asyncio.run(archive.accept(task.task_id, task.revision, task.candidate_digest))
    assert accepted.status == RefinementTaskStatus.ACCEPTED
    assert (archive.accepted_dir / f"{task.task_id}.png").read_bytes() == content
    assert archive.candidate_path(task.task_id) is None


@pytest.mark.parametrize("stale", ["revision", "digest", "changed_bytes", "missing_bytes"])
def test_accept_rejects_stale_or_missing_candidates_without_publication(archived, stale):
    archive, task, _ = archived
    revision, digest = task.revision, task.candidate_digest
    if stale == "revision":
        revision -= 1
    elif stale == "digest":
        digest = "0" * 64
    elif stale == "changed_bytes":
        archive.candidate_path(task.task_id).write_bytes(b"tampered")
    else:
        archive.candidate_path(task.task_id).unlink()
    with pytest.raises(ValueError):
        asyncio.run(archive.accept(task.task_id, revision, digest))
    assert task.status == RefinementTaskStatus.REVIEW_REQUIRED
    assert not list(archive.accepted_dir.iterdir())


def test_accept_is_idempotent_but_rechecks_published_bytes(archived):
    archive, task, content = archived
    revision, digest = task.revision, task.candidate_digest
    asyncio.run(archive.accept(task.task_id, revision, digest))
    accepted_revision = task.revision
    result = asyncio.run(archive.accept(task.task_id, revision, digest))
    assert result is task and task.revision == accepted_revision
    published = archive.accepted_dir / f"{task.task_id}.png"
    assert published.read_bytes() == content
    published.write_bytes(b"tampered after acceptance")
    with pytest.raises(ValueError, match="missing or changed"):
        asyncio.run(archive.accept(task.task_id, revision, digest))


def test_atomic_replace_failure_never_exposes_partial_formal_result(archived, monkeypatch):
    archive, task, _ = archived

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("api.refinement.legacy.os.replace", fail)
    with pytest.raises(OSError):
        asyncio.run(archive.accept(task.task_id, task.revision, task.candidate_digest))
    assert task.status == RefinementTaskStatus.ACCEPTING
    assert not list(archive.accepted_dir.iterdir())
    assert archive._store.load("refinement")[0][0]["status"] == "ACCEPTING"


def test_path_escaping_historical_snapshot_is_not_loaded(tmp_path):
    task = RefinementTaskData(task_id="../escape", status="REVIEW_REQUIRED", created_at="now")
    db_path = tmp_path / "tasks.sqlite3"
    SQLiteTaskStore(db_path).save("refinement", task.task_id, task.model_dump(mode="json"))
    archive = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    assert archive.get(task.task_id) is None
    assert archive.candidate_path(task.task_id) is None


def test_candidate_symlink_cannot_escape_archive_root(archived, tmp_path):
    archive, task, content = archived
    outside = tmp_path / "outside.png"
    outside.write_bytes(content)
    candidate = archive.candidate_path(task.task_id)
    candidate.unlink()
    candidate.symlink_to(outside)
    assert archive.candidate_path(task.task_id) is None
    with pytest.raises(ValueError, match="valid historical candidate"):
        asyncio.run(archive.accept(task.task_id, task.revision, task.candidate_digest))
    assert not list(archive.accepted_dir.iterdir())


def test_archive_get_does_not_invent_unknown_tasks(archived):
    archive, _, _ = archived
    assert archive.get("missing") is None
    assert archive.quality_detail("missing") is None
    assert asyncio.run(archive.accept("missing", 0, "0" * 64)) is None
    asyncio.run(archive.shutdown())


def test_delete_legacy_clears_evidence_ratings_and_published_bytes_only_for_its_owner(archived):
    archive, task, content = archived
    asyncio.run(archive.accept(task.task_id, task.revision, task.candidate_digest))
    evidence = archive.quality.root / task.task_id
    evidence.mkdir(parents=True)
    (evidence / "evidence.png").write_bytes(content)
    new_sessions = archive.staging_dir / "sessions"
    new_sessions.mkdir()
    (new_sessions / "keep").write_bytes(content)
    for kind in (
        "refinement_quality_initial",
        "refinement_quality_generation",
        "refinement_quality_candidate",
        "refinement_quality_evaluation",
    ):
        archive._store.save(kind, task.task_id + ":1", {"task_id": task.task_id})
        archive._store.save(kind, task.task_id + "-other:1", {"task_id": task.task_id + "-other"})
    assert asyncio.run(archive.delete(task.task_id, task.revision))
    assert archive.get(task.task_id) is None
    assert not evidence.exists()
    assert not (archive.accepted_dir / f"{task.task_id}.png").exists()
    assert (new_sessions / "keep").read_bytes() == content
    assert archive._store.load("refinement") == []
    for kind in (
        "refinement_quality_initial",
        "refinement_quality_generation",
        "refinement_quality_candidate",
        "refinement_quality_evaluation",
    ):
        assert archive._store.load(kind) == [({"task_id": task.task_id + "-other"}, None)]
    restored = LegacyRefinementArchive(archive.staging_dir, archive.accepted_dir, db_path=archive._store.path)
    assert restored.list() == []


def test_legacy_delete_database_failure_restores_files_and_memory(archived):
    import sqlite3

    archive, task, content = archived
    with sqlite3.connect(archive._store.path) as conn:
        conn.execute("CREATE TRIGGER block_delete BEFORE DELETE ON api_tasks BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(archive.delete(task.task_id, task.revision))
    assert archive.get(task.task_id) is task
    assert archive.candidate_path(task.task_id).read_bytes() == content


def test_legacy_delete_rejects_stale_active_and_reserved_ids(archived):
    archive, task, _ = archived
    with pytest.raises(ValueError):
        asyncio.run(archive.delete(task.task_id, task.revision - 1))
    task.status = RefinementTaskStatus.ACCEPTING
    with pytest.raises(ValueError):
        asyncio.run(archive.delete(task.task_id, task.revision))
    reserved = task.model_copy(update={"task_id": "sessions", "status": RefinementTaskStatus.FAILED})
    archive.tasks["sessions"] = reserved
    archive._locks["sessions"] = asyncio.Lock()
    with pytest.raises(ValueError):
        asyncio.run(archive.delete("sessions", reserved.revision))
