from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from personaweave.comfy import ComfyUIClient, WorkflowOutput


DEFAULT_PROMPT = (
    "Edit only the transparent masked facial region of the adult man at the upper right. "
    "Replace his facial identity with a clearly different fictional adult East Asian man "
    "around 35 years old, with a broader square jaw, thicker straight eyebrows, and a "
    "slightly broader nose. The original black fringe hairstyle and exact hairline are "
    "outside the mask and must remain completely unchanged. Preserve the original thin "
    "round glasses, head angle, gaze, expression, lollipop, ears, neck, skin lighting, "
    "denim clothing, body and pose. Match the new face skin tone and illumination seamlessly "
    "to the original ears and neck at the mask boundary. Do not create hair, a second "
    "hairline, a halo, ghosting, blur, double eyebrows, duplicate glasses, or visible "
    "blending seams. Preserve all other people and the entire scene exactly."
)


def parse_bbox(value: str) -> tuple[int, int, int, int]:
    try:
        x, y, width, height = (int(part.strip()) for part in value.split(","))
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("bbox must be x,y,width,height") from error
    if min(x, y) < 0 or min(width, height) <= 0:
        raise argparse.ArgumentTypeError("bbox coordinates and dimensions are invalid")
    return x, y, width, height


def face_only_mask(size: tuple[int, int], bbox: tuple[int, int, int, int]) -> Image.Image:
    """Exclude hair and most neck while retaining the facial identity region."""
    x, y, width, height = bbox
    normalized_points = (
        (0.28, 0.31),
        (0.50, 0.27),
        (0.72, 0.30),
        (0.86, 0.42),
        (0.89, 0.62),
        (0.80, 0.80),
        (0.65, 0.93),
        (0.49, 0.97),
        (0.32, 0.90),
        (0.18, 0.76),
        (0.11, 0.57),
        (0.15, 0.40),
    )
    points = [
        (x + round(width * px), y + round(height * py))
        for px, py in normalized_points
    ]
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def exact_change_counts(
    source: Image.Image,
    result: Image.Image,
    mask: Image.Image,
) -> dict[str, int | float]:
    difference = ImageChops.difference(source.convert("RGB"), result.convert("RGB"))
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    changed = maximum.point(lambda value: 255 if value else 0)
    inside = ImageChops.multiply(changed, mask)
    outside = ImageChops.multiply(changed, ImageOps.invert(mask))
    inside_pixels = mask.histogram()[255]
    outside_pixels = source.width * source.height - inside_pixels
    inside_changed = inside.histogram()[255]
    outside_changed = outside.histogram()[255]
    return {
        "inside_pixels": inside_pixels,
        "inside_changed_pixels": inside_changed,
        "inside_change_ratio": inside_changed / inside_pixels if inside_pixels else 0.0,
        "outside_pixels": outside_pixels,
        "outside_changed_pixels": outside_changed,
        "outside_change_ratio": outside_changed / outside_pixels if outside_pixels else 0.0,
    }


def inward_feather_mask(mask: Image.Image, radius: float = 28.0) -> Image.Image:
    blurred = mask.filter(ImageFilter.GaussianBlur(radius))
    return blurred.point(lambda value: max(0, min(255, round((value / 255 - 0.5) * 510))))


def comparison_panel(image: Image.Image, label: str) -> Image.Image:
    panel = Image.new("RGB", (720, 900), "white")
    preview = image.copy()
    preview.thumbnail((720, 844), Image.Resampling.LANCZOS)
    panel.paste(preview, ((720 - preview.width) // 2, 56))
    ImageDraw.Draw(panel).text((18, 18), label, fill="black", font=ImageFont.load_default(24))
    return panel


def recover_saved_candidate(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(args.source) as source_image:
        source = ImageOps.exif_transpose(source_image).convert("RGB")
    with Image.open(args.candidate) as candidate_image:
        candidate = ImageOps.exif_transpose(candidate_image).convert("RGB")
    if candidate.size != source.size:
        candidate = candidate.resize(source.size, Image.Resampling.LANCZOS)

    mask = face_only_mask(source.size, args.bbox)
    mask_content = png_bytes(mask)
    mask_path = output_dir / "authorization-mask-face-only.png"
    mask_path.write_bytes(mask_content)
    blend_mask = inward_feather_mask(mask)
    blend_mask_path = output_dir / "blend-alpha-face-only.png"
    blend_mask.save(blend_mask_path, format="PNG")
    result = Image.composite(candidate, source, blend_mask)
    result_path = output_dir / "result-native-masked.png"
    result.save(result_path, format="PNG", optimize=True)

    x, y, width, height = args.bbox
    crop_box = (
        max(0, x - round(width * 0.30)),
        max(0, y - round(height * 0.22)),
        min(source.width, x + round(width * 1.30)),
        min(source.height, y + round(height * 1.18)),
    )
    panels = [
        comparison_panel(source.crop(crop_box), "Original crop"),
        comparison_panel(candidate.crop(crop_box), "Provider-native masked crop"),
        comparison_panel(result.crop(crop_box), "Strict recovered crop"),
    ]
    sheet = Image.new("RGB", (720 * len(panels), 900), "white")
    for index, panel in enumerate(panels):
        sheet.paste(panel, (index * 720, 0))
    sheet.save(output_dir / "comparison-native-masked.jpg", quality=94)

    evidence = exact_change_counts(source, result, mask)
    report = {
        "recovered_from_saved_candidate": True,
        "candidate": str(args.candidate.resolve()),
        "result": str(result_path),
        "mask": str(mask_path),
        "blend_alpha": str(blend_mask_path),
        "mask_sha256": hashlib.sha256(mask_content).hexdigest(),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "pixel_evidence": evidence,
        "automatic_acceptance": False,
        "review_reason": (
            "Provider-native masking preserved the hairstyle boundary, but the requested "
            "identity change was not visually strong enough for automatic acceptance."
        ),
    }
    (output_dir / "native-masked-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


async def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_content = args.source.read_bytes()
    with Image.open(BytesIO(source_content)) as source_image:
        source = ImageOps.exif_transpose(source_image).convert("RGB")
    mask = face_only_mask(source.size, args.bbox)
    mask_content = png_bytes(mask)
    mask_path = output_dir / "authorization-mask-face-only.png"
    mask_path.write_bytes(mask_content)

    client = ComfyUIClient(
        args.comfy_url,
        api_prefix=args.comfy_api_prefix,
        timeout=600.0,
        execution_timeout=900.0,
    )
    try:
        source_name = await client.upload_image(
            source_content,
            "image/jpeg",
            args.source.name,
            "personaweave/native-masked-demo",
        )
        mask_name = await client.upload_image(
            mask_content,
            "image/png",
            mask_path.name,
            "personaweave/native-masked-demo",
        )
        prompt = {
            "1": {"class_type": "LoadImage", "inputs": {"image": source_name}},
            "2": {
                "class_type": "LoadImageMask",
                "inputs": {"image": mask_name, "channel": "red"},
            },
            "7": {
                "class_type": "PersonaWeaveYunwuMaskedImageEdit",
                "inputs": {
                    "image": ["1", 0],
                    "mask": ["2", 0],
                    "prompt": args.prompt,
                    "model": args.model,
                    "api_mode": "multipart_edits",
                    "api_path": args.api_path,
                    "output_size": args.output_size,
                    "response_format": args.response_format,
                },
            },
            "8": {
                "class_type": "PersonaWeaveMaskedOriginalComposite",
                "inputs": {
                    "original": ["1", 0],
                    "candidate": ["7", 0],
                    "mask": ["2", 0],
                    "feather_radius": 28.0,
                },
            },
            "9": {
                "class_type": "PersonaWeaveOutsideMaskPixelGuard",
                "inputs": {
                    "original": ["1", 0],
                    "result": ["8", 0],
                    "mask": ["2", 0],
                    "max_channel_delta": 0,
                },
            },
            "10": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["9", 1]}},
            "11": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["7", 0],
                    "filename_prefix": "PersonaWeave/native-masked/candidate",
                },
            },
            "12": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["8", 0],
                    "filename_prefix": "PersonaWeave/native-masked/result",
                },
            },
        }
        prompt_id, history = await client.execute(prompt)
        guard, _ = await client.read_output(
            history, WorkflowOutput(node_id="10", field="text", kind="json")
        )
        candidate_content, _ = await client.read_output(
            history, WorkflowOutput(node_id="11", field="images", kind="image")
        )
        result_content, _ = await client.read_output(
            history, WorkflowOutput(node_id="12", field="images", kind="image")
        )
    finally:
        await client.aclose()

    candidate_path = output_dir / "candidate-native-masked.png"
    result_path = output_dir / "result-native-masked.png"
    candidate_path.write_bytes(candidate_content)
    result_path.write_bytes(result_content)
    with Image.open(result_path) as result_image:
        result = result_image.convert("RGB")
    report = {
        "prompt_id": prompt_id,
        "model": args.model,
        "mask_sha256": hashlib.sha256(mask_content).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_content).hexdigest(),
        "result_sha256": hashlib.sha256(result_content).hexdigest(),
        "guard": guard,
        "pixel_evidence": exact_change_counts(source, result, mask),
        "candidate": str(candidate_path),
        "result": str(result_path),
        "mask": str(mask_path),
    }
    (output_dir / "native-masked-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a provider-native masked face edit demo.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bbox", type=parse_bbox, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        help="Recover and evaluate an already saved provider candidate without another API call.",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--api-path", default="/images/edits")
    parser.add_argument("--output-size", default="1024x1536")
    parser.add_argument("--response-format", default="")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-api-prefix", default="")
    args = parser.parse_args()
    if args.candidate is not None:
        recover_saved_candidate(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
