"""Job status polling and cancellation routes."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.results import Result
from rq.serializers import JSONSerializer
from slowapi import Limiter

from web.backend.artifact_kinds import ARTIFACT_MEDIA_TYPES, ARTIFACT_RESULT_FIELDS
from web.backend.errors import user_facing_error_message
from web.backend.models import JobStatusResponse
from web.backend.queue import get_queue
from web.backend.routes.common import get_session
from web.backend.services.session import (
    fetch_owned_record,
    owner_matches,
    record_artifact,
    update_owned_record_fields,
)

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


def _fetch_owned_job(job_id: str, session_hash: str, queue: Queue) -> Job:
    """Verify job ownership and fetch the RQ job, mapping missing/unowned jobs to 404."""
    if not owner_matches(fetch_owned_record('job', job_id), session_hash):
        raise HTTPException(status_code=404, detail='Job not found.')
    try:
        return Job.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail='Job not found.') from exc


def build_jobs_router(*, limiter: Limiter, api_rate_limit: str) -> APIRouter:
    """Build job inspection and cancellation routes."""
    router = APIRouter()

    @router.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
    @limiter.limit(api_rate_limit)
    def job_status(
        request: Request,
        job_id: str,
        queue: Queue = Depends(get_queue),
    ) -> JobStatusResponse:
        session = get_session(request)
        job = _fetch_owned_job(job_id, session.session_hash, queue)

        rq_status = job.get_status()
        status = _map_job_status(rq_status)
        result = job.return_value() if status == 'succeeded' else None
        error = user_facing_error_message(_job_exc_string(job)) if status == 'failed' else None
        if status == 'failed' and rq_status in ('stopped', 'canceled'):
            error = 'Job canceled by user.'
        if result is not None:
            result = _redact_result_paths(job_id, session.session_hash, result)
        return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)

    @router.delete('/api/jobs/{job_id}', status_code=204)
    @limiter.limit(api_rate_limit)
    def cancel_job(
        request: Request,
        job_id: str,
        queue: Queue = Depends(get_queue),
    ) -> Response:
        session = get_session(request)
        job = _fetch_owned_job(job_id, session.session_hash, queue)

        rq_status = job.get_status()
        if rq_status in ('queued', 'scheduled', 'deferred'):
            job.cancel()
        elif rq_status == 'started':
            kill_worker = getattr(job, 'kill_worker', None)
            if callable(kill_worker):
                kill_worker()
            else:
                # Fallback when worker-kill support is unavailable in the installed RQ version.
                Result.create_failure(job, ttl=job.failure_ttl or 0, exc_string='Job canceled by user.')
                job.set_status('failed')
                job.save()
        return Response(status_code=204)

    return router


def _validate_stored_artifact_ids(
    stored_ids: str,
    fields: list[str],
    result: dict,
    session_hash: str,
) -> list[str]:
    artifact_ids = stored_ids.split(',')
    if len(artifact_ids) != len(fields):
        raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
    for field, artifact_id in zip(fields, artifact_ids, strict=True):
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
        artifact = fetch_owned_record('artifact', artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail='Job artifacts unavailable.')
        if (
            not owner_matches(artifact, session_hash)
            or artifact.canonical_path != str(result[field])
            or artifact.fields.get('media_type') != ARTIFACT_MEDIA_TYPES[field]
        ):
            raise HTTPException(status_code=500, detail='Job artifacts unavailable.')
    return artifact_ids


def _record_job_artifacts(job_id: str, fields: list[str], result: dict, session_hash: str) -> list[str]:
    artifact_ids = [
        record_artifact(
            session_hash=session_hash,
            canonical_path=result[field],
            media_type=ARTIFACT_MEDIA_TYPES[field],
        )
        for field in fields
    ]
    update_owned_record_fields('job', job_id, {'artifact_ids': ','.join(artifact_ids)})
    return artifact_ids


def _redact_result_paths(job_id: str, session_hash: str, result: dict) -> dict:
    """Replace absolute artifact paths in a job result with session-owned opaque artifact IDs."""
    record = fetch_owned_record('job', job_id)
    if not owner_matches(record, session_hash):
        raise HTTPException(status_code=404, detail='Job not found.')
    fields = [field for field in ARTIFACT_RESULT_FIELDS if result.get(field)]
    stored_ids = record.fields.get('artifact_ids', '')
    artifact_ids = (
        _validate_stored_artifact_ids(stored_ids, fields, result, session_hash)
        if stored_ids
        else _record_job_artifacts(job_id, fields, result, session_hash)
    )
    return {
        **result,
        **dict(zip(fields, artifact_ids, strict=True)),
        'artifact_ids': artifact_ids,
    }
