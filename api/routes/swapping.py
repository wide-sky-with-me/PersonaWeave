"""HTTP endpoints for swapping; application composition lives in api.app."""

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.inference_limiter import InferenceLimiter
from api.schemas import (
    BatchTaskCreateData,
    BatchTaskCreateRequest,
    BatchTaskCreateResponse,
    BatchTaskListResponse,
    SyncSwapData,
    SyncSwapRequest,
    SyncSwapResponse,
    TaskDetailResponse,
)
from api.tasks.batch import TaskManager

router = APIRouter()


@router.post(
    "/api/v1/batch-tasks",
    response_model=BatchTaskCreateResponse,
    status_code=status.HTTP_200_OK,
    tags=["Batch Swapping"],
)
async def submit_batch_task(req: BatchTaskCreateRequest, request: Request):
    """Submits a new batch face swapping task for asynchronous execution."""
    if req.ai_refinement:
        sessions = getattr(request.app.state, "refinement_sessions", None)
        if sessions is None or sessions.ai is None:
            raise HTTPException(
                status_code=503,
                detail="AI refinement is not configured; choose local-only or configure the AI services first",
            )
    task_manager: TaskManager = request.app.state.task_manager
    try:
        task_id, estimated_seconds = task_manager.create_batch_task(req)
        return BatchTaskCreateResponse(
            code=200,
            message="Batch task accepted successfully",
            data=BatchTaskCreateData(
                task_id=task_id,
                total_images=req.total_images,
                status=task_manager.get_task(task_id).status,
                estimated_seconds=estimated_seconds,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/api/v1/batch-tasks/{task_id}",
    response_model=TaskDetailResponse,
    tags=["Batch Swapping"],
)
async def get_batch_task_status(task_id: str, request: Request):
    """Retrieves current processing progress, status, and generated result image links for a batch task."""
    task_manager: TaskManager = request.app.state.task_manager
    task_data = task_manager.get_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return TaskDetailResponse(code=200, data=task_data)


@router.delete(
    "/api/v1/batch-tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Batch Swapping"],
)
async def delete_batch_task(task_id: str, request: Request):
    """Delete one terminal batch task and its persisted output artifacts."""
    task_manager: TaskManager = request.app.state.task_manager
    try:
        deleted = task_manager.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")


@router.get("/api/v1/batch-tasks", tags=["Batch Swapping"], deprecated=True)
async def list_recent_tasks(request: Request, limit: int = 50):
    """List complete tasks for backward compatibility; new clients use the history projection."""
    task_manager: TaskManager = request.app.state.task_manager
    return {"code": 200, "data": [task.model_dump(mode="json") for task in task_manager.list_tasks(limit=limit)]}


@router.get(
    "/api/v1/task-history/batch-tasks",
    response_model=BatchTaskListResponse,
    tags=["Batch Swapping"],
)
async def list_batch_task_history(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    """List lightweight persisted history; fetch one task for complete result details."""
    task_manager: TaskManager = request.app.state.task_manager
    return BatchTaskListResponse(data=task_manager.list_task_summaries(limit=limit))


@router.post(
    "/api/v1/swap-sync",
    response_model=SyncSwapResponse,
    status_code=status.HTTP_200_OK,
    tags=["Synchronous Swapping"],
)
async def swap_single_image_sync(req: SyncSwapRequest, request: Request):
    """Synchronously swaps a single image and returns the result immediately."""
    task_manager: TaskManager = request.app.state.task_manager
    inference_limiter: InferenceLimiter = request.app.state.inference_limiter
    try:
        result_dict = await inference_limiter.run(task_manager.execute_sync_swap, req)
        return SyncSwapResponse(
            code=200,
            message="Image swapped successfully",
            data=SyncSwapData(**result_dict),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
