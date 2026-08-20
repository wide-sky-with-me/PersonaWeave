import hashlib

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat, UnidentifiedImageError

from personaweave.assets import AssetResolver
from personaweave.domain import ValidationRequest, ValidationResult
from personaweave.storage import Repository


def _expanded_box(box: dict, width: int, height: int, scale: float = 0.2) -> tuple[int, int, int, int]:
    padding_x = round(box["width"] * scale)
    padding_y = round(box["height"] * scale)
    return (
        max(0, box["x"] - padding_x),
        max(0, box["y"] - padding_y),
        min(width, box["x"] + box["width"] + padding_x),
        min(height, box["y"] + box["height"] + padding_y),
    )


def _changed_ratio(
    difference: Image.Image,
    mask: Image.Image,
    *,
    minimum_delta: int = 12,
    maximum_channel: bool = False,
) -> float:
    normalized_mask = mask.convert("L").point(lambda value: 255 if value else 0)
    measured = normalized_mask.histogram()[255]
    if not measured:
        return 0.0
    if maximum_channel:
        red, green, blue = difference.convert("RGB").split()
        scalar_difference = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    else:
        scalar_difference = difference.convert("L")
    changed_pixels = scalar_difference.point(
        lambda value: 255 if value >= minimum_delta else 0
    )
    measured_changes = ImageChops.multiply(changed_pixels, normalized_mask)
    changed = measured_changes.histogram()[255]
    return changed / measured


def _load_authorization_mask(
    request: ValidationRequest,
    assets: AssetResolver,
    expected_size: tuple[int, int],
) -> tuple[Image.Image | None, list[str], dict[str, bool | float | str]]:
    """Load and authenticate the formal mask before any paid reviewer call."""
    if not request.preserve_outside_mask:
        return None, [], {"authorization_mask.required": False}
    checks: dict[str, bool | float | str] = {
        "authorization_mask.required": True,
        "authorization_mask.available": False,
    }
    reasons: list[str] = []
    if not request.authorization_mask_uri:
        reasons.append("正式结果缺少授权蒙版，无法证明蒙版外像素保持不变")
        return None, reasons, checks
    try:
        path = assets.local_path(request.authorization_mask_uri)
        if path is None:
            raise ValueError("authorization mask is not locally materialized")
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        checks["authorization_mask.sha256_match"] = (
            request.authorization_mask_sha256 is not None
            and actual_sha256 == request.authorization_mask_sha256
        )
        if request.authorization_mask_sha256 != actual_sha256:
            raise ValueError("authorization mask sha256 does not match compiled evidence")
        with Image.open(path) as mask_image:
            mask = mask_image.convert("L")
            if mask.size != expected_size:
                raise ValueError(
                    f"authorization mask dimensions {mask.size} do not match result {expected_size}"
                )
            if (
                request.authorization_mask_width != mask.width
                or request.authorization_mask_height != mask.height
            ):
                raise ValueError("authorization mask dimensions do not match compiled evidence")
            mask = mask.copy()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        reasons.append(f"授权蒙版校验失败：{error}")
        return None, reasons, checks
    checks["authorization_mask.available"] = True
    return mask, reasons, checks


def compare_source_result(
    request: ValidationRequest,
    assets: AssetResolver,
    repository: Repository,
) -> ValidationResult:
    try:
        source_path = assets.local_path(request.source_uri)
        result_path = assets.local_path(request.result_uri)
        if source_path is None or result_path is None:
            raise ValueError("comparison requires local materialized images")
        with Image.open(source_path) as source_image, Image.open(result_path) as result_image:
            source_image.seek(0)
            result_image.seek(0)
            source = source_image.convert("RGB")
            result = result_image.convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError) as error:
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=False,
            provider="local-source-comparison",
            checks={"comparison.available": False},
            reasons=[f"原图像素对比不可用：{error}"],
        )

    dimensions_match = source.size == result.size
    if not dimensions_match:
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=False,
            provider="local-source-comparison",
            checks={
                "comparison.available": True,
                "canvas_dimensions_match": False,
                "source_width": source.width,
                "source_height": source.height,
                "result_width": result.width,
                "result_height": result.height,
            },
            reasons=["结果图片尺寸或比例与原图不一致"],
        )

    original_width, original_height = source.size
    full_difference = ImageChops.difference(source, result)
    authorization_mask, mask_reasons, mask_checks = _load_authorization_mask(
        request,
        assets,
        source.size,
    )
    if authorization_mask is not None:
        outside_authorization = ImageOps.invert(authorization_mask)
        outside_mask_change_ratio = _changed_ratio(
            full_difference,
            outside_authorization,
            minimum_delta=1,
            maximum_channel=True,
        )
        mask_checks["authorization_mask.outside_pixels_unchanged"] = (
            outside_mask_change_ratio == 0.0
        )
        mask_checks["authorization_mask.outside_change_ratio"] = round(
            outside_mask_change_ratio,
            8,
        )
    else:
        outside_mask_change_ratio = None
        if request.preserve_outside_mask:
            mask_checks["authorization_mask.outside_pixels_unchanged"] = False

    editable_mask = Image.new("L", source.size, 0)
    editable_draw = ImageDraw.Draw(editable_mask)
    if authorization_mask is not None:
        editable_mask = authorization_mask
    else:
        for subject in request.expected_subjects:
            bbox = subject.bbox.model_dump()
            editable_draw.rectangle(
                (
                    bbox["x"],
                    bbox["y"],
                    bbox["x"] + bbox["width"] - 1,
                    bbox["y"] + bbox["height"] - 1,
                ),
                fill=255,
            )
    editable_change_ratio = _changed_ratio(full_difference, editable_mask)
    edit_observation_required = bool(request.expected_subjects) or request.preserve_outside_mask
    edit_observed = (
        not edit_observation_required
        or editable_change_ratio >= 0.01
    )
    source.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    result = result.resize(source.size, Image.Resampling.LANCZOS)
    scale_x = source.width / original_width
    scale_y = source.height / original_height

    def scaled_box(box: dict) -> dict:
        return {
            "x": round(box["x"] * scale_x),
            "y": round(box["y"] * scale_y),
            "width": max(1, round(box["width"] * scale_x)),
            "height": max(1, round(box["height"] * scale_y)),
        }

    difference = ImageChops.difference(source, result)
    outside_mask = Image.new("L", source.size, 255)
    outside_draw = ImageDraw.Draw(outside_mask)
    for subject in request.expected_subjects:
        outside_draw.rectangle(_expanded_box(scaled_box(subject.bbox.model_dump()), *source.size), fill=0)
    outside_change_ratio = _changed_ratio(difference, outside_mask)
    source_mean = ImageStat.Stat(source, outside_mask).mean
    result_mean = ImageStat.Stat(result, outside_mask).mean
    tone_deltas = [
        abs(source_value - result_value)
        for source_value, result_value in zip(source_mean, result_mean, strict=True)
    ]
    maximum_tone_delta = max(tone_deltas, default=0.0)

    protected_mask = Image.new("L", source.size, 0)
    protected_draw = ImageDraw.Draw(protected_mask)
    for face_id in request.protected_face_instance_ids:
        try:
            face = repository.get_face(face_id)
        except KeyError:
            continue
        protected_draw.rectangle(_expanded_box(scaled_box(face["bbox"]), *source.size), fill=255)
    protected_change_ratio = _changed_ratio(difference, protected_mask)

    outside_preserved = outside_change_ratio <= 0.03
    protected_preserved = protected_change_ratio <= 0.08
    global_tone_preserved = maximum_tone_delta <= 10.0
    passed = (
        dimensions_match
        and outside_preserved
        and protected_preserved
        and global_tone_preserved
        and edit_observed
        and not mask_reasons
        and (
            not request.preserve_outside_mask
            or mask_checks.get("authorization_mask.outside_pixels_unchanged") is True
        )
    )
    reasons = [*mask_reasons]
    if edit_observation_required and not edit_observed:
        reasons.append("授权区域未检测到足够的实际像素变化，疑似未执行人物编辑")
    if (
        request.preserve_outside_mask
        and outside_mask_change_ratio is not None
        and outside_mask_change_ratio > 0.0
    ):
        reasons.append(
            f"授权蒙版外检测到像素变化：{outside_mask_change_ratio:.8%}"
        )
    if not outside_preserved:
        reasons.append(f"请求区域外变化比例过高：{outside_change_ratio:.2%}")
    if not protected_preserved:
        reasons.append(f"保留人物区域变化比例过高：{protected_change_ratio:.2%}")
    if not global_tone_preserved:
        reasons.append(f"请求区域外存在明显全局色调或曝光偏移：最大通道均值差 {maximum_tone_delta:.1f}/255")
    return ValidationResult(
        result_asset_id=request.result_asset_id,
        passed=passed,
        provider="local-source-comparison",
        checks={
            "comparison.available": True,
            "canvas_dimensions_match": dimensions_match,
            "outside_requested_regions_preserved": outside_preserved,
            "outside_change_ratio": round(outside_change_ratio, 6),
            "protected_people_preserved": protected_preserved,
            "protected_people_change_ratio": round(protected_change_ratio, 6),
            "global_tone_preserved": global_tone_preserved,
            "global_tone_mean_red_delta": round(tone_deltas[0], 3),
            "global_tone_mean_green_delta": round(tone_deltas[1], 3),
            "global_tone_mean_blue_delta": round(tone_deltas[2], 3),
            "editable_region_changed": edit_observed,
            "editable_region_change_required": edit_observation_required,
            "editable_change_ratio": round(editable_change_ratio, 6),
            **mask_checks,
        },
        reasons=reasons,
    )
