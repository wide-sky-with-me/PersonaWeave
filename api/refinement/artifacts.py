"""Content-addressed session files, derived previews and atomic publication I/O.

This boundary knows managed paths and bytes, not SQLite ownership or revisions.
The session store keeps its database lock across publication/deletion decisions.
Decision: .agents/notes/platform/20261014-domain-extraction-with-stable-entrypoints.md
"""

import hashlib
import io
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import cv2
from PIL import Image

from api.refinement.contracts import ArtifactUnavailable, SessionNotFound
from core.image_security import MAX_SNAPSHOT_BYTES, decode_snapshot_bytes, encode_snapshot_image

_ID = re.compile(r"^[a-f0-9]{32}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PUBLICATION = re.compile(r"^[a-f0-9]{32}-[a-f0-9]{32}\.png$")


class SessionArtifacts:
    """Own filesystem effects; callers must separately authorize database references."""

    def __init__(self, artifact_root, accepted_root):
        self.artifact_root = Path(artifact_root).resolve()
        self.accepted_root = Path(accepted_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.accepted_root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id, digest):
        """Resolve an opaque artifact identifier without escaping the managed root."""
        if not _ID.fullmatch(session_id) or not _DIGEST.fullmatch(digest):
            raise SessionNotFound("Artifact not found")
        path = (self.artifact_root / session_id / f"{digest}.png").resolve()
        if not path.is_relative_to(self.artifact_root):
            raise ArtifactUnavailable("Artifact is outside the managed root")
        return path

    @staticmethod
    def verified_bytes(path, digest):
        """Read intact owned bytes without decoding a full-resolution canvas again."""
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise ArtifactUnavailable("Artifact exceeds image size limits")
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactUnavailable("Artifact is unavailable") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactUnavailable("Artifact digest verification failed")
        # Pixel validation happened before committing this digest. History and accept
        # must work even when there is no headroom to decode another complete canvas.
        return data

    def read(self, session_id, digest):
        """Read and checksum one artifact; this does not grant session ownership."""
        return self.verified_bytes(self.path(session_id, digest), digest)

    def snapshot(self, session_id, image):
        """Encode once, validate and atomically retain content-addressed PNG bytes."""
        data = encode_snapshot_image(image)
        decode_snapshot_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        path = self.path(session_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.verified_bytes(path, digest)
            self.write_thumbnail(session_id, digest, image)
            return digest
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Create-only: an immutable existing artifact is never overwritten.
            try:
                os.link(temporary, path)
            except FileExistsError:
                self.verified_bytes(path, digest)
        finally:
            temporary.unlink(missing_ok=True)
        self.write_thumbnail(session_id, digest, image)
        return digest

    def thumbnail_path(self, session_id, digest):
        """Resolve a rebuildable preview, checking its own possible symlink too."""
        path = self.path(session_id, digest).with_suffix(".preview")
        if not path.resolve().is_relative_to(self.artifact_root):
            raise ArtifactUnavailable("Preview is outside the managed root")
        return path

    def write_thumbnail(self, session_id, digest, image):
        """Cache a derived 384px JPEG; full-resolution evidence/acceptance stays untouched."""
        scale = min(1.0, 384 / max(image.shape[:2]))
        small = cv2.resize(
            image,
            (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("Could not encode refinement preview")
        data = encoded.tobytes()
        path = self.thumbnail_path(session_id, digest)
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(hashlib.sha256(data).hexdigest().encode("ascii") + data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return data

    def cached_thumbnail(self, session_id, digest):
        """Checksum the source in bounded chunks before returning a small verified cache."""
        path = self.path(session_id, digest)
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as source:
                if os.fstat(source.fileno()).st_size > MAX_SNAPSHOT_BYTES:
                    raise ArtifactUnavailable("Artifact exceeds image size limits")
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    checksum.update(chunk)
        except OSError as exc:
            raise ArtifactUnavailable("Artifact is unavailable") from exc
        if checksum.hexdigest() != digest:
            raise ArtifactUnavailable("Artifact digest verification failed")
        try:
            with self.thumbnail_path(session_id, digest).open("rb") as preview:
                data = preview.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024 or hashlib.sha256(data[64:]).hexdigest().encode("ascii") != data[:64]:
                return None
            return data[64:]
        except FileNotFoundError:
            return None

    @staticmethod
    def image_shape(data):
        """Inspect the header of already verified PNG bytes without decoding pixels."""
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        return height, width, 3

    def publication_path(self, session_id, version_id):
        """Resolve a server-derived publication name beneath the accepted root."""
        if not _ID.fullmatch(session_id) or not _ID.fullmatch(version_id):
            raise SessionNotFound("Version not found in this session")
        destination = (self.accepted_root / f"{session_id}-{version_id}.png").resolve()
        if not destination.is_relative_to(self.accepted_root):
            raise ArtifactUnavailable("Publication is outside the managed root")
        return destination

    @contextmanager
    def publish(self, session_id, version_id, digest, data):
        """Publish the checked buffer and report whether this attempt created the file.

        Keep this context open past SQLite commit/rollback cleanup. The caller owns
        rollback decisions: a concurrent retry may already own a published file.
        """
        destination = self.publication_path(session_id, version_id)
        temporary = None
        try:
            if destination.exists():
                self.verified_bytes(destination, digest)
                yield False
            else:
                temporary = self.path(session_id, digest).with_name(f".publish-{uuid4().hex}.tmp")
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                yield True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remove_publication(self, session_id, version_id):
        """Remove a publication only after the caller proves no transaction owns it."""
        self.publication_path(session_id, version_id).unlink(missing_ok=True)

    def deletion_paths(self, session_id):
        """Enumerate direct children for shared staged deletion, without following links."""
        if not _ID.fullmatch(session_id):
            raise SessionNotFound("Session not found")
        paths = [(self.artifact_root, self.artifact_root / session_id)]
        # Persisted URLs never select deletion targets, including unrelated files.
        paths.extend(
            (self.accepted_root, path)
            for path in self.accepted_root.glob(f"{session_id}-*.png")
            if _PUBLICATION.fullmatch(path.name)
        )
        return paths

    def discard_session(self, session_id):
        """Remove an unregistered attempt after the caller excludes concurrent registration."""
        if not _ID.fullmatch(session_id):
            raise SessionNotFound("Session not found")
        directory = self.artifact_root / session_id
        if directory.is_symlink():
            directory.unlink()
        elif directory.is_dir() and directory.resolve().is_relative_to(self.artifact_root):
            shutil.rmtree(directory)

    def recover_publications(self, committed_names):
        """Remove only owned-format files absent from the store's committed publications."""
        # A process can die between os.replace and SQLite commit. Only this
        # subsystem owns these UUID filenames; leave every other filename alone.
        for path in self.accepted_root.glob("*.png"):
            if _PUBLICATION.fullmatch(path.name) and path.name not in committed_names:
                path.unlink()
