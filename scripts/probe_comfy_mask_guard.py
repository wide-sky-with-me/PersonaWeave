import asyncio
import json
from io import BytesIO

from PIL import Image, ImageDraw

from personaweave.comfy import ComfyUIClient, WorkflowOutput
from personaweave.comfy_gateway import ComfyGatewaySettings


def png(color: str, *, mask: bool = False) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    if mask:
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((8, 8, 23, 23), fill="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def main() -> None:
    settings = ComfyGatewaySettings()
    if not settings.backend_url:
        raise SystemExit("PERSONAWEAVE_COMFY_BACKEND_URL is not configured")
    client = ComfyUIClient(
        settings.backend_url,
        api_prefix=settings.backend_api_prefix,
        api_key=settings.backend_api_key,
        timeout=settings.request_timeout_seconds,
    )
    original = await client.upload_image(png("black"), "image/png", "original.png", "personaweave/probe")
    candidate = await client.upload_image(png("white"), "image/png", "candidate.png", "personaweave/probe")
    mask = await client.upload_image(png("black", mask=True), "image/png", "mask.png", "personaweave/probe")
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": original}},
        "2": {"class_type": "LoadImage", "inputs": {"image": candidate}},
        "3": {"class_type": "LoadImage", "inputs": {"image": mask}},
        "4": {"class_type": "ImageToMask", "inputs": {"image": ["3", 0], "channel": "red"}},
        "5": {
            "class_type": "PersonaWeaveMaskedOriginalComposite",
            "inputs": {"original": ["1", 0], "candidate": ["2", 0], "mask": ["4", 0], "feather_radius": 0.0},
        },
        "6": {
            "class_type": "PersonaWeaveOutsideMaskPixelGuard",
            "inputs": {"original": ["1", 0], "result": ["5", 0], "mask": ["5", 1], "max_channel_delta": 0},
        },
        "7": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["6", 1]}},
    }
    _, history = await client.execute(prompt)
    report, _ = await client.read_output(history, WorkflowOutput(node_id="7", field="text", kind="json"))
    if not report.get("passed") or report.get("outside_mask_changed_pixels") != 0:
        raise RuntimeError(f"outside-mask guard failed: {report}")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
