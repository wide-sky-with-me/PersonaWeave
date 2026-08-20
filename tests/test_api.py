from concurrent.futures import ThreadPoolExecutor
import base64
import json
from pathlib import Path
from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from personaweave.api import (
    build_router,
    public_asset_evidence,
    public_batch_evidence,
    public_compiled_contract,
    public_identity_evidence,
)
from personaweave.config import Settings
from personaweave.providers.gateway import InferenceGateway
from personaweave.providers.yunwu import (
    ImageEditIssue,
    ImageEditPlan,
    ImageEditReview,
    ImageEditScores,
)
from personaweave.storage import Repository
from personaweave.domain import (
    AttributePermission,
    JobPhase,
    JobStage,
    SemanticMaskContour,
    SemanticMaskProposal,
)


def make_client(tmp_path: Path, *, max_upload_bytes: int = 25 * 1024 * 1024) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key=None,
        yunwu_vision_model=None,
        yunwu_vision_api_mode="chat_completions",
        yunwu_vision_api_path="/chat/completions",
        yunwu_image_model="gpt-image-1",
        yunwu_image_api_mode="multipart_edits",
        yunwu_image_api_path="/images/edits",
        yunwu_image_size="1024x1024",
        yunwu_image_response_format="url",
        inference_base_url=None,
        max_upload_bytes=max_upload_bytes,
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    return TestClient(app)


def test_settings_reject_duplicate_quality_reviewer_models(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        Settings(
            database_path=tmp_path / "api.sqlite3",
            asset_root=tmp_path / "assets",
            yunwu_reviewer_model_a="same-model",
            yunwu_reviewer_model_b="same-model",
        )


def test_settings_reject_partial_quality_reviewer_configuration(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must both be configured"):
        Settings(
            database_path=tmp_path / "api.sqlite3",
            asset_root=tmp_path / "assets",
            yunwu_reviewer_model_a="review-model-a",
            yunwu_reviewer_model_b=None,
        )


def test_settings_reject_non_multipart_formal_image_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="formal_image_api_mode"):
        Settings(
            database_path=tmp_path / "api.sqlite3",
            asset_root=tmp_path / "assets",
            formal_image_api_mode="json_generations",
        )


def image_bytes(color: str = "white") -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="JPEG")
    return output.getvalue()


def passing_scores(**overrides) -> ImageEditScores:
    values = {
        "instruction_fulfillment": 95,
        "target_identity_consistency": 95,
        "face_naturalness": 92,
        "pose_expression_preservation": 93,
        "source_content_preservation": 98,
        "text_logo_preservation": 100,
    }
    values.update(overrides)
    return ImageEditScores(**values)


def test_router_lifespan_closes_owned_inference_gateway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class TrackingInferenceGateway(InferenceGateway):
        instances: list["TrackingInferenceGateway"] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_calls = 0
            self.instances.append(self)

        async def aclose(self) -> None:
            self.close_calls += 1
            await super().aclose()

    monkeypatch.setattr(
        "personaweave.api.InferenceGateway",
        TrackingInferenceGateway,
    )
    client = make_client(tmp_path)
    gateway = TrackingInferenceGateway.instances[0]

    assert gateway.close_calls == 0
    assert not gateway._client.is_closed

    with client:
        assert gateway.close_calls == 0
        assert not gateway._client.is_closed

    assert gateway.close_calls == 1
    assert gateway._client.is_closed


def test_batch_detail_and_progress_exclude_superseded_contract_jobs(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "active jobs"}).json()
    repository = Repository(tmp_path / "api.sqlite3")
    contract_hash = "a" * 64
    with repository.connect() as connection:
        connection.execute(
            "UPDATE batches SET compiled_contract_hash = ? WHERE id = ?",
            (contract_hash, batch["id"]),
        )
    current = repository.create_job(
        batch["id"],
        JobStage.SWAP,
        "current-source",
        {"contract_hash": contract_hash},
    )
    stale = repository.create_job(
        batch["id"],
        JobStage.SWAP,
        "superseded-source",
        {"contract_hash": "0" * 64},
    )
    repository.fail_job(stale["id"], "provider_unavailable", "stale failure")
    current_validation = repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        "stable-result",
        {
            "result_asset_id": "stable-result",
            "contract_hash": contract_hash,
        },
    )
    stale_validation = repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        "stable-result",
        {
            "result_asset_id": "stable-result",
            "contract_hash": "0" * 64,
        },
    )
    repository.fail_job(
        stale_validation["id"],
        "provider_unavailable",
        "stale validation failure",
    )

    detail = client.get(f"/api/v1/batches/{batch['id']}").json()
    progress = client.get(f"/api/v1/batches/{batch['id']}/progress").json()

    assert [job["id"] for job in detail["latest_jobs"] if job["stage"] == "swap"] == [
        current["id"]
    ]
    assert [
        job["id"] for job in detail["latest_jobs"] if job["stage"] == "validate"
    ] == [current_validation["id"]]
    assert progress["stages"]["swap"]["total"] == 1
    assert progress["stages"]["swap"]["failed"] == 0
    assert progress["stages"]["validate"]["total"] == 1
    assert progress["stages"]["validate"]["failed"] == 0


def test_health_reports_unconfigured_providers_without_leaking_secrets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "providers": {
            "inference_gateway": {"configured": False},
            "yunwu_multimodal_analysis": {
                "configured": False,
                "model": None,
                "api_mode": "chat_completions",
                "api_path": "/chat/completions",
                "default_person_analysis": True,
            },
            "yunwu_preview_image": {
                "configured": False,
                "model": "gpt-image-1",
                "api_mode": "multipart_edits",
                "api_path": "/images/edits",
                "size": "1024x1024",
                "response_format": "url",
                "timeout_seconds": 600.0,
            },
            "yunwu_formal_image": {
                "configured": False,
                "model": "gpt-image-2",
                "api_mode": "multipart_edits",
                "api_path": "/images/edits",
                "size": "1024x1024",
                "response_format": None,
                "timeout_seconds": 600.0,
            },
        },
    }


def test_health_never_exposes_provider_credentials(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key="yunwu-test-secret",
        yunwu_vision_model="vision-model",
        inference_base_url="http://inference.example",
        inference_api_key="inference-test-secret",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["providers"]["yunwu_preview_image"]["configured"] is True
    assert response.json()["providers"]["yunwu_formal_image"]["configured"] is True
    assert "yunwu-test-secret" not in response.text
    assert "inference-test-secret" not in response.text


def test_model_catalog_uses_provider_capabilities_instead_of_model_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fake_models(self):
        return [
            {
                "id": "future-vision-model",
                "owned_by": "provider",
                "supported_endpoint_types": ["openai-response"],
            },
            {
                "id": "future-renderer-without-image-in-name",
                "owned_by": "provider",
                "supported_endpoint_types": ["image-generation", "openai-编辑"],
            },
        ]

    monkeypatch.setattr(
        "personaweave.api.YunwuMultimodalProvider.list_model_records",
        fake_models,
    )
    client = make_client(tmp_path)

    response = client.get("/api/v1/providers/yunwu/models")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"]["preview_image"] == {
        "model": "gpt-image-1",
        "api_mode": "multipart_edits",
        "api_path": "/images/edits",
        "size": "1024x1024",
        "response_format": "url",
        "timeout_seconds": 600.0,
    }
    assert body["configured"]["formal_image"] == {
        "model": "gpt-image-2",
        "api_mode": "multipart_edits",
        "api_path": "/images/edits",
        "size": "1024x1024",
        "response_format": None,
        "timeout_seconds": 600.0,
    }
    assert body["conversation_candidates"][0]["id"] == "future-vision-model"
    assert body["conversation_candidates"][0]["roles"] == ["conversation"]
    assert body["image_candidates"][0]["id"] == "future-renderer-without-image-in-name"
    assert body["image_candidates"][0]["roles"] == ["image_generation", "image_edit"]


def test_batch_and_image_upload_vertical_slice(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "launch"}).json()

    upload = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    detail = client.get(f"/api/v1/batches/{batch['id']}")

    assert upload.status_code == 201
    assert detail.status_code == 200
    assert detail.json()["assets"][0]["kind"] == "source"
    assert "storage_uri" not in detail.json()["assets"][0]
    content = client.get(detail.json()["assets"][0]["content_url"])
    assert content.status_code == 200
    assert content.content == image_bytes()


def test_parallel_image_uploads_allocate_unique_stable_codes(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "parallel uploads"}).json()
    content = image_bytes()

    def upload(index: int):
        return client.post(
            f"/api/v1/batches/{batch['id']}/images",
            files={"image": (f"photo-{index}.jpg", content, "image/jpeg")},
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(upload, range(10)))

    assert [response.status_code for response in responses] == [201] * 10
    codes = sorted(response.json()["display_code"] for response in responses)
    assert codes == [f"I{index:02d}" for index in range(1, 11)]
    detail = client.get(f"/api/v1/batches/{batch['id']}").json()
    assert sorted(asset["display_code"] for asset in detail["assets"]) == codes


def test_batch_recycle_bin_supports_restore_and_permanent_deletion(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "delete me"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    ).json()

    deleted = client.delete(f"/api/v1/batches/{batch['id']}")

    assert deleted.status_code == 204
    assert client.get(f"/api/v1/batches/{batch['id']}").status_code == 404
    assert client.get(source["content_url"]).status_code == 200
    assert client.get("/api/v1/batches").json() == []
    recycled = client.get("/api/v1/recycle-bin").json()["items"]
    assert len(recycled) == 1
    assert recycled[0] == {
        "kind": "batch",
        "id": batch["id"],
        "name": "delete me",
        "status": "draft",
        "deleted_at": recycled[0]["deleted_at"],
        "restorable": True,
        "source_count": 1,
        "result_count": 0,
        "preview_urls": [source["content_url"]],
    }

    restored = client.post(f"/api/v1/recycle-bin/batches/{batch['id']}/restore")
    assert restored.status_code == 200
    assert client.get(f"/api/v1/batches/{batch['id']}").status_code == 200

    assert client.delete(f"/api/v1/batches/{batch['id']}").status_code == 204
    permanent = client.delete(f"/api/v1/recycle-bin/batches/{batch['id']}")
    assert permanent.status_code == 204
    assert client.get(source["content_url"]).status_code == 404
    assert not (tmp_path / "assets" / batch["id"]).exists()
    assert client.get("/api/v1/recycle-bin").json()["items"] == []


def test_legacy_recycle_item_keeps_name_preview_and_restores_assets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    entry = tmp_path / "assets" / ".trash" / "legacy-entry"
    source_path = entry / "source" / "source.jpg"
    result_path = entry / "results" / "result.png"
    source_path.parent.mkdir(parents=True)
    result_path.parent.mkdir(parents=True)
    source_path.write_bytes(image_bytes("red"))
    output = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, format="PNG")
    result_path.write_bytes(output.getvalue())
    (entry / ".personaweave-trash.json").write_text(json.dumps({
        "version": 1,
        "name": "当初的任务名",
        "task_instruction": "把 @P01 换成指定形象",
        "max_iterations": 3,
        "sources": [{
            "display_code": "I01",
            "filename": "original.jpg",
            "path": "source/source.jpg",
        }],
        "results": [{
            "display_code": "O01",
            "filename": "result.png",
            "path": "results/result.png",
            "parent_source_code": "I01",
        }],
    }), encoding="utf-8")

    recycled = client.get("/api/v1/recycle-bin").json()["items"]

    assert recycled[0]["name"] == "当初的任务名"
    assert recycled[0]["restorable"] is True
    assert recycled[0]["source_count"] == 1
    assert recycled[0]["result_count"] == 1
    assert len(recycled[0]["preview_urls"]) == 2
    assert client.get(recycled[0]["preview_urls"][0]).content == image_bytes("red")

    restored = client.post("/api/v1/recycle-bin/legacy/legacy-entry/restore")

    assert restored.status_code == 201
    detail = client.get(f"/api/v1/batches/{restored.json()['id']}").json()
    assert detail["name"] == "当初的任务名"
    assert detail["status"] == "draft"
    assert detail["task_instruction"] == "把 @P01 换成指定形象"
    assert [asset["display_code"] for asset in detail["assets"]] == ["I01", "O01"]
    assert detail["assets"][1]["parent_asset_id"] == detail["assets"][0]["id"]
    assert client.get("/api/v1/recycle-bin").json()["items"] == []


def test_material_library_can_copy_common_identity_into_batch(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "use material"}).json()

    created = client.post(
        "/api/v1/material-library",
        data={"label": "常用人物甲"},
        files={"image": ("target.jpg", image_bytes("green"), "image/jpeg")},
    )

    assert created.status_code == 201
    material = created.json()
    assert material["display_code"] == "L01"
    assert material["label"] == "常用人物甲"
    assert len(material["assets"]) == 1
    assert client.get(material["assets"][0]["content_url"]).content == image_bytes("green")

    used = client.post(
        f"/api/v1/material-library/{material['id']}/use",
        json={"batch_id": batch["id"]},
    )

    assert used.status_code == 201
    identity = used.json()
    assert identity["display_code"] == "T01"
    assert identity["label"] == "常用人物甲"
    assert len(identity["references"]) == 1
    copied_content_url = identity["references"][0]["content_url"]

    assert client.delete(f"/api/v1/material-library/{material['id']}").status_code == 204
    assert client.get("/api/v1/material-library").json() == []
    assert client.get(copied_content_url).content == image_bytes("green")


def test_task_assets_and_identities_receive_stable_reference_codes(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "numbered task"}).json()
    first = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("one.jpg", image_bytes("red"), "image/jpeg")},
    ).json()
    second = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("two.jpg", image_bytes("blue"), "image/jpeg")},
    ).json()
    identity = client.post(
        f"/api/v1/batches/{batch['id']}/identities",
        json={"label": "target"},
    ).json()
    reference = client.post(
        f"/api/v1/batches/{batch['id']}/identities/{identity['id']}/references",
        files={"image": ("target.jpg", image_bytes("green"), "image/jpeg")},
    ).json()

    assert (first["display_code"], second["display_code"]) == ("I01", "I02")
    assert identity["display_code"] == "T01"
    assert reference["display_code"] == "R01"

    assert client.delete(f"/api/v1/batches/{batch['id']}/assets/{second['id']}").status_code == 204
    third = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("three.jpg", image_bytes("yellow"), "image/jpeg")},
    ).json()
    assert third["display_code"] == "I03"


def test_task_instruction_rejects_unknown_numbered_references(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "prompt references"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("one.jpg", image_bytes(), "image/jpeg")},
    ).json()

    accepted = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={"task_instruction": f"编辑 @{source['display_code']}，保持其他内容", "max_iterations": 4},
    )
    rejected = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={"task_instruction": "把 @P99 替换为 @T88", "max_iterations": 3},
    )

    assert accepted.status_code == 200
    assert accepted.json()["max_iterations"] == 4
    assert rejected.status_code == 422
    assert "@P99" in rejected.json()["detail"]


def test_jpeg_upload_uses_image_contents_when_mime_is_generic(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "camera originals"}).json()

    upload = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("PHOTO.JPG", image_bytes(), "application/octet-stream")},
    )

    assert upload.status_code == 201
    assert upload.json()["filename"] == "PHOTO.JPG"
    assert upload.json()["media_type"] == "image/jpeg"


def test_non_image_upload_is_rejected_after_content_validation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "invalid input"}).json()

    upload = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
    )

    assert upload.status_code == 415
    assert upload.json()["detail"] == "uploaded file is not a supported safe image"


def test_camera_mpo_upload_is_normalized_as_jpeg(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "camera MPO"}).json()

    class VerifiedMpo:
        format = "MPO"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def verify(self) -> None:
            return None

    monkeypatch.setattr("personaweave.api.Image.open", lambda _path: VerifiedMpo())

    upload = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("NIKON.JPG", image_bytes(), "image/jpeg")},
    )

    assert upload.status_code == 201
    assert upload.json()["filename"] == "NIKON.JPG"
    assert upload.json()["media_type"] == "image/jpeg"


def test_draft_source_can_be_removed_without_breaking_shared_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "remove inputs"}).json()
    first = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("first.jpg", image_bytes(), "image/jpeg")},
    ).json()
    second = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("duplicate.jpg", image_bytes(), "image/jpeg")},
    ).json()

    removed = client.delete(f"/api/v1/batches/{batch['id']}/assets/{first['id']}")

    assert removed.status_code == 204
    assert client.get(first["content_url"]).status_code == 404
    remaining_content = client.get(second["content_url"])
    assert remaining_content.status_code == 200
    assert remaining_content.content == image_bytes()


def test_batch_inputs_cannot_be_removed_after_analysis_starts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "locked removal"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("source.jpg", image_bytes(), "image/jpeg")},
    ).json()
    assert client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs").status_code == 202

    removed = client.delete(f"/api/v1/batches/{batch['id']}/assets/{source['id']}")

    assert removed.status_code == 409
    assert client.get(source["content_url"]).status_code == 200


def test_reference_identity_cannot_cross_batch_boundary(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    first = client.post("/api/v1/batches", json={"name": "first"}).json()
    second = client.post("/api/v1/batches", json={"name": "second"}).json()
    identity = client.post(
        f"/api/v1/batches/{first['id']}/identities",
        json={"label": "target"},
    ).json()

    response = client.post(
        f"/api/v1/batches/{second['id']}/identities/{identity['id']}/references",
        files={"image": ("target.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 400


def test_batch_inputs_are_immutable_after_analysis_starts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "locked"}).json()
    client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("first.jpg", image_bytes(), "image/jpeg")},
    )
    started = client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")

    late_upload = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("late.jpg", image_bytes(), "image/jpeg")},
    )
    late_identity = client.post(
        f"/api/v1/batches/{batch['id']}/identities",
        json={"label": "late target"},
    )

    assert started.status_code == 202
    assert late_upload.status_code == 409
    assert late_identity.status_code == 409


def test_targets_remain_editable_while_confirming_extracted_people(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "confirm people"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("group.jpg", image_bytes(), "image/jpeg")},
    ).json()
    assert client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs").status_code == 202
    completed = client.post(
        f"/api/v1/batches/{batch['id']}/analysis/complete",
        json={
            "results": [
                {
                    "asset_id": source["id"],
                    "faces": [],
                    "provider": "test-detector",
                }
            ]
        },
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "awaiting_confirmation"

    identity = client.post(
        f"/api/v1/batches/{batch['id']}/identities",
        json={"label": "new target"},
    )
    assert identity.status_code == 201
    reference = client.post(
        f"/api/v1/batches/{batch['id']}/identities/{identity.json()['id']}/references",
        files={"image": ("target.jpg", image_bytes("green"), "image/jpeg")},
    )
    assert reference.status_code == 201
    assert client.delete(
        f"/api/v1/batches/{batch['id']}/assets/{reference.json()['id']}"
    ).status_code == 204


def test_target_identity_removal_blocks_live_references_and_never_reuses_codes(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "remove target"}).json()
    identity = client.post(
        f"/api/v1/batches/{batch['id']}/identities",
        json={"label": "temporary target"},
    ).json()
    reference = client.post(
        f"/api/v1/batches/{batch['id']}/identities/{identity['id']}/references",
        files={"image": ("target.jpg", image_bytes("green"), "image/jpeg")},
    ).json()
    referenced = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={
            "task_instruction": (
                f"把人物替换为 @{identity['display_code']}，参考 @{reference['display_code']}"
            ),
            "max_iterations": 3,
        },
    )
    assert referenced.status_code == 200

    blocked = client.delete(
        f"/api/v1/batches/{batch['id']}/identities/{identity['id']}"
    )
    assert blocked.status_code == 409
    assert f"@{identity['display_code']}" in blocked.json()["detail"]
    assert f"@{reference['display_code']}" in blocked.json()["detail"]
    assert client.get(reference["content_url"]).status_code == 200

    cleared = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={"task_instruction": "保持整体协调", "max_iterations": 3},
    )
    assert cleared.status_code == 200
    removed = client.delete(
        f"/api/v1/batches/{batch['id']}/identities/{identity['id']}"
    )
    assert removed.status_code == 204
    assert client.get(reference["content_url"]).status_code == 404
    assert client.get(f"/api/v1/batches/{batch['id']}").json()["identities"] == []

    replacement = client.post(
        f"/api/v1/batches/{batch['id']}/identities",
        json={"label": "replacement target"},
    )
    assert replacement.status_code == 201
    assert replacement.json()["display_code"] == "T02"


def test_extracted_person_can_be_removed_without_changing_source_image(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "remove person"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("group.jpg", image_bytes(), "image/jpeg")},
    ).json()
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")
    completed = client.post(
        f"/api/v1/batches/{batch['id']}/analysis/complete",
        json={
            "results": [{
                "asset_id": source["id"],
                "provider": "test-detector",
                "faces": [{
                    "external_face_id": "false-positive",
                    "bbox": {"x": 0, "y": 0, "width": 2, "height": 2},
                    "detection_confidence": 0.75,
                    "suggested_person_key": "false-positive",
                }],
            }]
        },
    )
    assert completed.status_code == 200
    person = client.get(f"/api/v1/batches/{batch['id']}").json()["persons"][0]
    saved = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={
            "task_instruction": f"@{person['display_code']} 保留其他人物",
            "max_iterations": 3,
        },
    )
    assert saved.status_code == 200

    removed = client.delete(f"/api/v1/batches/{batch['id']}/persons/{person['id']}")
    detail = client.get(f"/api/v1/batches/{batch['id']}").json()

    assert removed.status_code == 204
    assert detail["persons"] == []
    assert Repository(tmp_path / "api.sqlite3").get_batch(batch["id"])[
        "task_instruction"
    ] == "保留其他人物"
    assert client.get(source["content_url"]).content == image_bytes()


def test_missing_face_can_be_manually_added_to_existing_person(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "manual face"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("group.jpg", image_bytes(), "image/jpeg")},
    ).json()
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")
    completed = client.post(
        f"/api/v1/batches/{batch['id']}/analysis/complete",
        json={
            "results": [{
                "asset_id": source["id"],
                "provider": "test-detector",
                "faces": [{
                    "external_face_id": "face-0",
                    "bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "detection_confidence": 0.9,
                    "suggested_person_key": "person-a",
                }],
            }]
        },
    )
    assert completed.status_code == 200
    person = client.get(f"/api/v1/batches/{batch['id']}").json()["persons"][0]
    assert person["confirmed"] is False
    assert person["faces"][0]["needs_confirmation"] is True

    added = client.post(
        f"/api/v1/batches/{batch['id']}/assets/{source['id']}/faces",
        json={
            "bbox": {"x": 2, "y": 2, "width": 2, "height": 2},
            "person_id": person["id"],
        },
    )

    assert added.status_code == 201
    assert added.json()["display_code"] == "P01"
    assert len(added.json()["faces"]) == 2
    assert added.json()["faces"][1]["identity_confidence"] == 1.0
    assert added.json()["faces"][1]["needs_confirmation"] is False
    duplicate = client.post(
        f"/api/v1/batches/{batch['id']}/assets/{source['id']}/faces",
        json={"bbox": {"x": 2, "y": 2, "width": 2, "height": 2}},
    )
    assert duplicate.status_code == 400
    assert "overlaps" in duplicate.json()["detail"]


def test_failed_occurrence_can_be_manually_framed_or_kept_per_source(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "resolve occurrence"}).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("group.jpg", image_bytes(), "image/jpeg")},
    ).json()
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")
    completed = client.post(
        f"/api/v1/batches/{batch['id']}/analysis/complete",
        json={
            "results": [{
                "asset_id": source["id"],
                "provider": "test-detector",
                "faces": [{
                    "external_face_id": "I01-V01",
                    "bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "detection_confidence": 0.2,
                    "suggested_person_key": "person-a",
                    "identity_confidence": 0.9,
                }],
                "detection_audit": {
                    "available": True,
                    "complete": True,
                    "detected_face_count": 1,
                    "visible_face_count": 1,
                    "requires_manual_review": True,
                    "summary": "candidate is occluded",
                },
                "localization_attempts": [{
                    "occurrence_code": "I01-V01",
                    "attempt": 1,
                    "bbox": {"x": 0, "y": 0, "width": 2, "height": 2},
                    "confidence": 0.4,
                    "full_face_visible": False,
                    "crop_contains_all_source_visible_face": True,
                    "source_face_condition": "major_occlusion",
                    "face_centered": True,
                    "tight_face_crop": True,
                    "accepted": True,
                    "boundary_issue": "none",
                    "unrecoverable_occlusion": True,
                    "reason": "phone obscures the face",
                }],
            }]
        },
    )
    assert completed.status_code == 200
    detail = client.get(f"/api/v1/batches/{batch['id']}").json()
    face = detail["persons"][0]["faces"][0]
    assert face["localization_preview_url"]
    assert client.get(face["localization_preview_url"]).status_code == 200

    accepted_occlusion = client.put(
        f"/api/v1/batches/{batch['id']}/faces/{face['id']}/resolution",
        json={"action": "accept_occlusion"},
    )
    assert accepted_occlusion.status_code == 200
    assert accepted_occlusion.json()["analysis_review_issues"] == []
    assert (
        accepted_occlusion.json()["person"]["faces"][0]["analysis_resolution"]
        == "accept_occlusion"
    )

    framed = client.put(
        f"/api/v1/batches/{batch['id']}/faces/{face['id']}/resolution",
        json={
            "action": "manual_bbox",
            "bbox": {"x": 1, "y": 1, "width": 2, "height": 2},
        },
    )
    assert framed.status_code == 200
    assert framed.json()["analysis_review_issues"] == []
    updated_face = framed.json()["person"]["faces"][0]
    assert updated_face["bbox"] == {"x": 1, "y": 1, "width": 2, "height": 2}
    assert updated_face["analysis_resolution"] == "manual_bbox"

    kept = client.put(
        f"/api/v1/batches/{batch['id']}/faces/{face['id']}/resolution",
        json={"action": "keep_original"},
    )
    assert kept.status_code == 200
    assert kept.json()["analysis_review_issues"] == []
    assert kept.json()["person"]["faces"][0]["analysis_resolution"] == "keep_original"


def test_combined_analysis_action_is_exposed_without_hiding_provider_failure(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "one action"}).json()
    client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("group.jpg", image_bytes(), "image/jpeg")},
    )

    response = client.post(f"/api/v1/batches/{batch['id']}/analysis/run")
    detail = client.get(f"/api/v1/batches/{batch['id']}").json()

    assert response.status_code == 503
    assert detail["status"] == "analyzing"
    assert detail["latest_jobs"][0]["status"] == "queued"


def test_execute_analysis_requires_multimodal_provider(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "remote"}).json()
    client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")

    response = client.post(f"/api/v1/batches/{batch['id']}/stages/analyze/execute")
    detail = client.get(f"/api/v1/batches/{batch['id']}").json()

    assert response.status_code == 503
    assert detail["latest_jobs"][0]["status"] == "queued"
    assert detail["latest_jobs"][0]["phase"] == "queued"
    assert detail["latest_jobs"][0]["current_iteration"] == 0
    assert detail["latest_jobs"][0]["total_iterations"] == 1
    assert detail["latest_jobs"][0]["progress_message"] == "等待执行"
    assert "input" not in detail["latest_jobs"][0]



def test_public_job_derives_retryability_from_canonical_code(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "policy failure"}).json()
    client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")
    repository = Repository(tmp_path / "api.sqlite3")
    job = repository.latest_stage_jobs(batch["id"], JobStage.ANALYZE)[0]
    repository.mark_job_running(
        job["id"],
        phase=JobPhase.ANALYZING,
        message="processing",
    )
    repository.fail_job(
        job["id"],
        "provider_policy_rejected",
        "图片内容触发服务商安全策略，无法完成生成。",
    )

    public = client.get(f"/api/v1/batches/{batch['id']}").json()["latest_jobs"][0]

    assert public["error_code"] == "provider_policy_rejected"
    assert public["error_message"] == "图片内容触发服务商安全策略，无法完成生成。"
    assert public["error_retryable"] is False
    assert "input" not in public

    repository.fail_job(
        job["id"],
        "provider_rate_limited",
        "图片生成服务请求过于频繁，请稍后重试。",
    )
    retryable_public = client.get(
        f"/api/v1/batches/{batch['id']}"
    ).json()["latest_jobs"][0]
    assert retryable_public["error_retryable"] is True


def test_swap_retry_preserves_stored_fidelity_mode(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_image_model="preview-only-model",
        yunwu_image_api_mode="json_generations",
        yunwu_image_api_path="/api/v3/images/generations",
        yunwu_image_size="2K",
        formal_image_model="gpt-image-1",
        formal_image_api_mode="multipart_edits",
        formal_image_api_path="/images/edits",
        formal_image_size="1024x1024",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    for preserve_outside_mask in (True, False):
        batch = repository.create_batch(f"fidelity-{preserve_outside_mask}")
        job = repository.create_job(
            batch["id"],
            JobStage.SWAP,
            f"source-{preserve_outside_mask}",
            {
                "image_model": "stored-model",
                "image_api_mode": "json_generations",
                "image_api_path": "/stored/path",
                "image_output_size": "1536x1024",
                "image_response_format": "b64_json",
                "preserve_outside_mask": preserve_outside_mask,
            },
        )
        repository.fail_job(job["id"], "provider_timeout", "failed")

        response = client.post(f"/api/v1/jobs/{job['id']}/retry")

        assert response.status_code == 201
        retried = repository.get_job_for_item(
            batch["id"],
            JobStage.SWAP,
            f"source-{preserve_outside_mask}",
        )
        assert retried["input"]["preserve_outside_mask"] is preserve_outside_mask
        assert retried["input"]["image_model"] == "gpt-image-1"
        assert retried["input"]["image_api_mode"] == "multipart_edits"
        assert retried["input"]["image_api_path"] == "/images/edits"
        assert retried["input"]["image_output_size"] == "1024x1024"
        assert retried["input"]["image_response_format"] is None

def test_same_filename_does_not_overwrite_different_images(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "same names"}).json()

    first = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes("white"), "image/jpeg")},
    ).json()
    second = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("photo.jpg", image_bytes("black"), "image/jpeg")},
    ).json()

    assert first["sha256"] != second["sha256"]
    assert client.get(first["content_url"]).content == image_bytes("white")
    assert client.get(second["content_url"]).content == image_bytes("black")


def test_spoofed_image_upload_is_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post("/api/v1/batches", json={"name": "invalid"}).json()

    response = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("fake.jpg", b"not-an-image", "image/jpeg")},
    )

    assert response.status_code == 415


def test_upload_limit_is_enforced_before_image_parsing(tmp_path: Path) -> None:
    client = make_client(tmp_path, max_upload_bytes=1024)
    batch = client.post("/api/v1/batches", json={"name": "large"}).json()

    response = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("large.jpg", b"x" * 1025, "image/jpeg")},
    )

    assert response.status_code == 413


def test_preview_iteration_limit_rejects_values_above_ten(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={"description": "replace both people", "max_iterations": "11"},
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 422


def test_preview_forwards_configured_image_model(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured.setdefault("timeouts", []).append(kwargs.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["data"] = kwargs["data"]
            return httpx.Response(
                200,
                content=b"generated-image",
                headers={"content-type": "image/png"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key=None,
        yunwu_image_model="gpt-image-2",
        yunwu_image_api_mode="multipart_edits",
        yunwu_image_api_path="/images/edits",
        yunwu_image_size="1024x1024",
        yunwu_image_response_format="url",
        yunwu_image_timeout_seconds=321,
        preview_strategy="controlled_mask",
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={"description": "replace every detected face"},
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.content == b"generated-image"
    assert captured == {
        "timeouts": [600.0, 600.0, 321.0],
        "url": "http://127.0.0.1:8010/v1/preview-replacement",
        "data": {
            "description": "replace every detected face",
            "model": "gpt-image-2",
            "api_mode": "multipart_edits",
            "api_path": "/images/edits",
            "output_size": "1024x1024",
            "response_format": "url",
            "strategy": "controlled_mask",
        },
    }


def test_model_native_preview_uses_multimodal_plan_and_review(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    async def fake_plan(self, **kwargs):
        captured["plan_description"] = kwargs["description"]
        return ImageEditPlan(
            edit_prompt="Modify the requested faces while preserving every other source detail.",
            observed_people=2,
            intended_face_edits=2,
            warnings=[],
        )

    async def fake_review(self, **kwargs):
        captured["review_iteration"] = kwargs["iteration"]
        captured["review_description"] = kwargs["description"]
        return ImageEditReview(
            passed=True,
            scores=passing_scores(),
            issues=[],
            summary="通过",
            revised_edit_prompt=None,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["gateway_data"] = kwargs["data"]
            report = base64.urlsafe_b64encode(
                json.dumps({"strategy": "model_native", "model": "gpt-image-2"}).encode()
            ).decode()
            return httpx.Response(
                200,
                content=image_bytes("blue"),
                headers={
                    "content-type": "image/jpeg",
                    "X-PersonaWeave-Report": report,
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.plan_image_edit", fake_plan)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.review_image_edit", fake_review)
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key="secret",
        yunwu_vision_model="gpt-5.6-sol",
        yunwu_image_model="gpt-image-2",
        preview_strategy="model_native",
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={"description": "replace both people with one identity"},
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert captured["plan_description"] == "replace both people with one identity"
    assert captured["review_iteration"] == 1
    assert captured["review_description"] == "replace both people with one identity"
    assert captured["gateway_data"]["model"] == "gpt-image-2"
    assert captured["gateway_data"]["strategy"] == "model_native"
    assert "IMMUTABLE USER REQUIREMENT" in captured["gateway_data"]["description"]
    assert "replace both people with one identity" in captured["gateway_data"]["description"]
    assert "Modify the requested faces" in captured["gateway_data"]["description"]
    report = json.loads(
        base64.urlsafe_b64decode(
            response.headers["X-PersonaWeave-Report"]
            + "=" * (-len(response.headers["X-PersonaWeave-Report"]) % 4)
        )
    )
    assert report["multimodal_plan"]["intended_face_edits"] == 2
    assert report["multimodal_review"]["passed"] is True
    assert report["completed_iterations"] == 1
    assert report["stopped_reason"] == "review_passed"


def test_model_native_preview_uses_review_revised_prompt_with_bounded_feedback_loop(
    tmp_path: Path, monkeypatch
) -> None:
    planner_calls = []
    generation_descriptions = []
    generation_sources = []
    review_calls = []

    async def fake_plan(self, **kwargs):
        planner_calls.append(kwargs)
        return ImageEditPlan(
            edit_prompt="Initial complete prompt: modify faces and preserve all source details.",
            observed_people=2,
            intended_face_edits=2,
            warnings=[],
        )

    async def fake_review(self, **kwargs):
        review_calls.append(kwargs)
        if len(review_calls) == 1:
            return ImageEditReview(
                passed=False,
                scores=passing_scores(source_content_preservation=60),
                issues=[
                    ImageEditIssue(
                        category="source_content",
                        severity="major",
                        evidence="background changed",
                        correction="restore the original background exactly",
                    )
                ],
                summary="背景变化",
                revised_edit_prompt=(
                    "Complete revised prompt: replace both faces while restoring the original "
                    "background and retaining every earlier constraint."
                ),
            )
        return ImageEditReview(
            passed=True,
            scores=passing_scores(),
            issues=[],
            summary="通过",
            revised_edit_prompt=None,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            generation_descriptions.append(kwargs["data"]["description"])
            generation_sources.append(kwargs["files"][0][1][1])
            color = "blue" if len(generation_descriptions) == 1 else "red"
            return httpx.Response(
                200,
                content=image_bytes(color),
                headers={"content-type": "image/jpeg"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.plan_image_edit", fake_plan)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.review_image_edit", fake_review)
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key="secret",
        yunwu_vision_model="gpt-5.6-sol",
        yunwu_image_model="gpt-image-2",
        preview_strategy="model_native",
        preview_max_iterations=2,
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={
            "description": "replace both people with one identity",
            "max_iterations": "3",
        },
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.content == image_bytes("red")
    assert len(generation_descriptions) == 2
    assert generation_sources == [image_bytes(), image_bytes()]
    assert len(planner_calls) == 1
    assert len(review_calls) == 2
    assert "IMMUTABLE USER REQUIREMENT" in generation_descriptions[0]
    assert "replace both people with one identity" in generation_descriptions[0]
    assert "Initial complete prompt" in generation_descriptions[0]
    assert "IMMUTABLE USER REQUIREMENT" in generation_descriptions[1]
    assert "replace both people with one identity" in generation_descriptions[1]
    assert "Complete revised prompt" in generation_descriptions[1]
    assert review_calls[0]["source_uri"] == review_calls[1]["source_uri"]
    report = json.loads(
        base64.urlsafe_b64decode(
            response.headers["X-PersonaWeave-Report"]
            + "=" * (-len(response.headers["X-PersonaWeave-Report"]) % 4)
        )
    )
    assert report["max_iterations"] == 2
    assert report["completed_iterations"] == 2
    assert report["stopped_reason"] == "review_passed"
    assert report["iterations"][1]["prompt_source"] == "multimodal_review_revision"


def test_model_native_preview_stops_when_review_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    generation_count = 0

    async def fake_plan(self, **kwargs):
        return ImageEditPlan(
            edit_prompt="Modify the requested faces while preserving all source details.",
            observed_people=2,
            intended_face_edits=2,
            warnings=[],
        )

    async def unavailable_review(self, **kwargs):
        raise httpx.TimeoutException("review timed out")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            nonlocal generation_count
            generation_count += 1
            return httpx.Response(
                200,
                content=image_bytes("blue"),
                headers={"content-type": "image/jpeg"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.plan_image_edit", fake_plan)
    monkeypatch.setattr(
        "personaweave.api.YunwuMultimodalProvider.review_image_edit",
        unavailable_review,
    )
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key="secret",
        yunwu_vision_model="gpt-5.6-sol",
        preview_strategy="model_native",
        preview_max_iterations=2,
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={"description": "replace both people", "max_iterations": "2"},
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert generation_count == 1
    assert response.headers["X-PersonaWeave-Needs-Review"] == "true"
    report = json.loads(
        base64.urlsafe_b64decode(
            response.headers["X-PersonaWeave-Report"]
            + "=" * (-len(response.headers["X-PersonaWeave-Report"]) % 4)
        )
    )
    assert report["completed_iterations"] == 1
    assert report["stopped_reason"] == "review_unavailable"
    assert report["multimodal_review"]["error_type"] == "TimeoutException"


def test_model_native_preview_stops_when_review_omits_next_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    generation_count = 0

    async def fake_plan(self, **kwargs):
        return ImageEditPlan(
            edit_prompt="Modify both requested faces and preserve all source details exactly.",
            observed_people=2,
            intended_face_edits=2,
            warnings=[],
        )

    async def incomplete_review(self, **kwargs):
        return ImageEditReview(
            passed=False,
            scores=passing_scores(source_content_preservation=50),
            issues=[
                ImageEditIssue(
                    category="source_content",
                    severity="major",
                    evidence="the background geometry changed",
                    correction="retain the original background geometry exactly",
                )
            ],
            summary="未通过，但未返回下一轮完整提示词",
            revised_edit_prompt=None,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            nonlocal generation_count
            generation_count += 1
            return httpx.Response(
                200,
                content=image_bytes("blue"),
                headers={"content-type": "image/jpeg"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("personaweave.api.YunwuMultimodalProvider.plan_image_edit", fake_plan)
    monkeypatch.setattr(
        "personaweave.api.YunwuMultimodalProvider.review_image_edit",
        incomplete_review,
    )
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key="secret",
        yunwu_vision_model="gpt-5.6-sol",
        preview_strategy="model_native",
        preview_max_iterations=2,
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/replace-all",
        data={"description": "replace both people", "max_iterations": "2"},
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert generation_count == 1
    assert response.headers["X-PersonaWeave-Needs-Review"] == "true"
    report = json.loads(
        base64.urlsafe_b64decode(
            response.headers["X-PersonaWeave-Report"]
            + "=" * (-len(response.headers["X-PersonaWeave-Report"]) % 4)
        )
    )
    assert report["completed_iterations"] == 1
    assert report["stopped_reason"] == "review_incomplete"


def test_face_plan_preview_proxies_without_image_model(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["files"] = kwargs["files"]
            return httpx.Response(
                200,
                content=b"mask-preview",
                headers={"content-type": "image/png"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key=None,
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))
    client = TestClient(app)

    response = client.post(
        "/api/v1/previews/face-plan",
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.content == b"mask-preview"
    assert captured["url"] == "http://127.0.0.1:8010/v1/preview-face-plan"
    assert set(captured["files"]) == {"source"}


def test_preview_proxy_replaces_malformed_gateway_detail(tmp_path: Path, monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return httpx.Response(
                502,
                content=b"raw provider body prompt=private sk-super-secret",
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("personaweave.api.httpx.AsyncClient", FakeAsyncClient)
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        asset_root=tmp_path / "assets",
        yunwu_api_key=None,
        inference_base_url="http://127.0.0.1:8010",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    app = FastAPI()
    app.include_router(build_router(repository, settings))

    response = TestClient(app).post(
        "/api/v1/previews/face-plan",
        files={"source": ("photo.jpg", image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "provider_unavailable",
        "message": "图片生成服务暂时不可用，请稍后重试。",
        "retryable": True,
    }
    assert "private" not in response.text
    assert "sk-super-secret" not in response.text

def test_public_contract_evidence_allowlists_bounded_fields() -> None:
    contract = {
        "version": "1",
        "task_instruction": "private prompt",
        "provider_raw_body": "raw provider body sk-secret",
        "max_iterations": 2,
        "requires_manual_review": True,
        "operations": [{
            "person_id": "person-1",
            "person_code": "P01",
            "action": "edit",
            "instruction": "provider-expanded prompt",
            "storage_uri": "file:///private/source.jpg",
            "permissions": {
                "editable": ["hair"],
                "protected": ["face_identity"],
                "raw_body": "secret",
            },
            "target_profile": None,
            "reference_assessment": None,
            "reference_assets": [],
            "source_reference_coverage": [],
            "requires_manual_review": True,
        }],
        "identities": [],
        "source_assets": [
            {
                "asset_id": "asset-1",
                "code": "I01",
                "sha256": "a" * 64,
                "storage_uri": "file:///private/source.jpg",
            }
        ],
    }

    public = public_compiled_contract(contract)
    serialized = json.dumps(public)

    assert public is not None
    assert public["operations"][0]["permissions"] == {
        "editable": ["hair"],
        "protected": ["face_identity"],
    }
    assert "prompt" not in serialized
    assert "storage_uri" not in serialized
    assert "raw provider body" not in serialized
    assert "sk-secret" not in serialized
    assert public_batch_evidence(
        {"id": "batch", "task_instruction": "user-authored prompt"}
    )["task_instruction"] == "user-authored prompt"
    assert "storage_uri" not in public_identity_evidence(
        {"id": "identity", "storage_uri": "file:///private/reference.jpg"}
    )
    public_reference = public_asset_evidence(
        {
            "id": "reference",
            "kind": "reference",
            "storage_uri": "file:///private/reference.jpg",
            "prompt": "private prompt",
            "raw_body": "raw provider body",
        },
        "/api/v1/assets/reference/content",
    )
    assert public_reference == {
        "id": "reference",
        "kind": "reference",
        "content_url": "/api/v1/assets/reference/content",
    }


def test_api_requires_compile_and_exact_hash_confirmation_before_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def compile_permissions(_self, *, task_instruction, operations):
        assert task_instruction in {"修改 @P01 的脸", "修改 @P01 的发型"}
        assert [item["person_code"] for item in operations] == ["P01"]
        return {
            "P01": AttributePermission(
                editable=["face_features"],
                protected=[
                    "face_identity",
                    "apparent_age",
                    "hair",
                    "eyewear",
                    "accessories",
                    "makeup",
                ],
            )
        }

    async def compile_semantic_mask(
        _self,
        *,
        source_asset_id,
        source_asset_code,
        source_uri,
        faces,
        protected_people,
        identity_evidence,
    ):
        assert source_uri.startswith("data:image/jpeg;base64,")
        assert len(faces) == 1
        assert protected_people == []
        assert isinstance(identity_evidence, list)
        face = faces[0]
        permissions = AttributePermission(
            editable=face["editable_attributes"],
            protected=face["protected_attributes"],
        )
        return SemanticMaskProposal(
            source_asset_id=source_asset_id,
            source_asset_code=source_asset_code,
            contours=[
                SemanticMaskContour(
                    operation="include",
                    face_instance_id=face["face_instance_id"],
                    person_id=face["person_id"],
                    person_code=face["person_code"],
                    permissions=permissions,
                    points=[
                        {"x": 0.25, "y": 0.25},
                        {"x": 0.75, "y": 0.25},
                        {"x": 0.75, "y": 0.75},
                        {"x": 0.25, "y": 0.75},
                    ],
                    confidence=1,
                    evidence="Deterministic API test contour.",
                )
            ],
            confidence=1,
            summary="Deterministic API semantic-mask proposal.",
        )

    async def stop_after_swap_started(_self, batch_id, stage):
        assert stage == JobStage.SWAP
        return {"batch_id": batch_id, "completed": False}

    monkeypatch.setattr(
        "personaweave.providers.yunwu.YunwuMultimodalProvider.compile_attribute_permissions",
        compile_permissions,
    )
    monkeypatch.setattr(
        "personaweave.providers.yunwu.YunwuMultimodalProvider.compile_semantic_mask_proposal",
        compile_semantic_mask,
    )
    monkeypatch.setattr(
        "personaweave.executor.PipelineExecutor.execute_stage",
        stop_after_swap_started,
    )
    client = make_client(tmp_path)
    batch = client.post(
        "/api/v1/batches",
        json={
            "name": "explicit contract",
            "task_instruction": "修改 @P01 的脸",
            "max_iterations": 2,
        },
    ).json()
    source = client.post(
        f"/api/v1/batches/{batch['id']}/images",
        files={"image": ("source.jpg", image_bytes(), "image/jpeg")},
    ).json()
    client.post(f"/api/v1/batches/{batch['id']}/analysis/jobs")
    client.post(
        f"/api/v1/batches/{batch['id']}/analysis/complete",
        json={
            "results": [{
                "asset_id": source["id"],
                "provider": "deterministic",
                "faces": [{
                    "external_face_id": "face-1",
                    "bbox": {"x": 0, "y": 0, "width": 4, "height": 4},
                    "detection_confidence": 1,
                    "suggested_person_key": "person-1",
                }],
            }]
        },
    )

    assert client.post(
        f"/api/v1/batches/{batch['id']}/task/run"
    ).status_code == 409
    compiled = client.post(
        f"/api/v1/batches/{batch['id']}/task/compile"
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["contract_compile_status"] == "succeeded"
    assert compiled.json()["contract_compile_error"] is None
    assert compiled.json()["contract_compile_started_at"] is not None
    assert compiled.json()["contract_compile_finished_at"] is not None
    contract_hash = compiled.json()["contract_hash"]
    assert "task_instruction" not in compiled.json()["compiled_contract"]
    unchanged = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={"task_instruction": "修改 @P01 的脸", "max_iterations": 2},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["contract_hash"] == contract_hash
    assert unchanged.json()["contract_compile_status"] == "succeeded"
    assert client.post(
        f"/api/v1/batches/{batch['id']}/task/run"
    ).status_code == 409

    changed = client.put(
        f"/api/v1/batches/{batch['id']}/spec",
        json={"task_instruction": "修改 @P01 的发型", "max_iterations": 2},
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "awaiting_confirmation"
    assert changed.json()["contract_compile_status"] == "idle"
    stale = client.post(
        f"/api/v1/batches/{batch['id']}/task/confirm",
        json={"contract_hash": contract_hash},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"]

    recompiled = client.post(
        f"/api/v1/batches/{batch['id']}/task/compile"
    ).json()
    acknowledged_masks = [
        source["authorization_mask"]["sha256"]
        for source in recompiled["compiled_contract"]["source_assets"]
        if source["authorization_mask"] is not None
    ]
    confirmed = client.post(
        f"/api/v1/batches/{batch['id']}/task/confirm",
        json={
            "contract_hash": recompiled["contract_hash"],
            "acknowledged_mask_sha256s": acknowledged_masks,
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert client.post(
        f"/api/v1/batches/{batch['id']}/task/run"
    ).status_code == 200


def test_task_compile_failure_is_persisted_for_the_workspace(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    batch = client.post(
        "/api/v1/batches",
        json={
            "name": "visible compile failure",
            "task_instruction": "修改 @P01 的脸",
            "max_iterations": 2,
        },
    ).json()

    failed = client.post(f"/api/v1/batches/{batch['id']}/task/compile")

    assert failed.status_code == 409
    current = client.get(f"/api/v1/batches/{batch['id']}").json()
    assert current["contract_compile_status"] == "failed"
    assert "cannot compile task mappings" in current["contract_compile_error"]
    assert current["contract_compile_started_at"] is not None
    assert current["contract_compile_finished_at"] is not None


def test_duplicate_contract_compilation_is_rejected(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "compile-state.sqlite3")
    repository.initialize()
    batch = repository.create_batch("single compile")

    repository.start_contract_compilation(batch["id"])

    with pytest.raises(ValueError, match="正在编译"):
        repository.start_contract_compilation(batch["id"])
