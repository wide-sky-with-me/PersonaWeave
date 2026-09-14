"""HTTP endpoints for portraits; application composition lives in api.app."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.portraits.library import build_portrait_library_catalog, resolve_asset_path

router = APIRouter()


@router.get("/api/v1/portrait-library", tags=["Portrait Library"])
async def get_portrait_library():
    """Return the indexed local male/female portrait identity corpus."""
    return {"code": 200, "data": build_portrait_library_catalog()}


@router.get("/api/v1/portrait-assets/{asset_id}/thumbnail", tags=["Portrait Library"])
async def get_portrait_asset_thumbnail(asset_id: str):
    """Serve a managed portrait asset's thumbnail, resolved by opaque ID via SQLite."""
    path = resolve_asset_path(asset_id, thumbnail=True)
    if path is None:
        raise HTTPException(status_code=404, detail="Portrait asset thumbnail not found")
    return FileResponse(path)


@router.get("/api/v1/portrait-assets/{asset_id}/content", tags=["Portrait Library"])
async def get_portrait_asset_content(asset_id: str):
    """Serve a managed portrait asset's original image, resolved by opaque ID via SQLite."""
    path = resolve_asset_path(asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Portrait asset not found")
    return FileResponse(path)
