"""Explicit DMX Responses image-edit provider adapter.

DMX Qwen Image Edit Plus/Max use a vendor-specific Responses envelope that
LiteLLM cannot currently parse. This module owns that protocol explicitly so
business services continue to depend only on AIGateway aliases.
"""

from dataclasses import dataclass, field

import httpx

from core.image_security import download_image_bytes


@dataclass(frozen=True)
class ImageEditResult:
    """Normalized image-edit result returned by every gateway provider."""

    image_bytes: bytes
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    request_id: str | None = None


class DMXResponsesProvider:
    """Call DMX's documented Qwen image-edit Responses protocol."""

    parameters_in_input = False

    def __init__(self, api_base: str, api_key: str, timeout: float = 300):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def edit(self, model: str, images: list[str], prompt: str, parameters: dict | None = None):
        if not 1 <= len(images) <= 3:
            raise ValueError("DMX Responses image edit requires 1 to 3 images")
        if not prompt.strip():
            raise ValueError("DMX Responses image edit requires a prompt")

        content = [{"image": image} for image in images]
        content.append({"text": prompt})
        options = {"n": 1, "watermark": False, **(parameters or {})}
        if model.startswith("qwen"):
            options["prompt_extend"] = False
        elif model.startswith("wan"):
            options["enable_sequential"] = False
        input_data = {"messages": [{"role": "user", "content": content}]}
        payload = {"model": model, "input": input_data}
        if self.parameters_in_input:
            input_data["parameters"] = options
        else:
            payload["parameters"] = options
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(f"{self.api_base}/responses", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            output_items = body.get("output") if isinstance(body.get("output"), list) else []
            image_urls = [
                item.get("text")
                for output in output_items
                for item in output.get("content", [])
                if item.get("type") == "image" and item.get("text")
            ]
            output_object = body.get("output") if isinstance(body.get("output"), dict) else {}
            image_urls.extend(
                item.get("image")
                for choice in output_object.get("choices", [])
                for item in choice.get("message", {}).get("content", [])
                if item.get("type") == "image" and item.get("image")
            )
            if not image_urls:
                raise ValueError("DMX Responses image edit returned no image URL")
            # DMX documents Qwen output as a signed Aliyun OSS URL. The managed
            # runtime resolves public egress through 198.18/15, so only this
            # provider-owned HTTPS suffix may use that proxy mapping.
            image_bytes = await download_image_bytes(image_urls[0], client, proxy_host_suffixes=(".aliyuncs.com",))
        return ImageEditResult(
            image_bytes=image_bytes,
            model=model,
            provider="dmx-responses",
            usage=body.get("usage") or {},
            request_id=body.get("request_id") or response.headers.get("x-request-id"),
        )


class DMXInputParametersResponsesProvider(DMXResponsesProvider):
    """Use the Qwen Image 2.0 envelope whose parameters live inside ``input``."""

    parameters_in_input = True
