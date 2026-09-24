"""Start one authorized AI turn from committed local outputs and the actual loaded reference gallery."""

import asyncio
import hashlib

from api.refinement.contracts import SessionCreateRequest, TurnCreateRequest
from core.refinement.image_ops import encode_image


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


def batch_session_request(task_id, request, result, source_references, prepared_mappings):
    """Use stable assignment outcomes, never guess that a person ID is a new image face index."""
    mapped_sources = {item["target_index"]: item["source_references"] for item in prepared_mappings}
    people = []
    for outcome in assigned_outcomes(result):
        person_id = outcome["person_index"]
        face_index = outcome.get("diagnostics", {}).get("matching", {}).get("face_index")
        gallery = mapped_sources.get(person_id, source_references)
        people.append(
            {
                "person_id": person_id,
                "face_index": face_index,
                "person_locator": f"本次原图中从左至右第 {face_index} 位人物；稳定身份编号 P{person_id}，请核验可见定位。",
                "source_manual_images": [encode_image(item["image"], lossless=True) for item in gallery],
            }
        )
    if not people:
        raise ValueError("The local result has no stable person assignments")
    # Task + target is immutable, so a retry after a lost response reuses the same creation key.
    request_id = "batch-" + hashlib.sha256(f"{task_id}:{result.client_target_id}".encode()).hexdigest()
    return SessionCreateRequest(
        request_id=request_id,
        target_id=result.client_target_id,
        title=result.client_target_id,
        original_image_url=result.original_url,
        baseline_image_url=result.processed_url,
        people=people,
    )


def create_batch_refinement_hook(sessions):
    """Bind session API commands to the local task completion boundary without another worker queue."""

    async def start(task_id, request, result, source_references, prepared_mappings, *, on_session_created=None):
        payload = await asyncio.to_thread(
            batch_session_request, task_id, request, result, source_references, prepared_mappings
        )
        session = await sessions.create_session(payload)
        result.refinement_session_id = session["id"]
        if on_session_created:
            on_session_created()  # Persist the association before waiting for a potentially slow paid turn.
        await sessions.submit_turn(
            session["id"],
            TurnCreateRequest(
                request_id=payload.request_id + "-auto",
                expected_revision=session["revision"],
                base_version_id=session["baseline_version_id"],
                mode="auto",
                message="对照原图、当前结果和人物参考板检查问题；有明确可修复问题时生成一个整图候选，无明确问题或人物对应不确定时停止并说明。",
            ),
        )
        # One batch must not fan out nine image-heavy AI conversations while local swapping continues.
        # Finish this authorized turn (including review_required), without waiting for human acceptance.
        await sessions.wait_until_idle(session["id"])

    return start
