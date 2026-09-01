"""
Color harmonization and lighting adaptation module.
Implements Reinhard statistical color transfer in LAB color space and Poisson blending.
"""

import cv2
import numpy as np


def reinhard_color_transfer(
    source_face: np.ndarray,
    target_orig_face: np.ndarray,
    blend_ratio: float = 0.85,
    face_mask: np.ndarray | None = None,
) -> np.ndarray:
    """
    Adapts the lighting, skin tone, and color temperature of the swapped face
    to match the original target face using Reinhard color transfer in LAB color space
    with robust facial region masking and shadow chrominance stabilization.

    Args:
        source_face: Swapped face image in BGR (H, W, 3).
        target_orig_face: Original unswapped face image in BGR (H, W, 3).
        blend_ratio: Ratio of harmonized color (0.85 = high natural consistency).
        face_mask: Optional anatomical mask in aligned face space, with values in [0, 1].

    Returns:
        harmonized_face: BGR image matching the lighting and color temperature of target_orig_face.
    """
    if source_face.shape != target_orig_face.shape:
        target_orig_face = cv2.resize(target_orig_face, (source_face.shape[1], source_face.shape[0]))

    # Convert BGR to CIE L*a*b* color space
    src_lab = cv2.cvtColor(source_face, cv2.COLOR_BGR2LAB).astype(np.float32)
    tgt_lab = cv2.cvtColor(target_orig_face, cv2.COLOR_BGR2LAB).astype(np.float32)

    # 1. Compute statistical moments specifically over facial skin mask (avoids background/clothing bias)
    h, w = source_face.shape[:2]
    if face_mask is None:
        face_mask = np.zeros((h, w), dtype=np.float32)
        cv2.ellipse(face_mask, (w // 2, int(h * 0.52)), (int(w * 0.38), int(h * 0.46)), 0, 0, 360, 1.0, -1)
        face_mask = cv2.GaussianBlur(face_mask, (31, 31), 0)
    else:
        if face_mask.shape[:2] != (h, w):
            face_mask = cv2.resize(face_mask, (w, h), interpolation=cv2.INTER_LINEAR)
        if face_mask.ndim == 3:
            face_mask = face_mask[:, :, 0]
        face_mask = np.clip(face_mask.astype(np.float32), 0.0, 1.0)
    pts = np.where(face_mask > 0.5)
    if pts[0].size < 2:
        return source_face.copy()
    src_mean = np.mean(src_lab[pts], axis=0)
    src_std = np.std(src_lab[pts], axis=0)
    tgt_mean = np.mean(tgt_lab[pts], axis=0)
    tgt_std = np.std(tgt_lab[pts], axis=0)

    # Prevent division by zero
    src_std = np.maximum(src_std, 1e-6)

    # Apply Reinhard transformation per channel
    harmonized_lab = np.zeros_like(src_lab)
    for c in range(3):
        harmonized_lab[:, :, c] = ((src_lab[:, :, c] - src_mean[c]) / src_std[c]) * tgt_std[c] + tgt_mean[c]

    # 2. Suppress unnatural purple/magenta chromatic drift in dark and shadow areas
    # In natural human skin, melanin produces warm amber tones where b* (yellow) >= a* (red/magenta).
    # Super-resolution models on dark contours can create cool/magenta bias (a* > b* or B > G).
    L_chan = harmonized_lab[:, :, 0]
    a_chan = harmonized_lab[:, :, 1]
    b_chan = harmonized_lab[:, :, 2]

    shadow_weight = np.clip((160.0 - L_chan) / 80.0, 0.0, 1.0)
    max_a_allowed = b_chan + 1.5
    excess_magenta = np.maximum(0.0, a_chan - max_a_allowed)
    harmonized_lab[:, :, 1] -= excess_magenta * shadow_weight * 0.95

    harmonized_lab = np.clip(harmonized_lab, 0, 255)
    harmonized_bgr = cv2.cvtColor(harmonized_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    # 3. Transfer skin chrominance from target_orig_face to eliminate residual chromatic aberration
    ycrcb_harm = cv2.cvtColor(harmonized_bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    ycrcb_orig = cv2.cvtColor(target_orig_face, cv2.COLOR_BGR2YCrCb).astype(np.float32)

    soft_face_mask = face_mask

    # Blend chrominance (Cr, Cb) with original skin tone while 100% preserving high-frequency luminance details
    ycrcb_harm[:, :, 1] = (1.0 - soft_face_mask * 0.70) * ycrcb_harm[:, :, 1] + (soft_face_mask * 0.70) * ycrcb_orig[
        :, :, 1
    ]
    ycrcb_harm[:, :, 2] = (1.0 - soft_face_mask * 0.70) * ycrcb_harm[:, :, 2] + (soft_face_mask * 0.70) * ycrcb_orig[
        :, :, 2
    ]

    clean_bgr = cv2.cvtColor(np.clip(ycrcb_harm, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)

    # Clamp B <= G in warm skin tones (R > G) to eliminate any edge purple fringing
    b, g, r = cv2.split(clean_bgr)
    skin_warm = (r > g) & (b > g)
    b_fixed = np.where(skin_warm, np.maximum(0, g - 2), b)
    final_harmonized = cv2.merge([b_fixed, g, r])

    if blend_ratio < 1.0:
        final_harmonized = cv2.addWeighted(final_harmonized, blend_ratio, source_face, 1.0 - blend_ratio, 0.0)

    return final_harmonized
