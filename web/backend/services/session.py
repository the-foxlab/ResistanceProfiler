"""Opaque server-side session issuance, Redis-backed store, and ownership registry.

Security properties:
* Raw session tokens carry 256 bits of entropy; only the SHA-256 hex digest is
  persisted, so a store read cannot be replayed as a cookie.
* Cookies use ``HttpOnly; SameSite=Lax`` (plus ``Secure`` in non-local modes).
* Unknown or tampered cookies silently issue a fresh session rather than erroring.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import redis

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
    env_str,
    parse_int_env,
    redact_redis_text,
    redact_redis_url,
    sanitize_redis_client_error,
)
from web.backend.startup_config import is_path_within_allowed_roots

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = 'respro_session'
_SESSION_ENTROPY_BYTES = 32  # 256 bits of entropy, url-safe encoded.

_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
_REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
_REDIS_NEGATIVE_CACHE_SECONDS = 5
_MEMORY_SWEEP_INTERVAL_SECONDS = 60.0

# Process-local fallbacks used when Redis is unreachable (local zero-config mode).
_MEMORY_STORE: dict[str, float] = {}
_MEMORY_STORE_LOCK = threading.Lock()
_MEMORY_STORE_LAST_SWEEP: float | None = None
_MEMORY_OWNED_STORE: dict[str, tuple[float, dict[str, str]]] = {}
_MEMORY_OWNED_LOCK = threading.Lock()
_MEMORY_OWNED_LAST_SWEEP: float | None = None

# Shared Redis ConnectionPool and short negative cache for local fallback mode.
_REDIS_POOL_LOCK = threading.Lock()
_REDIS_POOL: redis.ConnectionPool | None = None
_REDIS_POOL_URL: str | None = None
_REDIS_UNAVAILABLE_UNTIL: float | None = None
_REDIS_REQUIRED_CONTEXT: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    '_REDIS_REQUIRED_CONTEXT',
    default=None,
)

_CLAIM_OWNED_FIELD_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
if redis.call('HSETNX', KEYS[1], ARGV[1], ARGV[2]) == 0 then
    return 0
end
local target_ttl = tonumber(ARGV[3])
local current_ttl = redis.call('TTL', KEYS[1])
if target_ttl and target_ttl > 0 then
    redis.call('EXPIRE', KEYS[1], target_ttl)
elseif current_ttl and current_ttl < 0 then
    local default_ttl = tonumber(ARGV[4])
    if default_ttl and default_ttl > 0 then
        redis.call('EXPIRE', KEYS[1], default_ttl)
    end
end
return 1
"""


class SessionBackendError(RuntimeError):
    """Raised when a required Redis session/ownership backend is unreachable."""


def _sweep_memory_store_locked(now: float) -> None:
    global _MEMORY_STORE_LAST_SWEEP
    if (
        _MEMORY_STORE_LAST_SWEEP is None
        or now < _MEMORY_STORE_LAST_SWEEP
        or (now - _MEMORY_STORE_LAST_SWEEP) >= _MEMORY_SWEEP_INTERVAL_SECONDS
    ):
        expired = [key for key, expiry in _MEMORY_STORE.items() if now > expiry]
        for key in expired:
            _MEMORY_STORE.pop(key, None)
        _MEMORY_STORE_LAST_SWEEP = now


def _sweep_memory_owned_locked(now: float) -> None:
    global _MEMORY_OWNED_LAST_SWEEP
    if (
        _MEMORY_OWNED_LAST_SWEEP is None
        or now < _MEMORY_OWNED_LAST_SWEEP
        or (now - _MEMORY_OWNED_LAST_SWEEP) >= _MEMORY_SWEEP_INTERVAL_SECONDS
    ):
        expired = [key for key, (expiry, _fields) in _MEMORY_OWNED_STORE.items() if now > expiry]
        for key in expired:
            _MEMORY_OWNED_STORE.pop(key, None)
        _MEMORY_OWNED_LAST_SWEEP = now


def _resolve_redis_url(redis_url: str | None = None) -> str:
    if redis_url is not None:
        return redis_url.strip()
    return env_str(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)


def get_redis_pool(redis_url: str | None = None) -> redis.ConnectionPool:
    """Return the shared, timeout-bounded Redis ``ConnectionPool`` for *redis_url*."""
    global _REDIS_POOL, _REDIS_POOL_URL, _REDIS_UNAVAILABLE_UNTIL
    url = _resolve_redis_url(redis_url)
    old_pool: redis.ConnectionPool | None = None
    with _REDIS_POOL_LOCK:
        if _REDIS_POOL is not None and _REDIS_POOL_URL == url:
            return _REDIS_POOL
        old_pool = _REDIS_POOL
        try:
            new_pool = redis.ConnectionPool.from_url(
                url,
                socket_connect_timeout=_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
            )
        except ValueError as exc:
            sanitized_msg = sanitize_redis_client_error(str(exc), url)
            if sanitized_msg != str(exc):
                raise ValueError(sanitized_msg) from None
            raise
        _REDIS_POOL = new_pool
        _REDIS_POOL_URL = url
        _REDIS_UNAVAILABLE_UNTIL = None
    if old_pool is not None:
        with contextlib.suppress(Exception):
            old_pool.disconnect()
    return new_pool


def get_redis_client(redis_url: str | None = None) -> redis.Redis:
    """Return a Redis client backed by the shared ``ConnectionPool``."""
    return redis.Redis(connection_pool=get_redis_pool(redis_url))


def invalidate_redis_pool(
    pool: redis.ConnectionPool | None = None,
    *,
    mark_unavailable: bool = True,
) -> None:
    """Drop the cached Redis ``ConnectionPool`` after an error."""
    global _REDIS_POOL, _REDIS_POOL_URL, _REDIS_UNAVAILABLE_UNTIL
    old_pool: redis.ConnectionPool | None = None
    with _REDIS_POOL_LOCK:
        if pool is None or _REDIS_POOL is pool:
            old_pool = _REDIS_POOL
            _REDIS_POOL = None
            _REDIS_POOL_URL = None
            _REDIS_UNAVAILABLE_UNTIL = (
                time.monotonic() + _REDIS_NEGATIVE_CACHE_SECONDS if mark_unavailable else None
            )
    if old_pool is not None:
        with contextlib.suppress(Exception):
            old_pool.disconnect()


def reset_redis_pool() -> None:
    """Reset the shared Redis ``ConnectionPool`` and negative cache (test helper)."""
    invalidate_redis_pool(None, mark_unavailable=False)


def reset_memory_stores() -> None:
    """Clear in-memory fallback stores and cached Redis pool state (test helper)."""
    global _MEMORY_STORE_LAST_SWEEP, _MEMORY_OWNED_LAST_SWEEP
    with _MEMORY_STORE_LOCK:
        _MEMORY_STORE.clear()
        _MEMORY_STORE_LAST_SWEEP = None
    with _MEMORY_OWNED_LOCK:
        _MEMORY_OWNED_STORE.clear()
        _MEMORY_OWNED_LAST_SWEEP = None
    reset_redis_pool()


@dataclass(frozen=True)
class Session:
    """Resolved session token and its persisted SHA-256 hash."""

    token: str
    session_hash: str


@dataclass(frozen=True)
class OwnedRecord:
    """Resolved upload, job, or artifact ownership record."""

    record_id: str
    owner: str
    canonical_path: str | None
    fields: dict[str, str]


def resolve_session_ttl_seconds() -> int:
    """Resolve the session TTL from env with the config default, failing fast on invalid input."""
    return parse_int_env(
        WEB_ENV.session_ttl,
        WEB_BACKEND_CONFIG.defaults.session_ttl_seconds,
        min_value=1,
    )


def resolve_result_ttl_seconds() -> int:
    """Resolve the result/artifact TTL from env with the config default, failing fast on invalid input."""
    return parse_int_env(
        WEB_ENV.result_ttl,
        WEB_BACKEND_CONFIG.defaults.result_ttl_seconds,
        min_value=1,
    )


def _default_ttl_for_prefix(prefix: str) -> int:
    if prefix in {'artifact', 'job'}:
        return resolve_result_ttl_seconds()
    return resolve_session_ttl_seconds()


def _is_redis_required(deployment_mode: str | None = None) -> bool:
    mode = (
        deployment_mode
        if deployment_mode is not None
        else env_str(WEB_ENV.deployment_mode, WEB_BACKEND_CONFIG.defaults.deployment_mode)
    )
    return mode.strip().lower() != 'local'


def _is_lua_unsupported_error(exc: redis.ResponseError) -> bool:
    message = str(exc).lower()
    return 'unknown command' in message and 'eval' in message


def _redis_connection(required: bool | None = None) -> redis.Redis | None:
    """Return a pooled Redis client, or ``None`` if unreachable (falling back to in-memory)."""
    url = _resolve_redis_url()
    ctx_required = _REDIS_REQUIRED_CONTEXT.get()
    effective_required = (
        required
        if required is not None
        else (ctx_required if ctx_required is not None else _is_redis_required(None))
    )
    now = time.monotonic()
    with _REDIS_POOL_LOCK:
        if _REDIS_POOL is not None and _REDIS_POOL_URL == url:
            return redis.Redis(connection_pool=_REDIS_POOL)
        if not effective_required and _REDIS_UNAVAILABLE_UNTIL is not None and now < _REDIS_UNAVAILABLE_UNTIL:
            return None

    pool: redis.ConnectionPool | None = None
    try:
        pool = get_redis_pool(url)
        client = redis.Redis(connection_pool=pool)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 — best-effort probe; falls back to in-memory or fails closed in caller
        if effective_required:
            logger.debug(
                'Session store: Redis unavailable at %s (required mode): %s',
                redact_redis_url(url),
                sanitize_redis_client_error(str(exc), url),
            )
        else:
            logger.debug(
                'Session store: Redis unavailable at %s, using in-memory fallback: %s',
                redact_redis_url(url),
                sanitize_redis_client_error(str(exc), url),
            )
        invalidate_redis_pool(pool, mark_unavailable=not effective_required)
        return None


def _redis_client_for_mode(deployment_mode: str | None = None) -> tuple[redis.Redis | None, bool]:
    global _REDIS_UNAVAILABLE_UNTIL
    required = _is_redis_required(deployment_mode)
    if required:
        with _REDIS_POOL_LOCK:
            _REDIS_UNAVAILABLE_UNTIL = None
    token = _REDIS_REQUIRED_CONTEXT.set(required)
    try:
        client = _redis_connection()
    finally:
        _REDIS_REQUIRED_CONTEXT.reset(token)
    if client is None and required:
        raise SessionBackendError('Session store unavailable.')
    return client, required


def hash_session_token(token: str) -> str:
    """Return the hex SHA-256 hash of *token*."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _session_key(session_hash: str) -> str:
    return f'respro:session:{session_hash}'


def _owned_key(prefix: str, record_id: str) -> str:
    return f'respro:{prefix}:{record_id}'


def _store_session(session_hash: str, ttl: int, *, deployment_mode: str | None = None) -> None:
    client, required = _redis_client_for_mode(deployment_mode)
    if client is not None:
        key = _session_key(session_hash)
        try:
            with client.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping={'created_at': str(int(time.time())), 'expiry': str(ttl)})
                pipe.expire(key, ttl)
                pipe.execute()
            return
        except (redis.RedisError, OSError) as exc:
            logger.debug('Session store Redis write failed: %s', redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
    now = time.time()
    with _MEMORY_STORE_LOCK:
        _sweep_memory_store_locked(now)
        _MEMORY_STORE[session_hash] = now + ttl


def _session_exists(session_hash: str, *, deployment_mode: str | None = None) -> bool:
    ttl = resolve_session_ttl_seconds()
    client, required = _redis_client_for_mode(deployment_mode)
    if client is not None:
        try:
            return bool(client.expire(_session_key(session_hash), ttl))
        except (redis.RedisError, OSError) as exc:
            logger.debug('Session existence Redis check failed: %s', redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
    now = time.time()
    with _MEMORY_STORE_LOCK:
        _sweep_memory_store_locked(now)
        expiry = _MEMORY_STORE.get(session_hash)
        if expiry is None:
            return False
        if now > expiry:
            _MEMORY_STORE.pop(session_hash, None)
            return False
        _MEMORY_STORE[session_hash] = now + ttl
        return True


def create_session(*, deployment_mode: str | None = None) -> Session:
    """Generate a fresh session token, persist its hash, and return it."""
    token = secrets.token_urlsafe(_SESSION_ENTROPY_BYTES)
    session_hash = hash_session_token(token)
    _store_session(session_hash, resolve_session_ttl_seconds(), deployment_mode=deployment_mode)
    return Session(token=token, session_hash=session_hash)


def resolve_or_create_session(
    cookie_value: str | None,
    *,
    deployment_mode: str | None = None,
) -> Session:
    """Return a valid session for *cookie_value*, or issue a fresh session if absent/invalid."""
    if cookie_value:
        token = cookie_value.strip()
        if token:
            session_hash = hash_session_token(token)
            if _session_exists(session_hash, deployment_mode=deployment_mode):
                return Session(token=token, session_hash=session_hash)
    return create_session(deployment_mode=deployment_mode)


def set_session_cookie_header(session: Session, deployment_mode: str = 'local') -> str:
    """Format the ``Set-Cookie`` header value (``Secure`` only in non-local modes)."""
    secure = '; Secure' if deployment_mode != 'local' else ''
    return (
        f'{SESSION_COOKIE_NAME}={session.token}; Path=/; HttpOnly; SameSite=Lax{secure}; '
        f'Max-Age={resolve_session_ttl_seconds()}'
    )


# ─── Ownership registry (uploads / jobs / artifacts) ──────────────────────


def record_upload(
    *,
    session_hash: str,
    canonical_path: Path,
    file_type: str,
    ttl: int | None = None,
    deployment_mode: str | None = None,
) -> str:
    """Record an upload under *session_hash* and return its opaque upload ID."""
    return _record_owned(
        prefix='upload',
        session_hash=session_hash,
        canonical_path=canonical_path,
        extra={'file_type': file_type},
        ttl=ttl,
        deployment_mode=deployment_mode,
    )


def record_job(
    *,
    session_hash: str,
    upload_ids: list[str],
    job_id: str | None = None,
    ttl: int | None = None,
    deployment_mode: str | None = None,
) -> str:
    """Record a queued job under *session_hash* and return its job ID."""
    return _record_owned(
        prefix='job',
        session_hash=session_hash,
        canonical_path=None,
        extra={'upload_ids': ','.join(upload_ids), 'status': 'queued'},
        ttl=ttl if ttl is not None else resolve_result_ttl_seconds(),
        record_id=job_id,
        deployment_mode=deployment_mode,
    )


def record_artifact(
    *,
    session_hash: str,
    canonical_path: Path,
    media_type: str,
    ttl: int | None = None,
    deployment_mode: str | None = None,
) -> str:
    """Record an output artifact under *session_hash* and return its opaque artifact ID."""
    return _record_owned(
        prefix='artifact',
        session_hash=session_hash,
        canonical_path=canonical_path,
        extra={'media_type': media_type},
        ttl=ttl if ttl is not None else resolve_result_ttl_seconds(),
        deployment_mode=deployment_mode,
    )


def _record_owned(
    *,
    prefix: str,
    session_hash: str,
    canonical_path: Path | None,
    extra: dict[str, str],
    ttl: int | None,
    record_id: str | None = None,
    deployment_mode: str | None = None,
) -> str:
    record_id = record_id or secrets.token_urlsafe(16)
    key = _owned_key(prefix, record_id)
    mapping: dict[str, str] = {'owner': session_hash, **extra}
    if canonical_path is not None:
        mapping['canonical_path'] = str(canonical_path)
    effective_ttl = ttl if ttl is not None else _default_ttl_for_prefix(prefix)
    client, required = _redis_client_for_mode(deployment_mode)
    if client is not None:
        try:
            with client.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, effective_ttl)
                pipe.execute()
            return record_id
        except (redis.RedisError, OSError) as exc:
            logger.warning('Owned record write failed for %s: %s', key, redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
    now = time.time()
    with _MEMORY_OWNED_LOCK:
        _sweep_memory_owned_locked(now)
        _MEMORY_OWNED_STORE[key] = (now + effective_ttl, dict(mapping))
    return record_id


def fetch_owned_record(
    prefix: str,
    record_id: str,
    *,
    deployment_mode: str | None = None,
) -> OwnedRecord | None:
    """Fetch one owned record by ID, or ``None`` if absent."""
    key = _owned_key(prefix, record_id)
    client, required = _redis_client_for_mode(deployment_mode)
    decoded: dict[str, str] = {}
    if client is not None:
        try:
            raw = cast(dict[Any, Any], client.hgetall(key))
            decoded = (
                {
                    (k.decode('utf-8') if isinstance(k, bytes) else str(k)): (
                        v.decode('utf-8') if isinstance(v, bytes) else str(v)
                    )
                    for k, v in raw.items()
                }
                if raw
                else {}
            )
            if not decoded:
                return None
            return OwnedRecord(
                record_id=record_id,
                owner=decoded.get('owner', ''),
                canonical_path=decoded.get('canonical_path'),
                fields=decoded,
            )
        except (redis.RedisError, OSError) as exc:
            logger.debug('Owned record fetch failed for %s: %s', key, redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
    now = time.time()
    with _MEMORY_OWNED_LOCK:
        _sweep_memory_owned_locked(now)
        entry = _MEMORY_OWNED_STORE.get(key)
        if entry is None:
            return None
        expiry, fields = entry
        if now > expiry:
            _MEMORY_OWNED_STORE.pop(key, None)
            return None
        decoded = dict(fields)
    if not decoded:
        return None
    return OwnedRecord(
        record_id=record_id,
        owner=decoded.get('owner', ''),
        canonical_path=decoded.get('canonical_path'),
        fields=decoded,
    )


def _claim_owned_via_transaction(
    client: redis.Redis,
    key: str,
    field: str,
    value: str,
    target_ttl: int,
    default_ttl: int,
) -> bool:
    claimed = False

    def _txn(pipe: redis.client.Pipeline) -> None:
        nonlocal claimed
        if not bool(pipe.exists(key)):
            claimed = False
            return
        if bool(pipe.hexists(key, field)):
            claimed = False
            return
        current_ttl = int(cast(int, pipe.ttl(key)))
        pipe.multi()
        pipe.hsetnx(key, field, value)
        if target_ttl > 0:
            pipe.expire(key, target_ttl)
        elif current_ttl < 0 and default_ttl > 0:
            pipe.expire(key, default_ttl)
        claimed = True

    client.transaction(_txn, key, value_from_callable=False)
    return claimed


def claim_owned_record_field(
    prefix: str,
    record_id: str,
    field: str,
    value: str,
    *,
    ttl: int | None = None,
    deployment_mode: str | None = None,
) -> bool:
    """Atomically set *field* on an existing owned record only if *field* is not yet set."""
    key = _owned_key(prefix, record_id)
    target_ttl = ttl if ttl is not None and ttl > 0 else 0
    default_ttl = _default_ttl_for_prefix(prefix)
    client, required = _redis_client_for_mode(deployment_mode)
    if client is not None:
        try:
            try:
                result = client.eval(_CLAIM_OWNED_FIELD_LUA, 1, key, field, value, str(target_ttl), str(default_ttl))
                return bool(int(cast(int | str, result)))
            except redis.ResponseError as exc:
                if not _is_lua_unsupported_error(exc):
                    raise
                return _claim_owned_via_transaction(client, key, field, value, target_ttl, default_ttl)
        except (redis.RedisError, OSError) as exc:
            logger.warning('Owned record claim failed for %s: %s', key, redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
            return False
    now = time.time()
    with _MEMORY_OWNED_LOCK:
        _sweep_memory_owned_locked(now)
        entry = _MEMORY_OWNED_STORE.get(key)
        if entry is None:
            return False
        expiry, fields = entry
        if now > expiry:
            _MEMORY_OWNED_STORE.pop(key, None)
            return False
        if field in fields:
            return False
        updated_fields = {**fields, field: value}
        new_expiry = now + target_ttl if target_ttl > 0 else expiry
        _MEMORY_OWNED_STORE[key] = (new_expiry, updated_fields)
        return True


def owner_matches(record: OwnedRecord | None, session_hash: str) -> bool:
    """Return True only when *record* exists and belongs to *session_hash* (constant-time)."""
    return record is not None and bool(record.owner) and hmac.compare_digest(record.owner, session_hash)


def delete_owned_record(
    prefix: str,
    record_id: str,
    *,
    deployment_mode: str | None = None,
) -> bool:
    """Delete one owned record; return True if a key was removed."""
    key = _owned_key(prefix, record_id)
    client, required = _redis_client_for_mode(deployment_mode)
    if client is not None:
        try:
            return bool(client.delete(key))
        except (redis.RedisError, OSError) as exc:
            logger.debug('Owned record delete failed for %s: %s', key, redact_redis_text(str(exc)))
            invalidate_redis_pool(getattr(client, 'connection_pool', None), mark_unavailable=not required)
            if required:
                raise SessionBackendError('Session store unavailable.') from exc
            return False
    now = time.time()
    with _MEMORY_OWNED_LOCK:
        _sweep_memory_owned_locked(now)
        entry = _MEMORY_OWNED_STORE.pop(key, None)
        return entry is not None and now <= entry[0]


def resolve_owned_path(
    *,
    prefix: str,
    record_id: str,
    session_hash: str,
    allowed_roots: tuple[Path, ...],
    expected_file_type: str | None = None,
    deployment_mode: str | None = None,
) -> Path:
    """Resolve an opaque ID to an owned, path-confined file (``LookupError`` → 404, ``ValueError`` → 400)."""
    record = fetch_owned_record(prefix, record_id, deployment_mode=deployment_mode)
    if record is None or not owner_matches(record, session_hash):
        raise LookupError(f'{prefix} not found.')
    if expected_file_type is not None:
        actual_type = record.fields.get('file_type')
        if actual_type != expected_file_type:
            raise ValueError(f'Expected {expected_file_type} upload, got {actual_type or "unknown"}.')
    if not record.canonical_path:
        raise LookupError(f'{prefix} record has no canonical path.')
    resolved = Path(record.canonical_path).expanduser().resolve()
    resolved_roots = tuple(root.expanduser().resolve() for root in allowed_roots)
    if (
        not resolved_roots
        or not is_path_within_allowed_roots(resolved, resolved_roots)
        or any(resolved == root for root in resolved_roots)
    ):
        raise ValueError(f'{prefix} path is outside allowed directory.')
    return resolved
