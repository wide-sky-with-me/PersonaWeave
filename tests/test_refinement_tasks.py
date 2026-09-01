"""Lifecycle tests for whole-image asynchronous refinement tasks."""

import asyncio

import cv2
import numpy as np
import pytest

from api.inference_limiter import InferenceLimiter
from api.refinement_task_manager import RefinementTaskManager
from api.schemas import RefinementPersonRequest, RefinementTaskCreateRequest, RefinementTaskStatus


class FakeService:
    def __init__(self, block=False, recommendation="accept", review_error=None):
        self.block = block
        self.recommendation = recommendation
        self.review_error = review_error
        self.generation_calls = 0
        self.generation_inputs = []
        self.generation_people = []
        self.generation_models = []
        self.review_baselines = []

    async def refine_whole_image(self, swapped, people, global_instructions, model_alias=None):
        self.generation_calls += 1
        self.generation_inputs.append(swapped.copy())
        self.generation_people.append([person.person_id for person in people])
        self.generation_models.append(model_alias)
        if self.block:
            await asyncio.sleep(10)
        candidate = swapped.copy()
        candidate[:, :] = 30 + self.generation_calls
        return {
            "candidate": candidate,
            "audit": {
                "model": model_alias or "fake-gemini",
                "provider": "fake",
                "generation_mode": "whole-image-single-call",
                "input_image_count": 1,
            },
        }

    async def review_whole_candidate(self, baseline, candidate, people, global_instructions="", original_target=None):
        self.review_baselines.append(baseline.copy())
        if self.review_error:
            raise RuntimeError(self.review_error)
        return {
            "overall_score": 88,
            "coherence_score": 87,
            "naturalness_score": 86,
            "people": [
                {"person_id": person.person_id, "status": "improved", "evidence": "可见瑕疵已经改善"}
                for person in people
            ],
            "unrequested_changes_detected": self.recommendation == "regenerate",
            "new_high_severity_issue": False,
            "recommendation": self.recommendation,
            "summary": "整图质量审查完成，结果供人工判断。",
            "issues": ["背景细节可能有变化"] if self.recommendation == "regenerate" else [],
            "strengths": ["整体协调性改善"],
            "model_used": "fake-vlm",
        }


class BlockingReviewService(FakeService):
    """Hold advisory review open so candidate visibility can be tested."""

    def __init__(self):
        super().__init__()
        self.review_started = asyncio.Event()
        self.release_review = asyncio.Event()

    async def review_whole_candidate(self, baseline, candidate, people, global_instructions="", original_target=None):
        self.review_started.set()
        await self.release_review.wait()
        return await super().review_whole_candidate(
            baseline, candidate, people, global_instructions, original_target=original_target
        )


def request(people=(1, 2)):
    return RefinementTaskCreateRequest(
        swapped_image_url="swapped",
        people=[
            RefinementPersonRequest(
                person_id=index,
                face_index=index,
                person_locator=f"画面中第 {index} 个人物",
                user_instructions="修复可见换脸瑕疵",
            )
            for index in people
        ],
    )


def fake_input(_request):
    swapped = np.zeros((12, 12, 3), dtype=np.uint8)
    return {"swapped": swapped, "original": None}


async def settle(manager, task_id):
    worker = manager._workers.get(task_id)
    if worker:
        await worker
    return manager.get(task_id)


def manager(tmp_path, service=None, limiter=None):
    class ImmediateLimiter:
        async def run(self, function, /, *args, **kwargs):
            return function(*args, **kwargs)

    value = RefinementTaskManager(
        service or FakeService(),
        staging_dir=tmp_path / "staging",
        accepted_dir=tmp_path / "accepted",
        inference_limiter=limiter or ImmediateLimiter(),
    )
    value._load_inputs = fake_input
    return value


def test_load_inputs_requires_only_whole_image():
    swapped = np.ones((4, 4, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", swapped)
    assert ok
    req = RefinementTaskCreateRequest(
        swapped_image_url="data:image/png;base64," + __import__("base64").b64encode(encoded).decode(),
        people=[RefinementPersonRequest(person_id=1, face_index=1)],
    )

    loaded = RefinementTaskManager._load_inputs(req)

    assert loaded["swapped"].shape == (4, 4, 3)
    assert loaded["original"] is None


def test_load_inputs_accepts_optional_complete_original_for_vlm_comparison():
    swapped = np.ones((4, 4, 3), dtype=np.uint8)
    original = np.full((4, 4, 3), 2, dtype=np.uint8)
    ok, swapped_encoded = cv2.imencode(".png", swapped)
    original_ok, original_encoded = cv2.imencode(".png", original)
    assert ok and original_ok

    def encode(value):
        return "data:image/png;base64," + __import__("base64").b64encode(value).decode()

    req = RefinementTaskCreateRequest(
        swapped_image_url=encode(swapped_encoded),
        original_image_url=encode(original_encoded),
        people=[RefinementPersonRequest(person_id=1, face_index=1)],
    )

    loaded = RefinementTaskManager._load_inputs(req)

    assert np.array_equal(loaded["swapped"], swapped)
    assert np.array_equal(loaded["original"], original)


def test_only_secure_loading_uses_shared_local_limiter(tmp_path):
    class RecordingLimiter(InferenceLimiter):
        def __init__(self):
            super().__init__(1)
            self.calls = []

        async def run(self, function, /, *args, **kwargs):
            self.calls.append(function)
            return function(*args, **kwargs)

    async def scenario():
        limiter = RecordingLimiter()
        value = manager(tmp_path, limiter=limiter)
        task = value.create(request((1,)))
        await settle(value, task.task_id)
        assert value._load_inputs in limiter.calls
        assert len(limiter.calls) == 1

    asyncio.run(scenario())


def test_provider_whole_image_is_staged_directly(tmp_path):
    async def scenario():
        value = manager(tmp_path)
        task = value.create(request((1, 2)))
        task = await settle(value, task.task_id)
        staged = cv2.imread(str(tmp_path / "staging" / task.task_id / "candidate.png"))
        assert task.status == RefinementTaskStatus.REVIEW_REQUIRED
        assert np.all(staged == 31)
        assert task.final_review.recommendation == "accept"
        assert [person.report["status"] for person in task.people] == ["improved", "improved"]
        assert not list((tmp_path / "accepted").glob("*.png"))

    asyncio.run(scenario())


def test_staged_candidate_is_readable_during_advisory_review(tmp_path):
    async def scenario():
        service = BlockingReviewService()
        value = manager(tmp_path, service)
        task = value.create(request((1,)))
        await service.review_started.wait()

        assert task.status == RefinementTaskStatus.REVIEWING
        assert task.candidate_url
        assert value.candidate_path(task.task_id).is_file()

        service.release_review.set()
        task = await settle(value, task.task_id)
        assert task.status == RefinementTaskStatus.REVIEW_REQUIRED

    asyncio.run(scenario())


def test_initial_generation_uses_and_persists_selected_model(tmp_path):
    async def scenario():
        service = FakeService()
        value = manager(tmp_path, service)
        selected = request((1,))
        selected.model_alias = "personaweave-refine-seedream"
        task = await settle(value, value.create(selected).task_id)

        assert task.status == RefinementTaskStatus.REVIEW_REQUIRED
        assert service.generation_models == ["personaweave-refine-seedream"]
        persisted = value._store.load("refinement")
        assert persisted[0][1]["model_alias"] == "personaweave-refine-seedream"

    asyncio.run(scenario())


def test_human_can_accept_even_when_vlm_recommends_regeneration(tmp_path):
    async def scenario():
        value = manager(tmp_path, FakeService(recommendation="regenerate"))
        task = value.create(request((1,)))
        task = await settle(value, task.task_id)
        assert task.final_review.recommendation == "regenerate"
        accepted = await value.accept(task.task_id, task.revision, task.candidate_digest)
        assert accepted.status == RefinementTaskStatus.ACCEPTED
        assert accepted.accepted_digest == task.candidate_digest
        assert len(list((tmp_path / "accepted").glob("*.png"))) == 1

    asyncio.run(scenario())


def test_review_failure_keeps_candidate_available_for_human_acceptance(tmp_path):
    async def scenario():
        value = manager(tmp_path, FakeService(review_error="temporary VLM outage"))
        task = await settle(value, value.create(request((1,))).task_id)
        assert task.status == RefinementTaskStatus.REVIEW_REQUIRED
        assert task.final_review.recommendation == "review"
        assert task.final_review.review_error == "temporary VLM outage"
        staged = tmp_path / "staging" / task.task_id / "candidate.png"
        assert staged.is_file()
        accepted = await value.accept(task.task_id, task.revision, task.candidate_digest)
        assert accepted.status == RefinementTaskStatus.ACCEPTED

    asyncio.run(scenario())


def test_accept_requires_review_revision_digest_and_rehash(tmp_path):
    async def scenario():
        value = manager(tmp_path)
        task = value.create(request((1,)))
        task = await settle(value, task.task_id)
        revision, digest = task.revision, task.candidate_digest
        with pytest.raises(ValueError, match="changed; refresh"):
            await value.accept(task.task_id, revision - 1, digest)
        (tmp_path / "staging" / task.task_id / "candidate.png").write_bytes(b"tampered")
        with pytest.raises(ValueError, match="file changed"):
            await value.accept(task.task_id, revision, digest)

    asyncio.run(scenario())


def test_accept_is_idempotent_for_same_digest(tmp_path):
    async def scenario():
        value = manager(tmp_path)
        task = await settle(value, value.create(request((1,))).task_id)
        accepted = await value.accept(task.task_id, task.revision, task.candidate_digest)
        replay = await value.accept(task.task_id, accepted.revision - 1, accepted.accepted_digest)
        assert replay.status == RefinementTaskStatus.ACCEPTED
        with pytest.raises(ValueError, match="changed; refresh"):
            await value.accept(task.task_id, accepted.revision, "different")

    asyncio.run(scenario())


def test_reviewed_candidate_can_be_refined_from_prior_whole_image(tmp_path):
    async def scenario():
        service = FakeService()
        value = manager(tmp_path, service)
        task = await settle(value, value.create(request((1, 2))).task_id)
        first_digest = task.candidate_digest
        task = await value.refine_again(task.task_id, [1], {1: "继续修复眼睛"})
        task = await settle(value, task.task_id)
        assert task.status == RefinementTaskStatus.REVIEW_REQUIRED
        assert task.people[0].attempt == 2
        assert service.generation_people == [[1, 2], [1]]
        assert np.all(service.generation_inputs[1] == 31)
        assert task.candidate_digest != first_digest
        assert service.review_baselines[-1].mean() == 31

    asyncio.run(scenario())


def test_failed_retry_reruns_complete_requested_set(tmp_path):
    async def scenario():
        service = FakeService()
        value = manager(tmp_path, service)
        task = value.create(request((1, 2)))
        await asyncio.sleep(0)
        worker = value._workers[task.task_id]
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        task.status = RefinementTaskStatus.FAILED
        retried = await value.retry_person(task.task_id, 1)
        await settle(value, retried.task_id)
        assert service.generation_people[-1] == [1, 2]

    asyncio.run(scenario())


def test_cancel_discards_staging_and_never_publishes(tmp_path):
    async def scenario():
        value = manager(tmp_path, FakeService(block=True))
        task = value.create(request((1,)))
        await asyncio.sleep(0)
        await value.cancel(task.task_id)
        task = await settle(value, task.task_id)
        assert task.status == RefinementTaskStatus.CANCELLED
        assert not (tmp_path / "staging" / task.task_id).exists()
        assert not list((tmp_path / "accepted").glob("*.png"))

    asyncio.run(scenario())


def test_cancel_review_required_cleans_staging(tmp_path):
    async def scenario():
        value = manager(tmp_path)
        task = await settle(value, value.create(request((1,))).task_id)
        cancelled = await value.cancel(task.task_id)
        assert cancelled.status == RefinementTaskStatus.CANCELLED
        assert not (tmp_path / "staging" / task.task_id).exists()

    asyncio.run(scenario())


def test_concurrent_refine_again_registers_only_one_worker(tmp_path):
    async def scenario():
        service = FakeService()
        value = manager(tmp_path, service)
        task = await settle(value, value.create(request((1,))).task_id)
        service.block = True
        results = await asyncio.gather(
            value.refine_again(task.task_id, [1]),
            value.refine_again(task.task_id, [1]),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ValueError) for result in results) == 1
        await value.cancel(task.task_id)

    asyncio.run(scenario())
