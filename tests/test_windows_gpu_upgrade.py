"""Differential upgrades must preserve user state, validate first, and tolerate interrupted copying."""

import hashlib
import json
from pathlib import Path

import pytest

from api.portable import instance_lock
from scripts.upgrade_windows_gpu import apply_update, digest, owned_path


def write_files(root, files):
    result = {}
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        result[name] = hashlib.sha256(value).hexdigest()
    return result


@pytest.fixture
def upgrade(tmp_path):
    root = tmp_path / "app"
    old = write_files(
        root,
        {
            "runtime/engine.dll": b"cpu",
            "runtime/cpu.dist-info/METADATA": b"old",
            "models/model.onnx": b"unchanged",
            "runtime/__pycache__/sample.pyc": b"cached",
        },
    )
    write_files(
        root,
        {
            ".env": b"TEST_KEY=keep",
            "data/project.sqlite3": b"user-project",
            "outputs/result.png": b"user-result",
            "runtime/__pycache__/sample.pyc": b"regenerated",
        },
    )
    (root / "manifest.json").write_text(json.dumps({"files": old}))
    payload = root / "gpu-update"
    changed = write_files(payload / "files", {"runtime/engine.dll": b"gpu", "runtime/cuda.dll": b"cuda"})
    new = {**changed, "models/model.onnx": old["models/model.onnx"]}
    (payload / "manifest.json").write_text(json.dumps({"files": new}))
    (payload / "update.json").write_text(
        json.dumps(
            {
                "base_manifest_sha256": digest(root / "manifest.json"),
                "new_manifest_sha256": digest(payload / "manifest.json"),
                "base_files": old,
            }
        )
    )
    return root, payload


def test_upgrade_preserves_user_state_removes_cpu_metadata_and_is_idempotent(upgrade):
    root, payload = upgrade
    apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"gpu"
    assert not (root / "runtime/cpu.dist-info/METADATA").exists()
    assert not (root / "runtime/__pycache__").exists()
    assert not (payload / "files").exists()
    assert (root / ".env").read_bytes() == b"TEST_KEY=keep"
    assert (root / "data/project.sqlite3").read_bytes() == b"user-project"
    assert (root / "outputs/result.png").read_bytes() == b"user-result"
    apply_update(root, payload)


def test_corrupt_payload_changes_no_program_files(upgrade):
    root, payload = upgrade
    (payload / "files/runtime/cuda.dll").write_bytes(b"bad")
    before = digest(root / "manifest.json")
    with pytest.raises(ValueError, match="校验失败"):
        apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"cpu"
    assert digest(root / "manifest.json") == before


def test_active_workbench_blocks_upgrade(upgrade):
    root, payload = upgrade
    with instance_lock(root), pytest.raises(RuntimeError, match="先关闭"):
        apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"cpu"


def test_interrupted_upgrade_resumes_without_overwriting_user_edits(upgrade, monkeypatch):
    root, payload = upgrade
    original = Path.replace

    def fail_on_cuda(path, target):
        if target.name == "cuda.dll":
            raise OSError("interrupted")
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_on_cuda)
        with pytest.raises(OSError, match="interrupted"):
            apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"gpu"
    apply_update(root, payload)
    assert (root / "runtime/cuda.dll").read_bytes() == b"cuda"


def test_user_modified_program_is_not_overwritten(upgrade):
    root, payload = upgrade
    (root / "runtime/engine.dll").write_bytes(b"user modification")
    with pytest.raises(ValueError, match="已被修改"):
        apply_update(root, payload)
    assert not (root / "runtime/cuda.dll").exists()


def test_unchanged_model_is_not_rehashed_or_rewritten(upgrade, monkeypatch):
    root, payload = upgrade
    from scripts import upgrade_windows_gpu

    original = upgrade_windows_gpu.digest

    def checked(path):
        assert path != root / "models/model.onnx", "Unchanged weights are not update targets"
        return original(path)

    monkeypatch.setattr(upgrade_windows_gpu, "digest", checked)
    apply_update(root, payload)
    assert (root / "models/model.onnx").read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/tmp/outside",
        "C:/outside",
        "..\\escape",
        ".env",
        "data/projects.sqlite3",
        "outputs/result.png",
        "runtime/../.env",
        "./.env",
        "runtime/../data/projects.sqlite3",
    ],
)
def test_update_paths_cannot_touch_private_or_external_data(tmp_path, name):
    with pytest.raises(ValueError, match="非法路径"):
        owned_path(tmp_path, name)
