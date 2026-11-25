"""Regenerate routes (single and batch results JSON)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from rq import Queue
from slowapi import Limiter

from respro.db.results import load_run_from_json
from web.backend.errors import user_facing_error_message
from web.backend.jobs import run_regenerate_json
from web.backend.models import (
    BatchRegenerateJsonPayload,
    BatchSubmitResponse,
    JobSubmitResponse,
    RegenerateJsonPayload,
)
from web.backend.project_dbs import resolve_regenerate_project_db_path
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue
from web.backend.rate_limit import consume_sample_quota
from web.backend.routes.common import (
    enqueue_batch_jobs,
    enqueue_single_job,
    get_deployment_mode,
    get_session,
    resolve_owned_file,
    validate_batch_lengths_and_cap,
)
from web.backend.startup_config import StartupConfig

logger = logging.getLogger(__name__)


def build_regenerate_router(
    *,
    config: StartupConfig,
    sample_limit_per_minute: int,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build single and batch regenerate-from-JSON routes."""
    router = APIRouter()
    upload_roots = (config.uploads_dir,)

    def _resolve_json_and_db(
        request: Request,
        *,
        json_id: str,
        session_hash: str,
        fallback_database_id: str | None,
        not_found_detail: str,
    ) -> tuple[Path, Path]:
        deployment_mode = get_deployment_mode(request) or config.deployment_mode
        json_path = resolve_owned_file(
            prefix='upload',
            record_id=json_id,
            session_hash=session_hash,
            allowed_roots=upload_roots,
            not_found_detail=not_found_detail,
            expected_file_type='json',
            deployment_mode=deployment_mode,
        )
        try:
            run_dict, _, _, _, _ = load_run_from_json(json_path)
            # Read mutable UUID index from app.state so maintained-DB refreshes stay visible.
            project_db = resolve_regenerate_project_db_path(
                config.project_databases_dir,
                request.app.state.project_db_uuid_index,
                project_fingerprint=str(run_dict.get('project_fingerprint', '') or ''),
                fallback_database_id=fallback_database_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=user_facing_error_message(str(exc))) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=user_facing_error_message(str(exc))) from exc
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Project database access failed during regenerate: %s', exc)
            raise HTTPException(status_code=400, detail='Project database is unavailable or corrupt.') from exc
        return json_path, project_db

    @router.post('/api/regenerate/json', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def regenerate_json_route(
        payload: RegenerateJsonPayload,
        request: Request,
        queue: Queue = Depends(get_queue),
    ) -> JobSubmitResponse:
        session = get_session(request)
        deployment_mode = get_deployment_mode(request) or config.deployment_mode
        json_path, project_db = _resolve_json_and_db(
            request,
            json_id=payload.json_id,
            session_hash=session.session_hash,
            fallback_database_id=payload.database_id,
            not_found_detail='JSON file not found.',
        )
        consume_sample_quota(request, sample_count=1, sample_limit_per_minute=sample_limit_per_minute)
        job_id = enqueue_single_job(
            queue=queue,
            session_hash=session.session_hash,
            upload_ids=[payload.json_id],
            job_func=run_regenerate_json,
            kwargs={
                'project_db': str(project_db),
                'output_dir': str(config.results_dir),
                'json_path': str(json_path),
            },
            enqueue_options=build_enqueue_job_options(),
            deployment_mode=deployment_mode,
        )
        logger.info('Queue job enqueued: job_id=%s mode=regenerate-json database_id=%s', job_id, project_db.name)
        return JobSubmitResponse(job_id=job_id)

    @router.post('/api/regenerate/batch', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def regenerate_batch_json_route(
        request: Request,
        payload: BatchRegenerateJsonPayload,
        queue: Queue = Depends(get_batch_queue),
    ) -> BatchSubmitResponse:
        session = get_session(request)
        deployment_mode = get_deployment_mode(request) or config.deployment_mode
        validate_batch_lengths_and_cap(
            input_ids=payload.json_ids,
            sample_names=payload.sample_names,
            input_display_names=payload.input_display_names,
            id_field='json_ids',
            max_batch=sample_limit_per_minute,
        )
        # Resolve every JSON and its project DB before quota burn or any enqueue.
        resolved_entries = [
            _resolve_json_and_db(
                request,
                json_id=json_id,
                session_hash=session.session_hash,
                fallback_database_id=payload.database_id,
                not_found_detail=f'JSON file not found for sample {sample_name!r}.',
            )
            for json_id, sample_name in zip(payload.json_ids, payload.sample_names)
        ]
        consume_sample_quota(
            request, sample_count=len(payload.json_ids), sample_limit_per_minute=sample_limit_per_minute,
        )
        job_specs = [
            (
                payload.sample_names[index],
                [payload.json_ids[index]],
                {
                    'project_db': str(project_db),
                    'output_dir': str(config.results_dir),
                    'json_path': str(json_path),
                },
            )
            for index, (json_path, project_db) in enumerate(resolved_entries)
        ]
        samples = enqueue_batch_jobs(
            queue=queue,
            session_hash=session.session_hash,
            job_func=run_regenerate_json,
            job_specs=job_specs,
            enqueue_options=build_enqueue_job_options(),
            deployment_mode=deployment_mode,
        )
        logger.info('Batch regenerate-json jobs enqueued: count=%d', len(samples))
        return BatchSubmitResponse(samples=samples, total=len(samples))

    return router
