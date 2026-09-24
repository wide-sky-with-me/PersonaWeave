"""Source repair packages use the existing guarded updater without replacing runtimes or user data."""

import json
import zipfile

import pytest

from scripts.build_windows_fix import build
from scripts.build_windows_portable import sha256
from scripts.check_windows_portable import inspect
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
