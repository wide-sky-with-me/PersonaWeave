"""Read retained task evidence and accept human ratings; never produce new generation evidence.

This archive-only adapter can be removed with LegacyRefinementArchive after an
explicit historical-data retention/migration decision. New sessions do not use it.
"""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from api.tasks.persistence import SQLiteTaskStore


class QualityEvaluationStore:
    """Read old immutable evidence and transactionally save human ratings, without model or image writers."""

    def __init__(self, task_store: SQLiteTaskStore, root: Path):
        self.store = task_store
        self.root = Path(root)

    def records(self, kind, task_id):
        return [state for state, _ in self.store.load(kind) if state.get("task_id") == task_id]

    def path(self, task_id, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            return None
        records = self.records("refinement_quality_candidate", task_id)
        allowed = {value for record in records for value in record["hashes"].values() if value}
        allowed.update(record["baseline_digest"] for record in self.records("refinement_quality_generation", task_id))
        if digest not in allowed:
            return None
        base = self.root.resolve()
        path = (base / task_id / f"{digest}.png").resolve()
        if not path.is_relative_to(base) or not path.is_file():
            return None
        return path if hashlib.sha256(path.read_bytes()).hexdigest() == digest else None

    def put(self, task_id, request):
        """Transactionally check persisted revision; do not mutate refinement state."""
        with sqlite3.connect(self.store.path, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json FROM api_tasks WHERE kind='refinement' AND task_id=?", (task_id,)
            ).fetchone()
            if not row:
                raise ValueError("Refinement task is unavailable")
            state = json.loads(row[0])
            if (
                state["status"] not in {"REVIEW_REQUIRED", "ACCEPTED"}
                or state["revision"] != request.expected_revision
                or (state.get("accepted_digest") or state.get("candidate_digest")) != request.candidate_digest
            ):
                raise ValueError("Stale refinement candidate or revision")
            if {p["person_id"] for p in state["people"]} != {p.person_id for p in request.people}:
                raise ValueError("Evaluation must cover exactly the task's requested people")
            previous = connection.execute(
                "SELECT state_json FROM api_tasks WHERE kind='refinement_quality_evaluation' AND task_id=?",
                (f"{task_id}:{request.candidate_digest}",),
            ).fetchone()
            # If a later generation produces identical pixels, a later rating must not
            # rewrite whether the user considered the first generation ready.
            first_verdict = (
                request.verdict
                if state.get("generation_count") == 1
                else (json.loads(previous[0]).get("first_pass_verdict") if previous else None)
            )
            record = {
                "task_id": task_id,
                **request.model_dump(mode="json"),
                "generation_count": state.get("generation_count"),
                "first_pass_verdict": first_verdict,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            if previous:
                prior = json.loads(previous[0])
                if all(prior.get(key) == value for key, value in record.items() if key != "updated_at"):
                    return prior
            connection.execute(
                "INSERT INTO api_tasks(kind,task_id,state_json) VALUES('refinement_quality_evaluation',?,?) ON CONFLICT(kind,task_id) DO UPDATE SET state_json=excluded.state_json",
                (f"{task_id}:{request.candidate_digest}", json.dumps(record)),
            )
        return record

    def summary(self, tasks):
        """Separate all historical samples, human-rated samples, unknowns and generation failures."""
        task_ids = {task.task_id for task in tasks}
        evaluations = [
            state for state, _ in self.store.load("refinement_quality_evaluation") if state["task_id"] in task_ids
        ]
        evaluated_ids = {row["task_id"] for row in evaluations}
        known_started = [task for task in tasks if task.generation_count is not None and task.generation_count > 0]
        eligible = [task for task in known_started if (task.quality_provenance or {}).get("lineage") == "batch-local"]
        eligible_ids = {task.task_id for task in eligible}
        first_ratings = {
            row["task_id"]: row["first_pass_verdict"]
            for row in evaluations
            if row["task_id"] in eligible_ids and row.get("first_pass_verdict") is not None
        }
        ready = sum(verdict == "ready" for verdict in first_ratings.values())
        return {
            "total_samples": len(tasks),
            "evaluated_samples": sum(task.task_id in evaluated_ids for task in tasks),
            "unevaluated_samples": sum(task.task_id not in evaluated_ids for task in tasks),
            "generation_count_unknown_samples": sum(task.generation_count is None for task in tasks),
            "lineage_unknown_samples": sum(
                (task.quality_provenance or {}).get("lineage") != "batch-local" for task in tasks
            ),
            "not_generated_samples": sum(task.generation_count == 0 for task in tasks),
            "generation_failed_total": sum(
                row.get("status") == "failed"
                for row, _ in self.store.load("refinement_quality_generation")
                if row["task_id"] in task_ids
            ),
            "eligible_total": len(eligible),
            "first_pass_unevaluated": len(eligible) - len(first_ratings),
            "first_pass_ready_count": ready,
            "first_pass_denominator": len(first_ratings),
            "first_pass_acceptance_rate": ready / len(first_ratings) if first_ratings else None,
            "rate_definition": "per-refinement-task experiment: human-ready / human-rated first-generation samples with verified batch-local lineage; repeated batch images are not deduplicated; unrated and failed samples listed separately",
            "sample_unit": "refinement_task",
            "identity_satisfied": self.boolean_summary(evaluations, "identity_satisfied"),
            "naturalness_satisfied": self.boolean_summary(evaluations, "naturalness_satisfied"),
        }

    @staticmethod
    def boolean_summary(evaluations, field):
        values = [person[field] for row in evaluations for person in row["people"]]
        known = [value for value in values if value is not None]
        return {
            "true": sum(value is True for value in known),
            "false": sum(value is False for value in known),
            "unknown": len(values) - len(known),
            "rate_known_only": sum(value is True for value in known) / len(known) if known else None,
        }
