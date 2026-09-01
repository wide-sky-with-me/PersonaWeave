"""Protocol tests for DMX Responses image-edit providers."""

import asyncio
import base64
import json

import httpx
import pytest

from core import image_security
from core.providers.dmx_responses import DMXInputParametersResponsesProvider, DMXResponsesProvider

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def test_dmx_provider_enforces_documented_three_image_ceiling():
    provider = DMXResponsesProvider("https://dmx.test/v1", "secret")
    with pytest.raises(ValueError, match="1 to 3"):
        asyncio.run(provider.edit("qwen-image-edit-plus", ["a", "b", "c", "d"], "fix"))


def test_responses_parameter_location_matches_model_protocol(monkeypatch):
    payloads = []

    async def handler(request):
        if str(request.url).endswith("/responses"):
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"output": [{"content": [{"type": "image", "text": "https://cdn.test/result.png"}]}]},
                request=request,
            )
        return httpx.Response(200, content=base64.b64decode(PNG_BASE64), headers={"content-type": "image/png"})

    monkeypatch.setattr(image_security, "validate_public_url", lambda url, **kwargs: None)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))

    qwen = DMXInputParametersResponsesProvider("https://dmx.test/v1", "secret")
    wan = DMXResponsesProvider("https://dmx.test/v1", "secret")
    asyncio.run(qwen.edit("qwen-image-2.0-pro", ["data:image/png;base64,input"], "fix", {"size": "2K"}))
    asyncio.run(wan.edit("wan2.7-image-pro", ["data:image/png;base64,input"], "fix", {"size": "2K"}))

    assert "parameters" in payloads[0]["input"]
    assert "parameters" not in payloads[0]
    assert payloads[0]["input"]["parameters"]["prompt_extend"] is False
    assert "parameters" in payloads[1]
    assert "parameters" not in payloads[1]["input"]
    assert payloads[1]["parameters"]["enable_sequential"] is False


def test_qwen3_responses_object_result_is_supported(monkeypatch):
    payloads = []

    async def handler(request):
        if str(request.url).endswith("/responses"):
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {"message": {"content": [{"type": "image", "image": "https://cdn.test/qwen3.png"}]}}
                        ]
                    },
                    "request_id": "qwen3-request",
                },
                request=request,
            )
        return httpx.Response(200, content=base64.b64decode(PNG_BASE64), headers={"content-type": "image/png"})

    monkeypatch.setattr(image_security, "validate_public_url", lambda url, **kwargs: None)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))

    result = asyncio.run(
        DMXResponsesProvider("https://dmx.test/v1", "secret").edit(
            "qwen-image-3.0", ["data:image/png;base64,input"], "fix", {"size": "2048*1360"}
        )
    )

    assert payloads[0]["parameters"]["size"] == "2048*1360"
    assert payloads[0]["parameters"]["prompt_extend"] is False
    assert result.image_bytes == base64.b64decode(PNG_BASE64)
    assert result.request_id == "qwen3-request"
