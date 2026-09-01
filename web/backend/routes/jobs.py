"""Job status and cancel routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from rq import Queue
from rq.job import Job
from rq.results import Result
from rq.serializers import JSONSerializer
from slowapi import Limiter

from web.backend.models import JobStatusResponse
from web.backend.queue import get_queue
from web.backend.services.session import (
    Session,
    fetch_owned_record,
    owner_matches,
    record_artifact,
)


def build_jobs_router(
    *,
    map_job_status: Callable[[str], str],
    user_facing_error_message: Callable[[str | None], str],
    job_class: type[Job],
    no_such_job_error: type[Exception],
    limiter: Limiter,
    api_rate_limit: str,
    get_session: Callable[..., Session],
) -> APIRouter:
    """Build job inspection and cancellation routes."""
    router = APIRouter()

    def _require_job_owner(job_id: str, session_hash: str) -> None:
        """Return 404 unless the session owns the job record.

        A missing or non-owned record both map to 404 so existence is not
        confirmed to non-owners.
        """
        record = fetch_owned_record('job', job_id)
        if not owner_matches(record, session_hash):
            raise HTTPException(status_code=404, detail='Job not found.')

    def _job_exc_string(job: Job) -> str | None:
        """Return the exception string from the job's latest result.

        Replaces the deprecated ``job.exc_info`` property (RQ >=2.0). Returns
        ``None`` when there is no result or the latest result is not a failure.
        """
        latest = job.latest_result()
        if latest is not None and latest.type == Result.Type.FAILED:
            return latest.exc_string
        return None

    @router.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
    @limiter.limit(api_rate_limit)
    def job_status(
        request: Request,
        job_id: str,
        queue: Queue = Depends(get_queue),
    ) -> JobStatusResponse:
        session = get_session(request)
        _require_job_owner(job_id, session.session_hash)
        try:
            job = job_class.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
        except no_such_job_error:
            raise HTTPException(status_code=404, detail='Job not found.')

        rq_status = job.get_status()
        status = map_job_status(rq_status)
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
        _require_job_owner(job_id, session.session_hash)
        try:
            job = job_class.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
        except no_such_job_error:
            raise HTTPException(status_code=404, detail='Job not found.')

        rq_status = job.get_status()
        if rq_status in ('queued', 'scheduled', 'deferred'):
            job.cancel()
            return Response(status_code=204)

        if rq_status == 'started':
            kill_worker = getattr(job, 'kill_worker', None)
            if callable(kill_worker):
                kill_worker()
                return Response(status_code=204)

            # Fallback when worker-kill support is unavailable in the installed RQ version.
            Result.create_failure(
                job,
                ttl=job.failure_ttl or 0,
                exc_string='Job canceled by user.',
            )
            job.set_status('failed')
            job.save()
            return Response(status_code=204)

        return Response(status_code=204)

    return router


# Mapping of result dict keys that carry artifact paths to the artifact media
# type used when recording them for ownership tracking.
_ARTIFACT_PATH_FIELDS = {
    'report_html_path': 'text/html',
    'report_json_path': 'application/json',
    'report_pdf_path': 'application/pdf',
    'report_tsv_path': 'text/tab-separated-values',
}


def _redact_result_paths(job_id: str, session_hash: str, result: dict) -> dict:
    """Replace absolute artifact paths in a job result with opaque artifact IDs.

    On first successful fetch, each artifact path is recorded under the owning
    session and the job record is updated with the resulting IDs. Subsequent
    fetches reuse the stored IDs. The absolute paths are never returned to the
    client.
    """
    record = fetch_owned_record('job', job_id)
    if record is None:
        return result
    stored_ids = record.fields.get('artifact_ids', '')
    if stored_ids:
        artifact_ids = [value for value in stored_ids.split(',') if value]
    else:
        artifact_ids = []
        for field, media_type in _ARTIFACT_PATH_FIELDS.items():
            path = result.get(field)
            if not path:
                continue
            artifact_id = record_artifact(
                session_hash=session_hash,
                canonical_path=path,
                media_type=media_type,
            )
            artifact_ids.append(artifact_id)
        # Persist the IDs on the job record so later polls don't re-record.
        _store_job_artifact_ids(job_id, artifact_ids)

    redacted = dict(result)
    id_iter = iter(artifact_ids)
    for field in _ARTIFACT_PATH_FIELDS:
        if field in redacted and redacted[field]:
            try:
                redacted[field] = next(id_iter)
            except StopIteration:  # pragma: no cover - defensive mismatch
                break
    return redacted


def _store_job_artifact_ids(job_id: str, artifact_ids: list[str]) -> None:
    """Persist the resolved artifact IDs back onto the job record."""
    from web.backend.services.session import _redis_connection  # local import to avoid cycle

    record = fetch_owned_record('job', job_id)
    if record is None:
        return
    mapping = dict(record.fields)
    mapping['artifact_ids'] = ','.join(artifact_ids)
    key = f'respro:job:{job_id}'
    client = _redis_connection()
    if client is not None:
        try:
            client.hset(key, mapping=mapping)
        except Exception:  # noqa: BLE001 — best-effort persistence of artifact IDs
            pass
    else:
        from web.backend.services.session import _MEMORY_OWNED_LOCK, _MEMORY_OWNED_STORE

        with _MEMORY_OWNED_LOCK:
            _MEMORY_OWNED_STORE[key] = mapping
