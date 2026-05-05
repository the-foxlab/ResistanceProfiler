"""Unit tests for queue factory behavior in web backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web.backend.queue import get_batch_queue, get_queue


class TestQueueFactory:
    def test_get_queue_uses_profile_queue_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_defaults = SimpleNamespace(
            redis_url='redis://default/0',
            profile_queue_name='profile-jobs',
            batch_queue_name='batch-jobs',
            job_timeout_seconds=123,
            job_retry_max=0,
            job_retry_intervals_seconds=(30,),
        )
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=fake_defaults))
        monkeypatch.delenv('REDIS_URL', raising=False)
        mock_connection = Mock()
        monkeypatch.setattr('web.backend.queue.redis.Redis.from_url', lambda _url: mock_connection)

        queue = get_queue()

        assert queue.name == 'profile-jobs'

    def test_get_batch_queue_uses_batch_queue_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_defaults = SimpleNamespace(
            redis_url='redis://default/0',
            profile_queue_name='profile-jobs',
            batch_queue_name='batch-jobs',
            job_timeout_seconds=123,
            job_retry_max=0,
            job_retry_intervals_seconds=(30,),
        )
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=fake_defaults))
        monkeypatch.delenv('REDIS_URL', raising=False)
        mock_connection = Mock()
        monkeypatch.setattr('web.backend.queue.redis.Redis.from_url', lambda _url: mock_connection)

        queue = get_batch_queue()

        assert queue.name == 'batch-jobs'
