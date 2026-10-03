"""Diagnostic only: measure affine pasteback allocations without loading models or calling AI."""

import hashlib
import json
import time
import tracemalloc
from pathlib import Path

import cv2
import numpy as np

from core.compositing import warp_face_region
from core.pipeline import composite_in_rows


def main():
    """Compare the former full-canvas warp with the bounded production helper."""
    rng = np.random.default_rng(20260930)
    canvas = rng.integers(0, 256, (3000, 4000, 3), dtype=np.uint8)
    face = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    mask = cv2.GaussianBlur(np.pad(np.ones((192, 192), np.float32), 32), (31, 31), 5)
    transform = cv2.getRotationMatrix2D((128, 128), 23, 1.7)
    transform[:, 2] += (2100, 1500)

    def full():
        pixels = cv2.warpAffine(face, transform, (4000, 3000), borderMode=cv2.BORDER_REPLICATE)
        alpha = cv2.warpAffine(mask, transform, (4000, 3000)).clip(0, 1)
        return composite_in_rows(canvas, pixels, alpha)

    def bounded():
        (left, top, right, bottom), pixels, alpha = warp_face_region(face, mask, transform, canvas.shape)
        result = canvas.copy()
        result[top:bottom, left:right] = composite_in_rows(canvas[top:bottom, left:right], pixels, alpha)
        return result

    report = {
        "opencv": cv2.__version__,
        "canvas": [4000, 3000],
        "seed": 20260930,
        "input_sha256": hashlib.sha256(canvas).hexdigest(),
    }
    outputs = []
    for name, operation in (("full_canvas_warp", full), ("region_warp", bounded)):
        tracemalloc.start()
        start = time.perf_counter()
        output = operation()
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report[name] = {
            "tracked_peak_bytes": peak,
            "seconds": elapsed,
            "output_sha256": hashlib.sha256(output).hexdigest(),
        }
        outputs.append(output)
    difference = np.abs(outputs[0].astype(np.int16) - outputs[1].astype(np.int16))
    report["max_channel_difference"] = int(difference.max())
    report["changed_channel_fraction"] = np.count_nonzero(difference) / difference.size
    path = Path("docs/validation/region-compositing-20260930.json")
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
