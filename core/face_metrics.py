"""Pure face geometry and measurable quality policies shared across workflows."""

import cv2
import numpy as np

from core.detector import DetectedFace


def calculate_yaw_ratio(kps: np.ndarray) -> float:
    """Return roll-invariant horizontal yaw in [-1, 1] from five landmarks."""
    eye_vec = kps[1] - kps[0]
    eye_dist = np.linalg.norm(eye_vec)
    if eye_dist < 1e-6:
        return 0.0
    u_eye = eye_vec / eye_dist
    eye_mid = (kps[0] + kps[1]) / 2.0
    nose_vec = kps[2] - eye_mid
    raw_yaw = float(np.dot(nose_vec, u_eye) / (eye_dist * 0.5))
    return float(np.clip(raw_yaw, -1.0, 1.0))


def calculate_reference_quality(image: np.ndarray, face: DetectedFace) -> float:
    """Score a reference face using measurable detail, size, confidence, and pose."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = np.asarray(face.bbox, dtype=np.float32)
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0

    face_size = max(x2 - x1, y2 - y1)
    size_score = float(np.clip((face_size - 64.0) / 192.0, 0.0, 1.0))
    sharpness = float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    sharpness_score = float(np.clip(sharpness / 180.0, 0.0, 1.0))
    confidence_score = float(np.clip((face.det_score - 0.5) / 0.45, 0.0, 1.0))
    pose_score = float(np.clip(1.0 - abs(calculate_yaw_ratio(face.kps)) / 0.65, 0.0, 1.0))
    return 0.35 * size_score + 0.30 * sharpness_score + 0.20 * confidence_score + 0.15 * pose_score


def describe_reference_quality(score: float) -> str:
    """Return the stable user-facing quality tier for a reference score."""
    if score >= 0.72:
        return "excellent"
    if score >= 0.48:
        return "usable"
    return "low"


def has_visible_face_change(
    before: np.ndarray,
    after: np.ndarray,
    bbox: np.ndarray,
    minimum_mean_difference: float = 3.0,
) -> bool:
    """Return whether output pixels materially changed inside one target face."""
    if before.shape != after.shape or before.ndim != 3:
        return False
    height, width = before.shape[:2]
    x1, y1, x2, y2 = np.asarray(bbox, dtype=np.float32)
    x1 = max(0, min(width, int(np.floor(x1))))
    y1 = max(0, min(height, int(np.floor(y1))))
    x2 = max(x1, min(width, int(np.ceil(x2))))
    y2 = max(y1, min(height, int(np.ceil(y2))))
    if x2 <= x1 or y2 <= y1:
        return False
    difference = cv2.absdiff(before[y1:y2, x1:x2], after[y1:y2, x1:x2])
    return float(difference.mean()) >= minimum_mean_difference


def incompatible_extreme_pose(target_face: DetectedFace, source_references: list[dict]) -> tuple[bool, float | None]:
    """Reject a near-profile target when no source reference has a comparable yaw."""
    target_yaw = calculate_yaw_ratio(target_face.kps)
    if abs(target_yaw) <= 0.75:
        return False, None
    source_yaws = [float(reference["yaw"]) for reference in source_references if reference.get("yaw") is not None]
    if not source_yaws:
        return True, None
    closest_gap = min(abs(target_yaw - source_yaw) for source_yaw in source_yaws)
    return closest_gap > 0.45, closest_gap
