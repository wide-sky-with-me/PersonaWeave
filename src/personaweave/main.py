import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from personaweave.api import build_router
from personaweave.config import get_settings
from personaweave.storage import Repository


settings = get_settings()
legacy_asset_roots = (
    (settings.asset_legacy_root,)
    if settings.asset_legacy_root is not None
    else ()
)
repository = Repository(
    settings.database_path,
    formal_mask_root=settings.asset_root,
    formal_mask_read_roots=legacy_asset_roots,
)
repository.initialize()
repository.recover_interrupted_jobs()
repository.reconcile_terminal_batch_failures()
repository.recover_swap_result_assets()

app = FastAPI(
    title="PersonaWeave",
    version="0.1.0",
    description="Portable human-in-the-loop orchestration for batch face replacement.",
)
app.include_router(build_router(repository, settings))
app.mount(
    "/app",
    StaticFiles(directory=Path(__file__).with_name("static"), html=True),
    name="app",
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "PersonaWeave",
        "docs": "/docs",
        "app": "/app/",
        "health": "/api/v1/health",
    }


def run() -> None:
    uvicorn.run("personaweave.main:app", host="127.0.0.1", port=8000)
