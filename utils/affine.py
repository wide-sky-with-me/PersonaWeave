"""
Affine transformation and coordinate mapping utilities for face alignment,
warping, and seamless inverse pasting.
"""

import cv2
import numpy as np

# Standard 5-point landmark templates
# 1. Standard ArcFace 112x112 reference template
ARCFACE_DST_112 = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)

# 2. Standard InSwapper 128x128 reference template
INSWAPPER_DST_128 = (
    np.array(
        [
            [0.36167652, 0.40387734],
            [0.63696714, 0.40235468],
            [0.50019777, 0.53982928],
            [0.39339682, 0.66186835],
            [0.60606822, 0.66060877],
        ],
        dtype=np.float32,
    )
    * 128.0
)

# 3. Standard FFHQ / CodeFormer 512x512 template
CODEFORMER_DST_512 = np.array(
    [
        [192.98138, 239.94708],
        [318.90277, 240.34360],
        [256.03415, 314.01935],
        [201.26117, 371.41043],
        [313.08905, 371.15118],
    ],
    dtype=np.float32,
)


def estimate_affine_matrix(src_landmarks: np.ndarray, dst_template: np.ndarray) -> np.ndarray:
    """
    Computes 2x3 affine similarity transform matrix mapping src_landmarks to dst_template.

    Args:
        src_landmarks: (5, 2) detected face landmark coordinates.
        dst_template: (5, 2) target canonical template coordinates.

    Returns:
        matrix: 2x3 affine transformation matrix (np.float32).
    """
    matrix, _ = cv2.estimateAffinePartial2D(src_landmarks, dst_template, method=cv2.LMEDS)
    if matrix is None:
        # Fallback to standard affine transform if partial estimation fails
        matrix = cv2.getAffineTransform(src_landmarks[:3], dst_template[:3])
    return matrix.astype(np.float32)


def warp_face(image: np.ndarray, matrix: np.ndarray, crop_size: tuple[int, int]) -> np.ndarray:
    """
    Warps face region from original image into canonical aligned space.

    Args:
        image: Original input image (H, W, 3) BGR or RGB.
        matrix: 2x3 affine matrix from estimate_affine_matrix.
        crop_size: (width, height) of the output aligned face.

    Returns:
        cropped: Aligned face image of shape (height, width, 3).
    """
    return cv2.warpAffine(image, matrix, crop_size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def create_smooth_face_mask(crop_size: tuple[int, int], feather_radius: int = 15) -> np.ndarray:
    """
    Creates an elliptical soft feathered mask for blending aligned face back into canvas.

    Args:
        crop_size: (width, height) of the face crop (e.g. (128, 128) or (512, 512)).
        feather_radius: Blur radius for soft edge transition.

    Returns:
        mask: Single-channel float32 mask in range [0.0, 1.0] of shape (height, width).
    """
    w, h = crop_size
    mask = np.zeros((h, w), dtype=np.float32)
    center = (w // 2, int(h * 0.52))
    axes = (int(w * 0.38), int(h * 0.46))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)

    if feather_radius > 0:
        ksize = feather_radius * 2 + 1
        mask = cv2.GaussianBlur(mask, (ksize, ksize), 0)
    return np.clip(mask, 0.0, 1.0)


def paste_back_face(
    swapped_crop: np.ndarray,
    original_image: np.ndarray,
    affine_matrix: np.ndarray,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """
    Warps the swapped & enhanced face back to the original image canvas using inverse affine transform.

    Args:
        swapped_crop: Swapped & restored face image (H_crop, W_crop, 3).
        original_image: Full-size original background image (H, W, 3).
        affine_matrix: 2x3 forward affine matrix from original space to crop space.
        mask: Optional single-channel float32 mask (H_crop, W_crop) in [0.0, 1.0].

    Returns:
        result_image: Original canvas with swapped face seamlessly blended in.
    """
    h_orig, w_orig = original_image.shape[:2]
    h_crop, w_crop = swapped_crop.shape[:2]

    # Compute inverse affine transformation
    inv_matrix = cv2.invertAffineTransform(affine_matrix)

    # Default mask if not provided
    if mask is None:
        mask = create_smooth_face_mask((w_crop, h_crop), feather_radius=max(5, int(w_crop * 0.08)))

    # Inverse warp the face crop and the alpha mask to original image dimensions
    warped_face = cv2.warpAffine(swapped_crop, inv_matrix, (w_orig, h_orig), flags=cv2.INTER_LINEAR)
    warped_mask = cv2.warpAffine(mask, inv_matrix, (w_orig, h_orig), flags=cv2.INTER_LINEAR)
    warped_mask = np.expand_dims(np.clip(warped_mask, 0.0, 1.0), axis=2)

    # Alpha blend into original canvas
    result = original_image.astype(np.float32) * (1.0 - warped_mask) + warped_face.astype(np.float32) * warped_mask
    return np.clip(result, 0, 255).astype(np.uint8)
