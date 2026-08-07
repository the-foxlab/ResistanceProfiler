"""Regenerate routes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from rq import Queue
from slowapi import Limiter

from respro.db.results import load_run_from_json
from web.backend.jobs import run_regenerate_json
from web.backend.models import JobSubmitResponse, RegenerateJsonPayload
from web.backend.queue import build_enqueue_job_options, get_queue
from web.backend.services.session import (
    Session,
    record_job,
    resolve_owned_path,
)

logger = logging.getLogger(__name__)


def build_regenerate_router(
    *,
    config,
    require_api_token: Callable[..., None],
    user_facing_error_message: Callable[[str | None], str],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    resolve_regenerate_project_db_path,
    limiter: Limiter,
    api_rate_limit: str,
    get_session: Callable[..., Session],
) -> APIRouter:
    """Build regenerate-json route."""
    router = APIRouter()

    @router.post('/api/regenerate/json', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def regenerate_json_route(
        payload: RegenerateJsonPayload,
        request: Request,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        session = get_session(request)
        try:
            json_path = resolve_owned_path(
                prefix='upload',
                record_id=payload.json_id,
                session_hash=session.session_hash,
                allowed_roots=config.allowed_roots,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail='JSON file not found.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not json_path.is_file():
            raise HTTPException(status_code=404, detail='JSON file not found.')

        try:
            run_dict, _, _, _, _ = load_run_from_json(json_path)
            # Read the mutable UUID index from app state so a weekly maintained-DB
            # refresh (which assigns a new UUID) stays observable without mutating the
            # frozen StartupConfig.
            project_db_uuid_index = request.app.state.project_db_uuid_index
            project_db = resolve_regenerate_project_db_path(
                config.project_databases_dir,
                project_db_uuid_index,
                project_fingerprint=str(run_dict.get('project_fingerprint', '') or ''),
                fallback_database_id=payload.database_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=user_facing_error_message(str(exc))) from exc

        job = queue.enqueue(
            run_regenerate_json,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            json_path=str(json_path),
            **build_enqueue_job_options(),
        )
        record_job(session_hash=session.session_hash, upload_ids=[payload.json_id], job_id=job.id)
        logger.info(
            'Queue job enqueued: job_id=%s mode=regenerate-json database_id=%s',
            job.id,
            project_db.name,
        )
        return JobSubmitResponse(job_id=job.id)

    return router
