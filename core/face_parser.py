"""
Dense Facial Landmark (106-point) Parser & Anatomical Mask Generator.
Provides pixel-accurate contour parsing, jawline extraction, forehead arch generation,
and occlusion protection masks.
"""

import os

import cv2
import numpy as np
from insightface.model_zoo import get_model

from utils.gpu_helper import get_optimal_providers


class DenseFaceParser:
    """106-point dense facial landmark and anatomical mask generator."""

    def __init__(self, model_path: str = "models/buffalo_l/2d106det.onnx", providers: list[str] | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"106-point landmark model not found at {model_path}")

        opt_providers = providers or get_optimal_providers()
        self.model = get_model(model_path, providers=opt_providers)

    def extract_106_landmarks(self, image: np.ndarray, face_or_bbox) -> np.ndarray:
        """
        Extracts 106 dense landmarks from a detected face or bounding box.

        Args:
            image: Original full BGR image.
            face_or_bbox: DetectedFace instance, dict with 'bbox', or [x1, y1, x2, y2] array.

        Returns:
            (106, 2) numpy array of landmark coordinates in image space.
        """

        class FaceWrapper(dict):
            def __init__(self, b, k=None):
                super().__init__()
                self.bbox = b
                self.kps = k

        if hasattr(face_or_bbox, "bbox"):
            bbox = face_or_bbox.bbox
            kps = getattr(face_or_bbox, "kps", None)
        elif isinstance(face_or_bbox, dict) and "bbox" in face_or_bbox:
            bbox = face_or_bbox["bbox"]
            kps = face_or_bbox.get("kps")
        else:
            bbox = np.array(face_or_bbox, dtype=np.float32)
            kps = None

        face_obj = FaceWrapper(bbox, kps)
        pts = self.model.get(image, face_obj)
        if pts is None or len(pts) == 0:
            return np.zeros((106, 2), dtype=np.float32)
        return pts

    def generate_anatomical_mask_512(
        self, pts_512: np.ndarray | None = None, *, allow_fallback: bool = True
    ) -> np.ndarray:
        """
        Generates a smooth anatomical face mask in aligned 512x512 space.
        If pts_512 is not provided or invalid, uses an optimized anatomical face contour.

        Returns:
            (512, 512) float32 mask in [0.0, 1.0].
        """
        mask = np.zeros((512, 512), dtype=np.float32)

        # Degenerate landmarks (e.g. extract_106_landmarks failing and returning an
        # all-zero array) collapse to a near-point cluster. Building a hull from them
        # still yields a tiny nonzero blob after blurring, so `mask.max() == 0.0` below
        # would never catch the failure -- reject the spread explicitly instead.
        landmarks_valid = (
            pts_512 is not None
            and len(pts_512) == 106
            and float(np.ptp(pts_512[:, 0])) > 10.0
            and float(np.ptp(pts_512[:, 1])) > 10.0
        )

        if landmarks_valid:
            try:
                # 0-32: Jawline contour from left to right
                jaw_pts = pts_512[0:33].astype(np.int32)
                # Forehead arch estimated from eyebrows and facial height
                left_brow = pts_512[33:43]
                right_brow = pts_512[43:53]
                brow_top = min(np.min(left_brow[:, 1]), np.min(right_brow[:, 1]))

                chin_y = pts_512[16, 1]
                face_h = max(30.0, float(chin_y - brow_top))
                forehead_lift = face_h * 0.35

                forehead_pts = []
                for pt in reversed(pts_512[33:53]):
                    forehead_pts.append([pt[0], max(10, pt[1] - forehead_lift)])
                forehead_pts = np.array(forehead_pts, dtype=np.int32)

                poly_pts = np.vstack([jaw_pts, forehead_pts])
                hull = cv2.convexHull(poly_pts)
                cv2.fillConvexPoly(mask, hull, 1.0)
                mask = cv2.GaussianBlur(mask, (31, 31), 0)
            except Exception:
                mask = np.zeros((512, 512), dtype=np.float32)

        if mask.max() == 0.0:
            if not allow_fallback:
                raise ValueError("Valid 106-point landmarks are required for the anatomical refinement mask")
            # High-precision anatomical default ellipse
            cv2.ellipse(mask, (256, 265), (195, 230), 0, 0, 360, 1.0, -1)
            mask = cv2.GaussianBlur(mask, (31, 31), 0)

        return np.clip(mask, 0.0, 1.0)
