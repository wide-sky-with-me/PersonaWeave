"""Protocol tests for DMX Gemini native image editing."""

import asyncio
import base64
import json

import httpx

from core.providers.dmx_gemini import DMXGeminiProvider


def test_gemini_uses_generate_content_and_returns_inline_image(monkeypatch):
    requests = []
    encoded = base64.b64encode(b"jpeg-result").decode("ascii")

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/jpeg", "data": encoded}}]}}],
                "usageMetadata": {"totalTokenCount": 3342},
            },
            headers={"x-request-id": "gemini-request"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DMXGeminiProvider("https://dmx.test/v1beta", "secret")

    result = asyncio.run(
        provider.edit(
            "gemini-3-pro-image",
            ["data:image/png;base64,aW1hZ2U="],
            "repair the face",
            {"aspect_ratio": "3:2", "image_size": "4K"},
        )
    )

    payload = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://dmx.test/v1beta/models/gemini-3-pro-image:generateContent"
    assert requests[0].headers["x-goog-api-key"] == "secret"
    assert payload == {
        "model": "gemini-3-pro-image",
        "contents": [
            {
                "parts": [
                    {"text": "repair the face"},
                    {"inline_data": {"mime_type": "image/png", "data": "aW1hZ2U="}},
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "3:2", "imageSize": "4K"},
        },
    }
    assert result.image_bytes == b"jpeg-result"
    assert result.provider == "dmx-gemini"
    assert result.usage == {"totalTokenCount": 3342}
    assert result.request_id == "gemini-request"


def test_gemini_rejects_non_data_url_images():
    provider = DMXGeminiProvider("https://dmx.test/v1beta", "secret")

    try:
        asyncio.run(provider.edit("gemini-3-pro-image", ["https://cdn.test/input.png"], "repair"))
    except ValueError as exc:
        assert "data URLs" in str(exc)
    else:
        raise AssertionError("Gemini provider must reject unsupported image inputs")
