"""Build a differential GPU upgrade for one exact CPU delivery; no duplicate models or user settings."""

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_windows_portable import check_public_asset, cleanup_staging, publish_artifacts, sha256  # noqa: E402
from scripts.upgrade_windows_gpu import owned_path  # noqa: E402


def build(cpu, gpu, output):
    """Use verified delivery manifests to ship only changed bytes and a guarded local installer."""
    if any(output.resolve().is_relative_to(directory.resolve()) for directory in (cpu, gpu)):
        raise ValueError("Upgrade archive must be outside the source distributions")
    old_bytes = (cpu / "manifest.json").read_bytes()
    new_bytes = (gpu / "manifest.json").read_bytes()
    old, new = json.loads(old_bytes), json.loads(new_bytes)
    if old["edition"] != "windows-x64-cpu" or new["edition"] != "windows-x64-gpu":
        raise ValueError("Expected a CPU base and GPU target")
    changed = {name: value for name, value in new["files"].items() if old["files"].get(name) != value}
    for name, expected in changed.items():
        check_public_asset(name)
        if sha256(owned_path(gpu, name)) != expected:
            raise ValueError(f"GPU build checksum mismatch: {name}")
    update = {
        "base_manifest_sha256": hashlib.sha256(old_bytes).hexdigest(),
        "new_manifest_sha256": hashlib.sha256(new_bytes).hexdigest(),
        "base_files": old["files"],
    }
    contents = {
        "gpu-update/update.json": json.dumps(update).encode(),
        "gpu-update/manifest.json": new_bytes,
        "gpu-update/apply.py": (ROOT / "scripts/upgrade_windows_gpu.py").read_bytes(),
        "Upgrade-GPU.cmd": (
            "@echo off\r\nchcp 65001 >nul\r\n"
            'if not exist "%~dp0runtime\\python.exe" (\r\n'
            "echo 请将升级包解压到已有 CPU 程序目录，与 Start.cmd 放在一起。\r\npause\r\nexit /b 1\r\n)\r\n"
            '"%~dp0runtime\\python.exe" -X utf8 "%~dp0gpu-update\\apply.py"\r\npause\r\n'
        ).encode(),
    }
    expected = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    expected.update({"gpu-update/files/" + name: value for name, value in changed.items()})
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-build-", dir=output.parent))
    temporary = staging / output.name
    backups = staging / f".{output.name}-previous"
    published = False
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for name, value in contents.items():
                package.writestr(name, value)
            for name in sorted(changed):
                package.write(owned_path(gpu, name), "gpu-update/files/" + name)
        with zipfile.ZipFile(temporary) as package:
            if len(package.namelist()) != len(expected) or set(package.namelist()) != set(expected):
                raise ValueError("Upgrade archive file set differs from manifest")
            for name, value in expected.items():
                with package.open(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != value:
                        raise ValueError(f"Upgrade archive checksum mismatch: {name}")
        checksum = temporary.with_suffix(".zip.sha256")
        checksum.write_text(sha256(temporary) + "  " + output.name + "\n", encoding="utf-8")
        publish_artifacts([(temporary, output), (checksum, output.with_suffix(".zip.sha256"))], backups)
        published = True
    finally:
        cleanup_staging(staging, backups, published)
    print(f"GPU upgrade: {output} ({output.stat().st_size / 1024**2:.1f} MiB), {len(changed)} changed files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-x64")
    parser.add_argument("--gpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-GPU-x64")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/PersonaWeave-Windows-GPU-Upgrade.zip")
    args = parser.parse_args()
    build(args.cpu, args.gpu, args.output)
