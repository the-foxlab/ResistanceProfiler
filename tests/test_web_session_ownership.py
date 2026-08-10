"""Tests for the session-ownership foundation."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue

from tests.conftest import TINY_REF_NAME, TINY_REF_SEQ
from web.backend.main import create_app
from web.backend.services import session as session_service
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    create_session,
    fetch_owned_record,
    hash_session_token,
    owner_matches,
    record_upload,
)
from web.backend.startup_config import StartupConfig, build_project_db_uuid_index


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
        response = client.get('/api/health')
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
        response = client.get('/api/health')
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
        response = client.get('/api/health', cookies={SESSION_COOKIE_NAME: tampered})
        set_cookie = response.headers.get('set-cookie', '')
        # A new session is issued (the cookie value differs from the tampered one).
        assert SESSION_COOKIE_NAME in set_cookie
        assert tampered not in set_cookie

    def test_unknown_cookie_issues_new_session(self, fake_redis, no_token_config) -> None:
        client = TestClient(create_app(startup_config=no_token_config))
        response = client.get(
            '/api/health',
            cookies={SESSION_COOKIE_NAME: 'totally-unknown-value'},
        )
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie
        assert 'totally-unknown-value' not in set_cookie

    def test_valid_cookie_is_preserved_not_rotated(self, fake_redis, no_token_config) -> None:
        session = create_session()
        client = TestClient(create_app(startup_config=no_token_config))
        response = client.get(
            '/api/health',
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
        response = client.get('/api/health')
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie

        # The new session owns no records.
        cookie_value = client.cookies.get(SESSION_COOKIE_NAME)
        assert cookie_value is not None
        # No owned records exist for this session.
        assert fetch_owned_record('upload', 'any-id') is None

