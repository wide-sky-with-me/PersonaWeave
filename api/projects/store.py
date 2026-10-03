"""SQLite project documents with independent, content-addressed picture storage."""

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from core.image_security import decode_image_bytes, decode_snapshot_bytes

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


class ProjectConflict(ValueError):
    """The editor is based on a stale saved revision."""


class ProjectStore:
    """Save editor state without changing task execution or refinement acceptance."""

    def __init__(self, db_path="data/projects.sqlite3", assets_root="outputs/api_results/projects"):
        self.db_path, self.assets_root = Path(db_path), Path(assets_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL,
                    document TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_links (
                    kind TEXT NOT NULL, record_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    PRIMARY KEY(kind, record_id)
                );
            """)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _directory(self, project_id):
        if not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise ValueError("Invalid project ID")
        root = self.assets_root.resolve()
        path = (root / project_id).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Invalid project directory")
        return path

    def create(self):
        """Create an empty upload destination, saved only when a document is committed."""
        project_id = uuid4().hex
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?, ?, 0, '{}', ?)",
                (project_id, "未命名工程", datetime.now(UTC).isoformat()),
            )
        return self.get(project_id)

    def get(self, project_id):
        """Return one complete project, or raise KeyError for a missing record."""
        self._directory(project_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return {**dict(row), "document": json.loads(row["document"])}

    def list(self, limit=50):
        """Project history never carries document, source or image payloads."""
        with self._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,title,revision,updated_at FROM projects WHERE revision>0 ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def linked(self, kind, record_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM project_links WHERE kind=? AND record_id=?", (kind, record_id)
            ).fetchone()
        return self.get(row[0]) if row else None

    def linked_records(self, project_id, kind):
        """Return this project's record IDs, most recently associated first; no image payloads."""
        self.get(project_id)
        with self._connect() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT record_id FROM project_links WHERE project_id=? AND kind=? ORDER BY rowid DESC",
                    (project_id, kind),
                )
            ]

    def put_asset(self, project_id, data, *, owned_snapshot=False):
        """Validate image bytes, preserve them exactly and deduplicate within this project."""
        (decode_snapshot_bytes if owned_snapshot else decode_image_bytes)(data)
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            self.get(project_id)
            directory = self._directory(project_id)
            directory.mkdir(exist_ok=True)
            path = directory / digest
            if not path.exists():
                temporary = directory / (digest + ".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
        return {"$asset": digest}

    def asset(self, project_id, digest):
        """Read only a project's verified asset bytes, never caller-supplied filesystem paths."""
        self.get(project_id)
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid asset digest")
        directory = self._directory(project_id)
        path = (directory / digest).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise FileNotFoundError("Project image is unavailable")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Project image digest mismatch")
        return data

    def save(self, project_id, request):
        """Atomically replace an editor document after validating media and its revision."""
        serialized = json.dumps(request.document, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("Project document exceeds 4 MiB; images must use uploaded assets")
        document = request.document
        if set(document) != {"schema", "studio", "workbench", "warnings"} or document["schema"] != 1:
            raise ValueError("Unsupported project document")
        if not isinstance(document["studio"], dict) or not isinstance(document["workbench"], dict):
            raise ValueError("Invalid editor state")
        if not isinstance(document["warnings"], list) or any(not isinstance(x, str) for x in document["warnings"]):
            raise ValueError("Invalid recovery warnings")
        digests = set()

        def validate(value, depth=0):
            if depth > 30:
                raise ValueError("Project document is too deeply nested")
            if isinstance(value, dict):
                if "$asset" in value:
                    if set(value) != {"$asset"} or not isinstance(value["$asset"], str):
                        raise ValueError("Invalid media reference")
                    digests.add(value["$asset"])
                else:
                    if {"__proto__", "constructor", "prototype"}.intersection(value):
                        raise ValueError("Invalid document key")
                    for child in value.values():
                        validate(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    validate(child, depth + 1)
            elif isinstance(value, str) and value.startswith(("data:", "blob:")):
                raise ValueError("Transient media must be uploaded before saving")

        validate(document)
        with self.lock, self._connect() as conn:
            self.get(project_id)
            for digest in digests:
                self.asset(project_id, digest)
            cursor = conn.execute(
                "UPDATE projects SET title=?,document=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (request.title, serialized, datetime.now(UTC).isoformat(), project_id, request.expected_revision),
            )
            if not cursor.rowcount:
                raise ProjectConflict("Project changed in another editor; reopen it before saving")
            for kind, ids in (("batch", request.batch_ids), ("session", request.session_ids)):
                for record_id in ids:
                    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", record_id):
                        raise ValueError("Invalid linked record ID")
                    # One record keeps its original owning project, even after an explicit fork.
                    conn.execute("INSERT OR IGNORE INTO project_links VALUES (?,?,?)", (kind, record_id, project_id))
        return self.get(project_id)

    def delete(self, project_id, expected_revision):
        """Delete a saved project and only its own media, with rollback before SQL commit."""
        with self.lock:
            row = self.get(project_id)
            if row["revision"] != expected_revision:
                raise ProjectConflict("Project changed; refresh before deleting")
            directory = self._directory(project_id)
            staged = self.assets_root / (".delete-" + uuid4().hex)
            moved = directory.exists()
            if moved:
                directory.rename(staged)
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM project_links WHERE project_id=?", (project_id,))
                    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            except BaseException:
                if moved:
                    staged.rename(directory)
                raise
            if moved:
                shutil.rmtree(staged)
