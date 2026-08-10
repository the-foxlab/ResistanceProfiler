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


class TestWorkerSerializer:
    """
    The web app enqueues jobs with ``rq.serializers.JSONSerializer`` (see ``_build_queue``).
    If the worker is started with the bare ``rq worker`` CLI (which defaults to pickle),
    every job fails with ``DeserializationError: invalid load key, '['`` because the
    worker tries to pickle-load a JSON payload. The worker entrypoint must construct
    its ``Worker`` with ``serializer=JSONSerializer`` so both sides agree.
    """

    def test_run_worker_entrypoint_exists(self) -> None:
        """A ``run_worker`` callable is importable from the worker entrypoint module."""
        from web.backend.worker import run_worker

        assert callable(run_worker)

    def test_run_worker_constructs_worker_with_json_serializer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``run_worker`` builds a ``Worker`` whose serializer is ``JSONSerializer``."""
        import fakeredis
        from rq.serializers import JSONSerializer

        from web.backend import worker

        fake_connection = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(worker, '_resolve_redis_connection', lambda *a, **kw: fake_connection)

        captured: dict = {}
        original_worker = worker.Worker

        class _CapturingWorker(original_worker):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                captured['serializer'] = kwargs.get('serializer')
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(worker, 'Worker', _CapturingWorker)
        worker.run_worker(burst=True)

        assert captured['serializer'] is JSONSerializer

    def test_round_trip_app_enqueue_worker_executes(self) -> None:
        """A job enqueued via ``get_queue`` (JSON) is executed by the worker entrypoint.

        This is the end-to-end regression for the ``DeserializationError`` bug: the app
        enqueues with ``JSONSerializer`` and the worker must dequeue with the same
        serializer, otherwise the job never runs.
        """
        import fakeredis
        from rq import Queue, SimpleWorker
        from rq.serializers import JSONSerializer

        from web.backend import worker

        connection = fakeredis.FakeStrictRedis()
        # Enqueue side: mirrors the app's _build_queue (JSONSerializer).
        queue = Queue('profiling', connection=connection, serializer=JSONSerializer)
        job = queue.enqueue(_round_trip_job_func)

        # Worker side: run_worker must drain the queue with JSONSerializer.
        # SimpleWorker runs jobs in-process (no fork) so the fakeredis
        # in-memory connection is shared between enqueue and execution.
        worker.run_worker(connection=connection, burst=True, worker_class=SimpleWorker)

        fetched = type(job).fetch(job.id, connection=connection, serializer=JSONSerializer)
        assert fetched.return_value() == 'worker-ran'


def _round_trip_job_func() -> str:
    """Module-level job function importable by the forked RQ worker."""
    return 'worker-ran'
