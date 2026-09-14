"""Build a differential GPU upgrade for one exact CPU delivery; no duplicate models or user settings."""

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_windows_portable import check_public_asset, sha256  # noqa: E402


def build(cpu, gpu, output):
    """Use verified delivery manifests to ship only changed bytes and a guarded local installer."""
    old = json.loads((cpu / "manifest.json").read_text())
    new = json.loads((gpu / "manifest.json").read_text())
    if old["edition"] != "windows-x64-cpu" or new["edition"] != "windows-x64-gpu":
        raise ValueError("Expected a CPU base and GPU target")
    changed = {name: value for name, value in new["files"].items() if old["files"].get(name) != value}
    for name, expected in changed.items():
        check_public_asset(name)
        if sha256(gpu / name) != expected:
            raise ValueError(f"GPU build checksum mismatch: {name}")
    update = {
        "base_manifest_sha256": sha256(cpu / "manifest.json"),
        "new_manifest_sha256": sha256(gpu / "manifest.json"),
        "base_files": old["files"],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        package.writestr("gpu-update/update.json", json.dumps(update))
        package.write(gpu / "manifest.json", "gpu-update/manifest.json")
        package.write(ROOT / "scripts/upgrade_windows_gpu.py", "gpu-update/apply.py")
        package.writestr(
            "Upgrade-GPU.cmd",
            (
                "@echo off\r\nchcp 65001 >nul\r\n"
                'if not exist "%~dp0runtime\\python.exe" (\r\n'
                "echo 请将升级包解压到已有 CPU 程序目录，与 Start.cmd 放在一起。\r\npause\r\nexit /b 1\r\n)\r\n"
                '"%~dp0runtime\\python.exe" -X utf8 "%~dp0gpu-update\\apply.py"\r\npause\r\n'
            ).encode(),
        )
        for name in sorted(changed):
            package.write(gpu / name, "gpu-update/files/" + name)
    output.with_suffix(".zip.sha256").write_text(sha256(output) + "  " + output.name + "\n")
    print(f"GPU upgrade: {output} ({output.stat().st_size / 1024**2:.1f} MiB), {len(changed)} changed files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-x64")
    parser.add_argument("--gpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-GPU-x64")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/PersonaWeave-Windows-GPU-Upgrade.zip")
    args = parser.parse_args()
    build(args.cpu, args.gpu, args.output)
