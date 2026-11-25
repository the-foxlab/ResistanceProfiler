"""Shared route helpers for session lookup, owned-file resolution, and batch submission."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import redis
from fastapi import HTTPException, Request
from rq import Queue

from web.backend.models import BatchSampleEntry
from web.backend.services.session import (
    Session,
    delete_owned_record,
    record_job,
    resolve_owned_path,
)


def get_session(request: Request) -> Session:
    """Return the session resolved by the HTTP session middleware."""
    return cast(Session, request.state.session)


def get_deployment_mode(request: Request) -> str | None:
    """Return the configured deployment mode from ``app.state.startup_config``, if attached."""
    scope = getattr(request, 'scope', None)
    app = scope.get('app') if isinstance(scope, dict) else getattr(request, 'app', None)
    startup_config = getattr(getattr(app, 'state', None), 'startup_config', None)
    mode = getattr(startup_config, 'deployment_mode', None)
    return mode if isinstance(mode, str) else None


def resolve_owned_file(
    *,
    prefix: str,
    record_id: str,
    session_hash: str,
    allowed_roots: tuple[Path, ...],
    not_found_detail: str,
    require_file: bool = True,
    expected_file_type: str | None = None,
    deployment_mode: str | None = None,
) -> Path:
    """Resolve an opaque owned record ID to a path-confined file, translating errors to HTTP 404/400."""
    try:
        path = resolve_owned_path(
            prefix=prefix,
            record_id=record_id,
            session_hash=session_hash,
            allowed_roots=allowed_roots,
            expected_file_type=expected_file_type,
            deployment_mode=deployment_mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=not_found_detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if require_file and not path.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return path


def resolve_batch_display_names(
    *,
    input_ids: list[str],
    input_display_names: list[str] | None,
    id_field: str,
) -> list[str]:
    """Validate and return basenames for batch input display names."""
    if input_display_names is None:
        return [Path(item).name for item in input_ids]
    if len(input_display_names) != len(input_ids):
        raise HTTPException(
            status_code=422,
            detail=f'{id_field} and input_display_names must have the same length.',
        )
    return [Path(name).name for name in input_display_names]


def validate_batch_lengths_and_cap(
    *,
    input_ids: list[str],
    sample_names: list[str],
    input_display_names: list[str] | None,
    id_field: str,
    max_batch: int,
    extra_aligned: tuple[str, Sequence[Any] | None] | None = None,
) -> list[str]:
    """Validate batch list lengths, optional aligned lists, and max-batch cap; return display names."""
    if len(input_ids) != len(sample_names):
        raise HTTPException(status_code=422, detail=f'{id_field} and sample_names must have the same length.')
    if extra_aligned is not None:
        extra_field, extra_values = extra_aligned
        if extra_values is not None and len(extra_values) != len(input_ids):
            raise HTTPException(status_code=422, detail=f'{extra_field} and {id_field} must have the same length.')
    display_names = resolve_batch_display_names(
        input_ids=input_ids,
        input_display_names=input_display_names,
        id_field=id_field,
    )
    if len(input_ids) > max_batch:
        raise HTTPException(
            status_code=422,
            detail=f'Batch size {len(input_ids)} exceeds the maximum of {max_batch} samples per batch.',
        )
    return display_names


def enqueue_single_job(
    *,
    queue: Queue,
    session_hash: str,
    upload_ids: list[str],
    job_func: Callable[..., dict],
    kwargs: dict[str, Any],
    enqueue_options: dict[str, Any],
    deployment_mode: str | None = None,
) -> str:
    """Record job ownership before enqueueing, rolling back and mapping Redis errors to 503."""
    job_id = str(uuid4())
    record_job(
        session_hash=session_hash,
        upload_ids=upload_ids,
        job_id=job_id,
        deployment_mode=deployment_mode,
    )
    try:
        queue.enqueue(job_func, job_id=job_id, **kwargs, **enqueue_options)
    except (redis.RedisError, OSError) as exc:
        with contextlib.suppress(Exception):
            delete_owned_record('job', job_id, deployment_mode=deployment_mode)
        raise HTTPException(status_code=503, detail='Service temporarily unavailable.') from exc
    except BaseException:
        with contextlib.suppress(Exception):
            delete_owned_record('job', job_id, deployment_mode=deployment_mode)
        raise
    return job_id


def enqueue_batch_jobs(
    *,
    queue: Queue,
    session_hash: str,
    job_func: Callable[..., dict],
    job_specs: list[tuple[str, list[str], dict[str, Any]]],
    enqueue_options: dict[str, Any],
    deployment_mode: str | None = None,
) -> list[BatchSampleEntry]:
    """Record ownership before enqueueing each ``(sample_name, upload_ids, kwargs)`` entry."""
    samples: list[BatchSampleEntry] = []
    for sample_name, upload_ids, kwargs in job_specs:
        job_id = enqueue_single_job(
            queue=queue,
            session_hash=session_hash,
            upload_ids=upload_ids,
            job_func=job_func,
            kwargs=kwargs,
            enqueue_options=enqueue_options,
            deployment_mode=deployment_mode,
        )
        samples.append(BatchSampleEntry(job_id=job_id, sample_name=sample_name))
    return samples
