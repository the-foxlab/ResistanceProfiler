"""
RQ worker entrypoint for the web backend.

The web app enqueues jobs with ``rq.serializers.JSONSerializer`` (see
``web.backend.queue._build_queue``). The worker must use the same serializer;
otherwise every job fails with ``DeserializationError: invalid load key, '['``
because the worker tries to pickle-load a JSON payload.

Run as a module from the container or locally:

    python -m web.backend.worker

This replaces the bare ``rq worker`` CLI command previously used in the
compose files, which defaulted to pickle and caused the serializer mismatch.
"""

from __future__ import annotations

import os
from typing import Any

import redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV


def _resolve_redis_connection(redis_url: str | None = None) -> redis.Redis:
    """Build a Redis connection from the given URL or the configured default."""
    url = redis_url or os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    return redis.Redis.from_url(url)


def run_worker(
    *,
    queue_names: tuple[str, ...] | None = None,
    connection: redis.Redis | None = None,
    burst: bool = False,
    worker_class: type[Worker] | None = None,
    **worker_kwargs: Any,
) -> Worker:
    """Start an RQ worker using ``JSONSerializer`` to match the app side.

    Parameters
    ----------
    queue_names:
        Names of the queues to drain. Defaults to the profiling queue from
        ``WEB_BACKEND_CONFIG`` — the only queue the app enqueues to.
    connection:
        An existing Redis connection. When ``None`` a connection is built from
        ``REDIS_URL`` / the configured default, mirroring ``_build_queue``.
    burst:
        When ``True`` the worker exits after draining all pending jobs. Used by
        tests; the production worker runs indefinitely (``burst=False``).
    worker_class:
        The RQ ``Worker`` subclass to instantiate. Defaults to ``Worker`` (which
        forks a child per job). Tests backed by fakeredis pass ``SimpleWorker``
        so jobs execute in-process against the shared in-memory connection.
    worker_kwargs:
        Extra keyword arguments forwarded to ``worker_class`` (e.g.
        ``logging_level``).
    """
    if queue_names is None:
        queue_names = (WEB_BACKEND_CONFIG.defaults.profile_queue_name,)
    if connection is None:
        connection = _resolve_redis_connection()
    if worker_class is None:
        worker_class = Worker

    queues = [Queue(name, connection=connection, serializer=JSONSerializer) for name in queue_names]
    worker = worker_class(
        queues,
        connection=connection,
        serializer=JSONSerializer,
        **worker_kwargs,
    )
    worker.work(burst=burst)
    return worker


if __name__ == '__main__':
    run_worker()
