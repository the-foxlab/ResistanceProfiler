"""Unit tests for web backend queued job execution behavior."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock

import fakeredis
import pytest
from fastapi import HTTPException
from rq import Queue
from rq.job import Job

from web.backend.jobs import _run_job_with_logging
from web.backend.routes.jobs import _redact_result_paths
from web.backend.services import session as session_service


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


class TestJobResultPathRedaction:
    @pytest.fixture(autouse=True)
    def _owned_records(self, monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
        connection = fakeredis.FakeRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: connection)
        return connection

    def test_missing_job_record_does_not_return_paths(self) -> None:
        result = {'report_html_path': '/private/results/sample.report.html'}

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('missing-job', 'owner', result)

        assert exc.value.status_code == 404
        assert '/private/' not in str(exc.value.detail)

    def test_changed_job_owner_does_not_return_paths(self) -> None:
        session_service.record_job(session_hash='another-owner', upload_ids=[], job_id='job-1')
        result = {'report_html_path': '/private/results/sample.report.html'}

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', result)

        assert exc.value.status_code == 404
        assert '/private/' not in str(exc.value.detail)

    @pytest.mark.parametrize('stored_ids', ['valid-id', 'id-1,id-2,id-3', 'id-1,,id-2'])
    def test_mismatched_stored_ids_do_not_return_paths(
        self, _owned_records: fakeredis.FakeRedis, stored_ids: str,
    ) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        _owned_records.hset('respro:job:job-1', 'artifact_ids', stored_ids)
        result = {
            'report_html_path': '/private/results/sample.report.html',
            'report_json_path': '/private/results/sample.results.json',
        }

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', result)

        assert exc.value.status_code == 500
        assert '/private/' not in str(exc.value.detail)

    def test_persisted_path_is_not_returned_as_an_artifact_id(
        self, _owned_records: fakeredis.FakeRedis,
    ) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        _owned_records.hset('respro:job:job-1', 'artifact_ids', '/private/results/leaked')

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', {'report_html_path': '/private/report.html'})

        assert exc.value.status_code == 500
        assert '/private/' not in str(exc.value.detail)

    @pytest.mark.parametrize(
        ('artifact_owner', 'artifact_path'),
        [('other-owner', '/private/report.html'), ('owner', '/private/other.html')],
    )
    def test_persisted_id_must_match_artifact_owner_and_path(
        self, _owned_records: fakeredis.FakeRedis, artifact_owner: str, artifact_path: str,
    ) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        artifact_id = session_service.record_artifact(
            session_hash=artifact_owner, canonical_path=Path(artifact_path), media_type='text/html',
        )
        _owned_records.hset('respro:job:job-1', 'artifact_ids', artifact_id)

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', {'report_html_path': '/private/report.html'})

        assert exc.value.status_code == 500
        assert '/private/' not in str(exc.value.detail)

    def test_missing_persisted_artifact_record_is_rejected(
        self, _owned_records: fakeredis.FakeRedis,
    ) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        _owned_records.hset('respro:job:job-1', 'artifact_ids', 'missing-id')

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', {'report_html_path': '/private/report.html'})

        assert exc.value.status_code == 404
        assert '/private/' not in str(exc.value.detail)

    def test_persisted_artifact_media_type_must_match_field(
        self, _owned_records: fakeredis.FakeRedis,
    ) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        artifact_id = session_service.record_artifact(
            session_hash='owner', canonical_path=Path('/private/report.html'), media_type='application/pdf',
        )
        _owned_records.hset('respro:job:job-1', 'artifact_ids', artifact_id)

        with pytest.raises(HTTPException) as exc:
            _redact_result_paths('job-1', 'owner', {'report_html_path': '/private/report.html'})

        assert exc.value.status_code == 500
        assert '/private/' not in str(exc.value.detail)

    def test_valid_ids_are_stable_and_follow_present_fields(self) -> None:
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-1')
        result = {
            'report_html_path': '/private/results/sample.report.html',
            'report_pdf_path': '/private/results/sample.report.pdf',
            'mode': 'fasta',
        }

        first = _redact_result_paths('job-1', 'owner', result)
        second = _redact_result_paths('job-1', 'owner', result)

        assert first == second
        assert first['artifact_ids'] == [first['report_html_path'], first['report_pdf_path']]
        assert all('/' not in artifact_id for artifact_id in first['artifact_ids'])
        assert first['mode'] == 'fasta'

    def test_concurrent_redaction_claims_artifact_ids_atomically_with_result_ttl(
        self,
        _owned_records: fakeredis.FakeRedis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import concurrent.futures
        import threading

        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '180')
        session_service.record_job(session_hash='owner', upload_ids=[], job_id='job-concurrent')
        result = {
            'report_html_path': '/private/results/sample.report.html',
            'report_json_path': '/private/results/sample.results.json',
        }
        barrier = threading.Barrier(4)
        tls = threading.local()
        orig_record_artifact = session_service.record_artifact

        def _sync_record(**kwargs):
            if not getattr(tls, 'entered', False):
                tls.entered = True
                barrier.wait(timeout=1.0)
            return orig_record_artifact(**kwargs)

        monkeypatch.setattr('web.backend.routes.jobs.record_artifact', _sync_record)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(lambda _: _redact_result_paths('job-concurrent', 'owner', result), range(4)))

        first_ids = outputs[0]['artifact_ids']
        assert all(out['artifact_ids'] == first_ids for out in outputs)
        art_keys = list(_owned_records.scan_iter('respro:artifact:*'))
        assert len(art_keys) == 2
        for key in art_keys:
            assert 1 <= _owned_records.ttl(key) <= 180

    def test_cancel_started_job_handles_stop_race_and_queued_transition(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import tempfile

        from fastapi.testclient import TestClient
        from rq.exceptions import InvalidJobOperation, NoSuchJobError

        from web.backend.main import create_app
        from web.backend.queue import get_queue
        from web.backend.startup_config import StartupConfig, build_project_db_uuid_index
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pd_dir = root / 'project_databases'
            up_dir = root / 'uploads'
            res_dir = root / 'results'
            pd_dir.mkdir()
            up_dir.mkdir()
            res_dir.mkdir()
            cfg = StartupConfig(
                project_databases_dir=pd_dir,
                uploads_dir=up_dir,
                results_dir=res_dir,
                data_dir=root,
                allowed_roots=(pd_dir, up_dir, res_dir),
                project_db_uuid_index=build_project_db_uuid_index(pd_dir),
            )
            fake_q = Mock()
            fake_q.connection = fakeredis.FakeRedis()
            app = create_app(startup_config=cfg)
            app.dependency_overrides[get_queue] = lambda: fake_q
            client = TestClient(app)

            client.post('/api/session/cleanup', json={'upload_ids': [], 'artifact_ids': []})
            tok = client.cookies.get(session_service.SESSION_COOKIE_NAME)
            assert tok is not None
            sh = session_service.hash_session_token(tok)
            session_service.record_job(session_hash=sh, upload_ids=[], job_id='race-job')

            started_job = Mock()
            started_job.get_status.return_value = 'started'
            monkeypatch.setattr('web.backend.routes.jobs.Job.fetch', lambda *_a, **_kw: started_job)
            monkeypatch.setattr(
                'web.backend.routes.jobs.send_stop_job_command',
                lambda *_a, **_kw: (_ for _ in ()).throw(NoSuchJobError('already finished')),
            )
            assert client.delete('/api/jobs/race-job').status_code == 204

            # Queued -> started race during cancel() falls back to send_stop_job_command.
            transition_job = Mock()
            transition_job.get_status.side_effect = ['queued', 'started']
            transition_job.cancel.side_effect = InvalidJobOperation('already started')
            stop_calls: list[str] = []
            monkeypatch.setattr('web.backend.routes.jobs.Job.fetch', lambda *_a, **_kw: transition_job)
            monkeypatch.setattr(
                'web.backend.routes.jobs.send_stop_job_command',
                lambda _conn, jid, **_kw: stop_calls.append(jid),
            )
            assert client.delete('/api/jobs/race-job').status_code == 204
            assert stop_calls == ['race-job']


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
