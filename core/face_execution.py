"""Single-face execution, independent of image/person orchestration.

Decision: .agents/notes/platform/20261014-domain-extraction-with-stable-entrypoints.md
"""

import cv2
import numpy as np

from core.color_matcher import reinhard_color_transfer
from core.compositing import region_bbox_mean, warp_face_region
from core.detector import DetectedFace
from core.face_metrics import calculate_yaw_ratio
from core.swap_diagnostics import mask_component, validated_mask
from core.swapper import (
    ARCFACE_112_V1_TEMPLATE,
    ARCFACE_128_TEMPLATE,
    create_facefusion_box_mask,
    explode_pixel_boost,
    implode_pixel_boost,
)


def composite_in_rows(canvas: np.ndarray, swapped: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Preserve float32 alpha math exactly while bounding intermediate arrays to 256 rows."""
    result = np.empty_like(canvas)
    for start in range(0, canvas.shape[0], 256):
        rows = slice(start, start + 256)
        alpha = mask[rows, :, np.newaxis]
        blended = canvas[rows].astype(np.float32) * (1.0 - alpha) + swapped[rows].astype(np.float32) * alpha
        result[rows] = np.clip(blended, 0, 255).astype(np.uint8)
    return result


def swap_single_face_facefusion(
    canvas: np.ndarray,
    target_face: DetectedFace,
    source_embedding: np.ndarray,
    *,
    swapper,
    enable_dense_mask: bool,
    manual_mask: np.ndarray | None,
    identity_blend_strength: float = 0.9,
    enable_color_transfer: bool = True,
    color_transfer_mode: str = "boundary",
    color_transfer_ring_strength: float = 0.45,
    color_transfer_ring_width_px: float = 15.0,
    enable_face_restoration: bool = False,
    restoration_fidelity_weight: float = 0.75,
    restoration_strength: float = 0.5,
    diagnostics: dict | None = None,
    parser=None,
    occluder=None,
    region_parser=None,
    restorer=None,
) -> np.ndarray:
    """Execute one aligned swap with explicit runtimes; model loading, locking and person assignment belong to the caller."""
    model_width, model_height = swapper.spec.input_size
    face_size = max(
        float(target_face.bbox[2] - target_face.bbox[0]),
        float(target_face.bbox[3] - target_face.bbox[1]),
    )
    # Interleaved pixel boost is useful up to a 256px working crop, but pushing a
    # 128px model to 512px means four independent tiles per axis. Real-image
    # regressions showed that the seams between those tiles suppress facial
    # high-frequency detail on already-large faces. Keep one 256px working
    # crop for medium/large faces and use the model's native size for small
    # faces; 256px models therefore never need interleaved boosting.
    boost_total = max(1, 256 // model_width) if face_size >= 96 else 1
    boost_size = (model_width * boost_total, model_height * boost_total)

    if swapper.spec.crop_template in {"inswapper_128", "arcface_128"}:
        template = ARCFACE_128_TEMPLATE
    else:
        template = ARCFACE_112_V1_TEMPLATE
    # Multiplication creates a new array; shared normalized templates must not be scaled in place.
    destination = template * np.asarray(boost_size, dtype=np.float32)
    affine_matrix = cv2.estimateAffinePartial2D(
        target_face.kps.astype(np.float32), destination, method=cv2.RANSAC, ransacReprojThreshold=100
    )[0]
    if affine_matrix is None:
        raise ValueError("Could not estimate canonical face alignment")
    # INTER_AREA is only well-defined for shrinking; per OpenCV's own documented behavior it
    # degrades to near-INTER_NEAREST when the destination is larger than the source content,
    # which is exactly what pixel-boost enlargement does for small/medium faces. Choose the
    # interpolation from the transform's actual scale factor instead of hardcoding a
    # downscale-only flag.
    transform_scale = float(np.sqrt(abs(np.linalg.det(affine_matrix[:, :2]))))
    crop_interpolation = cv2.INTER_AREA if transform_scale <= 1.0 else cv2.INTER_CUBIC
    crop = cv2.warpAffine(canvas, affine_matrix, boost_size, flags=crop_interpolation, borderMode=cv2.BORDER_REPLICATE)

    outputs = []
    model_masks = []
    for tile in implode_pixel_boost(crop, boost_total, swapper.spec.input_size):
        inferred = swapper.infer_aligned(tile, source_embedding)
        if isinstance(inferred, tuple):
            outputs.append(inferred[0])
            model_masks.append(np.asarray(inferred[1], dtype=np.float32))
        else:
            outputs.append(inferred)
    swapped_crop = explode_pixel_boost(outputs, boost_total, swapper.spec.input_size)

    masks = [create_facefusion_box_mask(boost_size, blur=0.3)]
    components = [mask_component("box", "applied", masks[0])]
    if model_masks:
        # HyperSwap is a native 256px model, so its learned face mask and the aligned
        # crop share one tile. Intersecting it prevents the generic box mask from
        # pasting hallucinated profile pixels outside the model's trusted region.
        model_mask = model_masks[0]
        if model_mask.ndim == 3:
            model_mask = np.squeeze(model_mask)
        masks.append(cv2.resize(model_mask, boost_size, interpolation=cv2.INTER_LINEAR).clip(0.0, 1.0))
        components.append(mask_component("model", "applied", masks[-1]))
    else:
        components.append(mask_component("model", "disabled"))
    if enable_dense_mask and parser is not None:
        try:
            landmarks = parser.extract_106_landmarks(canvas, target_face)
            aligned = cv2.transform(landmarks.reshape(-1, 1, 2), affine_matrix).reshape(-1, 2)
            aligned_512 = aligned * np.asarray([512 / boost_size[0], 512 / boost_size[1]], dtype=np.float32)
            anatomical = parser.generate_anatomical_mask_512(aligned_512)
            masks.append(
                validated_mask(cv2.resize(anatomical, boost_size, interpolation=cv2.INTER_LINEAR), crop.shape[:2])
            )
            components.append(mask_component("dense", "applied", masks[-1]))
        except Exception:
            components.append(mask_component("dense", "failed", failure_code="dense_mask_failed"))
            # Failed landmarks must not remove anatomical protection. Build the
            # default ellipse in canonical 512px space, then resize like a measured mask.
            try:
                anatomical = parser.generate_anatomical_mask_512(None)
                masks.append(
                    validated_mask(cv2.resize(anatomical, boost_size, interpolation=cv2.INTER_LINEAR), crop.shape[:2])
                )
                components.append(mask_component("dense_fallback", "applied", masks[-1]))
            except Exception:
                components.append(mask_component("dense_fallback", "failed", failure_code="dense_fallback_failed"))
    else:
        components.append(
            mask_component(
                "dense",
                "missing" if enable_dense_mask else "disabled",
                failure_code="dense_model_unavailable" if enable_dense_mask else None,
            )
        )
    if occluder is not None:
        try:
            masks.append(validated_mask(occluder.create_occlusion_mask(crop), crop.shape[:2]))
            components.append(mask_component("occlusion", "applied", masks[-1]))
        except Exception:
            components.append(mask_component("occlusion", "failed", failure_code="occlusion_mask_failed"))
    else:
        components.append(mask_component("occlusion", "missing", failure_code="occlusion_model_unavailable"))
    if region_parser is not None:
        try:
            # XSeg only catches foreign objects (hands, props); it does not know "these
            # pixels are eyeglasses" as a semantic class, so glasses routinely warp/melt
            # during a swap with no protection at all. BiSeNet semantic parsing closes that
            # gap by explicitly protecting the glasses region.
            masks.append(
                validated_mask(
                    region_parser.create_region_protection_mask(crop, protected_regions=("glasses",)),
                    crop.shape[:2],
                )
            )
            components.append(mask_component("glasses", "applied", masks[-1]))
        except Exception:
            components.append(mask_component("glasses", "failed", failure_code="glasses_mask_failed"))
    else:
        components.append(mask_component("glasses", "missing", failure_code="glasses_model_unavailable"))
    crop_mask = np.minimum.reduce(masks).clip(0.0, 1.0)

    # Masked post-swap restoration: both swap models cap their working crop at
    # 256x256 (see boost_total above), which is measurably softer than the
    # surrounding pixels once warped back into a high-megapixel photo. CodeFormer
    # restores that detail, but its output is only ever blended in through
    # crop_mask -- the same mask gating the swap itself -- so it can never touch
    # background, occluded, or region-protected pixels, and restoration_strength
    # keeps it a dial rather than a full replacement of the swap output.
    if enable_face_restoration and restorer is not None:
        try:
            restored = restorer.restore(swapped_crop, fidelity_weight=restoration_fidelity_weight)
            strength = float(np.clip(restoration_strength, 0.0, 1.0))
            restoration_alpha = (crop_mask * strength)[:, :, np.newaxis]
            swapped_crop = np.clip(
                restoration_alpha * restored.astype(np.float32)
                + (1.0 - restoration_alpha) * swapped_crop.astype(np.float32),
                0,
                255,
            ).astype(np.uint8)
        except Exception as exc:
            print(f"[Warning] Face restoration failed; using unrestored swap: {exc}")

    # Boundary-ring color correction: only the narrow transition ring at the mask edge is
    # nudged toward the original crop's LAB statistics; the swapped face's own interior tone
    # is never touched. This targets seam/exposure-mismatch discoloration between the source
    # identity photo and the target scene without risking the whole-face skin-tone/identity
    # drift a full-face color transfer would cause -- keeping overall color and saturation
    # intact is a hard requirement here, not just a nice-to-have.
    if enable_color_transfer and color_transfer_mode != "off":
        try:
            # Keep color transfer confined to a narrow, low-opacity edge ring by
            # default. A wider/high-opacity ring made the transition darker and
            # less saturated than both the generated face and target skin in real
            # 8-25 regressions, so color_transfer_ring_strength/_width_px default
            # to that same safe 0.45 / 15px baseline; callers must opt into a
            # wider or stronger ring explicitly, and widening it revives that
            # known darkening/desaturation risk on the caller's own images.
            ring_strength = float(np.clip(color_transfer_ring_strength, 0.0, 1.0))
            harmonized_crop = reinhard_color_transfer(
                swapped_crop, crop, blend_ratio=ring_strength, face_mask=crop_mask
            )
            erosion_size = max(3, round(boost_size[0] * color_transfer_ring_width_px / 512))
            inner_mask = cv2.erode(crop_mask, np.ones((erosion_size, erosion_size), dtype=np.uint8))
            ring_mask = np.clip(crop_mask - inner_mask, 0.0, 1.0)
            ring_sigma = max(1.0, boost_size[0] * 2.5 / 512.0)
            ring_mask = cv2.GaussianBlur(ring_mask, (0, 0), ring_sigma)
            ring_mask = np.minimum(ring_mask, ring_strength)[:, :, np.newaxis]
            swapped_crop = np.clip(
                ring_mask * harmonized_crop.astype(np.float32) + (1.0 - ring_mask) * swapped_crop.astype(np.float32),
                0,
                255,
            ).astype(np.uint8)
        except Exception as exc:
            print(f"[Warning] Boundary color correction failed; using unmodified swap: {exc}")

    inverse = cv2.invertAffineTransform(affine_matrix)
    height, width = canvas.shape[:2]
    warped = warp_face_region(swapped_crop, crop_mask, inverse, canvas.shape)
    if warped is None:
        return canvas.copy()
    region, warped_face, warped_mask = warped
    left, top, right, bottom = region
    # InSwapper needs enough alpha to make the requested identity visible. Faces at
    # or above the 96px pixel-boost threshold have a 256px working crop and can safely
    # use a stronger composite. Only genuinely tiny native-resolution faces retain a
    # more conservative blend; the former blanket 0.18 alpha for every face below
    # 320px made ordinary group portraits effectively no-op.
    if swapper.spec.name == "inswapper_128":
        yaw = abs(calculate_yaw_ratio(target_face.kps))
        blend_strength = float(np.clip(identity_blend_strength, 0.0, 1.0))
        if face_size < 96:
            blend_strength *= 0.45 / 0.72
        elif yaw > 0.55:
            blend_strength *= 0.65 / 0.72
        warped_mask *= min(blend_strength, 1.0)
    if manual_mask is not None:
        protection = manual_mask
        if protection.shape[:2] != (height, width):
            protection = cv2.resize(protection, (width, height), interpolation=cv2.INTER_LINEAR)
        if protection.ndim == 3:
            protection = protection[:, :, 0]
        protection = protection.astype(np.float32)
        if protection.max() > 1.0:
            protection /= 255.0
        manual_keep = 1.0 - protection.clip(0.0, 1.0)
        warped_mask *= manual_keep[top:bottom, left:right]
        components.append(mask_component("manual", "applied", manual_keep))
    else:
        components.append(mask_component("manual", "disabled"))
    if diagnostics is not None:
        diagnostics.update(
            components=components,
            intersection_mean=float(crop_mask.mean()),
            bbox_alpha_mean=region_bbox_mean(warped_mask, region, target_face.bbox, canvas.shape),
            requested_strength=float(identity_blend_strength),
        )
    result = canvas.copy()
    result[top:bottom, left:right] = composite_in_rows(canvas[top:bottom, left:right], warped_face, warped_mask)
    return result
