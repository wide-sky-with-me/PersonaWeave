"""Portable-launcher isolation and offline packaging regressions."""

import asyncio
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from api.portable import configure, instance_lock
from api.routes.system import get_sample_manifest
from scripts.build_windows_portable import MSVC_FILES, check_public_asset, portable_requirements, trim_development_files


def test_vc_runtime_includes_onnx_extension_and_repair_matches_builder():
    from pathlib import Path

    assert "msvcp140_1.dll" in MSVC_FILES
    repair = Path("scripts/repair_windows_runtime.ps1").read_text()
    for name in MSVC_FILES:
        assert name in repair


def test_build_checks_missing_vc_component_before_installing_dependencies(tmp_path):
    from scripts.build_windows_portable import build

    for name in MSVC_FILES:
        if name != "msvcp140_1.dll":
            (tmp_path / name).write_bytes(b"fixture")
    with pytest.raises(ValueError, match=r"Missing Microsoft VC runtime files: msvcp140_1\.dll"):
        build(tmp_path / "not-created", tmp_path)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".ENV",
        "data/portable-runtime.json",
        "DATA/private.png",
        "data/projects.sqlite3",
        "data/private.png",
        "outputs/result.png",
        "OUTPUTS/result.png",
        "LOGS/private.txt",
        "api/private.DB",
        "OUTPUTS\\result.png",
    ],
)
def test_build_rejects_credentials_and_prior_runtime_data(path):
    with pytest.raises(ValueError, match="private data"):
        check_public_asset(path)


def test_cpu_requirements_keep_lock_pins_without_linux_gpu_packages():
    exported = "# generated\nnumpy==2.5.2 ; python_version >= '3.12'\nonnxruntime-gpu==1.20.2\nnvidia-cudnn-cu12==9.0\nopencv-python==5.0\nopencv-python-headless==5.0\n"
    result = portable_requirements(exported)
    assert "numpy==2.5.2 ; python_version >= '3.12'" in result
    assert "onnxruntime==" in result
    assert "nvidia-" not in result
    assert "onnxruntime-gpu" not in result
    assert "opencv-python==" not in result
    assert "opencv-python-headless==5.0" in result


def test_gpu_requirements_keep_nvidia_pins_and_exclude_cpu_runtime():
    result = portable_requirements(
        "onnxruntime-gpu==1.20.2\nnvidia-cudnn-cu12==9.24.0.43\nonnxruntime==1.23.2\n", "gpu"
    )
    assert "nvidia-cudnn-cu12==9.24.0.43" in result
    assert "onnxruntime-gpu==1.23.2" in result
    assert "onnxruntime==" not in result
    assert "1.20.2" not in result


def test_trimming_preserves_runtime_dlls_and_licenses(tmp_path):
    package = tmp_path / "Lib/site-packages/nvidia/cublas"
    package.mkdir(parents=True)
    for name in ("runtime.dll", "LICENSE", "library.lib", "header.h", "cache.pyc"):
        (package / name).write_bytes(b"test")
    assert trim_development_files(tmp_path) == 12
    assert sorted(path.name for path in package.iterdir()) == ["LICENSE", "runtime.dll"]


def test_second_launcher_cannot_open_same_database(tmp_path):
    with instance_lock(tmp_path) as first:
        assert first
        with instance_lock(tmp_path) as second:
            assert not second
    with instance_lock(tmp_path) as restarted:
        assert restarted


def test_ai_configuration_preserves_blanks_and_hides_keys(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text('AI_VISION_API_KEY="test-existing-key"\n', encoding="utf-8")
    inputs = iter(["openai/test-vision", "https://example.com/v1", "openai/test-image", "https://example.com/v1"])
    secrets = iter(["", 'test-new-"quoted-key'])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("api.portable.getpass.getpass", lambda _: next(secrets))
    configure(tmp_path)
    values = dotenv_values(tmp_path / ".env")
    assert values["AI_VISION_API_KEY"] == "test-existing-key"
    assert values["AI_IMAGE_API_KEY"] == 'test-new-"quoted-key'
    assert "test-existing-key" not in capsys.readouterr().out
    assert not (tmp_path / ".env.tmp").exists()


def test_sample_manifest_only_advertises_shipped_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "data/test_target"
    directory.mkdir(parents=True)
    (directory / "01.png").write_bytes(b"fixture")
    result = asyncio.run(get_sample_manifest())
    assert [item["path"] for item in result["sources"]] == ["data/test_target/01.png"]
    assert result["targets"] == []


def test_frontend_rebuild_removes_stale_chunks_only_after_success(tmp_path, monkeypatch):
    from scripts import build_windows_portable as builder

    source = tmp_path / "source"
    (source / "web/dist").mkdir(parents=True)
    (source / "web/dist/current.js").write_text("new")
    output = tmp_path / "output"
    (output / "web/dist").mkdir(parents=True)
    (output / "web/dist/old.js").write_text("old")
    monkeypatch.setattr(builder, "ROOT", source)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        builder.prepare_frontend(output)
    assert (output / "web/dist/old.js").exists()

    def succeed(command, **kwargs):
        assert command == ["pnpm", "--dir", "web", "build"]
        assert kwargs == {"cwd": source, "check": True}

    monkeypatch.setattr(builder.subprocess, "run", succeed)
    builder.prepare_frontend(output)
    assert [p.name for p in (output / "web/dist").iterdir()] == ["current.js"]


def test_archive_checks_bytes_before_replacing_previous_release(tmp_path):
    from scripts.build_windows_portable import write_archive

    output = tmp_path / "PW-CPU"
    output.mkdir()
    (output / "test.txt").write_bytes(b"original")
    (output / "manifest.json").write_text(json.dumps({"files": {"test.txt": hashlib.sha256(b"original").hexdigest()}}))
    archive = tmp_path / "release.zip"
    write_archive(output, archive)
    with zipfile.ZipFile(archive) as package:
        assert package.read("PW-CPU/test.txt") == b"original"
    prior = archive.read_bytes()
    (output / "test.txt").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Archive checksum mismatch"):
        write_archive(output, archive)
    assert archive.read_bytes() == prior
    assert not archive.with_suffix(".zip.partial").exists()
    (output / "untracked.txt").write_text("extra")
    with pytest.raises(ValueError, match="file set"):
        write_archive(output, archive)


def test_launcher_rejects_unwritable_data_with_actionable_message(tmp_path, monkeypatch):
    from api.portable import check_writable_directories

    check_writable_directories(tmp_path)

    def denied(**kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr("api.portable.tempfile.TemporaryFile", denied)
    with pytest.raises(OSError, match="程序目录无法写入"):
        check_writable_directories(tmp_path)


def test_tokenizer_cache_missing_crlf_and_corruption(tmp_path, monkeypatch):
    from scripts import build_windows_portable as builder

    canonical = b"valid\ntokenizer\n"
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "TOKENIZER_SHA256", hashlib.sha256(canonical).hexdigest())
    cached = tmp_path / "build/windows/cl100k_base.tiktoken"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(canonical)
    runtime = tmp_path / "runtime"
    target = (
        runtime / "Lib/site-packages/litellm/litellm_core_utils/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    )
    target.parent.mkdir(parents=True)
    builder.normalize_tokenizer_cache(runtime)
    assert target.read_bytes() == canonical
    target.write_bytes(canonical.replace(b"\n", b"\r\n"))
    builder.normalize_tokenizer_cache(runtime)
    assert target.read_bytes() == canonical
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="tokenizer cache checksum mismatch"):
        builder.normalize_tokenizer_cache(runtime)
    assert target.read_bytes() == b"corrupt"


@pytest.fixture
def portable_build_tree(tmp_path, monkeypatch):
    """Exercise the complete builder with tiny assets; only external build tools are simulated."""
    from scripts import build_windows_portable as builder

    root = tmp_path / "source"
    for name in (
        "api/portable.py",
        "core/__init__.py",
        "utils/__init__.py",
        "config/ai_gateway.yaml",
        "web/dist/index.html",
        "models/README.md",
        "models/fixture.onnx",
        "docs/windows-portable.md",
        "docs/windows-portable-validation.md",
        "scripts/check_windows_portable.py",
        "scripts/collect_windows_performance.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture source", encoding="utf-8")
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for name in builder.MSVC_FILES:
        (msvc / name).write_bytes(b"fixture VC")
    cache = root / "build/windows"
    cache.mkdir(parents=True)
    embed = cache / f"python-{builder.PYTHON_VERSION}-embed-amd64.zip"
    with zipfile.ZipFile(embed, "w") as package:
        for name in ("python.exe", "python313.dll", "python313.zip"):
            package.writestr(name, b"fixture Python")
    tokenizer = b"fixture canonical tokenizer\n"
    (cache / "cl100k_base.tiktoken").write_bytes(tokenizer)
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "PYTHON_SHA256", builder.sha256(embed))
    monkeypatch.setattr(builder, "TOKENIZER_SHA256", hashlib.sha256(tokenizer).hexdigest())
    monkeypatch.setattr(builder, "MODEL_SHA256", {"fixture.onnx": builder.sha256(root / "models/fixture.onnx")})
    monkeypatch.setattr(builder, "SAMPLES", {})
    state = SimpleNamespace(exported="uvicorn==0.35.0\nlitellm==1.98.0\n", installs=0, frontend_builds=0, fail=None)
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *args, **kwargs: state.exported)

    def run(command, **kwargs):
        if command[0] == "pnpm":
            state.frontend_builds += 1
            if state.fail == "frontend":
                raise subprocess.CalledProcessError(1, command)
            return
        assert command[:4] == ["uv", "--no-config", "pip", "install"]
        state.installs += 1
        if state.fail == "dependencies":
            raise subprocess.CalledProcessError(1, command)
        site = Path(command[command.index("--target") + 1])
        for name in (
            "onnxruntime/__init__.py",
            "uvicorn/__init__.py",
            "litellm/__init__.py",
            "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
        ):
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture installed module")
        (site / "litellm/litellm_core_utils/tokenizers").mkdir(parents=True)
        requirements = Path(command[command.index("-r") + 1]).read_text()
        distribution = "onnxruntime-gpu" if "onnxruntime-gpu==" in requirements else "onnxruntime"
        metadata = site / f"{distribution.replace('-', '_')}-1.23.2.dist-info/METADATA"
        metadata.parent.mkdir()
        metadata.write_text(f"Name: {distribution}\nVersion: 1.23.2\n", encoding="utf-8")
        if state.fail == "runtime":
            (site / "onnxruntime/capi/onnxruntime_pybind11_state.pyd").unlink()

    monkeypatch.setattr(builder.subprocess, "run", run)
    return SimpleNamespace(builder=builder, root=root, output=root / "dist/PW-CPU", msvc=msvc, state=state)


def release_bytes(directory):
    """Capture all fixture release files, including the archive and its checksum."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("edition", ["cpu", "gpu"])
def test_full_build_and_verified_dependency_reuse(portable_build_tree, edition):
    tree = portable_build_tree
    tree.output = tree.output.with_name(f"PW-{edition.upper()}")
    tree.builder.build(tree.output, tree.msvc, edition=edition)
    manifest = json.loads((tree.output / "manifest.json").read_text())
    assert manifest["dependency_fingerprint"]
    (tree.root / "api/portable.py").write_text("next backend")
    (tree.root / "web/dist/index.html").write_text("next frontend")
    tree.builder.build(tree.output, tree.msvc, skip_deps=True, edition=edition)
    assert tree.state.installs == 1
    assert tree.state.frontend_builds == 2
    assert (tree.output / "api/portable.py").read_text() == "next backend"
    with zipfile.ZipFile(tree.output.with_suffix(".zip")) as package:
        assert package.read(f"{tree.output.name}/web/dist/index.html") == b"next frontend"
        assert all(name.startswith(tree.output.name + "/") for name in package.namelist())
    assert not list(tree.output.parent.glob(".*-build-*"))


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "extra", "requirements", "old_manifest", "new_inputs", "metadata"]
)
def test_skip_deps_rejects_invalid_existing_installation(portable_build_tree, damage):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    module = tree.output / "runtime/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd"
    if damage == "missing":
        module.unlink()
    elif damage == "changed":
        module.write_bytes(b"corruption")
    elif damage == "extra":
        (module.parent / "untracked.pyd").write_bytes(b"unexpected")
    elif damage == "requirements":
        (tree.output / "requirements-windows.txt").write_text("stale==0.1\n")
    elif damage == "new_inputs":
        tree.state.exported = "uvicorn==0.36.0\nlitellm==1.98.0\n"
    else:
        manifest_path = tree.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if damage == "old_manifest":
            manifest.pop("dependency_fingerprint")
        else:
            metadata = tree.output / "runtime/Lib/site-packages/onnxruntime-1.23.2.dist-info/METADATA"
            metadata.write_text("Name: onnxruntime-gpu\nVersion: 1.23.2\n")
            manifest["files"][metadata.relative_to(tree.output).as_posix()] = tree.builder.sha256(metadata)
        manifest_path.write_text(json.dumps(manifest))
    before = release_bytes(tree.output.parent)
    with pytest.raises(ValueError, match="rebuild without --skip-deps"):
        tree.builder.build(tree.output, tree.msvc, skip_deps=True)
    assert release_bytes(tree.output.parent) == before
    assert tree.state.installs == 1 and tree.state.frontend_builds == 1


def test_skip_deps_requires_a_successful_build(portable_build_tree):
    tree = portable_build_tree
    with pytest.raises(ValueError, match="Missing or changed dependency fingerprint"):
        tree.builder.build(tree.output, tree.msvc, skip_deps=True)
    assert not tree.output.exists()


@pytest.mark.parametrize("failure", ["dependencies", "runtime", "frontend", "model", "archive"])
def test_full_build_failure_preserves_previous_directory_and_archive(portable_build_tree, monkeypatch, failure):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    before = release_bytes(tree.output.parent)
    (tree.root / "api/portable.py").write_text("incompatible next backend")
    if failure == "model":
        (tree.root / "models/fixture.onnx").write_bytes(b"corrupt model")
    elif failure == "archive":

        def fail_write(*args, **kwargs):
            raise OSError("injected ZIP write failure")

        monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)
    else:
        tree.state.fail = failure
    with pytest.raises((ValueError, subprocess.CalledProcessError, OSError)):
        tree.builder.build(tree.output, tree.msvc)
    assert release_bytes(tree.output.parent) == before
    assert not list(tree.output.parent.glob(".*-build-*"))


def test_runtime_changed_during_copy_is_not_recertified(portable_build_tree, monkeypatch):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    before = release_bytes(tree.output.parent)
    original = tree.builder.shutil.copytree

    def copytree(source, target, *args, **kwargs):
        result = original(source, target, *args, **kwargs)
        if Path(source) == tree.output / "runtime":
            (Path(target) / "Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd").write_bytes(b"changed")
        return result

    monkeypatch.setattr(tree.builder.shutil, "copytree", copytree)
    with pytest.raises(ValueError, match="checksum mismatch"):
        tree.builder.build(tree.output, tree.msvc, skip_deps=True)
    assert release_bytes(tree.output.parent) == before
    assert not list(tree.output.parent.glob(".*-build-*"))


@pytest.mark.parametrize("suffix", [".zip", ".zip.sha256"])
def test_publish_rename_failure_rolls_back_entire_release(portable_build_tree, monkeypatch, suffix):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    before = release_bytes(tree.output.parent)
    (tree.root / "api/portable.py").write_text("next backend")
    original = Path.replace
    failed = False

    def replace(source, target):
        nonlocal failed
        if Path(target) == tree.output.with_suffix(suffix) and not failed:
            failed = True
            raise PermissionError("injected publication failure")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError, match="publication failure"):
        tree.builder.build(tree.output, tree.msvc, skip_deps=True)
    assert failed
    assert release_bytes(tree.output.parent) == before
    assert not list(tree.output.parent.glob(".*-build-*"))


@pytest.mark.parametrize(
    "private_name", [".env", ".ENV", "OUTPUTS/private.png", "DATA/private.png", "LOGS/private.txt", "api/private.DB"]
)
def test_private_distribution_is_rejected_before_build_tools(portable_build_tree, monkeypatch, private_name):
    tree = portable_build_tree
    private = tree.output / private_name
    private.parent.mkdir(parents=True)
    private.write_text("TEST_ONLY=private-fixture")
    before = release_bytes(tree.output.parent)
    monkeypatch.setattr(tree.builder.subprocess, "check_output", lambda *args, **kwargs: pytest.fail("must not export"))
    with pytest.raises(ValueError, match="private data"):
        tree.builder.build(tree.output, tree.msvc)
    assert release_bytes(tree.output.parent) == before


def test_directory_only_build_keeps_prior_archive_explicitly(portable_build_tree, capsys):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    prior = {suffix: tree.output.with_suffix(suffix).read_bytes() for suffix in (".zip", ".zip.sha256")}
    (tree.root / "api/portable.py").write_text("directory-only update")
    tree.builder.build(tree.output, tree.msvc, skip_deps=True, archive=False)
    assert (tree.output / "api/portable.py").read_text() == "directory-only update"
    assert all(tree.output.with_suffix(suffix).read_bytes() == value for suffix, value in prior.items())
    assert "existing ZIP and checksum were retained unchanged" in capsys.readouterr().out


def test_repo_alias_is_resolved_without_rejecting_normal_output(portable_build_tree, tmp_path):
    tree = portable_build_tree
    alias = tmp_path / "repository-alias"
    alias.symlink_to(tree.root, target_is_directory=True)
    tree.builder.build(alias / "dist/PW-CPU", tree.msvc, archive=False)
    assert (tree.output / "manifest.json").is_file()


@pytest.mark.parametrize("kind", ["file", "directory", "root"])
def test_public_build_rejects_symlinks_before_tools(portable_build_tree, monkeypatch, tmp_path, kind):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    if kind == "root":
        original = tree.output.with_name("original")
        tree.output.rename(original)
        link = tree.output
        link.symlink_to(original, target_is_directory=True)
    else:
        external = tmp_path / "outside"
        external.mkdir()
        (external / "private.txt").write_text("private fixture")
        if kind == "directory":
            link = tree.output / "data"
            link.symlink_to(external, target_is_directory=True)
        else:
            link = tree.output / "runtime/external.dll"
            link.symlink_to(external / "private.txt")
    monkeypatch.setattr(tree.builder.subprocess, "check_output", lambda *a, **k: pytest.fail("must not export"))
    with pytest.raises(ValueError, match="symlink"):
        tree.builder.build(tree.output, tree.msvc)
    assert link.is_symlink()


@pytest.mark.parametrize("failure", ["checksum_write", "checksum_publish"])
def test_standalone_archive_preserves_zip_and_checksum_on_failure(portable_build_tree, monkeypatch, failure):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc)
    archive = tree.output.with_suffix(".zip")
    checksum = tree.output.with_suffix(".zip.sha256")
    previous = (archive.read_bytes(), checksum.read_bytes())
    if failure == "checksum_write":
        original = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name == checksum.name:
                raise OSError("injected checksum write failure")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write_text)
    else:
        original = Path.replace
        failed = False

        def replace(path, target):
            nonlocal failed
            if Path(target) == checksum and not failed:
                failed = True
                raise OSError("injected checksum publication failure")
            return original(path, target)

        monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="injected checksum"):
        tree.builder.write_archive(tree.output, archive)
    assert (archive.read_bytes(), checksum.read_bytes()) == previous
    assert not list(tree.output.parent.glob(".*-build-*"))


def test_archive_cannot_be_written_inside_its_source(portable_build_tree):
    tree = portable_build_tree
    tree.builder.build(tree.output, tree.msvc, archive=False)
    before = release_bytes(tree.output)
    with pytest.raises(ValueError, match="outside the distribution"):
        tree.builder.write_archive(tree.output, tree.output / "nested.zip")
    assert release_bytes(tree.output) == before


def test_launcher_blocks_pending_update_before_opening_service(tmp_path, monkeypatch):
    import sys

    from api import portable

    (tmp_path / "data").mkdir()
    marker = tmp_path / "data" / portable.UPDATE_MARKER
    marker.write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portable, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["Start.cmd", "--no-browser"])
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "old")
    monkeypatch.setenv("PERSONAWEAVE_AI_CONFIG", "old")
    monkeypatch.setattr(portable.socket, "socket", lambda *a, **k: pytest.fail("must not open service"))
    with pytest.raises(RuntimeError, match="升级尚未完成"):
        portable.main()
    assert marker.exists()
    assert not (tmp_path / "data/portable-runtime.json").exists()
