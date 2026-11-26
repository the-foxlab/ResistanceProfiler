"""Tests for the session-ownership foundation."""

from __future__ import annotations

import logging
import shutil
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
    def test_claim_does_not_resurrect_missing_keys_and_heals_ttl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '120')
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '300')
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)

        # Missing keys return False and do not create a zombie key.
        assert claim_owned_record_field('job', 'missing-job', 'artifact_ids', 'a1') is False
        assert fake_client.exists('respro:job:missing-job') == 0

        # Create job & artifact (default TTL = result_ttl_seconds = 120) vs upload (session_ttl = 300).
        job_id = record_job(session_hash='owner-1', upload_ids=['u1'])
        art_id = record_artifact(session_hash='owner-1', canonical_path=Path('/tmp/r.html'), media_type='text/html')
        up_id = record_upload(session_hash='owner-1', canonical_path=Path('/tmp/u.fa'), file_type='fasta')
        assert 1 <= fake_client.ttl(f'respro:job:{job_id}') <= 120
        assert 1 <= fake_client.ttl(f'respro:artifact:{art_id}') <= 120
        assert 120 < fake_client.ttl(f'respro:upload:{up_id}') <= 300

        # If a key lost its TTL (TTL == -1), claim heals it to default TTL and succeeds only once.
        fake_client.persist(f'respro:job:{job_id}')
        assert fake_client.ttl(f'respro:job:{job_id}') == -1
        assert claim_owned_record_field('job', job_id, 'artifact_ids', art_id) is True
        assert 1 <= fake_client.ttl(f'respro:job:{job_id}') <= 120
        assert claim_owned_record_field('job', job_id, 'artifact_ids', 'other-id') is False
        rec = fetch_owned_record('job', job_id)
        assert rec is not None and rec.fields['artifact_ids'] == art_id

    def test_claim_invokes_lua_eval_when_supported(
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

        assert claim_owned_record_field('job', 'j-lua', 'artifact_ids', 'a1,a2') is True
        assert len(lua_calls) == 1
        assert 'HSETNX' in lua_calls[0][0]

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

        # Past TTL (6s), before sweep interval (60s): direct read/claim/delete must treat as expired.
        clock.now += 6.0
        assert fetch_owned_record('upload', up1) is None
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
            ('redis://user:p@ss/w?o#rd@127.0.0.1:6379/0', 'redis://user:***@127.0.0.1:6379/0'),
            ('redis://u:p@127.0.0.1:6379/0?password=s1&token=s2', 'redis://u:***@127.0.0.1:6379/0?password=***&token=***'),
            ('redis://127.0.0.1:6379/0?pass%77ord=s1&auth=s2', 'redis://127.0.0.1:6379/0?pass%77ord=***&auth=***'),
            ('unix:///tmp/a@b.sock', 'unix:///tmp/a@b.sock'),
            ('unix://user:pw@/tmp/redis.sock', 'unix://user:***@/tmp/redis.sock'),
            ('redis+unix://user:pw@/tmp/redis.sock', 'redis+unix://user:***@/tmp/redis.sock'),
            ('redis://user:pw@[::1]:6379/0', 'redis://user:***@[::1]:6379/0'),
            ('redis://localhost:6379/0?user=a@b', 'redis://localhost:6379/0?user=a@b'),
            ('redis://localhost:6379/0#a@b', 'redis://localhost:6379/0#a@b'),
            ('user:pw@127.0.0.1:6379/0', 'user:***@127.0.0.1:6379/0'),
            ('', ''),
        ],
    )
    def test_redact_redis_url_cases(self, raw_url: str, expected: str) -> None:
        assert redact_redis_url(raw_url) == expected

    @pytest.mark.parametrize(
        ('raw_text', 'expected'),
        [
            (
                "ConnectionError('redis://user:topsecret@127.0.0.1:6379/0')",
                "ConnectionError('redis://user:***@127.0.0.1:6379/0')",
            ),
            (
                'Failed connecting to rediss://:mysecret@redis.internal:6380/0: refused',
                'Failed connecting to rediss://:***@redis.internal:6380/0: refused',
            ),
            (
                'Failed on unix:///var/run/redis.sock: Permission denied',
                'Failed on unix:///var/run/redis.sock: Permission denied',
            ),
        ],
    )
    def test_redact_redis_text_embedded_urls(self, raw_text: str, expected: str) -> None:
        assert redact_redis_text(raw_text) == expected

    def test_sanitize_redis_client_error_catches_port_cast_and_userinfo_leaks(self) -> None:
        url = 'redis://user:p:topsecret@127.0.0.1:6379/0'
        raw_msg = "Port could not be cast to integer value as 'topsecret'"
        sanitized = sanitize_redis_client_error(raw_msg, url)
        assert 'topsecret' not in sanitized
        assert '***' in sanitized

        # Benign port error without secret leak is preserved.
        benign_url = 'redis://user:pw@127.0.0.1:notaport/0'
        benign_msg = "Port could not be cast to integer value as 'notaport'"
        assert sanitize_redis_client_error(benign_msg, benign_url) == benign_msg

    def test_redact_redis_text_scales_linearly(self) -> None:
        payload = 'redis://user:secret@127.0.0.1:6379/0 ' + ('a ' * 40_000)
        t0 = time.perf_counter()
        redacted = redact_redis_text(payload)
        assert time.perf_counter() - t0 < 0.5
        assert 'secret' not in redacted
        assert 'redis://user:***@127.0.0.1:6379/0' in redacted
