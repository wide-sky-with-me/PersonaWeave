"""Small SQLite-backed store for recoverable API task state."""

import json
import sqlite3
from pathlib import Path


def default_task_db_path(*manager_dirs: str | Path) -> Path:
    """Choose the production DB, or keep temporary test outputs self-contained."""
    paths = [Path(value) for value in manager_dirs]
    for path in paths:
        if "pytest-" in str(path) or str(path).startswith("/tmp/"):
            return path / "tasks.sqlite3"
    return Path("data/task/tasks.sqlite3")


class SQLiteTaskStore:
    """Persist JSON task snapshots and optional requests using atomic upserts."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_tasks (
                    kind TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    request_json TEXT,
                    PRIMARY KEY (kind, task_id)
                )
                """
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def save(self, kind: str, task_id: str, state: dict, request: dict | None = None):
        """Commit one complete public-state/request snapshot in one transaction."""
        state_json = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
        request_json = json.dumps(request, separators=(",", ":"), ensure_ascii=False) if request is not None else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_tasks(kind, task_id, state_json, request_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind, task_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    request_json = COALESCE(excluded.request_json, api_tasks.request_json)
                """,
                (kind, task_id, state_json, request_json),
            )

    def load(self, kind: str) -> list[tuple[dict, dict | None]]:
        """Load snapshots in insertion order; invalid JSON rows are ignored."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json, request_json FROM api_tasks WHERE kind = ? ORDER BY rowid", (kind,)
            ).fetchall()
        loaded = []
        for state_json, request_json in rows:
            try:
                loaded.append((json.loads(state_json), json.loads(request_json) if request_json else None))
            except (TypeError, json.JSONDecodeError):
                continue
        return loaded
