"""Non-production CUDA audit: run all shipped models and inspect actual ONNX kernel profiling."""

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path


def verify(root, report_path):
    """Fail on CPU fallback; use a shipped real photo for image tensors and record each model's kernels."""
    if os.name != "nt":
        raise RuntimeError("This audit must run with the bundled Windows Python")
    os.chdir(root)
    import cv2
    import numpy as np
    import onnxruntime as ort

    from utils.gpu_helper import get_optimal_providers

    providers = get_optimal_providers()
    assert isinstance(providers[0], tuple) and providers[0][0] == "CUDAExecutionProvider", providers
    manifest = json.loads((root / "manifest.json").read_text())
    original = cv2.imdecode(
        np.frombuffer((root / "data/test_images/group-01.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"runtime": ort.__version__, "models": {}}
    for name in manifest["models"]:
        options = ort.SessionOptions()
        options.enable_profiling = True
        options.profile_file_prefix = str(report_path.parent / ("profile-" + Path(name).stem))
        session = ort.InferenceSession(str(root / "models" / name), sess_options=options, providers=providers)
        assert "CUDAExecutionProvider" in session.get_providers(), name
        inputs = {}
        for item in session.get_inputs():
            # SCRFD has dynamic spatial dimensions; its production detector uses 640x640.
            shape = [
                dimension if isinstance(dimension, int) and dimension > 0 else (1 if index == 0 else 640)
                for index, dimension in enumerate(item.shape)
            ]
            if len(shape) == 4:
                nchw = shape[1] == 3
                height, width = (shape[2], shape[3]) if nchw else (shape[1], shape[2])
                array = cv2.resize(original, (width, height)).astype(np.float32) / 255.0
                inputs[item.name] = (array.transpose(2, 0, 1) if nchw else array)[None].copy()
            else:
                # InSwapper's second tensor is a normalized identity embedding.
                inputs[item.name] = np.full(shape, 1 / np.sqrt(shape[-1]), dtype=np.float32)
        outputs = session.run(None, inputs)
        assert all(np.isfinite(value).all() for value in outputs), name
        profile = Path(session.end_profiling())
        counts = Counter(
            event.get("args", {}).get("provider")
            for event in json.loads(profile.read_text())
            if event.get("cat") == "Node"
        )
        assert counts["CUDAExecutionProvider"] > 0, (name, counts)
        report["models"][name] = {
            "providers": session.get_providers(),
            "inputs": {item.name: item.shape for item in session.get_inputs()},
            "outputs": [list(value.shape) for value in outputs],
            "cuda_kernel_count": counts["CUDAExecutionProvider"],
            "cpu_kernel_count": counts["CPUExecutionProvider"],
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        }
        print("CUDA_KERNELS_VERIFIED", name, counts["CUDAExecutionProvider"], flush=True)
        del session
    report["passed"] = True
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    verify(args.root.resolve(), args.report.resolve())
