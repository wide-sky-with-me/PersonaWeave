"""Source repair packages use the existing guarded updater without replacing runtimes or user data."""

import builtins
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path, PureWindowsPath

import pytest

from scripts.build_windows_fix import build
from scripts.build_windows_portable import sha256
from scripts.check_windows_portable import inspect, main
from scripts.upgrade_windows_gpu import apply_update


@pytest.mark.parametrize("edition", ["cpu", "gpu"])
def test_source_repair_preserves_runtime_settings_and_can_repeat(tmp_path, edition):
    base, source = tmp_path / "installed", tmp_path / "source"
    for root, value in [(base, "old"), (source, "new")]:
        (root / "api").mkdir(parents=True)
        (root / "api/media.py").write_text(value)
    (source / "utils").mkdir()
    (source / "utils/image_io.py").write_text("new helper")
    (source / "docs").mkdir()
    (source / "docs/windows-portable.md").write_text("instructions")
    (base / "runtime").mkdir()
    (base / "runtime/unchanged.dll").write_bytes(b"runtime")
    (base / ".env").write_text("private-setting")
    (base / "data").mkdir()
    (base / "data/projects.sqlite3").write_bytes(b"private-project")
    old = {
        "edition": f"windows-x64-{edition}",
        "files": {name: sha256(base / name) for name in ["api/media.py", "runtime/unchanged.dll"]},
    }
    (base / "manifest.json").write_text(json.dumps(old))
    archive = tmp_path / "repair.zip"
    build([base], archive, source=source)
    with zipfile.ZipFile(archive) as package:
        assert not any(".env" in n or "projects.sqlite3" in n or n.endswith(".dll") for n in package.namelist())
        assert "Check-Install.cmd" in package.namelist()
        package.extractall(base)
    payload = base / f"windows-fix/windows-x64-{edition}"
    apply_update(base, payload, label="程序修复")
    assert (base / "api/media.py").read_text() == "new"
    assert (base / "utils/image_io.py").read_text() == "new helper"
    assert (base / "runtime/unchanged.dll").read_bytes() == b"runtime"
    assert (base / ".env").read_text() == "private-setting"
    assert (base / "data/projects.sqlite3").read_bytes() == b"private-project"
    apply_update(base, payload, label="程序修复")


def test_repair_builder_rejects_a_modified_base_before_creating_archive(tmp_path):
    base = tmp_path / "installed"
    source = tmp_path / "source"
    for root in [base, source]:
        (root / "api").mkdir(parents=True)
        (root / "api/file.py").write_text("current")
    (base / "manifest.json").write_text(json.dumps({"edition": "windows-x64-gpu", "files": {"api/file.py": "bad"}}))
    with pytest.raises(ValueError, match="Base file differs"):
        build([base], tmp_path / "repair.zip", source=source)
    assert not (tmp_path / "repair.zip").exists()


def test_diagnostics_reports_missing_files_without_exposing_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", raising=False)
    (tmp_path / "api/projects").mkdir(parents=True)
    (tmp_path / "api/projects/recovery.py").write_text("_PORTRAIT_IMAGE")
    (tmp_path / ".env").write_text('AI_IMAGE_API_KEY="private-test-value"\n')
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"edition": "windows-x64-gpu", "files": {"missing.dll": "missing", "api/projects/recovery.py": "wrong"}}
        )
    )
    result = inspect(tmp_path)
    assert result["missing_files"] == ["missing.dll"]
    assert result["changed_files"] == ["api/projects/recovery.py"]
    assert result["inline_image"] == {"ok": True}
    assert not result["library_present"]
    assert result["portrait_preview_patch"]
    assert "private-test-value" not in json.dumps(result)
    assert not (tmp_path / "data/projects.sqlite3").exists()


@pytest.fixture
def diagnostic_installation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", str(tmp_path / "missing-library"))
    (tmp_path / "api/projects").mkdir(parents=True)
    (tmp_path / "api/projects/recovery.py").write_text("_PORTRAIT_IMAGE")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/msvcp140_1.dll").write_bytes(b"fixture")
    (tmp_path / "changed.dll").write_bytes(b"changed")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "edition": "windows-x64-cpu",
                "files": {
                    "api/projects/recovery.py": sha256(tmp_path / "api/projects/recovery.py"),
                    "missing.dll": "missing",
                    "changed.dll": "old",
                },
            }
        )
    )
    return tmp_path


def test_diagnostics_writes_complete_report_for_intact_installation(diagnostic_installation):
    root = diagnostic_installation
    files = ["api/projects/recovery.py", "runtime/msvcp140_1.dll", "changed.dll"]
    (root / "manifest.json").write_text(
        json.dumps({"edition": "windows-x64-cpu", "files": {name: sha256(root / name) for name in files}})
    )
    assert main(root) == 0
    result = json.loads((root / "install-check.json").read_text())
    assert result["missing_files"] == result["changed_files"] == result["unreadable_files"] == []
    assert result["onnx_vc_extension_present"]
    assert result["portrait_preview_patch"]
    assert result["inline_image"] == {"ok": True}
    assert not any(key.endswith("error") for key in result)


@pytest.mark.parametrize("failure", ["missing", "unreadable", "invalid_utf8"])
def test_diagnostics_retains_findings_when_patch_probe_fails(diagnostic_installation, monkeypatch, failure):
    root = diagnostic_installation
    recovery = root / "api/projects/recovery.py"
    if failure == "missing":
        recovery.unlink()
    elif failure == "invalid_utf8":
        recovery.write_bytes(b"\xff")
    else:
        original = Path.open

        def open_checked(path, *args, **kwargs):
            if path == recovery:
                raise PermissionError("private-path-or-key-must-not-appear")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_checked)
    result = inspect(root)
    assert "missing.dll" in result["missing_files"]
    assert "changed.dll" in result["changed_files"]
    assert result["onnx_vc_extension_present"]
    assert result["inline_image"] == {"ok": True}
    assert (
        result["portrait_preview_probe_error"]
        == {
            "missing": "FileNotFoundError",
            "unreadable": "PermissionError",
            "invalid_utf8": "UnicodeDecodeError",
        }[failure]
    )
    if failure == "missing":
        assert "api/projects/recovery.py" in result["missing_files"]
    if failure == "unreadable":
        assert result["unreadable_files"] == [{"file": "api/projects/recovery.py", "error_type": "PermissionError"}]
    assert "private-path-or-key-must-not-appear" not in json.dumps(result)


def test_diagnostics_writes_basic_report_when_optional_imports_fail(diagnostic_installation, monkeypatch):
    original = builtins.__import__

    def import_checked(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("api."):
            raise ModuleNotFoundError("private-path-or-key-must-not-appear")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_checked)
    assert main(diagnostic_installation) == 0
    result = json.loads((diagnostic_installation / "install-check.json").read_text())
    assert result["missing_files"] == ["missing.dll"]
    assert result["changed_files"] == ["changed.dll"]
    assert result["onnx_vc_extension_present"]
    for key in ("environment_load_error", "environment_probe_error", "project_probe_error"):
        assert result[key] == "ModuleNotFoundError"
    assert "private-path-or-key-must-not-appear" not in json.dumps(result)


@pytest.mark.parametrize("manifest", ["not json", "[]", '{"files": []}'])
def test_diagnostics_retains_independent_probes_when_manifest_is_invalid(diagnostic_installation, manifest):
    (diagnostic_installation / "manifest.json").write_text(manifest)
    result = inspect(diagnostic_installation)
    assert result["manifest_error"] in {"JSONDecodeError", "ValueError"}
    assert result["onnx_vc_extension_present"]
    assert result["portrait_preview_patch"]
    assert result["inline_image"] == {"ok": True}


def test_diagnostics_rejects_manifest_escape_without_echoing_path(diagnostic_installation):
    root = diagnostic_installation
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"].update({"../private": "x", "/private": "x", "C:/private": "x"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    result = inspect(root)
    assert result["invalid_manifest_entries"] == 3
    assert result["missing_files"] == ["missing.dll"]
    assert "private" not in json.dumps(result)


def test_diagnostics_prints_findings_when_report_cannot_be_written(diagnostic_installation, monkeypatch, capsys):
    original = Path.write_text

    def write_checked(path, *args, **kwargs):
        if path.name == "install-check.json":
            raise PermissionError("private-path-or-key-must-not-appear")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_checked)
    assert main(diagnostic_installation) == 1
    output = capsys.readouterr().out
    assert "无法保存 install-check.json" in output
    assert '"missing.dll"' in output
    assert '"changed.dll"' in output
    assert "private-path-or-key-must-not-appear" not in output


def test_diagnostics_explains_unavailable_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(tmp_path / "not-extracted") == 1
    assert "无法进入程序目录" in capsys.readouterr().out


@pytest.fixture
def repair_build(tmp_path):
    base, source = tmp_path / "installed", tmp_path / "source"
    for root, value in ((base, "old"), (source, "new")):
        (root / "api").mkdir(parents=True)
        (root / "api/file.py").write_text(value)
    (source / "docs").mkdir()
    (source / "docs/windows-portable.md").write_text("instructions")
    (base / "manifest.json").write_text(
        json.dumps(
            {
                "edition": "windows-x64-cpu",
                "files": {"api/file.py": sha256(base / "api/file.py")},
            }
        )
    )
    return base, source, tmp_path / "repair.zip"


def test_repair_paths_are_posix_even_with_windows_relative_paths(repair_build, monkeypatch):
    base, source, output = repair_build
    original = Path.relative_to

    def windows_relative(path, other, *args, **kwargs):
        relative = original(path, other, *args, **kwargs)
        return PureWindowsPath(relative.as_posix()) if other == source else relative

    monkeypatch.setattr(Path, "relative_to", windows_relative)
    build([base], output, source=source)
    with zipfile.ZipFile(output) as archive:
        archive.extractall(base)
        manifest = json.loads(archive.read("windows-fix/windows-x64-cpu/manifest.json"))
        assert "api/file.py" in manifest["files"]
        assert not any("\\" in name for name in archive.namelist())
    apply_update(base, base / "windows-fix/windows-x64-cpu")
    assert (base / "api/file.py").read_text() == "new"


@pytest.mark.parametrize("failure", ["zip_write", "source_changed", "checksum_write", "checksum_publish"])
def test_repair_archive_failure_preserves_zip_and_checksum(repair_build, monkeypatch, failure):
    base, source, output = repair_build
    build([base], output, source=source)
    checksum = output.with_suffix(".zip.sha256")
    previous = output.read_bytes(), checksum.read_bytes()
    if failure in {"zip_write", "source_changed"}:
        original = zipfile.ZipFile.write

        def write(archive, path, *args, **kwargs):
            if failure == "zip_write":
                raise OSError("injected ZIP failure")
            if Path(path) == source / "api/file.py":
                Path(path).write_bytes(b"changed after manifest preparation")
            return original(archive, path, *args, **kwargs)

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
        build([base], output, source=source)
    assert (output.read_bytes(), checksum.read_bytes()) == previous
    assert not list(output.parent.glob(".*-build-*"))


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_diagnostics_and_preview_do_not_migrate_existing_library(tmp_path, monkeypatch, journal):
    from io import BytesIO

    from PIL import Image

    from api.portraits.library import SCHEMA

    monkeypatch.chdir(tmp_path)
    library = tmp_path / "library"
    (library / "originals").mkdir(parents=True)
    (library / "thumbnails").mkdir()
    image = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, "PNG")
    for name in ("originals/test.png", "thumbnails/test.png"):
        (library / name).write_bytes(image.getvalue())
    db = library / "library.sqlite3"
    asset_id = "as_" + "a" * 20
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(SCHEMA)
        conn.execute("DROP TABLE archived_identities")
        conn.execute("DROP TABLE import_audit")
        conn.execute(
            "INSERT INTO identities VALUES (?,?,?,?,?,?,?)",
            ("pid_test", "test", "female", 20, "young_adult", "Test", "2026-10-08"),
        )
        conn.execute(
            "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                "pid_test",
                "angle",
                "front",
                "test.png",
                "originals/test.png",
                "thumbnails/test.png",
                "fixture",
                16,
                16,
                "2026-10-08",
            ),
        )
        conn.commit()
        conn.execute(f"PRAGMA journal_mode={journal}")
    monkeypatch.setenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", str(library))
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {}}))
    before = {p.relative_to(library): p.read_bytes() for p in library.rglob("*") if p.is_file()}
    result = inspect(tmp_path)
    assert result["missing_originals"] == result["missing_previews"] == 0
    assert result["library_preview"] == {"ok": True}
    assert "project_probe_error" not in result
    after = {p.relative_to(library): p.read_bytes() for p in library.rglob("*") if p.is_file()}
    # SQLite's read-only WAL reader may create coordination files; neither data nor schema may change.
    auxiliaries = {Path("library.sqlite3-shm"), Path("library.sqlite3-wal")}
    assert {p: data for p, data in after.items() if p not in auxiliaries} == before
    assert after.keys() - before.keys() <= auxiliaries
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == journal.lower()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables.intersection({"archived_identities", "import_audit"})


def test_readonly_asset_lookup_reads_committed_wal_and_never_executes_writes(tmp_path):
    from api.portraits.library import resolve_asset_path

    (tmp_path / "originals").mkdir()
    asset = tmp_path / "originals/test.png"
    asset.write_bytes(b"fixture")
    db = tmp_path / "library.sqlite3"
    with closing(sqlite3.connect(db)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE assets (id TEXT, original_relpath TEXT, thumbnail_relpath TEXT)")
        writer.execute("INSERT INTO assets VALUES ('as_test', 'originals/test.png', NULL)")
        writer.commit()
        before = db.read_bytes(), db.with_name(db.name + "-wal").read_bytes()
        assert resolve_asset_path("as_test", root=tmp_path) == asset
        assert (db.read_bytes(), db.with_name(db.name + "-wal").read_bytes()) == before
        with (
            closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as reader,
            pytest.raises(sqlite3.OperationalError, match="readonly"),
        ):
            reader.execute("DELETE FROM assets")
