"""Build isolated Windows CPU/CUDA editions; run with uv, never copy local user data."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.download_models import MODEL_SHA256  # noqa: E402

# Official python.org release checksum; kept separate from the Linux development interpreter.
PYTHON_VERSION = "3.13.15"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
CPU_RUNTIME = "onnxruntime==1.23.2"
GPU_RUNTIME = "onnxruntime-gpu==1.23.2"
MSVC_FILES = ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")
SAMPLES = {
    "source-01.png": "test_target/01.png",
    "source-02.png": "test_target/02.png",
    "group-01.png": "test_images/group-01.png",
    "group-02.png": "test_images/group-02.png",
}


def check_public_asset(relative):
    """Reject runtime state even when rebuilding a previously launched distribution."""
    path = Path(relative)
    if (
        path.name.startswith(".env")
        or path.suffix in {".sqlite", ".sqlite3", ".db"}
        or relative.startswith(("outputs/", "logs/"))
        or (relative.startswith("data/") and relative[5:] not in SAMPLES.values())
    ):
        raise ValueError(f"Remove runtime/private data before packaging: {relative}")


def sha256(path):
    """Hash a distribution asset without loading large model files into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def portable_requirements(exported, edition="cpu"):
    """Keep lock pins, selecting exactly one Windows runtime and one OpenCV distribution."""
    if edition not in {"cpu", "gpu"}:
        raise ValueError("Unknown portable edition")
    lines = [
        line
        for line in exported.splitlines()
        if line
        and not line.lstrip().startswith("#")
        and not line.startswith(("onnxruntime==", "onnxruntime-gpu==", "opencv-python=="))
        and (edition == "gpu" or not line.startswith("nvidia-"))
    ]
    return "\n".join([*lines, GPU_RUNTIME if edition == "gpu" else CPU_RUNTIME, ""])


def trim_development_files(runtime):
    """Remove regenerable bytecode and NVIDIA SDK headers/static libraries, retaining DLLs/licenses."""
    removed = 0
    for path in runtime.rglob("*"):
        if path.is_file() and (
            path.suffix == ".pyc"
            or ("nvidia" in path.parts and path.suffix.lower() in {".h", ".hpp", ".lib", ".exp", ".pdb"})
        ):
            removed += path.stat().st_size
            path.unlink()
    return removed


def build(output, msvc_dir, skip_deps=False, archive=True, edition="cpu"):
    """Assemble only shipped source, verified models and isolated Windows dependencies."""
    missing_msvc = [name for name in MSVC_FILES if not (msvc_dir / name).is_file()]
    if missing_msvc:
        raise ValueError("Missing Microsoft VC runtime files: " + ", ".join(missing_msvc))
    output = output.resolve()
    if output == ROOT / "dist" or not output.is_relative_to(ROOT / "dist"):
        raise ValueError("Portable output must be a dedicated directory below dist/")
    output.mkdir(parents=True, exist_ok=True)
    previous = output / "manifest.json"
    if previous.exists() and json.loads(previous.read_text())["edition"] != f"windows-x64-{edition}":
        raise ValueError("Use a separate output directory for each edition to avoid mixing ONNX runtimes")
    runtime = output / "runtime"
    runtime.mkdir(exist_ok=True)
    # Host System32 files can be read-only; the private copy must remain rebuildable.
    for path in runtime.glob("*.dll"):
        path.chmod(0o644)
    cache = ROOT / "build/windows"
    cache.mkdir(parents=True, exist_ok=True)
    download = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not download.exists():
        urllib.request.urlretrieve(PYTHON_URL, download)
    if sha256(download) != PYTHON_SHA256:
        raise ValueError("Python runtime checksum mismatch")
    with zipfile.ZipFile(download) as package:
        for member in package.infolist():
            if not (runtime / member.filename).resolve().is_relative_to(runtime):
                raise ValueError("Invalid runtime archive member")
        package.extractall(runtime)
    (runtime / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n..\nimport site\n", encoding="utf-8")
    exported = subprocess.check_output(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        text=True,
    )
    requirements = output / "requirements-windows.txt"
    requirements.write_text(portable_requirements(exported, edition), encoding="utf-8")
    override = cache / f"overrides-{edition}.txt"
    override.write_text(
        "opencv-python ; sys_platform == 'never'\n"
        + ("onnxruntime ; sys_platform == 'never'\n" if edition == "gpu" else ""),
        encoding="utf-8",
    )
    if not skip_deps:
        subprocess.run(
            [
                "uv",
                "--no-config",
                "pip",
                "install",
                "--target",
                str(runtime / "Lib/site-packages"),
                "--python-version",
                "3.13",
                "--python-platform",
                "x86_64-pc-windows-msvc",
                "--only-binary",
                ":all:",
                "--overrides",
                str(override),
                "-r",
                str(requirements),
            ],
            check=True,
            cwd=ROOT,
        )
    # App-local Microsoft VC runtime; supplied from a verified Microsoft installation by the builder.
    for name in MSVC_FILES:
        shutil.copyfile(msvc_dir / name, runtime / name)
    for name in ("api", "core", "utils"):
        target = output / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("config", "web/dist"):
        (output / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config/ai_gateway.yaml", output / "config/ai_gateway.yaml")
    shutil.copytree(ROOT / "web/dist", output / "web/dist", dirs_exist_ok=True)
    model_files = {name: digest for name, digest in MODEL_SHA256.items() if name.endswith(".onnx")}
    for name, digest in model_files.items():
        source = ROOT / "models" / name
        if sha256(source) != digest:
            raise ValueError(f"Model checksum mismatch: {name}")
        target = output / "models" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / "models/README.md", output / "models/README.md")
    shutil.copy2(ROOT / "docs/windows-portable.md", output / "README.md")
    (output / "docs").mkdir(exist_ok=True)
    for name in ("windows-portable.md", "windows-portable-validation.md"):
        shutil.copy2(ROOT / "docs" / name, output / "docs" / name)
    for source, target in SAMPLES.items():
        destination = output / "data" / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "tests/fixtures" / source, destination)
    for name, argument in (("Start.cmd", ""), ("Configure-AI.cmd", " --configure")):
        (output / name).write_bytes(
            (
                "@echo off\r\nchcp 65001 >nul\r\n"
                f'"%~dp0runtime\\python.exe" -X utf8 -m api.portable{argument}\r\n'
                "if errorlevel 1 pause\r\n"
            ).encode()
        )
    removed_bytes = trim_development_files(runtime)
    manifest = {
        "edition": f"windows-x64-{edition}",
        "python": PYTHON_VERSION,
        "python_url": PYTHON_URL,
        "python_sha256": PYTHON_SHA256,
        "inference_runtime": GPU_RUNTIME if edition == "gpu" else CPU_RUNTIME,
        "models": model_files,
        "files": {},
    }
    # Runtime test data and credentials are never eligible for a distributable archive.
    for path in output.rglob("*"):
        if path.is_file() and path.name != "manifest.json":
            relative = path.relative_to(output).as_posix()
            check_public_asset(relative)
            manifest["files"][relative] = sha256(path)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if archive:
        archive_path = output.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for path in output.rglob("*"):
                if path.is_file():
                    package.write(path, output.name + "/" + path.relative_to(output).as_posix())
        archive_path.with_suffix(".zip.sha256").write_text(sha256(archive_path) + "  " + archive_path.name + "\n")
        print(f"Built: {archive_path} ({archive_path.stat().st_size / 1024**2:.1f} MiB)")
    print(f"Removed regenerable/SDK files: {removed_bytes / 1024**2:.1f} MiB", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--edition", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--msvc-dir", type=Path, required=True)
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()
    output = args.output or ROOT / "dist" / (
        "PersonaWeave-Windows-GPU-x64" if args.edition == "gpu" else "PersonaWeave-Windows-x64"
    )
    build(output, args.msvc_dir, args.skip_deps, not args.no_archive, args.edition)
