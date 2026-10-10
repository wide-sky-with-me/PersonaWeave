"""Differential upgrades must preserve user state, validate first, and tolerate interrupted copying."""

import hashlib
import json
import shutil
import zipfile
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
    assert not (root / "data/portable-update.json").exists()
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
    from api.portable import UPDATE_MARKER, check_update_complete

    assert (root / "runtime/engine.dll").read_bytes() == b"gpu"
    assert (root / "data" / UPDATE_MARKER).exists()
    with pytest.raises(RuntimeError, match="升级尚未完成"):
        check_update_complete(root)
    apply_update(root, payload)
    assert not (root / "data" / UPDATE_MARKER).exists()
    check_update_complete(root)
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
        ".ENV",
        "OUTPUTS/result.png",
        "LOGS/private.txt",
        "DATA/projects.sqlite3",
        "runtime/../DATA/private.png",
    ],
)
def test_update_paths_cannot_touch_private_or_external_data(tmp_path, name):
    with pytest.raises(ValueError, match="非法路径"):
        owned_path(tmp_path, name)


@pytest.fixture
def upgrade_build(tmp_path):
    from scripts.build_windows_gpu_upgrade import build

    cpu, gpu = tmp_path / "cpu", tmp_path / "gpu"
    for root, edition in ((cpu, "cpu"), (gpu, "gpu")):
        files = write_files(root, {"runtime/engine.dll": edition.encode(), "models/model.onnx": b"unchanged"})
        (root / "manifest.json").write_text(
            json.dumps(
                {"edition": f"windows-x64-{edition}", "files": files, "dependency_fingerprint": edition + "-inputs"}
            )
        )
    return build, cpu, gpu, tmp_path / "upgrade.zip"


def test_upgrade_builder_preserves_new_manifest_contract_and_applies(upgrade_build, tmp_path):
    build, cpu, gpu, output = upgrade_build
    build(cpu, gpu, output)
    installation = tmp_path / "installed"
    shutil.copytree(cpu, installation)
    with zipfile.ZipFile(output) as archive:
        assert "gpu-update/files/models/model.onnx" not in archive.namelist()
        assert json.loads(archive.read("gpu-update/manifest.json"))["dependency_fingerprint"] == "gpu-inputs"
        archive.extractall(installation)
    apply_update(installation, installation / "gpu-update")
    assert (installation / "manifest.json").read_bytes() == (gpu / "manifest.json").read_bytes()
    assert (installation / "runtime/engine.dll").read_bytes() == b"gpu"


@pytest.mark.parametrize("failure", ["zip_write", "source_changed", "checksum_write", "checksum_publish"])
def test_upgrade_archive_failures_preserve_prior_release(upgrade_build, monkeypatch, failure):
    build, cpu, gpu, output = upgrade_build
    build(cpu, gpu, output)
    checksum = output.with_suffix(".zip.sha256")
    previous = output.read_bytes(), checksum.read_bytes()
    if failure in {"zip_write", "source_changed"}:
        original = zipfile.ZipFile.write

        def write(archive, source, *args, **kwargs):
            if failure == "zip_write":
                raise OSError("injected ZIP failure")
            Path(source).write_bytes(b"changed after the input check")
            return original(archive, source, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "write", write)
    elif failure == "checksum_write":
        original = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name == checksum.name:
                raise OSError("injected checksum failure")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write_text)
    else:
        original = Path.replace
        failed = False

        def replace(path, target):
            nonlocal failed
            if Path(target) == checksum and not failed:
                failed = True
                raise OSError("injected publication failure")
            return original(path, target)

        monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises((OSError, ValueError), match=r"injected|checksum mismatch"):
        build(cpu, gpu, output)
    assert (output.read_bytes(), checksum.read_bytes()) == previous
    assert not list(output.parent.glob(".*-build-*"))


def test_upgrade_builder_rejects_symlink_escape(upgrade_build, tmp_path):
    build, cpu, gpu, output = upgrade_build
    external = tmp_path / "external.dll"
    external.write_bytes(b"gpu")
    source = gpu / "runtime/engine.dll"
    source.unlink()
    source.symlink_to(external)
    with pytest.raises(ValueError, match="非法路径"):
        build(cpu, gpu, output)
    assert not output.exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_published_manifest_pending_marker_is_verified_before_clearing(upgrade, monkeypatch, corrupt):
    root, payload = upgrade
    marker = root / "data/portable-update.json"
    original = Path.unlink

    def fail_marker(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("marker cleanup interrupted")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_marker)
        with pytest.raises(PermissionError, match="cleanup interrupted"):
            apply_update(root, payload)
    assert digest(root / "manifest.json") == digest(payload / "manifest.json")
    assert marker.exists()
    if corrupt:
        (root / "runtime/cuda.dll").write_bytes(b"unrelated modification")
        with pytest.raises(ValueError, match="校验失败"):
            apply_update(root, payload)
        assert marker.exists()
    else:
        apply_update(root, payload)
        assert not marker.exists()
        assert not (payload / "files").exists()


def test_other_update_cannot_replace_pending_transaction(upgrade):
    root, payload = upgrade
    marker = root / "data/portable-update.json"
    marker.write_text(json.dumps({"new_manifest_sha256": "another update"}))
    with pytest.raises(ValueError, match="其他升级"):
        apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"cpu"
    assert json.loads(marker.read_text())["new_manifest_sha256"] == "another update"


def test_marker_publication_failure_changes_no_application_bytes(upgrade, monkeypatch):
    root, payload = upgrade
    original = Path.replace

    def fail_marker(path, target):
        if Path(target).name == "portable-update.json":
            raise OSError("marker write interrupted")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_marker)
    with pytest.raises(OSError, match="marker write interrupted"):
        apply_update(root, payload)
    assert (root / "runtime/engine.dll").read_bytes() == b"cpu"
    assert not (root / "runtime/cuda.dll").exists()


def test_older_installation_gets_guarded_launcher_before_other_changes(upgrade, monkeypatch):
    root, payload = upgrade
    # Deliberately append the launcher after runtime changes in the target manifest.
    update = json.loads((payload / "update.json").read_text())
    target = json.loads((payload / "manifest.json").read_text())
    target["files"].update(write_files(payload / "files", {"api/portable.py": b"guarded launcher"}))
    (payload / "manifest.json").write_text(json.dumps(target))
    update["new_manifest_sha256"] = digest(payload / "manifest.json")
    (payload / "update.json").write_text(json.dumps(update))
    original = Path.replace

    def fail_first_runtime(path, destination):
        if Path(destination) == root / "runtime/engine.dll":
            assert (root / "api/portable.py").read_bytes() == b"guarded launcher"
            raise OSError("runtime interrupted after launcher bootstrap")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", fail_first_runtime)
    with pytest.raises(OSError, match="after launcher bootstrap"):
        apply_update(root, payload)
    assert (root / "data/portable-update.json").exists()
    assert (root / "runtime/engine.dll").read_bytes() == b"cpu"
