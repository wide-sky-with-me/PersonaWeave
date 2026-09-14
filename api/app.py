"""FastAPI composition root: middleware, endpoint registration and static hosting."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.lifecycle import lifespan
from api.routes import faces, files, legacy_refinement, portraits, projects, refinement_sessions, swapping, system

load_dotenv()

app = FastAPI(
    title="PersonaWeave API",
    description="Industrial-Grade Batch Targeted Face Swapping & Harmonization RESTful API Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("PERSONAWEAVE_CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Register API routes before the catch-all Web Studio mount.
for router in (
    system.router,
    projects.router,
    swapping.router,
    faces.router,
    refinement_sessions.router,
    legacy_refinement.router,
    files.router,
    portraits.router,
):
    app.include_router(router)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    messages = "; ".join(f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors())
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": messages or "Request validation failed"},
    )


# In production/Docker, the built Web Studio bundle is served from this same process so the
# whole app is one deployable unit. In local dev the frontend instead runs as its own `vite`
# dev server (see scripts/dev.py) and this directory won't exist, so the mount is skipped.
_WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"
if _WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST_DIR, html=True), name="web-studio")
