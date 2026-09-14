"""
Face Detection and 5-Point Landmark Extraction module using SCRFD.
"""

import os
import warnings
from dataclasses import dataclass

import numpy as np

# InsightFace 1.0.1 still calls this deprecated skimage API while importing its
# face alignment helper. Scope the compatibility filter to the integration that
# needs it instead of mutating warning behavior whenever ``core`` is imported.
warnings.filterwarnings(
    "ignore",
    message=r"`estimate` is deprecated since version 0\.26 and will be removed in version 2\.2.*",
    category=FutureWarning,
    module=r"insightface\.utils\.face_align",
)

from insightface.model_zoo import get_model  # noqa: E402

from utils.gpu_helper import get_optimal_providers  # noqa: E402


@dataclass
class DetectedFace:
    """Represents a detected face with bounding box, keypoints, score, and optional embedding."""

    bbox: np.ndarray  # [x1, y1, x2, y2]
    kps: np.ndarray  # (5, 2) [left_eye, right_eye, nose, left_mouth, right_mouth]
    det_score: float  # Detection confidence score
    embedding: np.ndarray | None = None  # 512-d ArcFace feature vector (normalized)
    similarity: float = 0.0  # Cosine similarity to the target identity
    extra: dict | None = None

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}

    def __getitem__(self, item):
        if self.extra is None:
            self.extra = {}
        return self.extra.get(item)

    def __setitem__(self, key, value):
        if self.extra is None:
            self.extra = {}
        self.extra[key] = value


class FaceDetector:
    """SCRFD Face Detector wrapper."""

    def __init__(
        self,
        model_path: str,
        providers: list[str] | None = None,
        confidence_threshold: float = 0.5,
        input_size: tuple[int, int] = (640, 640),
    ):
        """
        Initialize the SCRFD face detector.

        Args:
            model_path: Path to the scrfd_10g_bnkps.onnx model file.
            providers: ONNX execution providers, e.g. ['CUDAExecutionProvider', 'CPUExecutionProvider'].
            confidence_threshold: Minimum detection confidence score.
            input_size: Input resolution (width, height) for detection inference.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Detector model not found at: {model_path}")

        if providers is None:
            providers = get_optimal_providers()

        self.confidence_threshold = confidence_threshold
        self.input_size = input_size

        # Load SCRFD model
        self.model = get_model(model_path, providers=providers)
        self.model.prepare(ctx_id=0, input_size=self.input_size, det_thresh=self.confidence_threshold)

    def detect_faces(
        self, image: np.ndarray, max_num: int = 0, confidence_threshold: float | None = None
    ) -> list[DetectedFace]:
        """
        Detects all faces and their 5 facial landmarks in the input image.

        Args:
            image: BGR image (H, W, 3).
            max_num: Maximum number of faces to return (0 = all detected faces).
            confidence_threshold: Optional override for detection threshold.

        Returns:
            List of DetectedFace objects sorted by bounding box area (largest first).
        """
        thresh = confidence_threshold if confidence_threshold is not None else self.confidence_threshold
        old_thresh = self.model.det_thresh
        if confidence_threshold is not None:
            self.model.det_thresh = confidence_threshold
        try:
            bboxes, kpss = self.model.detect(image, max_num=max_num, metric="default")
        finally:
            self.model.det_thresh = old_thresh
        if bboxes is None or len(bboxes) == 0:
            return []

        detected_faces: list[DetectedFace] = []
        for i in range(len(bboxes)):
            bbox = bboxes[i, :4]
            score = float(bboxes[i, 4])
            kps = kpss[i] if kpss is not None and len(kpss) > i else None

            if score < thresh or kps is None:
                continue

            detected_faces.append(DetectedFace(bbox=bbox, kps=kps, det_score=score))

        # Sort by face size (bounding box area) in descending order
        detected_faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        return detected_faces
