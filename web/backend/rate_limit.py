"""Rate limiting, CORS, proxy header settings, and per-minute sample quota accounting."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, cast

import redis
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
    env_str,
    parse_csv_tokens,
    parse_int_env,
    redact_redis_url,
    sanitize_redis_client_error,
)
from web.backend.services.session import get_redis_client, invalidate_redis_pool
from web.backend.services.session import reset_redis_pool as reset_redis_pool

logger = logging.getLogger(__name__)

_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
_REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
_SAMPLE_QUOTA_WINDOW_TTL_SECONDS = 120

_SAMPLE_QUOTA_LOCK = threading.Lock()
_SAMPLE_QUOTA_COUNTER: dict[tuple[str, int], int] = {}

_SAMPLE_QUOTA_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local sample_count = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if current + sample_count > limit then
    return {0, current}
end
local updated = redis.call('INCRBY', KEYS[1], sample_count)
redis.call('EXPIRE', KEYS[1], ttl)
return {1, updated}
"""


def _is_lua_unsupported_error(exc: redis.ResponseError) -> bool:
    message = str(exc).lower()
    return 'unknown command' in message and 'eval' in message


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


def _sanitize_rate_limiter_error_message(raw_msg: str, redis_url: str, storage_uri: str) -> str:
    sanitized = sanitize_redis_client_error(raw_msg, redis_url)
    if storage_uri != redis_url:
        sanitized = sanitize_redis_client_error(sanitized, storage_uri)
    redacted_url = redact_redis_url(redis_url)
    has_creds = redacted_url != redis_url or redact_redis_url(storage_uri) != storage_uri
    if has_creds and ('location ' in sanitized and '@' in sanitized):
        return f'Invalid rate limiter storage configuration for {redacted_url}'
    if has_creds and sanitized.startswith('Invalid Redis configuration for '):
        return f'Invalid rate limiter storage configuration for {redacted_url}'
    return sanitized


def create_rate_limiter() -> Limiter:
    """Create the shared SlowAPI limiter backed by Redis when ``REDIS_URL`` is set."""
    redis_url = os.getenv(WEB_ENV.redis_url, '').strip()
    if redis_url:
        storage_uri = f'redis+unix://{redis_url[7:]}' if redis_url.lower().startswith('unix://') else redis_url
        try:
            return Limiter(
                key_func=_rate_limit_key,
                storage_uri=storage_uri,
                in_memory_fallback_enabled=True,
                storage_options=cast(
                    dict[str, str],
                    {
                        'socket_connect_timeout': _REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS,
                        'socket_timeout': _REDIS_SOCKET_TIMEOUT_SECONDS,
                    },
                ),
            )
        except Exception as exc:
            redacted_msg = _sanitize_rate_limiter_error_message(str(exc), redis_url, storage_uri)
            try:
                redacted_exc: Exception = type(exc)(redacted_msg)
            except Exception:  # noqa: BLE001 — fallback when exception class requires non-str args
                redacted_exc = RuntimeError(redacted_msg)
            raise redacted_exc from None
    return Limiter(key_func=_rate_limit_key, in_memory_fallback_enabled=True)


def _resolve_slowapi_retry_after(request: Request, exc: Exception) -> int:
    try:
        limiter = getattr(getattr(request.app, 'state', None), 'limiter', None)
        view_limit = getattr(getattr(request, 'state', None), 'view_rate_limit', None)
        if limiter is not None and view_limit is not None:
            window_stats = limiter.limiter.get_window_stats(view_limit[0], *view_limit[1])
            reset_at = float(window_stats[0]) + 1.0
            return max(1, int(math.ceil(reset_at - time.time())))
    except Exception:  # noqa: BLE001 — fall back to limit expiry or 60s
        pass
    if isinstance(exc, RateLimitExceeded) and exc.limit is not None:
        rate_limit_item = getattr(exc.limit, 'limit', None)
        if rate_limit_item is not None and hasattr(rate_limit_item, 'get_expiry'):
            try:
                return max(1, int(math.ceil(float(rate_limit_item.get_expiry()))))
            except (TypeError, ValueError):
                pass
    return 60


def handle_rate_limit_exceeded(request: Request, exc: Exception) -> JSONResponse:
    """Return a 429 JSON error with ``Retry-After`` when a SlowAPI limit is exceeded."""
    detail = (
        'Upload rate limit exceeded. Try again later.'
        if request.url.path.startswith('/api/upload')
        else 'API rate limit exceeded. Try again later.'
    )
    retry_after = _resolve_slowapi_retry_after(request, exc)
    return JSONResponse(
        status_code=429,
        content={'detail': detail},
        headers={'Retry-After': str(retry_after)},
    )


def _consume_quota_via_transaction(
    client: redis.Redis,
    redis_key: str,
    sample_count: int,
    sample_limit_per_minute: int,
    ttl_seconds: int,
) -> bool:
    allowed = False

    def _txn(pipe: redis.client.Pipeline) -> None:
        nonlocal allowed
        raw_current = cast(Any, pipe.get(redis_key))
        current = int(raw_current) if raw_current is not None else 0
        if current + sample_count > sample_limit_per_minute:
            allowed = False
            return
        pipe.multi()
        pipe.incrby(redis_key, sample_count)
        pipe.expire(redis_key, ttl_seconds)
        allowed = True

    client.transaction(_txn, redis_key, value_from_callable=False)
    return allowed


def consume_sample_quota(request: Request, sample_count: int, sample_limit_per_minute: int) -> None:
    """Consume per-minute sample quota in Redis (falling back to process memory in local mode)."""
    if sample_count <= 0:
        return
    detail = (
        f'Sample rate limit exceeded. At most {sample_limit_per_minute} '
        'samples can be analyzed per minute.'
    )
    retry_after = max(1, 60 - int(time.time() % 60))
    headers = {'Retry-After': str(retry_after)}
    if sample_count > sample_limit_per_minute:
        raise HTTPException(status_code=429, detail=detail, headers=headers)

    identity = _rate_limit_key(request)
    window_minute = _current_window_minute()
    redis_url = os.getenv(WEB_ENV.redis_url, '').strip()
    scope = getattr(request, 'scope', None)
    app = scope.get('app') if isinstance(scope, dict) else getattr(request, 'app', None)
    startup_config = getattr(getattr(app, 'state', None), 'startup_config', None)
    configured_mode = getattr(startup_config, 'deployment_mode', None)
    deployment_mode = (
        configured_mode
        if isinstance(configured_mode, str) and configured_mode.strip()
        else env_str(WEB_ENV.deployment_mode, WEB_BACKEND_CONFIG.defaults.deployment_mode)
    ).strip().lower()
    if redis_url:
        client: redis.Redis | None = None
        try:
            client = get_redis_client(redis_url)
            redis_key = f'respro:sample_quota:{identity}:{window_minute}'
            try:
                raw = client.eval(
                    _SAMPLE_QUOTA_LUA,
                    1,
                    redis_key,
                    sample_count,
                    sample_limit_per_minute,
                    _SAMPLE_QUOTA_WINDOW_TTL_SECONDS,
                )
                if isinstance(raw, (list, tuple)) and raw:
                    allowed = bool(int(cast(int | str, raw[0])))
                else:
                    allowed = bool(int(cast(int | str, raw)))
            except redis.ResponseError as exc:
                if not _is_lua_unsupported_error(exc):
                    raise
                allowed = _consume_quota_via_transaction(
                    client,
                    redis_key,
                    sample_count,
                    sample_limit_per_minute,
                    _SAMPLE_QUOTA_WINDOW_TTL_SECONDS,
                )
            if not allowed:
                raise HTTPException(status_code=429, detail=detail, headers=headers)
            return
        except HTTPException:
            raise
        except (redis.RedisError, OSError, RuntimeError) as exc:
            invalidate_redis_pool(
                getattr(client, 'connection_pool', None) if client is not None else None,
                mark_unavailable=(deployment_mode == 'local'),
            )
            logger.debug(
                'Sample quota Redis check failed for identity %s (%s): %s',
                identity,
                redact_redis_url(redis_url),
                sanitize_redis_client_error(str(exc), redis_url),
            )
            if deployment_mode != 'local':
                raise HTTPException(status_code=503, detail='Service temporarily unavailable.') from exc

    with _SAMPLE_QUOTA_LOCK:
        stale_before = window_minute - 1
        for stale_key in [k for k in _SAMPLE_QUOTA_COUNTER if k[1] < stale_before]:
            del _SAMPLE_QUOTA_COUNTER[stale_key]
        counter_key = (identity, window_minute)
        total = _SAMPLE_QUOTA_COUNTER.get(counter_key, 0) + sample_count
        if total > sample_limit_per_minute:
            raise HTTPException(status_code=429, detail=detail, headers=headers)
        _SAMPLE_QUOTA_COUNTER[counter_key] = total
