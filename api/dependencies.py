"""Request-scoped service access; model availability never gates stored image history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from api.refinement.legacy import LegacyRefinementArchive
    from core.refinement.conversation import ConversationRefinementService


def require_refinement_service(request: Request) -> ConversationRefinementService:
    """Require configured model access only for model actions and the live model catalog."""
    service = getattr(request.app.state, "refinement_service", None)
    if service is None:
        raise HTTPException(503, "AI refinement is not configured on this server")
    return service


def require_legacy_refinement_archive(request: Request) -> LegacyRefinementArchive:
    """Access retired-task history independently of optional AI credentials."""
    archive = getattr(request.app.state, "legacy_refinement_archive", None)
    if archive is None:
        raise HTTPException(503, "Historical refinement archive is unavailable")
    return archive
