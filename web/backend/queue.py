"""Job queue factory and RQ runtime settings for the web backend."""

from __future__ import annotations

import os
from dataclasses import dataclass

import redis
from rq import Queue, Retry
from rq.serializers import JSONSerializer

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, parse_csv_tokens, parse_int_env


@dataclass(frozen=True)
class QueueRuntimeSettings:
    """Queue timeout, retry, and result-TTL settings resolved from defaults and env."""

    timeout_seconds: int
    retry_max: int
    retry_intervals_seconds: tuple[int, ...]
    result_ttl_seconds: int


def build_enqueue_job_options() -> dict[str, int | Retry]:
    """Return timeout, retry, and TTL kwargs aligned to ``RESPRO_WEB_RESULT_TTL``."""
    settings = resolve_queue_runtime_settings()
    options: dict[str, int | Retry] = {
        'job_timeout': settings.timeout_seconds,
        'ttl': settings.result_ttl_seconds,
        'result_ttl': settings.result_ttl_seconds,
        'failure_ttl': settings.result_ttl_seconds,
    }
    if settings.retry_max > 0:
        intervals = settings.retry_intervals_seconds
        options['retry'] = Retry(
            max=settings.retry_max,
            interval=intervals[0] if len(intervals) == 1 else list(intervals),
        )
    return options


def get_queue() -> Queue:
    """Return the standard profiling RQ queue."""
    return _build_queue(WEB_BACKEND_CONFIG.defaults.profile_queue_name)


def get_batch_queue() -> Queue:
    """Return the batch profiling RQ queue."""
    return _build_queue(WEB_BACKEND_CONFIG.defaults.batch_queue_name)


def _build_queue(queue_name: str) -> Queue:
    """Build an RQ queue using ``JSONSerializer`` so Redis payloads cannot trigger pickle RCE."""
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    runtime = resolve_queue_runtime_settings()
    return Queue(
        queue_name,
        connection=redis.Redis.from_url(redis_url),
        default_timeout=runtime.timeout_seconds,
        serializer=JSONSerializer,
    )


def resolve_queue_runtime_settings() -> QueueRuntimeSettings:
    """Resolve and validate queue timeout, retry, and TTL settings from environment."""
    defaults = WEB_BACKEND_CONFIG.defaults
    default_intervals = ','.join(str(v) for v in defaults.job_retry_intervals_seconds)
    return QueueRuntimeSettings(
        timeout_seconds=parse_int_env(WEB_ENV.job_timeout, defaults.job_timeout_seconds, min_value=0, allow_empty=False),
        retry_max=parse_int_env(WEB_ENV.job_retry_max, defaults.job_retry_max, min_value=0, allow_empty=False),
        retry_intervals_seconds=_parse_retry_intervals(os.getenv(WEB_ENV.job_retry_intervals, default_intervals)),
        result_ttl_seconds=parse_int_env(WEB_ENV.result_ttl, defaults.result_ttl_seconds, min_value=1, allow_empty=False),
    )


def resolve_subprocess_timeout_seconds() -> int:
    """Return the hard CLI subprocess timeout (60s above the RQ job timeout)."""
    return resolve_queue_runtime_settings().timeout_seconds + 60


def _parse_retry_intervals(raw_value: str) -> tuple[int, ...]:
    entries = parse_csv_tokens(raw_value)
    if not entries:
        raise ValueError(f'{WEB_ENV.job_retry_intervals} must include at least one retry interval.')
    parsed: list[int] = []
    for entry in entries:
        try:
            interval = int(entry)
        except ValueError as exc:
            raise ValueError(f'{WEB_ENV.job_retry_intervals} must contain integer seconds values.') from exc
        if interval <= 0:
            raise ValueError(f'{WEB_ENV.job_retry_intervals} intervals must be > 0 seconds.')
        parsed.append(interval)
    return tuple(parsed)
