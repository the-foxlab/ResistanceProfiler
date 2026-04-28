"""Unit tests for web backend queued job execution behavior."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

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
