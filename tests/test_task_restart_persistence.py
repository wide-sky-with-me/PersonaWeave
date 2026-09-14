"""Restart recovery coverage for persisted batch and refinement tasks."""

import asyncio
import hashlib

import pytest

from api.refinement.legacy import LegacyRefinementArchive
from api.schemas import (
    BatchTaskCreateRequest,
    ImageSwapResult,
    RefinementTaskData,
    RefinementTaskStatus,
    TaskDetailData,
    TaskStatus,
)
from api.tasks.batch import TaskManager
from api.tasks.persistence import SQLiteTaskStore


def test_batch_restart_recovers_terminal_and_fails_interrupted(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=db_path)
    request = BatchTaskCreateRequest(
        batch_images=[
            {"client_target_id": "target-1", "image_url": "https://example.com/image.png", "manual_mask": None}
        ]
    )
    completed = TaskDetailData(
        task_id="completed", status=TaskStatus.COMPLETED, progress=1, total_images=1, created_at="now"
    )
    processing = TaskDetailData(
        task_id="processing", status=TaskStatus.PROCESSING, progress=0.5, total_images=2, created_at="now"
    )
    manager.tasks = {completed.task_id: completed, processing.task_id: processing}
    manager._requests = {completed.task_id: request, processing.task_id: request}
    manager._persist(completed.task_id)
    manager._persist(processing.task_id)

    recovered = TaskManager(None, output_base_dir=tmp_path / "output", db_path=db_path)

    assert recovered.get_task("completed").status == TaskStatus.COMPLETED
    interrupted = recovered.get_task("processing")
    assert interrupted.status == TaskStatus.FAILED
    assert "restart" in interrupted.error
    assert interrupted.completed_at
    assert recovered._requests["processing"] == request


def test_interrupted_batch_recounts_only_persisted_results(tmp_path):
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.sqlite3")
    task = TaskDetailData(
        task_id="interrupted",
        status=TaskStatus.PROCESSING,
        progress=0.1,
        total_images=3,
        created_at="now",
        results=[
            ImageSwapResult(client_target_id="one", original_url="original", processed_url="result", status="PARTIAL"),
            ImageSwapResult(client_target_id="two", original_url="failed", status="FAILED", error="no_visible_change"),
        ],
    )
    manager.tasks[task.task_id] = task
    manager._persist(task.task_id)
    recovered = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.sqlite3")
    actual = recovered.get_task(task.task_id)
    assert actual.status == TaskStatus.FAILED
    assert actual.processed_images == 2
    assert actual.success_count == 1
    assert actual.failure_count == 1
    assert actual.progress == pytest.approx(2 / 3)
    assert actual.has_partial_failure
    assert actual.results == task.results
    assert recovered.list_task_summaries()[0].viewable_results == 1


def test_batch_history_summaries_exclude_complete_image_payloads(tmp_path):
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.sqlite3")
    task = TaskDetailData(
        task_id="completed",
        status=TaskStatus.COMPLETED,
        progress=1,
        total_images=1,
        processed_images=1,
        success_count=1,
        created_at="2026-08-31T00:00:00+00:00",
        completed_at="2026-08-31T00:00:01+00:00",
        results=[
            ImageSwapResult(
                client_target_id="target-1",
                original_url="data:image/png;base64,a-large-payload",
                processed_url="/api/v1/files/completed/res_0001.jpg",
            )
        ],
    )
    manager.tasks[task.task_id] = task

    summaries = manager.list_task_summaries()

    assert len(summaries) == 1
    assert summaries[0].viewable_results == 1
    assert "data:image" not in summaries[0].model_dump_json()


def test_batch_task_deletion_removes_snapshot_and_artifacts(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    output_dir = tmp_path / "output"
    manager = TaskManager(None, output_base_dir=output_dir, db_path=db_path)
    completed = TaskDetailData(
        task_id="completed", status=TaskStatus.COMPLETED, progress=1, total_images=1, created_at="now"
    )
    active = TaskDetailData(
        task_id="active", status=TaskStatus.PROCESSING, progress=0.5, total_images=1, created_at="now"
    )
    manager.tasks = {completed.task_id: completed, active.task_id: active}
    manager._persist(completed.task_id)
    manager._persist(active.task_id)
    artifact_dir = output_dir / completed.task_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "res_0001.jpg").write_bytes(b"result")

    with pytest.raises(ValueError, match="Active batch tasks"):
        manager.delete_task(active.task_id)
    assert manager.delete_task(completed.task_id) is True
    assert manager.get_task(completed.task_id) is None
    assert not artifact_dir.exists()

    recovered = TaskManager(None, output_base_dir=output_dir, db_path=db_path)
    assert recovered.get_task(completed.task_id) is None
    assert recovered.get_task(active.task_id) is not None


def _save_refinement(store, task):
    """Seed an old durable snapshot without ever constructing a retired generation worker."""
    store.save("refinement", task.task_id, task.model_dump(mode="json"), {"swapped_image_url": "historical"})


def test_refinement_restart_preserves_reviewable_and_accepted_without_ai(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    staging, accepted = tmp_path / "staging", tmp_path / "accepted"
    store = SQLiteTaskStore(db_path)
    content = b"candidate"
    digest = hashlib.sha256(content).hexdigest()
    candidate = staging / "reviewable" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(content)
    reviewable = RefinementTaskData(
        task_id="reviewable",
        status=RefinementTaskStatus.REVIEW_REQUIRED,
        created_at="now",
        candidate_digest=digest,
        candidate_url="candidate-url",
        revision=4,
    )
    accepted.mkdir()
    (accepted / "accepted.png").write_bytes(content)
    accepted_task = RefinementTaskData(
        task_id="accepted",
        status=RefinementTaskStatus.ACCEPTED,
        created_at="now",
        accepted_url="accepted-url",
        accepted_digest=digest,
    )
    _save_refinement(store, reviewable)
    _save_refinement(store, accepted_task)
    recovered = LegacyRefinementArchive(staging, accepted, db_path=db_path)
    assert recovered.get("reviewable").status == RefinementTaskStatus.REVIEW_REQUIRED
    assert recovered.get("reviewable").revision == 4
    assert recovered.candidate_path("reviewable") == candidate
    assert recovered.get("accepted").status == RefinementTaskStatus.ACCEPTED
    assert not (accepted / "reviewable.png").exists()


@pytest.mark.parametrize("status", ["QUEUED", "LOCALIZING", "REFINING", "CANCEL_REQUESTED"])
def test_refinement_restart_never_resumes_retired_generation(tmp_path, status):
    db_path = tmp_path / "tasks.sqlite3"
    task = RefinementTaskData(task_id="interrupted", status=status, created_at="now", revision=4)
    _save_refinement(SQLiteTaskStore(db_path), task)
    recovered = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    actual = recovered.get(task.task_id)
    assert actual.status == RefinementTaskStatus.FAILED
    assert "retired" in actual.error and "refinement session" in actual.error
    assert actual.completed_at and actual.revision == 5
    assert recovered.candidate_path(task.task_id) is None
    assert not list(recovered.accepted_dir.iterdir())


@pytest.mark.parametrize("status", ["REVIEWING", "REVIEW_REQUIRED", "ACCEPTING"])
def test_refinement_missing_or_changed_candidate_fails_closed(tmp_path, status):
    db_path = tmp_path / "tasks.sqlite3"
    task = RefinementTaskData(task_id="invalid", status=status, created_at="now", revision=7, candidate_digest="wrong")
    candidate = tmp_path / "staging" / task.task_id / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"changed")
    _save_refinement(SQLiteTaskStore(db_path), task)
    recovered = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    actual = recovered.get(task.task_id)
    assert actual.status == RefinementTaskStatus.FAILED
    assert actual.completed_at and actual.revision == 8
    assert actual.candidate_digest is None and recovered.candidate_path(task.task_id) is None
    assert not list(recovered.accepted_dir.iterdir())


def test_refinement_cancelled_state_survives_restart(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    task = RefinementTaskData(task_id="cancelled", status="CANCELLED", created_at="now")
    _save_refinement(SQLiteTaskStore(db_path), task)
    recovered = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    assert recovered.get(task.task_id).status == RefinementTaskStatus.CANCELLED


def test_only_explicit_human_accepting_record_recovers_publication(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.sqlite3"
    content = b"exact-normalized-png-bytes"
    digest = hashlib.sha256(content).hexdigest()
    candidate = tmp_path / "staging" / "human" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(content)
    task = RefinementTaskData(
        task_id="human", status="REVIEW_REQUIRED", created_at="now", revision=2, candidate_digest=digest
    )
    _save_refinement(SQLiteTaskStore(db_path), task)
    archive = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    assert not (archive.accepted_dir / "human.png").exists()

    def failed_publish(*args):
        raise OSError("simulated process loss after durable human consent")

    monkeypatch.setattr(archive, "_publish", failed_publish)
    with pytest.raises(OSError):
        asyncio.run(archive.accept(task.task_id, task.revision, digest))
    assert SQLiteTaskStore(db_path).load("refinement")[0][0]["status"] == "ACCEPTING"
    recovered = LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
    assert recovered.get(task.task_id).status == RefinementTaskStatus.ACCEPTED
    assert (recovered.accepted_dir / "human.png").read_bytes() == content
