"""RQ worker entrypoint configured with ``JSONSerializer`` to match ``web.backend.queue``.

Run via ``python -m web.backend.worker``.
"""

from __future__ import annotations

import os
from typing import Any

import redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV


def _resolve_redis_connection(redis_url: str | None = None) -> redis.Redis:
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
    """Start an RQ worker draining *queue_names* using ``JSONSerializer``."""
    names = queue_names if queue_names is not None else (WEB_BACKEND_CONFIG.defaults.profile_queue_name,)
    conn = connection if connection is not None else _resolve_redis_connection()
    cls = worker_class if worker_class is not None else Worker

    queues = [Queue(name, connection=conn, serializer=JSONSerializer) for name in names]
    worker = cls(queues, connection=conn, serializer=JSONSerializer, **worker_kwargs)
    worker.work(burst=burst)
    return worker


if __name__ == '__main__':
    run_worker()
