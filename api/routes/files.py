"""HTTP endpoints for files; application composition lives in api.app."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


def _safe_file(root: Path, subpath: str, not_found_detail: str):
    base = root.resolve()
    candidate = (base / subpath).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(candidate)


@router.get("/api/v1/files/{subpath:path}", tags=["Static Files"])
async def get_output_file(subpath: str):
    """Serve generated files without allowing paths outside the output root."""
    return _safe_file(Path("outputs/api_results"), subpath, "File not found")


@router.get("/api/v1/data/{subpath:path}", tags=["Static Files"])
async def get_data_file(subpath: str):
    """Serve sample files without allowing paths outside the data root."""
    return _safe_file(Path("data"), subpath, "Sample file not found")
