"""Protocol tests for DMX Seedream image editing."""

import asyncio

import httpx

from core import image_security
from core.providers.dmx_seedream import DMXSeedreamProvider


def test_seedream_uses_string_input_images_and_parameters(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        if str(request.url).endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "model": "doubao-seedream-5-0-pro-260628",
                    "data": [{"url": "https://cdn.test/result.jpg", "size": "2496x1664"}],
                    "usage": {"total_tokens": 6000},
                },
                request=request,
            )
        return httpx.Response(
            200,
            content=__import__("base64").b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            headers={"content-type": "image/png"},
            request=request,
        )

    monkeypatch.setattr(image_security, "validate_public_url", lambda url, **kwargs: None)
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DMXSeedreamProvider("https://dmx.test/v1", "secret")

    result = asyncio.run(
        provider.edit(
            "doubao-seedream-5-0-pro-260628",
            ["data:image/png;base64,one", "data:image/png;base64,two"],
            "fix hairline",
            {"size": "2048x1368"},
        )
    )

    payload = __import__("json").loads(requests[0].content)
    assert payload == {
        "model": "doubao-seedream-5-0-pro-260628",
        "input": "fix hairline",
        "image": ["data:image/png;base64,one", "data:image/png;base64,two"],
        "parameters": {
            "watermark": False,
            "output_format": "png",
            "response_format": "b64_json",
            "size": "2048x1368",
        },
    }
    assert result.image_bytes.startswith(b"\x89PNG")
    assert result.provider == "dmx-seedream"
    assert result.usage == {"total_tokens": 6000}
