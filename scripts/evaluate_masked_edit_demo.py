from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from personaweave.assets import AssetResolver
from personaweave.domain import BoundingBox, ValidationRequest, ValidationSubject
from personaweave.storage import Repository
from personaweave.validation import compare_source_result


def parse_bbox(value: str) -> BoundingBox:
    try:
        x, y, width, height = (int(part.strip()) for part in value.split(","))
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("bbox must be x,y,width,height") from error
    return BoundingBox(x=x, y=y, width=width, height=height)


def face_authorization_mask(size: tuple[int, int], bbox: BoundingBox) -> Image.Image:
    """Build a conservative face-only mask from a detected face box."""
    mask = Image.new("L", size, 0)
    left = bbox.x + round(bbox.width * 0.10)
    top = bbox.y + round(bbox.height * 0.12)
    right = bbox.x + round(bbox.width * 0.90)
    bottom = bbox.y + round(bbox.height * 0.92)
    ImageDraw.Draw(mask).ellipse((left, top, right, bottom), fill=255)
    return mask


def face_blend_mask(
    size: tuple[int, int],
    bbox: BoundingBox,
    authorization_mask: Image.Image,
    feather_radius: float,
) -> Image.Image:
    """Make alpha approach zero before reaching the hard authorization edge."""
    left = bbox.x + round(bbox.width * 0.10)
    top = bbox.y + round(bbox.height * 0.12)
    right = bbox.x + round(bbox.width * 0.90)
    bottom = bbox.y + round(bbox.height * 0.92)
    inset = max(4, round(feather_radius * 2.75))
    if right - left <= inset * 2 or bottom - top <= inset * 2:
        raise ValueError("face bbox is too small for the requested feather radius")
    inner = Image.new("L", size, 0)
    ImageDraw.Draw(inner).ellipse(
        (left + inset, top + inset, right - inset, bottom - inset),
        fill=255,
    )
    softened = inner.filter(ImageFilter.GaussianBlur(feather_radius))
    return ImageChops.multiply(softened, authorization_mask)


def match_face_tone(
    source: Image.Image,
    candidate: Image.Image,
    bbox: BoundingBox,
) -> tuple[Image.Image, dict[str, list[float]]]:
    """Apply a bounded RGB offset estimated from cheek and lower-face samples."""
    sample = Image.new("L", source.size, 0)
    drawing = ImageDraw.Draw(sample)
    for x0, x1 in ((0.20, 0.47), (0.53, 0.80)):
        drawing.ellipse(
            (
                bbox.x + round(bbox.width * x0),
                bbox.y + round(bbox.height * 0.43),
                bbox.x + round(bbox.width * x1),
                bbox.y + round(bbox.height * 0.76),
            ),
            fill=255,
        )
    source_valid = source.convert("L").point(lambda value: 255 if 72 <= value <= 245 else 0)
    candidate_valid = candidate.convert("L").point(
        lambda value: 255 if 72 <= value <= 245 else 0
    )
    sample = ImageChops.multiply(sample, ImageChops.multiply(source_valid, candidate_valid))
    if sample.histogram()[255] < 128:
        return candidate, {"source_rgb": [], "candidate_rgb": [], "applied_offset_rgb": []}

    source_mean = ImageStat.Stat(source, sample).mean
    candidate_mean = ImageStat.Stat(candidate, sample).mean
    offsets = [
        max(-24.0, min(24.0, source_value - candidate_value))
        for source_value, candidate_value in zip(source_mean, candidate_mean, strict=True)
    ]
    corrected_channels = []
    for channel, offset in zip(candidate.split(), offsets, strict=True):
        corrected_channels.append(
            channel.point(
                [max(0, min(255, round(value + offset))) for value in range(256)]
            )
        )
    return Image.merge("RGB", corrected_channels), {
        "source_rgb": [round(value, 3) for value in source_mean],
        "candidate_rgb": [round(value, 3) for value in candidate_mean],
        "applied_offset_rgb": [round(value, 3) for value in offsets],
    }


def masked_composite(
    source: Image.Image,
    candidate: Image.Image,
    authorization_mask: Image.Image,
    blend_mask: Image.Image,
) -> Image.Image:
    blended = Image.composite(candidate, source, blend_mask)
    # Keep this final hard guard even though alpha already approaches zero near
    # the edge. It makes the outside-mask pixel guarantee independent of blur.
    return Image.composite(blended, source, authorization_mask)


def exact_change_counts(
    source: Image.Image,
    result: Image.Image,
    authorization_mask: Image.Image,
) -> dict[str, int | float]:
    difference = ImageChops.difference(source, result).convert("RGB")
    red, green, blue = difference.split()
    maximum_delta = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    changed = maximum_delta.point(lambda value: 255 if value else 0)
    inside = ImageChops.multiply(changed, authorization_mask)
    outside = ImageChops.multiply(changed, ImageOps.invert(authorization_mask))
    inside_pixels = authorization_mask.histogram()[255]
    outside_pixels = source.width * source.height - inside_pixels
    inside_changed = inside.histogram()[255]
    outside_changed = outside.histogram()[255]
    return {
        "inside_mask_pixels": inside_pixels,
        "inside_mask_changed_pixels": inside_changed,
        "inside_mask_change_ratio": inside_changed / inside_pixels if inside_pixels else 0.0,
        "outside_mask_pixels": outside_pixels,
        "outside_mask_changed_pixels": outside_changed,
        "outside_mask_change_ratio": outside_changed / outside_pixels if outside_pixels else 0.0,
    }


def validation_request(
    source_path: Path,
    result_path: Path,
    mask_path: Path,
    bbox: BoundingBox,
    result_asset_id: str,
) -> ValidationRequest:
    mask_bytes = mask_path.read_bytes()
    with Image.open(mask_path) as mask:
        mask_width, mask_height = mask.size
    return ValidationRequest(
        batch_id="quality-demo",
        result_asset_id=result_asset_id,
        source_uri=source_path.resolve().as_uri(),
        result_uri=result_path.resolve().as_uri(),
        expected_face_instance_ids=["P05-demo-face"],
        protected_face_instance_ids=[],
        source_image_code="I04",
        task_instruction="Change only P05 facial identity to the requested fictional adult identity.",
        expected_subjects=[
            ValidationSubject(
                face_instance_id="P05-demo-face",
                source_person_code="P05",
                bbox=bbox,
            )
        ],
        authorization_mask_uri=mask_path.resolve().as_uri(),
        authorization_mask_sha256=hashlib.sha256(mask_bytes).hexdigest(),
        authorization_mask_width=mask_width,
        authorization_mask_height=mask_height,
        preserve_outside_mask=True,
    )


def labeled_panel(image: Image.Image, label: str, panel_size: tuple[int, int]) -> Image.Image:
    panel = Image.new("RGB", panel_size, "white")
    preview = image.copy()
    preview.thumbnail((panel_size[0], panel_size[1] - 56), Image.Resampling.LANCZOS)
    panel.paste(preview, ((panel.width - preview.width) // 2, 56))
    ImageDraw.Draw(panel).text((18, 18), label, fill="black", font=ImageFont.load_default(24))
    return panel


def contact_sheet(images: list[tuple[str, Image.Image]], panel_size: tuple[int, int]) -> Image.Image:
    panels = [labeled_panel(image, label, panel_size) for label, image in images]
    sheet = Image.new("RGB", (panel_size[0] * len(panels), panel_size[1]), "white")
    for index, panel in enumerate(panels):
        sheet.paste(panel, (index * panel_size[0], 0))
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one real masked identity-edit demo.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--bbox", type=parse_bbox, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feather-radius", type=float, default=28.0)
    parser.add_argument("--generation-seconds", type=float)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_copy = output_dir / "source.jpg"
    candidate_copy = output_dir / "candidate-full.png"
    if args.source.resolve() != source_copy:
        shutil.copyfile(args.source, source_copy)
    if args.candidate.resolve() != candidate_copy:
        shutil.copyfile(args.candidate, candidate_copy)

    with Image.open(source_copy) as source_image, Image.open(candidate_copy) as candidate_image:
        source = ImageOps.exif_transpose(source_image).convert("RGB")
        candidate = ImageOps.exif_transpose(candidate_image).convert("RGB")
    if source.size != candidate.size:
        raise ValueError(f"candidate dimensions {candidate.size} do not match source {source.size}")

    mask = face_authorization_mask(source.size, args.bbox)
    mask_path = output_dir / "authorization-mask.png"
    mask.save(mask_path, format="PNG")
    legacy_blend_mask = mask.filter(ImageFilter.GaussianBlur(args.feather_radius))
    legacy_strict = masked_composite(source, candidate, mask, legacy_blend_mask)
    legacy_strict_path = output_dir / "result-strict-v1.png"
    legacy_strict.save(legacy_strict_path, format="PNG", optimize=True)
    blend_mask = face_blend_mask(source.size, args.bbox, mask, args.feather_radius)
    blend_mask.save(output_dir / "blend-alpha.png", format="PNG")
    tone_matched_candidate, tone_evidence = match_face_tone(source, candidate, args.bbox)
    strict = masked_composite(source, tone_matched_candidate, mask, blend_mask)
    strict_path = output_dir / "result-strict.png"
    strict.save(strict_path, format="PNG", optimize=True)

    repository = Repository(output_dir / "validation.sqlite3")
    repository.initialize()
    assets = AssetResolver(output_dir)
    native_validation = compare_source_result(
        validation_request(source_copy, candidate_copy, mask_path, args.bbox, "native-preview"),
        assets,
        repository,
    )
    strict_validation = compare_source_result(
        validation_request(source_copy, strict_path, mask_path, args.bbox, "strict-result"),
        assets,
        repository,
    )

    crop_box = (
        max(0, args.bbox.x - round(args.bbox.width * 0.35)),
        max(0, args.bbox.y - round(args.bbox.height * 0.25)),
        min(source.width, args.bbox.x + round(args.bbox.width * 1.35)),
        min(source.height, args.bbox.y + round(args.bbox.height * 1.20)),
    )
    contact_sheet(
        [("Original", source), ("Model-native preview", candidate), ("Strict masked result", strict)],
        (720, 1080),
    ).save(output_dir / "comparison-full.jpg", quality=92)
    contact_sheet(
        [
            ("Original crop", source.crop(crop_box)),
            ("Model-native crop", candidate.crop(crop_box)),
            ("Strict masked crop", strict.crop(crop_box)),
        ],
        (720, 900),
    ).save(output_dir / "comparison-face.jpg", quality=94)

    contact_sheet(
        [
            ("Original crop", source.crop(crop_box)),
            ("Model-native crop", candidate.crop(crop_box)),
            ("Strict v1 crop", legacy_strict.crop(crop_box)),
            ("Strict v2 crop", strict.crop(crop_box)),
        ],
        (650, 820),
    ).save(output_dir / "comparison-blending.jpg", quality=94)

    report = {
        "source": str(source_copy),
        "candidate": str(candidate_copy),
        "strict_result": str(strict_path),
        "authorization_mask": str(mask_path),
        "canvas": {"width": source.width, "height": source.height},
        "bbox": args.bbox.model_dump(),
        "generation_seconds": args.generation_seconds,
        "tone_matching": tone_evidence,
        "native_preview": {
            "validation": native_validation.model_dump(mode="json"),
            "pixel_evidence": exact_change_counts(source, candidate, mask),
        },
        "strict_result_evidence": {
            "validation": strict_validation.model_dump(mode="json"),
            "pixel_evidence": exact_change_counts(source, strict, mask),
        },
        "limitations": [
            "No licensed identity-embedding model was available, so identity similarity is not scored.",
            "The authorization mask is derived from a VLM face box, not dense face landmarks.",
            "Human review is still required for visual naturalness and protected head attributes.",
        ],
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
