"""Non-production native dependency audit; launch with the shipped Python and a System32-only PATH."""

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path


def verify(root, output):
    """Run local inference and report where Python packages and loaded DLLs came from; no AI calls."""
    if os.name != "nt":
        raise RuntimeError("Use the bundled native Windows Python")
    root = root.resolve()
    import cv2
    import numpy as np
    import onnxruntime as ort

    from core.pipeline import PersonaWeavePipeline

    runtime = root / "runtime"
    report = {
        "isolated_python": bool(sys.flags.isolated),
        "python_from_package": Path(sys.executable).resolve().is_relative_to(runtime),
        "modules_from_package": {
            name: Path(module.__file__).resolve().is_relative_to(runtime)
            for name, module in [("cv2", cv2), ("numpy", np), ("onnxruntime", ort)]
        },
        "development_environment_removed": all(
            name not in os.environ for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CUDA_PATH")
        ),
        "ai_requests": 0,
    }
    assert report["isolated_python"] and report["python_from_package"]
    assert all(report["modules_from_package"].values()) and report["development_environment_removed"]
    pipeline = PersonaWeavePipeline(models_dir=str(root / "models"))

    def read(relative):
        return cv2.imdecode(np.frombuffer((root / relative).read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    source = read("data/test_target/01.png")
    target = read("data/test_images/group-01.png")
    face = pipeline.detector.detect_faces(source, max_num=1)[0]
    embedding = pipeline.recognizer.extract_embedding(source, face)
    # Use a different identity from the source; swapping a sample back onto itself
    # may correctly report no visible change and says nothing about missing DLLs.
    target_face = min(
        pipeline.detector.detect_faces(target),
        key=lambda face: float(np.dot(embedding, pipeline.recognizer.extract_embedding(target, face))),
    )
    target_embedding = pipeline.recognizer.extract_embedding(target, target_face)
    result = pipeline.process_image(
        target,
        [{"image": source, "face": face, "embedding": embedding}],
        embedding,
        target_embedding=target_embedding,
    )
    assert result.faces_detected > 0 and result.model_routing, result.person_outcomes
    report["faces_detected"] = result.faces_detected
    report["faces_swapped"] = result.faces_swapped
    report["outcomes"] = [{"status": item["status"], "reason": item["reason"]} for item in result.person_outcomes]
    report["providers"] = pipeline.providers
    # Enumerate DLL origins after inference, when lazy-loaded CUDA/BLAS libraries are present.
    kernel = ctypes.WinDLL("kernel32", winmode=0x00000800)
    psapi = ctypes.WinDLL("psapi", winmode=0x00000800)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.EnumProcessModules.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    modules = (ctypes.c_void_p * 2048)()
    needed = ctypes.c_ulong()
    assert psapi.EnumProcessModules(kernel.GetCurrentProcess(), modules, ctypes.sizeof(modules), ctypes.byref(needed))
    assert needed.value <= ctypes.sizeof(modules)
    origins = {"package": [], "windows": [], "external": []}
    windows = Path(os.environ["SYSTEMROOT"]).resolve()
    for handle in modules[: needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
            raise OSError("Unable to inspect a loaded module")
        path = Path(buffer.value).resolve()
        category = "package" if path.is_relative_to(root) else "windows" if path.is_relative_to(windows) else "external"
        origins[category].append(path.name)
    report["loaded_modules"] = {category: sorted(names) for category, names in origins.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "loaded_modules"}), flush=True)
    print("EXTERNAL_MODULES", origins["external"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.root, args.output)
