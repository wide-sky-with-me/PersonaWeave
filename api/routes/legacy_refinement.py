"""Deprecated history routes: old candidates remain inspectable, but generation is retired."""

import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from api.dependencies import require_legacy_refinement_archive
from api.schemas import QualityEvaluationRequest, RefinementAcceptRequest, RefinementTaskResponse

router = APIRouter(tags=["Legacy Refinement"], deprecated=True)


@router.get("/api/v1/refinement/legacy-tasks")
async def list_legacy_tasks(request: Request, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return {"code": 200, "data": require_legacy_refinement_archive(request).list(limit, offset)}


@router.get("/api/v1/refinement/quality-summary")
async def refinement_quality_summary(request: Request):
    archive = require_legacy_refinement_archive(request)
    return {"code": 200, "data": archive.quality.summary(list(archive.tasks.values()))}


@router.post("/api/v1/refinement/review")
@router.post("/api/v1/refinement/plan")
@router.post("/api/v1/refinement/tasks")
@router.post("/api/v1/refinement/tasks/{task_id}/cancel")
@router.post("/api/v1/refinement/tasks/{task_id}/people/{person_id}/retry")
async def retired_generation():
    """Never translate a stale command into a new paid request or silently change its input."""
    raise HTTPException(410, "Single-task refinement retired; use /api/v1/refinement/sessions")


@router.get("/api/v1/refinement/tasks/{task_id}", response_model=RefinementTaskResponse)
async def get_refinement_task(task_id: str, request: Request):
    task = require_legacy_refinement_archive(request).get(task_id)
    if task is None:
        raise HTTPException(404, "Historical refinement task not found")
    return RefinementTaskResponse(data=task)


@router.get("/api/v1/refinement/tasks/{task_id}/candidate")
async def get_refinement_candidate(task_id: str, request: Request):
    path = require_legacy_refinement_archive(request).candidate_path(task_id)
    if path is None:
        raise HTTPException(404, "Historical candidate unavailable")
    return FileResponse(path)


@router.delete("/api/v1/refinement/tasks/{task_id}", status_code=204)
async def delete_refinement_task(task_id: str, request: Request, expected_revision: int = Query(ge=1)):
    """Remove a legacy record and its retained candidate, accepted image and quality evidence."""
    try:
        deleted = await require_legacy_refinement_archive(request).delete(task_id, expected_revision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Historical refinement storage is unavailable") from None
    if not deleted:
        raise HTTPException(404, "Historical refinement task not found")
    return Response(status_code=204)


@router.post("/api/v1/refinement/tasks/{task_id}/accept", response_model=RefinementTaskResponse)
async def accept_refinement_task(task_id: str, req: RefinementAcceptRequest, request: Request):
    try:
        task = await require_legacy_refinement_archive(request).accept(
            task_id, req.expected_revision, req.candidate_digest
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if task is None:
        raise HTTPException(404, "Historical refinement task not found")
    return RefinementTaskResponse(data=task)


@router.get("/api/v1/refinement/tasks/{task_id}/quality-evaluation")
async def get_refinement_quality(task_id: str, request: Request):
    result = require_legacy_refinement_archive(request).quality_detail(task_id)
    if result is None:
        raise HTTPException(404, "Historical refinement task not found")
    return {"code": 200, "data": result}


@router.put("/api/v1/refinement/tasks/{task_id}/quality-evaluation")
async def evaluate_refinement_quality(task_id: str, req: QualityEvaluationRequest, request: Request):
    try:
        result = await require_legacy_refinement_archive(request).evaluate_quality(task_id, req)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if result is None:
        raise HTTPException(404, "Historical refinement task not found")
    return {"code": 200, "data": result}


@router.get("/api/v1/refinement/tasks/{task_id}/evidence/{digest}")
async def get_refinement_evidence(task_id: str, digest: str, request: Request):
    archive = require_legacy_refinement_archive(request)
    path = archive.quality.path(task_id, digest) if archive.get(task_id) else None
    if path is None:
        raise HTTPException(404, "Historical evidence unavailable")
    return FileResponse(path)
