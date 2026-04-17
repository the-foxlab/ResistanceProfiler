"""
Job queue factory for the web backend.

Provides a FastAPI-compatible dependency that creates an RQ queue connected to
Redis. Override `get_queue` in tests by injecting a queue backed by fakeredis.
"""

from __future__ import annotations

import os

import redis
from rq import Queue


def get_queue() -> Queue:
    """Return an RQ queue connected to Redis (configured via REDIS_URL)."""
    redis_url = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
    default_timeout = int(os.getenv('RESPRO_WEB_JOB_TIMEOUT', '3600'))
    connection = redis.Redis.from_url(redis_url)
    return Queue('profiling', connection=connection, default_timeout=default_timeout)
