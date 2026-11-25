"""RQ worker entrypoint configured with ``JSONSerializer`` to match ``web.backend.queue``.

Run via ``python -m web.backend.worker``.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import redis
from rq import Queue
from rq import Worker as _RQWorker
from rq.serializers import JSONSerializer

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
    env_str,
    redact_redis_text,
    sanitize_redis_client_error,
)

logger = logging.getLogger(__name__)

_WORKER_SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
_HORSE_TERMINATE_GRACE_SECONDS = 5.0
_redact_redis_text = redact_redis_text

_WORKER_POOL_LOCK = threading.Lock()
_WORKER_POOLS: dict[str, redis.ConnectionPool] = {}


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f'/proc/{pid}/stat')
    if stat_path.is_file():
        try:
            stat_text = stat_path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return True
        rparen = stat_text.rfind(')')
        if rparen != -1 and stat_text[rparen + 2 : rparen + 3] == 'Z':
            return False
    return True


class Worker(_RQWorker):
    """RQ worker whose ``kill_horse`` escalates ``SIGTERM`` before ``SIGKILL`` on the horse process group."""

    def kill_horse(self, sig: signal.Signals = signal.SIGTERM) -> None:
        horse_pid = getattr(self, 'horse_pid', 0) or 0
        if horse_pid <= 0:
            return
        if sig == signal.SIGKILL:
            super().kill_horse(sig)
            return
        try:
            pgid = os.getpgid(horse_pid)
            os.killpg(pgid, sig)
            self.log.info('Worker %s: sent %s to horse pid %s', self.name, sig.name, horse_pid)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                self.log.debug('Worker %s: horse already dead', self.name)
                return
            raise

        deadline = time.monotonic() + _HORSE_TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not _is_process_alive(horse_pid):
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(pgid, signal.SIGKILL)
                return
            time.sleep(0.05)

        try:
            os.killpg(pgid, signal.SIGKILL)
            self.log.info('Worker %s: escalated SIGKILL to horse pid %s', self.name, horse_pid)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                self.log.debug('Worker %s: horse exited before SIGKILL', self.name)
                return
            raise


def reset_redis_pool() -> None:
    """Clear cached worker Redis connection pools (test helper)."""
    with _WORKER_POOL_LOCK:
        pools = list(_WORKER_POOLS.values())
        _WORKER_POOLS.clear()
    for pool in pools:
        with contextlib.suppress(Exception):
            pool.disconnect()


def _resolve_redis_connection(redis_url: str | None = None) -> redis.Redis:
    url = (redis_url or '').strip() or env_str(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    with _WORKER_POOL_LOCK:
        pool = _WORKER_POOLS.get(url)
        if pool is None:
            try:
                pool = redis.ConnectionPool.from_url(
                    url,
                    socket_connect_timeout=_WORKER_SOCKET_CONNECT_TIMEOUT_SECONDS,
                    socket_timeout=None,
                )
            except ValueError as exc:
                sanitized_msg = sanitize_redis_client_error(str(exc), url)
                if sanitized_msg != str(exc):
                    raise ValueError(sanitized_msg) from None
                raise
            _WORKER_POOLS[url] = pool
    return redis.Redis(connection_pool=pool)


def run_worker(
    *,
    queue_names: tuple[str, ...] | None = None,
    connection: redis.Redis | None = None,
    burst: bool = False,
    worker_class: type[_RQWorker] | None = None,
    **worker_kwargs: Any,
) -> _RQWorker:
    """Start an RQ worker draining *queue_names* using ``JSONSerializer``."""
    names = (
        queue_names
        if queue_names is not None
        else tuple(
            dict.fromkeys((
                WEB_BACKEND_CONFIG.defaults.profile_queue_name,
                WEB_BACKEND_CONFIG.defaults.batch_queue_name,
            ))
        )
    )
    cls = worker_class if worker_class is not None else Worker

    try:
        conn = connection if connection is not None else _resolve_redis_connection()
        queues = [Queue(name, connection=conn, serializer=JSONSerializer) for name in names]
        worker = cls(queues, connection=conn, serializer=JSONSerializer, **worker_kwargs)
        worker.work(burst=burst)
    except (redis.RedisError, OSError, ValueError) as exc:
        if connection is None:
            err_url = env_str(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
            sanitized_msg = sanitize_redis_client_error(str(exc), err_url)
        else:
            sanitized_msg = _redact_redis_text(str(exc))
        logger.error('Worker Redis failure: %s', sanitized_msg)
        if connection is None:
            reset_redis_pool()
        if sanitized_msg != str(exc):
            try:
                sanitized_exc: Exception = type(exc)(sanitized_msg)
            except Exception:  # noqa: BLE001 — fallback when exception class requires non-str args
                sanitized_exc = RuntimeError(sanitized_msg)
            raise sanitized_exc from None
        raise
    return worker


if __name__ == '__main__':
    run_worker()
