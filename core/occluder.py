"""XSeg occlusion detection for aligned face crops (matches facefusion's face_masker.py convention)."""

import os

import cv2
import numpy as np
import onnxruntime as ort

from utils.gpu_helper import get_optimal_providers


class FaceOccluder:
    """ONNX wrapper producing a 'keep' mask: high = clean visible skin, low = occluded (hand/object)."""

    def __init__(self, model_path: str, providers: list[str] | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"XSeg model not found at {model_path}")

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            model_path,
            sess_options=session_options,
            providers=providers or get_optimal_providers(),
        )

    def create_occlusion_mask(self, face_bgr: np.ndarray) -> np.ndarray:
        """Return a float32 'keep' mask matching the aligned BGR crop size, in [0, 1]."""
        # No BGR->RGB conversion: the upstream xseg_1 model is trained on raw BGR crops.
        input_tensor = cv2.resize(face_bgr, (256, 256))[np.newaxis, ...].astype(np.float32) / 255.0
        output = self.session.run(["output"], {"input": input_tensor})[0][0]

        mask = output.clip(0, 1).astype(np.float32)
        height, width = face_bgr.shape[:2]
        mask = cv2.resize(mask, (width, height))
        # Values below 0.5 collapse to 0 (occluded); the blur softens the transition band.
        # The model infers at a fixed 256px, so scale the feather with the actual crop width
        # (matching create_facefusion_box_mask's approach) instead of a fixed pixel sigma --
        # otherwise pixel-boosted crops up to 1024px get a proportionally much harder edge than
        # the 256px baseline this sigma was tuned against.
        sigma = max(2.0, width * 5.0 / 256.0)
        mask = (cv2.GaussianBlur(mask.clip(0, 1), (0, 0), sigma).clip(0.5, 1) - 0.5) * 2
        return mask
