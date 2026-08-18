import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from personaweave.comfy import (
    ComfyGatewayError,
    ComfyUIClient,
    ComfyWorkflowEngine,
    RemoteAssetLoader,
    UnsafeRemoteAsset,
    WorkflowNotConfigured,
    WorkflowRegistry,
)
from personaweave.domain import (
    AnalysisRequest,
    AnalysisResult,
    SwapRequest,
    SwapResult,
    ValidationRequest,
    ValidationResult,
)
from personaweave.provider_errors import (
    ProviderExecutionError,
    provider_error_http_status,
)


class ComfyGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PERSONAWEAVE_COMFY_",
        extra="ignore",
    )

    backend_url: str | None = None
    backend_api_prefix: str = "/api"
    backend_api_key: str | None = Field(default=None, repr=False)
    gateway_api_key: str | None = Field(default=None, repr=False)
    workflow_dir: Path = Path("./comfy-workflows")
    database_path: Path = Path("./data/comfy-gateway.sqlite3")
    allowed_asset_hosts: str = ""
    request_timeout_seconds: float = Field(default=600.0, ge=600.0)
    execution_timeout_seconds: float = Field(default=600.0, gt=0)
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    max_asset_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    max_concurrent_transfers: int = Field(default=4, ge=1, le=16)

    @property
    def asset_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.allowed_asset_hosts.split(",")
            if host.strip()
        }


class IdempotencyConflict(ValueError):
    pass


T = TypeVar("T")


class IdempotencyStore:
    def __init__(self, path: Path):
        self.path = path
        self._locks: dict[str, asyncio.Lock] = {}

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_results (
                    idempotency_key TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get(self, key: str, digest: str) -> dict | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT request_sha256, response_json FROM gateway_results WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise IdempotencyConflict("idempotency key was reused with a different request")
        return json.loads(row[1])

    def put(self, key: str, digest: str, response: dict) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO gateway_results "
                "(idempotency_key, request_sha256, response_json) VALUES (?, ?, ?)",
                (key, digest, json.dumps(response, ensure_ascii=False)),
            )

    async def run(
        self,
        key: str,
        request: dict,
        operation: Callable[[], Awaitable[T]],
    ) -> T | dict:
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self.get(key, digest)
            if cached is not None:
                return cached
            result = await operation()
            serialized = result.model_dump(mode="json")  # type: ignore[union-attr]
            self.put(key, digest, serialized)
            return result


def create_gateway_app(
    settings: ComfyGatewaySettings | None = None,
    *,
    engine: ComfyWorkflowEngine | None = None,
    store: IdempotencyStore | None = None,
) -> FastAPI:
    settings = settings or ComfyGatewaySettings()
    registry = WorkflowRegistry(settings.workflow_dir)
    owned_engine = engine is None and bool(settings.backend_url)
    if owned_engine:
        client = ComfyUIClient(
            settings.backend_url,  # type: ignore[arg-type]
            api_prefix=settings.backend_api_prefix,
            api_key=settings.backend_api_key,
            timeout=settings.request_timeout_seconds,
            poll_interval=settings.poll_interval_seconds,
            execution_timeout=settings.execution_timeout_seconds,
        )
        assets = RemoteAssetLoader(
            allowed_hosts=settings.asset_hosts,
            max_bytes=settings.max_asset_bytes,
            timeout=settings.request_timeout_seconds,
        )
        engine = ComfyWorkflowEngine(
            registry,
            client,
            assets,
            max_concurrent_transfers=settings.max_concurrent_transfers,
        )
    engine_to_close = engine if owned_engine else None
    store = store or IdempotencyStore(settings.database_path)
    store.initialize()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if engine_to_close is not None:
                await engine_to_close.aclose()

    app = FastAPI(
        title="PersonaWeave ComfyUI Gateway",
        version="0.1.0",
        description="Face-workflow adapter between PersonaWeave and hosted ComfyUI.",
        lifespan=lifespan,
    )

    def authorize(authorization: str | None) -> None:
        if not settings.gateway_api_key:
            return
        expected = f"Bearer {settings.gateway_api_key}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "invalid gateway credentials")

    async def execute(
        idempotency_key: str | None,
        authorization: str | None,
        request: AnalysisRequest | SwapRequest | ValidationRequest,
        operation: Callable[[], Awaitable],
    ):
        authorize(authorization)
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header is required")
        if engine is None:
            raise HTTPException(503, "ComfyUI backend is not configured")
        try:
            return await store.run(
                idempotency_key,
                request.model_dump(mode="json"),
                operation,
            )
        except ProviderExecutionError as error:
            raise HTTPException(
                provider_error_http_status(error.error),
                detail=error.error.model_dump(mode="json", exclude_none=True),
            ) from error
        except IdempotencyConflict as error:
            raise HTTPException(409, str(error)) from error
        except WorkflowNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except UnsafeRemoteAsset as error:
            raise HTTPException(400, str(error)) from error
        except (httpx.HTTPError, ComfyGatewayError, TimeoutError) as error:
            raise HTTPException(502, f"ComfyUI execution failed: {error}") from error

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "backend_configured": bool(settings.backend_url and engine),
            "gateway_auth_required": bool(settings.gateway_api_key),
            "workflows": registry.available(),
        }

    @app.get("/ready")
    async def ready() -> dict:
        workflows = registry.available()
        if not settings.backend_url or engine is None or not workflows.get("analyze"):
            raise HTTPException(503, "backend and the analysis workflow must be configured")
        try:
            return await engine.check_ready()
        except (httpx.HTTPError, ComfyGatewayError, TimeoutError) as error:
            raise HTTPException(503, f"ComfyUI backend is not ready: {error}") from error

    @app.post("/v1/analyze-faces", response_model=AnalysisResult)
    async def analyze(
        request: AnalysisRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None),
    ):
        return await execute(
            idempotency_key,
            authorization,
            request,
            lambda: engine.analyze(request),  # type: ignore[union-attr]
        )

    @app.post("/v1/swap-face", response_model=SwapResult)
    async def swap(
        request: SwapRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None),
    ):
        return await execute(
            idempotency_key,
            authorization,
            request,
            lambda: engine.swap(request),  # type: ignore[union-attr]
        )

    @app.post("/v1/validate-result", response_model=ValidationResult)
    async def validate(
        request: ValidationRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None),
    ):
        return await execute(
            idempotency_key,
            authorization,
            request,
            lambda: engine.validate(request),  # type: ignore[union-attr]
        )

    @app.post("/v1/preview-replacement")
    async def preview_replacement(
        source: UploadFile = File(),
        description: str = Form(min_length=1, max_length=2000),
        reference: UploadFile | None = File(default=None),
        model: str = Form(default="gpt-image-1"),
        api_mode: str = Form(default="multipart_edits"),
        api_path: str = Form(default="/images/edits"),
        output_size: str = Form(default="1024x1024"),
        response_format: str = Form(default="url"),
        strategy: str = Form(default="model_native"),
        authorization: str | None = Header(default=None),
    ):
        authorize(authorization)
        if engine is None:
            raise HTTPException(503, "ComfyUI backend is not configured")

        async def read_upload(upload: UploadFile) -> bytes:
            content = await upload.read(settings.max_asset_bytes + 1)
            if len(content) > settings.max_asset_bytes:
                raise HTTPException(413, "preview image exceeds configured size limit")
            if not content:
                raise HTTPException(400, "preview image is empty")
            return content

        source_content = await read_upload(source)
        reference_content = await read_upload(reference) if reference else None
        try:
            image, media_type, report = await engine.preview_replace_all(
                source_content=source_content,
                source_media_type=source.content_type or "application/octet-stream",
                source_filename=source.filename or "source.png",
                description=description,
                reference_content=reference_content,
                reference_media_type=reference.content_type if reference else None,
                reference_filename=reference.filename if reference else None,
                model=model,
                api_mode=api_mode,
                api_path=api_path,
                output_size=output_size,
                response_format=response_format,
                strategy=strategy,
            )
        except ProviderExecutionError as error:
            raise HTTPException(
                provider_error_http_status(error.error),
                detail=error.error.model_dump(mode="json", exclude_none=True),
            ) from error
        except (httpx.HTTPError, ComfyGatewayError, TimeoutError, ValueError) as error:
            raise HTTPException(502, f"preview execution failed: {error}") from error
        encoded_report = base64.urlsafe_b64encode(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        return Response(
            content=image,
            media_type=media_type,
            headers={
                "X-PersonaWeave-Report": encoded_report,
                "Cache-Control": "no-store",
            },
        )

    @app.post("/v1/preview-face-plan")
    async def preview_face_plan(
        source: UploadFile = File(),
        authorization: str | None = Header(default=None),
    ):
        authorize(authorization)
        if engine is None:
            raise HTTPException(503, "ComfyUI backend is not configured")
        source_content = await source.read(settings.max_asset_bytes + 1)
        if len(source_content) > settings.max_asset_bytes:
            raise HTTPException(413, "preview image exceeds configured size limit")
        if not source_content:
            raise HTTPException(400, "preview image is empty")
        try:
            image, media_type, report = await engine.preview_face_plan(
                source_content=source_content,
                source_media_type=source.content_type or "application/octet-stream",
                source_filename=source.filename or "source.png",
            )
        except ProviderExecutionError as error:
            raise HTTPException(
                provider_error_http_status(error.error),
                detail=error.error.model_dump(mode="json", exclude_none=True),
            ) from error
        except (httpx.HTTPError, ComfyGatewayError, TimeoutError, ValueError) as error:
            raise HTTPException(502, f"face-plan execution failed: {error}") from error
        encoded_report = base64.urlsafe_b64encode(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        return Response(
            content=image,
            media_type=media_type,
            headers={
                "X-PersonaWeave-Report": encoded_report,
                "Cache-Control": "no-store",
            },
        )

    return app


app = create_gateway_app()


def run() -> None:
    uvicorn.run("personaweave.comfy_gateway:app", host="0.0.0.0", port=8010)
