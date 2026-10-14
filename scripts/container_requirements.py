"""Export the locked CPU container dependencies without changing the development lock."""

import argparse
import re
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^;\s]+)(?:\s*;.*)?$")
# The GPU-only 1.20.2 release has no CPU distribution; this pin has Linux ARM64 wheels.
CPU_RUNTIME = "onnxruntime==1.23.2"


def cpu_requirements(exported: str) -> str:
    """Keep lock pins and markers, replace CUDA ORT, and retain only headless OpenCV."""
    lines = []
    runtime_versions = set()
    has_headless = False
    for row in exported.splitlines():
        line = row.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"Expected a pinned uv export requirement: {line}")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name == "onnxruntime-gpu":
            runtime_versions.add(match.group(2))
        if name.startswith("nvidia-") or name in {"onnxruntime", "onnxruntime-gpu", "opencv-python"}:
            continue
        has_headless |= name == "opencv-python-headless"
        lines.append(line)
    if len(runtime_versions) != 1:
        raise ValueError("Expected exactly one locked CUDA ONNX Runtime version")
    if not has_headless:
        raise ValueError("The lock must contain opencv-python-headless")
    return "\n".join([*lines, CPU_RUNTIME, ""])


def main() -> None:
    """Convert a no-hashes, no-project uv export into CPU installation requirements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exported", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(cpu_requirements(args.exported.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
