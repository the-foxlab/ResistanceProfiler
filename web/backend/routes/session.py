"""Session cleanup routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter

from web.backend.models import (
    SessionCleanupPayload,
    SessionCleanupResponse,
    session_cleanup_payload_model,
)
from web.backend.routes.common import get_session
from web.backend.services.session import delete_owned_record, resolve_owned_path

logger = logging.getLogger(__name__)


def _delete_owned_batch(
    prefix: str,
    record_ids: list[str],
    session_hash: str,
    allowed_roots: tuple[Path, ...],
) -> int:
    """Delete caller-owned files for *record_ids*, silently skipping unknown/foreign IDs."""
    deleted = 0
    for record_id in record_ids:
        try:
            path = resolve_owned_path(
                prefix=prefix,
                record_id=record_id,
                session_hash=session_hash,
                allowed_roots=allowed_roots,
            )
        except (LookupError, ValueError):
            continue
        if path.is_file():
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning('Failed to delete %s %s: %s', prefix, record_id, exc)
        delete_owned_record(prefix=prefix, record_id=record_id)
    return deleted


def build_session_router(
    *,
    allowed_roots: tuple[Path, ...],
    artifact_max_ids: int,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build session-scoped cleanup routes."""
    router = APIRouter()

    def cleanup_session_uploads(
        request: Request,
        payload: SessionCleanupPayload,
    ) -> SessionCleanupResponse:
        session = get_session(request)
        if len(payload.artifact_ids) > artifact_max_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Too many artifact ids ({len(payload.artifact_ids)}). '
                    f'At most {artifact_max_ids} can be cleaned up at once.'
                ),
            )
        deleted = _delete_owned_batch('upload', payload.upload_ids, session.session_hash, allowed_roots)
        deleted += _delete_owned_batch('artifact', payload.artifact_ids, session.session_hash, allowed_roots)
        return SessionCleanupResponse(deleted_count=deleted)

    # Bind this app's payload bounds before FastAPI inspects annotations.
    cleanup_session_uploads.__annotations__['payload'] = session_cleanup_payload_model(artifact_max_ids)
    router.post('/api/session/cleanup', response_model=SessionCleanupResponse)(
        limiter.limit(api_rate_limit)(cleanup_session_uploads),
    )
    return router
