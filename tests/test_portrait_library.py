"""Tests for the SQLite-backed managed portrait library repository."""

from pathlib import Path

import pytest

from api.portraits.library import (
    age_group_for,
    build_portrait_library_catalog,
    connect,
    ensure_layout,
    opaque_id,
    originals_dir,
    resolve_asset_ids,
    resolve_asset_path,
    resolve_identity_asset_ids,
    thumbnails_dir,
)


def _seed_identity_with_asset(
    root: Path, *, identity_source_key="F01", gender="female", age=16, role="angle", label="01_front"
):
    ensure_layout(root)
    identity_id = opaque_id("id", identity_source_key)
    asset_id = opaque_id("as", identity_id, f"{identity_source_key}/{label}.png")

    original_relpath = f"originals/{identity_id}/{asset_id}.png"
    thumbnail_relpath = f"thumbnails/{identity_id}/{asset_id}.webp"
    (originals_dir(root) / identity_id).mkdir(parents=True, exist_ok=True)
    (thumbnails_dir(root) / identity_id).mkdir(parents=True, exist_ok=True)
    (root / original_relpath).write_bytes(b"original-bytes")
    (root / thumbnail_relpath).write_bytes(b"thumb-bytes")

    with connect(root) as conn:
        conn.execute(
            "INSERT INTO identities (id, source_key, gender, age, age_group, display_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET gender=excluded.gender, age=excluded.age, "
            "age_group=excluded.age_group, display_name=excluded.display_name",
            (identity_id, identity_source_key, gender, age, age_group_for(age), f"{identity_source_key} · {age}岁"),
        )
        conn.execute(
            "INSERT INTO assets (id, identity_id, role, label, source_relpath, original_relpath, thumbnail_relpath, "
            "content_hash, width, height, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'hash', 10, 10, datetime('now'))",
            (
                asset_id,
                identity_id,
                role,
                label,
                f"{identity_source_key}/{label}.png",
                original_relpath,
                thumbnail_relpath,
            ),
        )
    return identity_id, asset_id


def test_catalog_reports_unavailable_when_no_database_exists(tmp_path: Path):
    catalog = build_portrait_library_catalog(tmp_path / "missing")
    assert catalog["available"] is False
    assert catalog["identities"] == []
    assert "root" not in catalog


def test_catalog_groups_assets_by_identity(tmp_path: Path):
    identity_id, asset_id = _seed_identity_with_asset(tmp_path)

    catalog = build_portrait_library_catalog(tmp_path)

    assert catalog["available"] is True
    assert catalog["total_identities"] == 1
    assert catalog["total_assets"] == 1
    assert "root" not in catalog
    identity = catalog["identities"][0]
    # Catalog identities expose an opaque ID plus the human-readable manifest
    # code - never a filesystem path or URL.
    assert identity["id"] == identity_id
    assert identity["code"] == "F01"
    assert identity["gender"] == "female"
    assert identity["age_group"] == "teen"
    assert identity["cover_asset_id"] == asset_id
    assert identity["asset_count"] == 1
    assert identity["assets"][0]["asset_id"] == asset_id
    assert "url" not in identity["assets"][0]
    assert "cover_url" not in identity
    assert "thumbnail_url" not in identity["assets"][0]


def test_catalog_excludes_contact_sheet_only_identities_from_images(tmp_path: Path):
    _seed_identity_with_asset(tmp_path, role="contact_sheet", label="contact_sheet")

    catalog = build_portrait_library_catalog(tmp_path)

    assert catalog["total_identities"] == 0


def test_resolve_asset_path_returns_none_for_unknown_id(tmp_path: Path):
    _seed_identity_with_asset(tmp_path)
    assert resolve_asset_path("as_does_not_exist", root=tmp_path) is None


def test_resolve_asset_path_resolves_original_and_thumbnail(tmp_path: Path):
    identity_id, asset_id = _seed_identity_with_asset(tmp_path)

    original = resolve_asset_path(asset_id, root=tmp_path)
    thumbnail = resolve_asset_path(asset_id, root=tmp_path, thumbnail=True)

    assert original == (originals_dir(tmp_path) / identity_id / f"{asset_id}.png").resolve()
    assert thumbnail == (thumbnails_dir(tmp_path) / identity_id / f"{asset_id}.webp").resolve()


def test_resolve_asset_path_rejects_path_escaping_managed_subdirectory(tmp_path: Path):
    _identity_id, asset_id = _seed_identity_with_asset(tmp_path)
    with connect(tmp_path) as conn:
        conn.execute("UPDATE assets SET original_relpath = ? WHERE id = ?", ("../outside.png", asset_id))
    (tmp_path.parent / "outside.png").write_bytes(b"escape")

    assert resolve_asset_path(asset_id, root=tmp_path) is None


def test_resolve_identity_asset_ids_lists_non_contact_sheet_assets(tmp_path: Path):
    identity_id, asset_id = _seed_identity_with_asset(tmp_path)
    _seed_identity_with_asset(tmp_path, label="02_left_15deg", role="angle")
    _seed_identity_with_asset(tmp_path, label="contact_sheet", role="contact_sheet")

    asset_ids = resolve_identity_asset_ids(identity_id, root=tmp_path)

    assert asset_id in asset_ids
    assert len(asset_ids) == 2


def test_resolve_identity_asset_ids_returns_none_for_unknown_identity(tmp_path: Path):
    _seed_identity_with_asset(tmp_path)
    assert resolve_identity_asset_ids("id_does_not_exist", root=tmp_path) is None


def test_resolve_asset_ids_returns_ordered_original_paths(tmp_path: Path):
    identity_id, asset_id = _seed_identity_with_asset(tmp_path)

    paths = resolve_asset_ids([asset_id], root=tmp_path)

    assert paths == [(originals_dir(tmp_path) / identity_id / f"{asset_id}.png").resolve()]


def test_resolve_asset_ids_raises_for_unknown_id(tmp_path: Path):
    _seed_identity_with_asset(tmp_path)
    with pytest.raises(ValueError, match="Unknown portrait asset id"):
        resolve_asset_ids(["as_does_not_exist"], root=tmp_path)


def test_connect_rolls_back_on_error(tmp_path: Path):
    identity_id, _ = _seed_identity_with_asset(tmp_path)

    with pytest.raises(RuntimeError), connect(tmp_path) as conn:
        conn.execute("UPDATE identities SET display_name = 'corrupted' WHERE id = ?", (identity_id,))
        raise RuntimeError("boom")

    with connect(tmp_path) as conn:
        identity = conn.execute("SELECT display_name FROM identities WHERE id = ?", (identity_id,)).fetchone()
    assert identity["display_name"] != "corrupted"


def test_opaque_id_is_deterministic_and_prefixed():
    first = opaque_id("id", "F01")
    second = opaque_id("id", "F01")
    different = opaque_id("id", "F02")

    assert first == second
    assert first != different
    assert first.startswith("id_")


def test_age_group_buckets():
    assert age_group_for(10) == "child"
    assert age_group_for(16) == "teen"
    assert age_group_for(25) == "young_adult"
    assert age_group_for(40) == "adult"
    assert age_group_for(55) == "middle_aged"
    assert age_group_for(70) == "senior"
