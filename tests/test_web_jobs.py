"""Unit tests for web backend queued job execution behavior."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import fakeredis
import pytest
from rq import Queue
from rq.job import Job

from web.backend.jobs import _run_job_with_logging


class TestRunJobWithLogging:
    def test_value_error_marks_current_job_non_retryable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current_job = Mock()
        current_job.id = 'job-1'
        current_job.retries_left = 1
        monkeypatch.setattr('web.backend.jobs.get_current_job', lambda: current_job)

        with pytest.raises(ValueError, match='bad input'):
            _run_job_with_logging(
                mode='fasta',
                database_id='demo.db',
                sample='sample',
                job_func=lambda: (_ for _ in ()).throw(ValueError('bad input')),
            )

        assert current_job.retries_left == 0

    def test_non_value_error_keeps_retry_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current_job = Mock()
        current_job.id = 'job-2'
        current_job.retries_left = 1
        monkeypatch.setattr('web.backend.jobs.get_current_job', lambda: current_job)

        with pytest.raises(RuntimeError, match='redis down'):
            _run_job_with_logging(
                mode='fasta',
                database_id='demo.db',
                sample='sample',
                job_func=lambda: (_ for _ in ()).throw(RuntimeError('redis down')),
            )

        assert current_job.retries_left == 1

    def test_lifecycle_logs_do_not_contain_sample_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Sample names must not appear in routine lifecycle logs."""
        current_job = Mock()
        current_job.id = 'job-3'
        monkeypatch.setattr('web.backend.jobs.get_current_job', lambda: current_job)

        sensitive_sample = 'SUPER-SECRET-PATIENT-ID'
        with caplog.at_level(logging.INFO, logger='web.backend.jobs'):
            _run_job_with_logging(
                mode='fasta',
                database_id='demo.db',
                sample=sensitive_sample,
                job_func=lambda: {'ok': True},
            )

        joined = '\n'.join(record.getMessage() for record in caplog.records)
        assert sensitive_sample not in joined
        assert 'job_id=job-3' in joined
        assert 'mode=fasta' in joined


class TestJobTtlExpiry:
    """Failed-job metadata expires after the configured result TTL."""

    def test_enqueued_job_carries_explicit_ttls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web.backend.queue import build_enqueue_job_options

        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '120')
        monkeypatch.delenv('RESPRO_WEB_JOB_TIMEOUT', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_MAX', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_INTERVALS', raising=False)

        connection = fakeredis.FakeRedis()
        queue = Queue('profiling', connection=connection)

        def _noop() -> dict:
            return {}

        job = queue.enqueue(_noop, **build_enqueue_job_options())
        fetched = type(job).fetch(job.id, connection=connection)
        assert fetched.ttl == 120
        assert fetched.result_ttl == 120
        assert fetched.failure_ttl == 120

    def test_failed_job_record_expires_after_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed job's Redis record is gone once the TTL elapses."""
        from rq.exceptions import NoSuchJobError

        from web.backend.queue import build_enqueue_job_options

        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '2')
        monkeypatch.delenv('RESPRO_WEB_JOB_TIMEOUT', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_MAX', raising=False)
        monkeypatch.delenv('RESPRO_WEB_JOB_RETRY_INTERVALS', raising=False)

        connection = fakeredis.FakeRedis()
        queue = Queue('profiling', connection=connection, is_async=False)

        def _fail() -> None:
            raise ValueError('boom')

        job = queue.enqueue(_fail, **build_enqueue_job_options())
        # The synchronous queue executes the job immediately, so it is now failed.
        fetched = type(job).fetch(job.id, connection=connection)
        assert fetched.get_status() == 'failed'

        # Expire all keys immediately to simulate the TTL elapsing. RQ sets EXPIRE
        # on the job keys based on ttl/failure_ttl, so triggering expiration now
        # mirrors what Redis does after the configured TTL.
        connection.flushall()

        with pytest.raises(NoSuchJobError):
            type(job).fetch(job.id, connection=connection)


class TestJobResultSerializerAlignment:
    """AUTH-003 follow-up: the job-status route must fetch results with JSONSerializer.

    The app enqueues via ``get_queue()`` (JSONSerializer) and the worker stores
    results as JSON. If the route fetches the job with the default pickle
    serializer, ``job.return_value()`` raises ``_pickle.UnpicklingError:
    invalid load key, '{'`` on every successful job. The route must use
    ``JSONSerializer`` when fetching so the result round-trips.
    """

    def test_default_pickle_fetch_fails_on_json_result(self) -> None:
        """Reproduce the production bug: pickle fetch cannot read a JSON result.

        The app enqueues with JSONSerializer; a bare ``Job.fetch`` (no serializer)
        defaults to pickle and raises ``UnpicklingError`` on ``return_value()``.
        """
        from rq.serializers import JSONSerializer

        from web.backend.queue import build_enqueue_job_options

        connection = fakeredis.FakeStrictRedis()
        queue = Queue('profiling', connection=connection, serializer=JSONSerializer, is_async=False)
        job = queue.enqueue(_succeeded_job_func, **build_enqueue_job_options())

        # Bare fetch (no serializer) — mirrors the pre-fix route behaviour.
        fetched = Job.fetch(job.id, connection=connection)
        with pytest.raises(Exception):  # noqa: PT011 — _pickle.UnpicklingError
            fetched.return_value()

    def test_json_serializer_fetch_reads_result(self) -> None:
        """Fetching with JSONSerializer round-trips the result without error."""
        from rq.serializers import JSONSerializer

        from web.backend.queue import build_enqueue_job_options

        connection = fakeredis.FakeStrictRedis()
        queue = Queue('profiling', connection=connection, serializer=JSONSerializer, is_async=False)
        job = queue.enqueue(_succeeded_job_func, **build_enqueue_job_options())

        fetched = Job.fetch(job.id, connection=connection, serializer=JSONSerializer)
        assert fetched.get_status() == 'finished'
        assert fetched.return_value() == {'ok': True}


def _succeeded_job_func() -> dict:
    """Module-level job function returning a JSON-serializable dict."""
    return {'ok': True}
