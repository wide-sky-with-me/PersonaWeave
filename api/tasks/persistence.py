"""Small SQLite-backed store for recoverable API task state."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
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

    def iter_states(self, kind: str) -> Iterator[dict]:
        """Read states one at a time without loading historical image requests.

        Close each cursor before yielding: restart recovery may update the same
        database, and must not hold a read transaction across those writes.
        """
        row_id = 0
        with closing(self._connect()) as connection:
            while True:
                row = connection.execute(
                    "SELECT rowid, state_json FROM api_tasks WHERE kind = ? AND rowid > ? ORDER BY rowid LIMIT 1",
                    (kind, row_id),
                ).fetchone()
                if row is None:
                    return
                row_id, state_json = row
                try:
                    state = json.loads(state_json)
                except (TypeError, json.JSONDecodeError):
                    continue
                yield state

    def load_request(self, kind: str, task_id: str) -> dict | None:
        """Read one immutable request for explicit recovery or result provenance."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT request_json FROM api_tasks WHERE kind = ? AND task_id = ?", (kind, task_id)
            ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def load_state(self, kind: str, task_id: str) -> dict | None:
        """Read one complete historical state only when its detail is requested."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_json FROM api_tasks WHERE kind = ? AND task_id = ?", (kind, task_id)
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def delete(self, kind: str, task_id: str) -> bool:
        """Delete one persisted task snapshot and report whether it existed."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM api_tasks WHERE kind = ? AND task_id = ?",
                (kind, task_id),
            )
        return cursor.rowcount > 0
