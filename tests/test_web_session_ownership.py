"""Tests for the session-ownership foundation."""

from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import fakeredis
import pytest
import redis
from fastapi.testclient import TestClient
from rq import Queue

from tests.conftest import TINY_REF_NAME, TINY_REF_SEQ
from web.backend.config import redact_redis_text, redact_redis_url, sanitize_redis_client_error
from web.backend.main import create_app
from web.backend.rate_limit import _sanitize_rate_limiter_error_message
from web.backend.services import session as session_service
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    SessionBackendError,
    claim_owned_record_field,
    create_session,
    delete_owned_record,
    fetch_owned_record,
    hash_session_token,
    owner_matches,
    record_artifact,
    record_job,
    record_upload,
    resolve_or_create_session,
    resolve_owned_path,
    update_owned_record_fields,
)
from web.backend.startup_config import StartupConfig, build_project_db_uuid_index


@pytest.fixture(autouse=True)
def _reset_session_stores_each_test() -> None:
    session_service.reset_memory_stores()
    yield
    session_service.reset_memory_stores()


@pytest.fixture()
def startup_config(project_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StartupConfig:
    """Local copy of the web_api startup_config fixture (not shared via conftest)."""
    monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'http://localhost:5173')
    data_dir = tmp_path / 'data'
    project_databases_dir = data_dir / 'project_databases'
    uploads_dir = data_dir / 'uploads'
    results_dir = data_dir / 'results'
    project_databases_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    bundled_project_db = project_databases_dir / project_db.name
    shutil.copy2(project_db, bundled_project_db)

    return StartupConfig(
        project_databases_dir=project_databases_dir.resolve(),
        uploads_dir=uploads_dir.resolve(),
        results_dir=results_dir.resolve(),
        data_dir=data_dir.resolve(),
        allowed_roots=(project_databases_dir.resolve(), uploads_dir.resolve(), results_dir.resolve()),
        project_db_uuid_index=build_project_db_uuid_index(project_databases_dir.resolve()),
    )


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeStrictRedis:
    """Patch the session service to use an in-memory fakeredis server."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server)

    def _from_url(_url, **_kwargs):
        return fakeredis.FakeStrictRedis(server=server)

    # Patch the module-level redis client factory used by the session service.
    monkeypatch.setattr(session_service, '_redis_connection', lambda: client)
    return client


@pytest.fixture()
def no_token_config(startup_config: StartupConfig) -> StartupConfig:
    """A local-mode config (zero-config startup)."""

    return replace(startup_config, deployment_mode='local')


class TestSessionIssuance:
    def test_fresh_request_receives_httponly_samesite_cookie_in_local_mode(
        self,
        fake_redis,
        no_token_config,
    ) -> None:
        client = TestClient(create_app(startup_config=no_token_config))
        response = client.post('/api/session/cleanup', json={'upload_ids': [], 'artifact_ids': []})
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie
        assert 'HttpOnly' in set_cookie
        assert 'SameSite=Lax' in set_cookie
        # In local mode (HTTP on loopback) Secure is omitted so the cookie is
        # actually sent by clients over plain HTTP.
        assert 'Secure' not in set_cookie

    def test_fresh_request_receives_secure_cookie_in_online_mode(
        self,
        fake_redis,
        startup_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.com')
        from web.backend.startup_config import _validate_startup_policy
        _validate_startup_policy(deployment_mode='online')
        online_config = replace(startup_config, deployment_mode='online')
        client = TestClient(create_app(startup_config=online_config))
        response = client.get('/api/databases')
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie
        assert 'HttpOnly' in set_cookie
        assert 'SameSite=Lax' in set_cookie
        assert 'Secure' in set_cookie

    def test_raw_session_value_is_not_stored_in_redis(self, fake_redis) -> None:
        session = create_session()
        # The raw token must not appear anywhere in Redis.
        keys = list(fake_redis.scan_iter('*'))
        for key in keys:
            key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
            assert session.token not in key_str
            # Session records are stored as hashes; read all field values.
            raw = fake_redis.hgetall(key)
            for value in raw.values():
                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                assert session.token not in value_str
        # The stored hash key must equal the hash of the token.
        expected_key = f'respro:session:{hash_session_token(session.token)}'
        assert fake_redis.exists(expected_key) == 1

    def test_token_has_at_least_256_bits_of_entropy(self, fake_redis) -> None:
        # token_urlsafe(32) -> 32 bytes -> 256 bits. The decoded base64url string
        # encodes those 32 bytes; decoding it back must yield >= 32 bytes.
        import base64

        session = create_session()
        decoded = base64.urlsafe_b64decode(session.token + '==' * (-len(session.token) % 4))
        assert len(decoded) >= 32  # 256 bits

    def test_tampered_cookie_issues_new_session(self, fake_redis, no_token_config) -> None:
        session = create_session()
        client = TestClient(create_app(startup_config=no_token_config))
        # Present a tampered cookie value.
        tampered = session.token + 'tampered'
        response = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            cookies={SESSION_COOKIE_NAME: tampered},
        )
        set_cookie = response.headers.get('set-cookie', '')
        # A new session is issued (the cookie value differs from the tampered one).
        assert SESSION_COOKIE_NAME in set_cookie
        assert tampered not in set_cookie

    def test_unknown_cookie_issues_new_session(self, fake_redis, no_token_config) -> None:
        client = TestClient(create_app(startup_config=no_token_config))
        response = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            cookies={SESSION_COOKIE_NAME: 'totally-unknown-value'},
        )
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie
        assert 'totally-unknown-value' not in set_cookie

    def test_valid_cookie_is_preserved_not_rotated(self, fake_redis, no_token_config) -> None:
        session = create_session()
        client = TestClient(create_app(startup_config=no_token_config))
        response = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            cookies={SESSION_COOKIE_NAME: session.token},
        )
        set_cookie = response.headers.get('set-cookie', '')
        # The same token is echoed back (not rotated).
        assert session.token in set_cookie


class TestOwnedRecords:
    def test_record_and_fetch_upload(self, fake_redis) -> None:
        session = create_session()
        from pathlib import Path

        upload_id = record_upload(
            session_hash=session.session_hash,
            canonical_path=Path('/data/uploads/x.vcf'),
            file_type='vcf',
        )
        record = fetch_owned_record('upload', upload_id)
        assert record is not None
        assert record.owner == session.session_hash
        assert record.canonical_path == '/data/uploads/x.vcf'
        assert record.fields['file_type'] == 'vcf'

    def test_owner_matches_rejects_nonexistent_record(self, fake_redis) -> None:
        session = create_session()
        assert owner_matches(None, session.session_hash) is False

    def test_owner_matches_rejects_wrong_owner(self, fake_redis) -> None:
        from pathlib import Path

        session_a = create_session()
        session_b = create_session()
        upload_id = record_upload(
            session_hash=session_a.session_hash,
            canonical_path=Path('/data/uploads/x.vcf'),
            file_type='vcf',
        )
        record = fetch_owned_record('upload', upload_id)
        assert owner_matches(record, session_a.session_hash) is True
        assert owner_matches(record, session_b.session_hash) is False

    def test_fetch_unknown_record_returns_none(self, fake_redis) -> None:
        assert fetch_owned_record('upload', 'does-not-exist') is None


class TestSessionIsolation:
    """Automated session-isolation security tests.

    Proves that session A cannot inspect, cancel, download, or delete session
    B's jobs, artifacts, or uploads. All cross-session access attempts must
    return 404 (not 403, to avoid confirming existence to non-owners).
    """

    @pytest.fixture()
    def two_clients(
        self,
        fake_redis,
        startup_config: StartupConfig,
    ) -> tuple[TestClient, TestClient]:
        """Return two TestClient instances with independent session cookies."""
        from web.backend.main import create_app
        from web.backend.queue import get_batch_queue, get_queue

        app = create_app(startup_config=startup_config)
        connection = fakeredis.FakeRedis()
        from rq.serializers import JSONSerializer
        sync_queue = Queue('profiling', connection=connection, is_async=False, serializer=JSONSerializer)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client_a = TestClient(app)
        client_b = TestClient(app)
        return client_a, client_b

    def _upload_and_profile_fasta(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> tuple[str, str]:
        """Upload a FASTA, profile it, return (job_id, report_artifact_id)."""
        fasta_path = startup_config.uploads_dir / 'isolation-test.fasta'
        fasta_path.write_text(f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n')
        upload_resp = client.post(
            '/api/upload/fasta',
            files={'file': ('isolation-test.fasta', fasta_path.read_bytes(), 'text/plain')},
        )
        assert upload_resp.status_code == 200, upload_resp.text
        fasta_id = upload_resp.json()['upload_id']

        submit = client.post(
            '/api/profile/fasta',
            json={'fasta_id': fasta_id, 'sample': 'iso-test'},
        )
        assert submit.status_code == 200, submit.text
        job_id = submit.json()['job_id']

        # Poll to succeeded.
        artifact_id = None
        for _ in range(20):
            status = client.get(f'/api/jobs/{job_id}')
            assert status.status_code == 200, status.text
            payload = status.json()
            if payload['status'] in ('succeeded', 'failed'):
                if payload['status'] == 'succeeded':
                    artifact_id = payload['result']['report_html_path']
                break
        assert artifact_id is not None, 'Job did not succeed'
        return job_id, artifact_id

    def test_session_a_cannot_inspect_session_b_job_status(
        self,
        two_clients: tuple[TestClient, TestClient],
        startup_config: StartupConfig,
    ) -> None:
        client_a, client_b = two_clients
        job_id, _ = self._upload_and_profile_fasta(client_a, startup_config)

        # Session B requests session A's job → 404.
        response = client_b.get(f'/api/jobs/{job_id}')
        assert response.status_code == 404

    def test_session_a_cannot_cancel_session_b_job(
        self,
        two_clients: tuple[TestClient, TestClient],
        startup_config: StartupConfig,
    ) -> None:
        client_a, client_b = two_clients
        job_id, _ = self._upload_and_profile_fasta(client_a, startup_config)

        # Session B tries to cancel session A's job → 404.
        response = client_b.delete(f'/api/jobs/{job_id}')
        assert response.status_code == 404

    def test_session_a_cannot_download_session_b_artifact(
        self,
        two_clients: tuple[TestClient, TestClient],
        startup_config: StartupConfig,
    ) -> None:
        client_a, client_b = two_clients
        _, artifact_id = self._upload_and_profile_fasta(client_a, startup_config)

        # Session B tries to download session A's report → 404.
        report_resp = client_b.get(
            '/api/report',
            params={'artifact_id': artifact_id},
        )
        assert report_resp.status_code == 404

        # Session B tries to download session A's artifact → 404.
        artifact_resp = client_b.get(
            '/api/artifact',
            params={'artifact_id': artifact_id},
        )
        assert artifact_resp.status_code == 404

    def test_session_a_cannot_delete_session_b_artifacts_via_cleanup(
        self,
        two_clients: tuple[TestClient, TestClient],
        startup_config: StartupConfig,
    ) -> None:
        client_a, client_b = two_clients
        job_id, artifact_id = self._upload_and_profile_fasta(client_a, startup_config)

        # Session B tries to delete session A's artifact via cleanup.
        # The IDs are silently skipped (not owned by B), deleted_count == 0.
        cleanup_resp = client_b.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': [artifact_id]},
        )
        assert cleanup_resp.status_code == 200
        assert cleanup_resp.json()['deleted_count'] == 0

        # Session A can still download the artifact (it was not deleted).
        report_resp = client_a.get(
            '/api/report',
            params={'artifact_id': artifact_id},
        )
        assert report_resp.status_code == 200

    def test_session_a_cannot_bundle_session_b_artifacts(
        self,
        two_clients: tuple[TestClient, TestClient],
        startup_config: StartupConfig,
    ) -> None:
        client_a, client_b = two_clients
        _, artifact_id = self._upload_and_profile_fasta(client_a, startup_config)

        # Session B tries to bundle session A's artifact → 404.
        bundle_resp = client_b.post(
            '/api/artifact-bundle',
            json={'artifact_ids': [artifact_id]},
        )
        assert bundle_resp.status_code == 404

    def test_request_without_session_cookie_gets_fresh_empty_session(
        self,
        fake_redis,
        no_token_config: StartupConfig,
    ) -> None:
        """A request with no session cookie is issued a fresh, empty-owned session."""
        from web.backend.services.session import fetch_owned_record

        client = TestClient(create_app(startup_config=no_token_config))
        response = client.post('/api/session/cleanup', json={'upload_ids': [], 'artifact_ids': []})
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie

        # The new session owns no records.
        cookie_value = client.cookies.get(SESSION_COOKIE_NAME)
        assert cookie_value is not None
        # No owned records exist for this session.
        assert fetch_owned_record('upload', 'any-id') is None


class TestOwnedPathConfinementAndType:
    def test_expected_file_type_enforcement_and_no_cross_session_type_oracle(
        self,
        fake_redis: fakeredis.FakeStrictRedis,
        tmp_path: Path,
    ) -> None:
        del fake_redis
        uploads_dir = tmp_path / 'uploads'
        uploads_dir.mkdir()
        vcf_file = uploads_dir / 'sample.vcf'
        vcf_file.write_text('##fileformat=VCFv4.2\n', encoding='utf-8')

        owner_a = create_session()
        owner_b = create_session()
        up_id = record_upload(session_hash=owner_a.session_hash, canonical_path=vcf_file, file_type='vcf')

        # Matching expected_file_type succeeds; expected_file_type=None remains backward-compatible.
        assert resolve_owned_path(
            prefix='upload',
            record_id=up_id,
            session_hash=owner_a.session_hash,
            allowed_roots=(uploads_dir,),
            expected_file_type='vcf',
        ) == vcf_file.resolve()
        assert resolve_owned_path(
            prefix='upload',
            record_id=up_id,
            session_hash=owner_a.session_hash,
            allowed_roots=(uploads_dir,),
        ) == vcf_file.resolve()

        # Mismatched expected_file_type for the owner raises ValueError without leaking paths.
        with pytest.raises(ValueError, match='Expected fasta upload, got vcf') as mismatch_exc:
            resolve_owned_path(
                prefix='upload',
                record_id=up_id,
                session_hash=owner_a.session_hash,
                allowed_roots=(uploads_dir,),
                expected_file_type='fasta',
            )
        assert str(uploads_dir) not in str(mismatch_exc.value)

        # Foreign session with mismatched type MUST raise LookupError (404), NOT ValueError (type oracle).
        with pytest.raises(LookupError):
            resolve_owned_path(
                prefix='upload',
                record_id=up_id,
                session_hash=owner_b.session_hash,
                allowed_roots=(uploads_dir,),
                expected_file_type='fasta',
            )

    def test_strict_allowed_roots_confinement_rejects_outside_symlink_root_and_empty(
        self,
        fake_redis: fakeredis.FakeStrictRedis,
        tmp_path: Path,
    ) -> None:
        del fake_redis
        uploads_dir = tmp_path / 'uploads'
        results_dir = tmp_path / 'results'
        outside_dir = tmp_path / 'outside'
        uploads_dir.mkdir()
        results_dir.mkdir()
        outside_dir.mkdir()

        secret_file = outside_dir / 'secret.txt'
        secret_file.write_text('secret', encoding='utf-8')
        escape_link = uploads_dir / 'escape.fasta'
        escape_link.symlink_to(secret_file)
        result_file = results_dir / 'out.html'
        result_file.write_text('<html></html>', encoding='utf-8')

        session_obj = create_session()
        id_symlink = record_upload(session_hash=session_obj.session_hash, canonical_path=escape_link, file_type='fasta')
        id_outside = record_upload(session_hash=session_obj.session_hash, canonical_path=result_file, file_type='fasta')
        id_root = record_upload(session_hash=session_obj.session_hash, canonical_path=uploads_dir, file_type='fasta')
        valid_file = uploads_dir / 'ok.fasta'
        valid_file.write_text('>s\nATGC\n', encoding='utf-8')
        id_valid = record_upload(session_hash=session_obj.session_hash, canonical_path=valid_file, file_type='fasta')

        for bad_id in (id_symlink, id_outside, id_root):
            with pytest.raises(ValueError, match='outside allowed directory') as exc:
                resolve_owned_path(
                    prefix='upload',
                    record_id=bad_id,
                    session_hash=session_obj.session_hash,
                    allowed_roots=(uploads_dir,),
                )
            assert str(tmp_path) not in str(exc.value)

        with pytest.raises(ValueError, match='outside allowed directory'):
            resolve_owned_path(
                prefix='upload',
                record_id=id_valid,
                session_hash=session_obj.session_hash,
                allowed_roots=(),
            )


class TestRedisPoolSlidingTtlAndOnlineFailClosed:
    def test_shared_pool_reused_across_threads_recreated_on_url_change_and_invalidated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/1')
        pools: list[redis.ConnectionPool] = []
        barrier = threading.Barrier(6)

        def _grab() -> None:
            barrier.wait()
            pools.append(session_service.get_redis_pool())

        threads = [threading.Thread(target=_grab) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(pools) == 6
        assert len({id(p) for p in pools}) == 1
        assert pools[0].connection_kwargs.get('socket_connect_timeout') == 2.0
        assert pools[0].connection_kwargs.get('socket_timeout') == 2.0

        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/2')
        new_pool = session_service.get_redis_pool()
        assert new_pool is not pools[0]

        session_service.invalidate_redis_pool(new_pool, mark_unavailable=False)
        assert session_service._REDIS_POOL is None

    def test_mid_run_redis_drop_invalidates_pool_and_falls_back_in_local_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = 'session-secret-pw-4242'
        redis_url = f'redis://default:{secret}@127.0.0.1:1/0'
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        class _FlakyRedis(fakeredis.FakeStrictRedis):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.fail_ops = False

            def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> Any:
                if self.fail_ops:
                    raise redis.ConnectionError(f'broken pipe to {redis_url}')
                return super().pipeline(transaction=transaction, shard_hint=shard_hint)

            def expire(self, name: Any, time: Any, **kwargs: Any) -> Any:
                if self.fail_ops:
                    raise redis.ConnectionError(f'broken pipe to {redis_url}')
                return super().expire(name, time, **kwargs)

            def hgetall(self, name: Any) -> Any:
                if self.fail_ops:
                    raise redis.ConnectionError(f'broken pipe to {redis_url}')
                return super().hgetall(name)

        flaky = _FlakyRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: flaky)

        s1 = create_session()
        assert hash_session_token(s1.token) not in session_service._MEMORY_STORE

        # Now drop Redis mid-run in local mode -> must log redacted error and fall back cleanly.
        flaky.fail_ops = True
        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            s2 = create_session()
            assert resolve_or_create_session(s1.token).token != s1.token
            up_id = record_upload(session_hash=s2.session_hash, canonical_path=Path('/tmp/a.fa'), file_type='fasta')
            assert fetch_owned_record('upload', up_id) is not None
        assert secret not in caplog.text
        assert hash_session_token(s2.token) in session_service._MEMORY_STORE

    def test_online_mode_fails_closed_with_session_backend_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = 'online-secret-999'
        redis_url = f'redis://default:{secret}@127.0.0.1:1/0'
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')

        class _DeadConn(redis.Connection):
            def connect(self) -> None:
                raise redis.ConnectionError(f'unreachable {redis_url}')

        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: redis.ConnectionPool(connection_class=_DeadConn)),
        )

        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            with pytest.raises(SessionBackendError):
                create_session()
            with pytest.raises(SessionBackendError):
                resolve_or_create_session('existing-tok', deployment_mode='online')
            with pytest.raises(SessionBackendError):
                record_upload(session_hash='h1', canonical_path=Path('/tmp/a.fa'), file_type='fasta')
            with pytest.raises(SessionBackendError):
                fetch_owned_record('upload', 'up-1')
            with pytest.raises(SessionBackendError):
                update_owned_record_fields('job', 'job-1', {'status': 'failed'})
            with pytest.raises(SessionBackendError):
                claim_owned_record_field('job', 'job-1', 'artifact_ids', 'a1')
            with pytest.raises(SessionBackendError):
                delete_owned_record('upload', 'up-1')

        assert secret not in caplog.text
        assert '***' in caplog.text
        assert session_service._MEMORY_STORE == {}
        assert session_service._MEMORY_OWNED_STORE == {}

    def test_session_exists_slides_redis_and_memory_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '100')
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)

        session_obj = create_session()
        key = session_service._session_key(session_obj.session_hash)
        fake_client.expire(key, 10)
        assert fake_client.ttl(key) <= 10

        resolved = resolve_or_create_session(session_obj.token)
        assert resolved.session_hash == session_obj.session_hash
        assert fake_client.ttl(key) > 50

        # Memory mode sliding TTL.
        class _FakeClock:
            def __init__(self) -> None:
                self.now = 1000.0

            def time(self) -> float:
                return self.now

            def monotonic(self) -> float:
                return self.now

        clock = _FakeClock()
        monkeypatch.setattr(session_service, 'time', clock)
        monkeypatch.setattr(session_service, '_redis_connection', lambda: None)

        mem_session = create_session()
        clock.now = 1060.0
        assert resolve_or_create_session(mem_session.token).session_hash == mem_session.session_hash
        assert session_service._MEMORY_STORE[mem_session.session_hash] == pytest.approx(1160.0)


class TestAtomicOwnershipAndMemoryTtl:
    def test_update_and_claim_do_not_resurrect_missing_or_expired_keys_and_heal_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '120')
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '300')
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)

        # Missing keys return False and do not create a zombie key.
        assert update_owned_record_fields('job', 'missing-job', {'status': 'failed'}) is False
        assert claim_owned_record_field('job', 'missing-job', 'artifact_ids', 'a1') is False
        assert fake_client.exists('respro:job:missing-job') == 0

        # Create job & artifact (default TTL = result_ttl_seconds = 120) vs upload (session_ttl = 300).
        job_id = record_job(session_hash='owner-1', upload_ids=['u1'])
        art_id = record_artifact(session_hash='owner-1', canonical_path=Path('/tmp/r.html'), media_type='text/html')
        up_id = record_upload(session_hash='owner-1', canonical_path=Path('/tmp/u.fa'), file_type='fasta')
        assert 1 <= fake_client.ttl(f'respro:job:{job_id}') <= 120
        assert 1 <= fake_client.ttl(f'respro:artifact:{art_id}') <= 120
        assert 120 < fake_client.ttl(f'respro:upload:{up_id}') <= 300

        # Claim succeeds once, fails on second claim.
        assert claim_owned_record_field('job', job_id, 'artifact_ids', art_id) is True
        assert claim_owned_record_field('job', job_id, 'artifact_ids', 'other-id') is False
        rec = fetch_owned_record('job', job_id)
        assert rec is not None and rec.fields['artifact_ids'] == art_id

        # If a key lost its TTL (TTL == -1), update heals it to default TTL.
        fake_client.persist(f'respro:job:{job_id}')
        assert fake_client.ttl(f'respro:job:{job_id}') == -1
        assert update_owned_record_fields('job', job_id, {'status': 'succeeded'}) is True
        assert 1 <= fake_client.ttl(f'respro:job:{job_id}') <= 120

    def test_update_and_claim_invoke_lua_eval_when_supported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_client = fakeredis.FakeStrictRedis()
        lua_calls: list[tuple[str, tuple[Any, ...]]] = []

        def _spy_eval(
            self_client: redis.Redis,
            script: str | bytes,
            numkeys: int,
            *keys_and_args: Any,
        ) -> int:
            del self_client, numkeys
            lua_calls.append((str(script), keys_and_args))
            return 1

        monkeypatch.setattr(redis.Redis, 'eval', _spy_eval)
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)

        assert update_owned_record_fields('job', 'j-lua', {'status': 'done'}) is True
        assert claim_owned_record_field('job', 'j-lua', 'artifact_ids', 'a1,a2') is True
        assert len(lua_calls) == 2
        assert 'EXISTS' in lua_calls[0][0]
        assert 'HSETNX' in lua_calls[1][0]

    def test_concurrent_claim_only_allows_one_winner_in_redis_and_memory(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 1) Shared FakeServer across 8 concurrent threads (WATCH/MULTI/EXEC).
        server = fakeredis.FakeServer()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fakeredis.FakeStrictRedis(server=server))
        job_id = record_job(session_hash='owner-1', upload_ids=[])

        barrier = threading.Barrier(8)
        results: list[bool] = []
        results_lock = threading.Lock()

        def _worker(idx: int) -> None:
            barrier.wait()
            won = claim_owned_record_field('job', job_id, 'artifact_ids', f'art-{idx}')
            with results_lock:
                results.append(won)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert results.count(True) == 1
        assert results.count(False) == 7

        # 2) In-memory fallback concurrency under _MEMORY_OWNED_LOCK.
        monkeypatch.setattr(session_service, '_redis_connection', lambda: None)
        mem_job_id = record_job(session_hash='owner-1', upload_ids=[])
        mem_results: list[bool] = []
        mem_barrier = threading.Barrier(8)

        def _mem_worker(idx: int) -> None:
            mem_barrier.wait()
            won = claim_owned_record_field('job', mem_job_id, 'artifact_ids', f'mem-{idx}')
            with results_lock:
                mem_results.append(won)

        mem_threads = [threading.Thread(target=_mem_worker, args=(i,)) for i in range(8)]
        for t in mem_threads:
            t.start()
        for t in mem_threads:
            t.join(timeout=5.0)
        assert mem_results.count(True) == 1
        assert mem_results.count(False) == 7

    def test_non_eval_response_error_not_swallowed_invalidates_pool_and_fails_closed_online(
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

        with pytest.raises(SessionBackendError):
            update_owned_record_fields('job', 'j-wrongtype', {'status': 'done'})
        assert session_service._REDIS_POOL is None

        with pytest.raises(SessionBackendError):
            claim_owned_record_field('job', 'j-wrongtype', 'artifact_ids', 'a1')
        assert session_service._REDIS_POOL is None
        assert session_service._MEMORY_STORE == {}
        assert session_service._MEMORY_OWNED_STORE == {}

    def test_memory_owned_store_enforces_ttl_and_periodic_sweep(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _FakeClock:
            def __init__(self, initial: float = 1_700_000_000.0) -> None:
                self.now = initial

            def time(self) -> float:
                return self.now

            def monotonic(self) -> float:
                return self.now

        clock = _FakeClock()
        monkeypatch.setattr(session_service, 'time', clock)
        monkeypatch.setattr(session_service, '_redis_connection', lambda: None)
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '5')
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '5')

        s1 = create_session()
        up1 = record_upload(session_hash=s1.session_hash, canonical_path=Path('/tmp/u1.fa'), file_type='fasta')
        up2 = record_upload(session_hash=s1.session_hash, canonical_path=Path('/tmp/u2.fa'), file_type='fasta')
        assert f'respro:upload:{up1}' in session_service._MEMORY_OWNED_STORE
        assert f'respro:upload:{up2}' in session_service._MEMORY_OWNED_STORE

        # Past TTL (6s), before sweep interval (60s): direct read/update/claim/delete must treat as expired.
        clock.now += 6.0
        assert fetch_owned_record('upload', up1) is None
        assert update_owned_record_fields('upload', up1, {'extra': '1'}) is False
        assert claim_owned_record_field('upload', up1, 'claimed', '1') is False
        assert delete_owned_record('upload', up1) is False

        # Past sweep interval (+65s), a fresh write triggers sweep of un-accessed up2 and s1.
        clock.now += 65.0
        s2 = create_session()
        record_upload(session_hash=s2.session_hash, canonical_path=Path('/tmp/u3.fa'), file_type='fasta')
        assert s1.session_hash not in session_service._MEMORY_STORE
        assert f'respro:upload:{up2}' not in session_service._MEMORY_OWNED_STORE

    def test_redis_client_for_mode_online_ping_failure_does_not_set_unavailable_or_log_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv('RESPRO_WEB_DEPLOYMENT_MODE', raising=False)
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        fake_pool = fakeredis.FakeStrictRedis().connection_pool
        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: fake_pool),
        )
        monkeypatch.setattr(
            redis.Redis,
            'ping',
            lambda _self: (_ for _ in ()).throw(ConnectionError('redis down')),
        )

        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            with pytest.raises(SessionBackendError):
                session_service._redis_client_for_mode('online')

        assert session_service._REDIS_UNAVAILABLE_UNTIL is None
        assert 'using in-memory fallback' not in caplog.text


class TestRedactRedisUrl:
    @pytest.mark.parametrize(
        ('raw_url', 'expected'),
        [
            ('redis://user:secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('rediss://:secret@redis.example.com:6380/1', 'rediss://:***@redis.example.com:6380/1'),
            ('redis://user:p/w?a=1#frag@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://u:p@127.0.0.1:6379/0?password=s1&token=s2', 'redis://u:***@127.0.0.1:6379/0?password=***&token=***'),
            ('redis://127.0.0.1:6379/0?pass%77ord=s1&auth=s2', 'redis://127.0.0.1:6379/0?pass%77ord=***&auth=***'),
            ('unix:///tmp/a@b.sock', 'unix:///tmp/a@b.sock'),
            ('unix://user:pw@/tmp/redis.sock', 'unix://user:***@/tmp/redis.sock'),
            ('redis://user:pw@[::1]:6379/0', 'redis://user:***@[::1]:6379/0'),
            ('user:pw@127.0.0.1:6379/0', 'user:***@127.0.0.1:6379/0'),
            ('localhost:6379?user=a@b#a@b', 'localhost:6379?user=a@b#a@b'),
            ('host:port?user=a@b', 'host:port?user=a@b'),
            ('host:port/0#a@b', 'host:port/0#a@b'),
            ('redis://localhost:6379/0?user=a@b', 'redis://localhost:6379/0?user=a@b'),
            ('redis://localhost:6379/0#a@b', 'redis://localhost:6379/0#a@b'),
            # Compound passwords containing @, /, ?, #, =, or numeric prefixes
            ('redis://default:p@ss/w?o#rd@127.0.0.1:6379/0', 'redis://default:***@127.0.0.1:6379/0'),
            ('redis://:@/?#sec@127.0.0.1:6379/0', 'redis://:***@127.0.0.1:6379/0'),
            ('redis://user:1234?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/ab?c@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234#frag@redis.example.com/0', 'redis://user:***@redis.example.com/0'),
            ('redis://user:p@1.2/x@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:p@ss?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('unix://user:p@w/x@/var/run/redis.sock', 'unix://user:***@/var/run/redis.sock'),
            ('user:1234#ss@localhost/0', 'user:***@localhost/0'),
            # Portless, DB-less URLs with @ and / in password + DB-path query invariant preservation
            ('redis://user:p@w/x@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@ss/w?o#rd@redis.example.com', 'redis://user:***@redis.example.com'),
            ('redis://user:pw@localhost/0?user=a@b', 'redis://user:***@localhost/0?user=a@b'),
            ('redis://user:pw@localhost/0#a@b', 'redis://user:***@localhost/0#a@b'),
            ('redis://user:p@ss?1#2@[::1]', 'redis://user:***@[::1]'),
            # Portless, DB-less passwords containing ':', '[', or numeric prefixes before '/', '?', or '#'
            ('redis://user:p@ss:word/x@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@ss:1234/x@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@[foo/x@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@[foo?w=1@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@[::1]/x@localhost', 'redis://user:***@localhost'),
            ('redis://user:1234/x@localhost', 'redis://user:***@localhost'),
            ('redis://:p@ss:word/x@localhost', 'redis://:***@localhost'),
            ('redis://user:p@ss:word?w=1@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@ss:word#frag@localhost', 'redis://user:***@localhost'),
            # M1A invariants: valid host[:port] with '@' only in query/fragment must not over-redact
            ('redis://user:pw@localhost:6379?user=a@b', 'redis://user:***@localhost:6379?user=a@b'),
            ('redis://user:pw@[::1]?user=a@b', 'redis://user:***@[::1]?user=a@b'),
            ('redis://[::1]:6379/0?user=a@b', 'redis://[::1]:6379/0?user=a@b'),
            ('redis://[::1]:6379?user=a@b', 'redis://[::1]:6379?user=a@b'),
            ('redis://[::1]/0?user=a@b', 'redis://[::1]/0?user=a@b'),
            # *+unix schemes & bracket-prefixed usernames & trailing wrapper chars in passwords
            ('redis+unix://user:p@ss/w?o#rd@/tmp/test_probe.sock', 'redis+unix://user:***@/tmp/test_probe.sock'),
            ('valkey+unix://user:p@ss/w?o#rd@/tmp/test_probe.sock', 'valkey+unix://user:***@/tmp/test_probe.sock'),
            ('redis+unix:///tmp/a@b.sock', 'redis+unix:///tmp/a@b.sock'),
            ('redis://[user]:pass/x@localhost', 'redis://***@localhost'),
            ('redis://[user:pass/x@localhost', 'redis://***@localhost'),
            ('redis://[user]:pass:word/x@localhost', 'redis://***@localhost'),
            ('redis://[user]:1234?a=1@127.0.0.1:6379', 'redis://***@127.0.0.1:6379'),
            ('redis://[::1]:1234?a=1@127.0.0.1:6379', 'redis://***@127.0.0.1:6379'),
            ('redis://[user]:1234?a=1@localhost', 'redis://***@localhost'),
            ('redis://user:pass*_~@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:p,w@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:p;w@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:p@ss,w@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            # Query containing @host:port/path must not override genuine authority host[:port]/path
            (
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=s1_secret',
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=***',
            ),
            (
                'https://example.com:8443/cb?user=a@host:8080/p,b&token=s1_secret',
                'https://example.com:8443/cb?user=a@host:8080/p,b&token=***',
            ),
            (
                'badscheme://127.0.0.1:6379/0?user=a@host:8080/p&token=s1_secret',
                'badscheme://127.0.0.1:6379/0?user=a@host:8080/p&token=***',
            ),
            (
                'redis://[::1]:6379/0?user=a@host:8080/p&token=s1_secret',
                'redis://[::1]:6379/0?user=a@host:8080/p&token=***',
            ),
            (
                'redis://127.0.0.1:6379?user=a@host:8080/p&token=s1_secret',
                'redis://127.0.0.1:6379?user=a@host:8080/p&token=***',
            ),
            ('redis://user:pw@localhost?user=a@b', 'redis://user:***@localhost?user=a@b'),
            ('redis://user:p@ss?w@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@ss:word?w=1@localhost', 'redis://user:***@localhost'),
            # Port-only ?query, non-numeric path (?query), #fragment @ alignment, and :port ,k=
            ('redis://127.0.0.1:6379?other=1&password=a@b,pw_secret', 'redis://127.0.0.1:6379?other=1&password=***'),
            ('redis://127.0.0.1:6379?other=1&password=a@b;pw_secret', 'redis://127.0.0.1:6379?other=1&password=***'),
            ('redis://127.0.0.1:6379?other=1&password=a@b)pw_secret', 'redis://127.0.0.1:6379?other=1&password=***'),
            ('redis://[::1]:6379?other=1&password=a@b,pw_secret', 'redis://[::1]:6379?other=1&password=***'),
            ('redis://127.0.0.1:6379?password=a@b:8080,pw_secret', 'redis://127.0.0.1:6379?password=***'),
            ('redis://127.0.0.1:6379?password=a@b:8080/0,k=pw_secret', 'redis://127.0.0.1:6379?password=***'),
            ('redis://127.0.0.1:6379?user=a@b,c&password=s1', 'redis://127.0.0.1:6379?user=a@b,c&password=***'),
            ('redis://[::1]:6379?user=a@b=1&password=s1', 'redis://[::1]:6379?user=a@b=1&password=***'),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b;pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b)pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?other=1&password=a@b,pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?other=1&password=***',
            ),
            (
                'redis+cluster://127.0.0.1:6379/mycluster?password=a@b,pw_secret',
                'redis+cluster://127.0.0.1:6379/mycluster?password=***',
            ),
            (
                'badscheme://127.0.0.1:6379/app?password=a@b,pw_secret',
                'badscheme://127.0.0.1:6379/app?password=***',
            ),
            (
                'redis://user:1234?a=1@127.0.0.1:6379/0?target=a@host:6379,k=1&password=s1',
                'redis://user:***@127.0.0.1:6379/0?target=a@host:6379,k=1&password=***',
            ),
            ('redis://user:pw@localhost/0#a@b', 'redis://user:***@localhost/0#a@b'),
            ('redis://user:pw@localhost:6379#a@b', 'redis://user:***@localhost:6379#a@b'),
            ('redis://user:p@ss#w@localhost', 'redis://user:***@localhost'),
            ('redis://user:p@fake:8080,k=pw_secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            # Iter 10: non-alpha scheme / #frag!redis:// / multi-URL REDIS_URL & auth non-numeric-path ?password=
            (
                'redis://123://x?q=1!redis://h2:6379/0?password=s2',
                'redis://123://x?q=1!redis://h2:6379/0?password=***',
            ),
            (
                'redis://127.0.0.1:6379?q=1!123://x!redis://h2:6379/0?password=s2',
                'redis://127.0.0.1:6379?q=1!123://x!redis://h2:6379/0?password=***',
            ),
            (
                'redis://127.0.0.1:6379?q=1#frag!redis://h2:6379/0?password=s2',
                'redis://127.0.0.1:6379?q=1#frag!redis://h2:6379/0?password=***',
            ),
            (
                'redis://user:pw_secret1@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=s2',
                'redis://user:***@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b,pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b)pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b:8080/0;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b/0;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
            ),
            (
                'redis+cluster://u:sec1@127.0.0.1:6379/mycluster?password=sec2@b;pw_secret',
                'redis+cluster://u:***@127.0.0.1:6379/mycluster?password=***',
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b;pw_secret',
                'badscheme://u:***@127.0.0.1:6379/app?password=***',
            ),
            (
                'redis://u:sec1@localhost/app?password=sec2@b;pw_secret',
                'redis://u:***@localhost/app?password=***',
            ),
            (
                'redis+sentinel://u:sec1@[::1]:26379/mymaster?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@[::1]:26379/mymaster?password=***',
            ),
            (
                'redis://u:sec1@localhost?password=sec2@b:8080/0;pw_secret',
                'redis://u:***@localhost?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?user=a@b;foo',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?user=a@b;foo',
            ),
            # Iter 11: query items without '=' or '://' in key, bare_head_stop+!, multi-host, unix @/, @ in path, #frag
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379/0?foo&!redis://u2:***@h2:6379/0',
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379/0?redis://u2:***@h2:6379/0',
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0=1',
                'redis://127.0.0.1:6379/0?redis://u2:***@h2:6379/0=1',
            ),
            (
                'redis://127.0.0.1:6379/0?redis://h2:6379/0?password=s2',
                'redis://127.0.0.1:6379/0?redis://h2:6379/0?password=***',
            ),
            (
                'redis://user,k=1@127.0.0.1/0!redis://u2:s2@h2:6379/0',
                'redis://***@127.0.0.1/0!redis://u2:***@h2:6379/0',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@[::1]:26379,[::1]:26380/mymaster?password=sec2',
                'redis+sentinel://u:***@[::1]:26379,[::1]:26380/mymaster?password=***',
            ),
            (
                'redis+unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/redis.sock?password=***',
            ),
            (
                'unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run;pw_secret',
                'unix://u:***@/tmp/redis.sock?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/my@master?password=***',
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/my@app?password=sec2@b:8080/0;pw_secret',
                'badscheme://u:***@127.0.0.1:6379/my@app?password=***',
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b#frag@c:8080/0',
                'badscheme://u:***@127.0.0.1:6379/app?password=***#frag@c:8080/0',
            ),
            # Iter 12 regression tests (Explorers 1, 2, 3)
            (
                'redis://user:1234!http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis://pw_part:6379!http://a@127.0.0.1:6379/0',
                'redis://pw_part:***@127.0.0.1:6379/0',
            ),
            (
                'redis://[2001:db8::1]!http://a@127.0.0.1:6379/0',
                'redis://***@127.0.0.1:6379/0',
            ),
            (
                'redis://user,k=1@127.0.0.1/0!redis://u2:s2@h2:6379/0',
                'redis://***@127.0.0.1/0!redis://u2:***@h2:6379/0',
            ),
            (
                'redis://127.0.0.1:6379/0!redis://h2:6379/0?user=a@b&password=s2',
                'redis://127.0.0.1:6379/0!redis://h2:6379/0?user=a@b&password=***',
            ),
            (
                'redis+sentinel://u:sec1@node1,node2:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@node1,node2:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2/mymaster?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2,node3:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2,node3:26379/mymaster?password=***',
            ),
            (
                'redis+sentinel://u:sec1@node1:26379,node2?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2?password=***',
            ),
            (
                'redis+sentinel://127.0.0.1,127.0.0.2:26379/0?password=sec2',
                'redis+sentinel://127.0.0.1,127.0.0.2:26379/0?password=***',
            ),
            (
                'redis+sentinel://node1:26379,node2/mymaster?user=a@b&password=sec2',
                'redis+sentinel://node1:26379,node2/mymaster?user=a@b&password=***',
            ),
            (
                'redis+unix://u:sec1@/tmp/@dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/@dir/redis.sock?password=***',
            ),
            (
                'redis+unix://u:sec1@/tmp/my@dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/my@dir/redis.sock?password=***',
            ),
            (
                'redis+unix://u:sec1@/tmp@/dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/dir/redis.sock?password=***',
            ),
            (
                'redis+sentinel://u:sec1@localhost/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@localhost/my@master?password=***',
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1/my@master?password=***',
            ),
            (
                'redis://u:sec1@localhost/my@app?password=sec2@b;pw_secret',
                'redis://u:***@localhost/my@app?password=***',
            ),
            (
                'redis://u:sec1@localhost/my@app?password=sec2',
                'redis://u:***@localhost/my@app?password=***',
            ),
            (
                'redis://localhost/my@app',
                'redis://localhost/my@app',
            ),
            # Iter 13 regression cases (Findings 2 & 3)
            (
                'redis://user:1234?a=1#frag@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis://user:1234/ab?c=1#frag@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'rediss://user:1234?a=1#frag@127.0.0.1:6379/0',
                'rediss://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis+sentinel://user:1234?a=1#frag@127.0.0.1:26379/mymaster',
                'redis+sentinel://user:***@127.0.0.1:26379/mymaster',
            ),
            (
                'redis://user:1234?a=1#b@localhost/0',
                'redis://user:***@localhost/0',
            ),
            (
                'redis://user:1234?a=1#b/c@d:8080,k=1;m=2@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis://host:6379?a=1#frag@b',
                'redis://host:6379?a=1#frag@b',
            ),
            (
                'redis://host:6379/0?a=1#frag@b',
                'redis://host:6379/0?a=1#frag@b',
            ),
            (
                'redis+sentinel://node1:26379,node2?user=a@b',
                'redis+sentinel://node1:26379,node2?user=a@b',
            ),
            (
                'redis+sentinel://node1:26379,node2#frag@b',
                'redis+sentinel://node1:26379,node2#frag@b',
            ),
            (
                'redis://user:1234,pw?k=1@localhost',
                'redis://user:***@localhost',
            ),
            (
                'redis://user:1234,pw?k=1@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
        ],
    )
    def test_redact_redis_url_cases(self, raw_url: str, expected: str) -> None:
        assert redact_redis_url(raw_url) == expected

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
                'Error(redis://user:p@ss?1#2@[::1])',
                'Error(redis://user:***@[::1])',
                ('p@ss', 'ss?1#2'),
            ),
            (
                'Error([redis://user:p@ss?1#2@[::1]])',
                'Error([redis://user:***@[::1]])',
                ('p@ss', 'ss?1#2'),
            ),
            (
                'Error(redis://user:p@1.2/x@127.0.0.1:6379)',
                'Error(redis://user:***@127.0.0.1:6379)',
                ('1.2/x',),
            ),
            (
                'Failed on redis://user:p@ss/w?o#rd@127.0.0.1:6379.',
                'Failed on redis://user:***@127.0.0.1:6379.',
                ('p@ss', 'w?o#rd'),
            ),
            (
                'Failed on redis://user:p@ss/w?o#rd@localhost.',
                'Failed on redis://user:***@localhost.',
                ('p@ss', 'w?o#rd'),
            ),
            (
                'unix://user:p@w/x@/var/run/redis.sock',
                'unix://user:***@/var/run/redis.sock',
                ('p@w', 'w/x'),
            ),
            (
                'redis://user:pass@127.0.0.1:6379/0?token=sec',
                'redis://user:***@127.0.0.1:6379/0?token=***',
                ('pass', 'sec'),
            ),
            # Colon-terminated errors (and credential-free invariants)
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
                'Failed on redis://localhost:6379/0?user=a@b: timeout',
                'Failed on redis://localhost:6379/0?user=a@b: timeout',
                (),
            ),
            # Backtick-wrapped URLs
            (
                'See `redis://user:1234?a=1@127.0.0.1:6379` for details',
                'See `redis://user:***@127.0.0.1:6379` for details',
                ('1234?a=1', '1234'),
            ),
            (
                'See `redis://user:p@ss/w?o#rd@127.0.0.1:6379` for details',
                'See `redis://user:***@127.0.0.1:6379` for details',
                ('p@ss', 'w?o#rd'),
            ),
            # Unspaced multi-URL tokens (and passwords containing delimiters/schemes)
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
                'redis://u1:p1@h1:6379,redis://u2:p2@h2',
                'redis://u1:***@h1:6379,redis://u2:***@h2',
                ('p1', 'p2'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?next=https://example.com',
                'redis://user:***@127.0.0.1:6379/0?next=https://example.com',
                ('pw',),
            ),
            (
                'redis://user:p,redis://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p,redis',),
            ),
            (
                'redis://user:p=redis://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p=redis',),
            ),
            # Wrapped portless/DB-less passwords with ':' or '['
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
            # redis+unix multi-URL, Markdown emphasis/tilde wrappers, bracket-prefixed usernames
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
                '*redis://user:1234?a=1@127.0.0.1:6379*',
                '*redis://user:***@127.0.0.1:6379*',
                ('1234?a=1', '1234'),
            ),
            (
                '_redis://user:1234?a=1@127.0.0.1:6379_',
                '_redis://user:***@127.0.0.1:6379_',
                ('1234?a=1', '1234'),
            ),
            (
                'redis://user:1234?a=1@127.0.0.1:6379~',
                'redis://user:***@127.0.0.1:6379~',
                ('1234?a=1', '1234'),
            ),
            (
                'Failed on redis://[user]:pass/x@localhost: timeout',
                'Failed on redis://***@localhost: timeout',
                ('pass/x',),
            ),
            (
                'Failed on redis://[user:pass/x@localhost: timeout',
                'Failed on redis://***@localhost: timeout',
                ('pass/x',),
            ),
            # Unspaced cred-free URL + :/)/]/>/&url[0]= + secondary cred-URL (and password safety)
            (
                'unix:///var/run/redis.sock:redis://u2:secret2@h2:6379',
                'unix:///var/run/redis.sock:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]:6379:redis://u2:secret2@h2:6379',
                'redis://[::1]:6379:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://localhost/0:redis://u2:secret2@h2:6379',
                'redis://localhost/0:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'https://example.com/api:redis://u2:sec2@h2:6379',
                'https://example.com/api:redis://u2:***@h2:6379',
                ('sec2',),
            ),
            (
                'https://example.com/foo>redis://u2:secret2@h2:6379',
                'https://example.com/foo>redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]/0>redis://u2:secret2@h2:6379',
                'redis://[::1]/0>redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                '(unix:///tmp/a.sock)redis://u2:secret2@h2:6379',
                '(unix:///tmp/a.sock)redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                '(redis://[::1]:6379)redis://u2:secret2@h2:6379',
                '(redis://[::1]:6379)redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                '{"a":"unix:///tmp/a.sock"}:redis://u2:secret2@h2:6379',
                '{"a":"unix:///tmp/a.sock"}:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]:6379&url[0]=redis://u2:secret2@h2:6379',
                'redis://[::1]:6379&url[0]=redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1],redis://u2:secret2@h2:6379',
                'redis://[::1],redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://user:p:redis://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p:redis',),
            ),
            (
                'redis://user:p://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p://w',),
            ),
            # Query/fragment '@' multi-URLs (no '@' in first authority)
            (
                'redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:secret2@h2:6379',
                'redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]:6379/0?user=a@b&foo=1&url[0]=redis://u2:secret2@h2:6379',
                'redis://[::1]:6379/0?user=a@b&foo=1&url[0]=redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'https://example.com/path?email=a@b.com&next=1,redis://u2:secret2@h2:6379',
                'https://example.com/path?email=a@b.com&next=1,redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://localhost:6379/0?user=a@b&token=1,redis://u2:secret2@h2:6379',
                'redis://localhost:6379/0?user=a@b&token=***,redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'https://example.com/foo?user=a@b&bar=2:redis://u2:secret2@h2:6379',
                'https://example.com/foo?user=a@b&bar=2:redis://u2:***@h2:6379',
                ('secret2',),
            ),
            (
                'redis://[::1]/0?email=a@b=1:redis://h2:6379/0?password=secret2',
                'redis://[::1]/0?email=a@b=1:redis://h2:6379/0?password=***',
                ('secret2',),
            ),
            (
                'https://example.com/path?email=a@b.com&next=1,redis://h2:6379/0?password=secret2',
                'https://example.com/path?email=a@b.com&next=1,redis://h2:6379/0?password=***',
                ('secret2',),
            ),
            # Non-whitespace structured-log / compact JSON / HTML / XML / Markdown links
            (
                '<a href="redis://user:1234?a=1@127.0.0.1:6379">here</a>',
                '<a href="redis://user:***@127.0.0.1:6379">here</a>',
                ('1234?a=1', '1234'),
            ),
            (
                '<url>redis://user:1234?a=1@127.0.0.1:6379</url>',
                '<url>redis://user:***@127.0.0.1:6379</url>',
                ('1234?a=1', '1234'),
            ),
            (
                '<a href="redis://user:p@ss/w@host:6379">link</a>',
                '<a href="redis://user:***@host:6379">link</a>',
                ('p@ss', 'ss/w'),
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
                '(redis://user:1234?a=1@127.0.0.1:6379)foo',
                '(redis://user:***@127.0.0.1:6379)foo',
                ('1234?a=1', '1234'),
            ),
            (
                '[click](redis://user:1234?a=1@127.0.0.1:6379)text',
                '[click](redis://user:***@127.0.0.1:6379)text',
                ('1234?a=1', '1234'),
            ),
            (
                '{"url":"redis://user:1234?a=1@127.0.0.1:6379","email":"a@b.com/1"}',
                '{"url":"redis://user:***@127.0.0.1:6379","email":"a@b.com/1"}',
                ('1234?a=1', '1234'),
            ),
            (
                '{"url":"redis://user:p@ss/w@host:6379","foo":"bar","next":"redis://u2:s2@h2:6379"}',
                '{"url":"redis://user:***@host:6379","foo":"bar","next":"redis://u2:***@h2:6379"}',
                ('p@ss', 'ss/w', 's2'),
            ),
            (
                '{"url":"redis://127.0.0.1:6379/0?password=s1","next":"ok"}',
                '{"url":"redis://127.0.0.1:6379/0?password=***","next":"ok"}',
                ('s1',),
            ),
            (
                'redis://user:p,w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p,w',),
            ),
            (
                'redis://user:p;w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p;w',),
            ),
            (
                'redis://127.0.0.1:6379/0?user=a@b,c&password=s1',
                'redis://127.0.0.1:6379/0?user=a@b,c&password=***',
                ('s1',),
            ),
            # Bracket-prefixed usernames with numeric password prefix + IPv6 preservation
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
                'redis://[::1]:6379/0?user=a@b',
                'redis://[::1]:6379/0?user=a@b',
                (),
            ),
            (
                'redis://[::1]:6379?user=a@b',
                'redis://[::1]:6379?user=a@b',
                (),
            ),
            (
                'redis://[::1]/0?user=a@b',
                'redis://[::1]/0?user=a@b',
                (),
            ),
            # Passwords with structural delimiters + @ / : before @host or @/path
            (
                'redis://user:p@ss/w,secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('ss/w', 'secret'),
            ),
            (
                'redis://user:p@ss/w;secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('ss/w', 'secret'),
            ),
            (
                'redis://user:p@ss/w>secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('ss/w', 'secret'),
            ),
            (
                'redis://user:p@ss/w)secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('ss/w', 'secret'),
            ),
            (
                'redis://user:p@ss/w"secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('ss/w', 'secret'),
            ),
            (
                'redis://user:p@h1:80,secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('h1:80', 'secret'),
            ),
            (
                'redis://user:p@h1:80)secret@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('h1:80', 'secret'),
            ),
            (
                'redis://user:p@127.0.0.1:6379,pw2@realhost:6379',
                'redis://user:***@realhost:6379',
                ('pw2',),
            ),
            (
                '{"url":"redis://user:p@127.0.0.1:6379,pw2@realhost:6379","next":"ok"}',
                '{"url":"redis://user:***@realhost:6379","next":"ok"}',
                ('pw2',),
            ),
            (
                'unix://user:p@/w>secret@/tmp/redis.sock',
                'unix://user:***@/tmp/redis.sock',
                ('secret',),
            ),
            (
                'redis+unix://user:p@/w>secret@/tmp/redis.sock',
                'redis+unix://user:***@/tmp/redis.sock',
                ('secret',),
            ),
            (
                '{"url":"redis+unix://user:p\\"w,a;b>@/tmp/a.sock","next":"ok"}',
                '{"url":"redis+unix://user:***@/tmp/a.sock","next":"ok"}',
                ('p"w', 'a;b>'),
            ),
            (
                'url=redis://user:1234?a=1@127.0.0.1:6379,email=a@b.com/1',
                'url=redis://user:***@127.0.0.1:6379,email=a@b.com/1',
                ('1234?a=1', '1234'),
            ),
            # Query-secret preservation, percent-encoded keys, JSON/HTML/paren bounds & userinfo '?'
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
                'https://example.com/cb?user=a@host:8080/p,b&token=s1',
                'https://example.com/cb?user=a@host:8080/p,b&token=***',
                ('s1',),
            ),
            (
                '{"url":"redis://127.0.0.1:6379/0?user=a@b&password=s1","next":"ok"}',
                '{"url":"redis://127.0.0.1:6379/0?user=a@b&password=***","next":"ok"}',
                ('s1',),
            ),
            (
                '<a href="redis://127.0.0.1:6379/0?user=a@b&password=s1">here</a>',
                '<a href="redis://127.0.0.1:6379/0?user=a@b&password=***">here</a>',
                ('s1',),
            ),
            (
                '(redis://127.0.0.1:6379/0?user=a@b&password=s1)foo',
                '(redis://127.0.0.1:6379/0?user=a@b&password=***)foo',
                ('s1',),
            ),
            (
                'url=redis://user:1234/ab?c@localhost,auth_ok=1&reason=timeout',
                'url=redis://user:***@localhost,auth_ok=1&reason=timeout',
                ('1234/ab?c', '1234'),
            ),
            # Multi-URL with portless host + structured log context before next URL
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
                'url=redis://user:pw?a=1@localhost,reason=timeout,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost,reason=timeout,next=redis://h2:6379/0?password=***',
                ('pw?a=1', 'secret2'),
            ),
            (
                'url=redis://user:pw?a=1@127.0.0.1:6379/0?user=a@b,c&password=s1,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@127.0.0.1:6379/0?user=a@b,c&password=***,next=redis://h2:6379/0?password=***',
                ('pw?a=1', 's1', 'secret2'),
            ),
            # Iter 7 regressions: pre-authority outer-delimiter passwords (12 non-unix + JSON escaped + 4 unix)
            ('redis://user:p">secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p"}secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p</secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p",{"a":1}@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('"a":1',)),
            ("redis://user:p',secret@127.0.0.1:6379/0", 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ("redis://user:p'>secret@127.0.0.1:6379/0", 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ("redis://user:p'k=1@127.0.0.1:6379/0", 'redis://user:***@127.0.0.1:6379/0', ('k=1',)),
            ('redis://user:p"k=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('k=1',)),
            ("redis://user:p':secret@127.0.0.1:6379/0", 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p":secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p"a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('a=1',)),
            ('redis://user:p},secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('secret',)),
            (
                '{"url":"redis://user:p\\">secret@127.0.0.1:6379/0","next":"ok"}',
                '{"url":"redis://user:***@127.0.0.1:6379/0","next":"ok"}',
                ('secret',),
            ),
            ('unix://user:p@/w">secret@/tmp/redis.sock', 'unix://user:***@/tmp/redis.sock', ('secret',)),
            ('unix://user:p@/w"}secret@/tmp/redis.sock', 'unix://user:***@/tmp/redis.sock', ('secret',)),
            ('unix://user:p@/w</secret@/tmp/redis.sock', 'unix://user:***@/tmp/redis.sock', ('secret',)),
            ("unix://user:p@/w',secret@/tmp/redis.sock", 'unix://user:***@/tmp/redis.sock', ('secret',)),
            # Tiered ,key= / ;key= inside multi-@ passwords vs real outer ,key=
            ('redis://user:p@fake,k=pw_secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('pw_secret',)),
            ('redis://user:p@fake/0,k=pw_secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('pw_secret',)),
            ('redis://user:p@fake;k=pw_secret@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0', ('pw_secret',)),
            # Query @host:port/p, userinfo ?, portless @localhost?, secret query values with @ + delims
            (
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=s1_secret',
                'redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=***',
                ('s1_secret',),
            ),
            (
                'https://example.com:8443/cb?user=a@host:8080/p,b&token=s1_secret',
                'https://example.com:8443/cb?user=a@host:8080/p,b&token=***',
                ('s1_secret',),
            ),
            (
                'redis://user:1234?a=1@127.0.0.1:6379/0?target=a@host:6379,k=1&password=s1',
                'redis://user:***@127.0.0.1:6379/0?target=a@host:6379,k=1&password=***',
                ('1234?a=1', 's1'),
            ),
            (
                'redis://user:pw@localhost?target=a@host:6379,b&password=s1',
                'redis://user:***@localhost?target=a@host:6379,b&password=***',
                ('pw', 's1'),
            ),
            (
                'redis://user:pw?a=1@localhost?target=a@host:6379,b&password=s1',
                'redis://user:***@localhost?target=a@host:6379,b&password=***',
                ('pw?a=1', 's1'),
            ),
            ('redis://127.0.0.1:6379/0?password=a@b,secret', 'redis://127.0.0.1:6379/0?password=***', ('secret',)),
            (
                'redis://127.0.0.1:6379/0?password=a@b,secret&other=1',
                'redis://127.0.0.1:6379/0?password=***&other=1',
                ('secret',),
            ),
            (
                'redis://127.0.0.1:6379/0?foo=1&password=a@b,secret&bar=2',
                'redis://127.0.0.1:6379/0?foo=1&password=***&bar=2',
                ('secret',),
            ),
            ('redis://127.0.0.1:6379/0?pass%77ord=a@b,secret', 'redis://127.0.0.1:6379/0?pass%77ord=***', ('secret',)),
            ('https://example.com/cb?token=a@host:8080/p,secret', 'https://example.com/cb?token=***', ('secret',)),
            ('redis://127.0.0.1:6379/0?password=a@host:6379,secret', 'redis://127.0.0.1:6379/0?password=***', ('secret',)),
            ('redis://127.0.0.1:6379/0?password=a@b;pw_secret', 'redis://127.0.0.1:6379/0?password=***', ('pw_secret',)),
            ('redis://127.0.0.1:6379/0?password=a@b)pw_secret', 'redis://127.0.0.1:6379/0?password=***', ('pw_secret',)),
            ('redis://127.0.0.1:6379/0?password=a@b]pw_secret', 'redis://127.0.0.1:6379/0?password=***', ('pw_secret',)),
            ('(redis://127.0.0.1:6379/0?password=a@b)foo', '(redis://127.0.0.1:6379/0?password=***)foo', ('a@b',)),
            # Portless host + '@' query + ,next= multi-URL & JSON literal arrays & bang-separated multi-URL
            (
                'url=redis://user:pw@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                'url=redis://user:pw@localhost?user=a@b&password=s1,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost?user=a@b&password=***,next=redis://h2:6379/0?password=***',
                ('pw', 's1', 'secret2'),
            ),
            (
                'url=redis://user:pw@localhost?user=a@b&url[0]=http://x,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost?user=a@b&url[0]=http://x,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                '["redis://127.0.0.1:6379/0?user=a@b&password=s1",123,"a@b.com/1"]',
                '["redis://127.0.0.1:6379/0?user=a@b&password=***",123,"a@b.com/1"]',
                ('s1',),
            ),
            (
                '["redis://127.0.0.1:6379/0?user=a@b&password=s1",false,"a@b.com/1"]',
                '["redis://127.0.0.1:6379/0?user=a@b&password=***",false,"a@b.com/1"]',
                ('s1',),
            ),
            (
                'redis://user:p@fake">secret@127.0.0.1:6379/0!redis://u2:s2@h2:6379',
                'redis://user:***@127.0.0.1:6379/0!redis://u2:***@h2:6379',
                ('secret', 's2'),
            ),
            # Query-embedded URLs & bare query-embedded URLs
            (
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=SECRET123',
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=***',
                ('SECRET123',),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?next=redis://other:6379/0&password=s1',
                'redis://user:***@127.0.0.1:6379/0?next=redis://other:6379/0&password=***',
                ('pw', 's1'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?cb=http://a&password=s1',
                'redis://user:***@127.0.0.1:6379/0?cb=http://a&password=***',
                ('pw', 's1'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1&cb=http://a&password=s1',
                'redis://user:***@127.0.0.1:6379/0?q=1&cb=http://a&password=***',
                ('pw', 's1'),
            ),
            (
                '?q=1&cb=http://a&password=s1',
                '?q=1&cb=http://a&password=***',
                ('s1',),
            ),
            (
                'https://app.example.com/auth?redirect=https://u2:s2@client.example.com/cb&token=SECRET123',
                'https://app.example.com/auth?redirect=https://u2:***@client.example.com/cb&token=***',
                ('s2', 'SECRET123'),
            ),
            (
                'https://app.example.com/auth?redirect=https://u2:s2@client.example.com/cb&password=a@b,pw_secret',
                'https://app.example.com/auth?redirect=https://u2:***@client.example.com/cb&password=***',
                ('s2', 'pw_secret'),
            ),
            # '?'/'#' + '!redis://' multi-URLs & password safety
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=s2',
                'redis://user:***@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=***',
                ('pw', 's2'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0#frag!redis://h2:6379/0?password=s2',
                'redis://user:***@127.0.0.1:6379/0#frag!redis://h2:6379/0?password=***',
                ('pw', 's2'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1!redis://u2:s2@h2:6379/0',
                'redis://user:***@127.0.0.1:6379/0?q=1!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0#frag!redis://u2:s2@h2:6379/0',
                'redis://user:***@127.0.0.1:6379/0#frag!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://127.0.0.1:6379?q=1!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379?q=1!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://user:p!redis://w@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('p!redis://w',),
            ),
            # Port-only ?query & non-numeric paths (redis+sentinel / redis+cluster / badscheme)
            (
                'redis://127.0.0.1:6379?other=1&password=a@b,pw_secret',
                'redis://127.0.0.1:6379?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis://127.0.0.1:6379?other=1&password=a@b;pw_secret',
                'redis://127.0.0.1:6379?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis://127.0.0.1:6379?other=1&password=a@b)pw_secret',
                'redis://127.0.0.1:6379?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis://[::1]:6379?other=1&password=a@b,pw_secret',
                'redis://[::1]:6379?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis://127.0.0.1:6379?password=a@b:8080,pw_secret',
                'redis://127.0.0.1:6379?password=***',
                ('pw_secret',),
            ),
            (
                'redis://127.0.0.1:6379?password=a@b:8080/0,k=pw_secret',
                'redis://127.0.0.1:6379?password=***',
                ('pw_secret',),
            ),
            (
                'redis://127.0.0.1:6379?user=a@b,c&password=s1',
                'redis://127.0.0.1:6379?user=a@b,c&password=***',
                ('s1',),
            ),
            (
                'redis://[::1]:6379?user=a@b=1&password=s1',
                'redis://[::1]:6379?user=a@b=1&password=***',
                ('s1',),
            ),
            (
                'redis://127.0.0.1:6379?target=a@host:6379,k=1&password=s1',
                'redis://127.0.0.1:6379?target=a@host:6379,k=1&password=***',
                ('s1',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
                ('pw_secret',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b;pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
                ('pw_secret',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?password=a@b)pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?password=***',
                ('pw_secret',),
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?other=1&password=a@b,pw_secret',
                'redis+sentinel://127.0.0.1:26379/mymaster?other=1&password=***',
                ('pw_secret',),
            ),
            (
                'redis+cluster://127.0.0.1:6379/mycluster?password=a@b,pw_secret',
                'redis+cluster://127.0.0.1:6379/mycluster?password=***',
                ('pw_secret',),
            ),
            (
                'badscheme://127.0.0.1:6379/app?password=a@b,pw_secret',
                'badscheme://127.0.0.1:6379/app?password=***',
                ('pw_secret',),
            ),
            (
                '{"url":"redis+sentinel://127.0.0.1:26379/mymaster","email":"a@b.com/1"}',
                '{"url":"redis+sentinel://127.0.0.1:26379/mymaster","email":"a@b.com/1"}',
                (),
            ),
            (
                'url=redis://user:pw@localhost#a@b,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@b,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                'url=redis://user:pw@localhost/0#a@b,next=redis://h2:6379/0?password=secret2',
                'url=redis://user:***@localhost/0#a@b,next=redis://h2:6379/0?password=***',
                ('pw', 'secret2'),
            ),
            (
                'url=redis://u:p@fake:8080,email=a@b:8080/1',
                'url=redis://u:***@fake:8080,email=a@b:8080/1',
                (),
            ),
            # HTML </a> email preservation & Sub-phase 4a colonless userinfo
            (
                '<a href="redis://user:pw@localhost">a@b.com</a>',
                '<a href="redis://user:***@localhost">a@b.com</a>',
                ('pw',),
            ),
            (
                '<a href="redis://user:pw@127.0.0.1:6379/0">a@b.com</a>',
                '<a href="redis://user:***@127.0.0.1:6379/0">a@b.com</a>',
                ('pw',),
            ),
            (
                "<a href='redis://user:pw@localhost'>a@b.com</a>",
                "<a href='redis://user:***@localhost'>a@b.com</a>",
                ('pw',),
            ),
            (
                '<a href="redis://user:pw@localhost">a@b.com</a>.',
                '<a href="redis://user:***@localhost">a@b.com</a>.',
                ('pw',),
            ),
            (
                '<b><a href="redis://user:pw@localhost">a@b.com</a></b>',
                '<b><a href="redis://user:***@localhost">a@b.com</a></b>',
                ('pw',),
            ),
            (
                '<a href="redis://localhost">a@b.com</a>',
                '<a href="redis://localhost">a@b.com</a>',
                (),
            ),
            (
                '<a href="redis://127.0.0.1:6379">a@b.com</a>',
                '<a href="redis://127.0.0.1:6379">a@b.com</a>',
                (),
            ),
            (
                '<a href="redis+unix://u:p@/tmp/a.sock">a@/b</a>',
                '<a href="redis+unix://u:***@/tmp/a.sock">a@/b</a>',
                (':p',),
            ),
            (
                'redis://u1:p1@h1">a@b.com</a>,next=redis://u2:p2@h2">c@d.com</a>',
                'redis://u1:***@h1">a@b.com</a>,next=redis://u2:***@h2">c@d.com</a>',
                ('p1', 'p2'),
            ),
            (
                'redis://u1:p1@h1">a@b.com</a>redis://u2:p2@h2',
                'redis://u1:***@h1">a@b.com</a>redis://u2:***@h2',
                ('p1', 'p2'),
            ),
            (
                'redis://secretuser,k=pw@127.0.0.1:6379/0',
                'redis://***@127.0.0.1:6379/0',
                ('secretuser', 'pw'),
            ),
            (
                'redis://secretuser"},pw@127.0.0.1:6379/0',
                'redis://***@127.0.0.1:6379/0',
                ('secretuser', 'pw'),
            ),
            (
                'redis://secretuser","k":"pw"@127.0.0.1:6379/0',
                'redis://secretuser","k":***@127.0.0.1:6379/0',
                ('pw',),
            ),
            (
                '(redis://secretuser)pw@127.0.0.1:6379/0',
                '(redis://***@127.0.0.1:6379/0',
                ('secretuser', 'pw'),
            ),
            (
                'redis://localhost,email=a@b.com/1',
                'redis://localhost,email=a@b.com/1',
                (),
            ),
            (
                '{"url":"redis://localhost","email":"a@b.com/1"}',
                '{"url":"redis://localhost","email":"a@b.com/1"}',
                (),
            ),
            (
                '["redis://localhost",123,"a@b.com/1"]',
                '["redis://localhost",123,"a@b.com/1"]',
                (),
            ),
            (
                'redis://localhost,k=1!redis://u2:s2@h2:6379/0',
                'redis://localhost,k=1!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://localhost,email=a@b.com/1!redis://u2:s2@h2:6379/0',
                'redis://localhost,email=a@b.com/1!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            # Iter 10 Explorer 1: non-alpha 123:// before !redis://, #frag!redis://, json_open }x
            (
                'redis://123://x?q=1!redis://h2:6379/0?password=s2',
                'redis://123://x?q=1!redis://h2:6379/0?password=***',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379?q=1!123://x!redis://h2:6379/0?password=s2',
                'redis://127.0.0.1:6379?q=1!123://x!redis://h2:6379/0?password=***',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379?q=1#frag!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379?q=1#frag!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379?q=1#a@b!redis://u2:s2@h2:6379/0',
                'redis://127.0.0.1:6379?q=1#a@b!redis://u2:***@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://u:p@1.2/x",{}x",{"c"}]@end',
                'redis://u:***@1.2/x",{}x",{"c"}]@end',
                ('u:p@',),
            ),
            (
                'redis://h:6379?",{}x",{"c"}]@end',
                'redis://h:6379?",{}x",{"c"}]@end',
                (),
            ),
            # Iter 10 Explorer 2: authenticated non-numeric-path ?password=
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
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?other=1&password=sec2@b,pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?other=1&password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b:8080,pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b:8080/0,pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+cluster://u:sec1@127.0.0.1:6379/mycluster?password=sec2@b,pw_secret',
                'redis+cluster://u:***@127.0.0.1:6379/mycluster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b,pw_secret',
                'badscheme://u:***@127.0.0.1:6379/app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/app?password=sec2@b,pw_secret',
                'redis://u:***@localhost/app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/app?password=sec2@b;pw_secret',
                'redis://u:***@localhost/app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@[::1]:26379/mymaster?password=sec2@b,pw_secret',
                'redis+sentinel://u:***@[::1]:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                '(redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b)pw_secret)',
                '(redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***)pw_secret)',
                ('sec1', 'sec2'),
            ),
            (
                '{"url":"redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b&token=pw_secret"}',
                '{"url":"redis+sentinel://u:***@127.0.0.1:26379/mymaster?password=***&token=***"}',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            # Iter 10 Explorer 3: quoted/bracketed query-embedded URLs & bare_head_stop ,key= userinfo
            (
                'https://app.example.com/auth?redirect="https://client.example.com/cb&token=SECRET123"',
                'https://app.example.com/auth?redirect="https://client.example.com/cb&token=***"',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect="https://client.example.com/cb"&token=SECRET123',
                'https://app.example.com/auth?redirect="https://client.example.com/cb"&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect=<https://client.example.com/cb>&token=SECRET123',
                'https://app.example.com/auth?redirect=<https://client.example.com/cb>&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect={https://client.example.com/cb}&token=SECRET123',
                'https://app.example.com/auth?redirect={https://client.example.com/cb}&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect=https://client.example.com/cb?a=1"&token=SECRET123',
                'https://app.example.com/auth?redirect=https://client.example.com/cb?a=1"&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?a="foo"&redirect="https://client.example.com/cb"&token=SECRET123',
                'https://app.example.com/auth?a="foo"&redirect="https://client.example.com/cb"&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect="https://client.example.com/cb&token=SECRET123"',
                'GET /auth?redirect="https://client.example.com/cb&token=***"',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect="https://client.example.com/cb"&token=SECRET123',
                'GET /auth?redirect="https://client.example.com/cb"&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect="https://client.example.com/cb?a=1"&token=SECRET123',
                'GET /auth?redirect="https://client.example.com/cb?a=1"&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=<https://client.example.com/cb>&token=SECRET123',
                'GET /auth?redirect=<https://client.example.com/cb>&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=<https://client.example.com/cb?a=1>&token=SECRET123',
                'GET /auth?redirect=<https://client.example.com/cb?a=1>&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect={https://client.example.com/cb}&token=SECRET123',
                'GET /auth?redirect={https://client.example.com/cb}&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect={https://client.example.com/cb?a=1}&token=SECRET123',
                'GET /auth?redirect={https://client.example.com/cb?a=1}&token=***',
                ('SECRET123',),
            ),
            (
                "GET /auth?redirect='https://client.example.com/cb'&token=SECRET123",
                "GET /auth?redirect='https://client.example.com/cb'&token=***",
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=(https://client.example.com/cb)&token=SECRET123',
                'GET /auth?redirect=(https://client.example.com/cb)&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=(https://client.example.com/cb?a=1)&token=SECRET123',
                'GET /auth?redirect=(https://client.example.com/cb?a=1)&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=[https://client.example.com/cb]&token=SECRET123',
                'GET /auth?redirect=[https://client.example.com/cb]&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=[https://client.example.com/cb?a=1]&token=SECRET123',
                'GET /auth?redirect=[https://client.example.com/cb?a=1]&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=(redis://127.0.0.1:6379/app?a=1@b)&token=SECRET123',
                'GET /auth?redirect=(redis://127.0.0.1:6379/app?a=1@b)&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=[redis://127.0.0.1:6379/app?a=1@b]&token=SECRET123',
                'GET /auth?redirect=[redis://127.0.0.1:6379/app?a=1@b]&token=***',
                ('SECRET123',),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?redirect="redis://u2:s2@h2:6379/0"&password=s1',
                'redis://user:***@127.0.0.1:6379/0?redirect="redis://u2:***@h2:6379/0"&password=***',
                ('pw', 's2', 's1'),
            ),
            (
                '<b>https://app.example.com/auth?a=1</b>&token=SECRET123',
                '<b>https://app.example.com/auth?a=1</b>&token=SECRET123',
                (),
            ),
            (
                '<https://app.example.com/auth?a=1>&token=SECRET123',
                '<https://app.example.com/auth?a=1>&token=SECRET123',
                (),
            ),
            ('redis://user,k=1:pw@127.0.0.1/0', 'redis://user,k=1:***@127.0.0.1/0', ('pw',)),
            ('redis://user,k=1:pw@localhost', 'redis://user,k=1:***@localhost', ('pw',)),
            ('redis://user,k=1@127.0.0.1/0', 'redis://***@127.0.0.1/0', ('user,k=1',)),
            ('redis://user,k=1@localhost', 'redis://***@localhost', ('user,k=1',)),
            (
                'redis://user,k=1@127.0.0.1/0,email=a@b.com/1',
                'redis://***@127.0.0.1/0,email=a@b.com/1',
                ('user,k=1',),
            ),
            ('url=redis://host,k=pw@otherhost', 'url=redis://host,k=pw@otherhost', ()),
            ('redis://localhost,time=12:30,email=a@b.com/1', 'redis://localhost,time=12:30,email=a@b.com/1', ()),
            # Iter 11 regression tests (all 6 WA-010 findings)
            (
                'redis://user,k=1:pw@localhost!redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user,k=1:pw@localhost~redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost~redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user,k=1:pw@localhost^redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost^redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user,k=1:pw@localhost*redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost*redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user,k=1:pw@localhost!https://example.com',
                'redis://user,k=1:***@localhost!https://example.com',
                ('pw',),
            ),
            (
                'redis://user,k=1:pw@127.0.0.1!redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@127.0.0.1!redis://u2:***@h2:6379/0',
                ('pw', 's2'),
            ),
            (
                'redis://user,k=1@localhost!redis://u2:s2@h2:6379/0',
                'redis://***@localhost!redis://u2:***@h2:6379/0',
                ('user,k=1', 's2'),
            ),
            (
                'redis://user,k=1:pw@localhost!123://x!redis://u2:s2@h2:6379/0',
                'redis://user,k=1:***@localhost!123://x!redis://u2:***@h2:6379/0',
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
                'redis://127.0.0.1:6379/0?redis://h2:6379/0?password=s2',
                'redis://127.0.0.1:6379/0?redis://h2:6379/0?password=***',
                ('s2',),
            ),
            (
                r'{"url":"https://app.example.com/auth?q=\"foo\"&token=SECRET123"}',
                r'{"url":"https://app.example.com/auth?q=\"foo\"&token=***"}',
                ('SECRET123',),
            ),
            (
                r'{"url":"https://app.example.com/auth?redirect=\"https://client.example.com/cb\"&token=SECRET123"}',
                r'{"url":"https://app.example.com/auth?redirect=\"https://client.example.com/cb\"&token=***"}',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=("https://client.example.com/cb")&token=SECRET123',
                'GET /auth?redirect=("https://client.example.com/cb")&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=("https://client.example.com/cb?a=1")&token=SECRET123',
                'GET /auth?redirect=("https://client.example.com/cb?a=1")&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect=("https://client.example.com/cb")&token=SECRET123',
                'https://app.example.com/auth?redirect=("https://client.example.com/cb")&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect=["https://client.example.com/cb"]&token=SECRET123',
                'https://app.example.com/auth?redirect=["https://client.example.com/cb"]&token=***',
                ('SECRET123',),
            ),
            (
                'https://app.example.com/auth?redirect={"https://client.example.com/cb"}&token=SECRET123',
                'https://app.example.com/auth?redirect={"https://client.example.com/cb"}&token=***',
                ('SECRET123',),
            ),
            (
                'GET /auth?redirect=<"https://client.example.com/cb">&token=SECRET123',
                'GET /auth?redirect=<"https://client.example.com/cb">&token=***',
                ('SECRET123',),
            ),
            (
                '{"url":"https://app.example.com/auth?a=1"}&token=ok',
                '{"url":"https://app.example.com/auth?a=1"}&token=ok',
                (),
            ),
            (
                '<b>https://app.example.com/auth?a=1</b>&token=ok',
                '<b>https://app.example.com/auth?a=1</b>&token=ok',
                (),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=sec2',
                'redis+sentinel://u:***@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@[::1]:26379,[::1]:26380/mymaster?password=sec2',
                'redis+sentinel://u:***@[::1]:26379,[::1]:26380/mymaster?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379,127.0.0.2:26379/mymaster?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run;pw_secret',
                'unix://u:***@/tmp/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+unix://u:sec1@/tmp/redis.sock?password=sec2@/var/run)pw_secret',
                'redis+unix://u:***@/tmp/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/my@master?password=sec2@b;pw_secret',
                'redis+sentinel://u:***@127.0.0.1:26379/my@master?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/my@app?password=sec2@b:8080/0;pw_secret',
                'badscheme://u:***@127.0.0.1:6379/my@app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'badscheme://u:sec1@127.0.0.1:6379/app?password=sec2@b#frag@c:8080/0',
                'badscheme://u:***@127.0.0.1:6379/app?password=***#frag@c:8080/0',
                ('sec1', 'sec2'),
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
                'redis://pw_part:6379!http://a@127.0.0.1:6379/0',
                'redis://pw_part:***@127.0.0.1:6379/0',
                ('6379!',),
            ),
            (
                'redis://[2001:db8::1]!http://a@127.0.0.1:6379/0',
                'redis://***@127.0.0.1:6379/0',
                ('2001:db8::1',),
            ),
            (
                'redis://user,k=1@127.0.0.1/0!redis://u2:s2@h2:6379/0',
                'redis://***@127.0.0.1/0!redis://u2:***@h2:6379/0',
                ('user,k=1', 's2'),
            ),
            (
                'redis://127.0.0.1:6379/0!redis://h2:6379/0?user=a@b&password=s2',
                'redis://127.0.0.1:6379/0!redis://h2:6379/0?user=a@b&password=***',
                ('s2',),
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
                'redis+sentinel://u:sec1@node1:26379,node2?password=sec2',
                'redis+sentinel://u:***@node1:26379,node2?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis+sentinel://127.0.0.1,127.0.0.2:26379/0?password=sec2',
                'redis+sentinel://127.0.0.1,127.0.0.2:26379/0?password=***',
                ('sec2',),
            ),
            (
                'redis+sentinel://node1:26379,node2/mymaster?user=a@b&password=sec2',
                'redis+sentinel://node1:26379,node2/mymaster?user=a@b&password=***',
                ('sec2',),
            ),
            (
                'redis+unix://u:sec1@/tmp/@dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/@dir/redis.sock?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis+unix://u:sec1@/tmp/my@dir/redis.sock?password=sec2@/var/run,pw_secret',
                'redis+unix://u:***@/tmp/my@dir/redis.sock?password=***',
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
                'redis://u:sec1@localhost/my@app?password=sec2@b;pw_secret',
                'redis://u:***@localhost/my@app?password=***',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://u:sec1@localhost/my@app?password=sec2',
                'redis://u:***@localhost/my@app?password=***',
                ('sec1', 'sec2'),
            ),
            (
                'redis://localhost/my@app',
                'redis://localhost/my@app',
                (),
            ),
            (
                'redis://127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1',
                'redis://127.0.0.1:6379/0?password=***',
                ('secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo=1&token=https://secret_host.internal/secret_path?a=1',
                'redis://127.0.0.1:6379/0?foo=1&token=***',
                ('secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?secret=https://u2:s2@secret_host.internal/secret_path?a=1',
                'redis://127.0.0.1:6379/0?secret=***',
                ('u2', 's2', 'secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?%70assword=https://secret_host.internal/secret_path?a=1',
                'redis://127.0.0.1:6379/0?%70assword=***',
                ('secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?password=https://a.com?next=https://b.com?c=1',
                'redis://127.0.0.1:6379/0?password=***',
                ('a.com', 'b.com'),
            ),
            (
                'redis://user:p#f@127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1',
                'redis://user:***@127.0.0.1:6379/0?password=***',
                ('p#f', 'secret_host', 'secret_path'),
            ),
            (
                'redis://127.0.0.1:6379/0?redirect=https://client.example.com/cb&token=SECRET123',
                'redis://127.0.0.1:6379/0?redirect=https://client.example.com/cb&token=***',
                ('SECRET123',),
            ),
            (
                'redis://127.0.0.1:6379/0?redirect=https://client.example.com/cb?a=1&token=SECRET123',
                'redis://127.0.0.1:6379/0?redirect=https://client.example.com/cb?a=1&token=***',
                ('SECRET123',),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?foo&!redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?!redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?!redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&~redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?foo&~redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&^redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?foo&^redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&*redis://u2:s2@h2:6379/0&password=s1',
                'redis://127.0.0.1:6379/0?foo&*redis://u2:***@h2:6379/0&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!"redis://u2:s2@h2:6379/0"&password=s1',
                'redis://127.0.0.1:6379/0?foo&!"redis://u2:***@h2:6379/0"&password=***',
                ('s2', 's1'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://h2:6379/0&password=a@b,s1',
                'redis://127.0.0.1:6379/0?foo&!redis://h2:6379/0&password=***',
                ('s1', 'a@b'),
            ),
            # Iter 13 regression cases (Findings 1, 2, 3)
            (
                'redis://user:1234|http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234', 'example.com'),
            ),
            (
                'redis://user:5678|redis+sentinel://u2:sec2@h2:26379/m@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('5678', 'sec2'),
            ),
            (
                'Error(redis://user:1234|redis://u2:s2@h2:6379/0@127.0.0.1:6379/0)',
                'Error(redis://user:***@127.0.0.1:6379/0)',
                ('1234', 's2'),
            ),
            (
                'redis://user:1234&http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234=http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234,http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234:http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234`http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234/ab|http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://user:1234?a=1|http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234',),
            ),
            (
                'redis://127.0.0.1:6379|redis://u:sec@h:6379/0',
                'redis://127.0.0.1:6379|redis://u:***@h:6379/0',
                ('sec',),
            ),
            (
                'redis://user:1234?a=1#frag@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
                ('1234', 'frag'),
            ),
            (
                '{"u":"redis://user:1234/ab?c=1#frag@127.0.0.1:6379/0"}',
                '{"u":"redis://user:***@127.0.0.1:6379/0"}',
                ('1234', 'frag'),
            ),
            (
                'redis+sentinel://user:1234?a=1#frag@127.0.0.1:26379/mymaster',
                'redis+sentinel://user:***@127.0.0.1:26379/mymaster',
                ('1234', 'frag'),
            ),
            (
                '(redis://user:1234?a=1#b@localhost/0) next',
                '(redis://user:***@localhost/0) next',
                ('1234',),
            ),
            (
                'redis://host:6379?a=1#frag@b',
                'redis://host:6379?a=1#frag@b',
                (),
            ),
            (
                'redis://host:6379/0?a=1#frag@b',
                'redis://host:6379/0?a=1#frag@b',
                (),
            ),
            (
                'url=redis+sentinel://node1:26379,node2?user=a@b,reason=down',
                'url=redis+sentinel://node1:26379,node2?user=a@b,reason=down',
                (),
            ),
            (
                'redis+sentinel://node1:26379,node2#frag@b',
                'redis+sentinel://node1:26379,node2#frag@b',
                (),
            ),
        ],
    )
    def test_redact_redis_text_wrapped_and_punctuated_urls(
        self,
        raw_text: str,
        expected: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        redacted = redact_redis_text(raw_text)
        assert redacted == expected
        assert session_service._redact_redis_text(raw_text) == expected
        for fragment in secret_fragments:
            assert fragment not in redacted

    @pytest.mark.parametrize(
        ('redis_url', 'secret_fragments'),
        [
            ('redis://default:p@ss/w?o#rd@127.0.0.1:1/0', ('p@ss', 'w?o#rd')),
            ('redis://:@/?#sec@127.0.0.1:1/0', ('/?#sec',)),
            ('redis://user:1234?a=1@127.0.0.1:1/0', ('1234?a=1',)),
            ('redis://user:1234/ab?c@127.0.0.1:1/0', ('1234/ab?c',)),
            ('redis://user:1234#frag@redis.example.com/0', ('1234#frag',)),
            ('redis://user:p@1.2/x@127.0.0.1:1/0', ('1.2/x',)),
            ('redis://user:p@ss?a=1@127.0.0.1:1/0', ('ss?a=1',)),
            ('redis://user:p@w/x@localhost', ('p@w', 'w/x')),
            ('redis://user:p@ss/w?o#rd@redis.example.com', ('p@ss', 'w?o#rd')),
            ('redis://user:p@ss:word/x@localhost', ('p@ss', 'ss:word/x')),
            ('redis://user:p@ss:1234/x@localhost', ('p@ss', 'ss:1234/x')),
            ('redis://user:p@[foo/x@localhost', ('p@[foo', '[foo/x')),
            ('redis://:p@ss:word/x@localhost', ('p@ss', 'ss:word/x')),
            ('redis://user:p@ss:word?w=1@localhost', ('p@ss', 'ss:word?w=1')),
            ('redis://user:p@ss:word#frag@localhost', ('p@ss', 'ss:word#frag')),
            (
                'redis://user:pw_secret1@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=s2',
                ('pw_secret1', 's2'),
            ),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b;pw_secret',
                ('sec1', 'sec2', 'pw_secret'),
            ),
            (
                'redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0',
                ('s2',),
            ),
            (
                'redis://127.0.0.1:6379/0?redis://u2:s2@h2:6379/0=1',
                ('s2',),
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
            # Iter 12 live session.create_session() DEBUG log cases
            ('redis://user:1234!http://example.com@127.0.0.1:6379/0', ('1234', 'example.com')),
            ('redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0', ('1234', 's2')),
            ('redis://pw_part:6379!http://a@127.0.0.1:6379/0', ('6379!',)),
            ('redis://[2001:db8::1]!http://a@127.0.0.1:6379/0', ('2001:db8::1',)),
            ('redis+sentinel://u:sec1@node1,node2:26379/mymaster?password=sec2', ('sec1', 'sec2')),
            ('redis+sentinel://u:sec1@node1:26379,node2,node3:26379/mymaster?password=sec2', ('sec1', 'sec2')),
            ('redis+unix://u:sec1@/tmp/@dir/redis.sock?password=sec2@/var/run,pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('redis+sentinel://u:sec1@localhost/my@master?password=sec2@b;pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('redis+sentinel://u:sec1@127.0.0.1/my@master?password=sec2@b;pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('redis://127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1', ('secret_host', 'secret_path')),
            ('redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0&password=s1', ('s2', 's1')),
            # Iter 13 live session.create_session() DEBUG log cases (Findings 1 & 2)
            ('redis://user:1234|http://example.com@127.0.0.1:6379/0', ('1234', 'example.com')),
            ('redis://user:5678|redis+sentinel://u2:sec2@h2:26379/m@127.0.0.1:6379/0', ('5678', 'sec2')),
            ('redis://user:1234?a=1#frag@127.0.0.1:6379/0', ('1234', 'frag')),
            ('redis://user:1234?a=1#b@localhost/0', ('1234',)),
        ],
    )
    def test_session_error_logging_redacts_compound_passwords(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        redis_url: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')

        class _DeadConn(redis.Connection):
            def connect(self) -> None:
                raise redis.ConnectionError(f"connect failed for '{redis_url}'.")

        monkeypatch.setattr(
            redis.ConnectionPool,
            'from_url',
            classmethod(lambda cls, _url, **_kw: redis.ConnectionPool(connection_class=_DeadConn)),
        )

        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            session_obj = create_session()
            assert session_obj.token

        assert '***' in caplog.text
        for fragment in secret_fragments:
            assert fragment not in caplog.text

    @pytest.mark.parametrize(
        ('error_message', 'secret_fragments'),
        [
            ("ConnectionError('redis://user:1234?a=1@127.0.0.1:6379')", ('1234?a=1',)),
            ('Error("redis://default:p@ss/w?o#rd@127.0.0.1:6379")', ('p@ss', 'w?o#rd')),
            ('Error(redis://user:p@ss?1#2@[::1]:6379)', ('p@ss', 'ss?1#2')),
            ('Error(redis://user:p@1.2/x@127.0.0.1:6379)', ('1.2/x',)),
            ('Failed on redis://user:p@ss/w?o#rd@127.0.0.1:6379.', ('p@ss', 'w?o#rd')),
            ('Failed on redis://user:p@ss/w?o#rd@localhost.', ('p@ss', 'w?o#rd')),
            ('Failed connecting to redis://user:1234?a=1@127.0.0.1:6379: Connection refused', ('1234?a=1', '1234')),
            ('Failed on redis://user:1234?a=1@[::1]: timeout', ('1234?a=1', '1234')),
            ('Failed on unix://user:p@w/x@/var/run/redis.sock: Permission denied', ('p@w', 'w/x')),
            ('See `redis://user:1234?a=1@127.0.0.1:6379` for details', ('1234?a=1', '1234')),
            ('["redis://u1:p1@h1:6379","redis://u2:p2@h2"]', ('p1', 'p2')),
            ('redis://u1:p1@h1:6379,redis://u2:p2@h2:6379', ('p1', 'p2')),
            ('**redis://user:1234?a=1@127.0.0.1:6379**', ('1234?a=1', '1234')),
            ('redis://[user]:pass/x@localhost', ('pass/x',)),
            ('primary=redis+unix://u1:p@ss/w@/tmp/a.sock,replica=redis+unix://u2:p@ss/w@/tmp/b.sock', ('p@ss', 'ss/w')),
            ('unix:///var/run/redis.sock:redis://u2:secret2@h2:6379', ('secret2',)),
            ('redis://[::1]:6379&url[0]=redis://u2:secret2@h2:6379', ('secret2',)),
            ('redis://[::1]:6379?user=a@b=1&url[0]=redis://u2:secret2@h2:6379', ('secret2',)),
            ('{"url":"redis://user:1234?a=1@127.0.0.1:6379","next":"ok"}', ('1234?a=1', '1234')),
            ('<a href="redis://user:p@ss/w@host:6379">link</a>', ('p@ss', 'ss/w')),
            ('redis://[user]:1234?a=1@127.0.0.1:6379', ('1234?a=1', '1234')),
            ('redis://user:p@127.0.0.1:6379,pw2@realhost:6379', ('pw2',)),
            ('unix://user:p@/w>secret@/tmp/redis.sock', ('secret',)),
            ('redis://127.0.0.1:6379/0?target=a@host:6379,b&password=s1', ('s1',)),
            ('url=redis://user:pw@localhost,reason=timeout,next=redis://h2:6379/0?password=secret2', ('pw', 'secret2')),
            ('url=redis://user:1234/ab?c@localhost,auth_ok=1&reason=timeout', ('1234/ab?c',)),
            ('redis://127.0.0.1:6379/0?user=a@host:8080/p,b&token=s1_secret', ('s1_secret',)),
            ('redis://user:p">secret@127.0.0.1:6379/0', ('secret',)),
            ('redis://user:p@fake,k=pw_secret@127.0.0.1:6379/0', ('pw_secret',)),
            ('redis://user:pw@localhost?target=a@host:6379,b&password=s1', ('pw', 's1')),
            ('redis://127.0.0.1:6379/0?password=a@b,secret&other=1', ('secret',)),
            ('url=redis://user:pw@localhost?user=a@b&foo=1,next=redis://h2:6379/0?password=secret2', ('pw', 'secret2')),
            ('["redis://127.0.0.1:6379/0?user=a@b&password=s1",123,"a@b.com/1"]', ('s1',)),
            (
                'https://app.example.com/auth?redirect=https://client.example.com/cb&token=SECRET123',
                ('SECRET123',),
            ),
            (
                'redis://user:pw@127.0.0.1:6379/0?q=1!redis://u2:s2@h2:6379/0',
                ('pw', 's2'),
            ),
            ('redis://127.0.0.1:6379?other=1&password=a@b,pw_secret', ('pw_secret',)),
            ('redis+sentinel://127.0.0.1:26379/mymaster?password=a@b,pw_secret', ('pw_secret',)),
            ('<a href="redis://user:pw@localhost">a@b.com</a>', ('pw',)),
            ('redis://secretuser,k=pw@127.0.0.1:6379/0', ('secretuser', 'pw')),
            ('redis://secretuser"},pw@127.0.0.1:6379/0', ('secretuser', 'pw')),
            ('redis://123://x?q=1!redis://h2:6379/0?password=s2', ('s2',)),
            ('redis://127.0.0.1:6379?q=1#frag!redis://u2:s2@h2:6379/0', ('s2',)),
            ('redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=sec2@b,pw_secret', ('sec1', 'sec2', 'pw_secret')),
            ('GET /auth?redirect="https://client.example.com/cb"&token=SECRET123', ('SECRET123',)),
            ('redis://user,k=1:pw@127.0.0.1/0', ('pw',)),
            ('redis://user,k=1@localhost', ('user,k=1',)),
            # Iter 12 wrapped/text live caplog cases
            ('{"u":"redis+sentinel://node1:26379,node2/mymaster?password=sec2","email":"a@b.com"}', ('sec2',)),
            ('{"u":"redis+unix://u:sec1@/tmp@/dir/redis.sock?password=sec2\\"a","email":"x@/y"}', ('sec1', 'sec2')),
            ('redis://127.0.0.1:6379/0?foo&!redis://u2:s2@h2:6379/0&password=s1', ('s2', 's1')),
            ('redis://127.0.0.1:6379/0?password=https://secret_host.internal/secret_path?a=1', ('secret_host', 'secret_path')),
            ('redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0', ('1234', 's2')),
        ],
    )
    def test_session_error_logging_redacts_wrapped_exception_messages(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        error_message: str,
        secret_fragments: tuple[str, ...],
    ) -> None:
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

        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            session_obj = create_session()
            assert session_obj.token

        assert '***' in caplog.text
        for fragment in secret_fragments:
            assert fragment not in caplog.text

    def test_redact_redis_text_scales_linearly_on_large_adversarial_inputs(self) -> None:
        cases = [
            ('redis://user:secret_pw@127.0.0.1:6379/0?' + ('a=@' * 50_000), 'secret_pw'),
            ('redis://user:1234/0?' + ('a=@' * 50_000), None),
            ('redis://user:secret_pw@127.0.0.1:6379/' + ('a@' * 75_000), 'secret_pw'),
            ('redis://user:secret_pw@host' + ('/a@b' * 40_000) + '@end', 'secret_pw'),
            ('redis://user:secret_pw@127.0.0.1:6379/' + ('a@b,' * 40_000), 'secret_pw'),
            ('redis://user:p@ss' + ('/a@b' * 40_000) + ',redis://u2:secret2@h2:6379', 'secret2'),
            ('redis://user:secret_pw@127.0.0.1:6379/0?' + ('a@h,' * 40_000) + '&password=s1', 'secret_pw'),
            ('unix://user:p' + ('@/a>' * 40_000) + '@/tmp/redis.sock', 'user:p'),
            (('url=redis://user:pw@localhost,reason=timeout,next=' * 3_500) + 'redis://u2:s2@h2:6379', 's2'),
            ('redis://u:p@' + ('a?' * 40_000) + ',redis://u2:s2@h2:6379', 's2'),
            ('A' * 160_000, None),
            (('1' * 160_000) + '://x', None),
            ('redis://[' + ('a' * 160_000) + ',redis://u2:s2@h2:6379', 's2'),
            ('!'.join(f'redis://u:p{i}@localhost">x{i}' for i in range(5_000)), 'p4999'),
            ('!'.join(f'redis://u:p{i}@localhost">x{i}' for i in range(5_000)) + ('.' * 160_000), 'p4999'),
            (','.join(f'redis://[b{i}">x' for i in range(2_500)), None),
            (','.join(f'redis://[b{i}">x?a=@' for i in range(2_500)), None),
            (','.join(f'redis://[b{i}">x' for i in range(2_500)) + ',redis://u:p@127.0.0.1:6379/0', 'u:p'),
            ('redis://u:p@barehost?' + ('@h:1,k=1&' * 17_775) + 'password=s1', 's1'),
            ('!'.join(f'redis://h{i}">x' for i in range(10_000)) + '@end', None),
            ('!'.join(f'redis://u:p{i}@h">a@b' for i in range(5_000)), 'p4999'),
            ('redis://[' + ("'k=1,redis://[" * (160_000 // 14)), None),
            ('redis://user:pw' + ("'k=1,redis://user:pw" * (160_000 // 20)), None),
            ('!'.join(f'redis://[b{i}">x' for i in range(5_000)) + ',redis://u:p@127.0.0.1:6379/0', 'u:p'),
            ('redis://secretuser,k=' + ('a@b,' * 40_000), None),
            ('redis://host' + (':' * 80_000) + ('>:' * 20_000), None),
            ('redis://host' + ('a>' * 80_000), None),
            ('redis://h' + (':' * 40_000) + '>:redis://h2' + (':' * 40_000) + '>:', None),
            ('!'.join(f'redis://u:p{i}@h{i}?x=1' for i in range(5_000)), 'p4999'),
            # Iter 10 160k O(N) stress assertions (Explorers 1, 2, 3)
            ('redis://h:6379?' + ('",{' * 53_330) + '@end', None),
            ('redis://h:6379?' + ('",[' * 53_330) + '@end', None),
            ('redis://127.0.0.1:6379?' + ('@' * 160_000), None),
            ('redis://127.0.0.1:6379?' + ('a@' * 80_000), None),
            ('redis://127.0.0.1:6379?' + ('a%20' * 10_000) + '=' + ('@' * 40_000), None),
            ('redis://127.0.0.1:6379?' + ('?@' * 80_000), None),
            ('redis://u:p@barehost?@=' + (',redis://h' * 16_000), 'u:p@'),
            ('redis://u:p@' + ('a' * 80_000) + '?@=' + (',redis://h' * 8_000), 'u:p@'),
            ('redis://u:p@' + ('a' * 80_000) + ('?:,redis://h' * 6_666), 'u:p@'),
            (('redis://h",{!' * 12_300) + '}]@end', None),
            (('redis://h:6379/app",{!' * 7_200) + '}]@end', None),
            (('redis://h:6379",{!' * 8_800) + '}]@end', None),
            (
                'redis+sentinel://u:sec1@127.0.0.1:26379/mymaster?password=' + ('a@' * 80_000) + ',pw_secret',
                'pw_secret',
            ),
            ('https://app.example.com/auth?' + ('r="x"&' * 26_600) + 'token=SECRET123', 'SECRET123'),
            (
                'GET /auth?redirect="https://client.example.com/cb"' + ('&a=1' * 40_000) + '&token=SECRET123',
                'SECRET123',
            ),
            ('redis://secretuser,' + ('k=1:pw,m=2,' * 14_500) + 'email=a@b.com/1', None),
            ('redis://secretuser,k=' + ('a@b,' * 39_900) + 'k2=1@localhost', 'secretuser'),
            # Iter 11 160k O(N) stress assertions
            (r'{"url":"https://a.com/auth?q=' + (r'\"' * 80_000) + '&token=SECRET123"}', 'SECRET123'),
            ('GET /auth?redirect="https://client.example.com/cb' + ('"' * 80_000) + 'z&token=SECRET123', None),
            ('GET /auth?redirect="https://client.example.com/cb' + ('"' * 80_000) + '&token=SECRET123', 'SECRET123'),
            ('redis+sentinel://u:p@' + ('1.1.1.1:1,' * 16_000) + '2.2.2.2:2/mymaster?password=s1', 's1'),
            ('redis://u:p@127.0.0.1:6379/' + ('1.1.1.1:80/@' * 13_300) + 'end', 'u:p@'),
            ('redis+unix://u:p' + ('@/a' * 53_000) + '?q=1', 'u:p'),
            ('redis://u,k=1:pw@' + ('@' * 80_000) + '!' + ('a' * 80_000) + '://h2', None),
            # Iter 12 160k+ O(N) stress assertions (Explorers 1, 2, 3)
            ('redis://h:6379?password=u@' + ('1.1.1.1:80/@' * 13_300) + '?password=s1,x', 's1'),
            ('{"u":"redis://127.0.0.1:6379?password=' + ('@x:80/a' * 22_800) + '?password=pw_s2","k":1}', 'pw_s2'),
            ('{"u":"redis://127.0.0.1:6379?k=1' + ('@x:80/a' * 22_800) + '?password=2","k":1}', None),
            ('{"u":"redis://127.0.0.1:6379?k=1#frag' + ('@x:80/a' * 22_800) + '?password=2","k":1}', None),
            ('{"u":"redis://127.0.0.1:6379?password=' + ('@localhost/a' * 13_300) + '?password=pw_s2","k":1}', 'pw_s2'),
            ('{"u":"redis://h:6379?password=u' + ('@x:80/a@b?k=1&' * 11_400) + '","k":1}', 'u@x:80'),
            (
                '{"u":"redis://h:6379?password=u'
                + ('@x:80/a' * 11_400)
                + '@'
                + ('a' * 80_000)
                + '://h?password=2","k":1}',
                'u@x:80',
            ),
            ('{"u":"redis://h:6379?password=u' + ('@1.1.1.1:80/a@b?k=1&m=2' * 7_000) + '","k":1}', 'u@1.1.1.1'),
            ('{"u":"redis://h:6379?password=u' + ('@localhost/a@b?k=1&m=2' * 7_300) + '","k":1}', 'u@localhost'),
            ('unix://u:p' + ('@/(' * 53_000) + ')@/tmp/s', 'u:p'),
            ('redis+sentinel://u:p@' + ('node,' * 32_000) + 'realhost:26379/mymaster?password=s2', 's2'),
            ('redis://127.0.0.1:6379/0?password=https://secret.internal/p?' + ('a=1&' * 40_000) + 'other=1', 'secret.internal'),
            ('redis://127.0.0.1:6379/0?foo' + ('&!redis://u2:s2@h2:6379/0' * 6_000) + '&password=s1', 's1'),
            # Iter 13 160k+ O(N) stress assertions (Findings 1, 2, 3)
            ('redis://user:1234|' + ('http://a|' * 20_000) + '@127.0.0.1:6379/0', '1234'),
            ('redis://127.0.0.1:6379|' * 7_500, None),
            ('redis://user:1234?a=1#frag' + ('@x:80/a' * 25_000) + '@127.0.0.1:6379/0', '1234'),
            ('redis+sentinel://' + ('node,' * 35_000) + 'node2:26379?user=a@b', None),
            # Iter 14 160k+ O(N) stress assertions (Explorers 1, 2, 3)
            (
                'redis://user:5678'
                + ('|redis://u2:sec2@h2:6379/0' * 5_500)
                + '|http://example.com@127.0.0.1:6379/0',
                '5678',
            ),
            ('redis://user:1234/0' + ('<http://a@b' * 15_000) + '@127.0.0.1:6379/0', '1234'),
            ('redis://user:1234?password=sec1#frag' + ('@x:80/a' * 25_000) + '@127.0.0.1:6379/0', '1234'),
            ('redis://user:1234/0?c=1#frag' + ('@x:80/a' * 25_000) + '@127.0.0.1:6379/0', '1234'),
            ('redis+sentinel://' + ('node,' * 30_000) + 'node2:26379/mymaster?k=1#frag' + ('@x:80/a' * 2_000), None),
            # Iter 16 160k O(N) & ReDoS regression assertions (Explorers 1, 2, 3)
            ('redis://u:sec0' + ('|http://h:80/a' * (160_000 // 14)), None),
            ('redis://u:sec0' + ('|redis://h:6379/0' * (160_000 // 17)), None),
            ('http://h:80/a' + ('!http://h:80/a' * (160_000 // 14)), None),
            ('redis://u:sec0' + ('~redis://h:6379/0' * (160_000 // 17)), None),
            ('redis://user:1234' + ('^redis+unix:///tmp/a.sock' * (160_000 // 25)), None),
            ('redis://127.0.0.1:6379/0' + ('*redis+sentinel://s1:26379/mymaster' * (160_000 // 35)), None),
            ('redis://[' + (':' * 160_000) + '/0@h:6379/0', ':::'),
            ('redis://u@[' + (':' * 160_000) + '?a=1|http://h', 'redis://u@['),
        ]
        for payload, secret in cases:
            start = time.perf_counter()
            redacted = redact_redis_text(payload)
            elapsed = time.perf_counter() - start
            assert elapsed < 0.5, f'Expected O(N) runtime on len={len(payload)}, took {elapsed:.3f}s'
            if secret is not None:
                assert secret not in redacted
                assert '***' in redacted

        for url_payload in (
            'redis://127.0.0.1:6379?password=' + ('@b:8080/0?' * 15_000),
            'redis://127.0.0.1:6379?' + ('@' * 160_000),
            'redis://127.0.0.1:6379?' + ('a@' * 80_000),
            'redis://127.0.0.1:6379?' + ('a%20' * 10_000) + '=' + ('@' * 40_000),
            'redis://user:5678' + ('|redis://u2:sec2@h2:6379/0' * 5_500) + '|http://example.com@127.0.0.1:6379/0',
            'redis://user:1234?password=sec1#frag' + ('@x:80/a' * 25_000) + '@127.0.0.1:6379/0',
            'redis://user:1234/0?c=1#frag' + ('@x:80/a' * 25_000) + '@127.0.0.1:6379/0',
        ):
            start = time.perf_counter()
            redacted_url = redact_redis_url(url_payload)
            elapsed = time.perf_counter() - start
            assert elapsed < 0.5, f'Expected O(N) redact_redis_url runtime, took {elapsed:.3f}s'
            assert redacted_url.startswith(('redis://127.0.0.1:6379?', 'redis://user:***@'))

        # Iter 16 _HOST_PART_RE 160k ReDoS regressions across all 4 entry points
        for colon_payload, expected_prefix in (
            ('redis://[' + (':' * 160_000) + '/0@h:6379/0', 'redis://***@h:6379/0'),
            ('redis://u@[' + (':' * 160_000) + '?a=1|http://h', 'redis://***@['),
        ):
            for entry_fn in (
                redact_redis_text,
                redact_redis_url,
                lambda p: sanitize_redis_client_error(p, p),
                lambda p: _sanitize_rate_limiter_error_message(p, p, p),
            ):
                t0 = time.perf_counter()
                out = entry_fn(colon_payload)
                elapsed = time.perf_counter() - t0
                assert elapsed < 0.5, f'Expected O(N) runtime on _HOST_PART_RE payload, took {elapsed:.3f}s'
                assert out.startswith(expected_prefix)

        # Iter 14 O(N) probes for sanitize_redis_client_error
        rep_port_colons_url = 'redis://user:p1@h:' + ('a:' * 40_000) + 'secret_tail?a=1@127.0.0.1:6379/0'
        t0 = time.perf_counter()
        sanitized_rep = sanitize_redis_client_error(
            "Port could not be cast to integer value as 'secret_tail'",
            rep_port_colons_url,
        )
        assert (time.perf_counter() - t0) < 0.5
        assert 'secret_tail' not in sanitized_rep

        many_colon_subs_url = 'redis://user:' + ':'.join(f'tok{i}' for i in range(20_000)) + '@127.0.0.1:6379/0'
        many_colon_msg = 'Error -2 connecting to ' + ('x:' * 80_000) + 'tok19999:6379'
        t0 = time.perf_counter()
        sanitized_subs = sanitize_redis_client_error(many_colon_msg, many_colon_subs_url)
        assert (time.perf_counter() - t0) < 0.5
        assert 'tok19999' not in sanitized_subs

        many_port_quotes_msg = (
            'Port could not be cast to integer value as ' * 3_500
        ) + "Port could not be cast to integer value as 'secret_tail'"
        t0 = time.perf_counter()
        sanitized_quotes = sanitize_redis_client_error(
            many_port_quotes_msg,
            'redis://user:p1@h:secret_tail?a=1@127.0.0.1:6379/0',
        )
        assert (time.perf_counter() - t0) < 0.5
        assert 'secret_tail' not in sanitized_quotes

        old_lim = sys.getrecursionlimit()
        sys.setrecursionlimit(150)
        try:
            assert 's2' not in redact_redis_text(
                '!'.join(f'redis://h{i}?q=1' for i in range(5_000)) + '!redis://h2:6379/0?password=s2'
            )
            assert 's2' not in redact_redis_url(
                'redis://user:pw_secret1@127.0.0.1:6379/0?q=1!redis://h2:6379/0?password=s2'
            )
            assert '1234' not in redact_redis_url(
                'redis://user:1234!redis://u2:s2@h2:6379/0@127.0.0.1:6379/0'
            )
            assert 's1' not in redact_redis_text(
                'redis://127.0.0.1:6379/0?foo' + ('&!redis://h2:6379/0' * 5_000) + '&password=s1'
            )
            assert '1234' not in redact_redis_text(
                'redis://user:1234|' + ('http://a|' * 5_000) + '@127.0.0.1:6379/0'
            )
            chain_5k = (
                'redis://user:5678'
                + ('|redis://u2:sec2@h2:6379/0' * 5_000)
                + '|http://example.com@127.0.0.1:6379/0'
            )
            assert '5678' not in redact_redis_text(chain_5k)
            assert '5678' not in redact_redis_url(chain_5k)
            assert '1234' not in redact_redis_text(
                'redis://user:1234/0' + ('<http://a@b' * 5_000) + '@127.0.0.1:6379/0'
            )
            assert '1234' not in redact_redis_url(
                'redis://user:1234?password=sec1#frag' + ('@x:80/a' * 5_000) + '@127.0.0.1:6379/0'
            )
            for iter16_payload in (
                'redis://u:sec0' + ('|http://h:80/a' * (160_000 // 14)),
                'redis://u:sec0' + ('|redis://h:6379/0' * (160_000 // 17)),
                'http://h:80/a' + ('!http://h:80/a' * (160_000 // 14)),
                'redis://u:sec0' + ('~redis://h:6379/0' * (160_000 // 17)),
                'redis://user:1234' + ('^redis+unix:///tmp/a.sock' * (160_000 // 25)),
                'redis://127.0.0.1:6379/0' + ('*redis+sentinel://s1:26379/mymaster' * (160_000 // 35)),
            ):
                t0 = time.perf_counter()
                assert redact_redis_text(iter16_payload) == iter16_payload
                assert (time.perf_counter() - t0) < 0.5
        finally:
            sys.setrecursionlimit(old_lim)

    @pytest.mark.parametrize(
        ('redis_url', 'secret_prefix'),
        [
            ('redis://user:mysecret?a=1@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:mysecret#frag@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:mysecret/path@127.0.0.1:6379/0', 'mysecret'),
            ('redis://user:pw?a=1@127.0.0.1:6379/0', 'pw'),
            ('redis://user:5678!redis://u2:sec2@h2:6379/0@127.0.0.1:6379/0', '5678'),
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
    def test_session_create_session_redacts_urlparse_port_value_error_in_caplog(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        redis_url: str,
        secret_prefix: str,
    ) -> None:
        monkeypatch.setenv('REDIS_URL', redis_url)
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'local')
        session_service.reset_redis_pool()

        with pytest.raises(ValueError) as pool_exc:
            session_service.get_redis_pool(redis_url)
        assert secret_prefix not in str(pool_exc.value)
        assert '***' in str(pool_exc.value)
        assert pool_exc.value.__cause__ is None

        with caplog.at_level(logging.DEBUG, logger='web.backend.services.session'):
            session_obj = create_session()
            assert session_obj.token

        assert '***' in caplog.text
        assert secret_prefix not in caplog.text

    @pytest.mark.parametrize(
        ('raw_url', 'expected'),
        [
            ('redis://user:1234|http://example.com?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234|redis://example.com?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234!http://example.com?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234!redis://example.com?a=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            (
                'redis://user:5678|redis://h2:6379/0?a=1|http://example.com@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            ('redis://user:5678|redis://h2:6379@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:5678:redis://h2:6379@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:5678`redis://h2:6379@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:5678|rediss://example.com:6379@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            (
                'redis://user:5678|redis+sentinel://example.com:26379@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            ('redis://user:5678|redis+unix:///tmp/a.sock@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234|redis://example.com?b=2@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234?a=1|redis://example.com?b=2@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0<http://example.com@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0<redis://u2:s2@h2:6379/0@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0"foo@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234?password=sec1#frag@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0?c=1#frag@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0?c=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://user:1234/0?password=sec1#frag@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis+unix://user:1234?password=sec1#frag@/tmp/redis.sock', 'redis+unix://user:***@/tmp/redis.sock'),
            ('redis://host:6379?a=1#frag@b', 'redis://host:6379?a=1#frag@b'),
            ('redis://host:6379/0?a=1#frag@b', 'redis://host:6379/0?a=1#frag@b'),
            ('redis://127.0.0.1:6379?k=1#frag@x:80/a', 'redis://127.0.0.1:6379?k=1#frag@x:80/a'),
            ('redis://127.0.0.1:6379/0?password=s1#frag@b', 'redis://127.0.0.1:6379/0?password=***#frag@b'),
            (
                'redis+sentinel://node1,node2:26379/mymaster?other=1#frag@b',
                'redis+sentinel://node1,node2:26379/mymaster?other=1#frag@b',
            ),
            (
                'redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
                'redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
            ),
            (
                'redis+sentinel://node1:26379,node2/mymaster?other=1#frag@b',
                'redis+sentinel://node1:26379,node2/mymaster?other=1#frag@b',
            ),
            (
                'redis+sentinel://node1:26379/mymaster?other=1#frag@b',
                'redis+sentinel://node1:26379/mymaster?other=1#frag@b',
            ),
            ('redis+sentinel://[::1]:26379/mymaster#frag@b', 'redis+sentinel://[::1]:26379/mymaster#frag@b'),
            (
                'redis+sentinel://u:sec1@s1:26379,s2:26379/mymaster#frag@b',
                'redis+sentinel://u:***@s1:26379,s2:26379/mymaster#frag@b',
            ),
            (
                'redis+sentinel://127.0.0.1:26379/mymaster?k=1#frag@x:80/a',
                'redis+sentinel://127.0.0.1:26379/mymaster?k=1#frag@x:80/a',
            ),
            ('redis+sentinel://user:1234,node2?k=1@localhost', 'redis+sentinel://user:***@localhost'),
            ('redis://user:1234,pw?k=1@localhost', 'redis://user:***@localhost'),
            ('redis://user:1234,pw?k=1@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
        ],
    )
    def test_redact_redis_url_and_text_r14_regressions(
        self,
        raw_url: str,
        expected: str,
    ) -> None:
        assert redact_redis_url(raw_url) == expected
        assert redact_redis_text(raw_url) == expected
        assert redact_redis_text(f"ConnectionError('{raw_url}')") == f"ConnectionError('{expected}')"

    def test_redact_redis_r14_chained_multi_urls_and_client_error_sanitization(self) -> None:
        chain = 'redis://user:5678|redis://u2:sec2@h2:6379/0|http://example.com@127.0.0.1:6379/0'
        for redacted_chain in (redact_redis_url(chain), redact_redis_text(chain)):
            assert '5678' not in redacted_chain
            assert 'sec2' not in redacted_chain
            assert 'example.com' not in redacted_chain
            assert '***' in redacted_chain

        assert (
            redact_redis_text('redis://127.0.0.1:6379|redis://u:sec@h:6379/0')
            == 'redis://127.0.0.1:6379|redis://u:***@h:6379/0'
        )
        assert (
            redact_redis_text('redis://[::1]:6379:redis://u2:secret2@h2:6379')
            == 'redis://[::1]:6379:redis://u2:***@h2:6379'
        )
        assert (
            redact_redis_text('redis://127.0.0.1:6379/0|http://user2:pw2@example.com:8080/0')
            == 'redis://127.0.0.1:6379/0|http://user2:***@example.com:8080/0'
        )
        assert (
            redact_redis_text('redis://user,k=1@127.0.0.1/0!redis://u2:s2@h2:6379/0')
            == 'redis://***@127.0.0.1/0!redis://u2:***@h2:6379/0'
        )

        # ConnectionError mis-parsed @host leaks (both >=4 and <4 chars) and benign preservation
        for err_msg, url, forbidden in (
            (
                'Error -2 connecting to secret_host:6379. Name or service not known.',
                'redis://user:p1@secret_host:6379?a=1@127.0.0.1:6379/0',
                'secret_host',
            ),
            (
                'Error -2 connecting to s3c:6379. Name or service not known.',
                'redis://user:p1@s3c?a=1@127.0.0.1:6379/0',
                's3c',
            ),
        ):
            sanitized = sanitize_redis_client_error(err_msg, url)
            assert forbidden not in sanitized
            assert sanitized.startswith('Invalid Redis configuration for ')

        for raw_msg, url in (
            ("Port could not be cast to integer value as 'badport'", 'redis://127.0.0.1:badport/0'),
            ("Port could not be cast to integer value as 'notaport'", 'redis://user:pw@127.0.0.1:notaport/0'),
            ("Port could not be cast to integer value as 'badport'", 'redis://user:p1@h:b@127.0.0.1:badport/0'),
        ):
            assert sanitize_redis_client_error(raw_msg, url) == raw_msg

    @pytest.mark.parametrize(
        ('raw_url', 'expected'),
        [
            ('redis://user:1234|redis://u:p@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            (
                'redis://user:123|redis+sentinel://u:p@127.0.0.1:26379/mymaster',
                'redis://user:***@127.0.0.1:26379/mymaster',
            ),
            (
                'redis://user:1234!redis+sentinel://u:p@127.0.0.1:26379/mymaster',
                'redis://user:***@127.0.0.1:26379/mymaster',
            ),
            ('redis://user:1234!redis+unix://u:p@/tmp/r.sock', 'redis://user:***@/tmp/r.sock'),
            ('redis://user:1234|redis+unix://u:p@/tmp/r.sock', 'redis://user:***@/tmp/r.sock'),
            (
                'redis://user:1234|redis://host1/0?k=1@127.0.0.1:6379/0|redis://host2/0',
                'redis://user:***@127.0.0.1:6379/0|redis://host2/0',
            ),
            (
                'redis://user:1234|redis://host1/0?k=1@127.0.0.1:6379/0!redis://host2/0',
                'redis://user:***@127.0.0.1:6379/0!redis://host2/0',
            ),
            (
                'redis://user:1234|redis://host1/0?password=sec1!redis://host2/0@127.0.0.1:6379/0',
                'redis://user:***@127.0.0.1:6379/0',
            ),
            (
                'redis://user:1234|http://example.com?password=sec1@127.0.0.1:1/0',
                'redis://user:***@127.0.0.1:1/0',
            ),
            (
                'redis://user:1234!redis://example.com?password=sec1@127.0.0.1:1/0',
                'redis://user:***@127.0.0.1:1/0',
            ),
            ('redis://user:1234/0?c=1#frag@localhost', 'redis://user:***@localhost'),
            ('redis://user:1234/0?c=1#frag@127.0.0.1', 'redis://user:***@127.0.0.1'),
            ('redis://user:1234/0#frag@127.0.0.1', 'redis://user:***@127.0.0.1'),
            (
                'redis+sentinel://user:1234/mymaster?k=1#frag@localhost',
                'redis+sentinel://user:***@localhost',
            ),
            (
                'redis+sentinel://user:1234/mymaster?k=1#frag@127.0.0.1',
                'redis+sentinel://user:***@127.0.0.1',
            ),
            (
                'redis://127.0.0.1:6379/0|http://example.com?password=sec1@127.0.0.1:6379/0',
                'redis://127.0.0.1:6379/0|http://example.com?password=***',
            ),
            ('redis://host:6379|http://example.com?password=sec1@b', 'redis://host:6379|http://example.com?password=***'),
            (
                'redis://127.0.0.1:6379/0|redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
                'redis://127.0.0.1:6379/0|redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
            ),
            (
                'redis://127.0.0.1:6379/0|redis+sentinel://u:p@127.0.0.1:26379/mymaster',
                'redis://127.0.0.1:6379/0|redis+sentinel://u:***@127.0.0.1:26379/mymaster',
            ),
            (
                'redis://127.0.0.1:6379/0|redis+unix://u:p@/tmp/r.sock',
                'redis://127.0.0.1:6379/0|redis+unix://u:***@/tmp/r.sock',
            ),
            (
                'redis://127.0.0.1:6379/0|redis://host1/0?k=1@127.0.0.1:6379/0!redis://host2/0',
                'redis://127.0.0.1:6379/0|redis://host1/0?k=1@127.0.0.1:6379/0!redis://host2/0',
            ),
            (
                'redis://127.0.0.1:6379/0?k=1#frag@localhost',
                'redis://127.0.0.1:6379/0?k=1#frag@localhost',
            ),
            ('redis://redis-primary:6379/0?k=1#frag@b', 'redis://redis-primary:6379/0?k=1#frag@b'),
            (
                'redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
                'redis+sentinel://s1:26379,s2:26379/mymaster?k=1#frag@b',
            ),
            (
                'redis+sentinel://s1:26379,s2:26379/mymaster#frag@b',
                'redis+sentinel://s1:26379,s2:26379/mymaster#frag@b',
            ),
            ('redis://host:6379/0?a=1#frag@b', 'redis://host:6379/0?a=1#frag@b'),
        ],
    )
    def test_redact_redis_url_and_text_r15_regressions(
        self,
        raw_url: str,
        expected: str,
    ) -> None:
        assert redact_redis_url(raw_url) == expected
        assert redact_redis_text(raw_url) == expected
        assert redact_redis_text(f"ConnectionError('{raw_url}')") == f"ConnectionError('{expected}')"

    def test_redact_redis_r15_3scheme_and_text_preservations(self) -> None:
        chain_3 = 'redis://user:5678|redis://u2:sec2@h2:6379/0|redis://u3:sec3@127.0.0.1:6379/0'
        assert redact_redis_url(chain_3) == 'redis://user:***@127.0.0.1:6379/0'
        assert redact_redis_text(chain_3) == 'redis://user:***@h2:6379/0|redis://u3:***@127.0.0.1:6379/0'
        assert (
            redact_redis_text('redis://127.0.0.1:6379|redis://u:sec@h:6379/0')
            == 'redis://127.0.0.1:6379|redis://u:***@h:6379/0'
        )
        assert (
            redact_redis_text('redis://[::1]:6379:redis://u2:secret2@h2:6379')
            == 'redis://[::1]:6379:redis://u2:***@h2:6379'
        )

    def test_sanitize_redis_client_error_r15_defense_in_depth_tab_hex_and_scaling(self) -> None:
        for url in (
            'redis://user:1234|http://example.com?password=sec1@127.0.0.1:1/0',
            'redis://user:1234!redis://example.com?password=sec1@127.0.0.1:1/0',
        ):
            sanitized = sanitize_redis_client_error('Error -2 connecting to example.com:80.', url)
            assert '1234' not in sanitized
            assert 'sec1' not in sanitized
            assert sanitized.startswith('Invalid Redis configuration for redis://')

        for raw_url, err_msg in (
            ('redis://user:p1@s3c\t/0@127.0.0.1:6379/0', 'Error -2 connecting to s3c:6379.'),
            ('redis://user:p1@s3\tc/0@127.0.0.1:6379/0', 'Error -2 connecting to s3c:6379.'),
            ('redis://user:p1@s3\rc/0@127.0.0.1:6379/0', 'Error -2 connecting to s3c:6379.'),
            ('redis://user:p1@s3\nc/0@127.0.0.1:6379/0', 'Error -2 connecting to s3c:6379.'),
        ):
            sanitized = sanitize_redis_client_error(err_msg, raw_url)
            assert 's3c' not in sanitized
            assert sanitized.startswith('Invalid Redis configuration for ')

        for raw_url, escaped_port in (
            ('redis://user:p1@h:sec\x0bret?a=1@127.0.0.1:6379/0', 'sec\\x0bret'),
            ('redis://user:p1@h:sec\x0cret?a=1@127.0.0.1:6379/0', 'sec\\x0cret'),
        ):
            raw_msg = f"Port could not be cast to integer value as '{escaped_port}'"
            sanitized = sanitize_redis_client_error(raw_msg, raw_url)
            assert 'sec' not in sanitized
            assert sanitized.startswith('Invalid Redis configuration for ')

        # Linear O(N) scaling probes and shallow-recursion guard
        n = 40_000
        many_at_url = 'redis://u:' + '@'.join(f'h{i}' for i in range(n // 8)) + '@127.0.0.1:6379/0'
        many_at_msg = 'Error connecting ' + ('x ' * (n // 2))
        aaaa_subs = ':'.join(f'aaaa{i:06d}bbbb' for i in range(n // 15))
        aaaa_url = f'redis://u:{aaaa_subs}@127.0.0.1:6379/0'
        aaaa_msg = 'Error ' + ('aaaa000000cccc ' * (n // 16))
        ports_msg = ' '.join(f"Port could not be cast to integer value as 'p{i}'" for i in range(n // 50))
        ports_url = 'redis://u:' + ':'.join(f'q{i}' for i in range(n // 50)) + '@127.0.0.1:6379/0'
        chain_5k = 'redis://u:sec0' + '|redis://u:sec@h:6379/0' * 200

        rl_cred_url = 'redis://u:sec0@127.0.0.1:6379/0'
        rl_loc_plain = 'location a ' * (160_000 // 11)
        rl_loc_at_prefix = 'x@y ' + ('location a ' * (160_000 // 11))
        rl_loc_multiline_at = ('location a\n' * (160_000 // 11)) + '@'

        prev_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(150)
        try:
            t0 = time.perf_counter()
            assert sanitize_redis_client_error(many_at_msg, many_at_url) == many_at_msg
            assert sanitize_redis_client_error(aaaa_msg, aaaa_url) == aaaa_msg
            assert sanitize_redis_client_error(ports_msg, ports_url) == ports_msg
            redacted_chain = redact_redis_text(chain_5k)
            assert 'sec0' not in redacted_chain
            assert time.perf_counter() - t0 < 1.5

            for rl_msg, expect_invalid in (
                (rl_loc_plain, False),
                (rl_loc_at_prefix, True),
                (rl_loc_multiline_at, True),
            ):
                t_rl = time.perf_counter()
                rl_out = _sanitize_rate_limiter_error_message(rl_msg, rl_cred_url, rl_cred_url)
                assert (time.perf_counter() - t_rl) < 0.5
                assert 'sec0' not in rl_out
                if expect_invalid:
                    assert rl_out.startswith('Invalid rate limiter storage configuration')
                else:
                    assert rl_out == rl_msg
        finally:
            sys.setrecursionlimit(prev_limit)



