"""A legal JPEG can expand past the upload cap when frozen as a lossless PNG."""

import asyncio
import base64
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi import HTTPException

from api.projects.recovery import import_assets
from api.projects.store import ProjectStore
from api.refinement.contracts import SessionCreateRequest
from api.refinement.service import RefinementSessionService
from api.refinement.store import RefinementSessionStore
from api.routes.refinement_sessions import _call
from api.schemas import ProjectAssetRequest, ProjectSaveRequest
from core.ai.gateway import AIGateway
from core.image_security import MAX_IMAGE_BYTES, decode_image_bytes, decode_snapshot_bytes, encode_snapshot_image


@pytest.fixture(scope="module")
def large_photo():
    # High-frequency photographic input reproduces JPEG -> PNG expansion without
    # relaxing any production limit or depending on the user's private photograph.
    pixels = np.random.default_rng(42).integers(0, 256, (2800, 2700, 3), dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", pixels)
    assert ok and len(jpeg) < MAX_IMAGE_BYTES
    decoded = decode_image_bytes(jpeg.tobytes())
    png = encode_snapshot_image(decoded)
    assert len(png) > MAX_IMAGE_BYTES
    return decoded, jpeg.tobytes(), png


def test_large_snapshot_create_restart_publish_and_project_import(tmp_path, large_photo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pixels, jpeg, _ = large_photo
    uri = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    store = RefinementSessionStore(tmp_path / "sessions.db", tmp_path / "private", tmp_path / "accepted")
    service = RefinementSessionService(store=store)
    command = SessionCreateRequest(
        request_id=uuid4().hex,
        target_id="large-photo",
        original_image_url=uri,
        baseline_image_url=uri,
        people=[{"person_id": 1, "face_index": 1}],
    )
    detail = asyncio.run(service.create_session(command))
    version = next(v for v in detail["versions"] if v["id"] == detail["baseline_version_id"])
    session_id, digest = detail["id"], version["digest"]
    frozen = store.artifact_bytes(session_id, digest)
    assert len(frozen) > MAX_IMAGE_BYTES
    reloaded = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root)
    np.testing.assert_array_equal(reloaded.image(session_id, digest), pixels)
    accepted = reloaded.accept(session_id, detail["revision"], version["id"], digest)
    assert accepted["accepted_version_id"] == version["id"]
    assert next(store.accepted_root.glob("*.png")).read_bytes() == frozen

    projects = ProjectStore(tmp_path / "projects.db", tmp_path / "outputs/api_results/projects")
    project_id = projects.create()["id"]
    request = ProjectAssetRequest(image=store.artifact_url(session_id, digest))
    asset = import_assets(projects, project_id, request, SimpleNamespace(store=reloaded))[0]
    assert projects.asset(project_id, asset["$asset"]) == frozen
    document = {
        "schema": 1,
        "studio": {"targetImages": [{"id": "large-photo", "refinedUrl": asset}]},
        "workbench": {},
        "warnings": [],
    }
    projects.save(project_id, ProjectSaveRequest(expected_revision=0, title="Large", document=document))
    restored_projects = ProjectStore(projects.db_path, projects.assets_root)
    assert restored_projects.get(project_id)["document"] == document
    reloaded.delete(session_id, accepted["revision"])
    # Recovery uses an independent managed URL and can save again after session deletion.
    restored_source = f"/api/v1/files/projects/{project_id}/{asset['$asset']}"
    assert import_assets(projects, project_id, ProjectAssetRequest(image=restored_source), None) == [asset]
    # Owning a snapshot does not loosen ordinary uploads, even for the same PNG.
    with pytest.raises(ValueError, match="encoded byte limit"):
        projects.put_asset(project_id, frozen)


def test_gateway_keeps_large_internal_canvas_lossless_but_caps_external_bytes(large_photo):
    pixels, _, png = large_photo
    wire = AIGateway._image_bytes(pixels)
    assert len(wire) > MAX_IMAGE_BYTES
    np.testing.assert_array_equal(decode_snapshot_bytes(wire), pixels)
    with pytest.raises(ValueError, match="encoded byte limit"):
        AIGateway._image_bytes(png)
    with pytest.raises(ValueError, match="encoded byte limit"):
        AIGateway._image_bytes("data:image/png;base64," + base64.b64encode(png).decode())


def test_owned_snapshots_still_enforce_format_pixels_and_byte_bounds(monkeypatch):
    pixels = np.zeros((16, 16, 3), np.uint8)
    png = encode_snapshot_image(pixels)
    with pytest.raises(ValueError, match="PNG"):
        decode_snapshot_bytes(cv2.imencode(".jpg", pixels)[1].tobytes())
    monkeypatch.setattr("core.image_security.MAX_IMAGE_PIXELS", 255)
    with pytest.raises(ValueError, match="pixel limit"):
        encode_snapshot_image(pixels)
    with pytest.raises(ValueError, match="pixel limit"):
        decode_snapshot_bytes(png)
    monkeypatch.setattr("core.image_security.MAX_SNAPSHOT_BYTES", len(png) - 1)
    with pytest.raises(ValueError, match="encoded byte limit"):
        decode_snapshot_bytes(png)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Image exceeds the encoded byte limit", "20 MiB"),
        ("Original and baseline must share the same image canvas", "尺寸不一致"),
        ("Reference identity is unavailable", "素材已不可用"),
        ("secret api_key=private C:/private/photo.png", "Invalid refinement input"),
    ],
)
def test_refinement_input_errors_are_actionable_and_sanitized(message, expected):
    async def fail():
        raise ValueError(message)

    with pytest.raises(HTTPException) as result:
        asyncio.run(_call(fail()))
    assert result.value.status_code == 422
    assert expected in result.value.detail
    assert "private" not in result.value.detail
