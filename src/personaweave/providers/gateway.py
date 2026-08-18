from typing import Any

import httpx
import hashlib

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
    provider_error,
    provider_error_from_detail,
)


class ProviderNotConfigured(RuntimeError):
    pass


class InferenceGateway:
    """Provider-neutral HTTP contract for hosted ComfyUI or another GPU service."""

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        timeout: float = 120.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        if transport is not None and client is not None:
            raise ValueError("transport and client cannot both be provided")
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.timeout = timeout
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(timeout=timeout, transport=transport)
        )
        self._closed = False

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        headers = {
            "Idempotency-Key": idempotency_key,
            "X-PersonaWeave-Workflow-Version": "1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise ProviderNotConfigured("remote inference gateway is not configured")
        try:
            response = await self._client.post(
                f"{self.base_url}{path}",
                headers=self._headers(idempotency_key),
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ProviderExecutionError(provider_error("provider_timeout")) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(provider_error("provider_unavailable")) from error
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            try:
                body = response.json()
            except ValueError:
                body = None
            detail = body.get("detail") if isinstance(body, dict) else None
            raise ProviderExecutionError(provider_error_from_detail(detail)) from error
        return response.json()

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        request_digest = hashlib.sha256(
            request.model_dump_json(
                exclude={"asset_uri"},
                exclude_computed_fields=True,
            ).encode()
        ).hexdigest()[:12]
        data = await self._post(
            "/v1/analyze-faces",
            request.model_dump(mode="json", exclude_computed_fields=True),
            f"analyze:{request.batch_id}:{request.asset_id}:{request_digest}",
        )
        return AnalysisResult.model_validate(data)

    async def swap(self, request: SwapRequest) -> SwapResult:
        request_digest = hashlib.sha256(
            request.model_dump_json(
                exclude={"asset_uri"},
                exclude_computed_fields=True,
            ).encode()
        ).hexdigest()[:12]
        data = await self._post(
            "/v1/swap-face",
            request.model_dump(mode="json", exclude_computed_fields=True),
            f"swap:{request.batch_id}:{request.asset_id}:{request.iteration}:{request_digest}",
        )
        return SwapResult.model_validate(data)

    async def validate(self, request: ValidationRequest) -> ValidationResult:
        data = await self._post(
            "/v1/validate-result",
            request.model_dump(mode="json", exclude_computed_fields=True),
            f"validate:{request.batch_id}:{request.result_asset_id}",
        )
        return ValidationResult.model_validate(data)
