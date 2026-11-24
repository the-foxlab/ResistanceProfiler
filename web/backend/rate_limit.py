"""Rate limiting, CORS, proxy header settings, and per-minute sample quota accounting."""

from __future__ import annotations

import logging
import os
import threading
import time

import redis
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, env_str, parse_csv_tokens, parse_int_env

logger = logging.getLogger(__name__)

_SAMPLE_QUOTA_LOCK = threading.Lock()
_SAMPLE_QUOTA_COUNTER: dict[tuple[str, int], int] = {}


def resolve_cors_origins() -> list[str]:
    """Return configured CORS origins or safe local-development defaults."""
    origins = parse_csv_tokens(os.getenv(WEB_ENV.cors_origins, ''))
    return origins or list(WEB_BACKEND_CONFIG.defaults.cors_local_origins)


def resolve_upload_rate_limit() -> str:
    return env_str(WEB_ENV.upload_rate_limit, WEB_BACKEND_CONFIG.defaults.upload_rate_limit)


def resolve_api_rate_limit() -> str:
    return env_str(WEB_ENV.api_rate_limit, WEB_BACKEND_CONFIG.defaults.api_rate_limit)


def resolve_max_batch_size() -> int:
    return parse_int_env(
        WEB_ENV.max_batch_size,
        WEB_BACKEND_CONFIG.defaults.max_batch_size,
        min_value=1,
        error_cls=RuntimeError,
    )


def resolve_proxy_settings() -> tuple[bool, str]:
    """Enable trusted proxy forwarding only when ``RESPRO_WEB_TRUSTED_PROXIES`` is non-empty."""
    proxy_ips = parse_csv_tokens(os.getenv(WEB_ENV.trusted_proxies, ''))
    return (True, ','.join(proxy_ips)) if proxy_ips else (False, '')


def _current_window_minute() -> int:
    return int(time.time() // 60)


def _rate_limit_key(request: Request) -> str:
    """Use the client IP as the rate-limit identity."""
    host = request.client.host if request.client else ''
    return f'ip:{host or get_remote_address(request)}'


def create_rate_limiter() -> Limiter:
    """Create the shared SlowAPI limiter backed by Redis when ``REDIS_URL`` is set."""
    redis_url = os.getenv(WEB_ENV.redis_url, '').strip()
    return Limiter(key_func=_rate_limit_key, storage_uri=redis_url) if redis_url else Limiter(key_func=_rate_limit_key)


def handle_rate_limit_exceeded(_: Request, __: RateLimitExceeded) -> JSONResponse:
    """Return a 429 JSON error when a SlowAPI limit is exceeded."""
    return JSONResponse(status_code=429, content={'detail': 'Upload rate limit exceeded. Try again later.'})


def consume_sample_quota(request: Request, sample_count: int, sample_limit_per_minute: int) -> None:
    """Consume per-minute sample quota in Redis (falling back to process memory)."""
    if sample_count <= 0:
        return
    detail = (
        f'Sample rate limit exceeded. At most {sample_limit_per_minute} '
        'samples can be analyzed per minute.'
    )
    if sample_count > sample_limit_per_minute:
        raise HTTPException(status_code=429, detail=detail)

    identity = _rate_limit_key(request)
    window_minute = _current_window_minute()
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url).strip()
    if redis_url:
        try:
            client = redis.Redis.from_url(redis_url)
            redis_key = f'respro:sample_quota:{identity}:{window_minute}'
            total = client.incrby(redis_key, sample_count)
            client.expire(redis_key, 120)
            if total > sample_limit_per_minute:
                raise HTTPException(status_code=429, detail=detail)
            return
        except HTTPException:
            raise
        except (redis.RedisError, OSError, RuntimeError) as exc:
            logger.debug('Sample quota Redis check failed for identity %s: %s', identity, exc)

    with _SAMPLE_QUOTA_LOCK:
        stale_before = window_minute - 1
        for stale_key in [k for k in _SAMPLE_QUOTA_COUNTER if k[1] < stale_before]:
            del _SAMPLE_QUOTA_COUNTER[stale_key]
        counter_key = (identity, window_minute)
        total = _SAMPLE_QUOTA_COUNTER.get(counter_key, 0) + sample_count
        if total > sample_limit_per_minute:
            raise HTTPException(status_code=429, detail=detail)
        _SAMPLE_QUOTA_COUNTER[counter_key] = total
