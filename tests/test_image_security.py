"""Security regression tests for external image inputs and callbacks."""

import asyncio
import base64
import socket

import cv2
import httpx
import numpy as np
import pytest

from api.schemas import BatchTaskCreateRequest, ImageSwapResult, TaskDetailData, TaskStatus
from api.task_manager import TaskManager, TrustedImagePath, decode_manual_mask, load_image_from_source
from core.image_security import decode_data_uri, resolve_managed_url, validate_public_url


def _png_data_uri() -> str:
    ok, encoded = cv2.imencode(".png", np.zeros((2, 3, 3), dtype=np.uint8))
    assert ok
    return "data:image/png;base64," + base64.b64encode(encoded).decode()


def test_external_loader_rejects_arbitrary_local_path(tmp_path):
    path = tmp_path / "secret.png"
    path.write_bytes(base64.b64decode(_png_data_uri().split(",", 1)[1]))
    with pytest.raises(ValueError, match="Local filesystem"):
        load_image_from_source(str(path))
    assert load_image_from_source(TrustedImagePath(path)).shape[:2] == (2, 3)


def test_data_uri_is_strict_and_decodes_image():
    assert decode_data_uri(_png_data_uri()).shape[:2] == (2, 3)
    with pytest.raises(ValueError, match="base64"):
        decode_data_uri("data:image/png;base64,not-valid!")
    with pytest.raises(ValueError, match="Only base64"):
        decode_data_uri("data:text/plain;base64,SGVsbG8=")


def test_managed_url_cannot_traverse_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "outside.png").write_bytes(b"x")
    with pytest.raises(ValueError, match="valid file"):
        resolve_managed_url("/api/v1/data/../outside.png")


def test_public_url_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 80))])
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("http://example.test/image.png")


def test_public_url_allows_managed_proxy_only_for_explicit_https_suffix(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("198.18.0.42", 443))])

    validate_public_url("https://result.oss.aliyuncs.com/image.png", proxy_host_suffixes=(".aliyuncs.com",))
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("https://attacker.test/image.png", proxy_host_suffixes=(".aliyuncs.com",))
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("http://result.oss.aliyuncs.com/image.png", proxy_host_suffixes=(".aliyuncs.com",))


def test_callback_validation_rejects_loopback():
    with pytest.raises(ValueError, match="non-public"):
        BatchTaskCreateRequest(
            batch_images=[{"client_target_id": "target-1", "image_url": _png_data_uri()}],
            callback_url="http://127.0.0.1/hook",
        )


def test_public_result_schema_has_no_server_file_path():
    assert "processed_file_path" not in ImageSwapResult.model_fields


def test_nonempty_invalid_manual_mask_is_rejected():
    with pytest.raises(ValueError, match="manual_mask"):
        decode_manual_mask("not-valid-base64")
    invalid_image = base64.b64encode(b"not an image").decode()
    with pytest.raises(ValueError, match="nonempty image"):
        decode_manual_mask(invalid_image)


def test_webhook_payload_omits_processed_file_path(monkeypatch):
    monkeypatch.setattr("api.task_manager.validate_public_url", lambda url: None)
    captured = {}

    async def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, request=request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    task = TaskDetailData(
        task_id="pw_task_test",
        status=TaskStatus.COMPLETED,
        created_at="now",
        results=[
            {
                "client_target_id": "target-1",
                "original_url": "x",
                "processed_file_path": "/secret/path",
                "processed_url": "/api/v1/files/x",
            }
        ],
    )
    manager = object.__new__(TaskManager)
    asyncio.run(manager._send_webhook("https://hooks.example.test/done", task))
    assert "processed_file_path" not in captured["data"]["results"][0]
