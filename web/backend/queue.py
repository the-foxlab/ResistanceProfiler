"""
Job queue factory for the web backend.

Provides a FastAPI-compatible dependency that creates an RQ queue connected to
Redis. Override `get_queue` in tests by injecting a queue backed by fakeredis.
"""

from __future__ import annotations

import os

import redis
from rq import Queue

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV


def get_queue() -> Queue:
    """Return an RQ queue connected to Redis (configured via REDIS_URL)."""
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    default_timeout = int(
        os.getenv(WEB_ENV.job_timeout, str(WEB_BACKEND_CONFIG.defaults.job_timeout_seconds))
    )
    connection = redis.Redis.from_url(redis_url)
    return Queue(
        WEB_BACKEND_CONFIG.defaults.profile_queue_name,
        connection=connection,
        default_timeout=default_timeout,
    )
