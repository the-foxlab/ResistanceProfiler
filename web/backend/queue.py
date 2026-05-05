"""
Job queue factory for the web backend.

Provides a FastAPI-compatible dependency that creates an RQ queue connected to
Redis. Override `get_queue` in tests by injecting a queue backed by fakeredis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import redis
from rq import Queue, Retry

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV


@dataclass(frozen=True)
class QueueRuntimeSettings:
    """Queue runtime settings resolved from defaults and environment."""

    timeout_seconds: int
    retry_max: int
    retry_intervals_seconds: tuple[int, ...]


def build_enqueue_job_options() -> dict[str, int | Retry]:
    """Return standard timeout/retry options for all profiling queue submissions."""
    settings = resolve_queue_runtime_settings()
    options: dict[str, int | Retry] = {'job_timeout': settings.timeout_seconds}
    if settings.retry_max > 0:
        interval: int | list[int]
        if len(settings.retry_intervals_seconds) == 1:
            interval = settings.retry_intervals_seconds[0]
        else:
            interval = list(settings.retry_intervals_seconds)
        options['retry'] = Retry(max=settings.retry_max, interval=interval)
    return options


def get_queue() -> Queue:
    """Return the standard profiling RQ queue connected to Redis."""
    return _build_queue(WEB_BACKEND_CONFIG.defaults.profile_queue_name)


def get_batch_queue() -> Queue:
    """Return the batch profiling RQ queue connected to Redis."""
    return _build_queue(WEB_BACKEND_CONFIG.defaults.batch_queue_name)


def _build_queue(queue_name: str) -> Queue:
    """Create an RQ queue with shared runtime settings and Redis connection."""
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    runtime = resolve_queue_runtime_settings()
    connection = redis.Redis.from_url(redis_url)
    return Queue(
        queue_name,
        connection=connection,
        default_timeout=runtime.timeout_seconds,
    )


def resolve_queue_runtime_settings() -> QueueRuntimeSettings:
    """Resolve queue timeout/retry settings from environment with validated defaults."""
    defaults = WEB_BACKEND_CONFIG.defaults
    timeout_seconds = _parse_non_negative_int(
        os.getenv(WEB_ENV.job_timeout, str(defaults.job_timeout_seconds)),
        setting_name=WEB_ENV.job_timeout,
    )
    retry_max = _parse_non_negative_int(
        os.getenv(WEB_ENV.job_retry_max, str(defaults.job_retry_max)),
        setting_name=WEB_ENV.job_retry_max,
    )
    retry_intervals_seconds = _parse_retry_intervals(
        os.getenv(
            WEB_ENV.job_retry_intervals,
            ','.join(str(value) for value in defaults.job_retry_intervals_seconds),
        )
    )
    return QueueRuntimeSettings(
        timeout_seconds=timeout_seconds,
        retry_max=retry_max,
        retry_intervals_seconds=retry_intervals_seconds,
    )


def _parse_non_negative_int(raw_value: str, *, setting_name: str) -> int:
    """Parse a non-negative integer setting and fail fast on invalid values."""
    try:
        parsed = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(f'{setting_name} must be an integer value.') from exc
    if parsed < 0:
        raise ValueError(f'{setting_name} must be >= 0.')
    return parsed


def _parse_retry_intervals(raw_value: str) -> tuple[int, ...]:
    """Parse comma-separated retry intervals and enforce positive values."""
    entries = [item.strip() for item in raw_value.split(',') if item.strip()]
    if not entries:
        raise ValueError(f'{WEB_ENV.job_retry_intervals} must include at least one retry interval.')

    parsed: list[int] = []
    for entry in entries:
        try:
            interval = int(entry)
        except ValueError as exc:
            raise ValueError(
                f'{WEB_ENV.job_retry_intervals} must contain integer seconds values.'
            ) from exc
        if interval <= 0:
            raise ValueError(f'{WEB_ENV.job_retry_intervals} intervals must be > 0 seconds.')
        parsed.append(interval)
    return tuple(parsed)
