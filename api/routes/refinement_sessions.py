"""Session commands, safe artifact bytes and resumable lightweight SSE notifications."""

import asyncio
import json
import sqlite3

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from api.refinement.contracts import (
    AcceptRequest,
    ArtifactUnavailable,
    CancelRequest,
    ModelUnavailable,
    SessionConflict,
    SessionCreateRequest,
    SessionEnvelope,
    SessionNotFound,
    SummaryEnvelope,
    TurnCreateRequest,
)
from api.tasks.provenance import batch_provenance
from core.image_security import resolve_managed_url

router = APIRouter(prefix="/api/v1/refinement/sessions", tags=["Refinement sessions"])

# Only these application-owned messages may cross the HTTP boundary. Arbitrary
# loader/provider exception text can include image bytes, paths or credentials.
_INPUT_ERRORS = {
    "Image exceeds the encoded byte limit": "输入图片超过 20 MiB 上传限制，请检查原图和人物参考。",
    "Image exceeds the pixel limit": "输入图片超过 4000 万像素限制，请缩小图片后重试。",
    "Original and baseline must share the same image canvas": "原图与本地换脸结果的尺寸不一致，请使用同一次换脸的结果。",
    "Session image evidence exceeds the decoded pixel budget": "原图、当前图和参考图的累计像素超过精修容量限制，请减少参考图或降低分辨率。",
    "Select at most eight reference images for each person": "每个人物最多使用 8 张精修参考图，请明确选择后重新执行本地换脸。",
    "A session may contain at most 64 reference images": "精修会话最多支持 64 张人物参考图，请减少参考图后重试。",
    "Reference identity is unavailable": "人物参考素材已不可用，请重新选择素材。",
    "Failed to read managed local image": "原图、换脸结果或人物参考文件无法读取，请检查图片是否仍然存在。",
}


def _service(request):
    service = getattr(request.app.state, "refinement_sessions", None)
    if service is None:
        raise HTTPException(503, "Refinement session storage is unavailable")
    return service


async def _call(operation):
    try:
        return await operation
    except SessionNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SessionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ArtifactUnavailable as exc:
        raise HTTPException(410, str(exc)) from None
    except ModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(
            422,
            _INPUT_ERRORS.get(
                str(exc), "Invalid refinement input; check image sources, references and model selection"
            ),
        ) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Refinement storage is unavailable") from None


@router.post("", response_model=SessionEnvelope)
async def create_session(body: SessionCreateRequest, request: Request):
    try:
        provenance = batch_provenance(getattr(request.app.state, "task_manager", None), body.baseline_image_url)
    except ValueError:
        raise HTTPException(404, "The referenced batch image snapshot is unavailable") from None
    if provenance is not None:
        if not provenance["original_url"]:
            raise HTTPException(404, "The original snapshot for this batch result is unavailable")
        if provenance["original_url"] != body.original_image_url:
            raise HTTPException(422, "Original image must match the immutable batch-result snapshot")
        try:
            original_path = resolve_managed_url(provenance["original_url"])
        except ValueError:
            raise HTTPException(404, "The original snapshot for this batch result is unavailable") from None
        if original_path is None:
            raise HTTPException(404, "The original snapshot for this batch result is unavailable")
    return {"code": 200, "data": await _call(_service(request).create_session(body))}


@router.get("", response_model=SummaryEnvelope)
async def list_sessions(request: Request, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return {"code": 200, "data": await _call(asyncio.to_thread(_service(request).list, limit, offset))}


@router.get("/{session_id}", response_model=SessionEnvelope)
async def get_session(session_id: str, request: Request):
    return {"code": 200, "data": await _call(asyncio.to_thread(_service(request).get, session_id))}


@router.post("/{session_id}/turns", response_model=SessionEnvelope)
async def submit_turn(session_id: str, body: TurnCreateRequest, request: Request):
    return {"code": 200, "data": await _call(_service(request).submit_turn(session_id, body))}


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request, expected_revision: int = Query(ge=1)):
    """Delete one idle session, including its versions, evidence and published files."""
    await _call(_service(request).delete(session_id, expected_revision))
    return Response(status_code=204)


@router.post("/{session_id}/cancel", response_model=SessionEnvelope)
async def cancel_turn(session_id: str, body: CancelRequest, request: Request):
    return {"code": 200, "data": await _call(_service(request).cancel(session_id, body.expected_revision))}


@router.post("/{session_id}/accept", response_model=SessionEnvelope)
async def accept_version(session_id: str, body: AcceptRequest, request: Request):
    return {"code": 200, "data": await _call(_service(request).accept(session_id, body))}


@router.get("/{session_id}/artifacts/{digest}")
async def get_artifact(session_id: str, digest: str, request: Request):
    # Return the byte buffer that was verified, not a FileResponse that re-opens a mutable path.
    content = await _call(asyncio.to_thread(_service(request).artifact_bytes, session_id, digest))
    return Response(
        content,
        media_type="image/png",
        headers={"ETag": f'"{digest}"', "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{session_id}/artifacts/{digest}/thumbnail")
async def get_thumbnail(session_id: str, digest: str, request: Request):
    content = await _call(_service(request).thumbnail_bytes(session_id, digest))
    return Response(
        content,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{session_id}/events")
async def session_events(
    session_id: str,
    request: Request,
    after: int = Query(0, ge=0, le=2**63 - 1),
    last_event_id: str | None = Header(None),
):
    service = _service(request)
    cursor = after
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
            if not 0 <= cursor <= 2**63 - 1:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Invalid event cursor") from None
    await _call(asyncio.to_thread(service.events, session_id, cursor))

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                events = await asyncio.to_thread(service.events, session_id, cursor)
            except SessionNotFound:
                yield "event: deleted\ndata: " + json.dumps({"session_id": session_id}) + "\n\n"
                return
            if events:
                for event in events:
                    cursor = event["id"]
                    # Clients fetch a new snapshot; events never carry images or conversation bodies.
                    payload = json.dumps(
                        {"session_id": session_id, "revision": event["revision"], "kind": event["kind"]}
                    )
                    yield f"id: {cursor}\nevent: snapshot\ndata: {payload}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
