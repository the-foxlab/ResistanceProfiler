"""Opaque server-side session issuance, Redis-backed store, and ownership registry.

Security properties:
* Raw session tokens carry 256 bits of entropy; only the SHA-256 hex digest is
  persisted, so a store read cannot be replayed as a cookie.
* Cookies use ``HttpOnly; SameSite=Lax`` (plus ``Secure`` in non-local modes).
* Unknown or tampered cookies silently issue a fresh session rather than erroring.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import redis

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, parse_int_env
from web.backend.startup_config import is_path_within_allowed_roots

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = 'respro_session'
_SESSION_ENTROPY_BYTES = 32  # 256 bits of entropy, url-safe encoded.

# Process-local fallbacks used when Redis is unreachable (local zero-config mode).
_MEMORY_STORE: dict[str, float] = {}
_MEMORY_STORE_LOCK = threading.Lock()
_MEMORY_OWNED_STORE: dict[str, dict[str, str]] = {}
_MEMORY_OWNED_LOCK = threading.Lock()

# Cached Redis client and short negative cache to avoid reconnecting per request.
_REDIS_CLIENT_CACHE: redis.Redis | None = None
_REDIS_UNAVAILABLE_UNTIL: float | None = None
_REDIS_NEGATIVE_CACHE_SECONDS = 5


def reset_memory_stores() -> None:
    """Clear in-memory fallback stores and cached Redis probe state (test helper)."""
    global _REDIS_CLIENT_CACHE, _REDIS_UNAVAILABLE_UNTIL
    with _MEMORY_STORE_LOCK:
        _MEMORY_STORE.clear()
    with _MEMORY_OWNED_LOCK:
        _MEMORY_OWNED_STORE.clear()
    _REDIS_CLIENT_CACHE = None
    _REDIS_UNAVAILABLE_UNTIL = None


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


def _redis_connection() -> redis.Redis | None:
    """Return a cached Redis client, or ``None`` if unreachable (falling back to in-memory)."""
    global _REDIS_CLIENT_CACHE, _REDIS_UNAVAILABLE_UNTIL
    now = time.time()
    if _REDIS_CLIENT_CACHE is not None:
        return _REDIS_CLIENT_CACHE
    if _REDIS_UNAVAILABLE_UNTIL is not None and now < _REDIS_UNAVAILABLE_UNTIL:
        return None
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    try:
        client = redis.Redis.from_url(redis_url)
        client.ping()
        _REDIS_CLIENT_CACHE = client
        return client
    except Exception as exc:  # noqa: BLE001 — best-effort probe; falls back to in-memory
        logger.debug('Session store: Redis unavailable at %s, using in-memory fallback: %s', redis_url, exc)
        _REDIS_UNAVAILABLE_UNTIL = now + _REDIS_NEGATIVE_CACHE_SECONDS
        return None


def hash_session_token(token: str) -> str:
    """Return the hex SHA-256 hash of *token*."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _session_key(session_hash: str) -> str:
    return f'respro:session:{session_hash}'


def _owned_key(prefix: str, record_id: str) -> str:
    return f'respro:{prefix}:{record_id}'


def _store_session(session_hash: str, ttl: int) -> None:
    client = _redis_connection()
    if client is not None:
        key = _session_key(session_hash)
        client.hset(key, mapping={'created_at': str(int(time.time())), 'expiry': str(ttl)})
        client.expire(key, ttl)
        return
    with _MEMORY_STORE_LOCK:
        _MEMORY_STORE[session_hash] = time.time() + ttl


def _session_exists(session_hash: str) -> bool:
    client = _redis_connection()
    if client is not None:
        try:
            return bool(client.exists(_session_key(session_hash)))
        except (redis.RedisError, OSError) as exc:
            logger.debug('Session existence Redis check failed: %s', exc)
            return False
    with _MEMORY_STORE_LOCK:
        expiry = _MEMORY_STORE.get(session_hash)
        if expiry is None:
            return False
        if time.time() > expiry:
            _MEMORY_STORE.pop(session_hash, None)
            return False
        return True


def create_session() -> Session:
    """Generate a fresh session token, persist its hash, and return it."""
    token = secrets.token_urlsafe(_SESSION_ENTROPY_BYTES)
    session_hash = hash_session_token(token)
    _store_session(session_hash, resolve_session_ttl_seconds())
    return Session(token=token, session_hash=session_hash)


def resolve_or_create_session(cookie_value: str | None) -> Session:
    """Return a valid session for *cookie_value*, or issue a fresh session if absent/invalid."""
    if cookie_value:
        token = cookie_value.strip()
        if token:
            session_hash = hash_session_token(token)
            if _session_exists(session_hash):
                return Session(token=token, session_hash=session_hash)
    return create_session()


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
) -> str:
    """Record an upload under *session_hash* and return its opaque upload ID."""
    return _record_owned(
        prefix='upload',
        session_hash=session_hash,
        canonical_path=canonical_path,
        extra={'file_type': file_type},
        ttl=ttl,
    )


def record_job(
    *,
    session_hash: str,
    upload_ids: list[str],
    job_id: str | None = None,
    ttl: int | None = None,
) -> str:
    """Record a queued job under *session_hash* and return its job ID."""
    return _record_owned(
        prefix='job',
        session_hash=session_hash,
        canonical_path=None,
        extra={'upload_ids': ','.join(upload_ids), 'status': 'queued'},
        ttl=ttl,
        record_id=job_id,
    )


def record_artifact(
    *,
    session_hash: str,
    canonical_path: Path,
    media_type: str,
    ttl: int | None = None,
) -> str:
    """Record an output artifact under *session_hash* and return its opaque artifact ID."""
    return _record_owned(
        prefix='artifact',
        session_hash=session_hash,
        canonical_path=canonical_path,
        extra={'media_type': media_type},
        ttl=ttl,
    )


def _record_owned(
    *,
    prefix: str,
    session_hash: str,
    canonical_path: Path | None,
    extra: dict[str, str],
    ttl: int | None,
    record_id: str | None = None,
) -> str:
    record_id = record_id or secrets.token_urlsafe(16)
    key = _owned_key(prefix, record_id)
    mapping: dict[str, str] = {'owner': session_hash, **extra}
    if canonical_path is not None:
        mapping['canonical_path'] = str(canonical_path)
    effective_ttl = ttl if ttl is not None else resolve_session_ttl_seconds()
    client = _redis_connection()
    if client is not None:
        client.hset(key, mapping=mapping)
        client.expire(key, effective_ttl)
    else:
        with _MEMORY_OWNED_LOCK:
            _MEMORY_OWNED_STORE[key] = dict(mapping)
    return record_id


def fetch_owned_record(prefix: str, record_id: str) -> OwnedRecord | None:
    """Fetch one owned record by ID, or ``None`` if absent."""
    key = _owned_key(prefix, record_id)
    client = _redis_connection()
    if client is not None:
        try:
            raw = client.hgetall(key)
        except (redis.RedisError, OSError) as exc:
            logger.debug('Owned record fetch failed for %s: %s', key, exc)
            return None
        decoded = {k.decode('utf-8'): v.decode('utf-8') for k, v in raw.items()} if raw else {}
    else:
        with _MEMORY_OWNED_LOCK:
            decoded = dict(_MEMORY_OWNED_STORE.get(key, {}))
    if not decoded:
        return None
    return OwnedRecord(
        record_id=record_id,
        owner=decoded.get('owner', ''),
        canonical_path=decoded.get('canonical_path'),
        fields=decoded,
    )


def update_owned_record_fields(prefix: str, record_id: str, updates: dict[str, str]) -> bool:
    """Merge *updates* into an existing owned record; return True if persisted."""
    record = fetch_owned_record(prefix, record_id)
    if record is None:
        return False
    mapping = {**record.fields, **updates}
    key = _owned_key(prefix, record_id)
    client = _redis_connection()
    if client is not None:
        try:
            client.hset(key, mapping=mapping)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning('Owned record update failed for %s: %s', key, exc)
            return False
    with _MEMORY_OWNED_LOCK:
        _MEMORY_OWNED_STORE[key] = mapping
    return True


def owner_matches(record: OwnedRecord | None, session_hash: str) -> bool:
    """Return True only when *record* exists and belongs to *session_hash* (constant-time)."""
    return record is not None and bool(record.owner) and hmac.compare_digest(record.owner, session_hash)


def delete_owned_record(prefix: str, record_id: str) -> bool:
    """Delete one owned record; return True if a key was removed."""
    key = _owned_key(prefix, record_id)
    client = _redis_connection()
    if client is not None:
        try:
            return bool(client.delete(key))
        except (redis.RedisError, OSError) as exc:
            logger.debug('Owned record delete failed for %s: %s', key, exc)
            return False
    with _MEMORY_OWNED_LOCK:
        return bool(_MEMORY_OWNED_STORE.pop(key, None))


def resolve_owned_path(
    *,
    prefix: str,
    record_id: str,
    session_hash: str,
    allowed_roots: tuple[Path, ...],
) -> Path:
    """Resolve an opaque ID to an owned, path-confined file (``LookupError`` → 404, ``ValueError`` → 400)."""
    record = fetch_owned_record(prefix, record_id)
    if not owner_matches(record, session_hash):
        raise LookupError(f'{prefix} not found.')
    if record.canonical_path is None:
        raise LookupError(f'{prefix} record has no canonical path.')
    resolved = Path(record.canonical_path).expanduser().resolve()
    if not is_path_within_allowed_roots(resolved, allowed_roots):
        raise ValueError(f'{prefix} path is outside allowed directory.')
    return resolved
