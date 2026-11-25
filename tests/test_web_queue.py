"""Unit tests for queue factory behavior in web backend."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
import redis
from fastapi import HTTPException
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from web.backend import jobs as jobs_module
from web.backend import queue as queue_module
from web.backend import rate_limit as rate_limit_module
from web.backend import worker as worker_module
from web.backend.config import WEB_BACKEND_CONFIG, sanitize_redis_client_error
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue
from web.backend.services import session as session_service


@pytest.fixture(autouse=True)
def _reset_queue_and_quota_state() -> None:
    session_service.reset_memory_stores()
    queue_module.reset_redis_pool()
    rate_limit_module.reset_redis_pool()
    worker_module.reset_redis_pool()
    rate_limit_module._SAMPLE_QUOTA_COUNTER.clear()
    yield
    session_service.reset_memory_stores()
    queue_module.reset_redis_pool()
    rate_limit_module.reset_redis_pool()
    worker_module.reset_redis_pool()
    rate_limit_module._SAMPLE_QUOTA_COUNTER.clear()


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
        captured: list[tuple[str, dict[str, Any]]] = []
        fake_pool = fakeredis.FakeStrictRedis().connection_pool
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, url, **kwargs: captured.append((url, kwargs)) or fake_pool),
        )

        queue = get_queue()

        assert queue.name == 'profile-jobs'
        assert queue.connection.connection_pool is fake_pool
        assert captured == [
            ('redis://default/0', {'socket_connect_timeout': 2.0, 'socket_timeout': 2.0}),
        ]

    def test_get_batch_queue_uses_batch_queue_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.delenv('REDIS_URL', raising=False)
        fake_pool = fakeredis.FakeStrictRedis().connection_pool
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kwargs: fake_pool),
        )

        queue = get_batch_queue()

        assert queue.name == 'batch-jobs'
        assert queue.connection.connection_pool is fake_pool

    def test_queues_share_and_reset_connection_pool_and_redact_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr('web.backend.queue.WEB_BACKEND_CONFIG', SimpleNamespace(defaults=_fake_defaults()))
        monkeypatch.setenv('REDIS_URL', 'redis://default:queue-secret@127.0.0.1:6379/0')
        created_pools: list[Any] = []

        def _fake_pool_from_url(cls: type[redis.ConnectionPool], _url: str, **_kwargs: Any) -> Any:
            pool = fakeredis.FakeStrictRedis().connection_pool
            created_pools.append(pool)
            return pool

        monkeypatch.setattr(redis.ConnectionPool, 'from_url', classmethod(_fake_pool_from_url))

        q1 = get_queue()
        q2 = get_batch_queue()
        assert q1.connection.connection_pool is q2.connection.connection_pool
        assert len(created_pools) == 1

        queue_module.reset_redis_pool()
        q3 = get_queue()
        assert q3.connection.connection_pool is not q1.connection.connection_pool
        assert len(created_pools) == 2

        queue_module.reset_redis_pool()
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(
                lambda cls, url, **_kw: (_ for _ in ()).throw(
                    redis.ConnectionError(f'connect failed for {url}')
                )
            ),
        )
        with caplog.at_level(logging.WARNING, logger='web.backend.queue'):
            with pytest.raises(redis.ConnectionError):
                get_queue()
        assert 'queue-secret' not in caplog.text
        assert '***' in caplog.text


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


def _make_request(path: str = '/api/profile/fasta', client_host: str = '198.51.100.7') -> Request:
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': path,
        'headers': [],
        'client': (client_host, 12345),
        'query_string': b'',
        'scheme': 'http',
        'server': ('testserver', 80),
    }
    return Request(scope)


class TestWorkerBehavior:
    def test_run_worker_drains_distinct_profile_and_batch_queues_and_deduplicates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rq import Queue, SimpleWorker
        from rq.job import Job
        from rq.serializers import JSONSerializer

        conn = fakeredis.FakeStrictRedis()
        distinct_defaults = replace(
            WEB_BACKEND_CONFIG.defaults,
            profile_queue_name='q-profile',
            batch_queue_name='q-batch',
        )
        monkeypatch.setattr(worker_module, 'WEB_BACKEND_CONFIG', SimpleNamespace(defaults=distinct_defaults))

        q_prof = Queue('q-profile', connection=conn, serializer=JSONSerializer)
        q_batch = Queue('q-batch', connection=conn, serializer=JSONSerializer)
        j1 = q_prof.enqueue(_round_trip_job_func)
        j2 = q_batch.enqueue(_round_trip_job_func)

        drained = worker_module.run_worker(connection=conn, burst=True, worker_class=SimpleWorker)
        assert [q.name for q in drained.queues] == ['q-profile', 'q-batch']
        assert Job.fetch(j1.id, connection=conn, serializer=JSONSerializer).return_value() == 'worker-ran'
        assert Job.fetch(j2.id, connection=conn, serializer=JSONSerializer).return_value() == 'worker-ran'

        same_defaults = replace(
            WEB_BACKEND_CONFIG.defaults,
            profile_queue_name='q-shared',
            batch_queue_name='q-shared',
        )
        monkeypatch.setattr(worker_module, 'WEB_BACKEND_CONFIG', SimpleNamespace(defaults=same_defaults))
        deduped = worker_module.run_worker(connection=conn, burst=True, worker_class=SimpleWorker)
        assert [q.name for q in deduped.queues] == ['q-shared']

    def test_worker_pool_uses_unbounded_socket_timeout_and_does_not_share_api_pool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/9')
        api_pool = session_service.get_redis_pool()
        worker_conn = worker_module._resolve_redis_connection()

        assert worker_conn.connection_pool is not api_pool
        assert api_pool.connection_kwargs.get('socket_timeout') == 2.0
        assert worker_conn.connection_pool.connection_kwargs.get('socket_timeout') is None
        assert worker_conn.connection_pool.connection_kwargs.get('socket_connect_timeout') == 2.0
        assert worker_module._resolve_redis_connection().connection_pool is worker_conn.connection_pool

        worker_module.reset_redis_pool()
        assert worker_module._resolve_redis_connection().connection_pool is not worker_conn.connection_pool

    def test_worker_kill_horse_sends_sigterm_before_sigkill(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = fakeredis.FakeStrictRedis()
        instance = worker_module.Worker(['profiling'], connection=conn)
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            start_new_session=True,
        )
        sent_signals: list[int] = []
        orig_killpg = os.killpg

        def _spy_killpg(pgid: int, sig: int) -> None:
            sent_signals.append(sig)
            orig_killpg(pgid, sig)

        try:
            monkeypatch.setattr(worker_module.os, 'killpg', _spy_killpg)
            instance._horse_pid = proc.pid
            instance.kill_horse()
            proc.wait(timeout=2.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2.0)

        assert proc.returncode == -signal.SIGTERM
        assert sent_signals == [signal.SIGTERM, signal.SIGKILL]

    def test_worker_kill_horse_escalates_to_sigkill_after_grace_period(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = fakeredis.FakeStrictRedis()
        instance = worker_module.Worker(['profiling'], connection=conn)
        instance._horse_pid = 424242
        sent_signals: list[tuple[int, int]] = []

        monkeypatch.setattr(worker_module, '_HORSE_TERMINATE_GRACE_SECONDS', 0.05)
        monkeypatch.setattr(worker_module.os, 'getpgid', lambda pid: pid)
        monkeypatch.setattr(worker_module.os, 'killpg', lambda pgid, sig: sent_signals.append((pgid, sig)))
        monkeypatch.setattr(worker_module, '_is_process_alive', lambda _pid: True)

        instance.kill_horse()
        assert sent_signals == [(424242, signal.SIGTERM), (424242, signal.SIGKILL)]

    def test_worker_kill_horse_allows_respro_command_grace_margin_and_cleans_descendants(
        self,
        tmp_path: Path,
    ) -> None:
        assert worker_module._HORSE_TERMINATE_GRACE_SECONDS >= jobs_module._SUBPROCESS_TERMINATE_GRACE_SECONDS + 2.0

        ready_path = tmp_path / 'horse_ready.txt'
        horse_status_path = tmp_path / 'horse_status.txt'
        post_cancel_path = tmp_path / 'post_cancel.txt'

        inner_script = (
            'import os, signal, subprocess, sys, time; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'gc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"]); '
            f'open({str(ready_path)!r}, "w").write(f"{{os.getpid()}} {{gc.pid}}"); '
            'time.sleep(30); '
            f'open({str(post_cancel_path)!r}, "w").write("leaked")'
        )
        horse_script = (
            'import sys\n'
            'from web.backend import jobs\n'
            'try:\n'
            f'    jobs._run_respro_command([sys.executable, "-c", {inner_script!r}])\n'
            'except RuntimeError as exc:\n'
            '    if "canceled" in str(exc):\n'
            f'        open({str(horse_status_path)!r}, "w").write("canceled")\n'
            '        sys.exit(0)\n'
            '    raise\n'
        )

        conn = fakeredis.FakeStrictRedis()
        instance = worker_module.Worker(['profiling'], connection=conn)
        horse_proc = subprocess.Popen(
            [sys.executable, '-c', horse_script],
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not ready_path.is_file():
                time.sleep(0.02)
            assert ready_path.is_file()

            instance._horse_pid = horse_proc.pid
            instance.kill_horse()
            horse_proc.wait(timeout=2.0)
        finally:
            if horse_proc.poll() is None:
                horse_proc.kill()
                horse_proc.wait(timeout=2.0)

        assert horse_proc.returncode == 0
        assert horse_status_path.read_text(encoding='utf-8') == 'canceled'
        child_pid_str, gc_pid_str = ready_path.read_text(encoding='utf-8').split()
        child_pid, gc_pid = int(child_pid_str), int(gc_pid_str)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and (
            worker_module._is_process_alive(child_pid) or worker_module._is_process_alive(gc_pid)
        ):
            time.sleep(0.02)
        assert not worker_module._is_process_alive(child_pid)
        assert not worker_module._is_process_alive(gc_pid)
        assert not post_cancel_path.exists()

    def test_worker_kill_horse_kills_sigterm_ignoring_descendants_when_horse_exits_early(
        self,
        tmp_path: Path,
    ) -> None:
        gc_path = tmp_path / 'gc_pid.txt'
        horse_script = (
            'import os, signal, subprocess, sys, time; '
            'gc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"]); '
            'signal.signal(signal.SIGTERM, lambda _s, _f: os._exit(0)); '
            f'open({str(gc_path)!r}, "w").write(str(gc.pid)); '
            'time.sleep(30)'
        )

        conn = fakeredis.FakeStrictRedis()
        instance = worker_module.Worker(['profiling'], connection=conn)
        horse_proc = subprocess.Popen(
            [sys.executable, '-c', horse_script],
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not gc_path.is_file():
                time.sleep(0.02)
            assert gc_path.is_file()
            gc_pid = int(gc_path.read_text(encoding='utf-8').strip())

            instance._horse_pid = horse_proc.pid
            instance.kill_horse()
            horse_proc.wait(timeout=2.0)
        finally:
            if horse_proc.poll() is None:
                horse_proc.kill()
                horse_proc.wait(timeout=2.0)

        assert horse_proc.returncode == 0
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and worker_module._is_process_alive(gc_pid):
            time.sleep(0.02)
        assert not worker_module._is_process_alive(gc_pid)

    def test_run_worker_startup_connection_error_redacts_url_and_clears_worker_pools(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        closed_port = sock.getsockname()[1]
        sock.close()

        secret = 'StartupSecret!42'
        monkeypatch.setenv('REDIS_URL', f'redis://workeruser:{secret}@127.0.0.1:{closed_port}/0')

        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises((redis.RedisError, OSError)):
                worker_module.run_worker(burst=True)

        assert secret not in caplog.text
        assert 'Worker Redis failure' in caplog.text
        assert worker_module._WORKER_POOLS == {}

        # Also verify URL credentials embedded in BaseWorker.__init__ error text are redacted.
        caplog.clear()
        redis_url = f'redis://workeruser:{secret}@127.0.0.1:{closed_port}/0'

        class _FailingInitWorker(worker_module.Worker):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                del args, kwargs
                raise redis.ConnectionError(f'connect failed for {redis_url}')

        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises(redis.ConnectionError):
                worker_module.run_worker(burst=True, worker_class=_FailingInitWorker)

        assert secret not in caplog.text
        assert '***' in caplog.text
        assert worker_module._WORKER_POOLS == {}


class TestJobsSubprocessAndBamGuard:
    @pytest.mark.parametrize('bam_value', [None, ''])
    def test_run_profile_vcf_omits_bam_flag_when_absent_or_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        bam_value: str | None,
    ) -> None:
        captured_commands: list[list[str]] = []
        ref_fasta = tmp_path / 'ref.fasta'
        ref_fasta.write_text('>ref1\nATGC\n', encoding='utf-8')
        vcf_file = tmp_path / 'sample.vcf'
        vcf_file.write_text('##fileformat=VCFv4.2\n', encoding='utf-8')

        monkeypatch.setattr(jobs_module, '_run_respro_command', lambda cmd: captured_commands.append(cmd))
        monkeypatch.setattr(
            jobs_module,
            'artifact_paths_for_html',
            lambda html: {'report_html_path': html, 'report_json_path': html.with_suffix('.json')},
        )
        monkeypatch.setattr(
            jobs_module,
            '_load_run_payload',
            lambda _path: {'sample_name': 'S1', 'created_at': '', 'reference_name': 'ref1'},
        )

        jobs_module.run_profile_vcf(
            project_db=str(tmp_path / 'db.sqlite'),
            output_dir=str(tmp_path / 'out'),
            vcf_path=str(vcf_file),
            ref_fasta_path=str(ref_fasta),
            sample='S1',
            min_af=0.05,
            min_depth=10,
            bam_path=bam_value,
            threads=1,
        )
        assert captured_commands
        assert '--bam' not in captured_commands[0]

        jobs_module.run_profile_vcf(
            project_db=str(tmp_path / 'db.sqlite'),
            output_dir=str(tmp_path / 'out'),
            vcf_path=str(vcf_file),
            ref_fasta_path=str(ref_fasta),
            sample='S1',
            min_af=0.05,
            min_depth=10,
            bam_path=str(tmp_path / 'reads.bam'),
            threads=1,
        )
        assert '--bam' in captured_commands[1]

    def test_run_respro_command_kills_process_group_on_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        info_path = tmp_path / 'pgid_info.txt'
        child_script = (
            'import os, subprocess, sys, time; '
            'gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
            f'open({str(info_path)!r}, "w").write(f"{{os.getpgid(0)}} {{gc.pid}}"); '
            'time.sleep(30)'
        )
        monkeypatch.setattr(jobs_module, 'resolve_subprocess_timeout_seconds', lambda: 0.4)

        with pytest.raises(RuntimeError, match='timed out'):
            jobs_module._run_respro_command([sys.executable, '-c', child_script])

        pgid_str, gc_pid_str = info_path.read_text(encoding='utf-8').split()
        child_pgid = int(pgid_str)
        gc_pid = int(gc_pid_str)
        assert child_pgid != os.getpgid(0)

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not worker_module._is_process_alive(gc_pid):
                break
            time.sleep(0.02)
        assert not worker_module._is_process_alive(gc_pid)

    def test_run_respro_command_kills_stubborn_process_group_on_sigterm(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ready_path = tmp_path / 'ready.txt'
        post_cancel_path = tmp_path / 'should_not_exist.txt'
        child_script = (
            'import os, signal, subprocess, sys, time; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            f'gc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"]); '
            f'open({str(ready_path)!r}, "w").write(str(gc.pid)); '
            'time.sleep(30); '
            f'open({str(post_cancel_path)!r}, "w").write("leaked")'
        )
        monkeypatch.setattr(jobs_module, '_SUBPROCESS_TERMINATE_GRACE_SECONDS', 0.15)
        monkeypatch.setattr(jobs_module, 'resolve_subprocess_timeout_seconds', lambda: 30)

        def _send_sigterm_once_ready() -> None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not ready_path.is_file():
                time.sleep(0.02)
            os.kill(os.getpid(), signal.SIGTERM)

        prev_handler = signal.getsignal(signal.SIGTERM)
        timer = threading.Timer(0.05, _send_sigterm_once_ready)
        timer.start()
        try:
            with pytest.raises(RuntimeError, match='canceled'):
                jobs_module._run_respro_command([sys.executable, '-c', child_script])
        finally:
            timer.join(timeout=2.0)

        assert signal.getsignal(signal.SIGTERM) is prev_handler
        gc_pid = int(ready_path.read_text(encoding='utf-8').strip())
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not worker_module._is_process_alive(gc_pid):
                break
            time.sleep(0.02)
        assert not worker_module._is_process_alive(gc_pid)
        assert not post_cancel_path.exists()


class TestRateLimitAndSampleQuota:
    def test_consume_sample_quota_skips_redis_when_redis_url_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        from_url_calls: list[str] = []
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, url, **_kw: from_url_calls.append(url)),
        )
        req = _make_request()

        rate_limit_module.consume_sample_quota(req, sample_count=2, sample_limit_per_minute=3)
        with pytest.raises(HTTPException) as exc:
            rate_limit_module.consume_sample_quota(req, sample_count=2, sample_limit_per_minute=3)

        assert exc.value.status_code == 429
        assert exc.value.headers is not None
        assert int(exc.value.headers['Retry-After']) >= 1
        assert from_url_calls == []

    @pytest.mark.parametrize('use_lua_emulator', [False, True])
    def test_consume_sample_quota_over_limit_does_not_poison_remaining_quota(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_lua_emulator: bool,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        fake_client = fakeredis.FakeStrictRedis()
        if use_lua_emulator:
            def _emulated_eval(
                self_client: redis.Redis,
                _script: str | bytes,
                _numkeys: int,
                key: str,
                sample_count: int,
                limit: int,
                ttl: int,
            ) -> list[int]:
                raw_cur = self_client.get(key)
                cur = int(raw_cur) if raw_cur is not None else 0
                if cur + int(sample_count) > int(limit):
                    return [0, cur]
                updated = int(self_client.incrby(key, int(sample_count)))
                self_client.expire(key, int(ttl))
                return [1, updated]

            monkeypatch.setattr(redis.Redis, 'eval', _emulated_eval)
        monkeypatch.setattr(rate_limit_module, 'get_redis_client', lambda _url: fake_client)
        req = _make_request()

        rate_limit_module.consume_sample_quota(req, sample_count=2, sample_limit_per_minute=3)
        with pytest.raises(HTTPException) as rejected:
            rate_limit_module.consume_sample_quota(req, sample_count=2, sample_limit_per_minute=3)
        assert rejected.value.status_code == 429
        assert rejected.value.headers is not None
        assert int(rejected.value.headers['Retry-After']) >= 1

        # Remaining 1 sample in the window must still succeed (no poisoned counter).
        rate_limit_module.consume_sample_quota(req, sample_count=1, sample_limit_per_minute=3)

    def test_consume_sample_quota_redis_failure_redacts_secret_and_fails_closed_online(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = 'quota-secret-pw-9911'
        redis_url = f'redis://default:{secret}@127.0.0.1:1/0'
        monkeypatch.setenv('REDIS_URL', redis_url)

        class _DeadConn(redis.Connection):
            def connect(self) -> None:
                raise redis.ConnectionError(f'dial failed for {redis_url}')

        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: redis.ConnectionPool(connection_class=_DeadConn)),
        )
        req = _make_request()

        # Local mode logs redacted error, invalidates pool, and falls back to memory counter.
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')
        with caplog.at_level(logging.DEBUG, logger='web.backend.rate_limit'):
            rate_limit_module.consume_sample_quota(req, sample_count=1, sample_limit_per_minute=2)
        assert secret not in caplog.text
        assert '***' in caplog.text
        assert session_service._REDIS_POOL is None

        # Online mode must fail closed with HTTP 503 and still redact credentials.
        caplog.clear()
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')
        with caplog.at_level(logging.DEBUG, logger='web.backend.rate_limit'):
            with pytest.raises(HTTPException) as exc:
                rate_limit_module.consume_sample_quota(req, sample_count=1, sample_limit_per_minute=2)
        assert exc.value.status_code == 503
        assert secret not in caplog.text

    def test_consume_sample_quota_non_eval_response_error_invalidates_pool_and_raises_503_online(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')
        fake_pool = fakeredis.FakeStrictRedis().connection_pool
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: fake_pool),
        )
        monkeypatch.setattr(
            redis.Redis,
            'eval',
            lambda *_a, **_kw: (_ for _ in ()).throw(
                redis.ResponseError('WRONGTYPE Operation against a key holding the wrong kind of value')
            ),
        )
        req = _make_request()

        with pytest.raises(HTTPException) as exc:
            rate_limit_module.consume_sample_quota(req, sample_count=1, sample_limit_per_minute=5)

        assert exc.value.status_code == 503
        assert session_service._REDIS_POOL is None
        assert rate_limit_module._SAMPLE_QUOTA_COUNTER == {}

    def test_create_rate_limiter_fallback_and_route_specific_429_messages(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from limits import parse
        from slowapi.wrappers import Limit

        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        limiter = rate_limit_module.create_rate_limiter()
        assert limiter._in_memory_fallback_enabled is True
        assert limiter._storage_options.get('socket_connect_timeout') == 2.0
        assert limiter._storage_options.get('socket_timeout') == 2.0

        limit_obj = Limit(parse('5/minute'), lambda: 'k', None, False, None, None, None, 1, False)
        exc = RateLimitExceeded(limit_obj)

        upload_resp = rate_limit_module.handle_rate_limit_exceeded(_make_request('/api/upload/fasta'), exc)
        assert upload_resp.status_code == 429
        assert b'Upload rate limit exceeded. Try again later.' in upload_resp.body
        assert int(upload_resp.headers['Retry-After']) >= 1

        api_resp = rate_limit_module.handle_rate_limit_exceeded(_make_request('/api/jobs/job-1'), exc)
        assert api_resp.status_code == 429
        assert b'API rate limit exceeded. Try again later.' in api_resp.body
        assert int(api_resp.headers['Retry-After']) >= 1

    @pytest.mark.parametrize(
        ('raw_text', 'expected', 'secret_fragments'),
        [
            (
                "ConnectionError('redis://user:1234?a=1@127.0.0.1:6379')",
                "ConnectionError('redis://user:***@127.0.0.1:6379')",
                ('1234?a=1',),
            ),
            (
                'Error("redis://default:p@ss/w?o#rd@127.0.0.1:6379")',
                'Error("redis://default:***@127.0.0.1:6379")',
                ('p@ss', 'w?o#rd'),
            ),
            (
                'Error(redis://user:p@ss?1#2@[::1]:6379)',
                'Error(redis://user:***@[::1]:6379)',
                ('p@ss', 'ss?1#2'),
            ),
            (
                'Failed on redis://user:p@ss/w?o#rd@localhost.',
                'Failed on redis://user:***@localhost.',
                ('p@ss', 'w?o#rd'),
            ),
            (
                'Failed connecting to redis://user:1234?a=1@127.0.0.1:6379: Connection refused',
                'Failed connecting to redis://user:***@127.0.0.1:6379: Connection refused',
                ('1234?a=1', '1234'),
            ),
            (
                'Failed on redis://user:1234?a=1@[::1]: timeout',
                'Failed on redis://user:***@[::1]: timeout',
                ('1234?a=1', '1234'),
            ),
            (
                'Failed on unix://user:p@w/x@/var/run/redis.sock: Permission denied',
                'Failed on unix://user:***@/var/run/redis.sock: Permission denied',
                ('p@w', 'w/x'),
            ),
            (
                'Failed on unix:///var/run/redis.sock: Permission denied',
                'Failed on unix:///var/run/redis.sock: Permission denied',
                (),
            ),
            (
                'See `redis://user:1234?a=1@127.0.0.1:6379` for details',
                'See `redis://user:***@127.0.0.1:6379` for details',
                ('1234?a=1', '1234'),
            ),
            (
                '["redis://u1:p1@h1:6379","redis://u2:p2@h2"]',
                '["redis://u1:***@h1:6379","redis://u2:***@h2"]',
                ('p1', 'p2'),
            ),
            (
                'redis://u1:p1@h1:6379,redis://u2:p2@h2:6379',
                'redis://u1:***@h1:6379,redis://u2:***@h2:6379',
                ('p1', 'p2'),
            ),
            (
                'Error(redis://user:p@ss:word/x@localhost)',
                'Error(redis://user:***@localhost)',
                ('p@ss', 'ss:word/x'),
            ),
            (
                'Failed on redis://user:p@[foo/x@localhost: timeout',
                'Failed on redis://user:***@localhost: timeout',
                ('p@[foo', '[foo/x'),
            ),
            (
                'primary=redis+unix://u1:p@ss/w@/tmp/a.sock,replica=redis+unix://u2:p@ss/w@/tmp/b.sock',
                'primary=redis+unix://u1:***@/tmp/a.sock,replica=redis+unix://u2:***@/tmp/b.sock',
                ('p@ss', 'ss/w'),
            ),
            (
                '**redis://user:1234?a=1@127.0.0.1:6379**',
                '**redis://user:***@127.0.0.1:6379**',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://[user]:pass/x@localhost',
                'redis://***@localhost',
                ('pass/x',),
            ),
            (
                'unix:///var/run/redis.sock:redis://u2:secret2@h2:6379',
                'unix:///var/run/redis.sock:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]:6379&url[0]=redis://u2:secret2@h2:6379',
                'redis://[::1]:6379&url[0]=redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://user:p:redis://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p:redis',),
            ),
            (
                'redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:secret2@h2:6379',
                'redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'https://example.com/path?email=a@b.com&next=1,redis://h2:6379/0?password=secret2',
                'https://example.com/path?email=a@b.com&next=1,redis://h2:6379/0?password=***',
                ('secret2',),
            ),
            (
                '<a href="redis://user:1234?a=1@127.0.0.1:6379">here</a>',
                '<a href="redis://user:***@127.0.0.1:6379">here</a>',
                ('1234?a=1', '1234'),
            ),
            (
                '{"url":"redis://user:1234?a=1@127.0.0.1:6379","next":"ok"}',
                '{"url":"redis://user:***@127.0.0.1:6379","next":"ok"}',
                ('1234?a=1', '1234'),
            ),
            (
                'url=redis://user:1234?a=1@127.0.0.1:6379,reason=timeout',
                'url=redis://user:***@127.0.0.1:6379,reason=timeout',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://user:p@ss/w@host:6379;status=500',
                'redis://user:***@host:6379;status=500',
                ('p@ss', 'ss/w'),
            ),
            (
                '[click](redis://user:1234?a=1@127.0.0.1:6379)text',
                '[click](redis://user:***@127.0.0.1:6379)text',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://[user]:1234?a=1@127.0.0.1:6379',
                'redis://***@127.0.0.1:6379',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://[::1]:1234?a=1@127.0.0.1:6379',
                'redis://***@127.0.0.1:6379',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://user:p@127.0.0.1:6379,pw2@realhost:6379',
                'redis://user:***@realhost:6379',
                ('pw2',),
            ),
            (
                'unix://user:p@/w>secret@/tmp/redis.sock',
                'unix://user:***@/tmp/redis.sock',
                ('secret',),
            ),
            (
                'redis://127.0.0.1:6379/0?user=a@b,c&pass%77ord=s1',
                'redis://127.0.0.1:6379/0?user=a@b,c&pass%77ord=***',
                ('s1',),
            ),
            (
                'redis://127.0.0.1:6379/0?target=a@host:6379,b&password=s1',
                'redis://127.0.0.1:6379/0?target=a@host:6379,b&password=***',
                ('s1',),
            ),
            (
                '{"url":"redis://127.0.0.1:6379/0?user=a@b&password=s1","next":"ok"}',
                '{"url":"redis://127.0.0.1:6379/0?user=a@b&password=***","next":"ok"}',
                ('s1',),
            ),
            (
                'url=redis://user:1234/ab?c@localhost,auth_ok=1&reason=timeout',
                'url=redis://user:***@localhost,auth_ok=1&reason=timeout',
                ('1234/ab?c', '1234'),
            ),
            (
                'url=redis://user:pw@localhost,reason=timeout,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost,reason=timeout,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                'url=redis://user:pw@localhost,reason=timeout,next=redis://u2:sec2@h2,end=1',
                'url=redis://user:***@localhost,reason=timeout,next=redis://u2:***@h2,end=1',
                ('pw', 'sec2'),
            ),
            (
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=s1_secret',
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=***',
                ('s1_secret',),
            ),
            (
                'redis://user:p">secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('secret',),
            ),
            (
                'redis://user:p@fake,k=pw_secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('pw_secret',),
            ),
            (
                'redis://user:pw@localhost?target=a@host:6379,b&password=s1',
                'redis://user:***@localhost?target=a@host:6379,b&password=***',
                ('pw', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?password=a@b,secret&other=1',
                'redis://127.0.0.1:6379/0?password=***&other=1',
                ('secret',),
            ),
            (
                'url=redis://user:pw@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                '["redis://127.0.0.1:6379/0?user=a@b&password=s1",123,"a@b.com/1"]',
                '["redis://127.0.0.1:6379/0?user=a@b&password=***",123,"a@b.com/1"]',
                ('s1',),
            ),
            (
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=SECRET123',
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=***',
                ('SECRET123',),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1!redis://u2:s2@h2:6379/0',
                'redis://user:***@127.0.0.1:6379/0?q=1!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://127.0.0.1:6379?other=1&password=a@b,pw_secret',
                'redis://127.0.0.1:6379?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
                ('pw_secret',),
            ),
            (
                '<a href="redis://user:pw@localhost">a@b.com</a>',
                '<a href="redis://user:***@localhost">a@b.com</a>',
                ('pw',),
            ),
            (
                'redis://secretuser,k=pw@127.0.0.1:6379/0',
                'redis://***@127.0.0.1:6379/0',
                ('secretuser', 'pw'),
            ),
            (
                'redis://123://x?q=1!redis://h2:6379/0?password=s2',
                'redis://123://x?q=1!redis://h2:6379/0?password=***',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379?q=1#frag!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379?q=1#frag!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b,pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b)pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/app?password=sec2@b,pw_secret',
                'redis://u:***@localhost/app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'GET /auth?redirect="https://client.example.com/cb"&token=SECRET123',
                'GET /auth?redirect="https://client.example.com/cb"&token=***',
                ('SECRET123',),
            ),
            (
                'redis://user,k=1:pw@127.0.0.1/0',
                'redis://user,k=1:***@127.0.0.1/0',
                ('pw',),
            ),
            (
                'redis://user,k=1@localhost',
                'redis://***@localhost',
                ('user,k=1',),
            ),
            # Iter 11 regression tests (all 6 WA-010 findings)
            (
                'redis://user,k=1:pw@localhost!redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379/0?foo&!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379/0?redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0=1',
                'redis://127.0.0.1:6379/0?redis://u2:***@h2:6379/0=1',
                ('s2',),
            ),
            (
                r'{"url":"https://app.example.com/auth?redirect=\"https://client.example.com/cb\"&token=SECRET123"}',
                r'{"url":"https://app.example.com/auth?redirect=\"https://client.example.com/cb\"&token=***"}',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=("https://client.example.com/cb?a=1")&token=SECRET123',
                'GET /auth?redirect=("https://client.example.com/cb?a=1")&token=***',
                ('SECRET123',),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/my@master?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            # Iter 12 regression tests (Explorers 1, 2, 3)
            (
                'redis://user:1234!http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234', 'example.com'),
            ),
            (
                'redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234', 's2'),
            ),
            (
                'redis+sentinel://u:sec1@node1,node2:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@node1,node2:26379/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2/mymaster?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2,node3:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2,node3:26379/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+unix://u:sec1@/tmp/@dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/@dir/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+unix://u:sec1@/tmp@/dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/dir/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@localhost/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@localhost/my@master?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1/my@master?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/my@app?password=sec2',
                'redis://u:***@localhost/my@app?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis://127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1',
                'redis://127.0.0.1:6379/0?password=***',
                ('secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?foo&!redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
        ],
    )
    def test_sibling_modules_redact_wrapped_and_punctuated_redis_urls(
        self,
        raw_text: str,
        expected: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        for mod in (rate_limit_module, queue_module, worker_module):
            redacted = mod._redact_redis_text(raw_text)
            assert redacted == expected
            for fragment in secret_fragments:
                assert fragment not in redacted

    @pytest.mark.parametrize(
        ('error_message', 'secret_fragments'),
        [
            (
                'Failed connecting to redis://user:1234?a=1@127.0.0.1:6379: Connection refused',
                ('1234?a=1', '1234'),
            ),
            (
                'Failed on redis://user:1234?a=1@[::1]: timeout',
                ('1234?a=1', '1234'),
            ),
            (
                'Failed on unix://user:p@w/x@/var/run/redis.sock: Permission denied',
                ('p@w', 'w/x'),
            ),
            (
                'See `redis://user:1234?a=1@127.0.0.1:6379` for details',
                ('1234?a=1', '1234'),
            ),
            (
                '["redis://u1:p1@h1:6379","redis://u2:p2@h2"]',
                ('p1', 'p2'),
            ),
            (
                '**redis://user:1234?a=1@127.0.0.1:6379**',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://[user]:pass/x@localhost',
                ('pass/x',),
            ),
            (
                'redis://[::1]:6379&url[0]=redis://u2:secret2@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:secret2@h2:6379',
                ('secret2',),
            ),
            (
                '{"url":"redis://user:1234?a=1@127.0.0.1:6379","next":"ok"}',
                ('1234?a=1', '1234'),
            ),
            (
                '<a href="redis://user:p@ss/w@host:6379">link</a>',
                ('p@ss', 'ss/w'),
            ),
            (
                'redis://[user]:1234?a=1@127.0.0.1:6379',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://user:p@127.0.0.1:6379,pw2@realhost:6379',
                ('pw2',),
            ),
            (
                'unix://user:p@/w>secret@/tmp/redis.sock',
                ('secret',),
            ),
            (
                'redis://127.0.0.1:6379/0?target=a@host:6379,b&password=s1',
                ('s1',),
            ),
            (
                'url=redis://user:pw@localhost,reason=timeout,next=redis://h2:6379/0?password=secret2',
                ('pw', 'secret2'),
            ),
            (
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=s1_secret',
                ('s1_secret',),
            ),
            (
                'redis://user:p">secret@127.0.0.1:6379/0',
                ('secret',),
            ),
            (
                'redis://user:p@fake,k=pw_secret@127.0.0.1:6379/0',
                ('pw_secret',),
            ),
            (
                'redis://user:pw@localhost?target=a@host:6379,b&password=s1',
                ('pw', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?password=a@b,secret&other=1',
                ('secret',),
            ),
            (
                'url=redis://user:pw@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=secret2',
                ('pw', 'secret2'),
            ),
            (
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=SECRET123',
                ('SECRET123',),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1!redis://u2:s2@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://127.0.0.1:6379?other=1&password=a@b,pw_secret',
                ('pw_secret',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret',
                ('pw_secret',),
            ),
            (
                '<a href="redis://user:pw@localhost">a@b.com</a>',
                ('pw',),
            ),
            (
                'redis://secretuser,k=pw@127.0.0.1:6379/0',
                ('secretuser', 'pw'),
            ),
            (
                'redis://123://x?q=1!redis://h2:6379/0?password=s2',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379?q=1#frag!redis://u2:s2@h2:6379/0',
                ('s2',),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b,pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b)pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/app?password=sec2@b,pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'GET /auth?redirect="https://client.example.com/cb"&token=SECRET123',
                ('SECRET123',),
            ),
            (
                'redis://user,k=1:pw@127.0.0.1/0',
                ('pw',),
            ),
            (
                'redis://user,k=1@localhost',
                ('user,k=1',),
            ),
            (
                'redis://user,k=1:pw@localhost!redis://u2:s2@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0=1',
                ('s2',),
            ),
            (
                'GET /auth?redirect=("https://client.example.com/cb?a=1")&token=SECRET123',
                ('SECRET123',),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=sec2',
                ('sec1', 'sec2'),
            ),
            (
                'redis+unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run,pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/my@master?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            # Iter 12 live caplog regression tests (Explorers 1, 2, 3)
            (
                'redis://user:1234!http://example.com@127.0.0.1:6379/0',
                ('1234', 'example.com'),
            ),
            (
                'redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0',
                ('1234', 's2'),
            ),
            (
                'redis+sentinel://u:sec1@node1,node2:26379/mymaster?password=sec2',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2/mymaster?password=sec2',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2,node3:26379/mymaster?password=sec2',
                ('sec1', 'sec2'),
            ),
            (
                'redis+unix://u:sec1@/tmp/@dir/redis.sock?password=sec2@/var/run,pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+unix://u:sec1@/tmp@/dir/redis.sock?password=sec2@/var/run,pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@localhost/my@master?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1/my@master?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/my@app?password=sec2',
                ('sec1', 'sec2'),
            ),
            (
                'redis://127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1',
                ('secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0&password=s1',
                ('s2', 's1'),
            ),
        ],
    )
    def test_live_caplog_redacts_colon_and_wrapped_exceptions_across_rate_limit_queue_and_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        error_message: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        # Credential-free REDIS_URL ensures '***' in caplog.text proves str(exc) was redacted.
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:1/0')
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        # 1) rate_limit.consume_sample_quota
        class _DeadConn(redis.Connection):
            def connect(self) -> None:
                raise redis.ConnectionError(error_message)

        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: redis.ConnectionPool(connection_class=_DeadConn)),
        )
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger='web.backend.rate_limit'):
            rate_limit_module.consume_sample_quota(_make_request(), sample_count=1, sample_limit_per_minute=2)
        assert '***' in caplog.text
        for fragment in secret_fragments:
            assert fragment not in caplog.text

        # 2) worker.run_worker
        class _FailingInitWorker(worker_module.Worker):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                del args, kwargs
                raise redis.ConnectionError(error_message)

        caplog.clear()
        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises(redis.ConnectionError):
                worker_module.run_worker(
                    connection=fakeredis.FakeStrictRedis(),
                    burst=True,
                    worker_class=_FailingInitWorker,
                )
        assert '***' in caplog.text
        for fragment in secret_fragments:
            assert fragment not in caplog.text

        # 3) queue.get_queue
        queue_module.reset_redis_pool()
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: (_ for _ in ()).throw(redis.ConnectionError(error_message))),
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger='web.backend.queue'):
            with pytest.raises(redis.ConnectionError):
                queue_module.get_queue()
        assert '***' in caplog.text
        for fragment in secret_fragments:
            assert fragment not in caplog.text

    def test_create_rate_limiter_translates_unix_scheme_without_leaking_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'unix://user:topsecret@/tmp/test.sock')
        limiter = rate_limit_module.create_rate_limiter()
        assert limiter is not None
        assert limiter._storage_uri == 'redis+unix://user:topsecret@/tmp/test.sock'
        assert 'topsecret' not in repr(limiter._storage)

    def test_create_rate_limiter_redacts_credentials_on_configuration_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', 'badscheme://user:topsecret@127.0.0.1:6379/0')
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        assert 'topsecret' not in str(exc_info.value)
        assert '***' in str(exc_info.value)
        assert exc_info.value.__cause__ is None

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('redis+sentinel://user:p@ssword@127.0.0.1/mymaster', ('p@ssword', 'ssword')),
            ('redis+cluster://user:p@ssword@127.0.0.1', ('p@ssword', 'ssword')),
            ('redis+sentinel://user:a@b@127.0.0.1/mymaster', ('a@b', 'b@127')),
        ],
    )
    def test_create_rate_limiter_redacts_schemeless_limits_location_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        assert '***' in msg
        for fragment in secret_fragments:
            assert fragment not in msg
        assert exc_info.value.__cause__ is None

    def test_create_rate_limiter_preserves_non_secret_port_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://user:pw@127.0.0.1:notaport/0')
        with pytest.raises(ValueError) as exc_info:
            rate_limit_module.create_rate_limiter()
        assert str(exc_info.value) == "Port could not be cast to integer value as 'notaport'"
        assert exc_info.value.__cause__ is None

    @pytest.mark.parametrize(
        'redis_url',
        [
            'badscheme://127.0.0.1:6379/0?user=a@host:8080/p&token=s1_secret',
            'badscheme://[::1]:6379/0?user=a@host:8080/p&token=s1_secret',
            'badscheme://example.com:8443/cb?user=a@host:8080/p,b&token=s1_secret',
        ],
    )
    def test_create_rate_limiter_redacts_query_token_when_query_contains_at_host_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        assert 's1_secret' not in msg
        assert 'token=***' in msg
        assert exc_info.value.__cause__ is None
        assert rate_limit_module._extract_uri_secret_fragments(redis_url) == ('s1_secret',)

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('badscheme://127.0.0.1:6379?other=1&password=a@b,pw_secret', ('pw_secret',)),
            ('badscheme://127.0.0.1:6379?other=1&password=a@b;pw_secret', ('pw_secret',)),
            ('badscheme://127.0.0.1:6379?other=1&password=a@b)pw_secret', ('pw_secret',)),
            ('badscheme://127.0.0.1:26379/mymaster?password=a@b,pw_secret', ('pw_secret',)),
            ('badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b;pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b)pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b]pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b:8080/0;pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('badscheme://u:sec1@localhost/app?password=sec2@b;pw_secret', ('sec1', 'sec2', 'pw_secret')),
            (
                'badscheme://u:sec1@127.0.0.1:6379,127.0.0.2:6379/mycluster?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            ('badscheme://u:sec1@127.0.0.1:6379/my@app?password=sec2@b:8080/0;pw_secret', ('sec1', 'sec2', 'pw_secret')),
        ],
    )
    def test_rate_limiter_secret_fragment_sub_splitting_and_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        from limits.errors import ConfigurationError

        fragments = rate_limit_module._extract_uri_secret_fragments(redis_url)
        assert 'pw_secret' in fragments
        sanitized_schemeless = rate_limit_module._sanitize_rate_limiter_error_message(
            'err ***@b,pw_secret',
            redis_url,
            redis_url,
        )
        assert sanitized_schemeless.startswith('Invalid rate limiter storage configuration')
        for secret_fragment in secret_fragments:
            assert secret_fragment not in sanitized_schemeless

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        for secret_fragment in secret_fragments:
            assert secret_fragment not in msg
        assert '***' in msg
        assert exc_info.value.__cause__ is None

        sentinel_url = 'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret'
        assert 'pw_secret' in rate_limit_module._extract_uri_secret_fragments(sentinel_url)
        assert rate_limit_module._sanitize_rate_limiter_error_message(
            'err ***@b,pw_secret',
            sentinel_url,
            sentinel_url,
        ).startswith('Invalid rate limiter storage configuration')

    def test_rate_limiter_defense_in_depth_on_mocked_authority_missplit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bad_url = 'badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b;pw_secret'
        monkeypatch.setattr(
            rate_limit_module,
            '_split_authority_at',
            lambda _rest, **_kw: ('u:sec1@127.0.0.1:6379/app?password=sec2', 'b;pw_secret'),
        )
        fragments = rate_limit_module._extract_uri_secret_fragments(bad_url)
        assert 'pw_secret' in fragments

        monkeypatch.setattr(rate_limit_module, 'redact_redis_url', lambda _u: 'badscheme://u:***@b;pw_secret')
        sanitized = rate_limit_module._sanitize_rate_limiter_error_message(
            'err b;pw_secret',
            bad_url,
            bad_url,
        )
        assert sanitized == 'Invalid rate limiter storage configuration for badscheme://***'
        for frag in ('sec1', 'sec2', 'pw_secret'):
            assert frag not in sanitized

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('badscheme://u:sec1@127.0.0.1:6379,127.0.0.2:6379/mycluster?password=s2', ('sec1', 's2')),
            ('badscheme://u:sec1@127.0.0.1:6379,127.0.0.2:6379/mycluster?password=s23', ('sec1', 's23')),
            ('badscheme://u:sec1@127.0.0.1:6379/app?password=s2', ('sec1', 's2')),
        ],
    )
    def test_create_rate_limiter_redacts_short_query_secrets_without_false_positives(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        assert 'password=***' in msg
        for frag in secret_fragments:
            assert frag not in msg
        assert exc_info.value.__cause__ is None

        non_secret_url = 'badscheme://user:p?password=1@127.0.0.1:6379/0'
        monkeypatch.setenv('REDIS_URL', non_secret_url)
        with pytest.raises(ConfigurationError) as exc_non_secret:
            rate_limit_module.create_rate_limiter()
        assert str(exc_non_secret.value) == 'unknown storage scheme : badscheme://user:***@127.0.0.1:6379/0'

    @pytest.mark.parametrize(
        ('redis_url', 'secret_prefix'),
        [
            ('redis://user:mysecret?a=1@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:mysecret#frag@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:mysecret/path@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:pw?a=1@127.0.0.1:6379/0', 'pw'),
            ('redis://user:5678!redis://u2:sec2@h2:6379/0@127.0.0.1:6379/0', '5678'),
            ('redis://user:5678|http://example.com@127.0.0.1:6379/0', '5678'),
            ('redis://user:p1@h:secret_tail?a=1@127.0.0.1:6379/0', 'secret_tail'),
            ('redis://user:p1@h:secret_tail#frag@127.0.0.1:6379/0', 'secret_tail'),
            ('redis://user:p1@h:secret_tail/path@127.0.0.1:6379/0', 'secret_tail'),
            ('redis://user:p1@h:s3c?a=1@127.0.0.1:6379/0', 's3c'),
            ('redis://user:p1@[::1]:s3c?a=1@127.0.0.1:6379/0', 's3c'),
            ('redis://[::1]:s3c?a=1@127.0.0.1:6379/0', 's3c'),
            ('redis://user:p1@h:p2:s3c?a=1@127.0.0.1:6379/0', 's3c'),
            ("redis://user:p1@h:s'c?a=1@127.0.0.1:6379/0", "s'c"),
            ('redis://user:p1@h:sec\tret?a=1@127.0.0.1:6379/0', 'secret'),
            ('redis://user:s\u2100?a=1@127.0.0.1:6379/0', 's\u2100'),
        ],
    )
    def test_urlparse_port_value_error_redacts_secret_across_rate_limit_queue_and_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        redis_url: str,
        secret_prefix: str,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        with pytest.raises(ValueError) as rl_exc:
            rate_limit_module.create_rate_limiter()
        assert secret_prefix not in str(rl_exc.value)
        assert '***' in str(rl_exc.value)
        assert rl_exc.value.__cause__ is None

        queue_module.reset_redis_pool()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger='web.backend.queue'):
            with pytest.raises(ValueError) as q_exc:
                queue_module.get_queue()
        assert secret_prefix not in str(q_exc.value)
        assert '***' in str(q_exc.value)
        assert secret_prefix not in caplog.text
        assert '***' in caplog.text

        worker_module.reset_redis_pool()
        with pytest.raises(ValueError) as wr_exc:
            worker_module._resolve_redis_connection()
        assert secret_prefix not in str(wr_exc.value)
        assert '***' in str(wr_exc.value)
        assert wr_exc.value.__cause__ is None

        worker_module.reset_redis_pool()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises(ValueError) as w_exc:
                worker_module.run_worker(burst=True)
        assert secret_prefix not in str(w_exc.value)
        assert '***' in str(w_exc.value)
        assert secret_prefix not in caplog.text
        assert '***' in caplog.text

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('redis+sentinel://user:p1@s3c,x@127.0.0.1:26379/mymaster', ('p1', 's3c')),
            ('redis+cluster://user:p1@s3c,x@127.0.0.1:6379', ('p1', 's3c')),
            ('redis+sentinel://user:p1@s3c?a=1@127.0.0.1:26379/mymaster', ('p1', 's3c')),
        ],
    )
    def test_create_rate_limiter_redacts_short_sentinel_cluster_location_leaks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        assert '***' in msg
        for frag in secret_fragments:
            assert frag not in msg
        assert exc_info.value.__cause__ is None

    @pytest.mark.parametrize(
        ('redis_url', 'expected_msg'),
        [
            ('redis://127.0.0.1:badport/0', "Port could not be cast to integer value as 'badport'"),
            ('redis://user:pw@127.0.0.1:notaport/0', "Port could not be cast to integer value as 'notaport'"),
            ('redis://user:p1@h:b@127.0.0.1:badport/0', "Port could not be cast to integer value as 'badport'"),
        ],
    )
    def test_create_rate_limiter_and_worker_preserve_benign_port_value_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        expected_msg: str,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ValueError) as rl_exc:
            rate_limit_module.create_rate_limiter()
        assert str(rl_exc.value) == expected_msg

        worker_module.reset_redis_pool()
        with pytest.raises(ValueError) as wr_exc:
            worker_module._resolve_redis_connection()
        assert str(wr_exc.value) == expected_msg

    def test_rate_limiter_sanitizer_scales_linearly_on_large_inputs(self) -> None:
        big_url = (
            'redis+sentinel://user:p1@h:'
            + ('a:' * 20_000)
            + 'secret_tail?a=1@127.0.0.1:26379/mymaster'
        )
        big_msg = 'Error connecting ' + ('x ' * 40_000) + 'secret_tail'
        t0 = time.perf_counter()
        sanitized = rate_limit_module._sanitize_rate_limiter_error_message(big_msg, big_url, big_url)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5
        assert 'secret_tail' not in sanitized

    def test_worker_run_worker_reraises_sanitized_connection_error_and_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://user:1234?password=sec1#frag@127.0.0.1:1/0')
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')
        worker_module.reset_redis_pool()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises(redis.ConnectionError) as exc_info:
                worker_module.run_worker(burst=True)
        msg = str(exc_info.value)
        assert '1234' not in msg
        assert 'sec1' not in msg
        assert '***' in msg
        assert exc_info.value.__cause__ is None

        class _MultiArgRedisError(redis.RedisError):
            def __init__(self, code: int, detail: str) -> None:
                super().__init__(f'{code}: {detail}')
                self.code = code
                self.detail = detail

        class _MultiArgFailWorker(worker_module.Worker):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                del args, kwargs
                raise _MultiArgRedisError(42, 'redis://u:topsecret@127.0.0.1:6379/0')

        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        worker_module.reset_redis_pool()
        with pytest.raises(RuntimeError) as fallback_exc:
            worker_module.run_worker(
                connection=fakeredis.FakeStrictRedis(),
                burst=True,
                worker_class=_MultiArgFailWorker,
            )
        assert 'topsecret' not in str(fallback_exc.value)
        assert '***' in str(fallback_exc.value)
        assert fallback_exc.value.__cause__ is None

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('redis+sentinel://user:p1@a b,x@127.0.0.1:26379/mymaster', ('p1', 'a b')),
            ('redis+sentinel://user:p1@ s3,x@127.0.0.1:26379/mymaster', ('p1', 's3')),
            ('redis+sentinel://user:p1@s3 ,x@127.0.0.1:26379/mymaster', ('p1', 's3')),
            ('redis+sentinel://user:p1@a\tb,x@127.0.0.1:26379/mymaster', ('p1', 'a\tb')),
            ('redis+sentinel://user:p1@a\nb,x@127.0.0.1:26379/mymaster', ('p1', 'a\nb')),
            ('redis+sentinel://user:p1@a@s3c,x@127.0.0.1:26379/mymaster', ('p1', 's3c')),
            ('redis+cluster://user:p1@a b,x@127.0.0.1:6379', ('p1', 'a b')),
            ('redis+cluster://user:p1@ s3,x@127.0.0.1:6379', ('p1', 's3')),
            ('redis+cluster://user:p1@s3 ,x@127.0.0.1:6379', ('p1', 's3')),
            ('redis+cluster://user:p1@a\tb,x@127.0.0.1:6379', ('p1', 'a\tb')),
            ('redis+cluster://user:p1@a\nb,x@127.0.0.1:6379', ('p1', 'a\nb')),
            ('redis+cluster://user:p1@a@s3c,x@127.0.0.1:6379', ('p1', 's3c')),
        ],
    )
    def test_create_rate_limiter_redacts_limits_whitespace_and_control_location_leaks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', redis_url)
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        msg = str(exc_info.value)
        assert '***' in msg
        for frag in secret_fragments:
            assert frag not in msg
        assert exc_info.value.__cause__ is None

    @pytest.mark.parametrize(
        'redis_url',
        [
            'redis://user:p1@s3c\t/0@127.0.0.1:6379/0',
            'redis://user:p1@s3\tc/0@127.0.0.1:6379/0',
            'redis://user:p1@s3\rc/0@127.0.0.1:6379/0',
            'redis://user:p1@s3\nc/0@127.0.0.1:6379/0',
        ],
    )
    def test_tab_and_crlf_short_host_connection_error_redacted_across_entrypoints(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        redis_url: str,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        class _TabHostConn(redis.Connection):
            def connect(self) -> None:
                raise redis.ConnectionError('Error -2 connecting to s3c:6379. Name or service not known.')

        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: redis.ConnectionPool(connection_class=_TabHostConn)),
        )
        queue_module.reset_redis_pool()
        worker_module.reset_redis_pool()

        caplog.clear()
        with caplog.at_level(logging.ERROR, logger='web.backend.worker'):
            with pytest.raises(redis.ConnectionError) as exc_info:
                worker_module.run_worker(burst=True)
        assert 's3c' not in str(exc_info.value)
        assert 's3c' not in caplog.text
        assert '***' in str(exc_info.value)

        sanitized = sanitize_redis_client_error('Error -2 connecting to s3c:6379.', redis_url)
        assert 's3c' not in sanitized
        assert sanitized.startswith('Invalid Redis configuration for ')

    @pytest.mark.parametrize(
        'redis_url',
        [
            'redis://user:p1@h:sec\x0bret?a=1@127.0.0.1:6379/0',
            'redis://user:p1@h:sec\x0cret?a=1@127.0.0.1:6379/0',
        ],
    )
    def test_hex_escape_port_cast_value_error_redacts_secret_across_entrypoints(
        self,
        monkeypatch: pytest.MonkeyPatch,
        redis_url: str,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        with pytest.raises(ValueError) as rl_exc:
            rate_limit_module.create_rate_limiter()
        assert 'sec' not in str(rl_exc.value)
        assert '***' in str(rl_exc.value)

        queue_module.reset_redis_pool()
        with pytest.raises(ValueError) as q_exc:
            queue_module.get_queue()
        assert 'sec' not in str(q_exc.value)
        assert '***' in str(q_exc.value)

        worker_module.reset_redis_pool()
        with pytest.raises(ValueError) as w_exc:
            worker_module.run_worker(burst=True)
        assert 'sec' not in str(w_exc.value)
        assert '***' in str(w_exc.value)

    def test_r15_sanitizers_linear_scaling_and_recursion_limit(self) -> None:
        n = 40_000
        rl_many_query_url = 'badscheme://127.0.0.1:6379/0?' + '&'.join(
            f'password=q{i:06d}' for i in range(n // 16)
        )
        rl_many_userinfo_url = (
            'badscheme://user:' + ':'.join(f'u{i:06d}' for i in range(n // 10)) + '@127.0.0.1:6379/0'
        )
        many_at_url = 'redis://u:' + '@'.join(f'h{i}' for i in range(n // 8)) + '@127.0.0.1:6379/0'
        aaaa_url = 'redis://u:' + ':'.join(f'aaaa{i:06d}bbbb' for i in range(n // 15)) + '@127.0.0.1:6379/0'
        ports_url = 'redis://u:' + ':'.join(f'q{i}' for i in range(n // 50)) + '@127.0.0.1:6379/0'
        msg = 'Error connecting ' + ('x ' * (n // 2))
        ports_msg = ' '.join(f"Port could not be cast to integer value as 'p{i}'" for i in range(n // 50))

        prev_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(150)
        try:
            t0 = time.perf_counter()
            assert rate_limit_module._sanitize_rate_limiter_error_message(msg, rl_many_query_url, rl_many_query_url) == msg
            assert (
                rate_limit_module._sanitize_rate_limiter_error_message(msg, rl_many_userinfo_url, rl_many_userinfo_url)
                == msg
            )
            assert rate_limit_module._sanitize_rate_limiter_error_message(msg, many_at_url, many_at_url) == msg
            assert rate_limit_module._sanitize_rate_limiter_error_message(msg, aaaa_url, aaaa_url) == msg
            assert sanitize_redis_client_error(ports_msg, ports_url) == ports_msg
            assert time.perf_counter() - t0 < 1.5
        finally:
            sys.setrecursionlimit(prev_limit)
