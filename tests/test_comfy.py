import asyncio
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from personaweave.comfy import (
    ComfyGatewayError,
    ComfyWorkflowEngine,
    ComfyUIClient,
    RemoteAssetLoader,
    UnsafeRemoteAsset,
    WorkflowOutput,
    WorkflowSpec,
    WorkflowNotConfigured,
    WorkflowRegistry,
    composite_canonical_authorization_mask,
    render_workflow,
    validate_canonical_authorization_mask,
)
from personaweave.domain import BoundingBox, SwapOperation, SwapRequest
from personaweave.provider_errors import (
    PROVIDER_ERROR_MARKER,
    ProviderExecutionError,
    provider_error_from_history,
)


def test_render_workflow_preserves_bound_value_types() -> None:
    rendered = render_workflow(
        {
            "1": {
                "inputs": {
                    "request": "{{request}}",
                    "images": "{{reference_images}}",
                }
            }
        },
        {"request": {"batch_id": "batch-1"}, "reference_images": ["a.png", "b.png"]},
    )

    assert rendered["1"]["inputs"]["request"] == {"batch_id": "batch-1"}
    assert rendered["1"]["inputs"]["images"] == ["a.png", "b.png"]


def test_canonical_mask_composite_preserves_holes_and_clips_feathering() -> None:
    source_buffer = BytesIO()
    candidate_buffer = BytesIO()
    mask_buffer = BytesIO()
    Image.new("RGB", (12, 10), "red").save(source_buffer, format="PNG")
    Image.new("RGB", (12, 10), "blue").save(candidate_buffer, format="PNG")
    mask = Image.new("L", (12, 10), 0)
    for x in range(0, 7):
        for y in range(2, 9):
            mask.putpixel((x, y), 255)
    mask.putpixel((3, 5), 0)
    mask.save(mask_buffer, format="PNG")

    result_content = composite_canonical_authorization_mask(
        source_buffer.getvalue(),
        candidate_buffer.getvalue(),
        mask_buffer.getvalue(),
    )

    with Image.open(BytesIO(result_content)) as result:
        assert result.size == (12, 10)
        assert result.getpixel((11, 5)) == (255, 0, 0)
        assert result.getpixel((3, 5)) == (255, 0, 0)
        assert result.getpixel((0, 5))[2] > 0
        assert result.getpixel((2, 5))[2] > 0


def _mask_png(
    width: int,
    height: int,
    *,
    box: tuple[int, int, int, int] | None = None,
    holes: tuple[tuple[int, int], ...] = (),
) -> bytes:
    mask = Image.new("L", (width, height), 0)
    if box is not None:
        left, top, right, bottom = box
        for x in range(left, right):
            for y in range(top, bottom):
                mask.putpixel((x, y), 255)
    for point in holes:
        mask.putpixel(point, 0)
    buffer = BytesIO()
    mask.save(buffer, format="PNG")
    return buffer.getvalue()


def _mask_request_fields(content: bytes, width: int, height: int) -> dict:
    return {
        "authorization_mask_uri": "mask",
        "authorization_mask_sha256": hashlib.sha256(content).hexdigest(),
        "authorization_mask_width": width,
        "authorization_mask_height": height,
    }


@pytest.mark.parametrize(
    ("content", "sha256", "width", "height", "message"),
    [
        (b"not-png", hashlib.sha256(b"not-png").hexdigest(), 4, 3, "readable PNG"),
        (_mask_png(4, 3), "0" * 64, 4, 3, "SHA-256"),
        (_mask_png(4, 3), hashlib.sha256(_mask_png(4, 3)).hexdigest(), 5, 3, "dimensions"),
    ],
)
def test_canonical_mask_validation_fails_closed(
    content: bytes,
    sha256: str,
    width: int,
    height: int,
    message: str,
) -> None:
    with pytest.raises(ComfyGatewayError, match=message):
        validate_canonical_authorization_mask(
            content,
            expected_sha256=sha256,
            expected_width=width,
            expected_height=height,
            source_size=(4, 3),
        )


def test_canonical_mask_validation_rejects_non_l_and_non_binary_pngs() -> None:
    rgb_buffer = BytesIO()
    Image.new("RGB", (4, 3), "white").save(rgb_buffer, format="PNG")
    grayscale_buffer = BytesIO()
    Image.new("L", (4, 3), 127).save(grayscale_buffer, format="PNG")

    for content, message in (
        (rgb_buffer.getvalue(), "L mode"),
        (grayscale_buffer.getvalue(), "binary"),
    ):
        with pytest.raises(ComfyGatewayError, match=message):
            validate_canonical_authorization_mask(
                content,
                expected_sha256=hashlib.sha256(content).hexdigest(),
                expected_width=4,
                expected_height=3,
                source_size=(4, 3),
            )


@pytest.mark.anyio
async def test_remote_asset_loader_rejects_non_allowlisted_hosts() -> None:
    loader = RemoteAssetLoader(allowed_hosts=set(), max_bytes=1024, timeout=1)

    with pytest.raises(UnsafeRemoteAsset, match="allow-listed"):
        await loader.load("https://private.example/photo.jpg")
    await loader.aclose()


@pytest.mark.anyio
async def test_comfy_client_uploads_polls_and_downloads_output() -> None:
    calls: list[str] = []
    history_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_polls
        calls.append(request.url.path)
        assert request.headers["x-api-key"] == "cloud-key"
        if request.url.path == "/api/upload/image":
            return httpx.Response(
                200,
                json={"name": "uploaded.png", "subfolder": "personaweave/batch", "type": "input"},
            )
        if request.url.path == "/api/prompt":
            body = json.loads(request.content)
            assert body["prompt"] == {"1": {"class_type": "TestNode", "inputs": {}}}
            return httpx.Response(200, json={"prompt_id": "prompt-1", "number": 1})
        if request.url.path == "/api/object_info":
            return httpx.Response(200, json={"TestNode": {"input": {}}})
        if request.url.path == "/api/history/prompt-1":
            history_polls += 1
            if history_polls == 1:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "result.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/api/view":
            return httpx.Response(302, headers={"location": "/download/result.png"})
        if request.url.path == "/download/result.png":
            return httpx.Response(200, content=b"png-result", headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = ComfyUIClient(
        "https://cloud.comfy.example",
        api_key="cloud-key",
        poll_interval=0.001,
        transport=httpx.MockTransport(handler),
    )
    pooled_client = client._client
    uploaded = await client.upload_image(b"png-input", "image/png", "source.png", "personaweave/batch")
    uploaded_again = await client.upload_image(
        b"png-input",
        "image/png",
        "source.png",
        "personaweave/batch",
    )
    object_info = await client.object_info()
    _, history = await client.execute({"1": {"class_type": "TestNode", "inputs": {}}})
    image, media_type = await client.read_output(
        history,
        WorkflowOutput(node_id="9", field="images", kind="image"),
    )
    await client.aclose()
    await client.aclose()

    assert uploaded == "personaweave/batch/uploaded.png"
    assert uploaded_again == uploaded
    assert client._client is pooled_client
    assert pooled_client.follow_redirects is False
    assert pooled_client.is_closed
    assert "TestNode" in object_info
    assert image == b"png-result"
    assert media_type == "image/png"
    assert calls == [
        "/api/upload/image",
        "/api/upload/image",
        "/api/object_info",
        "/api/prompt",
        "/api/history/prompt-1",
        "/api/history/prompt-1",
        "/api/view",
        "/download/result.png",
    ]

@pytest.mark.anyio
async def test_comfy_output_https_cross_origin_redirect_strips_api_key() -> None:
    headers_by_host: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        headers_by_host[request.url.host] = request.headers.get("x-api-key")
        if request.url.host == "cloud.comfy.example":
            return httpx.Response(
                302,
                headers={"location": "https://outputs.example/result.png"},
            )
        if request.url.host == "outputs.example":
            return httpx.Response(
                200,
                content=b"cross-origin-result",
                headers={"content-type": "image/png"},
            )
        raise AssertionError(f"unexpected output request: {request.url}")

    client = ComfyUIClient(
        "https://cloud.comfy.example",
        api_key="cloud-key",
        transport=httpx.MockTransport(handler),
    )
    image, media_type = await client.read_output(
        {"outputs": {"9": {"images": [{"filename": "result.png"}]}}},
        WorkflowOutput(node_id="9", field="images", kind="image"),
    )
    await client.aclose()

    assert image == b"cross-origin-result"
    assert media_type == "image/png"
    assert headers_by_host["cloud.comfy.example"] == "cloud-key"
    assert headers_by_host["outputs.example"] is None


@pytest.mark.anyio
async def test_comfy_output_rejects_insecure_cross_origin_redirect() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.host, request.url.path))
        return httpx.Response(
            302,
            headers={"location": "http://outputs.example/result.png"},
        )

    client = ComfyUIClient(
        "https://cloud.comfy.example",
        api_key="cloud-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ComfyGatewayError, match="must use HTTPS"):
        await client.read_output(
            {"outputs": {"9": {"images": [{"filename": "result.png"}]}}},
            WorkflowOutput(node_id="9", field="images", kind="image"),
        )
    await client.aclose()

    assert calls == [("cloud.comfy.example", "/api/view")]


@pytest.mark.anyio
async def test_comfy_client_exposes_bounded_execution_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-error"})
        if request.url.path == "/api/history/prompt-error":
            return httpx.Response(
                200,
                json={
                    "prompt-error": {
                        "status": {
                            "completed": False,
                            "status_str": "error",
                            "messages": [[
                                "execution_error",
                                {
                                    "node_type": "PersonaWeaveYunwuImageEdit",
                                    "exception_type": "RuntimeError",
                                    "exception_message": (
                                        "Yunwu image API returned HTTP 400: "
                                        "code=InvalidParameter message=size must be 2K"
                                    ),
                                },
                            ]],
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = ComfyUIClient(
        "https://cloud.comfy.example",
        poll_interval=0.001,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderExecutionError) as captured:
        await client.execute({"1": {"class_type": "TestNode", "inputs": {}}})
    await client.aclose()

    assert captured.value.error.code == "provider_request_rejected"
    assert captured.value.error.retryable is False
    assert "size must be 2K" not in str(captured.value)
    assert "size must be 2K" not in captured.value.error.model_dump_json()


def _execution_status(exception_message: str) -> dict:
    return {
        "messages": [[
            "execution_error",
            {
                "node_type": "PersonaWeaveYunwuImageEdit",
                "exception_type": "RuntimeError",
                "exception_message": exception_message,
            },
        ]]
    }


@pytest.mark.parametrize(
    ("path_facts", "expected_code", "expected_retryable"),
    [
        ({"failure_kind": "timeout"}, "provider_timeout", True),
        (
            {
                "failure_kind": "http",
                "upstream_status": 429,
                "retry_after_seconds": 30,
            },
            "provider_rate_limited",
            True,
        ),
        (
            {"failure_kind": "http", "upstream_status": 400},
            "provider_request_rejected",
            False,
        ),
        (
            {"failure_kind": "http", "upstream_status": 503},
            "provider_unavailable",
            True,
        ),
    ],
)
def test_marked_provider_failures_use_canonical_policy(
    path_facts: dict,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    marker = PROVIDER_ERROR_MARKER + json.dumps(path_facts, separators=(",", ":"))

    error = provider_error_from_history(_execution_status(f"{marker} safe fallback"))

    assert error.code == expected_code
    assert error.retryable is expected_retryable


def test_structured_policy_token_is_safe_and_non_retryable() -> None:
    raw_message = "blocked prompt includes sk-super-secret"
    marker = PROVIDER_ERROR_MARKER + json.dumps(
        {
            "failure_kind": "http",
            "upstream_status": 400,
            "provider_code": "OutputImageSensitiveContentDetected.PolicyViolation",
            "provider_type": "invalid_request_error",
            "request_id": "req-123",
        },
        separators=(",", ":"),
    )

    error = provider_error_from_history(
        _execution_status(f"{marker} safe fallback {raw_message}")
    )

    assert error.code == "provider_policy_rejected"
    assert error.retryable is False
    assert error.upstream_status == 400
    assert error.request_id == "req-123"
    assert raw_message not in error.model_dump_json()
    assert "sk-super-secret" not in error.model_dump_json()


def test_policy_is_never_inferred_from_legacy_message_prose() -> None:
    error = provider_error_from_history(
        _execution_status(
            "Yunwu image API returned HTTP 400: "
            "message=OutputImageSensitiveContentDetected.PolicyViolation secret prompt"
        )
    )

    assert error.code == "provider_request_rejected"
    assert "secret prompt" not in error.model_dump_json()


def test_unknown_legacy_comfy_failure_is_bounded_and_redacted() -> None:
    error = provider_error_from_history(
        _execution_status("provider said Bearer secret-token prompt=private")
    )

    assert error.code == "workflow_execution_failed"
    assert error.retryable is False
    assert "secret-token" not in error.model_dump_json()
    assert "private" not in error.model_dump_json()


@pytest.mark.anyio
async def test_data_uri_loader_checks_decoded_size() -> None:
    loader = RemoteAssetLoader(allowed_hosts=set(), max_bytes=4, timeout=1)
    uri = "data:image/png;base64," + base64.b64encode(b"12345").decode()

    with pytest.raises(UnsafeRemoteAsset, match="size limit"):
        loader._load_data_uri(uri)
    await loader.aclose()


@pytest.mark.anyio
async def test_model_native_preview_uses_no_detector_or_mask(tmp_path: Path) -> None:
    class FakeClient:
        prompt = None

        async def upload_image(self, *args):
            return "personaweave/previews/source.jpg"

        async def execute(self, prompt):
            self.prompt = prompt
            return "prompt-1", {"outputs": {"12": {"images": [{"filename": "result.png"}]}}}

        async def read_output(self, history, output):
            return b"result", "image/png"

    client = FakeClient()
    engine = ComfyWorkflowEngine(
        WorkflowRegistry(tmp_path),
        client,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    image, media_type, report = await engine.preview_replace_all(
        source_content=b"source",
        source_media_type="image/jpeg",
        source_filename="source.jpg",
        description="change only the requested identities",
        model="gpt-image-2",
        strategy="model_native",
    )

    classes = {node["class_type"] for node in client.prompt.values()}
    assert classes == {"LoadImage", "PersonaWeaveYunwuImageEdit", "SaveImage"}
    assert "mask" not in client.prompt["7"]["inputs"]
    assert client.prompt["7"]["inputs"] == {
        "image": ["1", 0],
        "prompt": "change only the requested identities",
        "model": "gpt-image-2",
        "api_mode": "multipart_edits",
        "api_path": "/images/edits",
        "output_size": "1024x1024",
        "response_format": "url",
    }
    assert image == b"result"
    assert media_type == "image/png"
    assert report["strategy"] == "model_native"


@pytest.mark.anyio
async def test_formal_swap_rejects_json_mode_before_loading_or_calling_comfy(
    tmp_path: Path,
) -> None:
    class Unused:
        calls = 0

        def __getattr__(self, name):
            self.calls += 1
            raise AssertionError(f"{name} must not be called")

    unused = Unused()
    engine = ComfyWorkflowEngine(
        WorkflowRegistry(tmp_path),
        unused,  # type: ignore[arg-type]
        unused,  # type: ignore[arg-type]
    )
    mask_content = _mask_png(8, 6, box=(0, 0, 2, 2))
    request = SwapRequest(
        batch_id="batch",
        asset_id="source",
        asset_uri="source",
        replacements=[
            SwapOperation(
                face_instance_id="face-1",
                bbox=BoundingBox(x=0, y=0, width=2, height=2),
            )
        ],
        protected_face_instance_ids=[],
        image_api_mode="json_generations",
        **_mask_request_fields(mask_content, 8, 6),
    )

    with pytest.raises(ComfyGatewayError, match="multipart_edits"):
        await engine.swap(request)
    assert unused.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source_kind", "message"),
    [
        ("exif-orientation", "non-identity EXIF orientation"),
        ("alpha", "alpha channel"),
        ("palette-transparency", "contain transparency"),
    ],
)
async def test_formal_swap_rejects_unsafe_source_images_before_mask_validation_or_upload(
    source_kind: str,
    message: str,
) -> None:
    source_buffer = BytesIO()
    if source_kind == "exif-orientation":
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (8, 6), "white").save(
            source_buffer,
            format="JPEG",
            exif=exif,
        )
    elif source_kind == "alpha":
        Image.new("RGBA", (8, 6), "white").save(source_buffer, format="PNG")
    else:
        transparent = Image.new("P", (8, 6), 0)
        transparent.info["transparency"] = 0
        transparent.save(source_buffer, format="PNG")

    class Assets:
        async def load(self, uri):
            if uri == "source":
                return source_buffer.getvalue(), "image/png", "source.png"
            return b"not-a-mask", "image/png", "mask.png"

    class Unused:
        def __getattr__(self, name):
            raise AssertionError(f"{name} must not be called")

    class SourceEvidence:
        asset_id = "source"
        sha256 = hashlib.sha256(source_buffer.getvalue()).hexdigest()

    class Contract:
        source_assets = [SourceEvidence()]

    class Request:
        image_api_mode = "multipart_edits"
        asset_uri = "source"
        asset_id = "source"
        authorization_mask_uri = "mask"
        authorization_mask_sha256 = "0" * 64
        authorization_mask_width = 8
        authorization_mask_height = 6
        compiled_contract = Contract()

    engine = ComfyWorkflowEngine(
        Unused(),  # type: ignore[arg-type]
        Unused(),  # type: ignore[arg-type]
        Assets(),  # type: ignore[arg-type]
    )

    with pytest.raises(ComfyGatewayError, match=message):
        await engine.swap(Request())  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_dynamic_formal_swap_uses_uploaded_canonical_mask_and_python_composite(
    tmp_path: Path,
) -> None:
    source_buffer = BytesIO()
    candidate_buffer = BytesIO()
    Image.new("RGB", (80, 60), "red").save(source_buffer, format="PNG")
    Image.new("RGB", (80, 60), "blue").save(candidate_buffer, format="PNG")
    mask_content = _mask_png(
        80,
        60,
        box=(0, 10, 50, 50),
        holes=((25, 25),),
    )

    class FakeAssets:
        calls: list[str] = []

        async def load(self, uri):
            self.calls.append(uri)
            if uri == "mask":
                return mask_content, "image/png", "canonical.png"
            return source_buffer.getvalue(), "image/png", "source.png"

    class FakeClient:
        prompt = None
        uploads: list[tuple[bytes, str, str, str]] = []

        async def upload_image(self, content, media_type, filename, subfolder):
            self.uploads.append((content, media_type, filename, subfolder))
            return f"uploaded:{filename}"

        async def execute(self, prompt):
            self.prompt = prompt
            return "prompt-1", {"outputs": {"12": {"images": [{"filename": "result.png"}]}}}

        async def read_output(self, history, output):
            return candidate_buffer.getvalue(), "image/png"

    assets = FakeAssets()
    client = FakeClient()
    engine = ComfyWorkflowEngine(
        WorkflowRegistry(tmp_path),
        client,  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
    )
    request = SwapRequest(
        batch_id="formal-batch",
        asset_id="source",
        asset_uri="source",
        compiled_instruction="Edit only the authorized identity.",
        replacements=[
            SwapOperation(
                face_instance_id="face-1",
                source_person_code="P01",
                bbox=BoundingBox(x=20, y=10, width=30, height=30),
            )
        ],
        protected_face_instance_ids=[],
        preserve_outside_mask=True,
        **_mask_request_fields(mask_content, 80, 60),
    )

    result = await engine.swap(request)

    assert assets.calls.count("source") == 1
    assert assets.calls.count("mask") == 1
    assert client.prompt["2"] == {
        "class_type": "LoadImageMask",
        "inputs": {"image": "uploaded:authorization-mask.png", "channel": "red"},
    }
    assert client.prompt["7"]["class_type"] == "PersonaWeaveYunwuMaskedImageEdit"
    assert client.prompt["7"]["inputs"]["mask"] == ["2", 0]
    assert client.prompt["7"]["inputs"]["prompt"] == request.compiled_instruction
    mask_upload = next(upload for upload in client.uploads if upload[2] == "authorization-mask.png")
    assert mask_upload[0] == mask_content
    assert hashlib.sha256(mask_upload[0]).hexdigest() == request.authorization_mask_sha256
    content = base64.b64decode(result.output_uri.split(",", 1)[1])
    with Image.open(BytesIO(content)) as image:
        assert image.getpixel((79, 59)) == (255, 0, 0)
        assert image.getpixel((25, 25)) == (255, 0, 0)
        assert image.getpixel((20, 30))[2] > 240
        assert image.getpixel((0, 30))[2] > 0
    assert result.provider == "comfyui:multipart_edits+canonical-authorization-mask"

@pytest.mark.anyio
async def test_strict_empty_swap_returns_original_without_comfy_calls() -> None:
    source_buffer = BytesIO()
    Image.new("RGB", (4, 3), "red").save(source_buffer, format="PNG")
    source = source_buffer.getvalue()
    mask = _mask_png(4, 3)

    class UnusedRegistry:
        load_calls = 0

        def load(self, operation):
            self.load_calls += 1
            raise AssertionError("strict empty swap must not inspect Comfy workflows")

    class SourceAssets:
        load_calls: list[str] = []

        async def load(self, uri):
            self.load_calls.append(uri)
            if uri == "mask":
                return mask, "image/png", "canonical.png"
            return source, "image/png", "original.png"

    class NoComfyClient:
        calls: list[str] = []

        async def upload_image(self, *args):
            self.calls.append("upload")
            raise AssertionError("strict empty swap must not upload")

        async def execute(self, *args):
            self.calls.append("execute")
            raise AssertionError("strict empty swap must not execute")

        async def read_output(self, *args):
            self.calls.append("read_output")
            raise AssertionError("strict empty swap must not read output")

    registry = UnusedRegistry()
    assets = SourceAssets()
    client = NoComfyClient()
    engine = ComfyWorkflowEngine(
        registry,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
    )
    result = await engine.swap(
        SwapRequest(
            batch_id="strict-empty",
            asset_id="source",
            asset_uri="source-uri",
            replacements=[],
            protected_face_instance_ids=[],
            preserve_outside_mask=True,
            **_mask_request_fields(mask, 4, 3),
        )
    )

    assert base64.b64decode(result.output_uri.split(",", 1)[1]) == source
    assert result.output_uri.startswith("data:image/png;base64,")
    assert result.output_sha256 == hashlib.sha256(source).hexdigest()
    assert result.provider == "personaweave:source-preserving-no-op"
    assert assets.load_calls == ["source-uri", "mask"]
    assert registry.load_calls == 0
    assert client.calls == []


@pytest.mark.anyio
async def test_remote_asset_loader_reuses_owned_client_and_closes_idempotently() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=request.url.path.encode(), headers={"content-type": "image/png"})

    loader = RemoteAssetLoader(
        allowed_hosts={"assets.example"},
        max_bytes=1024,
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    pooled_client = loader._client

    first = await loader.load("https://assets.example/first.png")
    second = await loader.load("https://assets.example/second.png")
    await loader.aclose()
    await loader.aclose()

    assert first[0] == b"/first.png"
    assert second[0] == b"/second.png"
    assert calls == ["/first.png", "/second.png"]
    assert loader._client is pooled_client
    assert pooled_client.follow_redirects is False
    assert pooled_client.is_closed


_TRANSFER_SOURCE = BytesIO()
Image.new("RGB", (10, 10), "red").save(_TRANSFER_SOURCE, format="PNG")
_TRANSFER_SOURCE_CONTENT = _TRANSFER_SOURCE.getvalue()
_TRANSFER_MASK_CONTENT = _mask_png(10, 10, box=(0, 0, 10, 10))
_TRANSFER_CANDIDATE = BytesIO()
Image.new("RGB", (10, 10), "blue").save(_TRANSFER_CANDIDATE, format="PNG")
_TRANSFER_CANDIDATE_CONTENT = _TRANSFER_CANDIDATE.getvalue()


class _TransferAssets:
    def __init__(self, *, failing_uri: str | None = None):
        self.failing_uri = failing_uri
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def load(self, uri: str) -> tuple[bytes, str, str]:
        self.calls.append(uri)
        if uri == "source":
            return _TRANSFER_SOURCE_CONTENT, "image/png", "source.png"
        if uri == "mask":
            return _TRANSFER_MASK_CONTENT, "image/png", "canonical.png"
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if uri == self.failing_uri:
                await asyncio.sleep(0.01)
                raise RuntimeError("reference transfer failed")
            await asyncio.sleep(0.05)
            return uri.encode(), "image/png", f"{uri}.png"
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        return None


class _TransferClient:
    def __init__(self):
        self.prompt: dict | None = None
        self.execute_calls = 0

    async def upload_image(self, content, media_type, filename, subfolder):
        if filename in {"source.png", "authorization-mask.png"}:
            return f"uploaded:{filename}"
        return f"uploaded:{content.decode()}"

    async def execute(self, prompt):
        self.execute_calls += 1
        self.prompt = prompt
        return "prompt-1", {}

    async def read_output(self, history, output):
        return _TRANSFER_CANDIDATE_CONTENT, "image/png"

    async def aclose(self) -> None:
        return None


class _SwapRegistry:
    def load(self, operation: str) -> WorkflowSpec:
        assert operation == "swap"
        return WorkflowSpec(
            name="configured-swap",
            version=1,
            prompt={
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "{{source_image}}"},
                },
                "2": {
                    "class_type": "LoadImageMask",
                    "inputs": {
                        "image": "{{authorization_mask}}",
                        "channel": "red",
                    },
                },
                "7": {
                    "class_type": "PersonaWeaveYunwuMaskedImageEdit",
                    "inputs": {
                        "image": ["1", 0],
                        "mask": ["2", 0],
                        "prompt": "{{provider_instruction}}",
                        "model": "gpt-image-1",
                        "api_mode": "multipart_edits",
                        "api_path": "/images/edits",
                        "output_size": "1024x1024",
                        "response_format": "url",
                    },
                },
                "9": {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["7", 0]},
                },
                "10": {
                    "class_type": "ConfiguredMetadata",
                    "inputs": {
                        "request": "{{request}}",
                        "reference_images": "{{reference_images}}",
                    },
                },
            },
            output=WorkflowOutput(node_id="9", field="images", kind="image"),
        )


def _transfer_swap_request(reference_lists: list[tuple[str, list[str]]]) -> SwapRequest:
    return SwapRequest(
        batch_id="batch",
        asset_id="asset",
        asset_uri="source",
        replacements=[
            SwapOperation(
                face_instance_id=f"face-{index}",
                bbox=BoundingBox(x=index, y=0, width=10, height=10),
                target_identity_code=code,
                reference_uris=uris,
            )
            for index, (code, uris) in enumerate(reference_lists)
        ],
        protected_face_instance_ids=[],
        **_mask_request_fields(_TRANSFER_MASK_CONTENT, 10, 10),
    )


@pytest.mark.anyio
async def test_configured_reference_transfers_are_bounded_deduplicated_and_reconstructed() -> None:
    assets = _TransferAssets()
    client = _TransferClient()
    engine = ComfyWorkflowEngine(
        _SwapRegistry(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
        max_concurrent_transfers=2,
    )
    request = _transfer_swap_request([
        ("P02", ["red", "blue", "red"]),
        ("P01", ["green", "blue"]),
    ])

    await engine.swap(request)

    assert assets.calls.count("source") == 1
    assert assets.calls.count("mask") == 1
    assert {uri: assets.calls.count(uri) for uri in ("red", "blue", "green")} == {
        "red": 1,
        "blue": 1,
        "green": 1,
    }
    assert assets.max_active == 2
    assert client.prompt is not None
    prompt_inputs = client.prompt["10"]["inputs"]
    assert client.prompt["2"]["inputs"] == {
        "image": "uploaded:authorization-mask.png",
        "channel": "red",
    }
    assert prompt_inputs["reference_images"] == [
        "uploaded:red",
        "uploaded:blue",
        "uploaded:red",
        "uploaded:green",
        "uploaded:blue",
    ]
    assert [
        replacement["reference_uris"]
        for replacement in prompt_inputs["request"]["replacements"]
    ] == [
        ["uploaded:red", "uploaded:blue", "uploaded:red"],
        ["uploaded:green", "uploaded:blue"],
    ]


@pytest.mark.anyio
async def test_configured_swap_receives_same_mask_and_uses_canonical_post_composite() -> None:
    source_buffer = BytesIO()
    candidate_buffer = BytesIO()
    Image.new("RGB", (80, 60), "red").save(source_buffer, format="PNG")
    Image.new("RGB", (80, 60), "blue").save(candidate_buffer, format="PNG")
    source_content = source_buffer.getvalue()
    mask_content = _mask_png(
        80,
        60,
        box=(10, 10, 60, 50),
        holes=((35, 25),),
    )

    class CountingAssets:
        calls: list[str] = []

        async def load(self, uri):
            self.calls.append(uri)
            if uri == "mask":
                return mask_content, "image/png", "canonical.png"
            return source_content, "image/png", "source.png"

    class RecordingClient:
        uploads: list[tuple[bytes, str, str, str]] = []
        prompt = None

        async def upload_image(self, content, media_type, filename, subfolder):
            self.uploads.append((content, media_type, filename, subfolder))
            return f"uploaded:{filename}"

        async def execute(self, prompt):
            self.prompt = prompt
            return "prompt-1", {}

        async def read_output(self, history, output):
            return candidate_buffer.getvalue(), "image/png"

    assets = CountingAssets()
    client = RecordingClient()
    engine = ComfyWorkflowEngine(
        _SwapRegistry(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
    )
    result = await engine.swap(
        SwapRequest(
            batch_id="configured-strict",
            asset_id="source",
            asset_uri="source",
            replacements=[
                SwapOperation(
                    face_instance_id="face-1",
                    bbox=BoundingBox(x=20, y=10, width=30, height=30),
                )
            ],
            protected_face_instance_ids=[],
            preserve_outside_mask=True,
            **_mask_request_fields(mask_content, 80, 60),
        )
    )

    assert assets.calls.count("source") == 1
    assert assets.calls.count("mask") == 1
    uploads_by_filename = {upload[2]: upload for upload in client.uploads}
    assert uploads_by_filename["source.png"][0] == source_content
    assert uploads_by_filename["authorization-mask.png"][0] == mask_content
    assert client.prompt["2"]["inputs"] == {
        "image": "uploaded:authorization-mask.png",
        "channel": "red",
    }
    content = base64.b64decode(result.output_uri.split(",", 1)[1])
    with Image.open(BytesIO(content)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((35, 25)) == (255, 0, 0)
        assert image.getpixel((40, 35))[2] > 240
    assert result.provider == "comfyui:configured-swap+canonical-authorization-mask"

@pytest.mark.anyio
async def test_reference_transfer_failure_cancels_siblings_before_prompt() -> None:
    assets = _TransferAssets(failing_uri="bad")
    client = _TransferClient()
    engine = ComfyWorkflowEngine(
        _SwapRegistry(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
        max_concurrent_transfers=2,
    )
    request = _transfer_swap_request([("P01", ["slow", "bad", "queued"])])

    with pytest.raises(RuntimeError, match="reference transfer failed"):
        await engine.swap(request)

    assert client.execute_calls == 0
    assert assets.active == 0
    assert assets.max_active <= 2


@pytest.mark.anyio
async def test_numbered_reference_board_preserves_pair_order_and_fetches_each_uri_once(
    monkeypatch,
) -> None:
    def color_png(color: str) -> bytes:
        output = BytesIO()
        Image.new("RGB", (24, 24), color).save(output, format="PNG")
        return output.getvalue()

    colors = {"red": color_png("red"), "blue": color_png("blue")}

    class BoardAssets(_TransferAssets):
        async def load(self, uri: str) -> tuple[bytes, str, str]:
            self.calls.append(uri)
            return colors[uri], "image/png", f"{uri}.png"

    labels: list[str] = []
    real_draw = __import__("PIL.ImageDraw", fromlist=["Draw"]).Draw

    def recording_draw(image):
        draw = real_draw(image)

        class DrawProxy:
            def rectangle(self, *args, **kwargs):
                return draw.rectangle(*args, **kwargs)

            def text(self, position, text, *args, **kwargs):
                labels.append(text)
                return draw.text(position, text, *args, **kwargs)

        return DrawProxy()

    monkeypatch.setattr("personaweave.comfy.ImageDraw.Draw", recording_draw)
    assets = BoardAssets()
    engine = ComfyWorkflowEngine(
        _SwapRegistry(),  # type: ignore[arg-type]
        _TransferClient(),  # type: ignore[arg-type]
        assets,  # type: ignore[arg-type]
        max_concurrent_transfers=2,
    )
    request = _transfer_swap_request([
        ("P02", ["red", "red"]),
        ("P01", ["blue", "red"]),
    ])

    verified = await engine._load_verified_references(request)
    content = await engine._build_numbered_reference_board(request, verified)

    assert content is not None
    assert assets.calls == ["red", "blue"]
    assert labels == ["TARGET @P02", "TARGET @P01", "TARGET @P01"]
    with Image.open(BytesIO(content)) as board:
        assert board.format == "PNG"
        assert board.size == (1536, 556)
        assert board.getpixel((256, 256)) == (255, 0, 0)
        assert board.getpixel((768, 256)) == (0, 0, 255)
        assert board.getpixel((1280, 256)) == (255, 0, 0)


@pytest.mark.anyio
async def test_dynamic_formal_swap_passes_reference_board_as_png() -> None:
    captured: dict = {}

    class MissingSwapRegistry:
        def load(self, operation: str):
            raise WorkflowNotConfigured("missing")

    class SourceAssets:
        async def load(self, uri: str):
            if uri == "mask":
                return _TRANSFER_MASK_CONTENT, "image/png", "canonical.png"
            return _TRANSFER_SOURCE_CONTENT, "image/png", "source.png"

        async def aclose(self):
            return None

    class CaptureEngine(ComfyWorkflowEngine):
        async def _build_numbered_reference_board(self, request, assets_by_uri):
            assert assets_by_uri
            return b"lossless-board"

        async def _execute_dynamic_formal_swap(self, request, **kwargs):
            captured.update(kwargs)
            return _TRANSFER_CANDIDATE_CONTENT, "image/png"

    engine = CaptureEngine(
        MissingSwapRegistry(),  # type: ignore[arg-type]
        _TransferClient(),  # type: ignore[arg-type]
        SourceAssets(),  # type: ignore[arg-type]
    )

    await engine.swap(_transfer_swap_request([("P01", ["red"])]))

    assert captured["reference_board"] == b"lossless-board"
    assert captured["authorization_mask"] == "uploaded:authorization-mask.png"


@pytest.mark.anyio
async def test_concurrent_fallback_swaps_share_upload_limit() -> None:
    class MissingSwapRegistry:
        def load(self, operation: str):
            raise WorkflowNotConfigured("missing")

    class SourceAssets:
        async def load(self, uri: str):
            if uri == "mask":
                return _TRANSFER_MASK_CONTENT, "image/png", "canonical.png"
            return _TRANSFER_SOURCE_CONTENT, "image/png", "source.png"

    class BoundedUploadClient:
        def __init__(self):
            self.active_uploads = 0
            self.max_active_uploads = 0
            self.uploaded_filenames: list[str] = []

        async def upload_image(self, content, media_type, filename, subfolder):
            self.active_uploads += 1
            self.max_active_uploads = max(
                self.max_active_uploads,
                self.active_uploads,
            )
            self.uploaded_filenames.append(filename)
            try:
                await asyncio.sleep(0.01)
                return f"uploaded:{filename}"
            finally:
                self.active_uploads -= 1

        async def execute(self, prompt):
            return "prompt-1", {}

        async def read_output(self, history, output):
            return _TRANSFER_CANDIDATE_CONTENT, "image/png"

    class BoardEngine(ComfyWorkflowEngine):
        async def _build_numbered_reference_board(self, request, assets_by_uri):
            assert assets_by_uri
            return b"reference-board"

    client = BoundedUploadClient()
    engine = BoardEngine(
        MissingSwapRegistry(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        SourceAssets(),  # type: ignore[arg-type]
        max_concurrent_transfers=1,
    )

    await asyncio.gather(
        engine.swap(_transfer_swap_request([("P01", ["red"])])),
        engine.swap(_transfer_swap_request([("P02", ["blue"])])),
    )

    assert client.max_active_uploads == 1
    assert client.uploaded_filenames.count("source.png") == 2
    assert client.uploaded_filenames.count("authorization-mask.png") == 2
    assert client.uploaded_filenames.count("numbered-target-identities.png") == 2
