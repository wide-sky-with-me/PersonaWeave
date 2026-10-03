"""Read/accept archive for pre-session refinement tasks; it never starts model work.

Remove with the legacy HTTP routes once retained old tasks/candidates have been
exported or deleted. New work must go through RefinementSessionService.
"""

import asyncio
import hashlib
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from api.refinement.deletion import staged_deletion
from api.refinement.legacy_quality import QualityEvaluationStore
from api.schemas import RefinementTaskData, RefinementTaskStatus
from api.tasks.persistence import SQLiteTaskStore, default_task_db_path


class LegacyRefinementArchive:
    """Keep existing history and explicitly approved candidate bytes usable without AI credentials."""

    def __init__(
        self,
        staging_dir="outputs/refinement_staging",
        accepted_dir="outputs/api_results/refinement/accepted",
        db_path=None,
    ):
        self.staging_dir = Path(staging_dir)
        self.accepted_dir = Path(accepted_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.accepted_dir.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteTaskStore(db_path or default_task_db_path(staging_dir, accepted_dir))
        self.quality = QualityEvaluationStore(self._store, self.staging_dir.parent / "refinement_evidence")
        self.tasks = {}
        self._locks = {}
        self._restore()

    def _path(self, root, task_id, filename=None):
        if not task_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in task_id
        ):
            raise ValueError("Invalid historical task identifier")
        path = root / task_id / filename if filename else root / f"{task_id}.png"
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Historical artifact escapes its root")
        return path

    def _persist(self, task):
        self._store.save("refinement", task.task_id, task.model_dump(mode="json"))

    def _restore(self):
        for state in self._store.iter_states("refinement"):
            try:
                task = RefinementTaskData.model_validate(state)
                candidate = self._path(self.staging_dir, task.task_id, "candidate.png")
                accepted = self._path(self.accepted_dir, task.task_id)
            except ValueError:
                continue
            self.tasks[task.task_id] = task
            self._locks[task.task_id] = asyncio.Lock()
            if task.status == RefinementTaskStatus.ACCEPTING:
                # The old durable ACCEPTING record is already an explicit human approval.
                if accepted.is_file() and self._digest(accepted) == task.candidate_digest:
                    self._finish_accept(task)
                elif candidate.is_file() and self._digest(candidate) == task.candidate_digest:
                    self._publish(task, candidate.read_bytes())
                else:
                    self._fail(task, "Historical accepted candidate is missing or changed")
            elif task.status in {RefinementTaskStatus.REVIEWING, RefinementTaskStatus.REVIEW_REQUIRED}:
                if candidate.is_file() and self._digest(candidate) == task.candidate_digest:
                    if task.status == RefinementTaskStatus.REVIEWING:
                        task.status = RefinementTaskStatus.REVIEW_REQUIRED
                        task.progress = 1
                        task.revision += 1
                        self._persist(task)
                else:
                    self._fail(task, "Historical candidate is missing or changed")
            elif task.status in {
                RefinementTaskStatus.QUEUED,
                RefinementTaskStatus.LOCALIZING,
                RefinementTaskStatus.REFINING,
                RefinementTaskStatus.CANCEL_REQUESTED,
            }:
                self._fail(task, "Legacy execution retired; open a refinement session to start new work")

    @staticmethod
    def _digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _fail(self, task, message):
        task.status = RefinementTaskStatus.FAILED
        task.error = message
        task.candidate_url = None
        task.candidate_digest = None
        task.completed_at = datetime.now(UTC).isoformat()
        task.revision += 1
        self._persist(task)

    def _finish_accept(self, task):
        task.status = RefinementTaskStatus.ACCEPTED
        task.accepted_digest = task.candidate_digest
        task.accepted_url = f"/api/v1/files/refinement/accepted/{task.task_id}.png"
        task.candidate_url = None
        task.error = None
        task.completed_at = datetime.now(UTC).isoformat()
        task.revision += 1
        self._persist(task)
        # Retired tasks have no version branches: accepted bytes now live in publication/evidence.
        directory = self._path(self.staging_dir, task.task_id, "candidate.png").parent
        if directory.exists():
            shutil.rmtree(directory)

    def _publish(self, task, content):
        source = self._path(self.staging_dir, task.task_id, "publish.tmp.png")
        destination = self._path(self.accepted_dir, task.task_id)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        os.replace(source, destination)
        self._finish_accept(task)

    def get(self, task_id):
        return self.tasks.get(task_id)

    def list(self, limit=30, offset=0):
        """Return bounded metadata only; image evidence and requests belong to explicit detail reads."""
        tasks = sorted(self.tasks.values(), key=lambda task: (task.created_at, task.task_id), reverse=True)
        return [
            {
                "task_id": task.task_id,
                "status": task.status.value,
                "created_at": task.created_at,
                "completed_at": task.completed_at,
                "revision": task.revision,
                "has_candidate": self.candidate_path(task.task_id) is not None,
                "has_accepted": self._verified_path(self.accepted_dir, task.task_id, task.accepted_digest) is not None,
            }
            for task in tasks[max(0, offset) : max(0, offset) + min(max(1, limit), 100)]
        ]

    def candidate_path(self, task_id):
        task = self.get(task_id)
        if not task or task.status != RefinementTaskStatus.REVIEW_REQUIRED:
            return None
        return self._verified_path(self.staging_dir, task_id, task.candidate_digest, "candidate.png")

    def _verified_path(self, root, task_id, digest, filename=None):
        """Missing, corrupt or escaped artifacts are unavailable, not a failure to list historical facts."""
        try:
            path = self._path(root, task_id, filename)
            return path if digest and path.is_file() and self._digest(path) == digest else None
        except (OSError, ValueError):
            return None

    async def accept(self, task_id, expected_revision, candidate_digest):
        task = self.get(task_id)
        if task is None:
            return None
        async with self._locks[task_id]:
            if task.status == RefinementTaskStatus.ACCEPTED and task.accepted_digest == candidate_digest:
                path = self._path(self.accepted_dir, task_id)
                if path.is_file() and self._digest(path) == candidate_digest:
                    return task
                raise ValueError("Published historical artifact is missing or changed")
            if task.revision != expected_revision or task.candidate_digest != candidate_digest:
                raise ValueError("The historical candidate changed; refresh before accepting")
            path = self.candidate_path(task_id)
            if path is None:
                raise ValueError("No valid historical candidate is available")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != candidate_digest:
                raise ValueError("The historical candidate changed; refresh before accepting")
            task.status = RefinementTaskStatus.ACCEPTING
            task.revision += 1
            self._persist(task)
            # Failure leaves ACCEPTING for deterministic recovery; never generate or re-encode.
            self._publish(task, content)
            return task

    def quality_detail(self, task_id):
        task = self.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task_id,
            "generation_count": task.generation_count,
            "provenance": task.quality_provenance,
            "current_candidate_digest": task.accepted_digest or task.candidate_digest,
            "evaluations": self.quality.records("refinement_quality_evaluation", task_id),
            "candidates": self.quality.records("refinement_quality_candidate", task_id),
            "generations": self.quality.records("refinement_quality_generation", task_id),
        }

    async def delete(self, task_id, expected_revision):
        """Delete one retained archive and its quality records; never touch new sessions."""
        lock = self._locks.get(task_id)
        if lock is None:
            return False
        async with lock:
            task = self.get(task_id)
            if task is None:
                return False
            if task.revision != expected_revision or task.status not in {
                RefinementTaskStatus.REVIEW_REQUIRED,
                RefinementTaskStatus.ACCEPTED,
                RefinementTaskStatus.FAILED,
                RefinementTaskStatus.CANCELLED,
            }:
                raise ValueError("Historical record changed or is active; refresh before deleting")
            # Validate ID without following a potentially hostile record directory symlink.
            if (
                task_id == "sessions"
                or not task_id
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in task_id)
            ):
                raise ValueError("Invalid historical task identifier")
            with (
                staged_deletion(self.staging_dir / ".deletion_quarantine") as stage,
                sqlite3.connect(self._store.path, timeout=30) as conn,
            ):
                conn.execute("BEGIN IMMEDIATE")
                stage(
                    [
                        (self.staging_dir, self.staging_dir / task_id),
                        (self.accepted_dir, self.accepted_dir / f"{task_id}.png"),
                        (self.quality.root, self.quality.root / task_id),
                    ]
                )
                conn.execute("DELETE FROM api_tasks WHERE kind='refinement' AND task_id=?", (task_id,))
                # Exact owner matching avoids prefix collisions and wildcard identifiers.
                conn.execute(
                    "DELETE FROM api_tasks WHERE kind IN (?, ?, ?, ?) AND json_valid(state_json) "
                    "AND json_extract(state_json, '$.task_id')=?",
                    (
                        "refinement_quality_initial",
                        "refinement_quality_generation",
                        "refinement_quality_candidate",
                        "refinement_quality_evaluation",
                        task_id,
                    ),
                )
            self.tasks.pop(task_id, None)
            self._locks.pop(task_id, None)
            return True

    async def evaluate_quality(self, task_id, evaluation):
        if self.get(task_id) is None:
            return None
        async with self._locks[task_id]:
            return self.quality.put(task_id, evaluation)

    async def shutdown(self):
        """No workers exist in this archive; shutdown deliberately performs no model work."""
