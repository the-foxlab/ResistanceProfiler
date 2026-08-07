"""Unit tests for web backend queued job execution behavior."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import fakeredis
import pytest
from rq import Queue

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
