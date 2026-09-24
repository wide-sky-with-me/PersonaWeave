"""The batch boundary starts AI only with consent and freezes actual assignment/reference evidence."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from api.refinement.batch import batch_session_request, create_batch_refinement_hook
from api.schemas import BatchTaskCreateRequest, ImageSwapResult
from core.image_security import decode_data_uri


def result():
    return ImageSwapResult(
        client_target_id="target-a",
        original_url="/api/v1/files/task/original.png",
        processed_url="/api/v1/files/task/result.png",
        person_outcomes=[{"person_index": 7, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": 2}}}],
    )


def test_batch_uses_assigned_face_index_not_stable_identity_number():
    image = np.full((32, 40, 3), 77, dtype=np.uint8)
    payload = batch_session_request(
        "task", None, result(), [], [{"target_index": 7, "source_references": [{"image": image}]}]
    )
    person = payload.people[0]
    assert person.person_id == 7
    assert person.face_index == 2
    assert np.array_equal(decode_data_uri(person.source_manual_images[0]), image)
    image[:] = 0
    assert np.all(decode_data_uri(person.source_manual_images[0]) == 77)


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
