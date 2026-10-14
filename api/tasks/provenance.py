"""Bind current refinement inputs to authoritative local batch results without leaking request payloads."""

import hashlib

from core.image_security import resolve_managed_url


def request_person_sources(request):
    """Enumerate the submitted source bindings, retaining single-source scope when no role matrix exists."""
    return (request.person_mappings or [request]) if request is not None else []


def assigned_outcomes(result):
    """Keep this image's recorded assignments; absent batch roles are not image people."""
    assigned = []
    for outcome in result.person_outcomes:
        face_index = outcome.get("diagnostics", {}).get("matching", {}).get("face_index")
        if face_index is None and outcome.get("status") == "SKIPPED":
            continue
        if not isinstance(face_index, int) or isinstance(face_index, bool) or face_index < 1:
            raise ValueError("A requested person lacks a confirmed face assignment")
        assigned.append(outcome)
    if not assigned:
        raise ValueError("The local result has no confirmed face assignments")
    return assigned


def describe_skipped_people(person_outcomes: list[dict]) -> str | None:
    """Turn stable per-person reason codes into one concise user-facing warning."""
    reason_text = {
        "no_faces_detected": "未检测到人脸",
        "identity_match_below_threshold": "未找到达到身份阈值的目标脸",
        "assignment_conflict": "目标脸已分配给匹配更强的另一人物，请检查人物映射是否重复",
        "target_reference_unavailable": "目标人物参考特征不可用，请重新选择代表图",
        "source_identity_unavailable": "源身份素材不可用",
        "extreme_pose_without_matching_reference": "目标为极端侧脸，但素材库缺少同方向侧脸参考",
        "no_visible_change": "模型输出未产生足够可见变化",
    }
    skipped = [item for item in person_outcomes if item.get("status") != "SWAPPED"]
    if not skipped:
        return None
    details = [
        f"人物 #{item.get('person_index')}：{reason_text.get(item.get('reason'), item.get('reason', '未完成'))}"
        for item in skipped
    ]
    return "；".join(details)


def batch_provenance(manager, swapped_url):
    """Bind a result to its server original, including aliases of the same controlled managed file."""
    if manager is None:
        return None
    results = [
        (task, result)
        for task in manager.tasks.values()
        for result in task.results
        if result.status in {"SUCCESS", "PARTIAL"} and result.processed_url
    ]
    selected = next(((task, result) for task, result in results if result.processed_url == swapped_url), None)
    if selected is None:
        requested_path = resolve_managed_url(swapped_url)
        if requested_path is None:
            return None
        for task, result in results:
            try:
                result_path = resolve_managed_url(result.processed_url)
            except ValueError:
                # An unrelated cleared historical result cannot invalidate this request.
                continue
            if requested_path == result_path:
                selected = task, result
                break
    if selected is None:
        return None
    task, result = selected
    request = manager.get_request_snapshot(task.task_id)
    mappings = request_person_sources(request)
    sources = [
        {
            "person_index": getattr(mapping, "person_index", None),
            "source_identity_id": mapping.source_identity_id,
            "source_asset_ids": mapping.source_asset_ids,
            "manual_count": len(mapping.source_manual_images),
            "manual_sha256": [hashlib.sha256(value.encode()).hexdigest() for value in mapping.source_manual_images],
        }
        for mapping in mappings
    ]
    return {
        "lineage": "batch-local",
        "batch_task_id": task.task_id,
        "client_target_id": result.client_target_id,
        "original_url": result.original_url
        if result.original_url and result.original_url.startswith("/api/v1/files/")
        else None,
        "original_provenance": "server-batch-result",
        "parameters": request.parameters.model_dump(mode="json") if request is not None else {},
        "sources": sources,
        "local_model_routing": result.model_routing,
    }
