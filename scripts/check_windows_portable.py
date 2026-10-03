"""Local-only portable diagnostics; never report credentials, picture contents or absolute library paths."""

import gc
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PureWindowsPath


def inspect(root):
    """Check shipped bytes and isolated image persistence without inference or AI requests."""
    root = Path(root).resolve()
    report = {"missing_files": [], "changed_files": [], "unreadable_files": [], "ai_requests": 0}
    # This file was absent from the old manifest, so manifest verification alone misses it.
    for key, name in (
        ("onnx_vc_extension_present", "runtime/msvcp140_1.dll"),
        ("unicode_io_patch", "utils/image_io.py"),
    ):
        try:
            report[key] = (root / name).is_file()
        except OSError as exc:
            report[key + "_error"] = type(exc).__name__
    files = {}
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            raise ValueError("Invalid manifest structure")
        report["edition"] = manifest.get("edition")
        files = manifest["files"]
    except (OSError, ValueError) as exc:
        report["manifest_error"] = type(exc).__name__
    for name, expected in files.items():
        # A damaged manifest must not make diagnostics read outside the installation,
        # nor echo arbitrary absolute paths into the shareable report.
        if not name or "\\" in name or "\x00" in name or PureWindowsPath(name).drive or ".." in Path(name).parts:
            report["invalid_manifest_entries"] = report.get("invalid_manifest_entries", 0) + 1
            continue
        try:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                report["invalid_manifest_entries"] = report.get("invalid_manifest_entries", 0) + 1
                continue
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    report["changed_files"].append(name)
        except FileNotFoundError:
            report["missing_files"].append(name)
        except (OSError, RuntimeError) as exc:
            report["unreadable_files"].append({"file": name, "error_type": type(exc).__name__})
    try:
        recovery = (root / "api/projects/recovery.py").resolve()
        if not recovery.is_relative_to(root):
            raise ValueError("Invalid probe path")
        report["portrait_preview_patch"] = "_PORTRAIT_IMAGE" in recovery.read_text(encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        report["portrait_preview_probe_error"] = type(exc).__name__
    # Optional path overrides may work on the original machine only. Report flags, never their values.
    env = {}
    try:
        from dotenv import dotenv_values

        env = dotenv_values(root / ".env") if (root / ".env").is_file() else {}
    except Exception as exc:
        # Optional dependencies can themselves be missing in the installation being checked.
        report["environment_probe_error"] = type(exc).__name__
    for name in ("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", "PERSONAWEAVE_AI_CONFIG"):
        value = os.environ.get(name) or env.get(name)
        report[name] = {"configured": bool(value), "absolute": bool(value and Path(value).is_absolute())}
    try:
        from api.portraits.library import get_library_root, resolve_asset_path
        from api.projects.recovery import import_assets
        from api.projects.store import ProjectStore
        from api.schemas import ProjectAssetRequest

        library = get_library_root()
        db = library / "library.sqlite3"
        report["library_present"] = db.is_file()
        previews = []
        if db.is_file():
            with closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)) as con:
                ids = [row[0] for row in con.execute("SELECT id FROM assets")]
            report["library_assets"] = len(ids)
            report["missing_originals"] = sum(resolve_asset_path(id) is None for id in ids)
            report["missing_previews"] = sum(resolve_asset_path(id, thumbnail=True) is None for id in ids)
            previews = ids[:1]
        import base64
        from io import BytesIO

        from PIL import Image

        image = BytesIO()
        Image.new("RGB", (16, 16), "blue").save(image, "PNG")
        tests = [("inline_image", "data:image/png;base64," + base64.b64encode(image.getvalue()).decode())]
        tests += [("library_preview", f"/api/v1/portrait-assets/{id}/thumbnail") for id in previews]
        # Exercise the installed implementation, but keep diagnostic records out of the real project database.
        with tempfile.TemporaryDirectory(prefix="personaweave-check-") as directory:
            store = ProjectStore(Path(directory) / "check.sqlite3", Path(directory) / "图片")
            project = store.create()
            try:
                for label, source in tests:
                    try:
                        asset = import_assets(store, project["id"], ProjectAssetRequest(image=source), None)[0]
                        report[label] = {"ok": bool(store.asset(project["id"], asset["$asset"]))}
                    except Exception as exc:
                        report[label] = {"ok": False, "error_type": type(exc).__name__}
            finally:
                gc.collect()  # Release legacy SQLite connection cycles before Windows removes the temporary tree.
    except Exception as exc:
        # Keep the manifest and missing-VC findings even when a native import cannot start.
        report["project_probe_error"] = type(exc).__name__
    return report


def main(root=None):
    """Write a shareable report, retaining basic diagnostics when optional imports fail."""
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parent.parent
    try:
        os.chdir(root)
    except OSError as exc:
        print(f"无法进入程序目录（{type(exc).__name__}），请确认已完整解压到本地可访问目录。")
        return 1
    print("正在校验程序文件和本地工程存储，不调用 AI；完整检查可能需要几分钟。", flush=True)
    environment_error = None
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except Exception as exc:
        environment_error = type(exc).__name__
    result = inspect(root)
    if environment_error:
        result["environment_load_error"] = environment_error
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    try:
        (root / "install-check.json").write_text(serialized, encoding="utf-8")
    except OSError as exc:
        print(f"无法保存 install-check.json（{type(exc).__name__}），请将程序移到可写目录后重试，或提供下方检查结果：")
        print(serialized)
        return 1
    print("检查完成，请提供程序目录里的 install-check.json。文件不包含 Key 或图片内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
