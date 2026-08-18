import base64
import hashlib
from io import BytesIO

import httpx
import pytest
from PIL import Image

from personaweave.domain import (
    AttributePermission,
    BoundingBox,
    CanonicalMaskEvidence,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledIdentityEvidence,
    CompiledOperation,
    ReferenceAssetEvidence,
    ReferenceSufficiency,
    SourceAssetEvidence,
    SemanticMaskContour,
    SemanticMaskProposal,
    SwapRequest,
    TargetIdentityProfile,
    compile_formal_instruction,
    formal_selection_mode,
    project_formal_swap_operations,
)
from personaweave.providers.gateway import InferenceGateway


class CountingTransport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        await super().aclose()


@pytest.mark.anyio
async def test_gateway_reuses_owned_client_and_closes_it_once() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"request_number": len(requests)})

    transport = CountingTransport(handle)
    gateway = InferenceGateway(
        "https://gateway.example",
        None,
        transport=transport,
    )
    client = gateway._client

    assert await gateway._post("/first", {"value": 1}, "first") == {
        "request_number": 1
    }
    assert await gateway._post("/second", {"value": 2}, "second") == {
        "request_number": 2
    }
    assert gateway._client is client
    assert [request.url.path for request in requests] == ["/first", "/second"]

    await gateway.aclose()
    await gateway.aclose()

    assert transport.close_calls == 1


@pytest.mark.anyio
async def test_gateway_does_not_close_injected_client() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True})
    )
    client = httpx.AsyncClient(transport=transport)
    gateway = InferenceGateway(
        "https://gateway.example",
        None,
        client=client,
    )

    try:
        assert await gateway._post("/first", {}, "first") == {"ok": True}
        await gateway.aclose()
        await gateway.aclose()
        assert gateway._client is client
        assert not client.is_closed
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_gateway_rejects_client_and_transport_together() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(
            ValueError,
            match="transport and client cannot both be provided",
        ):
            InferenceGateway(
                "https://gateway.example",
                None,
                transport=transport,
                client=client,
            )


@pytest.mark.anyio
async def test_swap_idempotency_key_covers_generation_profile_and_fidelity(
    monkeypatch,
) -> None:
    captured_keys: list[str] = []
    captured_hashes: list[str | None] = []

    async def fake_post(path: str, payload: dict, idempotency_key: str) -> dict:
        assert path == "/v1/swap-face"
        captured_keys.append(idempotency_key)
        captured_hashes.append(payload.get("contract_hash"))
        compiled_contract = payload.get("compiled_contract")
        if (
            payload["image_api_mode"] == "multipart_edits"
            and compiled_contract["task_instruction"] == payload["task_instruction"]
        ):
            SwapRequest.model_validate(payload)
        return {
            "source_asset_id": payload["asset_id"],
            "output_uri": "data:image/png;base64,AA==",
            "output_sha256": "digest",
            "provider": "fake",
        }

    gateway = InferenceGateway("https://gateway.example", None)
    monkeypatch.setattr(gateway, "_post", fake_post)
    mask_width = 64
    mask_height = 64
    mask_image = Image.new("L", (mask_width, mask_height), 0)
    mask_image.paste(255, (10, 20, 40, 60))
    mask_output = BytesIO()
    mask_image.save(mask_output, format="PNG", optimize=False, compress_level=9)
    mask_bytes = mask_output.getvalue()
    mask_sha256 = hashlib.sha256(mask_bytes).hexdigest()
    mask_uri = "data:image/png;base64," + base64.b64encode(mask_bytes).decode("ascii")
    mask_permissions = AttributePermission(
        editable=["face_identity"],
        protected=[
            "apparent_age",
            "face_features",
            "hair",
            "eyewear",
            "accessories",
            "makeup",
        ],
    )
    mask_proposal = SemanticMaskProposal(
        source_asset_id="source",
        source_asset_code="I01",
        contours=[
            SemanticMaskContour(
                operation="include",
                face_instance_id="face-1",
                person_id="person-1",
                person_code="P01",
                permissions=mask_permissions,
                points=[
                    {"x": 10 / mask_width, "y": 20 / mask_height},
                    {"x": 40 / mask_width, "y": 20 / mask_height},
                    {"x": 40 / mask_width, "y": 60 / mask_height},
                    {"x": 10 / mask_width, "y": 60 / mask_height},
                ],
                confidence=1.0,
                evidence="deterministic editable-face idempotency fixture",
            )
        ],
        confidence=1.0,
    )
    mask_evidence = CanonicalMaskEvidence(
        source_asset_id="source",
        source_asset_code="I01",
        width=mask_width,
        height=mask_height,
        sha256=mask_sha256,
        proposal=mask_proposal,
    )
    reference_sha256 = "b" * 64
    reference_asset = ReferenceAssetEvidence(
        code="R01",
        sha256=reference_sha256,
    )
    board_digest = hashlib.sha256(
        (
            '[{"code":"R01","sha256":"'
            + reference_sha256
            + '"}]'
        ).encode()
    ).hexdigest()
    target_profile = TargetIdentityProfile(
        identity_id="identity-1",
        identity_code="T01",
        reference_board_codes=["R01"],
        reference_board_digest=board_digest,
    )
    reference_assessment = ReferenceSufficiency(
        identity_id="identity-1",
        identity_code="T01",
        reference_count=1,
        sufficient=True,
    )
    contract = CompiledBatchContract(
        task_instruction="Replace the authorized face only.",
        max_iterations=3,
        operations=[
            CompiledOperation(
                person_id="person-1",
                person_code="P01",
                action="replace",
                target_identity_id="identity-1",
                target_identity_code="T01",
                permissions=mask_permissions,
                target_profile=target_profile,
                reference_assessment=reference_assessment,
                reference_assets=[reference_asset],
            )
        ],
        identities=[
            CompiledIdentityEvidence(
                profile=target_profile,
                reference_assessment=reference_assessment,
                reference_assets=[reference_asset],
            )
        ],
        source_assets=[
            SourceAssetEvidence(
                asset_id="source",
                code="I01",
                sha256="a" * 64,
                faces=[
                    CompiledFaceEvidence(
                        face_instance_id="face-1",
                        person_id="person-1",
                        person_code="P01",
                        bbox=BoundingBox(x=10, y=20, width=30, height=40),
                        source_appearance={},
                    )
                ],
                editable_face_instance_ids=["face-1"],
                authorization_mask=mask_evidence,
            )
        ],
    )
    source_evidence = contract.source_assets[0]
    replacement = project_formal_swap_operations(
        contract,
        source_evidence,
    )[0].model_copy(
        update={"reference_uris": ["file:///reference-1.png"]}
    )
    base = SwapRequest(
        batch_id="batch",
        asset_id="source",
        asset_uri="file:///large-source.png",
        replacements=[replacement],
        protected_face_instance_ids=[],
        source_image_code="I01",
        task_instruction=contract.task_instruction,
        compiled_instruction=compile_formal_instruction(
            contract,
            source_evidence,
        ),
        selection_mode=formal_selection_mode(contract.task_instruction),
        max_iterations=contract.max_iterations,
        compiled_contract=contract,
        contract_hash=contract.sha256(),
        authorization_mask_uri=mask_uri,
        authorization_mask_sha256=mask_sha256,
        authorization_mask_width=mask_width,
        authorization_mask_height=mask_height,
    )

    await gateway.swap(base)
    await gateway.swap(base.model_copy(deep=True))

    variants = [
        base.model_copy(update={"image_model": "future-image-model"}),
        base.model_copy(update={"image_api_mode": "json_generations"}),
        base.model_copy(update={"image_api_path": "/images/generations"}),
        base.model_copy(update={"image_output_size": "1536x1024"}),
        base.model_copy(update={"image_response_format": "b64_json"}),
        base.model_copy(update={"preserve_outside_mask": True}),
    ]
    reference_variant = base.model_copy(deep=True)
    reference_variant.replacements[0].reference_uris = ["file:///reference-2.png"]
    changed_contract = contract.model_copy(
        update={"task_instruction": "A different immutable contract."}
    )
    variants.append(
        base.model_copy(
            update={
                "compiled_contract": changed_contract,
                "contract_hash": changed_contract.sha256(),
            }
        )
    )
    variants.append(reference_variant)

    for request in variants:
        await gateway.swap(request)

    assert captured_keys[0] == captured_keys[1]
    assert len(set(captured_keys)) == len(captured_keys) - 1
    assert set(captured_hashes) == {contract.sha256(), changed_contract.sha256()}
    assert captured_hashes.count(changed_contract.sha256()) == 1
    assert all(len(key.rsplit(":", 1)[1]) == 12 for key in captured_keys)

    await gateway.aclose()
