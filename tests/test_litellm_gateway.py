"""Offline native Router integration, image safety and credential isolation regressions."""

import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
import yaml
from PIL import Image

from core.ai.gateway import AIGateway

_runner: asyncio.Runner


@pytest.fixture(scope="module", autouse=True)
def sdk_event_loop():
    """Keep the SDK callback worker on one loop, as in the real ASGI application."""
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    global _runner
    with asyncio.Runner() as _runner:
        yield
        _runner.run(GLOBAL_LOGGING_WORKER.clear_queue())
        _runner.run(GLOBAL_LOGGING_WORKER.stop())


def run(coroutine):
    return _runner.run(coroutine)


def png_bytes():
    """Return a real small image so safety validation is exercised, not bypassed."""
    stream = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AI_URL", "https://model.test/v1")
    monkeypatch.setenv("TEST_AI_KEY", "test-only-key")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    config = {
        "model_list": [
            {
                "model_name": alias,
                "litellm_params": {
                    "model": model,
                    "api_base": "os.environ/TEST_AI_URL",
                    "api_key": "os.environ/TEST_AI_KEY",
                    "max_retries": 0,
                },
            }
            for alias, model in [("vision", "openai/gpt-4o"), ("image", "openai/gpt-image-1")]
        ],
        "vision_roles": {"default": "vision", "identity_arbitration": "vision", "refinement": "vision"},
        "image_models": [{"model_name": "image", "display_name": "Image service", "generation": {"size": "auto"}}],
        "default_image_alias": "image",
        "router_settings": {"num_retries": 0, "timeout": 10, "fallbacks": []},
    }
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(config))
    return AIGateway(path)


@pytest.mark.parametrize("kind", ["vision", "image"])
def test_router_initialization_failure_is_local_and_sanitized(gateway, monkeypatch, kind):
    from core.ai.types import ModelConfigurationError

    calls = []

    def unavailable(**kwargs):
        calls.append(1)
        error = RuntimeError("SECRET https://private.provider?key=secret")
        error.status_code = 400
        raise error

    monkeypatch.setattr("core.ai.gateway.litellm.Router", unavailable)
    request = gateway.vision_completion([]) if kind == "vision" else gateway.edit_image([png_bytes()], "Edit")
    with pytest.raises(ModelConfigurationError) as caught:
        run(request)
    assert str(caught.value) == (
        "Local AI routing configuration invalid; check model provider prefixes and deployments"
    )
    assert caught.value.__suppress_context__
    assert gateway._router is None
    assert calls == [1]


def mock_http(monkeypatch, handler):
    """Replace the transport but keep real HTTP client types and LiteLLM transformations."""
    original = httpx.AsyncClient.__init__
    transport = httpx.MockTransport(handler)

    def initialize(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", initialize)


def test_native_router_image_edit_sends_one_image_and_size(gateway, monkeypatch):
    requests = []
    image = png_bytes()

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(image).decode()}],
                "usage": {
                    "total_tokens": 12,
                    "input_tokens": 4,
                    "output_tokens": 8,
                    "input_tokens_details": {"text_tokens": 2, "image_tokens": 2},
                },
            },
            headers={"x-request-id": "upstream-1"},
        )

    mock_http(monkeypatch, handler)
    result = run(gateway.edit_image([image], "repair only the face", parameters={"size": "auto"}))
    assert result.image_bytes == image
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://model.test/v1/images/edits"
    assert request.headers["authorization"] == "Bearer test-only-key"
    assert request.content.count(image) == 1
    assert b"gpt-image-1" in request.content
    assert b"auto" in request.content
    assert b"repair only the face" in request.content
    assert result.usage["total_tokens"] == 12
    assert result.model == "openai/gpt-image-1"
    assert result.request_id == "upstream-1"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_native_router_never_replays_image_to_redirect(gateway, monkeypatch, status):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://untrusted.test/collect"})

    mock_http(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="Image model request failed"):
        run(gateway.edit_image([png_bytes()], "repair"))
    assert len(requests) == 1
    assert requests[0].url.host == "model.test"


@pytest.mark.parametrize("images", [[], [b"x", b"y"], ["/etc/passwd"], ["http://127.0.0.1/private"], [b"not-image"]])
def test_invalid_input_never_reaches_router(gateway, images):
    with pytest.raises((ValueError, TypeError)):
        run(gateway.edit_image(images, "repair"))
    assert gateway._router is None


def test_unknown_alias_and_connection_override_never_reach_router(gateway):
    with pytest.raises(ValueError, match="Unknown image-edit"):
        run(gateway.edit_image([png_bytes()], "repair", alias="retired"))
    for parameters in [{"api_key": "override"}, {"mask": "forbidden"}, {"image": "extra"}]:
        with pytest.raises(ValueError, match="Only the declared"):
            run(gateway.edit_image([png_bytes()], "repair", parameters=parameters))
    with pytest.raises(ValueError, match="connections"):
        run(gateway.vision_completion([], api_base="https://other.test"))
    with pytest.raises(ValueError, match="Unknown vision"):
        run(gateway.vision_completion([], alias="image"))
    assert gateway._router is None


def test_result_metadata_uses_actual_response_not_requested_model(gateway):
    async def edit(**kwargs):
        assert kwargs["model"] == "image"
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode(), url=None)],
            model="actual-fallback-model",
            usage={"total_tokens": 7},
            _hidden_params={"custom_llm_provider": "native-provider", "additional_headers": {"x-request-id": "req-2"}},
        )

    gateway._router = SimpleNamespace(aimage_edit=edit)
    result = run(gateway.edit_image([png_bytes()], "repair"))
    assert result.model == "actual-fallback-model"
    assert result.provider == "native-provider"
    assert result.request_id == "req-2"
    assert result.usage == {"total_tokens": 7}


@pytest.mark.parametrize(
    "data", [[], [SimpleNamespace(b64_json="!!!", url=None)], [SimpleNamespace(b64_json=None, url=None)]]
)
def test_invalid_response_is_rejected(gateway, data):
    async def edit(**kwargs):
        return SimpleNamespace(data=data)

    gateway._router = SimpleNamespace(aimage_edit=edit)
    with pytest.raises(ValueError):
        run(gateway.edit_image([png_bytes()], "repair"))


def test_returned_private_url_is_rejected(gateway):
    async def edit(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(b64_json=None, url="http://127.0.0.1/private")])

    gateway._router = SimpleNamespace(aimage_edit=edit)
    with pytest.raises(ValueError, match="non-public"):
        run(gateway.edit_image([png_bytes()], "repair"))


def test_public_catalog_exposes_models_not_connection_details(gateway):
    from api.routes.system import list_ai_image_models

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(refinement_service=SimpleNamespace(gateway=gateway)))
    )
    catalog = run(list_ai_image_models(request)).model_dump()
    assert catalog["default_alias"] == "image"
    assert catalog["models"][0]["model"] == "openai/gpt-image-1"
    assert catalog["models"][0]["provider"] == "litellm"
    text = str(catalog)
    assert "test-only-key" not in text
    assert "model.test" not in text
    assert "TEST_AI" not in text


def test_returned_public_url_download_never_carries_model_credentials(gateway, monkeypatch):
    import socket

    requests = []
    image = png_bytes()

    async def edit(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(b64_json=None, url="https://cdn.test/image.png")])

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))])
    mock_http(monkeypatch, handler)
    gateway._router = SimpleNamespace(aimage_edit=edit)
    result = run(gateway.edit_image([image], "repair"))
    assert result.image_bytes == image
    assert len(requests) == 1
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "cdn.test"
    assert "authorization" not in requests[0].headers
    assert "test-only-key" not in str(requests[0].headers)


def test_native_vision_router_uses_alias_and_configured_connection(gateway, monkeypatch):
    import json

    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "created": 1,
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    mock_http(monkeypatch, handler)
    response = run(gateway.vision_completion([{"role": "user", "content": "test"}]))
    assert response.choices[0].message.content == "ok"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://model.test/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "gpt-4o"


@pytest.mark.parametrize("kind", ["vision", "image"])
def test_upstream_error_text_is_not_exposed(gateway, kind):
    class UpstreamError(Exception):
        status_code = 401

    async def fail(**kwargs):
        raise UpstreamError("secret-key https://model.test/private image-data")

    gateway._router = SimpleNamespace(acompletion=fail, aimage_edit=fail)
    call = gateway.vision_completion([]) if kind == "vision" else gateway.edit_image([png_bytes()], "repair")
    with pytest.raises(RuntimeError, match="HTTP 401") as caught:
        run(call)
    assert "secret-key" not in str(caught.value)
    assert "model.test" not in str(caught.value)
    assert "image-data" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_native_fallback_keeps_image_and_reports_actual_model(gateway, monkeypatch):
    from copy import deepcopy

    requests = []
    image = png_bytes()
    backup = deepcopy(gateway._model_list[1])
    backup["model_name"] = "backup"
    backup["litellm_params"]["model"] = "openai/gpt-image-1-mini"
    monkeypatch.setenv("TEST_BACKUP_URL", "https://backup.test/v1")
    backup["litellm_params"]["api_base"] = "os.environ/TEST_BACKUP_URL"
    gateway._model_list.append(backup)
    gateway._router_settings["fallbacks"] = [{"image": ["backup"]}]

    async def handler(request):
        requests.append(request)
        if request.url.host == "model.test":
            return httpx.Response(429, json={"error": {"message": "busy", "type": "rate_limit_error"}})
        return httpx.Response(
            200,
            json={"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}]},
            headers={"x-request-id": "backup-request"},
        )

    mock_http(monkeypatch, handler)
    result = run(gateway.edit_image([image], "repair", parameters={"size": "auto"}))
    assert [r.url.host for r in requests] == ["model.test", "backup.test"]
    assert all(request.content.count(image) == 1 for request in requests)
    assert result.model == "openai/gpt-image-1-mini"
    assert result.request_id == "backup-request"
