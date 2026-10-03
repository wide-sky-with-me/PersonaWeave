"""Full editor persistence, owned media, conflict handling and honest legacy recovery."""

import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api.portraits.library import connect, ensure_layout
from api.projects.recovery import recover_record
from api.projects.store import ProjectConflict, ProjectStore
from api.routes.projects import router
from api.schemas import BatchTaskCreateRequest, ProjectSaveRequest, TaskDetailData
from core.memory_budget import MemorySnapshot


def png(color="red"):
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, "PNG")
    return output.getvalue()


def document(asset):
    return {
        "schema": 1,
        "studio": {
            "sourcePeople": [{"id": 7, "name": "Frozen reference"}],
            "sourceGallery": [{"id": "ref", "kind": "manual", "personId": 7, "imageData": asset}],
            "targetImages": [
                {
                    "id": "target",
                    "url": asset,
                    "faces": [{"index": 2, "global_person_id": 9}],
                    "manualMask": {"dataUrl": asset, "undoStack": [None, asset], "redoStack": [asset]},
                }
            ],
            "personMappings": [{"person_index": 9, "source_manual_images": [asset]}],
            "parameters": {"identity_blend_strength": 0.63},
        },
        "workbench": {
            "view": "refinement",
            "sessionId": "session",
            "selections": {"session": {"baseVersionId": "v1", "viewVersionId": "v2", "message": "Keep this draft"}},
        },
        "warnings": [],
    }


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects.sqlite3", tmp_path / "assets")


def save(store, project, doc, **kwargs):
    return store.save(
        project["id"], ProjectSaveRequest(expected_revision=project["revision"], title="工程", document=doc, **kwargs)
    )


def test_restart_preserves_entire_editor_and_list_is_lightweight(store):
    project = store.create()
    asset = store.put_asset(project["id"], png())
    doc = document(asset)
    saved = save(store, project, doc, batch_ids=["batch1"], session_ids=["session"])
    restored = ProjectStore(store.db_path, store.assets_root)
    assert restored.get(project["id"])["document"] == doc
    assert restored.linked("session", "session") == saved
    assert restored.linked("batch", "batch1") == saved
    assert restored.asset(project["id"], asset["$asset"]) == png()
    assert set(restored.list()[0]) == {"id", "title", "revision", "updated_at"}
    assert store.put_asset(project["id"], png()) == asset
    assert len(list((store.assets_root / project["id"]).iterdir())) == 1


def test_conflicts_and_missing_or_cross_project_media_leave_previous_document(store):
    first, second = store.create(), store.create()
    asset = store.put_asset(first["id"], png())
    saved = save(store, first, document(asset))
    with pytest.raises(ProjectConflict):
        save(store, first, document(asset))
    with pytest.raises(FileNotFoundError):
        save(store, second, document(asset))
    assert store.get(first["id"]) == saved
    with pytest.raises(ValueError):
        store.asset(first["id"], "../" + asset["$asset"])
    (store.assets_root / first["id"] / asset["$asset"]).write_bytes(png("blue"))
    with pytest.raises(ValueError, match="digest"):
        store.asset(first["id"], asset["$asset"])


def test_delete_only_removes_this_project_and_honors_revision(store):
    first, second = store.create(), store.create()
    a = store.put_asset(first["id"], png())
    b = store.put_asset(second["id"], png())
    saved = save(store, first, document(a), batch_ids=["batch1"])
    with pytest.raises(ProjectConflict):
        store.delete(first["id"], 0)
    store.delete(first["id"], saved["revision"])
    assert store.linked("batch", "batch1") is None
    assert store.asset(second["id"], b["$asset"]) == png()
    with pytest.raises(KeyError):
        store.get(first["id"])


def test_http_contract_safe_media_and_stale_save(store):
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    with TestClient(app) as client:
        project = client.post("/api/v1/projects").json()["data"]
        source = "data:image/png;base64," + base64.b64encode(png()).decode()
        asset = client.post(f"/api/v1/projects/{project['id']}/assets", json={"image": source}).json()["data"][0]
        payload = {"expected_revision": 0, "title": "Saved", "document": document(asset)}
        assert client.put(f"/api/v1/projects/{project['id']}", json=payload).status_code == 200
        assert client.put(f"/api/v1/projects/{project['id']}", json=payload).status_code == 409
        response = client.get(f"/api/v1/projects/{project['id']}/assets/{asset['$asset']}")
        assert response.content == png()
        assert response.headers["content-type"] == "image/png"
        assert "document" not in client.get("/api/v1/projects").json()["data"][0]
        assert client.post(f"/api/v1/projects/{project['id']}/assets", json={"image": "/etc/passwd"}).status_code == 422
        assert client.get("/api/v1/projects/not-an-id").status_code == 422
        assert client.post(f"/api/v1/projects/{project['id']}/assets", json={"image": "data:"}).status_code == 422


def test_low_host_memory_reports_actionable_503_for_project_media(store, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    monkeypatch.setattr(
        "core.memory_budget.memory_snapshot", lambda: MemorySnapshot(total=8 * 1024**3, available=256 * 1024**2)
    )
    with TestClient(app) as client:
        project = client.post("/api/v1/projects").json()["data"]
        source = "data:image/png;base64," + base64.b64encode(png()).decode()
        response = client.post(f"/api/v1/projects/{project['id']}/assets", json={"image": source})
    assert response.status_code == 503
    assert "等待可用内存" in response.json()["detail"]


def test_camera_mpo_survives_project_save_restore_and_serves_as_jpeg(store, camera_mpo_bytes):
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    app.state.task_manager = None
    with TestClient(app) as client:
        project = client.post("/api/v1/projects").json()["data"]
        endpoint = f"/api/v1/projects/{project['id']}"
        source = "data:image/jpeg;base64," + base64.b64encode(camera_mpo_bytes).decode()
        response = client.post(endpoint + "/assets", json={"image": source})
        assert response.status_code == 200, response.text
        asset = response.json()["data"][0]
        doc = document(asset)
        assert (
            client.put(endpoint, json={"expected_revision": 0, "title": "Camera", "document": doc}).status_code == 200
        )
        assert client.post(endpoint + "/restore").json()["data"]["document"] == doc
        media = client.get(endpoint + "/assets/" + asset["$asset"])
        assert media.headers["content-type"] == "image/jpeg"
        assert media.content == camera_mpo_bytes  # Preserve camera bytes without lossy re-encoding.


def test_project_import_reads_managed_image_under_unicode_directory(tmp_path, monkeypatch):
    root = tmp_path / "材料" / "图片换脸任务"
    image = root / "data" / "人物.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png())
    monkeypatch.chdir(root)
    store = ProjectStore()
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    with TestClient(app) as client:
        project = client.post("/api/v1/projects").json()["data"]
        response = client.post(f"/api/v1/projects/{project['id']}/assets", json={"image": "/api/v1/data/人物.png"})
        assert response.status_code == 200, response.text
        asset = response.json()["data"][0]
        content = client.get(f"/api/v1/projects/{project['id']}/assets/{asset['$asset']}").content
        with Image.open(BytesIO(content)) as restored:
            assert restored.size == (32, 32)
            assert restored.getpixel((0, 0)) == (255, 0, 0)


@pytest.fixture
def portrait_preview(tmp_path, monkeypatch):
    root = ensure_layout(tmp_path / "材料" / "素材库")
    monkeypatch.setenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", str(root))
    asset_id = "as_" + "a" * 20
    original, preview = root / "originals/original.png", root / "thumbnails/preview.webp"
    original.write_bytes(png("red"))
    stream = BytesIO()
    Image.new("RGB", (12, 12), "blue").save(stream, "WEBP", lossless=True)
    preview.write_bytes(stream.getvalue())
    with connect(root) as conn:
        conn.execute("INSERT INTO identities VALUES ('identity', 'source', 'female', 30, 'adult', 'Person', 'now')")
        conn.execute(
            "INSERT INTO assets VALUES (?, 'identity', 'angle', 'front', 'source.png', "
            "'originals/original.png', 'thumbnails/preview.webp', 'hash', 32, 32, 'now')",
            (asset_id,),
        )
    return root, asset_id, original, preview


def test_portrait_previews_and_originals_survive_project_save_and_restore(store, portrait_preview):
    _, asset_id, original, preview = portrait_preview
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    app.state.task_manager = None
    with TestClient(app) as client:
        project = client.post("/api/v1/projects").json()["data"]
        endpoint = f"/api/v1/projects/{project['id']}"
        assets = {}
        for rendition, file in [("thumbnail", preview), ("content", original)]:
            response = client.post(
                endpoint + "/assets", json={"image": f"/api/v1/portrait-assets/{asset_id}/{rendition}"}
            )
            assert response.status_code == 200, response.text
            assets[rendition] = response.json()["data"][0]
            assert store.asset(project["id"], assets[rendition]["$asset"]) == file.read_bytes()
        doc = document(assets["content"])
        doc["studio"]["sourceGallery"][0]["facePreview"] = assets["thumbnail"]
        assert client.put(endpoint, json={"expected_revision": 0, "title": "工程", "document": doc}).status_code == 200
        # The editor owns both copies; removing the library must not break restoration.
        original.unlink()
        preview.unlink()
        restored = client.post(endpoint + "/restore")
        assert restored.status_code == 200
        assert restored.json()["data"]["document"] == doc
        for asset in assets.values():
            assert client.get(endpoint + "/assets/" + asset["$asset"]).status_code == 200


@pytest.mark.parametrize("failure", ["missing", "outside"])
def test_portrait_preview_import_preserves_library_containment_and_saved_revision(store, portrait_preview, failure):
    root, asset_id, _original, preview = portrait_preview
    if failure == "missing":
        preview.unlink()
    else:
        with connect(root) as conn:
            conn.execute("UPDATE assets SET thumbnail_relpath='originals/original.png' WHERE id=?", (asset_id,))
    project = store.create()
    saved = save(store, project, document(store.put_asset(project["id"], png())))
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.refinement_sessions = None
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/projects/{project['id']}/assets",
            json={"image": f"/api/v1/portrait-assets/{asset_id}/thumbnail"},
        )
        assert response.status_code == 410
        assert str(root) not in response.text
        assert store.get(project["id"]) == saved


def test_old_batch_recovers_recorded_inputs_without_inventing_face_topology(store):
    source = "data:image/png;base64," + base64.b64encode(png()).decode()
    request = BatchTaskCreateRequest.model_validate(
        {
            "source_manual_images": [source],
            "target_reference_url": source,
            "person_index": 2,
            "batch_images": [{"client_target_id": "t", "image_url": source, "manual_mask": source}],
            "parameters": {"identity_blend_strength": 0.71},
        }
    )
    task = TaskDetailData.model_validate(
        {
            "task_id": "old",
            "status": "COMPLETED",
            "total_images": 1,
            "created_at": "2026-09-11T00:00:00Z",
            "progress": 1,
            "processed_images": 1,
            "success_count": 1,
            "failure_count": 0,
            "results": [],
        }
    )
    manager = SimpleNamespace(get_task=lambda _: task, get_request_snapshot=lambda _: request)
    recovered = recover_record(store, "batch", "old", manager, None)
    studio = recovered["document"]["studio"]
    assert studio["parameters"]["identity_blend_strength"] == 0.71
    assert studio["targetImages"][0]["manualMask"]["undoStack"] == []
    assert studio["targetImages"][0]["faces"] == []
    assert studio["personMappings"][0]["person_index"] == 2
    assert recovered["document"]["warnings"]
    assert recover_record(store, "batch", "old", manager, None)["id"] == recovered["id"]


def test_old_session_recovers_original_references_and_keeps_acceptance_separate(store):
    source = "data:image/png;base64," + base64.b64encode(png()).decode()
    detail = {
        "id": "s",
        "title": "Old session",
        "target_id": "t",
        "original_url": source,
        "baseline_version_id": "v0",
        "versions": [{"id": "v0", "image_url": source}],
        "accepted_version_id": "v0",
        "people": [{"person_id": 4, "face_index": 2, "reference_urls": [source]}],
    }
    sessions = SimpleNamespace(get=lambda _: detail)
    recovered = recover_record(store, "session", "s", None, sessions)
    doc = recovered["document"]
    assert doc["studio"]["targetImages"][0]["refinementSessionId"] == "s"
    assert doc["studio"]["personMappings"][0]["person_index"] == 4
    assert "accepted_version_id" not in doc["workbench"]
    assert doc["workbench"]["sessionId"] == "s"
    assert len(doc["studio"]["sourceGallery"]) == 1


def test_old_session_missing_remote_image_is_reported_without_discarding_available_inputs(store, monkeypatch):
    import httpx

    source = "data:image/png;base64," + base64.b64encode(png()).decode()

    def unavailable(_):
        raise httpx.ReadTimeout("private upstream URL must not enter warnings")

    monkeypatch.setattr("api.projects.recovery.load_image_from_source", unavailable)
    detail = {
        "id": "s",
        "title": "Partial",
        "target_id": "t",
        "original_url": source,
        "baseline_version_id": "v0",
        "versions": [{"id": "v0", "image_url": source}],
        "people": [{"person_id": 4, "face_index": 2, "reference_urls": [source, "https://missing.invalid/photo"]}],
    }
    recovered = recover_record(store, "session", "s", None, SimpleNamespace(get=lambda _: detail))
    assert len(recovered["document"]["studio"]["sourceGallery"]) == 1
    assert any("不可用" in warning for warning in recovered["document"]["warnings"])
    assert "private upstream" not in str(recovered)


def batch_with_assignments():
    """One confirmed person per image, with other batch identities explicitly unassigned."""
    sources = ["data:image/png;base64," + base64.b64encode(png(color)).decode() for color in ("red", "blue")]
    request = BatchTaskCreateRequest(
        person_mappings=[
            {
                "person_index": person_id,
                "target_reference_url": sources[0],
                "target_reference_person_index": 1,
                "source_manual_images": [sources[index]],
            }
            for index, person_id in enumerate((1, 4))
        ],
        batch_images=[{"client_target_id": f"t{index}", "image_url": sources[0]} for index in range(2)],
    )
    task = TaskDetailData(
        task_id="batch",
        status="PARTIAL",
        total_images=2,
        created_at="2026-09-11T00:00:00Z",
        results=[
            {
                "client_target_id": f"t{index}",
                "status": "PARTIAL",
                "original_url": sources[0],
                "processed_url": sources[1],
                "person_outcomes": [
                    {
                        "person_index": person_id,
                        "status": "SWAPPED" if person_id == selected else "SKIPPED",
                        "diagnostics": {"matching": {"face_index": 2 if person_id == selected else None}},
                    }
                    for person_id in (1, 4)
                ],
            }
            for index, selected in enumerate((1, 4))
        ],
    )
    return SimpleNamespace(get_task=lambda _: task, get_request_snapshot=lambda _: request)


def test_batch_recovery_can_create_refinement_from_recorded_per_image_assignments(store):
    from api.refinement.contracts import SessionCreateRequest

    manager = batch_with_assignments()
    recovered = recover_record(store, "batch", "batch", manager, None)
    targets = recovered["document"]["studio"]["targetImages"]
    for target, person_id, color in zip(targets, (1, 4), ("red", "blue"), strict=True):
        recipe = target["refinementInput"]
        assert recipe["baseline_image_url"] == target["processedUrl"]
        assert recipe["original_image_url"] == target["url"]
        assert len(recipe["people"]) == 1
        person = recipe["people"][0]
        assert (person["person_id"], person["face_index"]) == (person_id, 2)
        asset = person["source_manual_images"][0]
        assert store.asset(recovered["id"], asset["$asset"]) == png(color)
        assert target["faces"] == []

        def hydrate(value):
            if isinstance(value, dict):
                if "$asset" in value:
                    return (
                        "data:image/png;base64,"
                        + base64.b64encode(store.asset(recovered["id"], value["$asset"])).decode()
                    )
                return {key: hydrate(item) for key, item in value.items()}
            return [hydrate(item) for item in value] if isinstance(value, list) else value

        SessionCreateRequest(request_id="recovered", target_id=target["id"], **hydrate(recipe))


def test_already_restored_project_gets_missing_recipe_without_overwriting_user_edits(store):
    manager = batch_with_assignments()
    project = recover_record(store, "batch", "batch", manager, None)
    doc = project["document"]
    for target in doc["studio"]["targetImages"]:
        del target["refinementInput"]
    doc["studio"]["targetImages"][0]["name"] = "My edited title"
    doc["studio"]["parameters"]["identity_blend_strength"] = 0.55
    # A replaced result must not receive the old result's reference recipe.
    doc["studio"]["targetImages"][1]["processedUrl"] = store.put_asset(project["id"], png("green"))
    doc["workbench"]["selections"] = {"session": {"message": "Keep my draft"}}
    saved = save(store, project, doc)
    repaired = recover_record(store, "batch", "batch", manager, None)
    assert repaired["id"] == saved["id"]
    assert repaired["revision"] == saved["revision"] + 1
    assert repaired["document"]["studio"]["parameters"]["identity_blend_strength"] == 0.55
    assert repaired["document"]["workbench"] == doc["workbench"]
    assert repaired["document"]["studio"]["targetImages"][0]["name"] == "My edited title"
    assert repaired["document"]["studio"]["targetImages"][0]["refinementInput"]
    assert "refinementInput" not in repaired["document"]["studio"]["targetImages"][1]
    assert recover_record(store, "batch", "batch", manager, None)["revision"] == repaired["revision"]


@pytest.mark.parametrize("entry", ["project", "batch", "session"])
def test_all_restore_routes_repair_old_inputs_and_keep_existing_inputs(store, entry):
    manager = batch_with_assignments()
    project = recover_record(store, "batch", "batch", manager, None)
    doc = project["document"]
    del doc["studio"]["targetImages"][0]["refinementInput"]
    # An existing valid recipe and a draft must not be replaced during missing-input repair.
    doc["studio"]["targetImages"][1]["refinementInput"]["people"][0]["person_locator"] = "Previously frozen location"
    doc["workbench"]["selections"] = {"session": {"message": "Keep draft", "baseVersionId": "chosen"}}
    saved = save(store, project, doc, session_ids=["session"])
    app = FastAPI()
    app.include_router(router)
    app.state.projects = store
    app.state.task_manager = manager
    app.state.refinement_sessions = None
    url = (
        f"/api/v1/projects/{saved['id']}/restore" if entry == "project" else f"/api/v1/projects/recover/{entry}/{entry}"
    )
    with TestClient(app) as client:
        # Inspection is still read-only; only the explicit restore command repairs saved inputs.
        assert client.get(f"/api/v1/projects/{saved['id']}").json()["data"]["revision"] == saved["revision"]
        response = client.post(url)
        assert response.status_code == 200
        fixed = response.json()["data"]
        assert fixed["document"]["studio"]["targetImages"][0]["refinementInput"]
        assert fixed["document"]["studio"]["targetImages"][1] == doc["studio"]["targetImages"][1]
        assert fixed["document"]["workbench"] == doc["workbench"]
        assert client.post(url).json()["data"]["revision"] == fixed["revision"]


def test_single_source_without_requested_face_index_uses_actual_outcome_assignment(store):
    manager = batch_with_assignments()
    old = manager.get_request_snapshot("batch")
    request = BatchTaskCreateRequest(
        source_manual_images=old.person_mappings[0].source_manual_images,
        batch_images=old.batch_images,
    )
    assert request.person_index is None
    manager.get_request_snapshot = lambda _: request
    project = recover_record(store, "batch", "batch", manager, None)
    people = [t["refinementInput"]["people"][0] for t in project["document"]["studio"]["targetImages"]]
    assert [(p["person_id"], p["face_index"]) for p in people] == [(1, 2), (4, 2)]
    assert people[0]["source_manual_images"] == people[1]["source_manual_images"]


@pytest.mark.parametrize("missing", ["references", "assignment", "duplicate_person", "identity_membership"])
def test_unverifiable_execution_input_never_creates_an_invented_recipe(store, missing):
    manager = batch_with_assignments()
    request = manager.get_request_snapshot("batch")
    task = manager.get_task("batch")
    if missing == "references":
        request.person_mappings[0].source_manual_images = ["data:image/png;base64,broken"]
    elif missing == "identity_membership":
        request.person_mappings[0].source_manual_images = []
        request.person_mappings[0].source_identity_id = "not-frozen"
    elif missing == "assignment":
        task.results[0].person_outcomes[0]["diagnostics"]["matching"]["face_index"] = None
    else:
        task.results[0].person_outcomes.append(
            {"person_index": 1, "status": "SWAPPED", "diagnostics": {"matching": {"face_index": 3}}}
        )
    project = recover_record(store, "batch", "batch", manager, None)
    target = project["document"]["studio"]["targetImages"][0]
    assert target["processedUrl"]
    assert "refinementInput" not in target
    assert any("精修输入未补建" in warning for warning in project["document"]["warnings"])


def test_deleted_linked_task_does_not_break_independently_saved_project(store):
    from api.projects.recovery import restore_project

    manager = batch_with_assignments()
    project = recover_record(store, "batch", "batch", manager, None)
    missing = SimpleNamespace(get_task=lambda _: None, get_request_snapshot=lambda _: None)
    assert restore_project(store, project["id"], missing, None) == project


def test_different_lossless_encoding_of_identical_pixels_can_recover_recipe(store):
    from api.projects.recovery import restore_project

    manager = batch_with_assignments()
    project = recover_record(store, "batch", "batch", manager, None)
    target = project["document"]["studio"]["targetImages"][0]
    del target["refinementInput"]
    original_digest = target["url"]
    differently_encoded = BytesIO()
    Image.new("RGB", (32, 32), "red").save(differently_encoded, "PNG", compress_level=0)
    target["url"] = store.put_asset(project["id"], differently_encoded.getvalue())
    assert target["url"] != original_digest
    save(store, project, project["document"])
    restored = restore_project(store, project["id"], manager, None)
    recipe = restored["document"]["studio"]["targetImages"][0]["refinementInput"]
    assert recipe["original_image_url"] == original_digest


@pytest.mark.parametrize(
    "case", ["matching", "unreferenced_bystander", "wrong_reference", "changed_canvas", "ambiguous", "unaccepted"]
)
def test_recovers_only_a_unique_accepted_session_with_matching_execution_evidence(store, tmp_path, case):
    import asyncio

    from api.projects.recovery import restore_project
    from api.refinement.contracts import SessionCreateRequest
    from api.refinement.service import RefinementSessionService
    from api.refinement.store import RefinementSessionStore

    manager = batch_with_assignments()
    project = recover_record(store, "batch", "batch", manager, None)
    target = project["document"]["studio"]["targetImages"][0]
    recipe = target["refinementInput"]

    def image(asset):
        return "data:image/png;base64," + base64.b64encode(store.asset(project["id"], asset["$asset"])).decode()

    sessions = RefinementSessionService(
        store=RefinementSessionStore(
            tmp_path / "sessions.sqlite3",
            tmp_path / "sessions",
            tmp_path / "accepted",
        )
    )
    person = recipe["people"][0]
    refs = [image(asset) for asset in person["source_manual_images"]]
    if case == "wrong_reference":
        refs = ["data:image/png;base64," + base64.b64encode(png("green")).decode()]
    people = [{"person_id": person["person_id"], "face_index": person["face_index"], "source_manual_images": refs}]
    if case == "unreferenced_bystander":
        people.append({"person_id": 99, "face_index": 3})
    for index in range(2 if case == "ambiguous" else 1):
        session = asyncio.run(
            sessions.create_session(
                SessionCreateRequest(
                    request_id=f"session-{index}",
                    target_id=target["id"],
                    people=people,
                    original_image_url=image(recipe["original_image_url"]),
                    baseline_image_url=image(recipe["baseline_image_url"]),
                )
            )
        )
        if case != "unaccepted":
            baseline = session["versions"][0]
            sessions.store.accept(session["id"], session["revision"], baseline["id"], baseline["digest"])
    if case == "changed_canvas":
        target["processedUrl"] = store.put_asset(project["id"], png("green"))
        save(store, project, project["document"])
    restored = restore_project(store, project["id"], manager, sessions)
    associated = restored["document"]["studio"]["targetImages"][0].get("refinementSessionId")
    if case in {"matching", "unreferenced_bystander"}:
        assert associated == session["id"]
        assert store.linked("session", associated)["id"] == project["id"]
        assert restore_project(store, project["id"], manager, sessions)["revision"] == restored["revision"]
    else:
        assert associated is None
