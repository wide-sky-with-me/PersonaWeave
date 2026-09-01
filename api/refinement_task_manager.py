"""Asynchronous, atomic task manager for whole-image generative refinement."""

import asyncio
import hashlib
import os
import shutil
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import cv2

from api.inference_limiter import InferenceLimiter
from api.schemas import (
    RefinementFinalReview,
    RefinementPersonProgress,
    RefinementTaskCreateRequest,
    RefinementTaskData,
    RefinementTaskStatus,
)
from api.task_manager import load_image_from_source
from api.task_persistence import SQLiteTaskStore, default_task_db_path
from core.refinement_service import RefinementService


class RefinementTaskManager:
    """Stage complete generated images privately and publish only after human acceptance."""

    def __init__(
        self,
        service: RefinementService,
        staging_dir: str = "outputs/refinement_staging",
        accepted_dir: str = "outputs/api_results/refinement/accepted",
        db_path: str | Path | None = None,
        inference_limiter: InferenceLimiter | None = None,
    ):
        self.service = service
        self.inference_limiter = inference_limiter or InferenceLimiter(1)
        self.staging_dir = Path(staging_dir)
        self.accepted_dir = Path(accepted_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.accepted_dir.mkdir(parents=True, exist_ok=True)
        self.tasks: dict[str, RefinementTaskData] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._requests: dict[str, RefinementTaskCreateRequest] = {}
        self._runtime: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._store = SQLiteTaskStore(db_path or default_task_db_path(staging_dir, accepted_dir))
        self._load_persisted_tasks()

    def _persist(self, task_id):
        request = self._requests.get(task_id)
        self._store.save(
            "refinement",
            task_id,
            self.tasks[task_id].model_dump(mode="json"),
            request.model_dump(mode="json") if request is not None else None,
        )

    def _load_persisted_tasks(self):
        active = {
            RefinementTaskStatus.QUEUED,
            RefinementTaskStatus.LOCALIZING,
            RefinementTaskStatus.REFINING,
            RefinementTaskStatus.REVIEWING,
            RefinementTaskStatus.ACCEPTING,
            RefinementTaskStatus.CANCEL_REQUESTED,
        }
        for state, request_data in self._store.load("refinement"):
            try:
                task = RefinementTaskData.model_validate(state)
                request = RefinementTaskCreateRequest.model_validate(request_data) if request_data else None
            except (TypeError, ValueError):
                continue
            self.tasks[task.task_id] = task
            if request is not None:
                self._requests[task.task_id] = request
            self._locks[task.task_id] = asyncio.Lock()
            recovery_error = None
            if task.status == RefinementTaskStatus.ACCEPTING:
                accepted = self.accepted_dir / f"{task.task_id}.png"
                if accepted.is_file() and hashlib.sha256(accepted.read_bytes()).hexdigest() == task.candidate_digest:
                    task.status = RefinementTaskStatus.ACCEPTED
                    task.accepted_digest = task.candidate_digest
                    task.accepted_url = f"/api/v1/files/refinement/accepted/{accepted.name}"
                    task.error = None
                    task.completed_at = datetime.now(UTC).isoformat()
                    task.revision += 1
                    self._persist(task.task_id)
                    continue
                recovery_error = "Refinement acceptance was interrupted before publication; retry acceptance"
            elif task.status in active:
                recovery_error = "Refinement task was interrupted by an application restart; retry the affected person"
            elif task.status == RefinementTaskStatus.REVIEW_REQUIRED:
                candidate = self.staging_dir / task.task_id / "candidate.png"
                if not candidate.is_file():
                    recovery_error = "Refinement candidate is missing after restart; retry the affected person"
                elif hashlib.sha256(candidate.read_bytes()).hexdigest() != task.candidate_digest:
                    recovery_error = "Refinement candidate digest changed after restart; retry the affected person"
            if recovery_error:
                task.status = RefinementTaskStatus.FAILED
                task.error = recovery_error
                task.completed_at = datetime.now(UTC).isoformat()
                task.revision += 1
                self._persist(task.task_id)

    def create(self, request: RefinementTaskCreateRequest):
        task_id = f"pw_refine_{uuid.uuid4().hex[:10]}"
        people = [
            RefinementPersonProgress(person_id=person.person_id, face_index=person.face_index)
            for person in request.people
        ]
        task = RefinementTaskData(
            task_id=task_id,
            status=RefinementTaskStatus.QUEUED,
            created_at=datetime.now(UTC).isoformat(),
            people=people,
        )
        self.tasks[task_id] = task
        self._requests[task_id] = request
        self._locks[task_id] = asyncio.Lock()
        self._persist(task_id)
        self._register_worker(task_id, asyncio.create_task(self._run(task_id)))
        return task

    def _register_worker(self, task_id, worker):
        """Register one worker without allowing an older callback to remove its replacement."""
        current = self._workers.get(task_id)
        if current is not None and not current.done():
            worker.cancel()
            raise ValueError("A refinement worker is already active for this task")
        self._workers[task_id] = worker

        def discard(completed):
            if self._workers.get(task_id) is completed:
                self._workers.pop(task_id, None)

        worker.add_done_callback(discard)

    def get(self, task_id):
        return self.tasks.get(task_id)

    def candidate_path(self, task_id):
        task = self.tasks.get(task_id)
        # The candidate URL becomes public task metadata as soon as generation
        # finishes. It must therefore be readable while advisory VLM review is
        # still running as well as after the task reaches human review.
        if task is None or task.status not in {
            RefinementTaskStatus.REVIEWING,
            RefinementTaskStatus.REVIEW_REQUIRED,
        }:
            return None
        path = self.staging_dir / task_id / "candidate.png"
        return path if path.is_file() else None

    async def cancel(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        async with self._locks[task_id]:
            if task.status in {
                RefinementTaskStatus.CANCELLED,
                RefinementTaskStatus.FAILED,
                RefinementTaskStatus.ACCEPTED,
            }:
                return task
            worker = self._workers.get(task_id)
            if task.status == RefinementTaskStatus.REVIEW_REQUIRED or worker is None or worker.done():
                task.status = RefinementTaskStatus.CANCELLED
                task.error = "Refinement task was cancelled"
                task.completed_at = datetime.now(UTC).isoformat()
                task.revision += 1
                shutil.rmtree(self.staging_dir / task_id, ignore_errors=True)
                self._persist(task_id)
                return task
            task.status = RefinementTaskStatus.CANCEL_REQUESTED
            task.revision += 1
            self._persist(task_id)
            worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        return task

    async def accept(self, task_id, expected_revision, candidate_digest):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        async with self._locks[task_id]:
            if task.status == RefinementTaskStatus.ACCEPTED:
                if task.accepted_digest == candidate_digest:
                    return task
                raise ValueError("The refinement candidate changed; refresh before accepting")
            if task.revision != expected_revision or task.candidate_digest != candidate_digest:
                raise ValueError("The refinement candidate changed; refresh before accepting")
            if task.status != RefinementTaskStatus.REVIEW_REQUIRED:
                raise ValueError("Only a reviewed candidate can be accepted")
            source = self.staging_dir / task_id / "candidate.png"
            if not source.is_file():
                raise ValueError("Refinement candidate file is missing")
            actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if actual_digest != candidate_digest:
                raise ValueError("The refinement candidate file changed; refresh before accepting")
            task.status = RefinementTaskStatus.ACCEPTING
            task.revision += 1
            self._persist(task_id)
            destination = self.accepted_dir / f"{task_id}.png"
            temp = destination.with_suffix(".tmp.png")
            shutil.copyfile(source, temp)
            os.replace(temp, destination)
            task.accepted_url = f"/api/v1/files/refinement/accepted/{destination.name}"
            task.accepted_digest = actual_digest
            task.status = RefinementTaskStatus.ACCEPTED
            task.revision += 1
            task.completed_at = datetime.now(UTC).isoformat()
            self._persist(task_id)
            return task

    async def refine_again(self, task_id, person_ids, instructions=None, model_alias=None):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        async with self._locks[task_id]:
            if task.status != RefinementTaskStatus.REVIEW_REQUIRED:
                raise ValueError("Only a reviewed candidate can be refined again")
            selected_ids = list(dict.fromkeys(person_ids))
            if not selected_ids:
                raise ValueError("Select at least one person to refine again")
            known_ids = {item.person_id for item in task.people}
            if any(person_id not in known_ids for person_id in selected_ids):
                raise ValueError("A requested person is not part of this task")
            request = self._requests[task_id]
            instruction_map = instructions or {}
            for person in request.people:
                if person.person_id in selected_ids:
                    if person.person_id in instruction_map:
                        person.user_instructions = instruction_map[person.person_id]
                    progress = next(item for item in task.people if item.person_id == person.person_id)
                    progress.attempt += 1
                    progress.status = "PENDING"
                    progress.progress = 0
                    progress.error = None
            task.status = RefinementTaskStatus.QUEUED
            task.error = None
            task.candidate_url = None
            task.candidate_digest = None
            task.final_review = None
            task.completed_at = None
            task.progress = sum(item.progress for item in task.people) / len(task.people)
            task.revision += 1
            self._persist(task_id)
            worker = asyncio.create_task(
                self._run(task_id, retry_person_ids=set(selected_ids), from_candidate=True, model_alias=model_alias)
            )
            self._register_worker(task_id, worker)
            return task

    async def retry_person(self, task_id, person_id, instructions=None, model_alias=None):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task.status == RefinementTaskStatus.REVIEW_REQUIRED:
            return await self.refine_again(
                task_id,
                [person_id],
                {person_id: instructions} if instructions is not None else None,
                model_alias=model_alias,
            )
        async with self._locks[task_id]:
            if task.status != RefinementTaskStatus.FAILED:
                raise ValueError("This refinement task cannot be retried in its current state")
            progress = next((item for item in task.people if item.person_id == person_id), None)
            if progress is None:
                raise ValueError("The requested person is not part of this task")
            request = self._requests.get(task_id)
            if request is None:
                raise ValueError("The persisted refinement request is missing; this task cannot be retried")
            person = next(item for item in request.people if item.person_id == person_id)
            if instructions is not None:
                person.user_instructions = instructions
            # A failed whole-image run has no trustworthy partial result. Re-run the full
            # requested edit set even when the user clicked retry beside one person.
            unresolved_ids = {item.person_id for item in task.people}
            for item in task.people:
                if item.person_id in unresolved_ids:
                    item.attempt += 1
                    item.status = "PENDING"
                    item.progress = 0
                    item.report = None
                    item.error = None
            task.status = RefinementTaskStatus.QUEUED
            task.error = None
            task.final_review = None
            task.revision += 1
            self._persist(task_id)
            worker = asyncio.create_task(self._run(task_id, retry_person_ids=unresolved_ids, model_alias=model_alias))
            self._register_worker(task_id, worker)
            return task

    async def shutdown(self):
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _run(
        self,
        task_id,
        retry_person_ids=None,
        from_candidate=False,
        model_alias=None,
    ):
        task = self.tasks[task_id]
        request = self._requests[task_id]
        staging = self.staging_dir / task_id
        staging.mkdir(parents=True, exist_ok=True)
        try:
            loaded = await self.inference_limiter.run(self._load_inputs, request)
            runtime = self._runtime.get(task_id)
            if runtime is None:
                runtime = {
                    "original_target": loaded["original"],
                    "original_swapped": loaded["swapped"],
                    "working_swapped": loaded["swapped"],
                }
                self._runtime[task_id] = runtime
            if from_candidate:
                candidate_path = staging / "candidate.png"
                if not candidate_path.is_file():
                    raise ValueError("The prior refinement candidate is missing")
                candidate = cv2.imread(str(candidate_path), cv2.IMREAD_COLOR)
                if candidate is None:
                    raise ValueError("Could not load the prior refinement candidate")
                runtime["working_swapped"] = candidate
            selected_people = [
                person for person in request.people if retry_person_ids is None or person.person_id in retry_person_ids
            ]
            task.status = RefinementTaskStatus.REFINING
            for person in selected_people:
                progress = next(item for item in task.people if item.person_id == person.person_id)
                progress.status = "PROCESSING"
                progress.progress = 0.2
            self._persist(task_id)
            generation = await self.service.refine_whole_image(
                swapped=runtime["working_swapped"],
                people=selected_people,
                global_instructions=request.global_instructions,
                model_alias=model_alias or request.model_alias,
            )
            candidate = generation["candidate"]
            # Persist the complete provider result before advisory review. A VLM outage must
            # never discard an otherwise valid whole-image candidate.
            candidate_path = staging / "candidate.png"
            if not cv2.imwrite(str(candidate_path), candidate):
                raise ValueError("Could not persist the whole-image refinement candidate")
            digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            task.candidate_url = f"/api/v1/refinement/tasks/{task_id}/candidate"
            task.candidate_digest = digest
            task.status = RefinementTaskStatus.REVIEWING
            self._persist(task_id)

            try:
                final_review = await self.service.review_whole_candidate(
                    runtime["working_swapped"],
                    candidate,
                    selected_people,
                    request.global_instructions,
                    original_target=runtime.get("original_target"),
                )
            except Exception as review_exc:
                final_review = {
                    "overall_score": 0,
                    "coherence_score": 0,
                    "naturalness_score": 0,
                    "people": [
                        {
                            "person_id": person.person_id,
                            "status": "unverifiable",
                            "evidence": "整图质量复评暂时不可用，请直接进行人工对比。",
                        }
                        for person in selected_people
                    ],
                    "unrequested_changes_detected": False,
                    "new_high_severity_issue": False,
                    "recommendation": "review",
                    "summary": "候选已生成，但整图 VLM 复评失败；请由人工检查后决定。",
                    "issues": [],
                    "strengths": [],
                    "model_used": "",
                    "review_error": str(review_exc),
                }
            assessments = {item["person_id"]: item for item in final_review["people"]}
            for person in selected_people:
                progress = next(item for item in task.people if item.person_id == person.person_id)
                progress.report = assessments[person.person_id]
                progress.audit = generation["audit"]
                progress.progress = 1
                progress.status = "COMPLETED"
                progress.error = None
            task.final_review = RefinementFinalReview.model_validate(final_review)
            task.progress = sum(item.progress for item in task.people) / len(task.people)
            task.revision += 1
            task.status = RefinementTaskStatus.REVIEW_REQUIRED
            task.error = None
            task.progress = 1
            task.completed_at = datetime.now(UTC).isoformat()
            task.revision += 1
            self._persist(task_id)
        except asyncio.CancelledError:
            async with self._locks[task_id]:
                if task.status not in {RefinementTaskStatus.ACCEPTED, RefinementTaskStatus.CANCELLED}:
                    task.status = RefinementTaskStatus.CANCELLED
                    task.error = "Refinement task was cancelled"
                    task.completed_at = datetime.now(UTC).isoformat()
                    task.revision += 1
                    self._persist(task_id)
                shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            async with self._locks[task_id]:
                # Cancellation wins over late provider/review failures from the worker.
                if task.status in {RefinementTaskStatus.CANCEL_REQUESTED, RefinementTaskStatus.CANCELLED}:
                    task.status = RefinementTaskStatus.CANCELLED
                    task.error = "Refinement task was cancelled"
                    shutil.rmtree(staging, ignore_errors=True)
                else:
                    task.status = RefinementTaskStatus.FAILED
                    task.error = str(exc)
                task.completed_at = datetime.now(UTC).isoformat()
                task.revision += 1
                self._persist(task_id)

    @staticmethod
    def _load_inputs(request):
        return {
            "swapped": load_image_from_source(request.swapped_image_url),
            "original": load_image_from_source(request.original_image_url) if request.original_image_url else None,
        }
