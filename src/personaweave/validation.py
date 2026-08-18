from PIL import Image, ImageChops, ImageDraw, ImageStat, UnidentifiedImageError

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


def _changed_ratio(difference: Image.Image, mask: Image.Image) -> float:
    difference_values = difference.convert("L").get_flattened_data()
    mask_values = mask.get_flattened_data()
    changed = 0
    measured = 0
    for delta, included in zip(difference_values, mask_values, strict=True):
        if not included:
            continue
        measured += 1
        if delta >= 12:
            changed += 1
    return changed / measured if measured else 0.0


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
    passed = dimensions_match and outside_preserved and protected_preserved and global_tone_preserved
    reasons = []
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
        },
        reasons=reasons,
    )
