"""Job status polling and cancellation routes."""

from __future__ import annotations

import contextlib
import re

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import Path as FastAPIPath
from rq import Queue
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job
from rq.results import Result
from rq.serializers import JSONSerializer
from slowapi import Limiter

from web.backend.artifact_kinds import ARTIFACT_MEDIA_TYPES, ARTIFACT_RESULT_FIELDS
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.errors import user_facing_error_message
from web.backend.models import JobStatusResponse
from web.backend.queue import get_queue
from web.backend.routes.common import get_deployment_mode, get_session
from web.backend.services.session import (
    claim_owned_record_field,
    delete_owned_record,
    fetch_owned_record,
    owner_matches,
    record_artifact,
    resolve_result_ttl_seconds,
)

_OPAQUE_ID_MAX_LEN = WEB_BACKEND_CONFIG.defaults.opaque_id_max_length
_JOB_STATUS_MAP: dict[str, str] = {
    'finished': 'succeeded',
    'started': 'running',
    'failed': 'failed',
    'stopped': 'failed',
    'canceled': 'failed',
}
_ARTIFACT_ID_RE = re.compile(r'[A-Za-z0-9_-]+')


def _map_job_status(rq_status: str) -> str:
    return _JOB_STATUS_MAP.get(rq_status, 'queued')


def _job_exc_string(job: Job) -> str | None:
    """Return the latest failure exception string (replaces deprecated ``job.exc_info``)."""
    latest = job.latest_result()
    return latest.exc_string if latest is not None and latest.type == Result.Type.FAILED else None


def _fetch_owned_job(
    job_id: str,
    session_hash: str,
    queue: Queue,
    *,
    deployment_mode: str | None = None,
) -> Job:
    """Verify job ownership and fetch the RQ job, mapping missing/unowned jobs to 404."""
    if not owner_matches(fetch_owned_record('job', job_id, deployment_mode=deployment_mode), session_hash):
        raise HTTPException(status_code=404, detail='Job not found.')
    try:
        return Job.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail='Job not found.') from exc
    except (redis.RedisError, OSError) as exc:
        raise HTTPException(status_code=503, detail='Service temporarily unavailable.') from exc


def build_jobs_router(*, limiter: Limiter, api_rate_limit: str) -> APIRouter:
    """Build job inspection and cancellation routes."""
    router = APIRouter()

    @router.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
    @limiter.limit(api_rate_limit)
    def job_status(
        request: Request,
        job_id: str = FastAPIPath(..., max_length=_OPAQUE_ID_MAX_LEN),
        queue: Queue = Depends(get_queue),
    ) -> JobStatusResponse:
        session = get_session(request)
        deployment_mode = get_deployment_mode(request)
        job = _fetch_owned_job(job_id, session.session_hash, queue, deployment_mode=deployment_mode)

        try:
            rq_status = job.get_status()
            status = _map_job_status(rq_status)
            result = job.return_value() if status == 'succeeded' else None
            error = user_facing_error_message(_job_exc_string(job)) if status == 'failed' else None
        except (redis.RedisError, OSError) as exc:
            raise HTTPException(status_code=503, detail='Service temporarily unavailable.') from exc
        if status == 'failed' and rq_status in ('stopped', 'canceled'):
            error = 'Job canceled by user.'
        if result is not None:
            result = _redact_result_paths(
                job_id,
                session.session_hash,
                result,
                deployment_mode=deployment_mode,
            )
        return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)

    @router.delete('/api/jobs/{job_id}', status_code=204)
    @limiter.limit(api_rate_limit)
    def cancel_job(
        request: Request,
        job_id: str = FastAPIPath(..., max_length=_OPAQUE_ID_MAX_LEN),
        queue: Queue = Depends(get_queue),
    ) -> Response:
        session = get_session(request)
        deployment_mode = get_deployment_mode(request)
        job = _fetch_owned_job(job_id, session.session_hash, queue, deployment_mode=deployment_mode)

        try:
            rq_status = job.get_status()
            if rq_status in ('queued', 'scheduled', 'deferred'):
                try:
                    job.cancel()
                except InvalidJobOperation:
                    job.refresh()
                    if job.get_status() == 'started':
                        with contextlib.suppress(InvalidJobOperation, NoSuchJobError):
                            send_stop_job_command(queue.connection, job_id, serializer=JSONSerializer)
            elif rq_status == 'started':
                with contextlib.suppress(InvalidJobOperation, NoSuchJobError):
                    send_stop_job_command(queue.connection, job_id, serializer=JSONSerializer)
        except (redis.RedisError, OSError) as exc:
            raise HTTPException(status_code=503, detail='Service temporarily unavailable.') from exc
        return Response(status_code=204)

    return router


def _validate_stored_artifact_ids(
    stored_ids: str,
    fields: list[str],
    result: dict,
    session_hash: str,
    *,
    deployment_mode: str | None = None,
) -> list[str]:
    artifact_ids = stored_ids.split(',')
    if len(artifact_ids) != len(fields):
        raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
    for field, artifact_id in zip(fields, artifact_ids, strict=True):
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
        artifact = fetch_owned_record('artifact', artifact_id, deployment_mode=deployment_mode)
        if artifact is None:
            raise HTTPException(status_code=404, detail='Job artifacts unavailable.')
        if (
            not owner_matches(artifact, session_hash)
            or artifact.canonical_path != str(result[field])
            or artifact.fields.get('media_type') != ARTIFACT_MEDIA_TYPES[field]
        ):
            raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
    return artifact_ids


def _record_job_artifacts(
    job_id: str,
    fields: list[str],
    result: dict,
    session_hash: str,
    *,
    deployment_mode: str | None = None,
) -> list[str]:
    if not fields:
        return []
    result_ttl = resolve_result_ttl_seconds()
    artifact_ids = [
        record_artifact(
            session_hash=session_hash,
            canonical_path=result[field],
            media_type=ARTIFACT_MEDIA_TYPES[field],
            ttl=result_ttl,
            deployment_mode=deployment_mode,
        )
        for field in fields
    ]
    if claim_owned_record_field(
        'job',
        job_id,
        'artifact_ids',
        ','.join(artifact_ids),
        ttl=result_ttl,
        deployment_mode=deployment_mode,
    ):
        return artifact_ids
    for artifact_id in artifact_ids:
        with contextlib.suppress(Exception):
            delete_owned_record('artifact', artifact_id, deployment_mode=deployment_mode)
    record = fetch_owned_record('job', job_id, deployment_mode=deployment_mode)
    if record is None or not owner_matches(record, session_hash):
        raise HTTPException(status_code=404, detail='Job not found.')
    stored_ids = record.fields.get('artifact_ids', '')
    if not stored_ids:
        raise HTTPException(status_code=404, detail='Job artifacts unavailable.')
    return _validate_stored_artifact_ids(
        stored_ids,
        fields,
        result,
        session_hash,
        deployment_mode=deployment_mode,
    )


def _redact_result_paths(
    job_id: str,
    session_hash: str,
    result: dict,
    *,
    deployment_mode: str | None = None,
) -> dict:
    """Replace absolute artifact paths in a job result with session-owned opaque artifact IDs."""
    record = fetch_owned_record('job', job_id, deployment_mode=deployment_mode)
    if record is None or not owner_matches(record, session_hash):
        raise HTTPException(status_code=404, detail='Job not found.')
    fields = [field for field in ARTIFACT_RESULT_FIELDS if result.get(field)]
    stored_ids = record.fields.get('artifact_ids', '')
    artifact_ids = (
        _validate_stored_artifact_ids(
            stored_ids,
            fields,
            result,
            session_hash,
            deployment_mode=deployment_mode,
        )
        if stored_ids
        else _record_job_artifacts(
            job_id,
            fields,
            result,
            session_hash,
            deployment_mode=deployment_mode,
        )
    )
    return {
        **result,
        **dict(zip(fields, artifact_ids, strict=True)),
        'artifact_ids': artifact_ids,
    }
