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
from web.backend.routes.common import get_deployment_mode, get_session
from web.backend.services.session import delete_owned_record, resolve_owned_path
from web.backend.startup_config import is_path_within_allowed_roots

logger = logging.getLogger(__name__)


def _delete_owned_batch(
    prefix: str,
    record_ids: list[str],
    session_hash: str,
    allowed_roots: tuple[Path, ...],
    *,
    uploads_dir: Path | None = None,
    deployment_mode: str | None = None,
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
                deployment_mode=deployment_mode,
            )
        except (LookupError, ValueError):
            continue
        unlinked_or_absent = False
        if path.is_file():
            try:
                path.unlink()
                deleted += 1
                unlinked_or_absent = True
            except OSError as exc:
                logger.warning('Failed to delete %s %s: %s', prefix, record_id, exc)
        elif not path.exists() and not path.is_symlink():
            unlinked_or_absent = True
        if unlinked_or_absent:
            if prefix == 'upload' and uploads_dir is not None and path.suffix.lower() == '.bam':
                bai_path = Path(f'{path}.bai')
                if (
                    not bai_path.is_symlink()
                    and bai_path.is_file()
                    and is_path_within_allowed_roots(bai_path, (uploads_dir,))
                ):
                    try:
                        bai_path.unlink()
                    except OSError as exc:
                        logger.warning('Failed to delete BAM index sidecar for %s %s: %s', prefix, record_id, exc)
            delete_owned_record(prefix=prefix, record_id=record_id, deployment_mode=deployment_mode)
    return deleted


def build_session_router(
    *,
    uploads_dir: Path,
    results_dir: Path,
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
        deployment_mode = get_deployment_mode(request)
        if len(payload.artifact_ids) > artifact_max_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Too many artifact ids ({len(payload.artifact_ids)}). '
                    f'At most {artifact_max_ids} can be cleaned up at once.'
                ),
            )
        deleted = _delete_owned_batch(
            'upload',
            payload.upload_ids,
            session.session_hash,
            (uploads_dir,),
            uploads_dir=uploads_dir,
            deployment_mode=deployment_mode,
        )
        deleted += _delete_owned_batch(
            'artifact',
            payload.artifact_ids,
            session.session_hash,
            (results_dir,),
            deployment_mode=deployment_mode,
        )
        return SessionCleanupResponse(deleted_count=deleted)

    # Bind this app's payload bounds before FastAPI inspects annotations.
    cleanup_session_uploads.__annotations__['payload'] = session_cleanup_payload_model(artifact_max_ids)
    router.post('/api/session/cleanup', response_model=SessionCleanupResponse)(
        limiter.limit(api_rate_limit)(cleanup_session_uploads),
    )
    return router
