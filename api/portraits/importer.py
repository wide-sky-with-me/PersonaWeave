"""Idempotent importer for the managed portrait identity library.

Reads a ``manifest.csv``-described, import-only source corpus (Windows-style
relative paths), copies validated originals and generated WebP thumbnails
into the managed library root, and records identities/assets/import-audit
rows in SQLite (see :mod:`api.portraits.library`). Safe to re-run: unchanged
source files are skipped by content hash, changed files are re-copied, and
corrupt/unreadable images are quarantined instead of aborting the run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from PIL import Image

from api.portraits.library import (
    age_group_for,
    connect,
    ensure_layout,
    opaque_id,
    originals_dir,
    quarantine_dir,
    thumbnails_dir,
)

VALID_GENDERS = {"female", "male"}
THUMBNAIL_MAX_DIM = 320
THUMBNAIL_QUALITY = 82
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class ImportSummary:
    imported: int = 0
    updated: int = 0
    skipped_unchanged: int = 0
    quarantined: int = 0
    invalid_row: int = 0
    missing_source: int = 0
    duplicate_relpath_conflict: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(vars(self))


def _normalize_manifest_relpath(raw_path: str) -> Path | None:
    """Converts a Windows-style manifest path into a safe relative Path.

    Rejects absolute paths and ``..`` segments so an untrusted/malformed
    manifest can't make the importer read or quarantine outside the source root.
    """
    raw_path = raw_path.strip()
    if not raw_path:
        return None
    windows_path = PureWindowsPath(raw_path)
    if windows_path.drive or windows_path.root:
        return None
    parts = windows_path.parts
    if not parts or ".." in parts:
        return None
    return Path(*parts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_image_size(source: Path) -> tuple[int, int]:
    """Raises if the image is unreadable/corrupt; Image objects can't be
    reused after verify(), so the file is opened a second time for size."""
    with Image.open(source) as img:
        img.verify()
    with Image.open(source) as img:
        return img.size


def _write_thumbnail(source: Path, destination: Path) -> None:
    with Image.open(source) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM))
        destination.parent.mkdir(parents=True, exist_ok=True)
        img.save(destination, format="WEBP", quality=THUMBNAIL_QUALITY)


def _audit(
    conn,
    now: str,
    manifest_name: str,
    source_relpath: str,
    identity_source_key: str | None,
    identity_id: str | None,
    asset_id: str | None,
    status: str,
    detail: str | None,
) -> None:
    conn.execute(
        "INSERT INTO import_audit "
        "(imported_at, source_manifest, source_relpath, identity_source_key, identity_id, asset_id, status, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now, manifest_name, source_relpath, identity_source_key, identity_id, asset_id, status, detail),
    )


def _upsert_identity(conn, source_key: str, gender: str, age: int, now: str) -> str:
    identity_id = opaque_id("id", source_key)
    display_name = f"{source_key} · {age}岁"
    conn.execute(
        """
        INSERT INTO identities (id, source_key, gender, age, age_group, display_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            gender = excluded.gender, age = excluded.age, age_group = excluded.age_group,
            display_name = excluded.display_name
        """,
        (identity_id, source_key, gender, age, age_group_for(age), display_name, now),
    )
    return identity_id


def _import_asset(
    conn,
    *,
    source_root: Path,
    library_root: Path,
    identity_id: str,
    source_key: str,
    role: str,
    label: str,
    relpath: Path,
    manifest_name: str,
    summary: ImportSummary,
    seen_relpaths: dict[str, str],
    now: str,
) -> None:
    source_relpath_str = relpath.as_posix()
    asset_id = opaque_id("as", identity_id, source_relpath_str)

    # `assets.source_relpath` is UNIQUE across the whole library (schema-level), so a source
    # file can only ever back one asset regardless of identity. A repeat under the *same*
    # identity (e.g. a contact sheet referenced twice) is a legitimate no-op skip; a repeat
    # under a *different* identity is a genuine manifest conflict that would otherwise fail
    # silently (or crash on the UNIQUE constraint) - surface it distinctly so it isn't mistaken
    # for a harmless skip and the second identity's missing asset doesn't go unnoticed.
    prior_identity_id = seen_relpaths.get(source_relpath_str)
    if prior_identity_id is not None:
        if prior_identity_id == identity_id:
            summary.skipped_unchanged += 1
        else:
            summary.duplicate_relpath_conflict += 1
            _audit(
                conn,
                now,
                manifest_name,
                source_relpath_str,
                source_key,
                identity_id,
                None,
                "duplicate_relpath_conflict",
                f"source_relpath already imported for identity '{prior_identity_id}'",
            )
        return
    seen_relpaths[source_relpath_str] = identity_id

    source_path = (source_root / relpath).resolve()
    if not source_path.is_relative_to(source_root) or not source_path.is_file():
        summary.missing_source += 1
        _audit(conn, now, manifest_name, source_relpath_str, source_key, identity_id, None, "missing_source", None)
        return

    content_hash = _sha256_file(source_path)
    existing = conn.execute("SELECT content_hash FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if existing is not None and existing["content_hash"] == content_hash:
        summary.skipped_unchanged += 1
        return

    try:
        width, height = _validated_image_size(source_path)
    except Exception as exc:
        quarantine_path = quarantine_dir(library_root) / relpath
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, quarantine_path)
        summary.quarantined += 1
        _audit(conn, now, manifest_name, source_relpath_str, source_key, identity_id, None, "quarantined", str(exc))
        return

    ext = relpath.suffix.lower() or ".bin"
    original_full = originals_dir(library_root) / identity_id / f"{asset_id}{ext}"
    thumbnail_full = thumbnails_dir(library_root) / identity_id / f"{asset_id}.webp"
    original_full.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, original_full)
    _write_thumbnail(source_path, thumbnail_full)

    status = "updated" if existing is not None else "imported"
    conn.execute(
        """
        INSERT INTO assets
            (id, identity_id, role, label, source_relpath, original_relpath, thumbnail_relpath,
             content_hash, width, height, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            role = excluded.role, label = excluded.label, original_relpath = excluded.original_relpath,
            thumbnail_relpath = excluded.thumbnail_relpath, content_hash = excluded.content_hash,
            width = excluded.width, height = excluded.height
        """,
        (
            asset_id,
            identity_id,
            role,
            label,
            source_relpath_str,
            original_full.relative_to(library_root).as_posix(),
            thumbnail_full.relative_to(library_root).as_posix(),
            content_hash,
            width,
            height,
            now,
        ),
    )
    if status == "imported":
        summary.imported += 1
    else:
        summary.updated += 1
    _audit(conn, now, manifest_name, source_relpath_str, source_key, identity_id, asset_id, status, None)


def _import_row(
    conn,
    row: dict[str, str],
    *,
    source_root: Path,
    library_root: Path,
    manifest_name: str,
    summary: ImportSummary,
    seen_relpaths: dict[str, str],
) -> None:
    now = datetime.now(UTC).isoformat()
    source_key = (row.get("id") or "").strip()
    gender = (row.get("gender") or "").strip().lower()
    role = (row.get("category") or "").strip()
    label = (row.get("label") or "").strip()
    relpath = _normalize_manifest_relpath(row.get("file") or "")

    if not source_key or gender not in VALID_GENDERS or relpath is None or not role or not label:
        summary.invalid_row += 1
        _audit(
            conn,
            now,
            manifest_name,
            row.get("file") or "",
            source_key or None,
            None,
            None,
            "invalid_row",
            "missing required field or unsafe file path",
        )
        return

    try:
        age = int((row.get("age") or "").strip())
    except ValueError:
        summary.invalid_row += 1
        _audit(conn, now, manifest_name, relpath.as_posix(), source_key, None, None, "invalid_row", "non-integer age")
        return

    identity_id = _upsert_identity(conn, source_key, gender, age, now)

    _import_asset(
        conn,
        source_root=source_root,
        library_root=library_root,
        identity_id=identity_id,
        source_key=source_key,
        role=role,
        label=label,
        relpath=relpath,
        manifest_name=manifest_name,
        summary=summary,
        seen_relpaths=seen_relpaths,
        now=now,
    )

    contact_sheet_relpath = _normalize_manifest_relpath(row.get("source_contact_sheet") or "")
    if contact_sheet_relpath is not None:
        _import_asset(
            conn,
            source_root=source_root,
            library_root=library_root,
            identity_id=identity_id,
            source_key=source_key,
            role="contact_sheet",
            label="contact_sheet",
            relpath=contact_sheet_relpath,
            manifest_name=manifest_name,
            summary=summary,
            seen_relpaths=seen_relpaths,
            now=now,
        )


def import_manifest(source_root: Path, library_root: Path | None = None) -> ImportSummary:
    """Imports every valid row of ``<source_root>/manifest.csv`` into the
    managed library. Idempotent: re-running with unchanged source files
    performs no filesystem writes and leaves existing rows untouched.
    """
    source_root = source_root.resolve()
    manifest_path = source_root / "manifest.csv"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.csv not found under {source_root}")

    root = ensure_layout(library_root)
    summary = ImportSummary()
    seen_relpaths: dict[str, str] = {}

    # Commit after each row instead of once for the whole manifest: original/thumbnail files
    # are copied to disk immediately per-row, outside any transaction, so a single end-of-run
    # commit would let a mid-import crash roll back every DB row while leaving every
    # already-copied file on disk as an orphan. Committing per row keeps the two in lockstep -
    # a crash leaves at most the in-flight row inconsistent, and a re-run's content-hash check
    # cleanly resumes from there.
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle, connect(root) as conn:
        for row in csv.DictReader(handle):
            _import_row(
                conn,
                row,
                source_root=source_root,
                library_root=root,
                manifest_name=manifest_path.name,
                summary=summary,
                seen_relpaths=seen_relpaths,
            )
            conn.commit()

    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a manifest.csv-described portrait corpus into the managed portrait library."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=Path,
        required=True,
        help="Import-only source root containing manifest.csv (e.g. the external Windows-mounted corpus).",
    )
    parser.add_argument(
        "--library-root",
        "-l",
        type=Path,
        default=None,
        help="Managed library root (defaults to $PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT or data/portrait_library).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    summary = import_manifest(args.source, args.library_root)
    print("Portrait library import complete:")
    for key, value in summary.as_dict().items():
        print(f"  {key:<18} {value}")


if __name__ == "__main__":
    main()
