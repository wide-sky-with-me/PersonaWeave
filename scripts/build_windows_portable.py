"""Build isolated Windows CPU/CUDA editions; run with uv, never copy local user data."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import UTC, datetime
from importlib.metadata import distributions
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
TOKENIZER_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
SAMPLES = {
    "source-01.png": "test_target/01.png",
    "source-02.png": "test_target/02.png",
    "group-01.png": "test_images/group-01.png",
    "group-02.png": "test_images/group-02.png",
}


def check_public_asset(relative):
    """Reject runtime state even when rebuilding a previously launched distribution."""
    # Windows aliases differ in spelling but address the same private files.
    relative = relative.replace("\\", "/").casefold()
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


def prepare_frontend(output):
    """Build current sources and replace assets so retired chunks cannot survive a rebuild."""
    subprocess.run(["pnpm", "--dir", "web", "build"], cwd=ROOT, check=True)
    target = output / "web/dist"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ROOT / "web/dist", target)


def normalize_tokenizer_cache(runtime):
    """Ship cl100k_base for offline startup, accepting only its canonical tiktoken checksum."""
    path = runtime / "Lib/site-packages/litellm/litellm_core_utils/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    if path.exists():
        contents = path.read_bytes()
    else:
        cache = ROOT / "build/windows/cl100k_base.tiktoken"
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(
                "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken", cache
            )
        contents = cache.read_bytes()
    if hashlib.sha256(contents).hexdigest() == TOKENIZER_SHA256:
        path.write_bytes(contents)
        return
    normalized = contents.replace(b"\r\n", b"\n")
    if hashlib.sha256(normalized).hexdigest() != TOKENIZER_SHA256:
        raise ValueError("Bundled tokenizer cache checksum mismatch")
    path.write_bytes(normalized)


def write_archive(output, archive_path):
    """Publish a ZIP only after every member matches the frozen manifest, including private overlays."""
    if archive_path.resolve().is_relative_to(output.resolve()):
        raise ValueError("Archive must be outside the distribution directory")
    if output.is_symlink() or any(path.is_symlink() for path in output.rglob("*")):
        raise ValueError("Distribution must not contain symlinks")
    manifest_path = output / "manifest.json"
    expected = dict(json.loads(manifest_path.read_text(encoding="utf-8"))["files"])
    expected["manifest.json"] = sha256(manifest_path)
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    if actual != set(expected):
        raise ValueError("Distribution file set differs from manifest")
    staging = Path(tempfile.mkdtemp(prefix=f".{archive_path.name}-build-", dir=archive_path.parent))
    temporary = staging / archive_path.name
    backups = staging / f".{archive_path.name}-previous"
    published = False
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for relative in sorted(expected):
                package.write(output / relative, output.name + "/" + relative)
        with zipfile.ZipFile(temporary) as package:
            for relative, digest in expected.items():
                with package.open(output.name + "/" + relative) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError(f"Archive checksum mismatch: {relative}")
        checksum = temporary.with_suffix(".zip.sha256")
        checksum.write_text(sha256(temporary) + "  " + archive_path.name + "\n", encoding="utf-8")
        publish_artifacts([(temporary, archive_path), (checksum, archive_path.with_suffix(".zip.sha256"))], backups)
        published = True
    finally:
        cleanup_staging(staging, backups, published)
    print(f"Built and verified: {archive_path} ({archive_path.stat().st_size / 1024**2:.1f} MiB)", flush=True)


def validate_runtime(runtime, edition):
    """Reject incomplete Python/ONNX installations before recording a reusable build."""
    site_packages = runtime / "Lib/site-packages"
    required = ["python.exe", "python313.dll", "python313.zip", "python313._pth"]
    required += [f"Lib/site-packages/{name}/__init__.py" for name in ("onnxruntime", "uvicorn", "litellm")]
    if any(not (runtime / name).is_file() for name in required) or not list(
        (site_packages / "onnxruntime/capi").glob("onnxruntime_pybind11_state*.pyd")
    ):
        raise ValueError("Incomplete portable runtime; rebuild without --skip-deps")
    expected_name, expected_version = (GPU_RUNTIME if edition == "gpu" else CPU_RUNTIME).split("==")
    installed = [
        (dist.metadata.get("Name", "").lower().replace("_", "-"), dist.version)
        for dist in distributions(path=[str(site_packages)])
        if dist.metadata.get("Name", "").lower().replace("_", "-") in {"onnxruntime", "onnxruntime-gpu"}
    ]
    if installed != [(expected_name, expected_version)]:
        raise ValueError("Portable ONNX distribution/version mismatch; rebuild without --skip-deps")


def validate_reusable_runtime(output, manifest, fingerprint, requirements, edition):
    """Reuse only an intact runtime from a successful build with identical dependency inputs."""
    if not manifest or manifest.get("dependency_fingerprint") != fingerprint:
        raise ValueError("Missing or changed dependency fingerprint; rebuild without --skip-deps")
    requirements_path = output / "requirements-windows.txt"
    if not requirements_path.is_file() or requirements_path.read_text(encoding="utf-8") != requirements:
        raise ValueError("Portable requirements changed; rebuild without --skip-deps")
    expected = {name: digest for name, digest in manifest["files"].items() if name.startswith("runtime/")}
    actual = {path.relative_to(output).as_posix() for path in (output / "runtime").rglob("*") if path.is_file()}
    if not expected or actual != set(expected):
        raise ValueError("Portable runtime file set changed; rebuild without --skip-deps")
    for name, digest in expected.items():
        path = (output / name).resolve()
        if not path.is_relative_to(output / "runtime") or sha256(path) != digest:
            raise ValueError(f"Portable runtime checksum mismatch: {name}; rebuild without --skip-deps")
    validate_runtime(output / "runtime", edition)


def publish_build(staged, output, archive):
    """Replace completed artifacts together, restoring previous bytes if any rename fails."""
    artifacts = [(staged, output)]
    if archive:
        artifacts += [(staged.with_suffix(suffix), output.with_suffix(suffix)) for suffix in (".zip", ".zip.sha256")]
    backups = staged.parent / f".{staged.name}-previous"
    publish_artifacts(artifacts, backups)


def publish_artifacts(artifacts, backups):
    """Publish prepared files/directories with rollback, never following destination symlinks."""
    if any(target.is_symlink() for _, target in artifacts):
        raise ValueError("Publication destinations must not be symlinks")
    backups.mkdir()
    replaced = []
    try:
        for source, target in artifacts:
            backup = backups / target.name
            if target.exists():
                target.replace(backup)
            replaced.append((source, target, backup))
            source.replace(target)
    except BaseException:
        # Move new artifacts back into staging before restoring old ones. If restoration
        # itself fails, build() retains the backups for manual recovery instead of deleting them.
        for source, target, backup in reversed(replaced):
            if target.exists():
                target.replace(source)
            if backup.exists():
                backup.replace(target)
        raise


def cleanup_staging(staging, backups, published):
    """Retain old artifacts when rollback failed, instead of deleting the only recoverable copy."""
    if not published and backups.exists() and any(backups.iterdir()):
        print(f"Publication interrupted; previous artifacts retained for recovery: {backups}", file=sys.stderr)
    else:
        shutil.rmtree(staging)


def build(output, msvc_dir, skip_deps=False, archive=True, edition="cpu"):
    """Build in a sibling staging directory; failed builds preserve the previous release."""
    missing_msvc = [name for name in MSVC_FILES if not (msvc_dir / name).is_file()]
    if missing_msvc:
        raise ValueError("Missing Microsoft VC runtime files: " + ", ".join(missing_msvc))
    if output.is_symlink():
        raise ValueError("Portable output must not be a symlink")
    output = output.resolve()
    if output == ROOT / "dist" or not output.is_relative_to(ROOT / "dist"):
        raise ValueError("Portable output must be a dedicated directory below dist/")
    if output == output.with_suffix(".zip"):
        raise ValueError("Portable output directory must not collide with its ZIP path")
    previous = output / "manifest.json"
    manifest = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else None
    if manifest and manifest["edition"] != f"windows-x64-{edition}":
        raise ValueError("Use a separate output directory for each edition to avoid mixing ONNX runtimes")
    # Fail before changing a previously used/private installation.
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ValueError("Distribution must not contain symlinks")
        if path.is_file():
            check_public_asset(path.relative_to(output).as_posix())
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
    requirements_text = portable_requirements(exported, edition)
    override_text = "opencv-python ; sys_platform == 'never'\n" + (
        "onnxruntime ; sys_platform == 'never'\n" if edition == "gpu" else ""
    )
    # Bump the recipe version when installer policy changes. Old manifests deliberately
    # require a fresh install: a hash of whatever happens to remain is not proof of success.
    fingerprint = hashlib.sha256(
        json.dumps(
            [1, PYTHON_VERSION, PYTHON_SHA256, "x86_64-pc-windows-msvc", edition, requirements_text, override_text]
        ).encode()
    ).hexdigest()
    if skip_deps:
        validate_reusable_runtime(output, manifest, fingerprint, requirements_text, edition)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{output.name}-build-", dir=output.parent))
    staged = staging_root / output.name
    published = False
    try:
        _assemble(
            staged,
            msvc_dir,
            requirements_text,
            override_text,
            fingerprint,
            output / "runtime" if skip_deps else None,
            archive,
            edition,
            manifest,
        )
        publish_build(staged, output, archive)
        published = True
        print(f"Published portable build: {output}", flush=True)
        if not archive and output.with_suffix(".zip").exists():
            print("Archive disabled: the existing ZIP and checksum were retained unchanged.", flush=True)
    finally:
        backups = staging_root / f".{staged.name}-previous"
        cleanup_staging(staging_root, backups, published)


def _assemble(
    output, msvc_dir, requirements_text, override_text, fingerprint, reuse_runtime, archive, edition, manifest
):
    """Assemble a fresh staging tree; only an already verified runtime may be copied in."""
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    requirements = output / "requirements-windows.txt"
    requirements.write_text(requirements_text, encoding="utf-8")
    if reuse_runtime is not None:
        shutil.copytree(reuse_runtime, runtime, dirs_exist_ok=True)
        # Verify the copied snapshot as well: a changed input during copying must not
        # be certified as a fresh successful runtime by the new manifest below.
        validate_reusable_runtime(output, manifest, fingerprint, requirements_text, edition)
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
    override = output.parent / f".{output.name}-overrides.txt"
    override.write_text(override_text, encoding="utf-8")
    if reuse_runtime is None:
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
    normalize_tokenizer_cache(runtime)
    validate_runtime(runtime, edition)
    for name in ("api", "core", "utils"):
        target = output / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("config", "web/dist"):
        (output / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config/ai_gateway.yaml", output / "config/ai_gateway.yaml")
    prepare_frontend(output)
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
    (output / "support").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "scripts/check_windows_portable.py", output / "support/check.py")
    shutil.copy2(ROOT / "scripts/collect_windows_performance.py", output / "support/performance.py")
    (output / "Check-Performance.cmd").write_bytes(
        b'@echo off\r\nchcp 65001 >nul\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0support\\performance.py" --root "%~dp0."\r\npause\r\n'
    )
    (output / "Check-Install.cmd").write_bytes(
        b'@echo off\r\nchcp 65001 >nul\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0support\\check.py"\r\npause\r\n'
    )
    removed_bytes = trim_development_files(runtime)
    manifest = {
        "edition": f"windows-x64-{edition}",
        "built_at": datetime.now(UTC).isoformat(),
        "python": PYTHON_VERSION,
        "python_url": PYTHON_URL,
        "python_sha256": PYTHON_SHA256,
        "inference_runtime": GPU_RUNTIME if edition == "gpu" else CPU_RUNTIME,
        "dependency_fingerprint": fingerprint,
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
        write_archive(output, output.with_suffix(".zip"))
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
