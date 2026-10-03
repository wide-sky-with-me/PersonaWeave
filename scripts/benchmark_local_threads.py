"""Non-production native timing comparison; uses only shipped local models and fixtures."""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def main():
    """Compare explicit thread policies in a disposable process, never changing installed files."""
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--mode", choices=["default", "sleep", "two", "four"], required=True)
    p.add_argument("--provider", choices=["cpu", "cuda"], required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    os.chdir(a.root)
    sys.path.insert(0, str(a.root))
    import numpy as np
    import onnxruntime as ort

    if a.mode in ("default", "sleep", "two", "four"):
        original_init = ort.InferenceSession.__init__

        def configured(self, path, sess_options=None, *args, **kwargs):
            opts = sess_options or ort.SessionOptions()
            opts.add_session_config_entry("session.intra_op.allow_spinning", "1" if a.mode == "default" else "0")
            opts.add_session_config_entry("session.inter_op.allow_spinning", "1" if a.mode == "default" else "0")
            if a.mode in ("two", "four"):
                opts.intra_op_num_threads = {"two": 2, "four": 4}[a.mode]
            original_init(self, path, opts, *args, **kwargs)

        ort.InferenceSession.__init__ = configured
    from api.portable import instance_lock
    from core.pipeline import PersonaWeavePipeline
    from utils.gpu_helper import get_optimal_providers
    from utils.image_io import read_image

    with instance_lock(a.root) as acquired:
        if not acquired:
            raise RuntimeError("Close the installed workbench before loading diagnostic models")
        providers = ["CPUExecutionProvider"] if a.provider == "cpu" else get_optimal_providers()
        started = time.perf_counter()
        pipeline = PersonaWeavePipeline(providers=providers)
        startup = time.perf_counter() - started
        sessions = {
            name: (model.session if hasattr(model, "session") else model.model.session)
            for name in ("detector", "recognizer", "swapper", "parser", "occluder", "region_parser")
            if (model := getattr(pipeline, name, None)) is not None
        }
        report = {
            "mode": a.mode,
            "provider": a.provider,
            "ort": ort.__version__,
            "cpu_count": os.cpu_count(),
            "startup_seconds": startup,
            "sessions": {k: s.get_providers() for k, s in sessions.items()},
            "runs": [],
        }
        metrics = {}

        def wrap(obj, name, label):
            original = getattr(obj, name)

            def timed(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    entry = metrics.setdefault(label, {"seconds": 0, "calls": 0})
                    entry["seconds"] += time.perf_counter() - start
                    entry["calls"] += 1

            setattr(obj, name, timed)

        for obj, name, label in [
            (pipeline.detector, "detect_faces", "detect"),
            (pipeline.recognizer, "extract_embedding", "recognize"),
            (pipeline.swapper, "infer_aligned", "swap"),
            (pipeline.parser, "extract_106_landmarks", "landmark"),
            (pipeline.occluder, "create_occlusion_mask", "occlusion"),
            (pipeline.region_parser, "create_region_protection_mask", "region"),
        ]:
            if obj is not None:
                wrap(obj, name, label)
        source = str(a.repo / "tests/fixtures/source-01.png")
        target_path = a.repo / "tests/fixtures/group-01.png"
        target = read_image(str(target_path))
        report["source_code_sha256"] = {
            name: hashlib.sha256((a.root / name).read_bytes()).hexdigest()
            for name in ("utils/gpu_helper.py", "core/pipeline.py")
        }
        report["input_sha256"] = hashlib.sha256(target_path.read_bytes()).hexdigest()
        report["shape"] = list(target.shape)
        for repeat in range(3):
            metrics.clear()
            start = time.perf_counter()
            cpu = os.times()
            refs = pipeline.load_source_references([source])
            ref_seconds = time.perf_counter() - start
            target_face = sorted(pipeline.detector.detect_faces(target), key=lambda f: f.bbox[0])[0]
            embedding = pipeline.recognizer.extract_embedding(target, target_face)
            result = pipeline.process_image(
                target, refs, refs[0]["embedding"], target_embedding=embedding, enable_dense_mask=True
            )
            elapsed = time.perf_counter() - start
            cpu_seconds = os.times().user + os.times().system - cpu.user - cpu.system
            record = {
                "seconds": elapsed,
                "cpu_seconds": cpu_seconds,
                "reference_seconds": ref_seconds,
                "faces_swapped": result.faces_swapped,
                "stages": json.loads(json.dumps(metrics)),
                "output_sha256": hashlib.sha256(result.output_image.tobytes()).hexdigest(),
            }
            report["runs"].append(record)
            np.save(a.report.with_name(a.report.stem + f"-{repeat}.npy"), result.output_image)
            print("TIMING", json.dumps(record), flush=True)
        a.report.write_text(json.dumps(report, indent=2))
        print("DONE", flush=True)


if __name__ == "__main__":
    main()
