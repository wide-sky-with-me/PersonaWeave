import asyncio
import hashlib
import json
import os
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw

from personaweave.comfy import ComfyUIClient, WorkflowOutput
from personaweave.comfy_gateway import ComfyGatewaySettings


def source_png() -> bytes:
    image = Image.new("RGB", (1024, 1024), "#dcecff")
    drawing = ImageDraw.Draw(image)
    drawing.rounded_rectangle((256, 256, 768, 768), radius=72, fill="#ffffff", outline="#23405f", width=16)
    drawing.text((400, 486), "TEST", fill="#23405f", stroke_width=1)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def authorization_mask_png() -> bytes:
    image = Image.new("RGB", (1024, 1024), "black")
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((352, 352, 672, 672), fill="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def main() -> None:
    settings = ComfyGatewaySettings()
    if not settings.backend_url:
        raise SystemExit("PERSONAWEAVE_COMFY_BACKEND_URL is not configured")
    model = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_MODEL") or "gpt-image-1"
    api_mode = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_API_MODE") or "multipart_edits"
    api_path = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_API_PATH") or "/images/edits"
    output_size = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_SIZE") or "1024x1024"
    response_format = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_RESPONSE_FORMAT") or "url"
    client = ComfyUIClient(
        settings.backend_url,
        api_prefix=settings.backend_api_prefix,
        api_key=settings.backend_api_key,
        timeout=settings.request_timeout_seconds,
        execution_timeout=420.0,
    )
    original = await client.upload_image(
        source_png(), "image/png", "yunwu-contract-source.png", "personaweave/probe"
    )
    mask = await client.upload_image(
        authorization_mask_png(), "image/png", "yunwu-contract-mask.png", "personaweave/probe"
    )
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": original}},
        "2": {"class_type": "LoadImage", "inputs": {"image": mask}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {
            "class_type": "PersonaWeaveYunwuMaskedImageEdit",
            "inputs": {
                "image": ["1", 0],
                "mask": ["3", 0],
                "prompt": (
                    "This is a synthetic API contract test, not a person. "
                    "Inside the editable circular area only, draw one small solid orange five-point star. "
                    "Do not alter anything outside that circle."
                ),
                "model": model,
                "api_mode": api_mode,
                "api_path": api_path,
                "output_size": output_size,
                "response_format": response_format,
            },
        },
        "5": {
            "class_type": "PersonaWeaveMaskedOriginalComposite",
            "inputs": {
                "original": ["1", 0],
                "candidate": ["4", 0],
                "mask": ["3", 0],
                "feather_radius": 4.0,
            },
        },
        "6": {
            "class_type": "PersonaWeaveOutsideMaskPixelGuard",
            "inputs": {
                "original": ["1", 0],
                "result": ["5", 0],
                "mask": ["3", 0],
                "max_channel_delta": 0,
            },
        },
        "7": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["6", 1]}},
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["5", 0], "filename_prefix": "PersonaWeave/probes/yunwu-edit"},
        },
    }
    prompt_id, history = await client.execute(prompt)
    report, _ = await client.read_output(
        history, WorkflowOutput(node_id="7", field="text", kind="json")
    )
    content, media_type = await client.read_output(
        history, WorkflowOutput(node_id="8", field="images", kind="image")
    )
    if report.get("passed") is not True or report.get("outside_mask_changed_pixels") != 0:
        raise RuntimeError(f"outside-mask guard failed: {report}")
    with Image.open(BytesIO(content)) as result:
        result = result.convert("RGB")
        result_size = list(result.size)
        source = Image.open(BytesIO(source_png())).convert("RGB")
        authorization = Image.open(BytesIO(authorization_mask_png())).convert("L")
        difference = ImageChops.difference(source, result)
        inside = Image.new("RGB", source.size)
        inside.paste(difference, mask=authorization)
        changed_histogram = inside.convert("L").point(lambda value: 255 if value else 0).histogram()
        inside_changed_pixels = changed_histogram[255]
    if inside_changed_pixels == 0:
        raise RuntimeError("provider returned no changed pixels inside the authorization mask")
    summary = {
        "passed": True,
        "prompt_id": prompt_id,
        "model": model,
        "media_type": media_type,
        "result_size": result_size,
        "result_sha256": hashlib.sha256(content).hexdigest(),
        "outside_mask_changed_pixels": report["outside_mask_changed_pixels"],
        "outside_mask_checked_pixels": report["outside_mask_checked_pixels"],
        "inside_mask_changed_pixels": inside_changed_pixels,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
