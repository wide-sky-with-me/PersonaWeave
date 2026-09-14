"""Explicit project save/recovery endpoints, independent of model execution."""

from io import BytesIO

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from PIL import Image

from api.projects.recovery import import_assets, recover_record, restore_project
from api.projects.store import ProjectConflict
from api.schemas import ProjectAssetRequest, ProjectSaveRequest

router = APIRouter(prefix="/api/v1/projects", tags=["Studio Projects"])


def checked(action):
    """Return safe project errors without exposing source paths or request payloads."""
    try:
        return action()
    except LookupError as exc:
        raise HTTPException(404, "Project or history record is unavailable") from exc
    except ProjectConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(410, "Project image is unavailable") from exc
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(422, "Project data or required image is invalid or unavailable") from exc


@router.get("")
def list_projects(request: Request, limit: int = Query(50, ge=1, le=100)):
    return {"data": request.app.state.projects.list(limit)}


@router.post("")
def create_project(request: Request):
    return {"data": request.app.state.projects.create()}


@router.post("/recover/{kind}/{record_id}")
def recover_project(kind: str, record_id: str, request: Request):
    return {
        "data": checked(
            lambda: recover_record(
                request.app.state.projects,
                kind,
                record_id,
                request.app.state.task_manager,
                request.app.state.refinement_sessions,
            )
        )
    }


@router.get("/{project_id}")
def get_project(project_id: str, request: Request):
    return {"data": checked(lambda: request.app.state.projects.get(project_id))}


@router.post("/{project_id}/restore")
def restore_saved_project(project_id: str, request: Request):
    """Open an editor with the same missing-input repair as either history entry."""
    return {
        "data": checked(
            lambda: restore_project(
                request.app.state.projects,
                project_id,
                request.app.state.task_manager,
                request.app.state.refinement_sessions,
            )
        )
    }


@router.put("/{project_id}")
def save_project(project_id: str, body: ProjectSaveRequest, request: Request):
    return {"data": checked(lambda: request.app.state.projects.save(project_id, body))}


@router.post("/{project_id}/assets")
def put_asset(project_id: str, body: ProjectAssetRequest, request: Request):
    return {
        "data": checked(
            lambda: import_assets(request.app.state.projects, project_id, body, request.app.state.refinement_sessions)
        )
    }


@router.get("/{project_id}/assets/{digest}")
def get_asset(project_id: str, digest: str, request: Request):
    data = checked(lambda: request.app.state.projects.asset(project_id, digest))
    with Image.open(BytesIO(data)) as image:
        mime = Image.MIME.get(image.format, "application/octet-stream")
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request, expected_revision: int = Query(ge=0)):
    checked(lambda: request.app.state.projects.delete(project_id, expected_revision))
