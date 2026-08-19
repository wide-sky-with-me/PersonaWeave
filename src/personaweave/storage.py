import hashlib
import json
import mimetypes
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from personaweave.assets import AssetResolver, UnsafeAssetPath

from personaweave.domain import (
    AssetKind,
    BatchStatus,
    CompiledBatchContract,
    JobPhase,
    JobStage,
    JobStatus,
    MappingAction,
    MappingDecision,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(
        self,
        database_path: Path,
        formal_mask_root: Path | None = None,
    ):
        self.database_path = database_path
        self.formal_mask_root = formal_mask_root or database_path.parent
        self.formal_mask_root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        committed = False
        try:
            yield connection
            connection.commit()
            committed = True
        finally:
            connection.close()
        if committed:
            self._drain_mask_cleanup_queue()

    @contextmanager
    def _formal_mask_mutation(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            yield connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task_instruction TEXT NOT NULL DEFAULT '',
                    max_iterations INTEGER NOT NULL DEFAULT 3,
                    compiled_contract_json TEXT,
                    compiled_contract_hash TEXT,
                    contract_confirmed_at TEXT,
                    requires_manual_review INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS identities (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    display_code TEXT,
                    label TEXT NOT NULL,
                    target_profile_json TEXT,
                    reference_sufficiency_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    identity_id TEXT REFERENCES identities(id) ON DELETE CASCADE,
                    parent_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    display_code TEXT,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS persons (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    cluster_key TEXT NOT NULL,
                    display_code TEXT,
                    label TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(batch_id, cluster_key)
                );

                CREATE TABLE IF NOT EXISTS face_instances (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
                    external_face_id TEXT NOT NULL,
                    bbox_json TEXT NOT NULL,
                    detection_confidence REAL NOT NULL,
                    identity_confidence REAL,
                    needs_confirmation INTEGER NOT NULL,
                    appearance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(asset_id, external_face_id)
                );

                CREATE TABLE IF NOT EXISTS mappings (
                    person_id TEXT PRIMARY KEY REFERENCES persons(id) ON DELETE CASCADE,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    target_identity_id TEXT REFERENCES identities(id) ON DELETE RESTRICT,
                    instruction TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    provider TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    phase TEXT NOT NULL DEFAULT 'queued',
                    current_iteration INTEGER NOT NULL DEFAULT 0,
                    total_iterations INTEGER NOT NULL DEFAULT 0,
                    progress_message TEXT,
                    phase_started_at TEXT,
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, stage, item_key, attempt)
                );

                CREATE TABLE IF NOT EXISTS display_code_counters (
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    prefix TEXT NOT NULL,
                    next_number INTEGER NOT NULL,
                    PRIMARY KEY(batch_id, prefix)
                );

                CREATE TABLE IF NOT EXISTS library_items (
                    id TEXT PRIMARY KEY,
                    display_code TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_assets (
                    id TEXT PRIMARY KEY,
                    library_item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_code_counter (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    next_number INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS formal_masks (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    source_asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    storage_uri TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    review_reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS formal_mask_cleanup (
                    storage_uri TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                """
            )
            asset_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(assets)").fetchall()
            }
            if "parent_asset_id" not in asset_columns:
                connection.execute(
                    """ALTER TABLE assets ADD COLUMN parent_asset_id TEXT
                    REFERENCES assets(id) ON DELETE SET NULL"""
                )
            migrations = {
                "batches": {
                    "task_instruction": "TEXT NOT NULL DEFAULT ''",
                    "max_iterations": "INTEGER NOT NULL DEFAULT 3",
                    "compiled_contract_json": "TEXT",
                    "compiled_contract_hash": "TEXT",
                    "contract_confirmed_at": "TEXT",
                    "requires_manual_review": "INTEGER NOT NULL DEFAULT 0",
                    "deleted_at": "TEXT",
                },
                "identities": {
                    "display_code": "TEXT",
                    "target_profile_json": "TEXT",
                    "reference_sufficiency_json": "TEXT",
                },
                "assets": {"display_code": "TEXT"},
                "persons": {"display_code": "TEXT"},
                "face_instances": {"appearance_json": "TEXT NOT NULL DEFAULT '{}'"},
                "mappings": {"instruction": "TEXT NOT NULL DEFAULT ''"},
                "jobs": {
                    "phase": "TEXT NOT NULL DEFAULT 'queued'",
                    "current_iteration": "INTEGER NOT NULL DEFAULT 0",
                    "total_iterations": "INTEGER NOT NULL DEFAULT 0",
                    "progress_message": "TEXT",
                    "phase_started_at": "TEXT",
                },
            }
            for table, columns in migrations.items():
                existing = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, definition in columns.items():
                    if column not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

            connection.execute(
                """UPDATE jobs
                SET phase = CASE status
                    WHEN 'queued' THEN 'queued'
                    WHEN 'running' THEN 'generating'
                    WHEN 'succeeded' THEN 'completed'
                    WHEN 'needs_review' THEN 'needs_review'
                    ELSE 'failed'
                END
                WHERE phase IS NULL OR phase = '' OR (phase = 'queued' AND status != 'queued')"""
            )
            connection.execute(
                """UPDATE jobs
                SET total_iterations = CASE
                    WHEN stage = 'swap' THEN COALESCE(
                        CAST(json_extract(input_json, '$.max_iterations') AS INTEGER), 1
                    )
                    ELSE 1
                END
                WHERE total_iterations = 0"""
            )
            connection.execute(
                """UPDATE jobs
                SET current_iteration = COALESCE(
                    CAST(json_extract(output_json, '$.completed_iterations') AS INTEGER),
                    current_iteration
                )
                WHERE stage = 'swap' AND current_iteration = 0 AND output_json IS NOT NULL"""
            )
            connection.execute(
                """UPDATE jobs
                SET progress_message = CASE status
                    WHEN 'queued' THEN '等待执行'
                    WHEN 'running' THEN '执行进程中断，请重试当前任务'
                    WHEN 'succeeded' THEN '处理完成'
                    WHEN 'needs_review' THEN '需要人工检查'
                    ELSE '处理失败'
                END
                WHERE progress_message IS NULL OR progress_message = ''"""
            )

            legacy_ready_batches = connection.execute(
                """SELECT id FROM batches
                WHERE status = ?
                  AND (
                    COALESCE(compiled_contract_json, '') = ''
                    OR COALESCE(compiled_contract_hash, '') = ''
                    OR COALESCE(contract_confirmed_at, '') = ''
                  )""",
                (BatchStatus.READY,),
            ).fetchall()
            for legacy_batch in legacy_ready_batches:
                batch_id = legacy_batch["id"]
                connection.execute(
                    "DELETE FROM mappings WHERE batch_id = ?",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE persons SET confirmed = 0 WHERE batch_id = ?",
                    (batch_id,),
                )
                connection.execute(
                    """UPDATE identities
                    SET target_profile_json = NULL,
                        reference_sufficiency_json = NULL
                    WHERE batch_id = ?""",
                    (batch_id,),
                )
                connection.execute(
                    """UPDATE batches
                    SET status = CASE WHEN EXISTS (
                            SELECT 1 FROM jobs
                            WHERE jobs.batch_id = batches.id
                              AND jobs.stage = ?
                              AND jobs.status = ?
                        )
                        THEN ? ELSE ? END,
                        compiled_contract_json = NULL,
                        compiled_contract_hash = NULL,
                        contract_confirmed_at = NULL,
                        requires_manual_review = 0,
                        updated_at = ?
                    WHERE id = ?""",
                    (
                        JobStage.ANALYZE,
                        JobStatus.SUCCEEDED,
                        BatchStatus.AWAITING_CONFIRMATION,
                        BatchStatus.DRAFT,
                        utc_now(),
                        batch_id,
                    ),
                )

            for batch in connection.execute("SELECT id FROM batches ORDER BY created_at").fetchall():
                batch_id = batch["id"]
                code_groups = [
                    ("assets", "kind = 'source'", "I"),
                    ("assets", "kind = 'reference'", "R"),
                    ("assets", "kind = 'result'", "O"),
                    ("identities", "1 = 1", "T"),
                    ("persons", "1 = 1", "P"),
                ]
                for table, condition, prefix in code_groups:
                    rows = connection.execute(
                        f"SELECT id, display_code FROM {table} "
                        f"WHERE batch_id = ? AND {condition} ORDER BY created_at",
                        (batch_id,),
                    ).fetchall()
                    used = {
                        int(row["display_code"][1:])
                        for row in rows
                        if row["display_code"]
                        and row["display_code"].startswith(prefix)
                        and row["display_code"][1:].isdigit()
                    }
                    next_number = max(used, default=0) + 1
                    for row in rows:
                        if row["display_code"]:
                            continue
                        connection.execute(
                            f"UPDATE {table} SET display_code = ? WHERE id = ?",
                            (f"{prefix}{next_number:02d}", row["id"]),
                        )
                        next_number += 1
                    connection.execute(
                        """INSERT INTO display_code_counters(batch_id, prefix, next_number)
                        VALUES (?, ?, ?)
                        ON CONFLICT(batch_id, prefix) DO UPDATE SET
                            next_number = MAX(display_code_counters.next_number, excluded.next_number)""",
                        (batch_id, prefix, next_number),
                    )

            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_batch_display_code "
                "ON assets(batch_id, display_code)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_identities_batch_display_code "
                "ON identities(batch_id, display_code)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_persons_batch_display_code "
                "ON persons(batch_id, display_code)"
            )

    def recover_interrupted_jobs(self) -> int:
        """Make jobs left running by a stopped worker explicitly retryable."""
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs
                SET status = ?, phase = ?, error_code = ?, error_message = ?,
                    progress_message = ?, updated_at = ?
                WHERE status = ?""",
                (
                    JobStatus.FAILED,
                    JobPhase.FAILED,
                    "worker_restarted",
                    "任务执行进程已重启；可安全重试，已完成的网关结果将通过幂等键复用",
                    "执行进程已重启，请重试当前任务",
                    now,
                    JobStatus.RUNNING,
                ),
            )
        return cursor.rowcount

    @staticmethod
    def _next_display_code(
        connection: sqlite3.Connection,
        table: str,
        batch_id: str,
        prefix: str,
    ) -> str:
        counter = connection.execute(
            f"""INSERT INTO display_code_counters(batch_id, prefix, next_number)
            SELECT ?, ?, COALESCE(
                MAX(CAST(SUBSTR(display_code, 2) AS INTEGER)),
                0
            ) + 2
            FROM {table}
            WHERE batch_id = ? AND display_code GLOB ?
            ON CONFLICT(batch_id, prefix) DO UPDATE
            SET next_number = display_code_counters.next_number + 1
            RETURNING next_number - 1 AS number""",
            (batch_id, prefix, batch_id, f"{prefix}[0-9]*"),
        ).fetchone()
        if counter is None:
            raise RuntimeError("display code allocation returned no number")
        number = counter["number"]
        return f"{prefix}{number:02d}"

    @staticmethod
    def _decode_batch(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        batch = dict(row)
        raw_contract = batch.pop("compiled_contract_json", None)
        batch["compiled_contract"] = json.loads(raw_contract) if raw_contract else None
        batch["contract_hash"] = batch.pop("compiled_contract_hash", None)
        batch["confirmed_at"] = batch.pop("contract_confirmed_at", None)
        # Additive legacy column is only a compatibility cache; confirmed
        # contract evidence always recomputes the public boolean.
        batch.pop("requires_manual_review", None)
        batch["requires_manual_review"] = bool(
            CompiledBatchContract.model_validate(
                batch["compiled_contract"]
            ).requires_manual_review
            if batch["compiled_contract"]
            else False
        )
        return batch

    @staticmethod
    def _decode_identity(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        identity = dict(row)
        raw_profile = identity.pop("target_profile_json", None)
        raw_assessment = identity.pop("reference_sufficiency_json", None)
        identity["target_profile"] = json.loads(raw_profile) if raw_profile else None
        identity["reference_sufficiency"] = (
            json.loads(raw_assessment) if raw_assessment else None
        )
        return identity

    def _invalidate_confirmation(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
        *,
        preserve_mask_uris: set[str] | None = None,
    ) -> bool:
        """Invalidate pre-generation confirmation without disturbing active/history states."""
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            "SELECT status FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise KeyError(batch_id)
        if batch["status"] not in {
            BatchStatus.DRAFT,
            BatchStatus.AWAITING_CONFIRMATION,
            BatchStatus.READY,
            BatchStatus.FAILED,
        }:
            raise ValueError("contract inputs are locked after generation starts")
        has_analysis_history = connection.execute(
            "SELECT 1 FROM jobs WHERE batch_id = ? AND stage = ? AND status = ? LIMIT 1",
            (batch_id, JobStage.ANALYZE, JobStatus.SUCCEEDED),
        ).fetchone() is not None
        next_status = (
            BatchStatus.AWAITING_CONFIRMATION
            if has_analysis_history
            else BatchStatus.DRAFT
        )
        connection.execute("DELETE FROM mappings WHERE batch_id = ?", (batch_id,))
        connection.execute(
            "UPDATE persons SET confirmed = 0 WHERE batch_id = ?",
            (batch_id,),
        )
        connection.execute(
            """UPDATE identities
            SET target_profile_json = NULL, reference_sufficiency_json = NULL
            WHERE batch_id = ?""",
            (batch_id,),
        )
        preserve = preserve_mask_uris or set()
        mask_rows = connection.execute(
            "SELECT id, storage_uri FROM formal_masks WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        mask_ids_to_delete = []
        for row in mask_rows:
            uri = row["storage_uri"]
            if uri not in preserve:
                connection.execute(
                    "INSERT OR IGNORE INTO formal_mask_cleanup(storage_uri, created_at) VALUES (?, ?)",
                    (uri, utc_now()),
                )
            mask_ids_to_delete.append(row["id"])
        if mask_ids_to_delete:
            placeholders = ",".join("?" for _ in mask_ids_to_delete)
            connection.execute(
                f"DELETE FROM formal_masks WHERE id IN ({placeholders})",
                tuple(mask_ids_to_delete),
            )
        connection.execute(
            """UPDATE batches
            SET status = ?, compiled_contract_json = NULL,
                compiled_contract_hash = NULL, contract_confirmed_at = NULL,
                requires_manual_review = 0, updated_at = ?
            WHERE id = ?""",
            (next_status, utc_now(), batch_id),
        )
        return True

    def _drain_mask_cleanup_queue(self) -> None:
        """Delete committed mask files without ever escaping the configured root."""
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            try:
                rows = connection.execute(
                    "SELECT storage_uri FROM formal_mask_cleanup ORDER BY created_at LIMIT 100"
                ).fetchall()
            except sqlite3.OperationalError:
                return
            resolver = AssetResolver(self.formal_mask_root)
            for row in rows:
                uri = row["storage_uri"]
                still_referenced = connection.execute(
                    "SELECT 1 FROM formal_masks WHERE storage_uri = ? LIMIT 1",
                    (uri,),
                ).fetchone()
                if still_referenced is not None:
                    connection.execute(
                        "DELETE FROM formal_mask_cleanup WHERE storage_uri = ?",
                        (uri,),
                    )
                    continue
                try:
                    local_path = resolver.local_path(uri)
                    if local_path is not None and local_path.exists():
                        if not local_path.is_file():
                            raise OSError("formal mask cleanup target is not a file")
                        local_path.unlink()
                except UnsafeAssetPath:
                    # Never retain or act on an unsafe cleanup request.
                    pass
                except OSError:
                    # Keep the queue item so a later committed operation can retry.
                    continue
                connection.execute(
                    "DELETE FROM formal_mask_cleanup WHERE storage_uri = ?",
                    (uri,),
                )
            connection.commit()
        except sqlite3.Error:
            # Cleanup is retryable and must not turn an already committed
            # domain mutation into an apparent failure.
            return
        finally:
            connection.close()



    def create_batch(
        self,
        name: str,
        task_instruction: str = "",
        max_iterations: int = 3,
    ) -> dict[str, Any]:
        batch_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO batches(
                    id, name, status, task_instruction, max_iterations, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    name,
                    BatchStatus.DRAFT,
                    task_instruction,
                    max_iterations,
                    now,
                    now,
                ),
            )
        return self.get_batch(batch_id)

    def update_batch_spec(
        self,
        batch_id: str,
        task_instruction: str,
        max_iterations: int,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            cursor = connection.execute(
                """UPDATE batches
                SET task_instruction = ?, max_iterations = ?, updated_at = ?
                WHERE id = ?""",
                (task_instruction, max_iterations, utc_now(), batch_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(batch_id)
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        query = "SELECT * FROM batches WHERE id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self.connect() as connection:
            row = connection.execute(query, (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return self._decode_batch(row)

    def list_batches(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM batches WHERE deleted_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
        return [self._decode_batch(row) for row in rows]

    def list_deleted_batches(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM batches WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
            ).fetchall()
        return [self._decode_batch(row) for row in rows]

    def trash_batch(self, batch_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE batches SET deleted_at = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, now, batch_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(batch_id)
        return self.get_batch(batch_id, include_deleted=True)

    def restore_batch(self, batch_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE batches SET deleted_at = NULL, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NOT NULL",
                (utc_now(), batch_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(batch_id)
        return self.get_batch(batch_id)

    def delete_batch(self, batch_id: str) -> None:
        with self.connect() as connection:
            mask_uris = [
                row["storage_uri"]
                for row in connection.execute(
                    "SELECT storage_uri FROM formal_masks WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
            ]
            for uri in mask_uris:
                connection.execute(
                    "INSERT OR IGNORE INTO formal_mask_cleanup(storage_uri, created_at) VALUES (?, ?)",
                    (uri, utc_now()),
                )
            cursor = connection.execute(
                "DELETE FROM batches WHERE id = ? AND deleted_at IS NOT NULL",
                (batch_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(batch_id)

    def create_library_item(self, label: str) -> dict[str, Any]:
        item_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            counter = connection.execute(
                "SELECT next_number FROM library_code_counter WHERE singleton = 1"
            ).fetchone()
            number = counter["next_number"] if counter else 1
            if counter is None:
                connection.execute(
                    "INSERT INTO library_code_counter(singleton, next_number) VALUES (1, ?)",
                    (number + 1,),
                )
            else:
                connection.execute(
                    "UPDATE library_code_counter SET next_number = ? WHERE singleton = 1",
                    (number + 1,),
                )
            connection.execute(
                "INSERT INTO library_items(id, display_code, label, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_id, f"L{number:02d}", label, now, now),
            )
        return self.get_library_item(item_id)

    def get_library_item(self, item_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM library_items WHERE id = ?", (item_id,)
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        item = dict(row)
        item["assets"] = self.list_library_assets(item_id)
        return item

    def list_library_items(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM library_items ORDER BY updated_at DESC"
            ).fetchall()
        return [self.get_library_item(row["id"]) for row in rows]

    def add_library_asset(
        self,
        *,
        item_id: str,
        filename: str,
        media_type: str,
        storage_uri: str,
        sha256: str,
    ) -> dict[str, Any]:
        asset_id = str(uuid4())
        now = utc_now()
        with self.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM library_items WHERE id = ?", (item_id,)
            ).fetchone() is None:
                raise KeyError(item_id)
            connection.execute(
                "INSERT INTO library_assets(id, library_item_id, filename, media_type, "
                "storage_uri, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (asset_id, item_id, filename, media_type, storage_uri, sha256, now),
            )
            connection.execute(
                "UPDATE library_items SET updated_at = ? WHERE id = ?",
                (now, item_id),
            )
        return self.get_library_asset(asset_id)

    def get_library_asset(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM library_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)

    def list_library_assets(self, item_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM library_assets WHERE library_item_id = ? ORDER BY created_at",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_library_item(self, item_id: str) -> dict[str, Any]:
        item = self.get_library_item(item_id)
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM library_items WHERE id = ?", (item_id,))
        if cursor.rowcount != 1:
            raise KeyError(item_id)
        return item

    def delete_identity(self, identity_id: str) -> None:
        with self.connect() as connection:
            identity = connection.execute(
                "SELECT batch_id FROM identities WHERE id = ?",
                (identity_id,),
            ).fetchone()
            if identity is None:
                raise KeyError(identity_id)
            self._invalidate_confirmation(connection, identity["batch_id"])
            cursor = connection.execute("DELETE FROM identities WHERE id = ?", (identity_id,))
        if cursor.rowcount != 1:
            raise KeyError(identity_id)

    def set_batch_status(self, batch_id: str, status: BatchStatus) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), batch_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(batch_id)

    def create_identity(self, batch_id: str, label: str) -> dict[str, Any]:
        identity_id = str(uuid4())
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            display_code = self._next_display_code(connection, "identities", batch_id, "T")
            connection.execute(
                """INSERT INTO identities(
                    id, batch_id, display_code, label, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (identity_id, batch_id, display_code, label, utc_now()),
            )
        return self.get_identity(identity_id)

    def add_asset(
        self,
        *,
        batch_id: str,
        kind: str,
        filename: str,
        media_type: str,
        storage_uri: str,
        sha256: str,
        identity_id: str | None = None,
        parent_asset_id: str | None = None,
    ) -> dict[str, Any]:
        asset_id = str(uuid4())
        mutation = (
            self._formal_mask_mutation
            if str(kind) in {AssetKind.SOURCE, AssetKind.REFERENCE}
            else self.connect
        )
        with mutation() as connection:
            prefix = {"source": "I", "reference": "R", "result": "O"}[str(kind)]
            display_code = self._next_display_code(connection, "assets", batch_id, prefix)
            connection.execute(
                """INSERT INTO assets(
                    id, batch_id, identity_id, parent_asset_id, kind, display_code, filename,
                    media_type, storage_uri, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    batch_id,
                    identity_id,
                    parent_asset_id,
                    kind,
                    display_code,
                    filename,
                    media_type,
                    storage_uri,
                    sha256,
                    utc_now(),
                ),
            )
            if str(kind) in {AssetKind.SOURCE, AssetKind.REFERENCE}:
                self._invalidate_confirmation(connection, batch_id)
        return self.get_asset(asset_id)

    def upsert_swap_result(
        self,
        *,
        batch_id: str,
        source_asset_id: str,
        storage_uri: str,
        sha256: str,
    ) -> dict[str, Any]:
        """Expose the latest generated candidate for one source under a stable Oxx."""

        source = self.get_asset(source_asset_id)
        if source["batch_id"] != batch_id or source["kind"] != AssetKind.SOURCE:
            raise ValueError("swap result source must belong to the same batch")
        output_name = Path(unquote(urlparse(storage_uri).path)).name
        output_suffix = Path(output_name).suffix or Path(source["filename"]).suffix
        media_type = mimetypes.guess_type(output_name)[0] or source["media_type"]
        filename = f"result-{Path(source['filename']).stem}{output_suffix}"
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT id FROM assets
                WHERE batch_id = ? AND kind = ? AND parent_asset_id = ?
                ORDER BY created_at LIMIT 1""",
                (batch_id, AssetKind.RESULT, source_asset_id),
            ).fetchone()
            if existing is not None:
                asset_id = existing["id"]
                connection.execute(
                    """UPDATE assets
                    SET filename = ?, media_type = ?, storage_uri = ?, sha256 = ?
                    WHERE id = ?""",
                    (filename, media_type, storage_uri, sha256, asset_id),
                )
            else:
                asset_id = str(uuid4())
                display_code = self._next_display_code(
                    connection, "assets", batch_id, "O"
                )
                connection.execute(
                    """INSERT INTO assets(
                        id, batch_id, identity_id, parent_asset_id, kind, display_code,
                        filename, media_type, storage_uri, sha256, created_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        asset_id,
                        batch_id,
                        source_asset_id,
                        AssetKind.RESULT,
                        display_code,
                        filename,
                        media_type,
                        storage_uri,
                        sha256,
                        utc_now(),
                    ),
                )
        return self.get_asset(asset_id)

    def recover_swap_result_assets(self) -> int:
        """Backfill candidates produced before per-image result persistence existed."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT batch_id, item_key, output_json
                FROM jobs
                WHERE stage = ? AND status IN (?, ?) AND output_json IS NOT NULL
                ORDER BY created_at, attempt""",
                (JobStage.SWAP, JobStatus.SUCCEEDED, JobStatus.NEEDS_REVIEW),
            ).fetchall()
        recovered = 0
        for row in rows:
            try:
                output = json.loads(row["output_json"])
                storage_uri = output["output_uri"]
                sha256 = output["output_sha256"]
                if not isinstance(storage_uri, str) or not isinstance(sha256, str):
                    continue
                self.upsert_swap_result(
                    batch_id=row["batch_id"],
                    source_asset_id=row["item_key"],
                    storage_uri=storage_uri,
                    sha256=sha256,
                )
                recovered += 1
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        for batch in self.list_batches():
            if batch["status"] != BatchStatus.SWAPPING:
                continue
            latest = self.latest_stage_jobs(batch["id"], JobStage.SWAP)
            if (
                latest
                and any(job["status"] == JobStatus.NEEDS_REVIEW for job in latest)
                and not any(
                    job["status"] in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED}
                    for job in latest
                )
            ):
                self.set_batch_status(batch["id"], BatchStatus.REVIEW)
        return recovered

    def get_identity(self, identity_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM identities WHERE id = ?", (identity_id,)
            ).fetchone()
        if row is None:
            raise KeyError(identity_id)
        return self._decode_identity(row)

    def list_identities(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM identities WHERE batch_id = ? ORDER BY created_at",
                (batch_id,),
            ).fetchall()
        result = []
        for row in rows:
            identity = self._decode_identity(row)
            identity["references"] = self.list_assets_for_identity(identity["id"])
            result.append(identity)
        return result

    @staticmethod
    def _write_mappings(
        connection: sqlite3.Connection,
        batch_id: str,
        decisions: list[MappingDecision],
        confirmed_at: str,
    ) -> None:
        person_ids = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM persons WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        }
        decision_ids = {decision.person_id for decision in decisions}
        if decision_ids != person_ids or len(decision_ids) != len(decisions):
            raise ValueError(
                "mapping coverage mismatch; "
                f"missing={person_ids - decision_ids}, unknown={decision_ids - person_ids}"
            )
        identity_ids = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM identities WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        }
        connection.execute("DELETE FROM mappings WHERE batch_id = ?", (batch_id,))
        for decision in decisions:
            if decision.target_identity_id and decision.target_identity_id not in identity_ids:
                raise ValueError(
                    f"identity does not belong to batch: {decision.target_identity_id}"
                )
            connection.execute(
                """INSERT INTO mappings(
                    person_id, batch_id, action, target_identity_id, instruction, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    decision.person_id,
                    batch_id,
                    decision.action,
                    decision.target_identity_id,
                    decision.instruction,
                    confirmed_at,
                ),
            )
        connection.execute(
            "UPDATE persons SET confirmed = 1 WHERE batch_id = ?",
            (batch_id,),
        )

    def store_compiled_contract(
        self,
        batch_id: str,
        compilation: Any,
    ) -> dict[str, Any]:
        from personaweave.formal_masks import FormalCompilationResult
        if isinstance(compilation, CompiledBatchContract):
            contract = compilation
            compiled_masks: tuple[Any, ...] = ()
        else:
            if not isinstance(compilation, FormalCompilationResult):
                compilation = FormalCompilationResult.model_validate(compilation)
            contract = compilation.contract
            compiled_masks = compilation.masks
        editable_sources = {
            source.asset_id: source
            for source in contract.source_assets
            if source.editable_face_instance_ids
        }
        if any(
            source.authorization_mask is not None
            for source in contract.source_assets
            if not source.editable_face_instance_ids
        ):
            raise ValueError("non-editable sources cannot carry authorization masks")
        if any(
            source.authorization_mask is None for source in editable_sources.values()
        ):
            raise ValueError("every editable source requires an authorization mask")
        artifacts_by_source: dict[str, Any] = {}
        for artifact in compiled_masks:
            source_id = artifact.evidence.source_asset_id
            if source_id in artifacts_by_source:
                raise ValueError("compiled mask artifacts must be unique per source")
            artifacts_by_source[source_id] = artifact
        if set(artifacts_by_source) != set(editable_sources):
            raise ValueError("compiled mask artifacts must cover exactly the editable sources")
        for source_id, source in editable_sources.items():
            artifact = artifacts_by_source[source_id]
            evidence = artifact.evidence
            if evidence != source.authorization_mask:
                raise ValueError("compiled mask artifact does not match contract evidence")
            if (
                evidence.source_asset_code != source.code
                or evidence.proposal.source_asset_id != source_id
                or evidence.proposal.source_asset_code != source.code
            ):
                raise ValueError("compiled mask source identity is inconsistent")
            if hashlib.sha256(artifact.png_bytes).hexdigest() != evidence.sha256:
                raise ValueError("compiled mask artifact digest is invalid")
            try:
                with Image.open(BytesIO(artifact.png_bytes)) as mask_image:
                    mask_image.load()
                    if (
                        mask_image.format != "PNG"
                        or mask_image.mode != "L"
                        or mask_image.size != (evidence.width, evidence.height)
                    ):
                        raise ValueError("compiled mask artifact format or dimensions are invalid")
            except (UnidentifiedImageError, OSError) as error:
                raise ValueError("compiled mask artifact is not a valid PNG") from error
            faces_by_id = {face.face_instance_id: face for face in source.faces}
            operations_by_person = {
                operation.person_id: operation for operation in contract.operations
            }
            editable_ids = set(source.editable_face_instance_ids)
            included_ids = {
                contour.face_instance_id
                for contour in evidence.proposal.contours
                if contour.operation == "include"
            }
            if not editable_ids.issubset(included_ids):
                raise ValueError("authorization mask must include every editable face")
            for contour in evidence.proposal.contours:
                face = faces_by_id.get(contour.face_instance_id)
                operation = operations_by_person.get(contour.person_id)
                if (
                    contour.face_instance_id not in editable_ids
                    or face is None
                    or operation is None
                    or face.person_id != contour.person_id
                    or face.person_code != contour.person_code
                    or operation.permissions != contour.permissions
                ):
                    raise ValueError("authorization mask contour is outside the editable contract scope")
        contract_hash = contract.sha256()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM batches WHERE id = ? AND deleted_at IS NULL",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
                raise ValueError(
                    f"cannot compile task contract from {batch['status']}"
                )
            if (
                contract.task_instruction != batch["task_instruction"]
                or contract.max_iterations != batch["max_iterations"]
            ):
                raise ValueError(
                    "compiled contract does not match the current task specification"
                )

            people = {
                row["id"]: row
                for row in connection.execute(
                    "SELECT id, display_code FROM persons WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
            }
            if {
                operation.person_id for operation in contract.operations
            } != set(people):
                raise ValueError(
                    "compiled contract must cover every current person exactly once"
                )
            for operation in contract.operations:
                if (
                    people[operation.person_id]["display_code"]
                    != operation.person_code
                ):
                    raise ValueError("compiled operation person code is stale")

            sources = {
                row["id"]: (row["display_code"], row["sha256"])
                for row in connection.execute(
                    "SELECT id, display_code, sha256 FROM assets "
                    "WHERE batch_id = ? AND kind = ?",
                    (batch_id, AssetKind.SOURCE),
                ).fetchall()
            }
            compiled_sources = {
                source.asset_id: (source.code, source.sha256)
                for source in contract.source_assets
            }
            if compiled_sources != sources:
                raise ValueError("compiled source asset evidence is stale")

            identities = {
                row["id"]: row["display_code"]
                for row in connection.execute(
                    "SELECT id, display_code FROM identities WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
            }
            evidence_by_id = {
                evidence.profile.identity_id: evidence
                for evidence in contract.identities
            }
            referenced_identity_ids = {
                operation.target_identity_id
                for operation in contract.operations
                if operation.action is MappingAction.REPLACE
                and operation.target_identity_id
            }
            if set(evidence_by_id) != referenced_identity_ids:
                raise ValueError(
                    "compiled contract must assess exactly the referenced target identities"
                )
            if not referenced_identity_ids.issubset(identities):
                raise ValueError(
                    "compiled contract references an identity outside the batch"
                )
            for identity_id, evidence in evidence_by_id.items():
                if evidence.profile.identity_code != identities[identity_id]:
                    raise ValueError("compiled identity code is stale")
                references = {
                    row["display_code"]: row["sha256"]
                    for row in connection.execute(
                        "SELECT display_code, sha256 FROM assets "
                        "WHERE identity_id = ? AND kind = ?",
                        (identity_id, AssetKind.REFERENCE),
                    ).fetchall()
                }
                compiled_references = {
                    item.code: item.sha256 for item in evidence.reference_assets
                }
                if compiled_references != references:
                    raise ValueError("compiled reference evidence is stale")
            for operation in contract.operations:
                if operation.target_identity_id:
                    evidence = evidence_by_id.get(operation.target_identity_id)
                    if (
                        evidence is None
                        or operation.target_profile != evidence.profile
                        or operation.reference_assessment
                        != evidence.reference_assessment
                        or operation.reference_assets != evidence.reference_assets
                    ):
                        raise ValueError(
                            "compiled operation target evidence is inconsistent"
                        )

            staged_paths: list[Path] = []
            created_paths: list[Path] = []
            mask_uris: dict[str, str] = {}
            try:
                for artifact in compiled_masks:
                    evidence = artifact.evidence
                    mask_filename = (
                        f"mask-{batch_id}-{evidence.source_asset_id}-{evidence.sha256[:12]}.png"
                    )
                    mask_path = (self.formal_mask_root / mask_filename).resolve()
                    if not mask_path.is_relative_to(self.formal_mask_root.resolve()):
                        raise ValueError("formal mask path escapes the configured root")
                    temporary_path = self.formal_mask_root / f".{mask_filename}.{uuid4().hex}.tmp"
                    staged_paths.append(temporary_path)
                    with temporary_path.open("xb") as temporary_file:
                        temporary_file.write(artifact.png_bytes)
                        temporary_file.flush()
                    if mask_path.exists():
                        if (
                            not mask_path.is_file()
                            or hashlib.sha256(mask_path.read_bytes()).hexdigest()
                            != evidence.sha256
                        ):
                            raise ValueError("existing formal mask artifact conflicts with its digest")
                        temporary_path.unlink()
                        staged_paths.remove(temporary_path)
                    else:
                        temporary_path.replace(mask_path)
                        staged_paths.remove(temporary_path)
                        created_paths.append(mask_path)
                    mask_uris[evidence.source_asset_id] = mask_path.as_uri()

                self._invalidate_confirmation(
                    connection,
                    batch_id,
                    preserve_mask_uris=set(mask_uris.values()),
                )
                now = utc_now()
                for artifact in compiled_masks:
                    evidence = artifact.evidence
                    review_reasons_list = sorted(
                        reason.value for reason in evidence.review_reasons
                    )
                    connection.execute(
                        """INSERT INTO formal_masks(
                            id, batch_id, source_asset_id, storage_uri, sha256,
                            width, height, status, proposal_json, review_reasons_json,
                            created_at, confirmed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (
                            str(uuid4()),
                            batch_id,
                            evidence.source_asset_id,
                            mask_uris[evidence.source_asset_id],
                            evidence.sha256,
                            evidence.width,
                            evidence.height,
                            "proposed",
                            json.dumps(
                                evidence.proposal.model_dump(mode="json"),
                                ensure_ascii=False,
                            ),
                            json.dumps(review_reasons_list, ensure_ascii=False),
                            now,
                        ),
                    )
                for identity_id, evidence in evidence_by_id.items():
                    connection.execute(
                    """UPDATE identities
                    SET target_profile_json = ?, reference_sufficiency_json = ?
                    WHERE id = ?""",
                    (
                        json.dumps(
                            evidence.profile.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            evidence.reference_assessment.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                        identity_id,
                    ),
                    )
                connection.execute(
                """UPDATE batches
                SET compiled_contract_json = ?, compiled_contract_hash = ?,
                    contract_confirmed_at = NULL, updated_at = ?
                WHERE id = ?""",
                (
                    contract.canonical_json(),
                    contract_hash,
                    utc_now(),
                    batch_id,
                ),
                )
                # Surface commit failures while newly-created files are still recoverable.
                connection.commit()
            except Exception:
                for temporary_path in staged_paths:
                    temporary_path.unlink(missing_ok=True)
                for created_path in created_paths:
                    created_path.unlink(missing_ok=True)
                raise
        return self.get_batch(batch_id)

    def confirm_compiled_contract(
        self,
        batch_id: str,
        contract_hash: str,
        decisions: list[MappingDecision] | None = None,
        acknowledged_mask_sha256s: list[str] | None = None,
    ) -> dict[str, Any]:
        from personaweave.domain import ReviewReason
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM batches WHERE id = ? AND deleted_at IS NULL",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
                raise ValueError(f"cannot confirm task contract from {batch['status']}")
            if (
                not batch["compiled_contract_json"]
                or batch["compiled_contract_hash"] != contract_hash
            ):
                raise ValueError("compiled contract hash is stale")
            contract = CompiledBatchContract.model_validate_json(
                batch["compiled_contract_json"]
            )
            if contract.sha256() != contract_hash:
                raise ValueError("stored compiled contract hash is invalid")
            compiled_decisions = [
                MappingDecision(
                    person_id=operation.person_id,
                    action=operation.action,
                    target_identity_id=operation.target_identity_id,
                    instruction=operation.instruction,
                )
                for operation in contract.operations
            ]
            if decisions is not None and [
                decision.model_dump(mode="json") for decision in decisions
            ] != [
                decision.model_dump(mode="json") for decision in compiled_decisions
            ]:
                raise ValueError("manual mappings do not match the compiled contract")
            mask_rows = connection.execute(
                """SELECT id, source_asset_id, storage_uri, sha256, width, height,
                          status, proposal_json, review_reasons_json
                   FROM formal_masks WHERE batch_id = ?""",
                (batch_id,),
            ).fetchall()
            expected_masks = {
                source.asset_id: source.authorization_mask
                for source in contract.source_assets
                if source.editable_face_instance_ids
            }
            if any(evidence is None for evidence in expected_masks.values()):
                raise ValueError("confirmed contract is missing an editable-source mask")
            masks_by_source = {row["source_asset_id"]: row for row in mask_rows}
            if len(masks_by_source) != len(mask_rows) or set(masks_by_source) != set(expected_masks):
                raise ValueError("stored masks do not exactly cover the editable contract sources")
            resolver = AssetResolver(self.formal_mask_root)
            uncertain_hashes: set[str] = set()
            for source_id, expected in expected_masks.items():
                assert expected is not None
                row = masks_by_source[source_id]
                stored_reasons = set(json.loads(row["review_reasons_json"]))
                if (
                    row["status"] != "proposed"
                    or row["sha256"] != expected.sha256
                    or row["width"] != expected.width
                    or row["height"] != expected.height
                    or json.loads(row["proposal_json"])
                    != expected.proposal.model_dump(mode="json")
                    or stored_reasons
                    != {reason.value for reason in expected.review_reasons}
                ):
                    raise ValueError("stored mask does not match the compiled contract")
                try:
                    mask_path = resolver.local_path(row["storage_uri"])
                except UnsafeAssetPath as error:
                    raise ValueError("stored mask path is outside the configured root") from error
                if mask_path is None or not mask_path.is_file():
                    raise ValueError("stored mask artifact is missing")
                mask_bytes = mask_path.read_bytes()
                if hashlib.sha256(mask_bytes).hexdigest() != expected.sha256:
                    raise ValueError("stored mask artifact digest is invalid")
                try:
                    with Image.open(BytesIO(mask_bytes)) as mask_image:
                        mask_image.load()
                        if (
                            mask_image.format != "PNG"
                            or mask_image.mode != "L"
                            or mask_image.size != (expected.width, expected.height)
                        ):
                            raise ValueError("stored mask artifact format or dimensions are invalid")
                except (UnidentifiedImageError, OSError) as error:
                    raise ValueError("stored mask artifact is not a valid PNG") from error
                if ReviewReason.MASK_UNCERTAIN.value in stored_reasons:
                    uncertain_hashes.add(row["sha256"])
            acknowledgements = acknowledged_mask_sha256s or []
            acknowledged = set(acknowledgements)
            if len(acknowledged) != len(acknowledgements) or acknowledged != uncertain_hashes:
                raise ValueError(
                    "uncertainty acknowledgement must exactly match uncertain mask digests"
                )
            now = utc_now()
            connection.execute(
                """UPDATE formal_masks
                SET status = ?, confirmed_at = ?
                WHERE batch_id = ? AND status = ?""",
                ("confirmed", now, batch_id, "proposed"),
            )
            self._write_mappings(connection, batch_id, compiled_decisions, now)
            connection.execute(
                """UPDATE batches
                SET status = ?, contract_confirmed_at = ?, updated_at = ?
                WHERE id = ?""",
                (BatchStatus.READY, now, now, batch_id),
            )
        return self.get_batch(batch_id)


    def list_assets_for_identity(self, identity_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE identity_id = ? ORDER BY created_at",
                (identity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)

    def list_assets(self, batch_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM assets WHERE batch_id = ?"
        params: tuple[Any, ...] = (batch_id,)
        if kind is not None:
            query += " AND kind = ?"
            params += (kind,)
        query += " ORDER BY created_at"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_formal_masks(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT source_asset_id, storage_uri, sha256, width, height,
                          status, proposal_json, review_reasons_json
                   FROM formal_masks
                   WHERE batch_id = ?
                   ORDER BY created_at""",
                (batch_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["proposal_json"] = json.loads(item["proposal_json"])
            item["review_reasons_json"] = json.loads(item["review_reasons_json"])
            result.append(item)
        return result

    def get_current_formal_mask(self, batch_id: str, sha256: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT source_asset_id, storage_uri, sha256, width, height,
                          status, proposal_json, review_reasons_json
                   FROM formal_masks
                   WHERE batch_id = ? AND sha256 = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (batch_id, sha256),
            ).fetchone()
        if row is None:
            raise KeyError((batch_id, sha256))
        item = dict(row)
        item["proposal_json"] = json.loads(item["proposal_json"])
        item["review_reasons_json"] = json.loads(item["review_reasons_json"])
        return item

    def delete_asset(self, asset_id: str) -> tuple[dict[str, Any], int]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            asset = dict(row)
            if asset["kind"] in {AssetKind.SOURCE, AssetKind.REFERENCE}:
                self._invalidate_confirmation(connection, asset["batch_id"])
            connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            remaining_references = connection.execute(
                "SELECT COUNT(*) FROM assets WHERE storage_uri = ?",
                (asset["storage_uri"],),
            ).fetchone()[0]
        return asset, remaining_references

    @staticmethod
    def _insert_job(
        connection: sqlite3.Connection,
        *,
        batch_id: str,
        stage: JobStage,
        item_key: str,
        payload: dict[str, Any],
        now: str,
    ) -> str:
        job_id = str(uuid4())
        total_iterations = (
            max(1, int(payload.get("max_iterations", 1)))
            if stage == JobStage.SWAP
            else 1
        )
        attempt = connection.execute(
            """SELECT COALESCE(MAX(attempt), 0) + 1
            FROM jobs WHERE batch_id = ? AND stage = ? AND item_key = ?""",
            (batch_id, stage, item_key),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO jobs(
                id, batch_id, stage, item_key, status, attempt, phase,
                current_iteration, total_iterations, progress_message,
                input_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                batch_id,
                stage,
                item_key,
                JobStatus.QUEUED,
                attempt,
                JobPhase.QUEUED,
                0,
                total_iterations,
                "等待执行",
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        return job_id

    def create_job(
        self,
        batch_id: str,
        stage: JobStage,
        item_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            job_id = self._insert_job(
                connection,
                batch_id=batch_id,
                stage=stage,
                item_key=item_key,
                payload=payload,
                now=utc_now(),
            )
        return self.get_job(job_id)

    def claim_ready_and_create_swap_jobs(
        self,
        batch_id: str,
        *,
        contract_hash: str,
        confirmed_at: str,
        jobs: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Atomically claim one confirmed READY contract and create its initial jobs."""
        if not jobs:
            raise ValueError("at least one swap job is required")
        item_keys = [item_key for item_key, _ in jobs]
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("initial swap jobs must have unique source item keys")

        now = utc_now()
        with self.connect() as connection:
            claimed = connection.execute(
                """UPDATE batches
                SET status = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL AND status = ?
                  AND compiled_contract_hash = ?
                  AND contract_confirmed_at = ?""",
                (
                    BatchStatus.SWAPPING,
                    now,
                    batch_id,
                    BatchStatus.READY,
                    contract_hash,
                    confirmed_at,
                ),
            )
            if claimed.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM batches WHERE id = ? AND deleted_at IS NULL",
                    (batch_id,),
                ).fetchone()
                if exists is None:
                    raise KeyError(batch_id)
                raise ValueError(
                    "READY claim no longer matches the confirmed contract"
                )
            job_ids = [
                self._insert_job(
                    connection,
                    batch_id=batch_id,
                    stage=JobStage.SWAP,
                    item_key=item_key,
                    payload=payload,
                    now=now,
                )
                for item_key, payload in jobs
            ]
        return [self.get_job(job_id) for job_id in job_ids]

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["input"] = json.loads(result.pop("input_json"))
        result["output"] = json.loads(result.pop("output_json")) if result["output_json"] else None
        return result

    def list_jobs(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE batch_id = ? ORDER BY created_at",
                (batch_id,),
            ).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def get_job_for_item(self, batch_id: str, stage: JobStage, item_key: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id FROM jobs
                WHERE batch_id = ? AND stage = ? AND item_key = ?
                ORDER BY attempt DESC LIMIT 1""",
                (batch_id, stage, item_key),
            ).fetchone()
        if row is None:
            raise KeyError((batch_id, stage, item_key))
        return self.get_job(row["id"])

    def complete_job(
        self,
        job_id: str,
        *,
        status: JobStatus,
        provider: str,
        output: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs
                SET status = ?, phase = ?, provider = ?, output_json = ?,
                    progress_message = ?, updated_at = ?
                WHERE id = ?""",
                (
                    status,
                    JobPhase.NEEDS_REVIEW if status == JobStatus.NEEDS_REVIEW else JobPhase.COMPLETED,
                    provider,
                    json.dumps(output, ensure_ascii=False),
                    "需要人工检查" if status == JobStatus.NEEDS_REVIEW else "处理完成",
                    utc_now(),
                    job_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(job_id)

    def mark_job_running(
        self,
        job_id: str,
        *,
        phase: JobPhase,
        message: str,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs
                SET status = ?, phase = ?, progress_message = ?,
                    phase_started_at = ?, updated_at = ?
                WHERE id = ? AND status = ?""",
                (
                    JobStatus.RUNNING,
                    phase,
                    message,
                    now,
                    now,
                    job_id,
                    JobStatus.QUEUED,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"job {job_id} is not queued")

    def update_job_progress(
        self,
        job_id: str,
        *,
        phase: JobPhase,
        message: str,
        current_iteration: int | None = None,
        total_iterations: int | None = None,
    ) -> None:
        now = utc_now()
        assignments = [
            "phase = ?",
            "progress_message = ?",
            "phase_started_at = ?",
            "updated_at = ?",
        ]
        values: list[Any] = [phase, message, now, now]
        if current_iteration is not None:
            assignments.append("current_iteration = ?")
            values.append(current_iteration)
        if total_iterations is not None:
            assignments.append("total_iterations = ?")
            values.append(total_iterations)
        values.extend((job_id, JobStatus.RUNNING))
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ? AND status = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise ValueError(f"job {job_id} is not running")

    def fail_job(self, job_id: str, error_code: str, error_message: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs
                SET status = ?, phase = ?, error_code = ?, error_message = ?,
                    progress_message = ?, updated_at = ?
                WHERE id = ?""",
                (
                    JobStatus.FAILED,
                    JobPhase.FAILED,
                    error_code,
                    error_message[:1000],
                    "处理失败",
                    utc_now(),
                    job_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(job_id)

    def latest_stage_jobs(self, batch_id: str, stage: JobStage) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT j.id
                FROM jobs j
                INNER JOIN (
                    SELECT item_key, MAX(attempt) AS max_attempt
                    FROM jobs
                    WHERE batch_id = ? AND stage = ?
                    GROUP BY item_key
                ) latest
                ON j.item_key = latest.item_key AND j.attempt = latest.max_attempt
                WHERE j.batch_id = ? AND j.stage = ?
                ORDER BY j.created_at""",
                (batch_id, stage, batch_id, stage),
            ).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def current_contract_stage_jobs(
        self,
        batch_id: str,
        stage: JobStage,
    ) -> list[dict[str, Any]]:
        """Return latest attempts belonging to the batch's confirmed contract."""
        if stage is JobStage.ANALYZE:
            return self.latest_stage_jobs(batch_id, stage)
        batch = self.get_batch(batch_id)
        contract_hash = batch.get("contract_hash")
        if not contract_hash:
            return self.latest_stage_jobs(batch_id, stage)
        all_jobs = [
            job
            for job in self.list_jobs(batch_id)
            if job["stage"] == stage
        ]
        if stage is JobStage.SWAP:
            candidates = [
                job
                for job in all_jobs
                if (job.get("input") or {}).get("contract_hash") == contract_hash
            ]
        else:
            candidates = [
                job
                for job in all_jobs
                if (job.get("input") or {}).get("contract_hash") == contract_hash
            ]
        latest_by_item: dict[str, dict[str, Any]] = {}
        for job in candidates:
            previous = latest_by_item.get(job["item_key"])
            if previous is None or job["attempt"] > previous["attempt"]:
                latest_by_item[job["item_key"]] = job
        return sorted(latest_by_item.values(), key=lambda job: job["created_at"])

    def latest_jobs(self, batch_id: str) -> list[dict[str, Any]]:
        jobs = []
        for stage in JobStage:
            jobs.extend(self.current_contract_stage_jobs(batch_id, stage))
        return jobs

    def reconcile_terminal_batch_failures(self) -> int:
        """Move stale active batches with terminal failed jobs to the failed state."""

        stage_by_status = {
            BatchStatus.ANALYZING: JobStage.ANALYZE,
            BatchStatus.SWAPPING: JobStage.SWAP,
            BatchStatus.VALIDATING: JobStage.VALIDATE,
        }
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, status FROM batches WHERE deleted_at IS NULL "
                "AND status IN (?, ?, ?)",
                tuple(stage_by_status),
            ).fetchall()
        reconciled = 0
        for row in rows:
            stage = stage_by_status[BatchStatus(row["status"])]
            jobs = self.current_contract_stage_jobs(row["id"], stage)
            if (
                jobs
                and all(job["status"] not in {JobStatus.QUEUED, JobStatus.RUNNING} for job in jobs)
                and any(job["status"] == JobStatus.FAILED for job in jobs)
            ):
                self.set_batch_status(row["id"], BatchStatus.FAILED)
                reconciled += 1
        return reconciled

    def retry_job(
        self,
        job_id: str,
        *,
        input_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        latest = self.get_job_for_item(job["batch_id"], JobStage(job["stage"]), job["item_key"])
        if latest["id"] != job_id:
            raise ValueError("only the latest attempt can be retried")
        if job["status"] not in {JobStatus.FAILED, JobStatus.NEEDS_REVIEW}:
            raise ValueError(f"job {job_id} is not retryable")
        new_id = str(uuid4())
        now = utc_now()
        allowed_updates = {
            "image_model",
            "image_api_mode",
            "image_api_path",
            "image_output_size",
            "image_response_format",
        }
        requested_updates = input_updates or {}
        unknown_updates = set(requested_updates) - allowed_updates
        if unknown_updates:
            raise ValueError(
                "retry can only refresh provider profile fields; "
                f"immutable fields={sorted(unknown_updates)}"
            )
        retry_input = {**job["input"], **requested_updates}
        is_formal = retry_input.get("compiled_contract") is not None
        if is_formal and retry_input.get("image_api_mode") != "multipart_edits":
            raise ValueError("formal jobs cannot switch away from multipart_edits")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO jobs(
                    id, batch_id, stage, item_key, status, attempt, phase,
                    current_iteration, total_iterations, progress_message,
                    input_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    job["batch_id"],
                    job["stage"],
                    job["item_key"],
                    JobStatus.QUEUED,
                    job["attempt"] + 1,
                    JobPhase.QUEUED,
                    0,
                    job["total_iterations"],
                    "等待重试",
                    json.dumps(retry_input, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_job(new_id)

    def batch_progress(self, batch_id: str) -> dict[str, Any]:
        self.get_batch(batch_id)
        stages = {}
        for stage in JobStage:
            jobs = self.current_contract_stage_jobs(batch_id, stage)
            counts = {status.value: 0 for status in JobStatus}
            for job in jobs:
                counts[job["status"]] += 1
            stages[stage.value] = {"total": len(jobs), **counts}
        return {"batch_id": batch_id, "stages": stages}

    def record_analysis(self, batch_id: str, asset_id: str, faces: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            for face in faces:
                cluster_key = face.get("suggested_person_key") or f"unresolved:{asset_id}:{face['external_face_id']}"
                person_row = connection.execute(
                    "SELECT id FROM persons WHERE batch_id = ? AND cluster_key = ?",
                    (batch_id, cluster_key),
                ).fetchone()
                person_id = person_row["id"] if person_row else str(uuid4())
                if person_row is None:
                    display_code = self._next_display_code(connection, "persons", batch_id, "P")
                    connection.execute(
                        """INSERT INTO persons(
                            id, batch_id, cluster_key, display_code, confirmed, created_at
                        ) VALUES (?, ?, ?, ?, 0, ?)""",
                        (person_id, batch_id, cluster_key, display_code, utc_now()),
                    )
                connection.execute(
                    """INSERT OR REPLACE INTO face_instances(
                        id, batch_id, asset_id, person_id, external_face_id,
                        bbox_json, detection_confidence, identity_confidence,
                        needs_confirmation, appearance_json, created_at
                    ) VALUES (
                        COALESCE((SELECT id FROM face_instances WHERE asset_id = ? AND external_face_id = ?), ?),
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    (
                        asset_id,
                        face["external_face_id"],
                        str(uuid4()),
                        batch_id,
                        asset_id,
                        person_id,
                        face["external_face_id"],
                        json.dumps(face["bbox"]),
                        face["detection_confidence"],
                        face.get("identity_confidence"),
                        int(face.get("needs_confirmation", True)),
                        json.dumps(face.get("appearance") or {}, ensure_ascii=False),
                        utc_now(),
                    ),
                )

    def reset_analysis(self, batch_id: str) -> None:
        """Remove derived analysis state while preserving assets, history and code counters."""
        with self.connect() as connection:
            batch = connection.execute(
                "SELECT status FROM batches WHERE id = ? AND deleted_at IS NULL",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            self._invalidate_confirmation(connection, batch_id)
            connection.execute("DELETE FROM mappings WHERE batch_id = ?", (batch_id,))
            connection.execute("DELETE FROM face_instances WHERE batch_id = ?", (batch_id,))
            connection.execute("DELETE FROM persons WHERE batch_id = ?", (batch_id,))
            connection.execute(
                "UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
                (BatchStatus.DRAFT, utc_now(), batch_id),
            )

    def add_manual_face(
        self,
        batch_id: str,
        asset_id: str,
        bbox: dict[str, int],
        person_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a user-confirmed face occurrence to an existing or new stable Pxx."""
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            asset = connection.execute(
                "SELECT id FROM assets WHERE id = ? AND batch_id = ? AND kind = ?",
                (asset_id, batch_id, AssetKind.SOURCE),
            ).fetchone()
            if asset is None:
                raise ValueError("source image does not belong to batch")
            if person_id:
                person = connection.execute(
                    "SELECT id FROM persons WHERE id = ? AND batch_id = ?",
                    (person_id, batch_id),
                ).fetchone()
                if person is None:
                    raise ValueError("person does not belong to batch")
                resolved_person_id = person["id"]
                identity_confidence = 1.0
            else:
                resolved_person_id = str(uuid4())
                display_code = self._next_display_code(connection, "persons", batch_id, "P")
                connection.execute(
                    """INSERT INTO persons(
                        id, batch_id, cluster_key, display_code, confirmed, created_at
                    ) VALUES (?, ?, ?, ?, 0, ?)""",
                    (
                        resolved_person_id,
                        batch_id,
                        f"manual:{resolved_person_id}",
                        display_code,
                        utc_now(),
                    ),
                )
                identity_confidence = None
            face_id = str(uuid4())
            connection.execute(
                """INSERT INTO face_instances(
                    id, batch_id, asset_id, person_id, external_face_id,
                    bbox_json, detection_confidence, identity_confidence,
                    needs_confirmation, appearance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    face_id,
                    batch_id,
                    asset_id,
                    resolved_person_id,
                    f"manual-{face_id}",
                    json.dumps(bbox),
                    1.0,
                    identity_confidence,
                    0 if person_id else 1,
                    "{}",
                    utc_now(),
                ),
            )
        return next(
            person
            for person in self.list_persons(batch_id)
            if person["id"] == resolved_person_id
        )

    def list_persons(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            people = connection.execute(
                "SELECT * FROM persons WHERE batch_id = ? ORDER BY created_at",
                (batch_id,),
            ).fetchall()
            result = []
            for person in people:
                item = dict(person)
                faces = connection.execute(
                    "SELECT * FROM face_instances WHERE person_id = ? ORDER BY created_at",
                    (person["id"],),
                ).fetchall()
                item["faces"] = []
                for face in faces:
                    face_item = dict(face)
                    face_item["bbox"] = json.loads(face_item.pop("bbox_json"))
                    face_item["appearance"] = json.loads(
                        face_item.pop("appearance_json") or "{}"
                    )
                    item["faces"].append(face_item)
                mapping = connection.execute(
                    "SELECT * FROM mappings WHERE person_id = ?",
                    (person["id"],),
                ).fetchone()
                item["mapping"] = dict(mapping) if mapping else None
                result.append(item)
        return result

    def list_faces_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM face_instances WHERE asset_id = ? ORDER BY created_at",
                (asset_id,),
            ).fetchall()
        result = []
        for row in rows:
            face = dict(row)
            face["bbox"] = json.loads(face.pop("bbox_json"))
            face["appearance"] = json.loads(face.pop("appearance_json") or "{}")
            result.append(face)
        return result

    def get_face(self, face_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM face_instances WHERE id = ?",
                (face_id,),
            ).fetchone()
        if row is None:
            raise KeyError(face_id)
        face = dict(row)
        face["bbox"] = json.loads(face.pop("bbox_json"))
        face["appearance"] = json.loads(face.pop("appearance_json") or "{}")
        return face

    def update_face_fusion_metadata(
        self,
        updates: list[tuple[str, float]],
    ) -> None:
        with self.connect() as connection:
            batch_ids = {
                row["batch_id"]
                for face_id, _ in updates
                for row in connection.execute(
                    "SELECT batch_id FROM face_instances WHERE id = ?",
                    (face_id,),
                ).fetchall()
            }
            for batch_id in batch_ids:
                self._invalidate_confirmation(connection, batch_id)
            connection.executemany(
                """UPDATE face_instances
                SET identity_confidence = ?, needs_confirmation = 1
                WHERE id = ?""",
                [(confidence, face_id) for face_id, confidence in updates],
            )

    def merge_persons(self, batch_id: str, person_ids: list[str]) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(person_ids))
        if len(unique_ids) < 2:
            raise ValueError("at least two people are required for merge")
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"""SELECT * FROM persons
                WHERE batch_id = ? AND id IN ({placeholders}) ORDER BY display_code""",
                (batch_id, *unique_ids),
            ).fetchall()
            if len(rows) != len(unique_ids):
                raise ValueError("person does not belong to batch")
            target_id = rows[0]["id"]
            merged_ids = [row["id"] for row in rows[1:]]
            merged_placeholders = ",".join("?" for _ in merged_ids)
            connection.execute(
                f"UPDATE face_instances SET person_id = ? WHERE person_id IN ({merged_placeholders})",
                (target_id, *merged_ids),
            )
            connection.execute(
                f"DELETE FROM mappings WHERE person_id IN ({placeholders})",
                tuple(unique_ids),
            )
            connection.execute(
                f"DELETE FROM persons WHERE id IN ({merged_placeholders})",
                tuple(merged_ids),
            )
            connection.execute("UPDATE persons SET confirmed = 0 WHERE id = ?", (target_id,))
        return next(person for person in self.list_persons(batch_id) if person["id"] == target_id)

    def delete_person(self, batch_id: str, person_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            person = connection.execute(
                "SELECT * FROM persons WHERE id = ? AND batch_id = ?",
                (person_id, batch_id),
            ).fetchone()
            if person is None:
                raise KeyError(person_id)
            connection.execute(
                "DELETE FROM face_instances WHERE person_id = ? AND batch_id = ?",
                (person_id, batch_id),
            )
            connection.execute(
                "DELETE FROM persons WHERE id = ? AND batch_id = ?",
                (person_id, batch_id),
            )
        return dict(person)

    def split_person_faces(
        self,
        batch_id: str,
        person_id: str,
        face_ids: list[str],
    ) -> dict[str, Any]:
        unique_face_ids = list(dict.fromkeys(face_ids))
        if not unique_face_ids:
            raise ValueError("at least one occurrence is required for split")
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            person = connection.execute(
                "SELECT * FROM persons WHERE id = ? AND batch_id = ?",
                (person_id, batch_id),
            ).fetchone()
            if person is None:
                raise ValueError("person does not belong to batch")
            all_faces = connection.execute(
                "SELECT id FROM face_instances WHERE person_id = ?",
                (person_id,),
            ).fetchall()
            all_face_ids = {row["id"] for row in all_faces}
            if not set(unique_face_ids).issubset(all_face_ids):
                raise ValueError("face occurrence does not belong to person")
            if len(unique_face_ids) == len(all_face_ids):
                raise ValueError("split must leave at least one occurrence on the original person")
            new_person_id = str(uuid4())
            display_code = self._next_display_code(connection, "persons", batch_id, "P")
            connection.execute(
                """INSERT INTO persons(
                    id, batch_id, cluster_key, display_code, confirmed, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)""",
                (new_person_id, batch_id, f"manual-split:{new_person_id}", display_code, utc_now()),
            )
            placeholders = ",".join("?" for _ in unique_face_ids)
            connection.execute(
                f"UPDATE face_instances SET person_id = ? WHERE id IN ({placeholders})",
                (new_person_id, *unique_face_ids),
            )
            connection.execute("DELETE FROM mappings WHERE person_id = ?", (person_id,))
            connection.execute("UPDATE persons SET confirmed = 0 WHERE id = ?", (person_id,))
        return next(
            person for person in self.list_persons(batch_id) if person["id"] == new_person_id
        )

    def save_mappings(self, batch_id: str, decisions: list[MappingDecision]) -> None:
        """Save an unconfirmed manual topology; only hash confirmation may make it READY."""
        with self.connect() as connection:
            self._invalidate_confirmation(connection, batch_id)
            self._write_mappings(connection, batch_id, decisions, utc_now())
            connection.execute(
                "UPDATE persons SET confirmed = 0 WHERE batch_id = ?",
                (batch_id,),
            )
