"""Session cleanup routes."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from slowapi import Limiter

from web.backend.models import SessionCleanupPayload, SessionCleanupResponse
from web.backend.services.session import (
    Session,
    delete_owned_record,
    resolve_owned_path,
)

logger = logging.getLogger(__name__)


def build_session_router(
    *,
    uploads_dir: Path,
    results_dir: Path,
    allowed_roots: tuple[Path, ...],
    require_api_token: Callable[..., None],
    limiter: Limiter,
    api_rate_limit: str,
    get_session: Callable[..., Session],
) -> APIRouter:
    """Build session cleanup routes.

    Cleanup deletes only files the calling session owns, referenced by opaque
    ``upload_id`` / ``artifact_id`` tokens. Tokens that do not belong to the
    caller are silently skipped so that a foreign ID cannot be used to enumerate
    or delete another session's data.
    """
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

        session = get_session(request)
        deleted_count = 0
        for upload_id in payload.upload_ids:
            try:
                path = resolve_owned_path(
                    prefix='upload',
                    record_id=upload_id,
                    session_hash=session.session_hash,
                    allowed_roots=allowed_roots,
                )
            except (LookupError, ValueError):
                continue
            if path.is_file():
                try:
                    path.unlink()
                    deleted_count += 1
                except OSError as exc:
                    logger.warning('Failed to delete upload %s: %s', upload_id, exc)
            delete_owned_record(prefix='upload', record_id=upload_id)
        for artifact_id in payload.artifact_ids:
            try:
                path = resolve_owned_path(
                    prefix='artifact',
                    record_id=artifact_id,
                    session_hash=session.session_hash,
                    allowed_roots=allowed_roots,
                )
            except (LookupError, ValueError):
                continue
            if path.is_file():
                try:
                    path.unlink()
                    deleted_count += 1
                except OSError as exc:
                    logger.warning('Failed to delete artifact %s: %s', artifact_id, exc)
            delete_owned_record(prefix='artifact', record_id=artifact_id)
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
