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
        json_path = Path(payload.json_path).expanduser().resolve()
        if not is_path_within_allowed_roots(json_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='JSON path is outside allowed upload/output directory.')
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
        logger.info(
            'Queue job enqueued: job_id=%s mode=regenerate-json database_id=%s',
            job.id,
            project_db.name,
        )
        return JobSubmitResponse(job_id=job.id)

    return router
