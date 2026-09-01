"""DMX OpenAI-compatible multipart image-edit provider."""

import base64
import binascii

import httpx

from core.image_security import download_image_bytes
from core.providers.dmx_responses import ImageEditResult


class DMXImagesEditProvider:
    """Call DMX's ``/images/edits`` endpoint for GPT Image models."""

    def __init__(self, api_base: str, api_key: str, timeout: float = 600):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def _image_file(image: str, index: int) -> tuple[str, bytes, str]:
        if not image.startswith("data:image/") or ";base64," not in image:
            raise ValueError("DMX multipart image edit requires base64 image data URLs")
        header, encoded = image.split(",", 1)
        mime_type = header[5:].split(";", 1)[0]
        extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "png")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("DMX multipart image edit received invalid base64 image data") from exc
        return f"input-{index}.{extension}", image_bytes, mime_type

    async def edit(self, model: str, images: list[str], prompt: str, parameters: dict | None = None):
        if not 1 <= len(images) <= 16:
            raise ValueError("DMX GPT image edit requires 1 to 16 images")
        if not prompt.strip():
            raise ValueError("DMX GPT image edit requires a prompt")

        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "background": "opaque",
            "output_format": "png",
            "quality": "high",
            **(parameters or {}),
        }
        files = [("image", self._image_file(image, index)) for index, image in enumerate(images, start=1)]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                f"{self.api_base}/images/edits",
                headers=headers,
                data=payload,
                files=files,
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or []
            if not data:
                raise ValueError("DMX GPT image edit returned no image data")
            item = data[0]
            if item.get("b64_json"):
                image_bytes = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                image_bytes = await download_image_bytes(item["url"], client)
            else:
                raise ValueError("DMX GPT image edit returned neither base64 data nor an image URL")

        return ImageEditResult(
            image_bytes=image_bytes,
            model=model,
            provider="dmx-images-edit",
            usage=body.get("usage") or {},
            request_id=body.get("request_id") or response.headers.get("x-request-id"),
        )
