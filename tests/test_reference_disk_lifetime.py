"""Source galleries retain features and optional immutable disk evidence, never decoded galleries."""

import weakref
from types import SimpleNamespace

import numpy as np

from api.tasks.sources import build_source_references
from core.image_security import decode_snapshot_bytes, encode_snapshot_image


def test_reference_pixels_released_before_next_load_and_disk_evidence_unchanged(monkeypatch, tmp_path):
    alive = []
    snapshots = []

    def load(source):
        assert all(ref() is None for ref in alive)
        image = np.full((128, 128, 3), int(source), np.uint8)
        alive.append(weakref.ref(image))
        return image

    def snapshot(image):
        path = tmp_path / f"{len(snapshots)}.png"
        path.write_bytes(encode_snapshot_image(image))
        snapshots.append(path)
        return f"/api/v1/files/test/{path.name}"

    pipeline = SimpleNamespace(
        detector=SimpleNamespace(detect_faces=lambda *a, **k: [SimpleNamespace(kps=None)]),
        recognizer=SimpleNamespace(extract_embedding=lambda *a: np.ones(512, dtype=np.float32)),
    )
    monkeypatch.setattr("api.tasks.sources.load_image_from_source", load)
    monkeypatch.setattr("api.tasks.sources.calculate_yaw_ratio", lambda *a: 0)
    monkeypatch.setattr("api.tasks.sources.calculate_reference_quality", lambda *a: 1)
    refs = build_source_references(pipeline, ["11", "22", "33"], snapshot)
    assert all(ref() is None for ref in alive)
    assert all("image" not in ref and "snapshot_url" in ref for ref in refs)
    for path, expected in zip(snapshots, (11, 22, 33), strict=True):
        assert np.all(decode_snapshot_bytes(path.read_bytes()) == expected)
    assert all("snapshot_url" not in ref for ref in build_source_references(pipeline, ["44"]))
