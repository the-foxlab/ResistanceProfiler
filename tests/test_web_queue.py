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
from web.backend.config import WEB_BACKEND_CONFIG
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

    def test_live_caplog_redacts_redis_credentials_across_rate_limit_queue_and_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        error_message = "ConnectionError('redis://user:topsecret@127.0.0.1:6379/0')"
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:1/0')
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

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
        assert '***' in caplog.text and 'topsecret' not in caplog.text

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
        assert '***' in caplog.text and 'topsecret' not in caplog.text

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
        assert '***' in caplog.text and 'topsecret' not in caplog.text

    def test_create_rate_limiter_unix_scheme_and_error_redaction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from limits.errors import ConfigurationError

        monkeypatch.setenv('REDIS_URL', 'unix://user:topsecret@/tmp/test.sock')
        limiter = rate_limit_module.create_rate_limiter()
        assert limiter is not None
        assert limiter._storage_uri == 'redis+unix://user:topsecret@/tmp/test.sock'
        assert 'topsecret' not in repr(limiter._storage)

        monkeypatch.setenv('REDIS_URL', 'badscheme://user:topsecret@127.0.0.1:6379/0')
        with pytest.raises(ConfigurationError) as exc_info:
            rate_limit_module.create_rate_limiter()
        assert 'topsecret' not in str(exc_info.value)
        assert '***' in str(exc_info.value)
        assert exc_info.value.__cause__ is None

        monkeypatch.setenv('REDIS_URL', 'redis+sentinel://user:p@ssword@127.0.0.1/mymaster')
        with pytest.raises(ConfigurationError) as sentinel_exc:
            rate_limit_module.create_rate_limiter()
        assert 'p@ssword' not in str(sentinel_exc.value)
        assert '***' in str(sentinel_exc.value)

        monkeypatch.setenv('REDIS_URL', 'redis://user:pw@127.0.0.1:notaport/0')
        with pytest.raises(ValueError) as port_exc:
            rate_limit_module.create_rate_limiter()
        assert str(port_exc.value) == "Port could not be cast to integer value as 'notaport'"

    def test_worker_run_worker_reraises_sanitized_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _MultiArgRedisError(redis.RedisError):
            def __init__(self, code: int, detail: str) -> None:
                super().__init__(f'{code}: {detail}')

        class _FailWorker(worker_module.Worker):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                del args, kwargs
                raise _MultiArgRedisError(42, 'redis://u:topsecret@127.0.0.1:6379/0')

        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        worker_module.reset_redis_pool()
        with pytest.raises(RuntimeError) as exc_info:
            worker_module.run_worker(
                connection=fakeredis.FakeStrictRedis(),
                burst=True,
                worker_class=_FailWorker,
            )
        assert 'topsecret' not in str(exc_info.value)
        assert '***' in str(exc_info.value)
        assert exc_info.value.__cause__ is None
