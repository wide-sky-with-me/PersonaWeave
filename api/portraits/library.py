"""SQLite-backed repository for the managed portrait identity library.

The managed library lives under a local root (``data/portrait_library`` by
default) containing ``library.sqlite3`` plus ``originals/``, ``thumbnails/``
and ``quarantine/`` directories. The external Windows-mounted corpus is
import-only: :mod:`api.portraits.importer` copies assets into the
managed root once, and everything at runtime is served from there via
opaque, deterministic identity/asset IDs looked up in SQLite.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

DEFAULT_LIBRARY_ROOT = Path("data/portrait_library")

DB_FILENAME = "library.sqlite3"
ORIGINALS_DIRNAME = "originals"
THUMBNAILS_DIRNAME = "thumbnails"
QUARANTINE_DIRNAME = "quarantine"

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    gender TEXT NOT NULL,
    age INTEGER NOT NULL,
    age_group TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES identities(id),
    role TEXT NOT NULL,
    label TEXT NOT NULL,
    source_relpath TEXT NOT NULL UNIQUE,
    original_relpath TEXT NOT NULL,
    thumbnail_relpath TEXT,
    content_hash TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archived_identities (
    identity_id TEXT PRIMARY KEY REFERENCES identities(id),
    archived_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,
    source_manifest TEXT NOT NULL,
    source_relpath TEXT NOT NULL,
    identity_source_key TEXT,
    identity_id TEXT,
    asset_id TEXT,
    status TEXT NOT NULL,
    detail TEXT
);
"""

# Age boundaries are inclusive upper bounds; anything past the last one falls
# into "senior". Kept as data rather than a branchy if/elif chain.
_AGE_GROUP_BOUNDARIES = (
    (12, "child"),
    (17, "teen"),
    (29, "young_adult"),
    (44, "adult"),
    (64, "middle_aged"),
)


def age_group_for(age: int) -> str:
    """Buckets a raw age into a coarse, stable age-group label."""
    for upper, label in _AGE_GROUP_BOUNDARIES:
        if age <= upper:
            return label
    return "senior"


def opaque_id(prefix: str, *parts: str) -> str:
    """Deterministic, opaque ID derived from stable natural-key parts.

    Hashing (rather than reusing manifest IDs or row order) keeps IDs stable
    and idempotent across re-imports while not leaking source layout.
    """
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:20]}"


def get_library_root() -> Path:
    return Path(os.getenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", DEFAULT_LIBRARY_ROOT)).expanduser()


def originals_dir(root: Path) -> Path:
    return root / ORIGINALS_DIRNAME


def thumbnails_dir(root: Path) -> Path:
    return root / THUMBNAILS_DIRNAME


def quarantine_dir(root: Path) -> Path:
    return root / QUARANTINE_DIRNAME


def db_path(root: Path) -> Path:
    return root / DB_FILENAME


def ensure_layout(root: Path | None = None) -> Path:
    """Creates the managed root and its subdirectories if missing."""
    library_root = (root or get_library_root()).resolve()
    for directory in (
        library_root,
        originals_dir(library_root),
        thumbnails_dir(library_root),
        quarantine_dir(library_root),
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return library_root


CONNECT_TIMEOUT_SECONDS = 30.0


@contextmanager
def connect(root: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Opens one WAL-mode connection for a single operation, then closes it.

    Commits on clean exit; rolls back if the caller raises, so a failed
    import row or partial write never leaves the database mid-transaction.
    """
    library_root = (root or get_library_root()).resolve()
    library_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(library_root), timeout=CONNECT_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_portrait_library_catalog(root: Path | None = None) -> dict:
    """Read-only catalog view, grouped by identity, sourced from SQLite.

    Exposes only opaque identity/asset IDs - never filesystem paths or URLs.
    Binary content is served separately via ``/api/v1/portrait-assets/{asset_id}``.
    """
    library_root = (root or get_library_root()).resolve()
    empty = {
        "available": False,
        "total_identities": 0,
        "total_assets": 0,
        "identities": [],
    }
    if not db_path(library_root).is_file():
        return empty

    with connect(library_root) as conn:
        identity_rows = conn.execute(
            "SELECT * FROM identities WHERE id NOT IN (SELECT identity_id FROM archived_identities) ORDER BY source_key"
        ).fetchall()
        asset_rows = conn.execute(
            "SELECT * FROM assets WHERE identity_id NOT IN (SELECT identity_id FROM archived_identities) "
            "ORDER BY identity_id, role, label"
        ).fetchall()

    if not identity_rows:
        return empty

    assets_by_identity: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for asset in asset_rows:
        assets_by_identity[asset["identity_id"]].append(asset)

    identities = []
    total_assets = 0
    for identity in identity_rows:
        assets = [a for a in assets_by_identity.get(identity["id"], []) if a["role"] != "contact_sheet"]
        contact_sheets = [a for a in assets_by_identity.get(identity["id"], []) if a["role"] == "contact_sheet"]
        if not assets:
            continue
        asset_items = [
            {
                "category": asset["role"],
                "label": asset["label"],
                "name": Path(asset["original_relpath"]).name,
                "asset_id": asset["id"],
            }
            for asset in assets
        ]
        preferred = next(
            (item for item in asset_items if item["category"] == "angle" and item["label"] == "01_front"),
            asset_items[0],
        )
        identities.append(
            {
                "id": identity["id"],
                "code": identity["source_key"],
                "gender": identity["gender"],
                "age": identity["age"],
                "age_group": identity["age_group"],
                "display_name": identity["display_name"],
                "cover_asset_id": preferred["asset_id"],
                "contact_sheet_asset_id": contact_sheets[0]["id"] if contact_sheets else None,
                "asset_count": len(asset_items),
                "assets": asset_items,
            }
        )
        total_assets += len(asset_items)

    return {
        "available": True,
        "total_identities": len(identities),
        "total_assets": total_assets,
        "identities": identities,
    }


def archive_outside_age_range(min_age: int, max_age: int, *, root: Path | None = None) -> int:
    """Hide out-of-range identities from new selections, preserving all historical ID/file lookups.

    This is an explicit library maintenance action, not a permanent age filter on future imports.
    Existing archives stay archived. Returns the number of newly archived identities.
    """
    if min_age < 0 or max_age < min_age:
        raise ValueError("Invalid portrait age range")
    with connect(root) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO archived_identities (identity_id, archived_at) "
            "SELECT id, datetime('now') FROM identities WHERE age < ? OR age > ?",
            (min_age, max_age),
        )
        return cursor.rowcount


def resolve_asset_path(asset_id: str, *, root: Path | None = None, thumbnail: bool = False) -> Path | None:
    """Safe ID-to-path resolution: looks up ``asset_id`` in SQLite and returns
    the managed-root file path, or ``None`` if the ID is unknown or the
    resolved path would escape the expected managed subdirectory.
    """
    library_root = (root or get_library_root()).resolve()
    if not db_path(library_root).is_file():
        return None

    # Lookup must not initialize/migrate a real library during installation diagnostics.
    with closing(sqlite3.connect(f"{db_path(library_root).as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT original_relpath, thumbnail_relpath FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
    if row is None:
        return None

    relpath = row["thumbnail_relpath"] if thumbnail else row["original_relpath"]
    if not relpath:
        return None

    base = (thumbnails_dir(library_root) if thumbnail else originals_dir(library_root)).resolve()
    candidate = (library_root / relpath).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    return candidate


def resolve_identity_asset_ids(identity_id: str, *, root: Path | None = None) -> list[str] | None:
    """Ordered, non-contact-sheet asset IDs belonging to an identity.

    Returns ``None`` if ``identity_id`` (the opaque identity ID, not the
    manifest source key) is unknown, so callers can distinguish "no assets"
    from "no such identity".
    """
    library_root = (root or get_library_root()).resolve()
    if not db_path(library_root).is_file():
        return None
    with connect(library_root) as conn:
        identity_row = conn.execute("SELECT id FROM identities WHERE id = ?", (identity_id,)).fetchone()
        if identity_row is None:
            return None
        asset_rows = conn.execute(
            "SELECT id FROM assets WHERE identity_id = ? AND role != 'contact_sheet' ORDER BY role, label",
            (identity_id,),
        ).fetchall()
    return [row["id"] for row in asset_rows]


def resolve_asset_ids(asset_ids: list[str], *, root: Path | None = None) -> list[Path]:
    """Resolves a list of asset IDs to managed original-file paths, in order.

    Raises ``ValueError`` naming any unknown or out-of-bounds ID rather than
    silently dropping it, since callers feed the result straight into the
    face-swap pipeline.
    """
    if not asset_ids:
        return []

    library_root = (root or get_library_root()).resolve()
    if not db_path(library_root).is_file():
        raise ValueError(f"Unknown portrait asset id(s): {', '.join(asset_ids)}")

    placeholders = ",".join("?" for _ in asset_ids)
    with connect(library_root) as conn:
        rows = conn.execute(
            f"SELECT id, original_relpath FROM assets WHERE id IN ({placeholders})",
            asset_ids,
        ).fetchall()
    relpath_by_id = {row["id"]: row["original_relpath"] for row in rows}

    missing = [asset_id for asset_id in asset_ids if asset_id not in relpath_by_id]
    if missing:
        raise ValueError(f"Unknown portrait asset id(s): {', '.join(missing)}")

    base = originals_dir(library_root).resolve()
    paths = []
    for asset_id in asset_ids:
        candidate = (library_root / relpath_by_id[asset_id]).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            raise ValueError(f"Portrait asset id resolved outside the managed library: {asset_id}")
        paths.append(candidate)
    return paths
