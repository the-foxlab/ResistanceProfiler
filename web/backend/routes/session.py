"""Session cleanup routes."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from slowapi import Limiter

from web.backend.models import SessionCleanupPayload, SessionCleanupResponse
from web.backend.services.upload import cleanup_session_files


def build_session_router(
    *,
    uploads_dir: Path,
    results_dir: Path,
    require_api_token: Callable[..., None],
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build session cleanup routes."""
    router = APIRouter()

    @router.post('/api/session/cleanup', response_model=SessionCleanupResponse)
    @limiter.limit(api_rate_limit)
    async def cleanup_session_uploads(
        request: Request,
        payload: SessionCleanupPayload,
        authorization: str | None = Header(default=None),
    ) -> SessionCleanupResponse:
        # Authorize via header OR body token so sendBeacon (which cannot
        # set headers) can still authenticate.
        config = request.app.state.startup_config
        if config.api_token:
            bearer_token = _extract_bearer_token(authorization)
            body_token = payload.token
            provided = bearer_token or body_token
            if not provided or not hmac.compare_digest(config.api_token, provided):
                raise HTTPException(status_code=401, detail='Unauthorized')

        deleted_count = cleanup_session_files(
            payload.upload_paths,
            payload.report_paths,
            uploads_dir,
            results_dir,
        )
        return SessionCleanupResponse(deleted_count=deleted_count)

    return router


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract bearer token from an Authorization header value."""
    if not authorization:
        return None
    parts = authorization.split(' ', 1)
    if len(parts) == 2 and parts[0].lower() == 'bearer':
        return parts[1].strip()
    return None
