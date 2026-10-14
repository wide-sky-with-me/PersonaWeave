"""Explicit, best-effort migration of records that predate complete editor snapshots."""

import base64
import hashlib
import re

import httpx
import numpy as np

from api.media import encode_image_to_base64, load_image_from_source
from api.portraits.library import resolve_asset_ids, resolve_asset_path, resolve_identity_asset_ids
from api.refinement.contracts import SessionPersonInput
from api.schemas import ProjectAssetRequest, ProjectSaveRequest
from api.tasks.provenance import assigned_outcomes, request_person_sources
from core.image_security import MAX_IMAGE_BYTES, decode_image_bytes, decode_snapshot_bytes, encode_snapshot_image
from core.refinement.contracts import MAX_PEOPLE

_SESSION_IMAGE = re.compile(r"^/api/v1/refinement/sessions/([a-f0-9]{32})/artifacts/([a-f0-9]{64})$")
_PORTRAIT_IMAGE = re.compile(r"^/api/v1/portrait-assets/(as_[a-f0-9]{20})/(thumbnail|content)$")


def _decode_project_asset(data):
    """ProjectStore has already verified ownership and digest of these persisted bytes."""
    return decode_snapshot_bytes(data) if len(data) > MAX_IMAGE_BYTES else decode_image_bytes(data)


def import_assets(store, project_id, request: ProjectAssetRequest, sessions):
    """Freeze original reference assets or controlled image inputs without contacting AI."""
    if request.identity_id is not None or request.asset_id is not None:
        ids = [request.asset_id] if request.asset_id else resolve_identity_asset_ids(request.identity_id)
        if not ids:
            raise ValueError("Reference identity is unavailable")
        paths = resolve_asset_ids(ids)
        if len(paths) != len(ids):
            raise ValueError("Reference asset is unavailable")
        return [store.put_asset(project_id, path.read_bytes()) for path in paths]
    source = request.image
    owned = re.fullmatch(r"/api/v1/files/projects/([a-f0-9]{32})/([a-f0-9]{64})", source)
    if owned and owned[1] == project_id:
        store.asset(project_id, owned[2])  # Verify existence and digest before reusing the reference.
        return [{"$asset": owned[2]}]
    portrait = _PORTRAIT_IMAGE.fullmatch(source)
    if portrait:
        # Editor previews are required snapshot media too. Resolve their opaque IDs
        # through the library's containment checks, never as arbitrary local paths.
        asset_id, rendition = portrait.groups()
        path = resolve_asset_path(asset_id, thumbnail=rendition == "thumbnail")
        if path is None:
            raise FileNotFoundError("Portrait image is unavailable")
        return [store.put_asset(project_id, path.read_bytes())]
    owned_snapshot = False
    if source.startswith("data:"):
        header, separator, payload = source.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
            raise ValueError("Invalid image data URI")
        data = base64.b64decode(payload, validate=True)
    else:
        match = _SESSION_IMAGE.fullmatch(source)
        image = sessions.store.image(*match.groups()) if match else load_image_from_source(source)
        data = encode_snapshot_image(image)
        owned_snapshot = True  # The loader already validated input; these PNG bytes were generated here.
    return [store.put_asset(project_id, data, owned_snapshot=owned_snapshot)]


def restore_project(store, project_id, task_manager, sessions):
    """Use one recovery path for project/history opening and refresh, without replaying model work."""
    project = store.get(project_id)
    for task_id in store.linked_records(project_id, "batch"):
        project = restore_batch_refinement_inputs(
            store, project, task_manager.get_task(task_id), task_manager.get_request_snapshot(task_id), sessions
        )
    return restore_accepted_session_links(store, project, sessions)


def restore_accepted_session_links(store, project, sessions):
    """Reconnect a unique accepted session only when both canvases and all identity references agree."""
    if sessions is None:
        return project
    doc = project["document"]
    links = []
    cache = {}

    def digest(asset):
        key = asset["$asset"]
        if key not in cache:
            pixels = _decode_project_asset(store.asset(project["id"], key))
            encoded = encode_image_to_base64(pixels, ".png").split(",", 1)[1]
            cache[key] = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
        return cache[key]

    for target in doc.get("studio", {}).get("targetImages", []):
        recipe = target.get("refinementInput")
        if target.get("refinementSessionId") or target.get("status") != "SUCCESS" or not recipe:
            continue
        try:
            if digest(target["url"]) != digest(recipe["original_image_url"]) or digest(
                target["processedUrl"]
            ) != digest(recipe["baseline_image_url"]):
                continue
            # Query by exact target and normalized canvas bytes, never by name or visual similarity.
            candidates = sessions.store.matching_accepted_sessions(
                target["id"], digest(recipe["original_image_url"]), digest(recipe["baseline_image_url"])
            )
            expected = {
                (p["person_id"], p["face_index"]): sorted(digest(image) for image in p.get("source_manual_images", []))
                for p in recipe["people"]
            }
            matches = []
            for candidate in candidates:
                actual = {
                    (p["person_id"], p["face_index"]): sorted(p["reference_digests"])
                    for p in candidate["people"]
                    if p["reference_digests"]
                }
                # Additional people with no identity references describe the same scene, not another identity binding.
                if actual == {key: refs for key, refs in expected.items() if refs} and actual:
                    matches.append(candidate["id"])
            if len(matches) == 1:
                target["refinementSessionId"] = matches[0]
                links.append(matches[0])
        except (ValueError, OSError, LookupError, TypeError):
            # Missing evidence cannot establish a new association; retain the saved local result.
            continue
    if not links:
        return project
    return store.save(
        project["id"],
        ProjectSaveRequest(
            expected_revision=project["revision"],
            title=project["title"],
            document=doc,
            session_ids=links,
        ),
    )


def recover_record(store, kind, record_id, task_manager, sessions):
    """Build a partial editor from facts only; do not redetect people or replay work."""
    linked = store.linked(kind, record_id)
    if linked:
        return restore_project(store, linked["id"], task_manager, sessions)
    if kind == "batch":
        task = task_manager.get_task(record_id)
        if task is None:
            raise KeyError(record_id)
        if task.status in {"QUEUED", "PROCESSING"}:
            raise ValueError("Wait for the batch task to finish before recovering it")
        request = task_manager.get_request_snapshot(record_id)
    elif kind == "session":
        session = sessions.get(record_id)
    else:
        raise ValueError("Unknown history kind")
    project = store.create()
    project_id = project["id"]
    warnings = ["旧记录没有完整工程快照：未提交意见、画笔撤销/重做和原画布视图无法恢复。"]

    def picture(source, label):
        if not source:
            warnings.append(f"{label}未保存。")
            return ""
        try:
            return import_assets(store, project_id, ProjectAssetRequest(image=source), sessions)[0]
        except (ValueError, OSError, LookupError, httpx.HTTPError):
            warnings.append(f"{label}文件不可用，未重建替代图片。")
            return ""

    def references(values, label):
        images = []
        try:
            if values.get("source_identity_id"):
                images += import_assets(
                    store, project_id, ProjectAssetRequest(identity_id=values["source_identity_id"]), sessions
                )
            for asset_id in values.get("source_asset_ids") or []:
                images += import_assets(store, project_id, ProjectAssetRequest(asset_id=asset_id), sessions)
        except (ValueError, OSError, KeyError):
            warnings.append(f"{label}部分人物素材不可用。")
        for source in values.get("source_manual_images") or []:
            frozen = picture(source, label)
            if frozen:
                images.append(frozen)
        return images

    studio = {"sourcePeople": [], "sourceGallery": [], "targetImages": [], "personMappings": []}
    workbench = {"view": "prepare", "sessionId": None, "targetId": None, "selections": {}}

    def add_person(person_id, images, name):
        studio["sourcePeople"].append({"id": person_id, "name": name})
        for index, image in enumerate(images):
            studio["sourceGallery"].append(
                {
                    "id": f"restored-{person_id}-{index}",
                    "personId": person_id,
                    "name": f"{name} 参考 {index + 1}",
                    "kind": "manual",
                    "imageData": image,
                }
            )

    try:
        if kind == "session":
            title = session["title"]
            original = picture(session["original_url"], "原图")
            baseline = next(v for v in session["versions"] if v["id"] == session["baseline_version_id"])
            target = {
                "id": session["target_id"],
                "name": title,
                "url": original,
                "faces": [],
                "processedUrl": picture(baseline["image_url"], "本地初稿"),
                "status": "IDLE",
                "refinementSessionId": record_id,
            }
            target["status"] = "SUCCESS" if target["processedUrl"] else "FAILED"
            studio["targetImages"].append(target)
            for person in session["people"]:
                # These URLs are the frozen individual source photos, not generated reference boards.
                images = [picture(url, f"P{person['person_id']} 参考") for url in person["reference_urls"]]
                images = [image for image in images if image]
                if images:
                    add_person(person["person_id"], images, f"人物 P{person['person_id']}")
                    studio["personMappings"].append(
                        {
                            "person_index": person["person_id"],
                            "target_reference_url": original,
                            "target_reference_person_index": person["face_index"],
                            "source_asset_ids": [],
                            "source_manual_images": images,
                        }
                    )
            warnings.append("精修记录未保存原换脸参数、检测脸框和保护遮罩；参数使用当前默认值，未重新推断人物。")
            workbench.update(view="refinement", sessionId=record_id)
        else:
            title = f"恢复任务 {record_id}"
            studio["parameters"] = request.parameters.model_dump(mode="json") if request else {}
            mappings = [m.model_dump(mode="json") for m in request_person_sources(request)]
            if request and not request.person_mappings:
                mappings[0]["target_reference_person_index"] = request.person_index
            for index, mapping in enumerate(mappings):
                images = references(mapping, f"角色 {index + 1}")
                if images:
                    add_person(index + 1, images, mapping.get("label") or f"角色 {index + 1}")
                ref_url = mapping.get("target_reference_url")
                if ref_url and images and mapping.get("person_index") is not None:
                    studio["personMappings"].append(
                        {
                            "person_index": mapping["person_index"],
                            "target_reference_url": picture(ref_url, "映射代表图"),
                            "target_reference_person_index": mapping["target_reference_person_index"],
                            "source_asset_ids": [],
                            "source_manual_images": images,
                            "source_mode": mapping.get("source_mode") or request.parameters.source_mode,
                        }
                    )
            results = {r.client_target_id: r for r in task.results}
            inputs = request.normalized_batch_images if request else []
            ids = [item.client_target_id for item in inputs] or list(results)
            for index, target_id in enumerate(ids):
                result = results.get(target_id)
                item = inputs[index] if inputs else None
                original = picture(
                    result.original_url if result and result.original_url else item.image_url if item else None,
                    f"图片 {index + 1} 原图",
                )
                target = {"id": target_id, "name": f"图片 {index + 1}", "url": original, "faces": [], "status": "IDLE"}
                if result:
                    if result.processed_url:
                        target["processedUrl"] = picture(result.processed_url, f"图片 {index + 1} 结果")
                    target["refinementSessionId"] = result.refinement_session_id
                    target["status"] = (
                        "SUCCESS"
                        if result.status in {"SUCCESS", "PARTIAL"} and target.get("processedUrl")
                        else "FAILED"
                    )
                    if result.error:
                        target["error"] = result.error
                mask = item.manual_mask if item else None
                mask = mask or (request.parameters.manual_mask if request else None)
                if mask:
                    target["manualMask"] = {
                        "dataUrl": picture(
                            mask if mask.startswith("data:") else "data:image/png;base64," + mask, "保护遮罩"
                        ),
                        "undoStack": [],
                        "redoStack": [],
                    }
                studio["targetImages"].append(target)
            studio.get("parameters", {}).pop("manual_mask", None)
            warnings.append("旧任务未保存检测脸框与原界面名称；保留请求中的人物对应，未自动检测或重新分配。")
            if request is None:
                warnings.append("原任务请求未保存，无法恢复素材、映射与换脸参数。")
        saved = store.save(
            project_id,
            ProjectSaveRequest(
                expected_revision=0,
                title=title,
                document={"schema": 1, "studio": studio, "workbench": workbench, "warnings": warnings},
                batch_ids=[record_id] if kind == "batch" else [],
                session_ids=[record_id] if kind == "session" else [],
            ),
        )
        return restore_project(store, saved["id"], task_manager, sessions) if kind == "batch" else saved
    except BaseException:
        # Only clean up the unfinished destination; never delete a successfully saved project on repair failure.
        if store.get(project_id)["revision"] == 0:
            store.delete(project_id, 0)
        raise


def restore_batch_refinement_inputs(store, project, task, request, sessions):
    """Repair missing recipes from persisted execution facts, including previously migrated projects.

    Both canvases must still match the recorded pixels. Current editor mappings and fresh
    detections cannot establish provenance for an old result. Only add missing input/diagnostic fields.
    """
    if task is None or request is None or task.status in {"QUEUED", "PROCESSING"}:
        return project
    doc = project["document"]
    project_id = project["id"]
    results = {item.client_target_id: item for item in task.results}
    sources = request_person_sources(request)
    mappings = {item.person_index: item.model_dump(mode="json") for item in sources} if request.person_mappings else {}
    # In the single-source API, one explicit source can apply to all recorded face assignments.
    # person_index is optional there; the outcome still supplies the actual stable ID and face index.
    single_source = sources[0].model_dump(mode="json") if not request.person_mappings else None
    changed = False
    references = {}

    def same_image(left, right):
        if left == right:
            return True
        if not isinstance(left, dict) or "$asset" not in left:
            return False
        # Uploaded JPEG/WebP and the task's lossless PNG can encode the same pixels differently.
        # Verify exact decoded pixels; visual similarity or dimensions alone cannot establish provenance.
        return np.array_equal(
            _decode_project_asset(store.asset(project_id, left["$asset"])),
            _decode_project_asset(store.asset(project_id, right["$asset"])),
        )

    for target in doc["studio"]["targetImages"]:
        result = results.get(target["id"])
        if not result or target.get("refinementInput") or target.get("refinementSessionId"):
            continue
        if not result.original_url or not result.processed_url or result.status not in {"SUCCESS", "PARTIAL"}:
            continue
        try:
            original = import_assets(store, project_id, ProjectAssetRequest(image=result.original_url), sessions)[0]
            baseline = import_assets(store, project_id, ProjectAssetRequest(image=result.processed_url), sessions)[0]
            if not same_image(target.get("url"), original) or not same_image(target.get("processedUrl"), baseline):
                # A user may have replaced either image since restoration; never attach a stale recipe.
                continue
            people = []
            for outcome in assigned_outcomes(result):
                person_id = outcome["person_index"]
                if person_id not in references:
                    mapping = mappings[person_id] if single_source is None else single_source
                    images = []
                    if mapping.get("source_identity_id"):
                        # An identity selection may have changed since execution. Only explicit content IDs
                        # or embedded source bytes can reconstruct immutable references without a session.
                        raise ValueError("Historical identity membership is not frozen")
                    for asset_id in mapping.get("source_asset_ids") or []:
                        images += import_assets(store, project_id, ProjectAssetRequest(asset_id=asset_id), sessions)
                    for image in mapping.get("source_manual_images") or []:
                        images += import_assets(store, project_id, ProjectAssetRequest(image=image), sessions)
                    if not images:
                        raise ValueError("Executed references are unavailable")
                    references[person_id] = images
                images = references[person_id]
                face_index = outcome["diagnostics"]["matching"]["face_index"]
                person = SessionPersonInput(
                    person_id=person_id,
                    face_index=face_index,
                    person_locator=f"本次原图中从左至右第 {face_index} 张已检测人脸；稳定人物 P{person_id}。对应来自本地任务执行记录，未重新检测或排序；其他人物身份未确认。",
                    source_manual_images=["frozen"] * len(images),
                ).model_dump(mode="json", exclude_defaults=True)
                person["source_manual_images"] = images
                people.append(person)
            if (
                len({p["face_index"] for p in people}) != len(people)
                or len({p["person_id"] for p in people}) != len(people)
                or len(people) > MAX_PEOPLE
            ):
                raise ValueError("Invalid recorded assignments")
            target["refinementInput"] = {
                "original_image_url": original,
                "baseline_image_url": baseline,
                "people": people,
            }
            warning = f"{target.get('name', target['id'])}：无法核实执行时的人物对应或完整参考素材，精修输入未补建。"
            if warning in doc["warnings"]:
                doc["warnings"].remove(warning)
            for key, value in {
                "personOutcomes": result.person_outcomes,
                "swapWarning": result.warning,
                "swappedPersonIds": result.swapped_person_indices,
                "facesDetected": result.faces_detected,
                "facesSwapped": result.faces_swapped,
            }.items():
                target.setdefault(key, value)
            changed = True
        except (ValueError, OSError, LookupError, httpx.HTTPError):
            warning = f"{target.get('name', target['id'])}：无法核实执行时的人物对应或完整参考素材，精修输入未补建。"
            if warning not in doc["warnings"]:
                doc["warnings"].append(warning)
                changed = True
    if not changed:
        return project
    return store.save(
        project_id,
        ProjectSaveRequest(expected_revision=project["revision"], title=project["title"], document=doc),
    )
