"""Diagnostic only: compare exact compositing pixels and tracked allocation peaks, without model calls."""

import hashlib
import json
import tracemalloc
from pathlib import Path

import numpy as np

from core.pipeline import composite_in_rows


def main():
    rng = np.random.default_rng(20260923)
    canvas = rng.integers(0, 256, (3000, 4000, 3), dtype=np.uint8)
    swapped = rng.integers(0, 256, canvas.shape, dtype=np.uint8)
    mask = rng.random(canvas.shape[:2], dtype=np.float32)

    def whole_canvas():
        alpha = mask[:, :, None]
        blended = canvas.astype(np.float32) * (1.0 - alpha) + swapped.astype(np.float32) * alpha
        return np.clip(blended, 0, 255).astype(np.uint8)

    results = {}
    for name, compose in (
        ("prior_whole_canvas", whole_canvas),
        ("bounded_rows", lambda: composite_in_rows(canvas, swapped, mask)),
    ):
        tracemalloc.start()
        output = compose()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[name] = {"peak_tracked_bytes": peak, "sha256": hashlib.sha256(output).hexdigest()}
        del output
    assert results["prior_whole_canvas"]["sha256"] == results["bounded_rows"]["sha256"]
    results["canvas_pixels"] = [4000, 3000]
    path = Path("outputs/memory-queue-validation/compositing.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
