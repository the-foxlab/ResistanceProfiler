"""Session cleanup routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends

from web.backend.models import SessionCleanupPayload, SessionCleanupResponse
from web.backend.services.upload import cleanup_session_files


def build_session_router(
    *,
    uploads_dir: Path,
    results_dir: Path,
    require_api_token: Callable[..., None],
) -> APIRouter:
    """Build session cleanup routes."""
    router = APIRouter()

    @router.post('/api/session/cleanup', response_model=SessionCleanupResponse)
    def cleanup_session_uploads(
        payload: SessionCleanupPayload,
        _auth: None = Depends(require_api_token),
    ) -> SessionCleanupResponse:
        deleted_count = cleanup_session_files(
            payload.upload_paths,
            payload.report_paths,
            uploads_dir,
            results_dir,
        )
        return SessionCleanupResponse(deleted_count=deleted_count)

    return router
