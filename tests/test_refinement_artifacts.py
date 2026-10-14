"""Isolated file contracts: temporary roots, synthetic pixels, no database or model."""

import hashlib
from uuid import uuid4

import cv2
import numpy as np
import pytest

from api.refinement.artifacts import SessionArtifacts
from api.refinement.contracts import ArtifactUnavailable, SessionNotFound
from api.refinement.deletion import staged_deletion
from core.image_security import decode_snapshot_bytes


@pytest.fixture
def artifacts(tmp_path):
    return SessionArtifacts(tmp_path / "private", tmp_path / "accepted")


@pytest.fixture
def pixels():
    return np.random.default_rng(47).integers(0, 256, (400, 600, 3), dtype=np.uint8)


def test_snapshot_freezes_exact_pixels_and_content_addressed_preview(artifacts, pixels):
    session_id = uuid4().hex
    original = pixels.copy()
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)
    assert hashlib.sha256(frozen).hexdigest() == digest
    np.testing.assert_array_equal(decode_snapshot_bytes(frozen), original)
    assert artifacts.image_shape(frozen) == original.shape
    preview = artifacts.cached_thumbnail(session_id, digest)
    assert cv2.imdecode(np.frombuffer(preview, np.uint8), cv2.IMREAD_COLOR).shape == (256, 384, 3)
    path = artifacts.path(session_id, digest)
    before = path.stat().st_mtime_ns
    assert artifacts.snapshot(session_id, pixels) == digest
    assert path.stat().st_mtime_ns == before
    pixels[:] = 0
    assert artifacts.read(session_id, digest) == frozen
    assert artifacts.cached_thumbnail(session_id, digest) == preview
    assert {item.suffix for item in path.parent.iterdir()} == {".png", ".preview"}


def test_snapshot_never_overwrites_an_existing_corrupted_digest(artifacts, pixels):
    session_id = uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    path = artifacts.path(session_id, digest)
    path.write_bytes(b"corrupted")
    with pytest.raises(ArtifactUnavailable, match="digest"):
        artifacts.snapshot(session_id, pixels)
    assert path.read_bytes() == b"corrupted"
    with pytest.raises(ArtifactUnavailable, match="digest"):
        artifacts.cached_thumbnail(session_id, digest)
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("operation", ["link", "fsync"])
def test_snapshot_write_failure_cleans_only_its_temporary_file(artifacts, pixels, monkeypatch, operation):
    session_id = uuid4().hex

    def fail(*args):
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(f"api.refinement.artifacts.os.{operation}", fail)
    with pytest.raises(OSError, match="snapshot failure"):
        artifacts.snapshot(session_id, pixels)
    assert list((artifacts.artifact_root / session_id).iterdir()) == []


def test_snapshot_racing_create_verifies_the_winning_bytes(artifacts, pixels, monkeypatch):
    session_id = uuid4().hex

    def race(temporary, destination):
        destination.write_bytes(temporary.read_bytes())
        raise FileExistsError("another snapshot won")

    monkeypatch.setattr("api.refinement.artifacts.os.link", race)
    digest = artifacts.snapshot(session_id, pixels)
    np.testing.assert_array_equal(decode_snapshot_bytes(artifacts.read(session_id, digest)), pixels)
    assert artifacts.cached_thumbnail(session_id, digest)
    assert not list((artifacts.artifact_root / session_id).glob("*.tmp"))


@pytest.mark.parametrize("cache", [None, b"", b"broken", b"0" * (1024 * 1024 + 1)])
def test_missing_or_corrupt_preview_is_rebuildable_without_changing_the_source(artifacts, pixels, cache):
    session_id = uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)
    preview = artifacts.thumbnail_path(session_id, digest)
    if cache is None:
        preview.unlink()
    else:
        preview.write_bytes(cache)
    assert artifacts.cached_thumbnail(session_id, digest) is None
    rebuilt = artifacts.write_thumbnail(session_id, digest, pixels)
    assert artifacts.cached_thumbnail(session_id, digest) == rebuilt
    assert artifacts.read(session_id, digest) == frozen


@pytest.mark.parametrize("read", ["read", "cached_thumbnail"])
def test_source_size_missing_and_digest_errors_precede_cache(artifacts, pixels, monkeypatch, read):
    session_id = uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    path = artifacts.path(session_id, digest)
    load = getattr(artifacts, read)
    with monkeypatch.context() as patch:
        patch.setattr("api.refinement.artifacts.MAX_SNAPSHOT_BYTES", path.stat().st_size - 1)
        with pytest.raises(ArtifactUnavailable, match="size limits"):
            load(session_id, digest)
    path.write_bytes(b"wrong digest")
    with pytest.raises(ArtifactUnavailable, match="digest"):
        load(session_id, digest)
    path.unlink()
    with pytest.raises(ArtifactUnavailable, match="unavailable"):
        load(session_id, digest)


@pytest.mark.parametrize(
    "session_id,digest",
    [("../outside", "a" * 64), ("a" * 32, "../outside"), ("A" * 32, "b" * 64), ("a" * 32, "b" * 63)],
)
def test_artifact_paths_reject_nonopaque_identifiers(artifacts, session_id, digest):
    with pytest.raises(SessionNotFound):
        artifacts.path(session_id, digest)


@pytest.mark.parametrize("kind", ["artifact", "directory", "preview", "publication"])
def test_symlink_escape_never_reads_or_overwrites_an_external_file(artifacts, pixels, tmp_path, kind):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / f"{digest}.png"
    target.write_bytes(b"external sentinel")
    operation, arguments = artifacts.read, (session_id, digest)
    if kind == "directory":
        artifacts.discard_session(session_id)
        (artifacts.artifact_root / session_id).symlink_to(outside, target_is_directory=True)
    elif kind == "artifact":
        path = artifacts.path(session_id, digest)
        path.unlink()
        path.symlink_to(target)
    elif kind == "preview":
        path = artifacts.thumbnail_path(session_id, digest)
        path.unlink()
        path.symlink_to(target)
        operation = artifacts.cached_thumbnail
    else:
        artifacts.publication_path(session_id, version_id).symlink_to(target)
        operation, arguments = artifacts.publication_path, (session_id, version_id)
    with pytest.raises(ArtifactUnavailable, match="outside"):
        operation(*arguments)
    assert target.read_bytes() == b"external sentinel"


def test_publication_uses_the_verified_buffer_without_reencoding(artifacts, pixels, monkeypatch):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)

    def no_codec(*args):
        pytest.fail("publishing already verified bytes must not invoke an image codec")

    monkeypatch.setattr("api.refinement.artifacts.encode_snapshot_image", no_codec)
    monkeypatch.setattr("api.refinement.artifacts.decode_snapshot_bytes", no_codec)
    # The store checks a buffer once, then publishes that buffer rather than re-reading.
    artifacts.path(session_id, digest).write_bytes(b"changed after verification")
    destination = artifacts.publication_path(session_id, version_id)
    with artifacts.publish(session_id, version_id, digest, frozen) as created:
        assert created
        assert destination.read_bytes() == frozen
    with artifacts.publish(session_id, version_id, digest, frozen) as created:
        assert not created
    destination.write_bytes(b"bad publication")
    with pytest.raises(ArtifactUnavailable, match="digest"), artifacts.publish(session_id, version_id, digest, frozen):
        pytest.fail("corrupted publication must not be overwritten")
    assert destination.read_bytes() == b"bad publication"
    assert not list((artifacts.artifact_root / session_id).glob("*.tmp"))


def test_publication_context_leaves_rollback_ownership_decisions_to_the_store(artifacts, pixels):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)
    destination = artifacts.publication_path(session_id, version_id)
    with (
        pytest.raises(RuntimeError, match="database rollback"),
        artifacts.publish(session_id, version_id, digest, frozen) as created,
    ):
        assert created
        raise RuntimeError("database rollback")
    assert destination.read_bytes() == frozen
    assert not list((artifacts.artifact_root / session_id).glob("*.tmp"))
    artifacts.remove_publication(session_id, version_id)
    assert not destination.exists()


def test_publication_replace_failure_cleans_temporary_without_creating_destination(artifacts, pixels, monkeypatch):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)

    def fail(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr("api.refinement.artifacts.os.replace", fail)
    with pytest.raises(OSError, match="replace failure"), artifacts.publish(session_id, version_id, digest, frozen):
        pytest.fail("failed publication must not yield")
    assert not artifacts.publication_path(session_id, version_id).exists()
    assert not list((artifacts.artifact_root / session_id).glob("*.tmp"))
    assert artifacts.read(session_id, digest) == frozen


def test_staged_deletion_restores_own_files_and_ignores_unrelated_names(artifacts, pixels):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)
    with artifacts.publish(session_id, version_id, digest, frozen):
        pass
    unrelated = artifacts.accepted_root / f"{session_id}-other.png"
    unrelated.write_bytes(b"keep")
    quarantine = artifacts.artifact_root.parent / ".deletion_quarantine"
    with pytest.raises(RuntimeError, match="rollback"), staged_deletion(quarantine) as stage:
        stage(artifacts.deletion_paths(session_id))
        assert not (artifacts.artifact_root / session_id).exists()
        assert not artifacts.publication_path(session_id, version_id).exists()
        raise RuntimeError("rollback")
    assert artifacts.read(session_id, digest) == frozen
    assert artifacts.cached_thumbnail(session_id, digest)
    assert artifacts.publication_path(session_id, version_id).read_bytes() == frozen
    with staged_deletion(quarantine) as stage:
        stage(artifacts.deletion_paths(session_id))
    assert not (artifacts.artifact_root / session_id).exists()
    assert not artifacts.publication_path(session_id, version_id).exists()
    assert unrelated.read_bytes() == b"keep"
    assert not list(quarantine.iterdir())


def test_discard_and_recovery_remove_only_managed_entries_not_symlink_targets(artifacts, pixels, tmp_path):
    session_id, version_id = uuid4().hex, uuid4().hex
    digest = artifacts.snapshot(session_id, pixels)
    frozen = artifacts.read(session_id, digest)
    with artifacts.publish(session_id, version_id, digest, frozen):
        pass
    committed = artifacts.publication_path(session_id, version_id)
    orphan = artifacts.accepted_root / f"{session_id}-{uuid4().hex}.png"
    orphan.write_bytes(frozen)
    unrelated = artifacts.accepted_root / "unrelated.png"
    unrelated.write_bytes(b"keep")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.png"
    sentinel.write_bytes(b"external")
    orphan_link = artifacts.accepted_root / f"{uuid4().hex}-{uuid4().hex}.png"
    orphan_link.symlink_to(sentinel)
    artifacts.recover_publications({committed.name})
    assert committed.read_bytes() == frozen
    assert not orphan.exists() and not orphan_link.is_symlink()
    assert unrelated.read_bytes() == b"keep" and sentinel.read_bytes() == b"external"
    artifacts.discard_session(session_id)
    directory = artifacts.artifact_root / session_id
    directory.symlink_to(outside, target_is_directory=True)
    artifacts.discard_session(session_id)
    assert not directory.is_symlink() and sentinel.read_bytes() == b"external"
    with pytest.raises(SessionNotFound):
        artifacts.discard_session("../outside")
    with pytest.raises(SessionNotFound):
        artifacts.deletion_paths("../outside")
