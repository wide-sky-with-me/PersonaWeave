import asyncio
import base64
import binascii
import copy
import hashlib
import hmac
import json
import mimetypes
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFilter, UnidentifiedImageError
from pydantic import BaseModel, Field

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
    provider_error_from_history,
)


class ComfyGatewayError(RuntimeError):
    pass


class WorkflowNotConfigured(ComfyGatewayError):
    pass


class UnsafeRemoteAsset(ComfyGatewayError):
    pass


class WorkflowOutput(BaseModel):
    node_id: str
    field: str
    kind: Literal["json", "image"]


class WorkflowSpec(BaseModel):
    name: str
    version: int = Field(ge=1)
    prompt: dict[str, Any]
    output: WorkflowOutput
    model: str | None = None


def render_workflow(value: Any, context: dict[str, Any]) -> Any:
    """Replace exact {{name}} values without losing list/dict types."""
    if isinstance(value, dict):
        return {key: render_workflow(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_workflow(item, context) for item in value]
    if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
        key = value[2:-2].strip()
        if key not in context:
            raise WorkflowNotConfigured(f"workflow references unknown input {key!r}")
        return copy.deepcopy(context[key])
    return value


def _contains_node_output(value: Any, node_id: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_node_output(item, node_id) for item in value.values())
    if isinstance(value, list):
        if value == [node_id, 0]:
            return True
        return any(_contains_node_output(item, node_id) for item in value)
    return False


def _configured_formal_graph_is_masked(spec: WorkflowSpec) -> bool:
    required_classes = {"PersonaWeaveYunwuMaskedImageEdit"}
    has_source = False
    has_mask = False
    has_masked_edit = False
    for node in spec.prompt.values():
        class_type = node.get("class_type")
        inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if class_type in required_classes:
            has_masked_edit = True
        if isinstance(inputs, dict):
            for input_value in inputs.values():
                if input_value == "{{source_image}}":
                    has_source = True
                if input_value == "{{authorization_mask}}":
                    has_mask = True
    return has_source and has_mask and has_masked_edit


def validate_formal_source_image(source_content: bytes) -> tuple[int, int]:
    """Validate stored formal-source raster semantics and return its dimensions."""
    try:
        with Image.open(BytesIO(source_content)) as source_image:
            orientation = source_image.getexif().get(274)
            if orientation is not None and orientation != 1:
                raise ComfyGatewayError(
                    "formal source has non-identity EXIF orientation"
                )
            if "A" in source_image.getbands():
                raise ComfyGatewayError("formal source must not have an alpha channel")
            if "transparency" in source_image.info:
                raise ComfyGatewayError("formal source must not contain transparency")
            source_image.load()
            width, height = source_image.size
            if width <= 0 or height <= 0 or width > 32768 or height > 32768:
                raise ComfyGatewayError("formal source dimensions are unsupported")
            return width, height
    except (OSError, UnidentifiedImageError) as error:
        raise ComfyGatewayError("source is not a readable image") from error


def compose_formal_provider_instruction(request: SwapRequest) -> str:
    """Compose runtime guidance without allowing it to replace immutable scope."""
    if not request.adaptive_instruction.strip():
        return request.compiled_instruction
    return (
        f"{request.compiled_instruction}\n\n"
        "SUBORDINATE ADAPTIVE GUIDANCE (runtime-only; it may refine execution but "
        "must not override, omit, or expand the immutable instruction above):\n"
        f"{request.adaptive_instruction}"
    )



    alpha = authorization
    if feather_radius > 0:
        alpha = authorization.filter(ImageFilter.GaussianBlur(feather_radius))
        alpha = ImageChops.multiply(alpha, authorization)
    result = Image.composite(candidate, source, alpha)
    output = BytesIO()
    result.save(output, format="PNG", optimize=True)
    return output.getvalue()


class WorkflowRegistry:
    FILENAMES = {
        "analyze": "analyze.json",
        "analyze_targeted": "analyze_targeted.json",
        "swap": "swap.json",
        "validate": "validate.json",
    }

    def __init__(self, root: Path):
        self.root = root

    def available(self) -> dict[str, bool]:
        return {
            operation: (self.root / filename).is_file()
            for operation, filename in self.FILENAMES.items()
        }

    def load(self, operation: str) -> WorkflowSpec:
        filename = self.FILENAMES.get(operation)
        if not filename:
            raise WorkflowNotConfigured(f"unsupported operation {operation!r}")
        path = self.root / filename
        if not path.is_file():
            raise WorkflowNotConfigured(f"missing ComfyUI workflow: {path}")
        try:
            return WorkflowSpec.model_validate_json(path.read_text())
        except (OSError, ValueError) as error:
            raise WorkflowNotConfigured(f"invalid ComfyUI workflow: {path}") from error


class RemoteAssetLoader:
    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        max_bytes: int,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.allowed_hosts = allowed_hosts
        self.max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def load(self, uri: str) -> tuple[bytes, str, str]:
        if uri.startswith("data:"):
            return self._load_data_uri(uri)
        parsed = urlparse(uri)
        if parsed.scheme != "https" or not parsed.hostname:
            raise UnsafeRemoteAsset("assets must use data: or allow-listed HTTPS URLs")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise UnsafeRemoteAsset(f"asset host {parsed.hostname!r} is not allow-listed")
        async with self._client.stream("GET", uri) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and int(declared) > self.max_bytes:
                raise UnsafeRemoteAsset("remote asset exceeds configured size limit")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self.max_bytes:
                    raise UnsafeRemoteAsset("remote asset exceeds configured size limit")
        media_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        return bytes(content), media_type, Path(parsed.path).name or "asset"

    def _load_data_uri(self, uri: str) -> tuple[bytes, str, str]:
        try:
            header, encoded = uri.split(",", 1)
            if ";base64" not in header:
                raise ValueError
            media_type = header[5:].split(";", 1)[0]
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise UnsafeRemoteAsset("invalid base64 data URI") from error
        if len(content) > self.max_bytes:
            raise UnsafeRemoteAsset("data URI exceeds configured size limit")
        extension = mimetypes.guess_extension(media_type) or ".bin"
        return content, media_type, f"asset{extension}"


class ComfyUIClient:
    _MAX_OUTPUT_REDIRECTS = 5

    def __init__(
        self,
        base_url: str,
        *,
        api_prefix: str = "/api",
        api_key: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 1.0,
        execution_timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        cleaned_prefix = api_prefix.strip("/")
        self.api_prefix = f"/{cleaned_prefix}" if cleaned_prefix else ""
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.execution_timeout = execution_timeout
        self.client_id = f"personaweave-{uuid4()}"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}{path}"

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    @staticmethod
    def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
        scheme = url.scheme.lower()
        port = url.port
        if port is None:
            port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return scheme, url.host.lower(), port

    async def upload_image(
        self,
        content: bytes,
        media_type: str,
        filename: str,
        subfolder: str,
    ) -> str:
        digest = hashlib.sha256(content).hexdigest()
        suffix = mimetypes.guess_extension(media_type) or Path(filename).suffix or ".bin"
        safe_name = f"{digest}{suffix}"
        response = await self._client.post(
            self._url("/upload/image"),
            headers=self._headers(),
            files={"image": (safe_name, content, media_type)},
            data={"type": "input", "subfolder": subfolder, "overwrite": "false"},
        )
        response.raise_for_status()
        result = response.json()
        returned_name = result.get("name")
        returned_folder = result.get("subfolder", "")
        if not isinstance(returned_name, str):
            raise ComfyGatewayError("ComfyUI upload response omitted image name")
        return f"{returned_folder}/{returned_name}" if returned_folder else returned_name

    async def execute(self, prompt: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        response = await self._client.post(
            self._url("/prompt"),
            headers=self._headers(),
            json={"prompt": prompt, "client_id": self.client_id},
        )
        response.raise_for_status()
        submitted = response.json()
        prompt_id = submitted.get("prompt_id")
        if not isinstance(prompt_id, str):
            raise ComfyGatewayError("ComfyUI did not return a prompt_id")
        return prompt_id, await self._wait_for_history(prompt_id)

    async def object_info(self) -> dict[str, Any]:
        response = await self._client.get(self._url("/object_info"), headers=self._headers())
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ComfyGatewayError("ComfyUI object_info response must be an object")
        return result

    async def _wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.execution_timeout
        while time.monotonic() < deadline:
            response = await self._client.get(
                self._url(f"/history/{prompt_id}"),
                headers=self._headers(),
            )
            response.raise_for_status()
            history = response.json()
            item = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(item, dict):
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    raise ProviderExecutionError(provider_error_from_history(status))
                if status.get("completed") is True or item.get("outputs"):
                    return item
            await asyncio.sleep(self.poll_interval)
        raise ProviderExecutionError(provider_error("provider_timeout"))

    async def read_output(
        self,
        history: dict[str, Any],
        output: WorkflowOutput,
    ) -> tuple[Any, str | None]:
        node = history.get("outputs", {}).get(output.node_id)
        if not isinstance(node, dict) or output.field not in node:
            raise ComfyGatewayError(
                f"ComfyUI output {output.node_id}.{output.field} was not produced"
            )
        value = node[output.field]
        if output.kind == "json":
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, dict):
                raise ComfyGatewayError("ComfyUI JSON output must be an object")
            return value, None

        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            raise ComfyGatewayError("ComfyUI image output metadata is invalid")
        image = value[0]
        params = {
            "filename": image.get("filename"),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
        if not isinstance(params["filename"], str):
            raise ComfyGatewayError("ComfyUI image output omitted filename")
        url = httpx.URL(self._url("/view"), params=params)
        configured_origin = self._origin(httpx.URL(self.base_url))
        redirect_statuses = {301, 302, 303, 307, 308}
        for redirect_count in range(self._MAX_OUTPUT_REDIRECTS + 1):
            headers = self._headers() if self._origin(url) == configured_origin else {}
            response = await self._client.get(
                url,
                headers=headers,
                follow_redirects=False,
            )
            try:
                if response.status_code in redirect_statuses:
                    location = response.headers.get("location")
                    if not location:
                        raise ComfyGatewayError(
                            "ComfyUI output redirect omitted the target location"
                        )
                    if redirect_count == self._MAX_OUTPUT_REDIRECTS:
                        raise ComfyGatewayError("ComfyUI output exceeded the redirect limit")
                    next_url = httpx.URL(urljoin(str(response.url), location))
                    if (
                        self._origin(next_url) != configured_origin
                        and next_url.scheme.lower() != "https"
                    ):
                        raise ComfyGatewayError(
                            "cross-origin ComfyUI output redirects must use HTTPS"
                        )
                    url = next_url
                    continue

                response.raise_for_status()
                media_type = response.headers.get("content-type", "image/png").split(
                    ";", 1
                )[0]
                return response.content, media_type
            finally:
                await response.aclose()
        raise AssertionError("unreachable")


class ComfyWorkflowEngine:
    def __init__(
        self,
        registry: WorkflowRegistry,
        client: ComfyUIClient,
        assets: RemoteAssetLoader,
        *,
        max_concurrent_transfers: int = 4,
    ):
        if not 1 <= max_concurrent_transfers <= 16:
            raise ValueError("max_concurrent_transfers must be between 1 and 16")
        self.registry = registry
        self.client = client
        self.assets = assets
        self._transfer_semaphore = asyncio.Semaphore(max_concurrent_transfers)

    async def aclose(self) -> None:
        try:
            await self.client.aclose()
        finally:
            await self.assets.aclose()

    async def _transfer_unique(
        self,
        items: list[str],
        transfer: Callable[[str], Awaitable[Any]],
    ) -> dict[str, Any]:
        unique_items = list(dict.fromkeys(items))

        async def run(item: str) -> Any:
            async with self._transfer_semaphore:
                return await transfer(item)

        tasks = [asyncio.create_task(run(item)) for item in unique_items]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return dict(zip(unique_items, results, strict=True))

    async def check_ready(self) -> dict[str, Any]:
        specs = {
            "analyze": self.registry.load("analyze"),
            "analyze_targeted": self.registry.load("analyze_targeted"),
        }
        for operation in ("swap", "validate"):
            try:
                specs[operation] = self.registry.load(operation)
            except WorkflowNotConfigured:
                pass
        if "swap" in specs and not _configured_formal_graph_is_masked(specs["swap"]):
            raise WorkflowNotConfigured(
                "swap workflow must route {{source_image}} and the red channel of "
                "{{authorization_mask}} into PersonaWeaveYunwuMaskedImageEdit "
                "using multipart_edits"
            )
        object_info = await self.client.object_info()
        required = {
            node.get("class_type")
            for spec in specs.values()
            for node in spec.prompt.values()
            if isinstance(node, dict) and isinstance(node.get("class_type"), str)
        }
        if "swap" not in specs:
            required.update(
                {"LoadImageMask", "PersonaWeaveYunwuMaskedImageEdit"}
            )
        missing = sorted(node for node in required if node not in object_info)
        if missing:
            raise WorkflowNotConfigured(
                f"ComfyUI backend is missing workflow node classes: {', '.join(missing)}"
            )
        return {
            "status": "ready",
            "workflows": self.registry.available(),
            "required_node_classes": sorted(required),
            "capabilities": {
                "analysis": "workflow",
                "generation": "workflow" if "swap" in specs else "model_native_cloud",
                "validation": "workflow" if "validate" in specs else "app_source_compare_and_multimodal",
            },
        }

    async def _upload_content_unbounded(
        self,
        content: bytes,
        media_type: str,
        filename: str,
        subfolder: str,
    ) -> str:
        return await self.client.upload_image(
            content,
            media_type,
            filename,
            subfolder,
        )

    async def _transfer_asset(self, uri: str, batch_id: str) -> str:
        content, media_type, filename = await self.assets.load(uri)
        return await self._upload_content_unbounded(
            content,
            media_type,
            filename,
            f"personaweave/{batch_id}",
        )

    async def _upload_content(
        self,
        content: bytes,
        media_type: str,
        filename: str,
        subfolder: str,
    ) -> str:
        async with self._transfer_semaphore:
            return await self._upload_content_unbounded(
                content,
                media_type,
                filename,
                subfolder,
            )

    async def _upload(self, uri: str, batch_id: str) -> str:
        uploads = await self._transfer_unique(
            [uri],
            lambda item: self._transfer_asset(item, batch_id),
        )
        return uploads[uri]

    async def _load(self, uri: str) -> tuple[bytes, str, str]:
        assets = await self._transfer_unique([uri], self.assets.load)
        return assets[uri]

    async def _load_formal_inputs(
        self,
        request: SwapRequest,
    ) -> tuple[tuple[bytes, str, str], tuple[bytes, str, str]]:
        if request.image_api_mode != "multipart_edits":
            raise ComfyGatewayError(
                "formal swaps require image_api_mode='multipart_edits'"
            )
        if request.authorization_mask_uri is None:
            raise ComfyGatewayError(
                "formal swaps require authorization_mask_uri"
            )
        loaded = await self._transfer_unique(
            [request.asset_uri, request.authorization_mask_uri],
            self.assets.load,
        )
        source_asset = loaded[request.asset_uri]
        mask_asset = loaded[request.authorization_mask_uri]
        contract = request.compiled_contract
        if contract is not None:
            source_evidence = next(
                (
                    source
                    for source in contract.source_assets
                    if source.asset_id == request.asset_id
                ),
                None,
            )
            if source_evidence is None:
                raise ComfyGatewayError("formal source has no compiled digest evidence")
            actual_source_sha256 = hashlib.sha256(source_asset[0]).hexdigest()
            if not hmac.compare_digest(actual_source_sha256, source_evidence.sha256):
                raise ComfyGatewayError(
                    "formal source SHA-256 does not match compiled evidence"
                )
        validate_formal_source_image(source_asset[0])
        if request.authorization_mask_sha256 is not None:
            actual_mask_sha256 = hashlib.sha256(mask_asset[0]).hexdigest()
            if not hmac.compare_digest(actual_mask_sha256, request.authorization_mask_sha256):
                raise ComfyGatewayError(
                    "formal mask SHA-256 does not match request evidence"
                )
        return source_asset, mask_asset

    async def _upload_formal_inputs(
        self,
        request: SwapRequest,
        source_asset: tuple[bytes, str, str],
        mask_asset: tuple[bytes, str, str],
    ) -> tuple[str, str]:
        source_uri = await self._upload_content_unbounded(
            source_asset[0],
            source_asset[1],
            source_asset[2],
            f"personaweave/{request.batch_id}",
        )
        mask_uri = await self._upload_content_unbounded(
            mask_asset[0],
            mask_asset[1],
            mask_asset[2],
            f"personaweave/{request.batch_id}",
        )
        return source_uri, mask_uri

    async def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        operation = "analyze_targeted" if request.analysis_pass == "targeted" else "analyze"
        spec = self.registry.load(operation)
        prepared = request.model_copy(deep=True)
        prepared.asset_uri = await self._upload(request.asset_uri, request.batch_id)
        result = await self._execute_json(spec, prepared.model_dump(mode="json"), {
            "source_image": prepared.asset_uri,
            "region_hints_json": json.dumps(
                [hint.model_dump(mode="json") for hint in request.region_hints],
                separators=(",", ":"),
            ),
        })
        return AnalysisResult(
            asset_id=request.asset_id,
            faces=result.get("faces", []),
            provider=f"comfyui:{spec.name}",
            model=result.get("model") or spec.model,
        )

    async def preview_face_plan(
        self,
        *,
        source_content: bytes,
        source_media_type: str,
        source_filename: str,
    ) -> tuple[bytes, str, dict[str, Any]]:
        source_image = await self._upload_content(
            source_content,
            source_media_type,
            source_filename,
            "personaweave/face-plans",
        )
        prompt: dict[str, Any] = {
            "1": {"class_type": "LoadImage", "inputs": {"image": source_image}},
            "2": {
                "class_type": "PersonaWeaveOverlappingImageTiles",
                "inputs": {"image": ["1", 0], "tile_size": 1024, "overlap": 0.25},
            },
            "3": {
                "class_type": "LoadMediaPipeFaceLandmarker",
                "inputs": {"model_name": "mediapipe_face_fp32.safetensors"},
            },
            "4": {
                "class_type": "MediaPipeFaceLandmarker",
                "inputs": {
                    "face_detection_model": ["3", 0],
                    "image": ["2", 0],
                    "detector_variant": "both",
                    "num_faces": 0,
                    "min_confidence": 0.65,
                    "missing_frame_fallback": "empty",
                },
            },
            "5": {
                "class_type": "PersonaWeaveFaceLandmarksToCropPlan",
                "inputs": {
                    "image": ["1", 0],
                    "face_landmarks": ["4", 0],
                    "tiles_json": ["2", 1],
                    "crop_size": 1024,
                    "context_scale": 2.2,
                },
            },
            "6": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["5", 2],
                    "filename_prefix": "PersonaWeave/face-plans/mask-preview",
                },
            },
            "7": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["5", 4]}},
        }
        _, history = await self.client.execute(prompt)
        report, _ = await self.client.read_output(
            history,
            WorkflowOutput(node_id="7", field="text", kind="json"),
        )
        preview, media_type = await self.client.read_output(
            history,
            WorkflowOutput(node_id="6", field="images", kind="image"),
        )
        return preview, media_type or "image/png", report

    async def preview_replace_all(
        self,
        *,
        source_content: bytes,
        source_media_type: str,
        source_filename: str,
        description: str,
        reference_content: bytes | None = None,
        reference_media_type: str | None = None,
        reference_filename: str | None = None,
        model: str = "gpt-image-1",
        api_mode: str = "multipart_edits",
        api_path: str = "/images/edits",
        output_size: str = "1024x1024",
        response_format: str = "url",
        strategy: str = "model_native",
    ) -> tuple[bytes, str, dict[str, Any]]:
        source_image = await self._upload_content(
            source_content,
            source_media_type,
            source_filename,
            "personaweave/previews",
        )
        reference_image = None
        if reference_content is not None:
            reference_image = await self._upload_content(
                reference_content,
                reference_media_type or "image/png",
                reference_filename or "reference.png",
                "personaweave/previews",
            )

        if strategy == "model_native":
            edit_instruction = description
        elif reference_image:
            edit_instruction = (
                "The first image is the source photo and the second image is the identity reference. "
                "Replace every visible face inside the editable regions with the same person identity "
                "from the second image. Preserve each source person's head angle, expression, gaze, "
                "lighting, skin integration, occlusion, hair, body, clothes, pose, background, text, "
                "logo, framing, and photographic style. Additional instruction: "
                f"{description}"
            )
        else:
            edit_instruction = (
                "Replace every visible face inside the editable regions with one consistent fictional "
                "identity matching this description: "
                f"{description}. Preserve each source person's head angle, expression, gaze, lighting, "
                "skin integration, occlusion, hair, body, clothes, pose, background, text, logo, "
                "framing, and photographic style."
            )

        if strategy == "model_native":
            prompt: dict[str, Any] = {
                "1": {"class_type": "LoadImage", "inputs": {"image": source_image}},
                "7": {
                    "class_type": "PersonaWeaveYunwuImageEdit",
                    "inputs": {
                        "image": ["1", 0],
                        "prompt": edit_instruction,
                        "model": model,
                        "api_mode": api_mode,
                        "api_path": api_path,
                        "output_size": output_size,
                        "response_format": response_format,
                    },
                },
                "12": {
                    "class_type": "SaveImage",
                    "inputs": {
                        "images": ["7", 0],
                        "filename_prefix": "PersonaWeave/previews/model-native-result",
                    },
                },
            }
            if reference_image:
                prompt["6"] = {"class_type": "LoadImage", "inputs": {"image": reference_image}}
                prompt["7"]["inputs"]["reference_image"] = ["6", 0]
            _, history = await self.client.execute(prompt)
            image, media_type = await self.client.read_output(
                history,
                WorkflowOutput(node_id="12", field="images", kind="image"),
            )
            return image, media_type or "image/png", {
                "mode": "reference" if reference_image else "prompt",
                "model": model,
                "api_mode": api_mode,
                "api_path": api_path,
                "output_size": output_size,
                "response_format": response_format,
                "strategy": strategy,
                "outside_mask_guard": "not_applicable",
            }
        if strategy != "controlled_mask":
            raise ValueError(f"unsupported preview strategy: {strategy}")

        prompt = {
            "1": {"class_type": "LoadImage", "inputs": {"image": source_image}},
            "2": {
                "class_type": "PersonaWeaveOverlappingImageTiles",
                "inputs": {"image": ["1", 0], "tile_size": 1024, "overlap": 0.25},
            },
            "3": {
                "class_type": "LoadMediaPipeFaceLandmarker",
                "inputs": {"model_name": "mediapipe_face_fp32.safetensors"},
            },
            "4": {
                "class_type": "MediaPipeFaceLandmarker",
                "inputs": {
                    "face_detection_model": ["3", 0],
                    "image": ["2", 0],
                    "detector_variant": "both",
                    "num_faces": 0,
                    "min_confidence": 0.65,
                    "missing_frame_fallback": "empty",
                },
            },
            "5": {
                "class_type": "PersonaWeaveBoundingBoxesToFaceMask",
                "inputs": {
                    "image": ["1", 0],
                    "bboxes": ["4", 1],
                    "tiles_json": ["2", 1],
                    "expand_ratio": 0.18,
                },
            },
            "7": {
                "class_type": "PersonaWeaveYunwuMaskedImageEdit",
                "inputs": {
                    "image": ["1", 0],
                    "mask": ["5", 0],
                    "prompt": edit_instruction,
                    "model": model,
                    "api_mode": api_mode,
                    "api_path": api_path,
                    "output_size": output_size,
                    "response_format": response_format,
                },
            },
            "8": {
                "class_type": "PersonaWeaveMaskedOriginalComposite",
                "inputs": {
                    "original": ["1", 0],
                    "candidate": ["7", 0],
                    "mask": ["5", 0],
                    "feather_radius": 4.0,
                },
            },
            "9": {
                "class_type": "PersonaWeaveOutsideMaskPixelGuard",
                "inputs": {
                    "original": ["1", 0],
                    "result": ["8", 0],
                    "mask": ["5", 0],
                    "max_channel_delta": 0,
                },
            },
            "10": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["9", 1]}},
            "11": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["5", 1]}},
            "12": {
                "class_type": "SaveImage",
                "inputs": {"images": ["8", 0], "filename_prefix": "PersonaWeave/previews/result"},
            },
        }
        if reference_image:
            prompt["6"] = {"class_type": "LoadImage", "inputs": {"image": reference_image}}
            prompt["7"]["inputs"]["reference_image"] = ["6", 0]

        _, history = await self.client.execute(prompt)
        pixel_report, _ = await self.client.read_output(
            history,
            WorkflowOutput(node_id="10", field="text", kind="json"),
        )
        face_report, _ = await self.client.read_output(
            history,
            WorkflowOutput(node_id="11", field="text", kind="json"),
        )
        image, media_type = await self.client.read_output(
            history,
            WorkflowOutput(node_id="12", field="images", kind="image"),
        )
        if not pixel_report.get("passed"):
            raise ComfyGatewayError(f"preview failed outside-mask guard: {pixel_report}")
        return image, media_type or "image/png", {
            **face_report,
            **pixel_report,
            "mode": "reference" if reference_image else "prompt",
            "model": model,
            "api_mode": api_mode,
            "api_path": api_path,
            "output_size": output_size,
            "response_format": response_format,
            "strategy": strategy,
        }

    async def _execute_dynamic_formal_swap(
        self,
        request: SwapRequest,
        *,
        source_image: str,
        authorization_mask: str,
        reference_board: bytes | None,
    ) -> tuple[bytes, str]:
        reference_image = None
        if reference_board is not None:
            reference_image = await self._upload_content(
                reference_board,
                "image/png",
                "numbered-target-identities.png",
                f"personaweave/{request.batch_id}",
            )
        prompt: dict[str, Any] = {
            "1": {"class_type": "LoadImage", "inputs": {"image": source_image}},
            "2": {
                "class_type": "LoadImageMask",
                "inputs": {"image": authorization_mask, "channel": "red"},
            },
            "7": {
                "class_type": "PersonaWeaveYunwuMaskedImageEdit",
                "inputs": {
                    "image": ["1", 0],
                    "mask": ["2", 0],
                    "prompt": compose_formal_provider_instruction(request),
                    "model": request.image_model,
                    "api_mode": request.image_api_mode,
                    "api_path": request.image_api_path,
                    "output_size": request.image_output_size,
                    "response_format": request.image_response_format,
                },
            },
            "12": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["7", 0],
                    "filename_prefix": "PersonaWeave/formal/candidate",
                },
            },
        }
        if reference_image is not None:
            prompt["6"] = {
                "class_type": "LoadImage",
                "inputs": {"image": reference_image},
            }
            prompt["7"]["inputs"]["reference_image"] = ["6", 0]
        _, history = await self.client.execute(prompt)
        image, media_type = await self.client.read_output(
            history,
            WorkflowOutput(node_id="12", field="images", kind="image"),
        )
        if not isinstance(image, bytes) or not media_type:
            raise ComfyGatewayError("dynamic formal swap must return an image output")
        return image, media_type

    async def swap(self, request: SwapRequest) -> SwapResult:
        source_asset, mask_asset = await self._load_formal_inputs(request)
        source_content, source_media_type, _ = source_asset
        mask_content = mask_asset[0]
        verified_references = await self._load_verified_references(request)
        if request.preserve_outside_mask and not request.replacements:
            encoded = base64.b64encode(source_content).decode("ascii")
            return SwapResult(
                source_asset_id=request.asset_id,
                output_uri=f"data:{source_media_type};base64,{encoded}",
                output_sha256=hashlib.sha256(source_content).hexdigest(),
                provider="personaweave:source-preserving-no-op",
                model=request.image_model,
                completed_iterations=request.iteration,
            )

        try:
            spec = self.registry.load("swap")
        except WorkflowNotConfigured:
            source_image, authorization_mask = await self._upload_formal_inputs(
                request,
                source_asset,
                mask_asset,
            )
            reference_board = await self._build_numbered_reference_board(
                request,
                verified_references,
            )
            candidate, _ = await self._execute_dynamic_formal_swap(
                request,
                source_image=source_image,
                authorization_mask=authorization_mask,
                reference_board=reference_board,
            )
            image = composite_canonical_authorization_mask(
                source_content,
                candidate,
                mask_content,
            )
            provider = "comfyui:multipart_edits+canonical-authorization-mask"
            model = request.image_model
        else:
            if not _configured_formal_graph_is_masked(spec):
                raise WorkflowNotConfigured(
                    "swap workflow must route {{source_image}} and the red channel of "
                    "{{authorization_mask}} into PersonaWeaveYunwuMaskedImageEdit "
                    "using multipart_edits"
                )
            prepared = request.model_copy(deep=True)
            prepared.asset_uri, prepared.authorization_mask_uri = (
                await self._upload_formal_inputs(
                    request,
                    source_asset,
                    mask_asset,
                )
            )
            reference_uris = [
                uri
                for replacement in prepared.replacements
                for uri in replacement.reference_uris
            ]
            async def upload_reference(uri: str) -> str:
                content, media_type, filename = verified_references[uri]
                return await self._upload_content_unbounded(
                    content,
                    media_type,
                    filename,
                    f"personaweave/{request.batch_id}",
                )

            uploaded_by_uri = await self._transfer_unique(
                reference_uris,
                upload_reference,
            )
            uploaded_references: list[str] = []
            for replacement in prepared.replacements:
                replacement.reference_uris = [
                    uploaded_by_uri[uri] for uri in replacement.reference_uris
                ]
                uploaded_references.extend(replacement.reference_uris)
            context = {
                "source_image": prepared.asset_uri,
                "authorization_mask": prepared.authorization_mask_uri,
                "authorization_mask_sha256": request.authorization_mask_sha256,
                "authorization_mask_width": request.authorization_mask_width,
                "authorization_mask_height": request.authorization_mask_height,
                "reference_images": uploaded_references,
                "reference_images_json": json.dumps(uploaded_references),
                "provider_instruction": compose_formal_provider_instruction(prepared),
            }
            request_payload = prepared.model_dump(mode="json")
            request_payload["provider_instruction"] = (
                compose_formal_provider_instruction(prepared)
            )
            candidate, media_type = await self._execute(
                spec,
                request_payload,
                context,
            )
            if not isinstance(candidate, bytes) or not media_type:
                raise ComfyGatewayError("swap workflow must return an image output")
            image = composite_canonical_authorization_mask(
                source_content,
                candidate,
                mask_content,
            )
            provider = f"comfyui:{spec.name}+canonical-authorization-mask"
            model = spec.model

        digest = hashlib.sha256(image).hexdigest()
        encoded = base64.b64encode(image).decode("ascii")
        return SwapResult(
            source_asset_id=request.asset_id,
            output_uri=f"data:image/png;base64,{encoded}",
            output_sha256=digest,
            provider=provider,
            model=model,
            completed_iterations=request.iteration,
        )

    async def _load_verified_references(
        self,
        request: SwapRequest,
    ) -> dict[str, tuple[bytes, str, str]]:
        reference_uris = [
            uri
            for replacement in request.replacements
            for uri in replacement.reference_uris
        ]
        if not reference_uris:
            return {}
        assets_by_uri = await self._transfer_unique(reference_uris, self.assets.load)
        for replacement in request.replacements:
            if not replacement.reference_sha256s:
                if request.compiled_contract is not None and replacement.reference_uris:
                    raise ComfyGatewayError(
                        "formal references have no compiled digest evidence"
                    )
                continue
            for uri, expected_sha256 in zip(
                replacement.reference_uris,
                replacement.reference_sha256s,
                strict=True,
            ):
                actual_sha256 = hashlib.sha256(assets_by_uri[uri][0]).hexdigest()
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ComfyGatewayError(
                        "target reference SHA-256 does not match compiled evidence"
                    )
        return assets_by_uri

    async def _build_numbered_reference_board(
        self,
        request: SwapRequest,
        assets_by_uri: dict[str, tuple[bytes, str, str]],
    ) -> bytes | None:
        entry_keys: list[tuple[str, str]] = []
        seen_entries: set[tuple[str, str]] = set()
        for replacement in request.replacements:
            code = replacement.target_identity_code or "TARGET"
            for uri in replacement.reference_uris:
                key = (code, uri)
                if key not in seen_entries:
                    seen_entries.add(key)
                    entry_keys.append(key)
        if not entry_keys:
            return None
        entries = [(code, assets_by_uri[uri][0]) for code, uri in entry_keys]
        tile_size = 512
        label_height = 44
        columns = min(3, len(entries))
        rows = (len(entries) + columns - 1) // columns
        board = Image.new("RGB", (columns * tile_size, rows * (tile_size + label_height)), "white")
        draw = ImageDraw.Draw(board)
        for index, (code, content) in enumerate(entries):
            try:
                with Image.open(BytesIO(content)) as image:
                    image.seek(0)
                    tile = image.convert("RGB")
                    tile.thumbnail((tile_size, tile_size), Image.Resampling.LANCZOS)
            except (OSError, UnidentifiedImageError) as error:
                raise ComfyGatewayError(f"target reference {code} is not a readable image") from error
            column = index % columns
            row = index // columns
            x = column * tile_size
            y = row * (tile_size + label_height)
            board.paste(tile, (x + (tile_size - tile.width) // 2, y + (tile_size - tile.height) // 2))
            draw.rectangle((x, y + tile_size, x + tile_size, y + tile_size + label_height), fill="white")
            draw.text((x + 14, y + tile_size + 14), f"TARGET @{code}", fill="black")
        output = BytesIO()
        board.save(output, format="PNG", optimize=True)
        return output.getvalue()

    async def validate(self, request: ValidationRequest) -> ValidationResult:
        spec = self.registry.load("validate")
        prepared = request.model_copy(deep=True)
        prepared.source_uri = await self._upload(request.source_uri, request.batch_id)
        prepared.result_uri = await self._upload(request.result_uri, request.batch_id)
        result = await self._execute_json(spec, prepared.model_dump(mode="json"), {
            "source_image": prepared.source_uri,
            "result_image": prepared.result_uri,
        })
        return ValidationResult(
            result_asset_id=request.result_asset_id,
            passed=result.get("passed", False),
            provider=f"comfyui:{spec.name}",
            checks=result.get("checks", {}),
            reasons=result.get("reasons", []),
        )

    async def _execute_json(
        self,
        spec: WorkflowSpec,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        value, media_type = await self._execute(spec, request_payload, context)
        if media_type is not None or not isinstance(value, dict):
            raise ComfyGatewayError("workflow must return a JSON output")
        return value

    async def _execute(
        self,
        spec: WorkflowSpec,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[Any, str | None]:
        full_context = {
            **context,
            "request": request_payload,
            "request_json": json.dumps(request_payload, ensure_ascii=False),
        }
        prompt = render_workflow(spec.prompt, full_context)
        _, history = await self.client.execute(prompt)
        return await self.client.read_output(history, spec.output)


def validate_canonical_authorization_mask(
    content: bytes,
    *,
    expected_sha256: str,
    expected_width: int,
    expected_height: int,
    source_size: tuple[int, int],
) -> None:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ComfyGatewayError("invalid SHA-256 expectation for authorization mask")
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != expected_sha256:
        raise ComfyGatewayError(
            f"authorization mask SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}"
        )
    try:
        with Image.open(BytesIO(content)) as img:
            img.load()
            if img.mode != "L":
                raise ComfyGatewayError(
                    "authorization mask must be a single-channel L mode grayscale PNG"
                )
            width, height = img.size
            if (width, height) != (expected_width, expected_height):
                raise ComfyGatewayError(
                    f"authorization mask dimensions mismatch: "
                    f"expected ({expected_width}x{expected_height}), "
                    f"got ({width}x{height})"
                )
            if (width, height) != tuple(source_size):
                raise ComfyGatewayError(
                    "authorization mask source size mismatch: mask must match source canvas dimensions"
                )
            pixels = set()
            for y in range(height):
                for x in range(width):
                    px = img.getpixel((x, y))
                    pixels.add(px)
                    if pixels - {0, 255}:
                        raise ComfyGatewayError(
                            "authorization mask must be a strict binary L mode PNG "
                            "(pixel values limited to 0 or 255)"
                        )
    except ComfyGatewayError:
        raise
    except (UnidentifiedImageError, ValueError, OSError) as exc:
        raise ComfyGatewayError(
            f"authorization mask must be a readable PNG image: {exc}"
        ) from exc


def composite_canonical_authorization_mask(
    source_content: bytes,
    candidate_content: bytes,
    mask_content: bytes,
) -> bytes:
    with Image.open(BytesIO(source_content)) as src_img:
        src = src_img.convert("RGBA")
        width, height = src.size
    with Image.open(BytesIO(candidate_content)) as cand_img:
        cand = cand_img.convert("RGBA").resize((width, height), Image.LANCZOS)
    with Image.open(BytesIO(mask_content)) as mask_img:
        mask = mask_img.convert("L").resize((width, height), Image.LANCZOS)
    validate_canonical_authorization_mask(
        mask_content,
        expected_sha256=hashlib.sha256(mask_content).hexdigest(),
        expected_width=width,
        expected_height=height,
        source_size=(width, height),
    )
    blended = Image.composite(cand, src, mask)
    buffer = BytesIO()
    blended.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
