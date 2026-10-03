"""Application startup and shutdown; owns shared model, archive and session instances."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.inference_limiter import InferenceLimiter, inference_concurrency_from_env
from api.projects.store import ProjectStore
from api.refinement.batch import create_batch_refinement_hook
from api.refinement.legacy import LegacyRefinementArchive
from api.refinement.service import RefinementSessionService
from api.tasks.batch import TaskManager
from core.pipeline import PersonaWeavePipeline
from core.refinement.conversation import ConversationRefinementService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize local inference and durable sessions independently of optional AI credentials."""
    print("[PersonaWeave API] Loading models and durable task state...")
    pipeline = PersonaWeavePipeline()
    inference_limiter = InferenceLimiter(inference_concurrency_from_env())
    task_manager = TaskManager(pipeline=pipeline, inference_limiter=inference_limiter)
    refinement_service = None
    try:
        refinement_service = ConversationRefinementService()
        refinement_service.gateway.validate_environment()
    except Exception:
        refinement_service = None
        # Provider/config exceptions can contain private connection data. Do not log them.
        print(
            "[PersonaWeave API] Optional AI services are unavailable; local swap and saved sessions remain available."
        )

    refinement_sessions = RefinementSessionService(ai=refinement_service, inference_limiter=inference_limiter)
    legacy_archive = LegacyRefinementArchive()
    task_manager.refinement_hook = create_batch_refinement_hook(refinement_sessions)
    app.state.inference_limiter = inference_limiter
    app.state.projects = ProjectStore()
    app.state.pipeline = pipeline
    app.state.task_manager = task_manager
    app.state.refinement_service = refinement_service
    app.state.refinement_sessions = refinement_sessions
    app.state.legacy_refinement_archive = legacy_archive
    print("[PersonaWeave API] Ready to serve inference requests.")
    try:
        yield
    finally:
        await refinement_sessions.shutdown()
        await legacy_archive.shutdown()
        print("[PersonaWeave API] Shutting down...")
