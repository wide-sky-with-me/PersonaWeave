"""DMX Gemini native image-edit provider adapter."""

import base64

import httpx

from core.providers.dmx_responses import ImageEditResult


class DMXGeminiProvider:
    """Call DMX's documented Gemini ``generateContent`` image-edit protocol."""

    def __init__(self, api_base: str, api_key: str, timeout: float = 300):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def _inline_part(image: str) -> dict:
        if not image.startswith("data:image/") or ";base64," not in image:
            raise ValueError("DMX Gemini image inputs must be base64 image data URLs")
        header, encoded = image.split(",", 1)
        mime_type = header[5:].split(";", 1)[0]
        return {"inline_data": {"mime_type": mime_type, "data": encoded}}

    async def edit(self, model: str, images: list[str], prompt: str, parameters: dict | None = None):
        if not images:
            raise ValueError("DMX Gemini image edit requires at least one image")
        if not prompt.strip():
            raise ValueError("DMX Gemini image edit requires a prompt")

        options = parameters or {}
        aspect_ratio = options.get("aspect_ratio", "3:2")
        image_size = options.get("image_size", "4K")
        payload = {
            "model": model,
            "contents": [{"parts": [{"text": prompt}, *[self._inline_part(image) for image in images]]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect_ratio, "imageSize": image_size},
            },
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        endpoint = f"{self.api_base}/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()

        image_bytes = None
        for candidate in body.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    image_bytes = base64.b64decode(inline["data"])
                    break
            if image_bytes is not None:
                break
        if image_bytes is None:
            raise ValueError("DMX Gemini image edit returned no image data")

        return ImageEditResult(
            image_bytes=image_bytes,
            model=model,
            provider="dmx-gemini",
            usage=body.get("usageMetadata") or {},
            request_id=response.headers.get("x-request-id"),
        )
