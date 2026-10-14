"""The batch boundary starts AI only with consent and freezes actual assignment/reference evidence."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from api.media import encode_image_to_base64
from api.refinement.batch import batch_session_request, create_batch_refinement_hook
from api.schemas import BatchTaskCreateRequest, ImageSwapResult, TaskStatus
from api.tasks.batch import TaskManager
from core.pipeline import SwapResult


def result():
    return ImageSwapResult(
        client_target_id="target-a",
        original_url="/api/v1/files/task/original.png",
        processed_url="/api/v1/files/task/result.png",
        person_outcomes=[{"person_index": 7, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": 2}}}],
    )


def test_batch_uses_assigned_face_index_not_stable_identity_number():
    frozen = "/api/v1/files/pw_task_test/references/" + "a" * 64 + ".png"
    payload = batch_session_request(
        "task", None, result(), [], [{"target_index": 7, "source_references": [{"snapshot_url": frozen}]}]
    )
    person = payload.people[0]
    assert person.person_id == 7
    assert person.face_index == 2
    assert person.source_manual_images == [frozen]
    assert "data:image" not in payload.model_dump_json()


def test_missing_assignment_never_guesses_person_location():
    output = result()
    output.person_outcomes[0]["diagnostics"]["matching"]["face_index"] = None
    with pytest.raises(ValueError, match="confirmed face"):
        batch_session_request("task", None, output, [], [])


def test_other_batch_roles_absent_from_this_image_do_not_block_refinement():
    output = result()
    output.person_outcomes.append(
        {"person_index": 8, "status": "SKIPPED", "diagnostics": {"matching": {"face_index": None}}}
    )
    payload = batch_session_request("task", None, output, [], [])
    assert [(person.person_id, person.face_index) for person in payload.people] == [(7, 2)]
    output.person_outcomes = output.person_outcomes[1:]
    with pytest.raises(ValueError, match="confirmed face"):
        batch_session_request("task", None, output, [], [])


def test_batch_creation_and_turn_use_stable_idempotency_keys():
    class Sessions:
        def __init__(self):
            self.calls = []

        async def create_session(self, request):
            self.calls.append(request)
            return {"id": "a" * 32, "revision": 1, "baseline_version_id": "b" * 32}

        async def submit_turn(self, session_id, request):
            self.calls.append(request)

        async def wait_until_idle(self, session_id):
            assert session_id == "a" * 32

    sessions = Sessions()
    hook = create_batch_refinement_hook(sessions)
    output = result()
    asyncio.run(hook("task", SimpleNamespace(ai_refinement=True), output, [], []))
    asyncio.run(hook("task", SimpleNamespace(ai_refinement=True), output, [], []))
    assert sessions.calls[0].request_id == sessions.calls[2].request_id
    assert sessions.calls[1].request_id == sessions.calls[3].request_id
    assert sessions.calls[1].mode == "auto"
    assert sessions.calls[1].base_version_id == "b" * 32
    assert output.refinement_session_id == "a" * 32


def test_batch_model_work_is_opt_in():
    request = BatchTaskCreateRequest(batch_images=[{"client_target_id": "a", "image_url": "https://example.com/a.png"}])
    assert request.ai_refinement is False


def test_batch_waits_for_existing_ai_turn_after_persisting_session_association():
    async def exercise():
        release = asyncio.Event()
        associated = []

        class Sessions:
            async def create_session(self, request):
                return {"id": "a" * 32, "revision": 1, "baseline_version_id": "b" * 32}

            async def submit_turn(self, session_id, request):
                assert associated == [session_id]

            async def wait_until_idle(self, session_id):
                await release.wait()

        output = result()
        task = asyncio.create_task(
            create_batch_refinement_hook(Sessions())(
                "task", None, output, [], [], on_session_created=lambda: associated.append(output.refinement_session_id)
            )
        )
        for _ in range(100):
            if associated:
                break
            await asyncio.sleep(0.005)
        assert associated and not task.done()
        release.set()
        await task

    asyncio.run(exercise())


def local_batch_fixture(tmp_path, *, total=1):
    """Only the local task store and generated PNGs are real; no detector or AI runtime is loaded."""
    image = np.full((32, 32, 3), 100, np.uint8)
    source = encode_image_to_base64(image, ".png")

    def process_image(**kwargs):
        return SwapResult(
            output_image=kwargs["image"] + 40,
            faces_detected=2,
            faces_swapped=1,
            matched_similarities=[0.9],
            elapsed_time_sec=0.01,
            swapped_person_indices=[7],
            requested_person_indices=[7],
            person_outcomes=[{"person_index": 7, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": 2}}}],
        )

    manager = TaskManager(
        SimpleNamespace(process_image=process_image),
        output_base_dir=tmp_path / "outputs",
        db_path=tmp_path / "tasks.sqlite3",
    )
    request = BatchTaskCreateRequest(
        ai_refinement=True,
        batch_images=[{"client_target_id": f"target-{index}", "image_url": source} for index in range(total)],
    )
    return manager, request


@pytest.mark.parametrize("failure", ["create", "submit", "wait"])
def test_failed_followup_preserves_local_artifacts_and_durable_association(tmp_path, failure):
    manager, request = local_batch_fixture(tmp_path)

    class Sessions:
        async def create_session(self, payload):
            assert [(p.person_id, p.face_index) for p in payload.people] == [(7, 2)]
            if failure == "create":
                raise ValueError("fixture creation failure")
            return {"id": "a" * 32, "revision": 1, "baseline_version_id": "b" * 32}

        async def submit_turn(self, _session_id, _request):
            if failure == "submit":
                raise ValueError("fixture turn failure")

        async def wait_until_idle(self, _session_id):
            if failure == "wait":
                raise ValueError("fixture wait failure")

    manager.refinement_hook = create_batch_refinement_hook(Sessions())

    async def exercise():
        task_id, _ = manager.create_batch_task(request)
        await asyncio.gather(*manager._background_tasks)
        return task_id

    task_id = asyncio.run(exercise())
    restored = TaskManager(None, output_base_dir=manager.output_base_dir, db_path=tmp_path / "tasks.sqlite3")
    for current in (manager, restored):
        task = current.get_task(task_id)
        output = task.results[0]
        assert task.status == TaskStatus.COMPLETED and task.success_count == 1
        assert output.status == "SUCCESS" and output.processed_url
        assert output.original_url.endswith("original_0001.png")
        assert output.refinement_session_id == (None if failure == "create" else "a" * 32)
        assert output.refinement_error and "fixture" not in output.refinement_error
        assert output.person_outcomes[0]["person_index"] == 7
        assert current._requests == {}
    assert (tmp_path / "outputs" / task_id / "original_0001.png").is_file()
    assert (tmp_path / "outputs" / task_id / "res_0001.jpg").is_file()


def test_cancelled_followup_keeps_finished_result_and_never_starts_next_image(tmp_path):
    manager, request = local_batch_fixture(tmp_path, total=2)

    async def exercise():
        waiting = asyncio.Event()

        class Sessions:
            async def create_session(self, _payload):
                return {"id": "a" * 32, "revision": 1, "baseline_version_id": "b" * 32}

            async def submit_turn(self, _session_id, _request):
                pass

            async def wait_until_idle(self, _session_id):
                waiting.set()
                await asyncio.Future()

        manager.refinement_hook = create_batch_refinement_hook(Sessions())
        task_id, _ = manager.create_batch_task(request)
        worker = next(iter(manager._background_tasks))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        persisted = manager._store.load_state("batch", task_id)
        assert persisted["results"][0]["refinement_session_id"] == "a" * 32
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        return task_id

    task_id = asyncio.run(exercise())
    restored = TaskManager(None, output_base_dir=manager.output_base_dir, db_path=tmp_path / "tasks.sqlite3")
    for current in (manager, restored):
        task = current.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.processed_images == task.success_count == 1 and task.failure_count == 0
        assert task.has_partial_failure and task.waiting_reason is None
        assert "CancelledError" in task.error
        assert len(task.results) == 1 and current._requests == {}
        output = task.results[0]
        assert output.status == "SUCCESS" and output.refinement_session_id == "a" * 32
        assert output.refinement_error is None
        assert output.person_outcomes[0]["diagnostics"]["matching"]["face_index"] == 2
    artifacts = tmp_path / "outputs" / task_id
    assert sorted(path.name for path in artifacts.iterdir()) == ["original_0001.png", "res_0001.jpg"]
