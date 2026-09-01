"""Restart recovery coverage for persisted batch and refinement tasks."""

import asyncio
import hashlib

from api.refinement_task_manager import RefinementTaskManager
from api.schemas import (
    BatchTaskCreateRequest,
    ImageSwapResult,
    RefinementPersonRequest,
    RefinementTaskCreateRequest,
    RefinementTaskData,
    RefinementTaskStatus,
    TaskDetailData,
    TaskStatus,
)
from api.task_manager import TaskManager


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


class StubService:
    pass


def _refinement_request():
    return RefinementTaskCreateRequest(
        swapped_image_url="https://example.com/swapped.png",
        people=[RefinementPersonRequest(person_id=1, face_index=1)],
    )


def _save_refinement(manager, task, request):
    manager.tasks[task.task_id] = task
    manager._requests[task.task_id] = request
    manager._persist(task.task_id)


def test_refinement_restart_preserves_valid_review_candidate_and_accepted(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    staging = tmp_path / "staging"
    accepted = tmp_path / "accepted"
    manager = RefinementTaskManager(StubService(), staging, accepted, db_path=db_path)
    request = _refinement_request()
    candidate = staging / "reviewable" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    reviewable = RefinementTaskData(
        task_id="reviewable",
        status=RefinementTaskStatus.REVIEW_REQUIRED,
        created_at="now",
        candidate_digest=digest,
        candidate_url="candidate-url",
        final_review={
            "overall_score": 80,
            "coherence_score": 80,
            "naturalness_score": 80,
            "people": [{"person_id": 1, "status": "improved", "evidence": "可见问题已经改善"}],
            "unrequested_changes_detected": False,
            "new_high_severity_issue": False,
            "recommendation": "review",
            "summary": "候选已完成整图复评，等待人工确认。",
        },
    )
    accepted_task = RefinementTaskData(
        task_id="accepted", status=RefinementTaskStatus.ACCEPTED, created_at="now", accepted_url="accepted-url"
    )
    _save_refinement(manager, reviewable, request)
    _save_refinement(manager, accepted_task, request)

    recovered = RefinementTaskManager(StubService(), staging, accepted, db_path=db_path)

    assert recovered.get("reviewable").status == RefinementTaskStatus.REVIEW_REQUIRED
    assert recovered.candidate_path("reviewable") == candidate
    assert recovered.get("accepted").status == RefinementTaskStatus.ACCEPTED


def test_refinement_restart_fails_interrupted_and_invalid_candidate(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    staging = tmp_path / "staging"
    accepted = tmp_path / "accepted"
    manager = RefinementTaskManager(StubService(), staging, accepted, db_path=db_path)
    request = _refinement_request()
    interrupted = RefinementTaskData(
        task_id="interrupted", status=RefinementTaskStatus.REFINING, created_at="now", revision=4
    )
    invalid = RefinementTaskData(
        task_id="invalid",
        status=RefinementTaskStatus.REVIEW_REQUIRED,
        created_at="now",
        candidate_digest="wrong",
        revision=7,
    )
    candidate = staging / "invalid" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"changed")
    _save_refinement(manager, interrupted, request)
    _save_refinement(manager, invalid, request)

    recovered = RefinementTaskManager(StubService(), staging, accepted, db_path=db_path)

    for task_id, previous_revision in (("interrupted", 4), ("invalid", 7)):
        task = recovered.get(task_id)
        assert task.status == RefinementTaskStatus.FAILED
        assert "restart" in task.error
        assert task.completed_at
        assert task.revision == previous_revision + 1
        assert task_id not in recovered._runtime
        assert task_id not in recovered._workers


def test_refinement_cancelled_state_survives_restart(tmp_path):
    async def scenario():
        db_path = tmp_path / "tasks.sqlite3"
        manager = RefinementTaskManager(StubService(), tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
        task = RefinementTaskData(task_id="cancelled", status=RefinementTaskStatus.CANCELLED, created_at="now")
        _save_refinement(manager, task, _refinement_request())
        recovered = RefinementTaskManager(StubService(), tmp_path / "staging", tmp_path / "accepted", db_path=db_path)
        assert recovered.get("cancelled").status == RefinementTaskStatus.CANCELLED

    asyncio.run(scenario())
