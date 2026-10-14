"""SQLite session truth and transaction orchestration over immutable artifact I/O."""

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

from api.refinement.artifacts import SessionArtifacts
from api.refinement.contracts import SessionConflict, SessionNotFound, SessionTurn
from api.refinement.deletion import staged_deletion
from core.image_security import decode_snapshot_bytes
from core.refinement.evidence import DeferredImage

_ID = re.compile(r"^[a-f0-9]{32}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 target_id TEXT NOT NULL, title TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 original_digest TEXT NOT NULL, baseline_version_id TEXT NOT NULL, accepted_version_id TEXT,
 active_turn_id TEXT, people TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), parent_version_id TEXT,
 digest TEXT NOT NULL, kind TEXT NOT NULL, model TEXT, task_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), request_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(session_id,request_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
 session_id TEXT NOT NULL REFERENCES sessions(id), digest TEXT NOT NULL, PRIMARY KEY(session_id,digest)
);
CREATE TABLE IF NOT EXISTS publications (
 session_id TEXT NOT NULL REFERENCES sessions(id), version_id TEXT NOT NULL REFERENCES versions(id),
 digest TEXT NOT NULL, PRIMARY KEY(session_id,version_id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
 revision INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_updated ON sessions(updated_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id,created_at);
CREATE INDEX IF NOT EXISTS versions_session ON versions(session_id,created_at);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id,id);
"""


def now():
    return datetime.now(UTC).isoformat()


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class RefinementSessionStore:
    """Own SQLite ownership and continuous session transactions. One server owns recovery.

    Service callers retain this entry point for snapshots, evidence and thumbnails;
    database ownership checks stay here while SessionArtifacts owns filesystem I/O.
    """

    def __init__(
        self,
        db_path="data/refinement_sessions.sqlite3",
        artifact_root="outputs/refinement_staging/sessions",
        accepted_root="outputs/api_results/refinement/accepted/sessions",
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._artifacts = SessionArtifacts(artifact_root, accepted_root)
        self.artifact_root = self._artifacts.artifact_root
        self.accepted_root = self._artifacts.accepted_root
        self._mutex = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self.recover()

    @contextmanager
    def _connect(self, write=False):
        with self._mutex:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _session(conn, session_id):
        if not _ID.fullmatch(session_id):
            raise SessionNotFound("Session not found")
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionNotFound("Session not found")
        return row

    @staticmethod
    def _revision(row, expected):
        if row["revision"] != expected:
            raise SessionConflict("Session changed; refresh before continuing")

    @staticmethod
    def _touch(conn, session_id, kind):
        timestamp = now()
        conn.execute("UPDATE sessions SET revision=revision+1,updated_at=? WHERE id=?", (timestamp, session_id))
        conn.execute(
            "INSERT INTO events(session_id,revision,kind,created_at) SELECT id,revision,?,? FROM sessions WHERE id=?",
            (kind, timestamp, session_id),
        )

    @staticmethod
    def artifact_url(session_id, digest):
        return f"/api/v1/refinement/sessions/{session_id}/artifacts/{digest}"

    def snapshot(self, session_id, image):
        """Freeze evidence before registration; failed creation discards only its own attempt."""
        return self._artifacts.snapshot(session_id, image)

    def _check_artifact(self, session_id, digest):
        """Authorize the database reference independently of filesystem presence."""
        with self._connect() as conn:
            self._session(conn, session_id)
            if not conn.execute(
                "SELECT 1 FROM artifacts WHERE session_id=? AND digest=?", (session_id, digest)
            ).fetchone():
                raise SessionNotFound("Artifact not found")

    def artifact_bytes(self, session_id, digest):
        """Read only registered session artifacts, verifying their exact committed bytes."""
        self._check_artifact(session_id, digest)
        return self._artifacts.read(session_id, digest)

    def image(self, session_id, digest):
        return decode_snapshot_bytes(self.artifact_bytes(session_id, digest))

    def cached_thumbnail(self, session_id, digest):
        """Authorize the source before the low-memory cache read used by the service."""
        self._check_artifact(session_id, digest)
        return self._artifacts.cached_thumbnail(session_id, digest)

    def thumbnail_bytes(self, session_id, digest):
        """Rebuild a missing preview via the service's memory queue and authorized image loader."""
        cached = self.cached_thumbnail(session_id, digest)
        if cached is not None:
            return cached
        return self._artifacts.write_thumbnail(session_id, digest, self.image(session_id, digest))

    def evidence(self, session_id, digest):
        """Read only a verified PNG header now; decode pixels when a stage needs them."""
        shape = self._artifacts.image_shape(self.artifact_bytes(session_id, digest))
        return DeferredImage(shape, partial(self.image, session_id, digest))

    def find_creation(self, request_id, request_hash):
        with self._connect() as conn:
            row = conn.execute("SELECT id,request_hash FROM sessions WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise SessionConflict("Creation request_id was already used for a different request")
        return self.get(row["id"])

    def create(self, session_id, request, request_hash, original_digest, baseline_digest, people):
        with self._connect(write=True) as conn:
            existing = conn.execute(
                "SELECT id,request_hash FROM sessions WHERE request_id=?", (request.request_id,)
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise SessionConflict("Creation request_id was already used for a different request")
                return self.get(existing["id"])
            timestamp = now()
            baseline_id = uuid4().hex
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?, ?,?,?,?, ?,?,?)",
                (
                    session_id,
                    request.request_id,
                    request_hash,
                    request.target_id,
                    request.title,
                    1,
                    "idle",
                    original_digest,
                    baseline_id,
                    None,
                    None,
                    json.dumps(people, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)",
                (baseline_id, session_id, None, baseline_digest, "baseline", None, None, timestamp),
            )
            digests = {original_digest, baseline_digest}
            digests.update(digest for person in people for digest in person["reference_digests"])
            conn.executemany("INSERT INTO artifacts VALUES (?,?)", [(session_id, digest) for digest in digests])
            conn.execute(
                "INSERT INTO events(session_id,revision,kind,created_at) VALUES (?,1,?,?)",
                (session_id, "created", timestamp),
            )
        return self.get(session_id)

    def get(self, session_id):
        with self._connect() as conn:
            row = self._session(conn, session_id)
            versions = [
                dict(item)
                for item in conn.execute(
                    "SELECT * FROM versions WHERE session_id=? ORDER BY created_at,id", (session_id,)
                )
            ]
            turns = [
                SessionTurn.model_validate(json.loads(item["data"])).model_dump()
                for item in conn.execute(
                    "SELECT data FROM turns WHERE session_id=? ORDER BY created_at,id", (session_id,)
                )
            ]
            cursor = conn.execute(
                "SELECT coalesce(max(id),0) FROM events WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        people = []
        for person in json.loads(row["people"]):
            people.append({key: value for key, value in person.items() if key != "reference_digests"})
            people[-1]["reference_urls"] = [
                self.artifact_url(session_id, digest) for digest in person["reference_digests"]
            ]
            people[-1]["reference_thumbnail_urls"] = [url + "/thumbnail" for url in people[-1]["reference_urls"]]
        for version in versions:
            version.pop("session_id")
            version["image_url"] = self.artifact_url(session_id, version["digest"])
            version["thumbnail_url"] = version["image_url"] + "/thumbnail"
        return {
            "id": row["id"],
            "target_id": row["target_id"],
            "title": row["title"],
            "revision": row["revision"],
            "status": row["status"],
            "original_url": self.artifact_url(session_id, row["original_digest"]),
            "baseline_version_id": row["baseline_version_id"],
            "accepted_version_id": row["accepted_version_id"],
            "accepted_image_url": self.accepted_url(session_id, row["accepted_version_id"])
            if row["accepted_version_id"]
            else None,
            "active_turn_id": row["active_turn_id"],
            "people": people,
            "versions": versions,
            "turns": turns,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "event_cursor": cursor,
        }

    def list(self, limit=30, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id,target_id,title,revision,status,accepted_version_id,active_turn_id,created_at,updated_at,
                (SELECT count(*) FROM versions WHERE session_id=sessions.id) AS version_count,
                (SELECT count(*) FROM turns WHERE session_id=sessions.id) AS turn_count
                FROM sessions ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def matching_accepted_sessions(self, target_id, original_digest, baseline_digest):
        """Find bounded evidence-matching candidates for explicit project-link repair, without loading turns."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.id,s.people FROM sessions s JOIN versions v ON v.id=s.baseline_version_id
                WHERE s.target_id=? AND s.original_digest=? AND v.digest=? AND s.accepted_version_id IS NOT NULL
                LIMIT 101""",
                (target_id, original_digest, baseline_digest),
            ).fetchall()
        if len(rows) > 100:
            raise ValueError("Too many matching sessions to resolve an editor association")
        return [{"id": row["id"], "people": json.loads(row["people"])} for row in rows]

    def existing_turn(self, session_id, request_id, request_hash):
        with self._connect() as conn:
            self._session(conn, session_id)
            row = conn.execute(
                "SELECT request_hash FROM turns WHERE session_id=? AND request_id=?", (session_id, request_id)
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise SessionConflict("Turn request_id was already used for a different request")
        return self.get(session_id)

    def queue_turn(self, session_id, request, request_hash, model_alias):
        with self._connect(write=True) as conn:
            row = self._session(conn, session_id)
            previous = conn.execute(
                "SELECT id,request_hash FROM turns WHERE session_id=? AND request_id=?",
                (session_id, request.request_id),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise SessionConflict("Turn request_id was already used for a different request")
                return previous["id"], False
            self._revision(row, request.expected_revision)
            if row["active_turn_id"]:
                raise SessionConflict("A turn is already running in this session")
            if not conn.execute(
                "SELECT 1 FROM versions WHERE session_id=? AND id=?", (session_id, request.base_version_id)
            ).fetchone():
                raise SessionNotFound("Base version not found in this session")
            if conn.execute("SELECT count(*) FROM turns WHERE session_id=?", (session_id,)).fetchone()[0] >= 200:
                raise SessionConflict("Session turn limit reached; start a new session from the chosen version")
            if request.diagnosis_turn_id is not None:
                # Confirmation names immutable evidence, never a mutable "latest" pointer.
                confirmed = conn.execute(
                    "SELECT data FROM turns WHERE session_id=? AND id=?",
                    (session_id, request.diagnosis_turn_id),
                ).fetchone()
                if confirmed is None:
                    raise SessionNotFound("Confirmed analysis not found in this session")
                analysis = json.loads(confirmed["data"])
                if (
                    analysis["base_version_id"] != request.base_version_id
                    or analysis["status"] != "completed"
                    or analysis["mode"] != "discuss"
                    or not analysis.get("diagnosis")
                    or analysis["diagnosis"].get("mapping_uncertain")
                ):
                    raise SessionConflict("Confirm a completed, unambiguous analysis of this exact base")
            turn_id, timestamp = uuid4().hex, now()
            data = {
                "id": turn_id,
                "request_id": request.request_id,
                "base_version_id": request.base_version_id,
                "mode": request.mode,
                "message": request.message,
                "model_alias": model_alias,
                "diagnosis_turn_id": request.diagnosis_turn_id,
                "status": "queued",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            conn.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?)",
                (
                    turn_id,
                    session_id,
                    request.request_id,
                    request_hash,
                    json.dumps(data, ensure_ascii=False),
                    timestamp,
                ),
            )
            conn.execute("UPDATE sessions SET status='running',active_turn_id=? WHERE id=?", (turn_id, session_id))
            self._touch(conn, session_id, "turn_queued")
        return turn_id, True

    def inputs(self, session_id, turn_id):
        with self._connect() as conn:
            session = self._session(conn, session_id)
            row = conn.execute("SELECT data FROM turns WHERE session_id=? AND id=?", (session_id, turn_id)).fetchone()
            if row is None:
                raise SessionNotFound("Turn not found")
            turn = json.loads(row["data"])
            version = conn.execute(
                "SELECT digest FROM versions WHERE session_id=? AND id=?", (session_id, turn["base_version_id"])
            ).fetchone()
        return dict(session), turn, version["digest"]

    def update_turn(self, session_id, turn_id, *, terminal=False, allow_cancel=False, **changes):
        with self._connect(write=True) as conn:
            session = self._session(conn, session_id)
            if session["active_turn_id"] != turn_id:
                return False
            row = conn.execute("SELECT data FROM turns WHERE id=? AND session_id=?", (turn_id, session_id)).fetchone()
            data = json.loads(row["data"])
            if data["status"] == "cancel_requested" and not allow_cancel:
                return False
            data.update(changes, updated_at=now())
            conn.execute("UPDATE turns SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), turn_id))
            if terminal:
                conn.execute("UPDATE sessions SET status='idle',active_turn_id=NULL WHERE id=?", (session_id,))
            self._touch(conn, session_id, data["status"])
        return True

    def add_candidate(self, session_id, turn_id, digest, metadata, *, allow_cancel=False):
        with self._connect(write=True) as conn:
            session = self._session(conn, session_id)
            row = conn.execute("SELECT data FROM turns WHERE id=? AND session_id=?", (turn_id, session_id)).fetchone()
            data = json.loads(row["data"])
            if session["active_turn_id"] != turn_id or (data["status"] == "cancel_requested" and not allow_cancel):
                return None
            version_id, timestamp = uuid4().hex, now()
            conn.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (session_id, digest))
            model = metadata.get("model")
            conn.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    session_id,
                    data["base_version_id"],
                    digest,
                    "candidate",
                    str(model)[:500] if model else None,
                    turn_id,
                    timestamp,
                ),
            )
            public_keys = {
                "model_alias",
                "model",
                "provider",
                "request_id",
                "generation_mode",
                "input_image_count",
                "requested_output_size",
                "people_count",
                "prompt_version",
                "provider_output_size",
                "target_canvas_size",
                "canvas_normalization",
                "generation_policy",
            }
            audit = {key: metadata[key] for key in public_keys if key in metadata}
            # Usage providers may attach arbitrary response data. Keep only numeric counters.
            usage = metadata.get("usage") or {}
            audit["usage"] = {
                key: value
                for key, value in usage.items()
                if key
                in {
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "cached_tokens",
                }
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            }
            prompt = metadata.get("prompt")
            if isinstance(prompt, str):
                audit["prompt_digest"] = hashlib.sha256(prompt.encode()).hexdigest()
                data["generation_prompt"] = prompt
            data.update(
                status="cancel_requested" if data["status"] == "cancel_requested" else "reviewing",
                candidate_version_id=version_id,
                updated_at=timestamp,
                generation_metadata=audit,
            )
            conn.execute("UPDATE turns SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), turn_id))
            self._touch(conn, session_id, "candidate_ready")
            return version_id

    def request_cancel(self, session_id, expected_revision):
        with self._connect(write=True) as conn:
            session = self._session(conn, session_id)
            if not session["active_turn_id"]:
                self._revision(session, expected_revision)
                return None
            turn_id = session["active_turn_id"]
            data = json.loads(conn.execute("SELECT data FROM turns WHERE id=?", (turn_id,)).fetchone()[0])
            if data["status"] == "cancel_requested":
                return turn_id
            self._revision(session, expected_revision)
            data.update(status="cancel_requested", updated_at=now())
            conn.execute("UPDATE turns SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), turn_id))
            self._touch(conn, session_id, "cancel_requested")
            return turn_id

    @staticmethod
    def accepted_url(session_id, version_id):
        return f"/api/v1/files/refinement/accepted/sessions/{session_id}-{version_id}.png"

    def accept(self, session_id, expected_revision, version_id, candidate_digest):
        """Validate ownership/revision/digest and atomically publish exactly the checked bytes."""
        created = False
        # Exit after rollback ownership checks, matching the lifetime of the former
        # local temporary file. Publication I/O never commits session state itself.
        with ExitStack() as files:
            try:
                with self._connect(write=True) as conn:
                    session = self._session(conn, session_id)
                    version = conn.execute(
                        "SELECT * FROM versions WHERE session_id=? AND id=?", (session_id, version_id)
                    ).fetchone()
                    if version is None:
                        raise SessionNotFound("Version not found in this session")
                    if version["digest"] != candidate_digest:
                        raise SessionConflict("Candidate digest changed; refresh before accepting")
                    data = self._artifacts.read(session_id, candidate_digest)
                    destination = self._artifacts.publication_path(session_id, version_id)
                    if session["accepted_version_id"] == version_id:
                        self._artifacts.verified_bytes(destination, candidate_digest)
                        return self.get(session_id)
                    self._revision(session, expected_revision)
                    if session["active_turn_id"]:
                        raise SessionConflict("Wait for or cancel the running turn before accepting")
                    created = files.enter_context(
                        self._artifacts.publish(session_id, version_id, candidate_digest, data)
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO publications VALUES (?,?,?)", (session_id, version_id, candidate_digest)
                    )
                    conn.execute("UPDATE sessions SET accepted_version_id=? WHERE id=?", (version_id, session_id))
                    self._touch(conn, session_id, "accepted")
            except BaseException:
                if created:
                    # A concurrent retry may have committed after our rollback. Serialize
                    # cleanup with writers and never unlink a now-owned publication.
                    with self._connect(write=True) as cleanup:
                        owned = cleanup.execute(
                            "SELECT 1 FROM publications WHERE session_id=? AND version_id=?",
                            (session_id, version_id),
                        ).fetchone()
                        if owned is None:
                            self._artifacts.remove_publication(session_id, version_id)
                raise
        return self.get(session_id)

    def delete(self, session_id, expected_revision):
        """Delete an idle session and its own images; reject stale or active commands.

        File moves and SQLite commit are serialized with all session writers. A
        failed commit restores artifacts, and missing files do not block removal.
        """
        with (
            self._mutex,
            staged_deletion(self.artifact_root.parent / ".deletion_quarantine") as stage,
            self._connect(write=True) as conn,
        ):
            session = self._session(conn, session_id)
            self._revision(session, expected_revision)
            if session["status"] != "idle" or session["active_turn_id"]:
                raise SessionConflict("Running sessions cannot be deleted; wait or cancel first")
            stage(self._artifacts.deletion_paths(session_id))
            for table in ("publications", "artifacts", "events", "turns", "versions"):
                conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def events(self, session_id, after=0, limit=100):
        with self._connect() as conn:
            self._session(conn, session_id)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,revision,kind,created_at FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                    (session_id, after, min(max(limit, 1), 100)),
                )
            ]

    def discard_unregistered(self, session_id):
        """Remove only an unregistered creation attempt, serialized against registration."""
        if not _ID.fullmatch(session_id):
            raise SessionNotFound("Session not found")
        with self._connect(write=True) as conn:
            if conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                return
            self._artifacts.discard_session(session_id)

    def recover(self):
        """Never retry an uncertain paid call; retain any already durable generated candidate."""
        with self._connect(write=True) as conn:
            for session in conn.execute("SELECT * FROM sessions WHERE active_turn_id IS NOT NULL").fetchall():
                row = conn.execute("SELECT data FROM turns WHERE id=?", (session["active_turn_id"],)).fetchone()
                if row:
                    data = json.loads(row["data"])
                    if data.get("candidate_version_id"):
                        data.update(
                            status="review_required",
                            review_error="Review interrupted by restart; candidate retained for human review",
                        )
                    else:
                        data.update(
                            status="cancelled" if data["status"] == "cancel_requested" else "failed",
                            error="Execution interrupted by restart; no model request was automatically repeated",
                        )
                    data["updated_at"] = now()
                    conn.execute(
                        "UPDATE turns SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), data["id"])
                    )
                conn.execute("UPDATE sessions SET active_turn_id=NULL,status='idle' WHERE id=?", (session["id"],))
                self._touch(conn, session["id"], "recovered")
            publications = {
                f"{row[0]}-{row[1]}.png" for row in conn.execute("SELECT session_id,version_id FROM publications")
            }
        self._artifacts.recover_publications(publications)
