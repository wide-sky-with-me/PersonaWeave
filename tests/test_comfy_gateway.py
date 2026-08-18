from pathlib import Path
import httpx
import pytest

from fastapi.testclient import TestClient

from personaweave.comfy_gateway import (
    ComfyGatewaySettings,
    IdempotencyStore,
    create_gateway_app,
)
from personaweave.domain import AnalysisResult, FaceObservation
from personaweave.provider_errors import ProviderExecutionError, provider_error
from personaweave.providers.gateway import InferenceGateway


class FakeEngine:
    def __init__(self):
        self.analysis_calls = 0
        self.face_plan_calls = 0
        self.close_calls = 0

    async def analyze(self, request):
        self.analysis_calls += 1
        return AnalysisResult(
            asset_id=request.asset_id,
            provider="fake-comfy",
            faces=[
                FaceObservation(
                    external_face_id="face-1",
                    bbox={"x": 1, "y": 2, "width": 30, "height": 40},
                    detection_confidence=0.99,
                )
            ],
        )

    async def preview_face_plan(self, **kwargs):
        self.face_plan_calls += 1
        return b"mask-preview", "image/png", {
            "detected_face_count": 2,
            "mask_type": "mediapipe_face_oval",
        }
    async def aclose(self):
        self.close_calls += 1


def make_gateway(tmp_path: Path) -> tuple[TestClient, FakeEngine]:
    settings = ComfyGatewaySettings(
        backend_url="https://comfy.example",
        gateway_api_key="gateway-secret",
        workflow_dir=tmp_path / "workflows",
        database_path=tmp_path / "gateway.sqlite3",
    )
    engine = FakeEngine()
    app = create_gateway_app(
        settings,
        engine=engine,  # type: ignore[arg-type]
        store=IdempotencyStore(settings.database_path),
    )
    return TestClient(app), engine


def analysis_payload(asset_id: str = "asset-1") -> dict:
    return {
        "batch_id": "batch-1",
        "asset_id": asset_id,
        "asset_uri": "data:image/png;base64,aW1hZ2U=",
    }


def test_gateway_requires_authentication_and_idempotency_key(tmp_path: Path) -> None:
    client, _ = make_gateway(tmp_path)

    unauthorized = client.post("/v1/analyze-faces", json=analysis_payload())
    no_key = client.post(
        "/v1/analyze-faces",
        headers={"Authorization": "Bearer gateway-secret"},
        json=analysis_payload(),
    )

    assert unauthorized.status_code == 401
    assert no_key.status_code == 400


def test_gateway_reuses_durable_idempotent_result(tmp_path: Path) -> None:
    client, engine = make_gateway(tmp_path)
    headers = {
        "Authorization": "Bearer gateway-secret",
        "Idempotency-Key": "analyze:batch-1:asset-1",
    }

    first = client.post("/v1/analyze-faces", headers=headers, json=analysis_payload())
    second = client.post("/v1/analyze-faces", headers=headers, json=analysis_payload())
    conflict = client.post(
        "/v1/analyze-faces",
        headers=headers,
        json=analysis_payload("different-asset"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert engine.analysis_calls == 1
    assert conflict.status_code == 409


def test_gateway_health_exposes_no_credentials(tmp_path: Path) -> None:
    client, _ = make_gateway(tmp_path)

    health = client.get("/health")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "backend_configured": True,
        "gateway_auth_required": True,
        "workflows": {
            "analyze": False,
            "analyze_targeted": False,
            "swap": False,
            "validate": False,
        },
    }
    assert "gateway-secret" not in health.text
    assert client.get("/ready").status_code == 503


def test_gateway_face_plan_is_local_and_authenticated(tmp_path: Path) -> None:
    client, engine = make_gateway(tmp_path)

    unauthorized = client.post(
        "/v1/preview-face-plan",
        files={"source": ("photo.jpg", b"image", "image/jpeg")},
    )
    response = client.post(
        "/v1/preview-face-plan",
        headers={"Authorization": "Bearer gateway-secret"},
        files={"source": ("photo.jpg", b"image", "image/jpeg")},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.content == b"mask-preview"
    assert response.headers["content-type"] == "image/png"
    assert engine.face_plan_calls == 1


def test_gateway_returns_canonical_provider_detail_for_execution(tmp_path: Path) -> None:
    class FailingEngine(FakeEngine):
        async def analyze(self, request):
            raise ProviderExecutionError(
                provider_error(
                    "provider_policy_rejected",
                    upstream_status=400,
                    provider_code="OutputImageSensitiveContentDetected.PolicyViolation",
                    request_id="req-123",
                )
            )

    settings = ComfyGatewaySettings(
        backend_url="https://comfy.example",
        gateway_api_key="gateway-secret",
        workflow_dir=tmp_path / "workflows",
        database_path=tmp_path / "gateway.sqlite3",
    )
    app = create_gateway_app(
        settings,
        engine=FailingEngine(),  # type: ignore[arg-type]
        store=IdempotencyStore(settings.database_path),
    )

    response = TestClient(app).post(
        "/v1/analyze-faces",
        headers={
            "Authorization": "Bearer gateway-secret",
            "Idempotency-Key": "analyze:policy",
        },
        json=analysis_payload(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "provider_policy_rejected",
        "message": "图片内容触发服务商安全策略，无法完成生成。",
        "retryable": False,
        "upstream_status": 400,
        "provider_code": "OutputImageSensitiveContentDetected.PolicyViolation",
        "request_id": "req-123",
    }
    assert "prompt" not in response.text
    assert "secret" not in response.text


def test_gateway_preview_returns_same_canonical_provider_detail(tmp_path: Path) -> None:
    class FailingPreviewEngine(FakeEngine):
        async def preview_face_plan(self, **kwargs):
            raise ProviderExecutionError(
                provider_error(
                    "provider_rate_limited",
                    upstream_status=429,
                    retry_after_seconds=12,
                )
            )

    settings = ComfyGatewaySettings(
        backend_url="https://comfy.example",
        gateway_api_key="gateway-secret",
        workflow_dir=tmp_path / "workflows",
        database_path=tmp_path / "gateway.sqlite3",
    )
    app = create_gateway_app(
        settings,
        engine=FailingPreviewEngine(),  # type: ignore[arg-type]
        store=IdempotencyStore(settings.database_path),
    )

    response = TestClient(app).post(
        "/v1/preview-face-plan",
        headers={"Authorization": "Bearer gateway-secret"},
        files={"source": ("photo.jpg", b"image", "image/jpeg")},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "provider_rate_limited"
    assert response.json()["detail"]["retryable"] is True
    assert response.json()["detail"]["retry_after_seconds"] == 12


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("detail", "expected_code"),
    [
        (
            provider_error(
                "provider_policy_rejected",
                upstream_status=400,
                provider_code="OutputImageSensitiveContentDetected.PolicyViolation",
            ).model_dump(mode="json", exclude_none=True),
            "provider_policy_rejected",
        ),
        ("raw provider body prompt=private sk-super-secret", "provider_unavailable"),
    ],
)
async def test_inference_gateway_validates_error_detail(
    detail: object,
    expected_code: str,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"detail": detail},
            request=request,
        )

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = InferenceGateway(
            "https://gateway.example",
            None,
            client=client,
        )

        with pytest.raises(ProviderExecutionError) as captured:
            await gateway._post("/v1/swap-face", {}, "swap:test")

        assert captured.value.error.code == expected_code
        assert "private" not in captured.value.error.model_dump_json()
        assert "sk-super-secret" not in captured.value.error.model_dump_json()


def test_gateway_lifespan_closes_factory_owned_resources_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created_clients = []
    created_assets = []

    class OwnedClient:
        def __init__(self, *args, **kwargs):
            self.close_calls = 0
            created_clients.append(self)

        async def aclose(self):
            self.close_calls += 1

    class OwnedAssets:
        def __init__(self, *args, **kwargs):
            self.close_calls = 0
            created_assets.append(self)

        async def aclose(self):
            self.close_calls += 1

    monkeypatch.setattr("personaweave.comfy_gateway.ComfyUIClient", OwnedClient)
    monkeypatch.setattr("personaweave.comfy_gateway.RemoteAssetLoader", OwnedAssets)
    settings = ComfyGatewaySettings(
        backend_url="https://comfy.example",
        workflow_dir=tmp_path / "workflows",
        database_path=tmp_path / "gateway.sqlite3",
        max_concurrent_transfers=3,
    )
    app = create_gateway_app(settings)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert len(created_clients) == 1
    assert len(created_assets) == 1
    assert created_clients[0].close_calls == 1
    assert created_assets[0].close_calls == 1


def test_gateway_lifespan_never_closes_injected_engine(tmp_path: Path) -> None:
    settings = ComfyGatewaySettings(
        backend_url="https://comfy.example",
        workflow_dir=tmp_path / "workflows",
        database_path=tmp_path / "gateway.sqlite3",
    )
    engine = FakeEngine()
    app = create_gateway_app(
        settings,
        engine=engine,  # type: ignore[arg-type]
        store=IdempotencyStore(settings.database_path),
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert engine.close_calls == 0


@pytest.mark.parametrize("value", [0, 17])
def test_gateway_rejects_transfer_limits_outside_supported_range(value: int) -> None:
    with pytest.raises(ValueError):
        ComfyGatewaySettings(max_concurrent_transfers=value)
