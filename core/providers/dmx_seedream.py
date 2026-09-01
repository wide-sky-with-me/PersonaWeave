"""DMX unified Seedream image editing adapter."""

import base64
import binascii

import httpx

from core.image_security import decode_image_bytes, download_image_bytes
from core.providers.dmx_responses import DMXResponsesProvider, ImageEditResult


class DMXSeedreamProvider(DMXResponsesProvider):
    """Call DMX's Seedream Responses schema and normalize the returned image."""

    async def edit(self, model: str, images: list[str], prompt: str, parameters: dict | None = None):
        if not 1 <= len(images) <= 3:
            raise ValueError("DMX Seedream image edit requires 1 to 3 images")
        if not prompt.strip():
            raise ValueError("DMX Seedream image edit requires a prompt")

        options = {
            "watermark": False,
            "output_format": "png",
            "response_format": "b64_json",
            **(parameters or {}),
        }
        payload = {
            "model": model,
            "input": prompt,
            "image": images,
            "parameters": options,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(f"{self.api_base}/responses", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or []
            item = data[0] if data else {}
            encoded = item.get("b64_json")
            if encoded:
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("DMX Seedream edit returned invalid base64 image data") from exc
                decode_image_bytes(image_bytes)
            elif item.get("url"):
                # Keep URL responses compatible with older gateway revisions,
                # while restricting the managed-proxy exception to Volcengine TOS.
                image_bytes = await download_image_bytes(item["url"], client, proxy_host_suffixes=(".volces.com",))
            else:
                raise ValueError("DMX Seedream edit returned no image")
        return ImageEditResult(
            image_bytes=image_bytes,
            model=body.get("model") or model,
            provider="dmx-seedream",
            usage=body.get("usage") or {},
            request_id=body.get("request_id") or response.headers.get("x-request-id"),
        )
