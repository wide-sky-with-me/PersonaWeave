"""Protocol tests for DMX GPT multipart image editing."""

import asyncio
import base64

import httpx

from core.providers.dmx_images_edit import DMXImagesEditProvider


def test_gpt_image_uses_multipart_edit_endpoint(monkeypatch):
    requests = []
    encoded = base64.b64encode(b"png-result").decode("ascii")

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": encoded}], "usage": {"total_tokens": 1234}},
            headers={"x-request-id": "gpt-image-request"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    provider = DMXImagesEditProvider("https://dmx.test/v1", "secret")

    result = asyncio.run(
        provider.edit(
            "gpt-image-2-ssvip",
            ["data:image/png;base64,aW1hZ2U="],
            "repair the face",
            {"size": "3072x2048"},
        )
    )

    request = requests[0]
    assert str(request.url) == "https://dmx.test/v1/images/edits"
    assert request.headers["authorization"] == "Bearer secret"
    assert b'name="model"' in request.content
    assert b"gpt-image-2-ssvip" in request.content
    assert b'name="image"' in request.content
    assert b"3072x2048" in request.content
    assert result.image_bytes == b"png-result"
    assert result.provider == "dmx-images-edit"
    assert result.request_id == "gpt-image-request"
