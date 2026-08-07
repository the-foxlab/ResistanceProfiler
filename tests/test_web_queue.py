"""Unit tests for queue factory behavior in web backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue


def _fake_defaults() -> SimpleNamespace:
    return SimpleNamespace(
        redis_url='redis://default/0',
        profile_queue_name='profile-jobs',
        batch_queue_name='batch-jobs',
        job_timeout_seconds=123,
        job_retry_max=0,
        job_retry_intervals_seconds=(30,),
        result_ttl_seconds=86400,
    )


class TestQueueFactory:
    def test_get_queue_uses_profile_queue_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.delenv('REDIS_URL', raising=False)
        mock_connection = Mock()
        monkeypatch.setattr('web.backend.queue.redis.Redis.from_url', lambda _url: mock_connection)

        queue = get_queue()

        assert queue.name == 'profile-jobs'

    def test_get_batch_queue_uses_batch_queue_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.delenv('REDIS_URL', raising=False)
        mock_connection = Mock()
        monkeypatch.setattr('web.backend.queue.redis.Redis.from_url', lambda _url: mock_connection)

        queue = get_batch_queue()

        assert queue.name == 'batch-jobs'


class TestEnqueueJobOptions:
    """Explicit ttl/result_ttl/failure_ttl aligned to RESPRO_WEB_RESULT_TTL."""

    def test_enqueue_options_include_explicit_ttls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '86400')
        monkeypatch.delenv('RESPRO_WEB_JOB_TIMEOUT', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_MAX', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_INTERVALS', raising=False)

        options = build_enqueue_job_options()

        assert options['ttl'] == 86400
        assert options['result_ttl'] == 86400
        assert options['failure_ttl'] == 86400
        assert options['job_timeout'] == 123

    def test_enqueue_options_ttls_track_configured_result_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A short RESPRO_WEB_RESULT_TTL is reflected in all three TTL options."""
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '60')
        monkeypatch.delenv('RESPRO_WEB_JOB_TIMEOUT', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_MAX', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_INTERVALS', raising=False)

        options = build_enqueue_job_options()

        assert options['ttl'] == 60
        assert options['result_ttl'] == 60
        assert options['failure_ttl'] == 60

    def test_enqueue_options_rejects_non_positive_result_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '0')

        with pytest.raises(ValueError, match='RESPRO_WEB_RESULT_TTL'):
            build_enqueue_job_options()
