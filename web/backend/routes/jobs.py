"""Job status and cancel routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from rq import Queue
from rq.job import Job
from slowapi import Limiter

from web.backend.models import JobStatusResponse
from web.backend.queue import get_queue


def build_jobs_router(
    *,
    require_api_token: Callable[..., None],
    map_job_status: Callable[[str], str],
    user_facing_error_message: Callable[[str | None], str],
    job_class: type[Job],
    no_such_job_error: type[Exception],
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build job inspection and cancellation routes."""
    router = APIRouter()

    @router.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
    @limiter.limit(api_rate_limit)
    def job_status(
        request: Request,
        job_id: str,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobStatusResponse:
        del request
        try:
            job = job_class.fetch(job_id, connection=queue.connection)
        except no_such_job_error:
            raise HTTPException(status_code=404, detail='Job not found.')

        rq_status = job.get_status()
        status = map_job_status(rq_status)
        result = job.return_value() if status == 'succeeded' else None
        error = user_facing_error_message(job.exc_info) if status == 'failed' else None
        if status == 'failed' and rq_status in ('stopped', 'canceled'):
            error = 'Job canceled by user.'
        return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)

    @router.delete('/api/jobs/{job_id}', status_code=204)
    @limiter.limit(api_rate_limit)
    def cancel_job(
        request: Request,
        job_id: str,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> Response:
        del request
        try:
            job = job_class.fetch(job_id, connection=queue.connection)
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
            job.exc_info = 'Job canceled by user.'
            job.set_status('failed')
            job.save()
            return Response(status_code=204)

        return Response(status_code=204)

    return router
