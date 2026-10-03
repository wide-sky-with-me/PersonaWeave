"""Semantic face-region parsing (BiSeNet) for protecting specific facial regions during swap.

Unlike core/face_parser.py's DenseFaceParser (106-point landmark geometry) or core/occluder.py's
FaceOccluder (XSeg foreign-object detection), this module classifies every pixel into one of 19
CelebAMask-HQ semantic classes (skin, eyebrows, eyes, glasses, nose, mouth, ears, hair, ...). It is
used to protect regions InSwapper commonly distorts but that XSeg does not treat as "occlusion" --
most notably eyeglasses, which routinely melt or warp during a swap.
"""

import os

import cv2
import numpy as np
import onnxruntime as ort

from utils.gpu_helper import get_optimal_providers, local_session_options

# CelebAMask-HQ 19-class convention used by this model (matches FaceFusion's
# facefusion.choices.face_mask_region_set for the subset it exposes).
REGION_CLASSES = {
    "background": 0,
    "skin": 1,
    "left-eyebrow": 2,
    "right-eyebrow": 3,
    "left-eye": 4,
    "right-eye": 5,
    "glasses": 6,
    "left-ear": 7,
    "right-ear": 8,
    "earring": 9,
    "nose": 10,
    "mouth": 11,
    "upper-lip": 12,
    "lower-lip": 13,
    "neck": 14,
    "necklace": 15,
    "cloth": 16,
    "hair": 17,
    "hat": 18,
}


class RegionFaceParser:
    """ONNX wrapper producing per-region 'keep' masks (high = safe to swap, low = protect)."""

    MODEL_SIZE = (512, 512)
    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, model_path: str, providers: list[str] | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"BiSeNet region parser model not found at {model_path}")

        session_options = local_session_options()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            model_path,
            sess_options=session_options,
            providers=providers or get_optimal_providers(),
        )

    @classmethod
    def preprocess(cls, face_bgr: np.ndarray) -> np.ndarray:
        """Resize/normalize an aligned BGR crop into the model's NCHW input tensor."""
        resized = cv2.resize(face_bgr, cls.MODEL_SIZE)
        rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
        normalized = (rgb - cls.MEAN) / cls.STD
        return normalized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

    @classmethod
    def postprocess_protection_mask(
        cls, logits: np.ndarray, protected_regions: tuple[str, ...], output_size: tuple[int, int]
    ) -> np.ndarray:
        """Convert (19, H, W) logits into a 'keep' mask at output_size (width, height).

        Args:
            logits: Per-class logits, shape (num_classes, H, W).
            protected_regions: Region names (see REGION_CLASSES) to exclude from swapping.
            output_size: (width, height) of the returned mask, in the caller's crop space.
        """
        class_map = logits.argmax(0)
        indices = [REGION_CLASSES[name] for name in protected_regions]
        protected = np.isin(class_map, indices).astype(np.float32)
        protected = cv2.resize(protected, output_size, interpolation=cv2.INTER_NEAREST)
        keep = 1.0 - protected
        # Same convention as FaceOccluder: blur then keep only the "confidently safe" half of the
        # transition, giving a soft edge on the keep side and a firm cutoff into protection.
        # Keep the same normalized feather on native 128px and boosted 256px crops.
        # The original 256px/sigma=5 baseline remains unchanged.
        sigma = output_size[0] * 5.0 / 256.0
        return (cv2.GaussianBlur(keep.clip(0, 1), (0, 0), sigma).clip(0.5, 1) - 0.5) * 2

    def create_region_protection_mask(
        self, face_bgr: np.ndarray, protected_regions: tuple[str, ...] = ("glasses",)
    ) -> np.ndarray:
        """Return a 'keep' mask matching the aligned BGR crop size, in [0, 1]."""
        height, width = face_bgr.shape[:2]
        input_tensor = self.preprocess(face_bgr)
        logits = self.session.run(None, {"input": input_tensor})[0][0]
        return self.postprocess_protection_mask(logits, protected_regions, (width, height))
