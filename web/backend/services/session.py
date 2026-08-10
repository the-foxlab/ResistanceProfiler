"""Opaque server-side session issuance and Redis-backed session store.

Sessions are the ownership boundary for uploads, jobs, and artifacts in
non-local deployment modes. In local mode they are additive: zero-config
startup still works without a token and without a running Redis, in which
case sessions fall back to a process-local in-memory store. In non-local
modes Redis is already required (for the RQ queue), so the Redis-backed store
is used and provides cross-process ownership semantics.

Security properties:

* The raw session value carries >=256 bits of randomness.
* Only a keyed hash of the session value is stored — the raw value is never
  persisted, so a store read cannot be replayed as a cookie.
* The cookie is ``Secure``, ``HttpOnly``, ``SameSite=Lax``.
* A tampered or unknown session cookie is treated as "no session" and a fresh
  session is issued rather than erroring.
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

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = 'respro_session'
# 32 raw bytes -> 256 bits of entropy, url-safe encoded.
_SESSION_ENTROPY_BYTES = 32
_SESSION_HASH_BYTES = 32

# Process-local fallback store used when Redis is unavailable (local zero-config
# startup without a Redis container). Keyed by session hash; value is the
# creation timestamp. In non-local modes Redis is required and this fallback is
# only a transient safety net.
_MEMORY_STORE: dict[str, float] = {}
_MEMORY_STORE_LOCK = threading.Lock()

# Process-local fallback for owned records (uploads/jobs/artifacts). Keyed by
# ``prefix:record_id``; value is the field mapping. Used only when Redis is
# unavailable so local single-user mode still works.
_MEMORY_OWNED_STORE: dict[str, dict[str, str]] = {}
_MEMORY_OWNED_LOCK = threading.Lock()

# Cached Redis client and negative-cache expiry so we don't reconnect on every
# request. Reset by tests that swap in fakeredis.
_REDIS_CLIENT_CACHE: redis.Redis | None = None
_REDIS_UNAVAILABLE_UNTIL: float | None = None
_REDIS_NEGATIVE_CACHE_SECONDS = 5


def reset_memory_stores() -> None:
    """Clear the in-memory fallback stores (test helper)."""
    global _REDIS_CLIENT_CACHE, _REDIS_UNAVAILABLE_UNTIL
    with _MEMORY_STORE_LOCK:
        _MEMORY_STORE.clear()
    with _MEMORY_OWNED_LOCK:
        _MEMORY_OWNED_STORE.clear()
    _REDIS_CLIENT_CACHE = None
    _REDIS_UNAVAILABLE_UNTIL = None


@dataclass(frozen=True)
class Session:
    """A resolved session: the opaque token to set on the cookie and its Redis key."""

    token: str
    session_hash: str


def resolve_session_ttl_seconds() -> int:
    """Resolve the session TTL from env with the config default, failing fast on invalid input."""
    default = WEB_BACKEND_CONFIG.defaults.session_ttl_seconds
    raw_value = os.getenv(WEB_ENV.session_ttl, str(default)).strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f'{WEB_ENV.session_ttl} must be an integer value.') from exc
    if parsed <= 0:
        raise ValueError(f'{WEB_ENV.session_ttl} must be > 0.')
    return parsed


def _redis_connection() -> redis.Redis | None:
    """Build a Redis connection from the configured URL, or None if Redis is unreachable.

    In local zero-config mode Redis may not be running; the session store then
    falls back to a process-local in-memory dict so single-user startup still
    works. In non-local modes Redis is required (for the RQ queue) and a
    connection is expected to succeed.

    The resolved client (or None) is cached to avoid reconnecting on every
    request; a short negative cache limits how often an unavailable Redis is
    retried so a Redis that comes up later is picked up.
    """
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
    except Exception as exc:  # noqa: BLE001 — best-effort Redis probe; any failure falls back to in-memory
        logger.debug('Session store: Redis unavailable at %s, using in-memory fallback: %s', redis_url, exc)
        _REDIS_UNAVAILABLE_UNTIL = now + _REDIS_NEGATIVE_CACHE_SECONDS
        return None


def hash_session_token(token: str) -> str:
    """Return the hex SHA-256 hash of a session token.

    Only the hash is stored, so a store read cannot be replayed as a cookie.
    SHA-256 is sufficient here because the token carries 256 bits of fresh
    randomness and is never reused, making brute-force preimage attacks
    infeasible.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _store_session(session_hash: str, ttl: int) -> None:
    """Persist a session hash with the given TTL, preferring Redis."""
    client = _redis_connection()
    if client is not None:
        key = _session_key(session_hash)
        client.hset(key, mapping={'created_at': str(int(time.time())), 'expiry': str(ttl)})
        client.expire(key, ttl)
        return
    with _MEMORY_STORE_LOCK:
        _MEMORY_STORE[session_hash] = time.time() + ttl


def _session_exists(session_hash: str) -> bool:
    """Return whether a session hash is known and unexpired."""
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
    ttl = resolve_session_ttl_seconds()
    _store_session(session_hash, ttl)
    return Session(token=token, session_hash=session_hash)


def resolve_or_create_session(cookie_value: str | None) -> Session:
    """Return a valid session for the request, creating one if the cookie is absent/invalid.

    A tampered or unknown cookie is treated as "no session" and a fresh session
    is issued rather than erroring — this avoids confirming session existence to
    an attacker and keeps the zero-config local flow robust.
    """
    if cookie_value:
        session = _validate_cookie(cookie_value)
        if session is not None:
            return session
    return create_session()


def _validate_cookie(cookie_value: str) -> Session | None:
    """Return a Session if the cookie value maps to a known, unexpired session."""
    token = cookie_value.strip()
    if not token:
        return None
    session_hash = hash_session_token(token)
    if _session_exists(session_hash):
        return Session(token=token, session_hash=session_hash)
    return None


def _session_key(session_hash: str) -> str:
    """Return the Redis key for one session hash."""
    return f'respro:session:{session_hash}'


def session_cookie_attributes(deployment_mode: str = 'local') -> str:
    """Return the cookie attribute string for a session cookie.

    ``Secure`` is set only in non-local deployment modes: in local mode the app
    binds to loopback over plain HTTP, and a ``Secure`` cookie would not be sent
    by clients (browsers exclude ``Secure`` cookies from HTTP requests, and so
    does the httpx test client). In non-local mode the app sits behind a
    TLS-terminating proxy, so ``Secure`` is correct. ``HttpOnly`` keeps the token
    out of JavaScript; ``SameSite=Lax`` allows top-level navigations (e.g.
    opening a report link) while blocking cross-site POSTs.
    """
    secure = '; Secure' if deployment_mode != 'local' else ''
    return (
        f'{SESSION_COOKIE_NAME}=%s; Path=/; HttpOnly; SameSite=Lax{secure}; '
        f'Max-Age={resolve_session_ttl_seconds()}'
    )


def set_session_cookie_header(session: Session, deployment_mode: str = 'local') -> str:
    """Return the full Set-Cookie header value for a session."""
    return session_cookie_attributes(deployment_mode) % session.token


def is_session_known(session_hash: str) -> bool:
    """Return whether a session hash maps to a known, unexpired session record."""
    return _session_exists(session_hash)


# ─── Ownership registry (uploads / jobs / artifacts) ──────────────────────
#
# Every upload, job, and artifact is recorded in Redis with its owning session
# hash and canonical path. Routes resolve opaque IDs to these records
# server-side and verify ownership before acting.


def record_upload(
    *,
    session_hash: str,
    canonical_path: Path,
    file_type: str,
    ttl: int | None = None,
) -> str:
    """Record an upload under a session and return its opaque upload id."""
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
    """Record a queued job under a session and return its opaque job id.

    When ``job_id`` is provided (the RQ job id), the ownership record is keyed by
    that id so the job-status route can look it up directly. Otherwise a fresh
    opaque id is generated.
    """
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
    """Record an output artifact under a session and return its opaque artifact id."""
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
    """Persist one owned record and return its opaque id."""
    if record_id is None:
        record_id = secrets.token_urlsafe(16)
    key = f'respro:{prefix}:{record_id}'
    mapping: dict[str, str] = {'owner': session_hash}
    if canonical_path is not None:
        mapping['canonical_path'] = str(canonical_path)
    mapping.update(extra)
    effective_ttl = ttl if ttl is not None else resolve_session_ttl_seconds()
    client = _redis_connection()
    if client is not None:
        client.hset(key, mapping=mapping)
        client.expire(key, effective_ttl)
    else:
        with _MEMORY_OWNED_LOCK:
            _MEMORY_OWNED_STORE[key] = dict(mapping)
    return record_id


@dataclass(frozen=True)
class OwnedRecord:
    """A resolved owned record from Redis."""

    record_id: str
    owner: str
    canonical_path: str | None
    fields: dict[str, str]


def fetch_owned_record(prefix: str, record_id: str) -> OwnedRecord | None:
    """Fetch one owned record by id, or None if it does not exist."""
    key = f'respro:{prefix}:{record_id}'
    client = _redis_connection()
    if client is not None:
        try:
            raw = client.hgetall(key)
        except (redis.RedisError, OSError) as exc:
            logger.debug('Owned record fetch failed for %s: %s', key, exc)
            return None
        if not raw:
            return None
        decoded = {k.decode('utf-8'): v.decode('utf-8') for k, v in raw.items()}
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


def owner_matches(record: OwnedRecord | None, session_hash: str) -> bool:
    """Return True only when the record exists and is owned by the given session.

    A non-existent record is treated as a non-match so callers can uniformly
    return 404 (not 403) for both unknown and non-owned records, avoiding
    confirming existence to non-owners.
    """
    if record is None:
        return False
    return bool(record.owner) and hmac.compare_digest(record.owner, session_hash)


def delete_owned_record(prefix: str, record_id: str) -> bool:
    """Delete one owned record; return True if a key was removed."""
    key = f'respro:{prefix}:{record_id}'
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
    """Resolve an opaque record ID to a validated, owned, path-confined file.

    Raises ``LookupError`` if the record does not exist or is not owned by the
    session (callers map this to 404 to avoid confirming existence), and
    ``ValueError`` if the resolved path escapes the allowed roots.
    """
    record = fetch_owned_record(prefix, record_id)
    if not owner_matches(record, session_hash):
        raise LookupError(f'{prefix} not found.')
    if record.canonical_path is None:
        raise LookupError(f'{prefix} record has no canonical path.')
    resolved = Path(record.canonical_path).expanduser().resolve()
    if not _is_within_allowed_roots(resolved, allowed_roots):
        raise ValueError(f'{prefix} path is outside allowed directory.')
    return resolved


def _is_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return whether a resolved path is contained in one of the allowed roots."""
    for root in allowed_roots:
        if path == root or root in path.parents:
            return True
    return False
