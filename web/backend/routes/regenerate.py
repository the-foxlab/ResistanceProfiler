"""Regenerate routes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from rq import Queue
from slowapi import Limiter

from respro.db.results import load_run_from_json
from web.backend.jobs import run_regenerate_json
from web.backend.models import (
    BatchRegenerateJsonPayload,
    BatchSampleEntry,
    BatchSubmitResponse,
    JobSubmitResponse,
    RegenerateJsonPayload,
)
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue
from web.backend.routes.profile import (
    _resolve_batch_input_display_names,
)
from web.backend.services.session import (
    Session,
    record_job,
    resolve_owned_path,
)

logger = logging.getLogger(__name__)


def build_regenerate_router(
    *,
    config,
    sample_limit_per_minute: int,
    consume_sample_quota: Callable[[Request, int, int], None],
    user_facing_error_message: Callable[[str | None], str],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    resolve_regenerate_project_db_path,
    limiter: Limiter,
    api_rate_limit: str,
    get_session: Callable[..., Session],
) -> APIRouter:
    """Build regenerate-json routes (single + batch)."""
    router = APIRouter()

    @router.post('/api/regenerate/json', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def regenerate_json_route(
        payload: RegenerateJsonPayload,
        request: Request,
        queue: Queue = Depends(get_queue),
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

    @router.post('/api/regenerate/batch', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def regenerate_batch_json_route(
        request: Request,
        payload: BatchRegenerateJsonPayload,
        queue: Queue = Depends(get_batch_queue),
    ) -> BatchSubmitResponse:
        """Enqueue one regenerate-from-JSON job per uploaded results JSON.

        Unlike the VCF/FASTA batch routes, each JSON resolves its own project
        database via the stored ``project_fingerprint`` (with ``database_id`` as a
        shared fallback), so a single batch may regenerate reports against more
        than one database. The per-minute sample quota applies to the batch size.
        """
        session = get_session(request)
        if len(payload.json_ids) != len(payload.sample_names):
            raise HTTPException(
                status_code=422,
                detail='json_ids and sample_names must have the same length.',
            )
        # Resolve and validate display names (length check) even though the regenerate
        # job derives its output name from the uploaded JSON filename — this keeps the
        # input_display_names contract consistent with the VCF/FASTA batch routes.
        _resolve_batch_input_display_names(
            input_paths=payload.json_ids,
            input_display_names=payload.input_display_names,
            path_label='json_ids',
        )
        max_batch = sample_limit_per_minute
        if len(payload.json_ids) > max_batch:
            raise HTTPException(
                status_code=422,
                detail=f'Batch size {len(payload.json_ids)} exceeds the maximum of {max_batch} samples per batch.',
            )
        consume_sample_quota(
            request,
            sample_count=len(payload.json_ids),
            sample_limit_per_minute=sample_limit_per_minute,
        )

        # Resolve every uploaded JSON path and its project DB up front so a missing
        # file or unresolvable fingerprint fails the whole batch with a clear error
        # before any job is enqueued (no partial enqueue, mirroring VCF/FASTA batches).
        resolved_entries: list[tuple[Path, Path]] = []
        for index, json_id in enumerate(payload.json_ids):
            sample_name = payload.sample_names[index]
            try:
                json_path = resolve_owned_path(
                    prefix='upload',
                    record_id=json_id,
                    session_hash=session.session_hash,
                    allowed_roots=config.allowed_roots,
                )
            except LookupError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f'JSON file not found for sample {sample_name!r}.',
                ) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not json_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=f'JSON file not found for sample {sample_name!r}.',
                )
            try:
                run_dict, _, _, _, _ = load_run_from_json(json_path)
                project_db_uuid_index = request.app.state.project_db_uuid_index
                project_db = resolve_regenerate_project_db_path(
                    config.project_databases_dir,
                    project_db_uuid_index,
                    project_fingerprint=str(run_dict.get('project_fingerprint', '') or ''),
                    fallback_database_id=payload.database_id,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=user_facing_error_message(str(exc)),
                ) from exc
            resolved_entries.append((json_path, project_db))

        enqueue_options = build_enqueue_job_options()
        samples: list[BatchSampleEntry] = []
        for index, (json_path, project_db) in enumerate(resolved_entries):
            job_id = str(uuid4())
            queue.enqueue(
                run_regenerate_json,
                project_db=str(project_db),
                output_dir=str(config.results_dir),
                json_path=str(json_path),
                job_id=job_id,
                **enqueue_options,
            )
            record_job(
                session_hash=session.session_hash,
                upload_ids=[payload.json_ids[index]],
                job_id=job_id,
            )
            samples.append(BatchSampleEntry(job_id=job_id, sample_name=payload.sample_names[index]))
        logger.info(
            'Batch regenerate-json jobs enqueued: count=%d',
            len(samples),
        )
        return BatchSubmitResponse(samples=samples, total=len(samples))

    return router
