"""Optional post-swap face restoration.

This exists to recover skin-texture and edge sharpness that the swap models lose:
InSwapper and HyperSwap both cap their effective working crop at 256x256 (see
`swap_single_face_facefusion` in `core/pipeline.py`), so on modern high-megapixel
photos the swapped region is measurably softer than the untouched pixels around it
once warped back to full resolution.

CodeFormer was chosen over GFPGAN for this reintroduction because it exposes a
`fidelity_weight` control that biases the network toward the input face instead of
its generative prior. That matters specifically because this network runs on an
already-swapped face: any identity drift it introduces compounds the swap's own
identity choice, which the rest of the pipeline treats as a correctness property
(see `core/face_metrics.py::has_visible_face_change` and the pixel-protection tests).
Restoration output must always be blended back through the caller's swap mask, never
pasted unconditionally.
"""

import os

import cv2
import numpy as np
import onnxruntime as ort

from utils.gpu_helper import get_optimal_providers, local_session_options


class CodeFormerRestorer:
    """CodeFormer ONNX adapter for masked post-swap detail restoration."""

    MODEL_FILENAME = "codeformer.onnx"
    INPUT_SIZE = (512, 512)

    def __init__(self, model_path: str, providers: list | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"CodeFormer model not found at: {model_path}")
        if providers is None:
            providers = get_optimal_providers()
        self.session = ort.InferenceSession(model_path, providers=providers, sess_options=local_session_options())

    def restore(self, crop: np.ndarray, fidelity_weight: float = 0.75) -> np.ndarray:
        """Restore a BGR face crop of any size; resampled to CodeFormer's fixed 512px input."""
        original_size = (crop.shape[1], crop.shape[0])
        resized = cv2.resize(crop, self.INPUT_SIZE, interpolation=cv2.INTER_CUBIC)
        blob = resized[:, :, ::-1].astype(np.float32) / 255.0
        blob = ((blob - 0.5) / 0.5).transpose(2, 0, 1)[np.newaxis]
        weight = np.array(fidelity_weight, dtype=np.float64)
        output = self.session.run(["output"], {"input": blob, "weight": weight})[0]
        restored = output[0].transpose(1, 2, 0)
        restored = np.clip(restored * 0.5 + 0.5, 0.0, 1.0)[:, :, ::-1]
        restored = np.rint(restored * 255.0).astype(np.uint8)
        return cv2.resize(restored, original_size, interpolation=cv2.INTER_CUBIC)
