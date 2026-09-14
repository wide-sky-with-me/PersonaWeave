"""HTTP endpoints for system; application composition lives in api.app."""

import os

from fastapi import APIRouter, Request

from api.dependencies import require_refinement_service
from api.schemas import (
    AIImageModelCatalog,
    AIImageModelItem,
    HealthResponse,
)
from utils.gpu_helper import get_optimal_providers

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["System"])
@router.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
async def health_check(request: Request):
    """Health check endpoint reporting pipeline readiness and execution providers."""
    providers = get_optimal_providers()
    provider_names = [p if isinstance(p, str) else p[0] for p in providers]
    pipeline_ready = hasattr(request.app.state, "pipeline") and request.app.state.pipeline is not None
    ai_refinement_ready = getattr(request.app.state, "refinement_service", None) is not None
    return HealthResponse(
        status="ok",
        app_name="PersonaWeave",
        version="0.1.0",
        pipeline_ready=pipeline_ready,
        providers=provider_names,
        gpu_available="CUDAExecutionProvider" in provider_names,
        ai_refinement_ready=ai_refinement_ready,
    )


@router.get("/api/v1/ai/models", response_model=AIImageModelCatalog, tags=["System"])
async def list_ai_image_models(request: Request):
    """Expose configured image aliases and non-secret provider metadata to the web client."""
    gateway = require_refinement_service(request).gateway
    return AIImageModelCatalog(
        default_alias=gateway.default_image_alias,
        models=[
            AIImageModelItem(
                alias=alias,
                provider="litellm",
                model=gateway.image_model_names(alias),
                display_name=config.display_name,
                description=config.description,
                is_default=alias == gateway.default_image_alias,
            )
            for alias, config in gateway.image_edits.items()
        ],
    )


@router.get("/api/v1/samples", tags=["System"])
async def get_sample_manifest():
    """Returns sample reference photos and target batch image paths."""
    sources = [
        {
            "name": "01.png (正脸)",
            "path": "data/test_target/01.png",
            "url": "/api/v1/data/test_target/01.png",
            "yaw": 0,
        },
        {
            "name": "02.png (微侧)",
            "path": "data/test_target/02.png",
            "url": "/api/v1/data/test_target/02.png",
            "yaw": 15,
        },
        {
            "name": "03.png (微侧)",
            "path": "data/test_target/03.png",
            "url": "/api/v1/data/test_target/03.png",
            "yaw": -15,
        },
    ]
    # Portable editions only ship curated fixtures; never advertise absent sample files.
    sources = [source for source in sources if os.path.isfile(source["path"])]
    targets = []
    test_img_dir = "data/test_images"
    if os.path.exists(test_img_dir):
        for fname in sorted(os.listdir(test_img_dir)):
            if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                targets.append(
                    {
                        "name": fname,
                        "path": f"data/test_images/{fname}",
                        "url": f"/api/v1/data/test_images/{fname}",
                    }
                )
    return {"sources": sources, "targets": targets}
