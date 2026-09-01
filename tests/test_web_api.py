"""API tests for the web backend."""

from __future__ import annotations

import importlib.metadata
import io
import json
import shutil
import sqlite3
import textwrap
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue
from rq.exceptions import NoSuchJobError

from tests.conftest import TINY_REF_NAME, TINY_REF_SEQ
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.main import _SAMPLE_QUOTA_COUNTER, _resolve_proxy_settings, create_app
from web.backend.queue import get_batch_queue, get_queue
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    record_job,
    reset_memory_stores,
)
from web.backend.startup_config import (
    ImprintConfig,
    StartupConfig,
    _resolve_imprint,
    _validate_startup_policy,
    build_project_db_uuid_index,
    load_startup_config,
)


def _upload_file(
    client: TestClient,
    file_path: Path,
    file_type: str,
    filename: str | None = None,
) -> str:
    """Upload a file and return its upload_id."""
    name = filename or file_path.name
    response = client.post(
        f'/api/upload/{file_type}',
        files={'file': (name, file_path.read_bytes(), 'application/octet-stream')},
    )
    assert response.status_code == 200, response.text
    return response.json()['upload_id']


def _upload_bytes(
    client: TestClient,
    data: bytes,
    file_type: str,
    filename: str,
) -> str:
    """Upload raw bytes and return its upload_id."""
    response = client.post(
        f'/api/upload/{file_type}',
        files={'file': (filename, data, 'application/octet-stream')},
    )
    assert response.status_code == 200, response.text
    return response.json()['upload_id']


def _poll_job(client: TestClient, job_id: str) -> dict:
    """Poll job status until succeeded/failed, return the full payload dict."""
    payload = None
    for _ in range(20):
        status = client.get(f'/api/jobs/{job_id}')
        assert status.status_code == 200, status.text
        payload = status.json()
        if payload['status'] in ('succeeded', 'failed'):
            break
    assert payload is not None
    return payload


def _download_artifact_json(
    client: TestClient,
    artifact_id: str,
) -> dict:
    """Download a .results.json artifact by ID and parse it."""
    response = client.get('/api/artifact', params={'artifact_id': artifact_id})
    assert response.status_code == 200, response.text
    return json.loads(response.content)


def _establish_session(client: TestClient) -> str:
    """Issue any request to establish the session cookie, return the session hash."""
    client.get('/api/ui/config')
    cookie = client.cookies.get(SESSION_COOKIE_NAME)
    return hash_session_token(cookie)


def _write_project_uuid(project_db: Path, project_uuid: str) -> None:
    """Assign a deterministic UUID in a copied project database for routing tests."""
    conn = sqlite3.connect(project_db)
    try:
        conn.execute('UPDATE project SET uuid = ? WHERE id = 1', (project_uuid,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def sync_queue():
    """An in-process RQ queue backed by fakeredis that executes jobs synchronously.

    Uses ``JSONSerializer`` to mirror the app's ``get_queue()`` so the route's
    serializer-aware ``Job.fetch`` round-trips results correctly.
    """
    from rq.serializers import JSONSerializer

    connection = fakeredis.FakeRedis()
    return Queue('profiling', connection=connection, is_async=False, serializer=JSONSerializer)


@pytest.fixture()
def startup_config(project_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StartupConfig:
    """Startup config fixture for startup-managed path mode."""
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
def web_sample_vcf(startup_config: StartupConfig) -> Path:
    """Minimal VCF written inside uploads_dir so path confinement checks pass."""
    vcf_content = textwrap.dedent(f"""\
        ##fileformat=VCFv4.2
        ##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
        ##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        {TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500
        {TINY_REF_NAME}\t10\t.\tT\tC\t80\tPASS\tAF=0.30;DP=200
    """)
    path = startup_config.uploads_dir / 'sample.vcf'
    path.write_text(vcf_content)
    return path


@pytest.fixture()
def web_sample_ref_fasta(startup_config: StartupConfig) -> Path:
    """Reference FASTA written inside uploads_dir so path confinement checks pass."""
    path = startup_config.uploads_dir / 'sample_ref.fasta'
    path.write_text(f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n')
    return path


@pytest.fixture()
def client(sync_queue: Queue, startup_config: StartupConfig):
    """TestClient with queue override and startup config injected."""
    app = create_app(startup_config=startup_config)
    app.dependency_overrides[get_queue] = lambda: sync_queue
    app.dependency_overrides[get_batch_queue] = lambda: sync_queue
    return TestClient(app)


class TestNoApiToken:
    """AUTH-001: RESPRO_WEB_API_TOKEN and require_api_token are removed entirely.

    The webapp is a pure browser UI; programmatic use is served by the CLI. The
    bearer-token gate, the ``api_token`` field, and the body-token fallback are
    all gone. Every route is open; the session cookie provides per-user data
    isolation, which is orthogonal to the (now-removed) token gate.
    """

    @pytest.fixture(autouse=True)
    def _reset_session_stores(self):
        reset_memory_stores()
        yield
        reset_memory_stores()

    def test_startup_config_has_no_api_token_field(self) -> None:
        import dataclasses

        assert not any(field.name == 'api_token' for field in dataclasses.fields(StartupConfig))

    def test_require_api_token_symbol_is_removed(self) -> None:
        import web.backend.main as main_module

        assert not hasattr(main_module, 'require_api_token')
        assert not hasattr(main_module, '_extract_bearer_token')

    def test_databases_route_requires_no_authorization_header(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
    ) -> None:
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.get('/api/databases')  # no Authorization header

        assert response.status_code == 200
        assert isinstance(response.json()['data']['items'], list)

    def test_databases_route_open_in_online_mode(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
    ) -> None:
        """AUTH-006: online mode must not re-introduce a token gate on /api/databases.

        The local-mode test above covers the default; this guards the online
        deployment path, where the session cookie is marked ``Secure`` and the
        docs are disabled, but the API routes remain open.
        """
        online_config = replace(startup_config, deployment_mode='online')
        app = create_app(startup_config=online_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.get('/api/databases')  # no Authorization header

        assert response.status_code == 200
        assert isinstance(response.json()['data']['items'], list)

    def test_session_cleanup_accepts_no_body_token(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
    ) -> None:
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},  # no token field
        )

        assert response.status_code == 200


class TestWebApi:
    @pytest.fixture(autouse=True)
    def _reset_session_stores(self):
        """Clear the in-memory session/ownership stores before and after each test."""
        reset_memory_stores()
        yield
        reset_memory_stores()

    def test_startup_policy_local_mode_allows_zero_config(self) -> None:
        # local mode (the default) requires no proxy, no CORS.
        _validate_startup_policy(deployment_mode='local')

    def test_startup_policy_online_requires_trusted_proxies(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv('RESPRO_WEB_TRUSTED_PROXIES', raising=False)
        with pytest.raises(RuntimeError, match='RESPRO_WEB_TRUSTED_PROXIES'):
            _validate_startup_policy(deployment_mode='online')

    def test_startup_policy_online_succeeds_with_trusted_proxies(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        _validate_startup_policy(deployment_mode='online')

    def test_startup_policy_rejects_unknown_mode(self) -> None:
        with pytest.raises(RuntimeError, match='Unknown RESPRO_WEB_DEPLOYMENT_MODE'):
            _validate_startup_policy(deployment_mode='bogus')

    def test_startup_policy_rejects_legacy_trusted_proxy_mode(self) -> None:
        with pytest.raises(RuntimeError, match='Unknown RESPRO_WEB_DEPLOYMENT_MODE'):
            _validate_startup_policy(deployment_mode='trusted-proxy')

    def test_startup_policy_rejects_legacy_public_session_mode(self) -> None:
        with pytest.raises(RuntimeError, match='Unknown RESPRO_WEB_DEPLOYMENT_MODE'):
            _validate_startup_policy(deployment_mode='public-session')

    def test_docs_enabled_in_local_mode(self, startup_config: StartupConfig) -> None:
        local_config = replace(startup_config, deployment_mode='local')
        client = TestClient(create_app(startup_config=local_config))
        assert client.get('/docs').status_code != 404
        assert client.get('/redoc').status_code != 404
        assert client.get('/openapi.json').status_code != 404

    def test_docs_disabled_in_online_mode(self, startup_config: StartupConfig) -> None:
        online_config = replace(startup_config, deployment_mode='online')
        client = TestClient(create_app(startup_config=online_config))
        assert client.get('/docs').status_code == 404
        assert client.get('/redoc').status_code == 404
        assert client.get('/openapi.json').status_code == 404

    def test_create_app_without_explicit_config_uses_loaded_startup_config(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The real ``run()`` path calls ``create_app()`` with no argument.

        Regression guard for the bug where the session-cookie middleware was
        wired with ``startup_config.deployment_mode`` (the parameter, which is
        ``None`` on this path) instead of the resolved ``config`` — raising
        ``AttributeError: 'NoneType' object has no attribute 'deployment_mode'``
        at startup. The test monkeypatches ``load_startup_config`` to return a
        known local-mode config and asserts the app builds and serves a request
        carrying the session cookie.
        """
        import web.backend.main as main_module

        no_token_config = replace(startup_config, deployment_mode='local')
        monkeypatch.setattr(main_module, 'load_startup_config', lambda: no_token_config)
        client = TestClient(create_app())
        # /api/health is public in local mode; the response must carry a session
        # cookie, proving the middleware was wired with the resolved config.
        response = client.get('/api/health')
        assert response.status_code == 200
        set_cookie = response.headers.get('set-cookie', '')
        assert SESSION_COOKIE_NAME in set_cookie

    def test_proxy_settings_default_to_disabled_without_trusted_proxies(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv('RESPRO_WEB_TRUSTED_PROXIES', raising=False)
        proxy_headers, forwarded_allow_ips = _resolve_proxy_settings()
        assert proxy_headers is False
        assert forwarded_allow_ips == ''

    def test_proxy_settings_enable_forwarded_headers_when_trusted_proxies_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1, 10.0.0.0/8')
        proxy_headers, forwarded_allow_ips = _resolve_proxy_settings()
        assert proxy_headers is True
        assert forwarded_allow_ips == '127.0.0.1,10.0.0.0/8'

    def test_cors_uses_configured_origins(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.com')
        client = TestClient(create_app(startup_config=startup_config))
        allowed = client.options(
            '/api/health',
            headers={
                'Origin': 'https://respro.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )
        blocked = client.options(
            '/api/health',
            headers={
                'Origin': 'https://other.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )

        assert allowed.status_code == 200
        assert allowed.headers['access-control-allow-origin'] == 'https://respro.example.com'
        assert blocked.status_code == 400

    def test_cors_uses_localhost_defaults_when_unconfigured(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv('RESPRO_WEB_CORS_ORIGINS', raising=False)
        client = TestClient(create_app(startup_config=startup_config))

        allowed = client.options(
            '/api/health',
            headers={
                'Origin': 'http://localhost:5173',
                'Access-Control-Request-Method': 'GET',
            },
        )
        blocked = client.options(
            '/api/health',
            headers={
                'Origin': 'https://respro.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )

        assert allowed.status_code == 200
        assert allowed.headers['access-control-allow-origin'] == 'http://localhost:5173'
        assert blocked.status_code == 400

    def test_cors_uses_env_override_when_configured(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            'RESPRO_WEB_CORS_ORIGINS',
            'https://respro.example.com, https://lab.example.com',
        )
        client = TestClient(create_app(startup_config=startup_config))

        allowed = client.options(
            '/api/health',
            headers={
                'Origin': 'https://lab.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )
        blocked = client.options(
            '/api/health',
            headers={
                'Origin': 'http://localhost:5173',
                'Access-Control-Request-Method': 'GET',
            },
        )

        assert allowed.status_code == 200
        assert allowed.headers['access-control-allow-origin'] == 'https://lab.example.com'
        assert blocked.status_code == 400

    def test_health_endpoint(self, startup_config: StartupConfig) -> None:
        client = TestClient(create_app(startup_config=startup_config))
        response = client.get('/api/health')
        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'ok'

    def test_root_route_does_not_shadow_api_routes(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = TestClient(create_app(startup_config=startup_config))

        root_response = client.get('/')
        api_response = client.get('/api/health')

        assert api_response.status_code == 200
        assert api_response.json()['status'] == 'ok'

        # Frontend mount is optional in tests. Validate deterministic behavior for both modes.
        if root_response.status_code == 200:
            assert 'text/html' in root_response.headers['content-type']
        else:
            assert root_response.status_code == 404

    def test_readiness_endpoint_reports_redis_and_project_db_readiness(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis_client = Mock()
        redis_client.ping.return_value = True
        monkeypatch.setattr('web.backend.main.redis.Redis.from_url', lambda *_args, **_kwargs: redis_client)

        client = TestClient(create_app(startup_config=startup_config))
        response = client.get('/api/readiness')

        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'ok'
        assert payload['data']['redis']['connected'] is True
        assert payload['data']['project_databases']['ready'] is True
        assert payload['data']['project_databases']['count'] >= 1

    def test_readiness_endpoint_returns_503_when_redis_unreachable(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis_client = Mock()
        redis_client.ping.side_effect = RuntimeError('redis connection failed')
        monkeypatch.setattr('web.backend.main.redis.Redis.from_url', lambda *_args, **_kwargs: redis_client)

        client = TestClient(create_app(startup_config=startup_config))
        response = client.get('/api/readiness')

        assert response.status_code == 503
        payload = response.json()
        assert payload['status'] == 'error'
        assert payload['data']['redis']['connected'] is False
        assert payload['data']['project_databases']['ready'] is True
        assert 'redis_unreachable' in payload['data']['diagnostics']

    @pytest.mark.parametrize(
        ('rq_status', 'expected_api_status'),
        [
            ('queued', 'queued'),
            ('deferred', 'queued'),
            ('scheduled', 'queued'),
            ('started', 'running'),
            ('finished', 'succeeded'),
            ('failed', 'failed'),
            ('stopped', 'failed'),
            ('canceled', 'failed'),
            ('unknown', 'queued'),
        ],
    )
    def test_job_status_maps_rq_statuses_to_stable_api_contract(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        rq_status: str,
        expected_api_status: str,
    ) -> None:
        session_hash = _establish_session(client)
        record_job(session_hash=session_hash, upload_ids=[], job_id='test-job-id')

        job = Mock()
        job.get_status.return_value = rq_status
        job.return_value.return_value = {'report_html_path': '/tmp/example.report.html'}
        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.get('/api/jobs/test-job-id')

        assert response.status_code == 200
        payload = response.json()
        assert payload['job_id'] == 'test-job-id'
        assert payload['status'] == expected_api_status
        if expected_api_status == 'succeeded':
            assert isinstance(payload['result'], dict)
            assert payload['result']['report_html_path']  # opaque artifact ID, non-empty
        else:
            assert payload['result'] is None

    def test_job_status_failed_without_exc_info_returns_stable_error_message(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_hash = _establish_session(client)
        record_job(session_hash=session_hash, upload_ids=[], job_id='test-job-id')

        job = Mock()
        job.get_status.return_value = 'failed'
        job.return_value.return_value = None
        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.get('/api/jobs/test-job-id')

        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'failed'
        assert payload['error'] == 'The operation failed on the server.'

    def test_job_status_missing_id_returns_404_with_stable_payload(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise_no_such_job(*_args, **_kwargs):
            raise NoSuchJobError('missing')

        monkeypatch.setattr('web.backend.main.Job.fetch', _raise_no_such_job)

        response = client.get('/api/jobs/missing-job-id')

        assert response.status_code == 404
        assert response.json() == {'detail': 'Job not found.'}

    def test_rules_endpoint(self, client: TestClient) -> None:
        rules_response = client.get(
            '/api/rules',
        )
        assert rules_response.status_code == 200
        rules = rules_response.json()['data']['items']
        assert len(rules) >= 1
        assert rules[0]['feature'] == 'gag'

    def test_databases_endpoint(self, client: TestClient) -> None:
        response = client.get('/api/databases')
        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['count'] == 1
        database = payload['items'][0]
        assert database['display_name']
        assert 'created_at' in database
        assert 'supported_organisms' in database

    def test_databases_endpoint_includes_project_metadata(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        project_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        conn = sqlite3.connect(project_db)
        conn.execute(
            'UPDATE project SET metadata_maintainers = ?, metadata_contact = ?, metadata_license = ? WHERE id = 1',
            ('Alice; Bob', 'team@example.org', 'MIT'),
        )
        conn.commit()
        conn.close()

        response = client.get('/api/databases')
        assert response.status_code == 200
        database = response.json()['data']['items'][0]
        assert database['metadata']['maintainers'] == 'Alice; Bob'
        assert database['metadata']['contact'] == 'team@example.org'
        assert database['metadata']['license'] == 'MIT'

    def test_databases_endpoint_has_example_false_by_default(self, client: TestClient) -> None:
        response = client.get('/api/databases')
        assert response.status_code == 200
        database = response.json()['data']['items'][0]
        assert database['has_example'] is False

    def test_databases_endpoint_has_example_true_when_stored(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        project_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        conn = sqlite3.connect(project_db)
        conn.execute(
            "UPDATE project SET example_fasta = ? WHERE id = 1",
            ('>example\nATGAAAGCT\n',),
        )
        conn.commit()
        conn.close()

        response = client.get('/api/databases')
        assert response.status_code == 200
        database = response.json()['data']['items'][0]
        assert database['has_example'] is True

    def test_mutations_endpoint_alias(self, client: TestClient) -> None:
        project_db = sorted(client.app.state.startup_config.project_databases_dir.glob('*.db'))[0]
        conn = sqlite3.connect(project_db)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE drug SET name = ?, alias = ? WHERE id = 1", ('Acyclovir', 'ACV'))
        conn.execute(
            'INSERT INTO interpretation_algorithm (project_id, algorithm_name, config_json) VALUES (?, ?, ?)',
            (
                1,
                'drug_groups',
                json.dumps({
                    'name': 'drug_groups',
                    'groups': {'Nucleoside analogues': ['Acyclovir']},
                }),
            ),
        )
        conn.commit()
        conn.close()

        response = client.get('/api/mutations')
        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['count'] >= 1
        assert 'formula_items' in payload
        assert 'formula_count' in payload
        assert 'formula_columns' in payload
        assert payload['items'][0]['drug'] == 'Acyclovir'
        assert payload['items'][0]['drug_group'] == 'Nucleoside analogues'
        assert payload['plot_meta']['drug_aliases']['acyclovir'] == 'ACV'
        assert payload['plot_meta']['drug_groups']['acyclovir'] == 'Nucleoside analogues'
        if payload['formula_count'] > 0:
            assert 'normalized_expression' in payload['formula_columns']

    def test_rules_endpoint_ignores_undefined_reference_filter(
        self,
        client: TestClient,
    ) -> None:
        response = client.get(
            '/api/rules',
            params={
                'reference': 'undefined',
            },
        )

        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['count'] >= 1

    def test_profile_fasta(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
    ) -> None:
        fasta_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/fasta',
            json={
                'fasta_id': fasta_id,
                'input_display_name': 'original-upload.fasta',
                'sample': 'web-fasta',
            },
        )
        assert submit.status_code == 200
        job_id = submit.json()['job_id']
        assert job_id

        payload = _poll_job(client, job_id)
        assert payload['status'] == 'succeeded'
        result = payload['result']
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        assert result['database_id'] == default_db.name
        assert 'database_path' not in result
        assert result['sample_name'] == 'web-fasta'
        assert result['run_id'] is None
        assert 'input_path' not in result
        assert 'reference_fasta_path' not in result
        html_id = result['report_html_path']
        json_id = result['report_json_path']
        pdf_id = result['report_pdf_path']
        assert html_id
        assert json_id
        assert pdf_id
        report_response = client.get('/api/report', params={'artifact_id': html_id})
        assert report_response.status_code == 200
        assert report_response.headers['content-type'].startswith('text/html')
        assert report_response.content.lstrip()[:5].lower().startswith(b'<html') or b'<!doctype' in report_response.content.lower()
        report_payload = _download_artifact_json(client, json_id)
        assert report_payload['run']['vcf_path'] == 'original-upload.fasta'

    def test_profile_fasta_use_example_profiles_stored_example(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        """use_example=true enqueues a fasta --example job without an uploaded FASTA."""
        project_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        conn = sqlite3.connect(project_db)
        # Store the tiny reference itself as the example so it profiles cleanly (0 hits).
        conn.execute(
            "UPDATE project SET example_fasta = ? WHERE id = 1",
            (f'>stored_example\n{TINY_REF_SEQ}\n',),
        )
        conn.commit()
        conn.close()

        submit = client.post(
            '/api/profile/fasta',
            json={
                'use_example': True,
                'sample': 'web-example',
            },
        )
        assert submit.status_code == 200, submit.text
        job_id = submit.json()['job_id']
        assert job_id

        payload = _poll_job(client, job_id)
        assert payload['status'] == 'succeeded', payload
        result = payload['result']
        assert result['database_id'] == project_db.name
        assert result['sample_name'] == 'web-example'

    def test_profile_fasta_use_example_rejects_missing_example(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        """use_example=true on a DB without an example fails the job with a clear error."""
        submit = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'sample': 'web-example'},
        )
        assert submit.status_code == 200
        job_id = submit.json()['job_id']

        payload = _poll_job(client, job_id)
        assert payload['status'] == 'failed'
        assert 'example' in (payload.get('error') or '').lower()

    def test_profile_fasta_path_outside_uploads_rejected(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'nonexistent-id'},
        )
        assert response.status_code == 404
        assert 'FASTA file not found' in response.json()['detail']

    def test_profile_vcf(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'input_display_name': 'original-upload.vcf',
                'sample': 'web-vcf',
            },
        )
        assert submit.status_code == 200
        job_id = submit.json()['job_id']
        assert job_id

        payload = _poll_job(client, job_id)
        assert payload['status'] == 'succeeded'
        result = payload['result']
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        assert result['sample_name'] == 'web-vcf'
        assert result['run_id'] is None
        assert result['database_id'] == default_db.name
        assert 'database_path' not in result
        assert 'input_path' not in result
        assert 'reference_fasta_path' not in result
        html_id = result['report_html_path']
        json_id = result['report_json_path']
        assert html_id
        assert json_id
        report_response = client.get('/api/report', params={'artifact_id': html_id})
        assert report_response.status_code == 200
        report_payload = _download_artifact_json(client, json_id)
        assert report_payload['run']['vcf_path'] == 'original-upload.vcf'

    def test_artifact_download_serves_pdf_from_results_dir(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'input_display_name': 'download-name-check.vcf',
                'sample': 'artifact-pdf',
            },
        )
        assert submit.status_code == 200

        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert isinstance(result, dict)
        pdf_id = result['report_pdf_path']

        artifact = client.get(
            '/api/artifact',
            params={'artifact_id': pdf_id},
        )

        assert artifact.status_code == 200
        assert artifact.headers['content-type'].startswith('application/pdf')
        assert 'filename="download-name-check.pdf"' in artifact.headers['content-disposition']
        assert artifact.content.startswith(b'%PDF')

    def test_artifact_download_serves_tsv_from_results_dir(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        """The TSV export produced by every profile job is downloadable via /api/artifact."""
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'input_display_name': 'tsv-name-check.vcf',
                'sample': 'artifact-tsv',
            },
        )
        assert submit.status_code == 200

        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert isinstance(result, dict)
        tsv_id = result['report_tsv_path']
        assert tsv_id

        artifact = client.get(
            '/api/artifact',
            params={'artifact_id': tsv_id},
        )

        assert artifact.status_code == 200
        assert artifact.headers['content-type'].startswith('text/tab-separated-values')
        assert 'filename="tsv-name-check.tsv"' in artifact.headers['content-disposition']
        text = artifact.content.decode('utf-8')
        header = text.splitlines()[0]
        assert header.startswith('reference\tgene\tnt_mut')
        # The test DB's K->E rule matches the POS 4 variant, so at least one hit row exists.
        assert 'TestDrug' in text

    def test_artifact_download_rejects_unknown_artifact_id(
        self,
        client: TestClient,
    ) -> None:
        response = client.get(
            '/api/artifact',
            params={'artifact_id': 'nonexistent-artifact-id'},
        )

        assert response.status_code == 404
        assert response.json()['detail'] == 'Artifact not found.'

    def test_artifact_bundle_download_packs_multiple_results_artifacts(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'input_display_name': 'bundle-name-check.vcf',
                'sample': 'artifact-bundle',
            },
        )
        assert submit.status_code == 200

        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert isinstance(result, dict)

        bundle = client.post(
            '/api/artifact-bundle',
            json={
                'artifact_ids': [
                    result['report_json_path'],
                    result['report_pdf_path'],
                    result['report_tsv_path'],
                ],
            },
        )

        assert bundle.status_code == 200
        assert bundle.headers['content-type'] == 'application/zip'
        assert 'respro-batch-artifacts.zip' in bundle.headers['content-disposition']

        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            assert 'bundle-name-check.json' in names
            assert 'bundle-name-check.pdf' in names
            assert 'bundle-name-check.tsv' in names
            report_payload = json.loads(archive.read('bundle-name-check.json').decode('utf-8'))
            assert report_payload['run']['sample_name'] == 'artifact-bundle'

    def test_artifact_bundle_rejects_unknown_artifact_id(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/artifact-bundle',
            json={'artifact_ids': ['nonexistent-artifact-id']},
        )

        assert response.status_code == 404
        assert response.json()['detail'] == 'Artifact not found.'

    def test_profile_vcf_path_outside_uploads_rejected(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': 'nonexistent-id',
                'reference_id': 'nonexistent-id',
            },
        )
        assert response.status_code == 404
        assert 'VCF file not found' in response.json()['detail']

    def test_profile_vcf_ref_fasta_outside_uploads_rejected(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': 'nonexistent-id',
            },
        )
        assert response.status_code == 404
        assert 'Reference FASTA file not found' in response.json()['detail']

    def test_profile_vcf_repeated_runs_keep_distinct_report_artifacts(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        first_submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'web-vcf-repeat',
            },
        )
        assert first_submit.status_code == 200
        first_payload = _poll_job(client, first_submit.json()['job_id'])
        assert first_payload['status'] == 'succeeded'
        first_result = first_payload['result']

        second_submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'web-vcf-repeat',
            },
        )
        assert second_submit.status_code == 200
        second_payload = _poll_job(client, second_submit.json()['job_id'])
        assert second_payload['status'] == 'succeeded'
        second_result = second_payload['result']

        assert first_result['report_html_path'] != second_result['report_html_path']
        assert first_result['report_json_path'] != second_result['report_json_path']
        assert first_result['report_pdf_path'] != second_result['report_pdf_path']
        first_report = client.get(
            '/api/report',
            params={'artifact_id': first_result['report_html_path']},
        )
        second_report = client.get(
            '/api/report',
            params={'artifact_id': second_result['report_html_path']},
        )
        assert first_report.status_code == 200
        assert second_report.status_code == 200

    def test_profile_vcf_uses_requested_database_id(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        primary_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        alternate_db = startup_config.project_databases_dir / 'alternate.db'
        shutil.copy2(primary_db, alternate_db)

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'database_id': alternate_db.name,
                'sample': 'web-vcf-alt',
            },
        )
        assert submit.status_code == 200

        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert result['database_id'] == alternate_db.name
        assert 'database_path' not in result
        assert 'input_path' not in result
        assert 'reference_fasta_path' not in result

    def test_profile_vcf_reports_reference_mismatch_clearly(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
    ) -> None:
        mismatch_vcf = startup_config.uploads_dir / 'mismatch.vcf'
        mismatch_vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'other_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        vcf_id = _upload_file(client, mismatch_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'web-vcf-mismatch',
            },
        )
        assert submit.status_code == 200

        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'failed'
        assert payload['error'] == (
            'VCF CHROM(s) have no matching reference FASTA record: other_ref. '
            'VCF CHROMs=[\'other_ref\'], FASTA records=[\'tiny_ref\']. '
            'Provide a reference FASTA whose record headers cover every VCF CHROM.'
        )

    def test_cancel_job_returns_404_for_unknown_id(
        self,
        client: TestClient,
    ) -> None:
        response = client.delete('/api/jobs/does-not-exist')
        assert response.status_code == 404
        assert response.json()['detail'] == 'Job not found.'

    def test_cancel_queued_job_calls_job_cancel(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_hash = _establish_session(client)
        record_job(session_hash=session_hash, upload_ids=[], job_id='test-job-id')

        job = Mock()
        job.get_status.return_value = 'queued'

        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.delete('/api/jobs/test-job-id')
        assert response.status_code == 204
        job.cancel.assert_called_once_with()

    def test_cancel_started_job_calls_kill_worker_when_available(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_hash = _establish_session(client)
        record_job(session_hash=session_hash, upload_ids=[], job_id='test-job-id')

        job = Mock()
        job.get_status.return_value = 'started'

        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.delete('/api/jobs/test-job-id')
        assert response.status_code == 204
        job.kill_worker.assert_called_once_with()

    def test_profile_vcf_enqueues_with_job_timeout_and_retry_defaults(
        self,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        queue = Mock()
        enqueued = Mock()
        enqueued.id = 'test-job-id'
        queue.enqueue.return_value = enqueued

        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: queue
        app.dependency_overrides[get_batch_queue] = lambda: queue
        client = TestClient(app)

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'queue-defaults',
            },
        )

        assert response.status_code == 200
        _args, kwargs = queue.enqueue.call_args
        assert kwargs['job_timeout'] == WEB_BACKEND_CONFIG.defaults.job_timeout_seconds
        if WEB_BACKEND_CONFIG.defaults.job_retry_max > 0:
            assert kwargs['retry'].max == WEB_BACKEND_CONFIG.defaults.job_retry_max
        else:
            assert 'retry' not in kwargs

    def test_upload_fasta_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        fasta_data = b'>seq1\nATCGATCG\n>seq2\nATCGATCG\n'
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', fasta_data, 'text/plain')},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'fasta'
        assert payload['size_bytes'] == len(fasta_data)
        assert payload['upload_id']
        # Verify a file matching the uploaded bytes now exists in the upload directory.
        uploaded_files = list(startup_config.uploads_dir.iterdir())
        assert any(f.read_bytes() == fasta_data for f in uploaded_files)

    def test_upload_vcf_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        vcf_data = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('sample.vcf', vcf_data, 'text/plain')},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'vcf'
        assert payload['size_bytes'] == len(vcf_data)
        assert payload['upload_id']
        uploaded_files = list(startup_config.uploads_dir.iterdir())
        assert any(f.read_bytes() == vcf_data for f in uploaded_files)

    def test_upload_bam_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        # Minimal valid BGZF block header plus EOF payload.
        bam_data = bytes.fromhex('1f8b08040000000000ff0600424302001b000300000000000000')
        response = client.post(
            '/api/upload/bam',
            files={'file': ('sample.bam', bam_data, 'application/octet-stream')},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'bam'
        assert payload['size_bytes'] == len(bam_data)
        assert payload['upload_id']
        uploaded_files = list(startup_config.uploads_dir.iterdir())
        assert any(f.read_bytes() == bam_data for f in uploaded_files)

    def test_upload_json_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        payload = {
            'run': {
                'project_name': 'demo',
                'reference_name': 'tiny_ref',
                'sample_name': 'sample1',
                'vcf_path': 'sample.vcf',
                'total_variants': 0,
                'variants_in_cds': 0,
                'resistance_hits': 0,
                'created_at': '2026-04-21T10:00:00',
            },
            'variant_result': [],
            'coverage_gap': [],
            'formula_rule_hit': [],
            'sample_classification': [],
        }
        response = client.post(
            '/api/upload/json',
            files={
                'file': (
                    'sample.results.json',
                    json.dumps(payload).encode('utf-8'),
                    'application/json',
                )
            },
        )
        assert response.status_code == 200
        upload_payload = response.json()
        assert upload_payload['file_type'] == 'json'
        assert upload_payload['upload_id']
        uploaded_files = list(startup_config.uploads_dir.iterdir())
        assert any(f.is_file() for f in uploaded_files)

    def test_upload_json_invalid_payload_rejected(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/upload/json',
            files={'file': ('invalid.results.json', b'{"run": {}}', 'application/json')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert 'Unsupported JSON format' in payload['detail']

    def test_regenerate_from_json(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit_profile = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'regen-web-vcf',
            },
        )
        assert submit_profile.status_code == 200
        profile_job_id = submit_profile.json()['job_id']

        profile_payload = _poll_job(client, profile_job_id)
        assert profile_payload['status'] == 'succeeded'
        json_artifact_id = profile_payload['result']['report_json_path']

        # The regenerate route resolves a json_id as an *upload* record, so
        # re-upload the results JSON artifact bytes via /api/upload/json.
        json_bytes = client.get(
            '/api/artifact',
            params={'artifact_id': json_artifact_id},
        ).content
        json_id = _upload_bytes(client, json_bytes, 'json', 'sample.results.json')

        submit_regen = client.post(
            '/api/regenerate/json',
            json={'json_id': json_id},
        )
        assert submit_regen.status_code == 200
        regen_job_id = submit_regen.json()['job_id']

        regen_payload = _poll_job(client, regen_job_id)
        assert regen_payload['status'] == 'succeeded'
        result = regen_payload['result']
        assert result['mode'] == 'regenerate-json'
        html_id = result['report_html_path']
        json_id_out = result['report_json_path']
        pdf_id = result['report_pdf_path']
        assert html_id
        assert json_id_out
        assert pdf_id
        report_response = client.get('/api/report', params={'artifact_id': html_id})
        assert report_response.status_code == 200
        json_response = client.get('/api/artifact', params={'artifact_id': json_id_out})
        assert json_response.status_code == 200
        pdf_response = client.get('/api/artifact', params={'artifact_id': pdf_id})
        assert pdf_response.status_code == 200

    def test_regenerate_from_json_auto_selects_database_by_uuid(
        self,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        primary_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        alternate_db = startup_config.project_databases_dir / 'z-regenerate-alt.db'
        shutil.copy2(primary_db, alternate_db)
        alternate_uuid = str(uuid4())
        _write_project_uuid(alternate_db, alternate_uuid)

        reindexed_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            project_db_uuid_index=build_project_db_uuid_index(startup_config.project_databases_dir),
        )
        test_app = create_app(startup_config=reindexed_config)
        test_app.dependency_overrides[get_queue] = lambda: sync_queue
        test_app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        test_client = TestClient(test_app)

        vcf_id = _upload_file(test_client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(test_client, web_sample_ref_fasta, 'fasta')
        submit_profile = test_client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'database_id': alternate_db.name,
                'sample': 'regen-web-vcf-auto-select',
            },
        )
        assert submit_profile.status_code == 200
        profile_job_id = submit_profile.json()['job_id']
        profile_payload = _poll_job(test_client, profile_job_id)
        assert profile_payload['status'] == 'succeeded'

        json_artifact_id = profile_payload['result']['report_json_path']
        json_bytes = test_client.get(
            '/api/artifact',
            params={'artifact_id': json_artifact_id},
        ).content
        json_id = _upload_bytes(test_client, json_bytes, 'json', 'sample.results.json')
        submit_regen = test_client.post(
            '/api/regenerate/json',
            json={'json_id': json_id},
        )
        assert submit_regen.status_code == 200

        regen_payload = _poll_job(test_client, submit_regen.json()['job_id'])
        assert regen_payload['status'] == 'succeeded'
        regenerated_payload = _download_artifact_json(
            test_client, regen_payload['result']['report_json_path']
        )
        assert regenerated_payload['run']['project_fingerprint'] == alternate_uuid

    def test_regenerate_from_json_ignores_wrong_database_id_when_uuid_present(
        self,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        primary_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        alternate_db = startup_config.project_databases_dir / 'z-regenerate-alt-override.db'
        shutil.copy2(primary_db, alternate_db)
        alternate_uuid = str(uuid4())
        _write_project_uuid(alternate_db, alternate_uuid)

        reindexed_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            project_db_uuid_index=build_project_db_uuid_index(startup_config.project_databases_dir),
        )
        test_app = create_app(startup_config=reindexed_config)
        test_app.dependency_overrides[get_queue] = lambda: sync_queue
        test_app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        test_client = TestClient(test_app)

        vcf_id = _upload_file(test_client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(test_client, web_sample_ref_fasta, 'fasta')
        submit_profile = test_client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'database_id': alternate_db.name,
                'sample': 'regen-web-vcf-prefer-json-uuid',
            },
        )
        assert submit_profile.status_code == 200
        profile_payload = _poll_job(test_client, submit_profile.json()['job_id'])
        assert profile_payload['status'] == 'succeeded'

        json_artifact_id = profile_payload['result']['report_json_path']
        json_bytes = test_client.get(
            '/api/artifact',
            params={'artifact_id': json_artifact_id},
        ).content
        json_id = _upload_bytes(test_client, json_bytes, 'json', 'sample.results.json')
        submit_regen = test_client.post(
            '/api/regenerate/json',
            json={
                'json_id': json_id,
                'database_id': primary_db.name,
            },
        )
        assert submit_regen.status_code == 200

        regen_payload = _poll_job(test_client, submit_regen.json()['job_id'])
        assert regen_payload['status'] == 'succeeded'
        regenerated_payload = _download_artifact_json(
            test_client, regen_payload['result']['report_json_path']
        )
        assert regenerated_payload['run']['project_fingerprint'] == alternate_uuid

    def test_regenerate_from_json_returns_400_when_uuid_missing_from_startup_index(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit_profile = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'sample': 'regen-web-vcf-missing-uuid',
            },
        )
        assert submit_profile.status_code == 200
        profile_job_id = submit_profile.json()['job_id']
        profile_payload = _poll_job(client, profile_job_id)
        assert profile_payload['status'] == 'succeeded'

        source_json_bytes = client.get(
            '/api/artifact',
            params={'artifact_id': profile_payload['result']['report_json_path']},
        ).content
        tampered_payload = json.loads(source_json_bytes.decode('utf-8'))
        tampered_payload['run']['project_fingerprint'] = 'missing-uuid-in-startup-index'
        tampered_bytes = json.dumps(tampered_payload).encode('utf-8')
        json_id = _upload_bytes(client, tampered_bytes, 'json', 'tampered.results.json')

        submit_regen = client.post(
            '/api/regenerate/json',
            json={'json_id': json_id},
        )
        assert submit_regen.status_code == 400
        assert 'No project database found for JSON project_fingerprint' in submit_regen.json()['detail']

    def test_upload_fasta_with_empty_file_rejected(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('empty.fasta', b'', 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert 'empty' in payload['detail'].lower()

    def test_upload_vcf_with_invalid_content_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_vcf = b'this is not a valid vcf file\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'

    def test_upload_fasta_with_binary_content_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_fasta = b'>seq\nATCG\x00ATCG\n'
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('invalid.fasta', invalid_fasta, 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported FASTA format. Upload a plain-text FASTA file.'

    def test_upload_vcf_without_chrom_header_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_vcf = b'##fileformat=VCFv4.2\n1\t10\t.\tA\tG\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'

    def test_upload_vcf_with_binary_content_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_vcf = b'##fileformat=VCFv4.2\n#CHROM\tPOS\x00\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a plain-text VCF file.'

    def test_upload_vcf_with_excessive_line_length_rejected(
        self,
        client: TestClient,
    ) -> None:
        long_alt = 'A' * 100_001
        invalid_vcf = f'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n1\t10\t.\tA\t{long_alt}\n'.encode()
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Input contains an excessively long line.'

    def test_upload_bam_with_invalid_magic_bytes_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_bam = b'\xff\xff' + b'\x00' * 100
        response = client.post(
            '/api/upload/bam',
            files={'file': ('invalid.bam', invalid_bam, 'application/octet-stream')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

    def test_upload_bam_with_invalid_bgzf_structure_rejected(
        self,
        client: TestClient,
    ) -> None:
        invalid_bam = b'\x1f\x8b\x08\x00' + b'\x00' * 64
        response = client.post(
            '/api/upload/bam',
            files={'file': ('invalid-structure.bam', invalid_bam, 'application/octet-stream')},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

    def test_upload_rate_limit_applies_per_ip(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1/minute')
        client = TestClient(create_app(startup_config=startup_config))

        first = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )
        second = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()['detail'] == 'Upload rate limit exceeded. Try again later.'

    def test_ui_config_uses_env_override_for_max_batch_size(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '7')
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.get('/api/ui/config')

        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['batch_max_samples'] == 7
        assert payload['sample_limit_per_minute'] == 7

    def test_ui_config_reports_respro_version(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
    ) -> None:
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.get('/api/ui/config')

        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['version'] == importlib.metadata.version('respro')

    def test_open_report_rejects_unknown_artifact_id(
        self,
        client: TestClient,
    ) -> None:
        response = client.get(
            '/api/report',
            params={'artifact_id': 'nonexistent-artifact-id'},
        )

        assert response.status_code == 404
        assert response.json()['detail'] == 'Report not found.'

    def test_session_cleanup_deletes_uploaded_and_report_files(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
    ) -> None:
        # Upload a FASTA and run a profile job to produce a real owned artifact.
        fasta_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/fasta',
            json={
                'fasta_id': fasta_id,
                'input_display_name': 'cleanup.fasta',
                'sample': 'cleanup-fasta',
            },
        )
        assert submit.status_code == 200
        payload = _poll_job(client, submit.json()['job_id'])
        assert payload['status'] == 'succeeded'
        artifact_id = payload['result']['report_html_path']

        # Resolve the artifact's on-disk path so we can verify deletion afterwards.
        report_response = client.get(
            '/api/report',
            params={'artifact_id': artifact_id},
        )
        assert report_response.status_code == 200

        cleanup_response = client.post(
            '/api/session/cleanup',
            json={
                'upload_ids': [fasta_id],
                'artifact_ids': [artifact_id],
            },
        )
        assert cleanup_response.status_code == 200
        assert cleanup_response.json()['deleted_count'] == 2
        # The upload and the artifact should no longer be resolvable.
        upload_check = client.post('/api/profile/fasta', json={'fasta_id': fasta_id})
        assert upload_check.status_code == 404
        report_check = client.get(
            '/api/report',
            params={'artifact_id': artifact_id},
        )
        assert report_check.status_code == 404

    def test_session_cleanup_deletes_uploaded_bam(
        self,
        client: TestClient,
        startup_config: StartupConfig,
    ) -> None:
        bam_data = bytes.fromhex('1f8b08040000000000ff0600424302001b000300000000000000')
        upload_id = _upload_bytes(client, bam_data, 'bam', 'sample.bam')

        cleanup_response = client.post(
            '/api/session/cleanup',
            json={
                'upload_ids': [upload_id],
                'artifact_ids': [],
            },
        )
        assert cleanup_response.status_code == 200
        assert cleanup_response.json()['deleted_count'] == 1
        # The BAM file is gone: resolving the upload id now returns 404.
        vcf_id = _upload_bytes(client, b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n', 'vcf', 'x.vcf')
        ref_id = _upload_bytes(
            client,
            b'>tiny_ref\nACGT\n',
            'fasta',
            'x.fasta',
        )
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_id,
                'reference_id': ref_id,
                'bam_id': upload_id,
            },
        )
        assert response.status_code == 404
        assert 'BAM file not found' in response.json()['detail']


# ---------------------------------------------------------------------------
# Batch profiling endpoint tests
# ---------------------------------------------------------------------------


class TestBatchProfileEndpoints:
    @pytest.fixture(autouse=True)
    def _reset_sample_quota(self):
        """Clear the in-memory sample-quota counter before each batch test.

        The quota is keyed by ``(identity, minute-window)`` and all batch tests share the same
        token identity, so without a reset the counter accumulates across tests in the same
        minute and later tests spuriously hit 429. Resetting here gives each batch test an
        isolated quota.
        """
        _SAMPLE_QUOTA_COUNTER.clear()
        reset_memory_stores()
        yield
        _SAMPLE_QUOTA_COUNTER.clear()
        reset_memory_stores()

    def test_batch_vcf_submit_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        project_db: Path,
    ) -> None:
        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        vcf_id_a = _upload_file(client, web_sample_vcf, 'vcf')
        vcf_id_b = _upload_file(client, vcf2, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id_a, vcf_id_b],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['batch-a.vcf', 'batch-b.vcf'],
                'reference_id': ref_id,
                'db_path': default_db.name,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data['total'] == 2
        assert len(data['samples']) == 2
        for entry in data['samples']:
            assert entry['job_id']
            assert entry['status'] == 'queued'

    def test_batch_fasta_submit_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        project_db: Path,
    ) -> None:
        fasta2 = startup_config.uploads_dir / 'sample2.fasta'
        fasta2.write_text(web_sample_ref_fasta.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        fasta_id_a = _upload_file(client, web_sample_ref_fasta, 'fasta')
        fasta_id_b = _upload_file(client, fasta2, 'fasta')

        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': [fasta_id_a, fasta_id_b],
                'sample_names': ['fasta-a', 'fasta-b'],
                'input_display_names': ['batch-a.fasta', 'batch-b.fasta'],
                'db_path': default_db.name,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data['total'] == 2
        assert len(data['samples']) == 2
        for entry in data['samples']:
            assert entry['job_id']
            assert entry['status'] == 'queued'

    def test_batch_duplicate_display_names_use_suffix_disambiguation_in_bundle(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        vcf_id_a = _upload_file(client, web_sample_vcf, 'vcf')
        vcf_id_b = _upload_file(client, vcf2, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')

        submit = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id_a, vcf_id_b],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['duplicate-name.vcf', 'duplicate-name.vcf'],
                'reference_id': ref_id,
                'db_path': default_db.name,
            },
        )
        assert submit.status_code == 200
        submitted = submit.json()['samples']

        artifact_ids: list[str] = []
        for sample in submitted:
            payload = _poll_job(client, sample['job_id'])
            assert payload['status'] == 'succeeded'
            artifact_ids.append(payload['result']['report_json_path'])

        bundle = client.post(
            '/api/artifact-bundle',
            json={'artifact_ids': artifact_ids},
        )
        assert bundle.status_code == 200

        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            assert 'duplicate-name.json' in names
            assert 'duplicate-name_1.json' in names

    def test_batch_vcf_exceeds_max_size(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id] * 26,
                'sample_names': [f'sample-{i}' for i in range(26)],
                'reference_id': 'nonexistent-ref-id',
                'db_path': default_db.name,
            },
        )
        assert response.status_code == 422
        detail = str(response.json())
        assert 'batch' in detail.lower() or '25' in detail

    def test_batch_vcf_mismatched_lengths(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id, vcf_id],
                'sample_names': ['only-one'],
                'reference_id': 'nonexistent-ref-id',
                'db_path': default_db.name,
            },
        )
        assert response.status_code == 422

    def test_batch_vcf_max_batch_size_uses_env_override(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id, vcf_id],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_id': 'nonexistent-ref-id',
                'db_path': default_db.name,
            },
        )

        assert response.status_code == 422
        assert 'maximum of 1 samples per batch' in response.json()['detail']

    def test_batch_fasta_exceeds_max_size(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        fasta_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': [fasta_id] * 26,
                'sample_names': [f'fasta-{i}' for i in range(26)],
                'db_path': default_db.name,
            },
        )
        assert response.status_code == 422
        detail = str(response.json())
        assert 'batch' in detail.lower() or '25' in detail

    def test_batch_vcf_validation_error_does_not_enqueue_partial_jobs(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        enqueue_calls = 0
        original_enqueue = sync_queue.enqueue

        def counting_enqueue(*args, **kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', counting_enqueue)

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id, 'nonexistent-missing-vcf-id'],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_id': ref_id,
                'db_path': default_db.name,
            },
        )

        assert response.status_code == 404
        assert "VCF file not found for sample 'sample-b'." in response.json()['detail']
        assert enqueue_calls == 0

    def test_batch_fasta_validation_error_does_not_enqueue_partial_jobs(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        fasta_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        enqueue_calls = 0
        original_enqueue = sync_queue.enqueue

        def counting_enqueue(*args, **kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', counting_enqueue)

        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': [fasta_id, 'nonexistent-missing-fasta-id'],
                'sample_names': ['fasta-a', 'fasta-b'],
                'db_path': default_db.name,
            },
        )

        assert response.status_code == 404
        assert "FASTA file not found for sample 'fasta-b'." in response.json()['detail']
        assert enqueue_calls == 0

    def test_batch_fasta_sample_quota_uses_default_redis_url_when_env_missing(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        redis_urls: list[str] = []

        class FakeQuotaRedis:
            def incrby(self, _key: str, _value: int) -> int:
                return 1

            def expire(self, _key: str, _seconds: int) -> bool:
                return True

        def fake_from_url(url: str):
            redis_urls.append(url)
            return FakeQuotaRedis()

        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.setattr('web.backend.main.redis.Redis.from_url', fake_from_url)

        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': ['nonexistent-fasta-id'],
                'sample_names': ['fasta-a'],
                'db_path': default_db.name,
            },
        )

        assert response.status_code == 404
        assert redis_urls
        assert redis_urls[-1] == WEB_BACKEND_CONFIG.defaults.redis_url

    # ── Batch VCF per-sample BAM ──────────────────────────────────────

    def _default_db(self, startup_config: StartupConfig) -> str:
        return sorted(startup_config.project_databases_dir.glob('*.db'))[0].name

    def test_batch_vcf_with_bam_paths_enqueues_each_job_with_matching_bam(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A full-length bam_ids list wires each BAM to its sample's job."""
        from tests.conftest import write_minimal_bam

        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        bam1 = write_minimal_bam(startup_config.uploads_dir / 'sample.bam')
        bam2 = write_minimal_bam(startup_config.uploads_dir / 'sample2.bam')

        vcf_id_a = _upload_file(client, web_sample_vcf, 'vcf')
        vcf_id_b = _upload_file(client, vcf2, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        bam_id_a = _upload_file(client, bam1, 'bam')
        bam_id_b = _upload_file(client, bam2, 'bam')

        enqueued_bam_paths: list[str | None] = []
        original_enqueue = sync_queue.enqueue

        def capturing_enqueue(*args, **kwargs):
            enqueued_bam_paths.append(kwargs.get('bam_path'))
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', capturing_enqueue)

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id_a, vcf_id_b],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['batch-a.vcf', 'batch-b.vcf'],
                'reference_id': ref_id,
                'db_path': self._default_db(startup_config),
                'bam_ids': [bam_id_a, bam_id_b],
            },
        )

        assert response.status_code == 200
        assert len(enqueued_bam_paths) == 2
        # Each BAM is resolved to its uploaded path within uploads_dir.
        assert enqueued_bam_paths[0] is not None
        assert enqueued_bam_paths[1] is not None
        assert Path(enqueued_bam_paths[0]).parent == startup_config.uploads_dir
        assert Path(enqueued_bam_paths[1]).parent == startup_config.uploads_dir
        assert enqueued_bam_paths[0] != enqueued_bam_paths[1]

    def test_batch_vcf_bam_paths_mismatched_length_returns_422(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """bam_ids shorter than vcf_ids is rejected before any job is enqueued."""
        enqueue_calls = 0
        original_enqueue = sync_queue.enqueue

        def counting_enqueue(*args, **kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', counting_enqueue)

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id, vcf_id],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_id': 'nonexistent-ref-id',
                'db_path': self._default_db(startup_config),
                'bam_ids': ['nonexistent-bam-id'],
            },
        )

        assert response.status_code == 422
        assert 'bam_ids and vcf_ids must have the same length.' in response.json()['detail']
        assert enqueue_calls == 0

    def test_batch_vcf_missing_bam_file_returns_404(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-existent bam_id is rejected with no partial jobs enqueued."""
        enqueue_calls = 0
        original_enqueue = sync_queue.enqueue

        def counting_enqueue(*args, **kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', counting_enqueue)

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id],
                'sample_names': ['sample-a'],
                'reference_id': ref_id,
                'db_path': self._default_db(startup_config),
                'bam_ids': ['nonexistent-bam-id'],
            },
        )

        assert response.status_code == 404
        assert 'BAM file not found.' in response.json()['detail']
        assert enqueue_calls == 0

    def test_batch_vcf_mixed_none_and_valid_bam_entries_enqueue_selectively(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A None entry skips BAM for that sample; a valid id wires its BAM."""
        from tests.conftest import write_minimal_bam

        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        bam2 = write_minimal_bam(startup_config.uploads_dir / 'sample2.bam')

        vcf_id_a = _upload_file(client, web_sample_vcf, 'vcf')
        vcf_id_b = _upload_file(client, vcf2, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        bam_id_b = _upload_file(client, bam2, 'bam')

        enqueued_bam_paths: list[str | None] = []
        original_enqueue = sync_queue.enqueue

        def capturing_enqueue(*args, **kwargs):
            enqueued_bam_paths.append(kwargs.get('bam_path'))
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', capturing_enqueue)

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id_a, vcf_id_b],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['batch-a.vcf', 'batch-b.vcf'],
                'reference_id': ref_id,
                'db_path': self._default_db(startup_config),
                'bam_ids': [None, bam_id_b],
            },
        )

        assert response.status_code == 200
        assert enqueued_bam_paths[0] is None
        assert enqueued_bam_paths[1] is not None
        assert Path(enqueued_bam_paths[1]).parent == startup_config.uploads_dir

    def test_batch_vcf_without_bam_paths_behaves_as_today(
        self,
        client: TestClient,
        sync_queue: Queue,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Omitting bam_ids entirely enqueues every job with bam_path=None."""
        enqueued_bam_paths: list[str | None] = []
        original_enqueue = sync_queue.enqueue

        def capturing_enqueue(*args, **kwargs):
            enqueued_bam_paths.append(kwargs.get('bam_path'))
            return original_enqueue(*args, **kwargs)

        monkeypatch.setattr(sync_queue, 'enqueue', capturing_enqueue)

        vcf_id = _upload_file(client, web_sample_vcf, 'vcf')
        ref_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id, vcf_id],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_id': ref_id,
                'db_path': self._default_db(startup_config),
            },
        )

        assert response.status_code == 200
        assert enqueued_bam_paths == [None, None]


class TestLegalRoute:
    """Legal notice / imprint route across the four configuration states.

    The env var ``RESPRO_WEB_IMPRINT`` accepts either an absolute ``http(s)://`` URL
    (link to an already-hosted imprint) or a local file path (self-hosted HTML).
    """

    def test_legal_route_disabled_when_imprint_unset(self, client: TestClient) -> None:
        """No imprint configured: /legal returns 404, indicator reports disabled."""
        legal_response = client.get('/legal')
        assert legal_response.status_code == 404

        indicator_response = client.get('/api/ui/legal')
        assert indicator_response.status_code == 200
        payload = indicator_response.json()['data']
        assert payload['enabled'] is False
        assert payload['kind'] == 'path'
        assert 'url' not in payload

    def test_legal_route_serves_html_when_imprint_points_at_local_file(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        tmp_path: Path,
    ) -> None:
        """Path mode: /legal serves the stored HTML, indicator reports kind='path'."""
        imprint_content = '<!DOCTYPE html><html><body><h1>Impressum</h1></body></html>'
        imprint_path = tmp_path / 'imprint.html'
        imprint_path.write_text(imprint_content, encoding='utf-8')

        enabled_config = replace(
            startup_config,
            imprint=ImprintConfig(kind='path', html=imprint_content),
        )
        app = create_app(startup_config=enabled_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        legal_client = TestClient(app)

        legal_response = legal_client.get('/legal')
        assert legal_response.status_code == 200
        assert 'text/html' in legal_response.headers['content-type']
        assert legal_response.text == imprint_content

        indicator_response = legal_client.get('/api/ui/legal')
        assert indicator_response.status_code == 200
        payload = indicator_response.json()['data']
        assert payload['enabled'] is True
        assert payload['kind'] == 'path'
        assert 'url' not in payload

    def test_legal_route_redirects_to_external_url_when_imprint_is_url(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
    ) -> None:
        """URL mode: /legal 302-redirects to the external URL; footer link is direct."""
        external_url = 'https://example.org/impressum'
        enabled_config = replace(
            startup_config,
            imprint=ImprintConfig(kind='url', url=external_url),
        )
        app = create_app(startup_config=enabled_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        legal_client = TestClient(app)

        legal_response = legal_client.get('/legal', follow_redirects=False)
        assert legal_response.status_code == 302
        assert legal_response.headers['location'] == external_url

        indicator_response = legal_client.get('/api/ui/legal')
        assert indicator_response.status_code == 200
        payload = indicator_response.json()['data']
        assert payload['enabled'] is True
        assert payload['kind'] == 'url'
        assert payload['url'] == external_url

    def test_startup_fails_fast_when_imprint_path_points_to_missing_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Path mode with missing file: load_startup_config raises (fail-fast)."""
        missing_path = tmp_path / 'does-not-exist.html'
        monkeypatch.setenv('RESPRO_WEB_IMPRINT', str(missing_path))
        with pytest.raises(FileNotFoundError, match='RESPRO_WEB_IMPRINT'):
            load_startup_config()

    def test_startup_fails_fast_when_imprint_url_has_unsupported_scheme(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """URL mode with non-http(s) scheme: load_startup_config raises (fail-fast)."""
        monkeypatch.setenv('RESPRO_WEB_IMPRINT', 'ftp://example.org/impressum')
        with pytest.raises(ValueError, match='RESPRO_WEB_IMPRINT'):
            load_startup_config()

    def test_startup_resolves_external_url_imprint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        startup_config: StartupConfig,
    ) -> None:
        """URL mode env var resolves to ImprintConfig(kind='url', url=...)."""
        external_url = 'https://example.org/impressum'
        monkeypatch.setenv('RESPRO_WEB_IMPRINT', external_url)
        resolved = _resolve_imprint()
        assert resolved == ImprintConfig(kind='url', url=external_url)


class TestExtractDisplayAlgorithms:
    """Unit tests for web.backend.services.browse._extract_display_algorithms."""

    def test_includes_drug_thresholds_for_drug_interpretation(self) -> None:
        from web.backend.services.browse import _extract_display_algorithms

        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
                ],
            }
        ]
        result = _extract_display_algorithms(algorithms)
        assert result['drug_interpretation'][0]['drug_thresholds'] == [
            {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
        ]

    def test_includes_drug_thresholds_for_ic50_thresholds(self) -> None:
        from web.backend.services.browse import _extract_display_algorithms

        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'fold_ic50',
                'thresholds': {'ACV': {'intermediate': 3.0, 'resistant': 10.0}},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'intermediate': 2.0, 'resistant': 5.0}},
                ],
            }
        ]
        result = _extract_display_algorithms(algorithms)
        assert result['ic50_thresholds']['use'] == 'fold_ic50'
        assert result['ic50_thresholds']['drug_thresholds'] == [
            {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'intermediate': 2.0, 'resistant': 5.0}},
        ]

    def test_omits_drug_thresholds_key_when_absent(self) -> None:
        from web.backend.services.browse import _extract_display_algorithms

        algorithms = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
        ]
        result = _extract_display_algorithms(algorithms)
        assert 'drug_thresholds' not in result['drug_interpretation'][0]
        assert 'ic50_thresholds' not in result

    def test_omits_ic50_thresholds_when_not_configured(self) -> None:
        from web.backend.services.browse import _extract_display_algorithms

        result = _extract_display_algorithms([])
        assert 'ic50_thresholds' not in result


class TestApiRouteRateLimits:
    """SEC-004: non-upload API routes must be rate-limited to resist brute-force/scraping."""

    def test_job_status_route_returns_429_when_hammered(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hammering GET /api/jobs/{id} beyond the API rate limit returns 429."""
        monkeypatch.setenv('RESPRO_WEB_API_RATE_LIMIT', '2/minute')
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        statuses = [
            client.get('/api/jobs/nonexistent-id').status_code
            for _ in range(5)
        ]

        # First requests within the limit return 404 (job not found), not 429.
        assert statuses[0] == 404
        # At least one request beyond the limit must be throttled to 429.
        assert 429 in statuses
        throttled = [s for s in statuses if s == 429]
        assert throttled, 'Expected at least one 429 when exceeding the API rate limit'

    def test_artifact_bundle_rejects_more_than_50_paths(
        self,
        client: TestClient,
    ) -> None:
        """ArtifactBundlePayload.artifact_ids capped at 50 → 422 for 51 ids (SEC-004)."""
        artifact_ids = [f'artifact-id-{i}' for i in range(51)]
        response = client.post(
            '/api/artifact-bundle',
            json={'artifact_ids': artifact_ids},
        )
        assert response.status_code == 422

    def test_artifact_bundle_accepts_50_paths(
        self,
        client: TestClient,
    ) -> None:
        """ArtifactBundlePayload.artifact_ids cap of 50 accepts exactly 50 ids (boundary)."""
        artifact_ids = [f'artifact-id-{i}' for i in range(50)]
        response = client.post(
            '/api/artifact-bundle',
            json={'artifact_ids': artifact_ids},
        )
        # 50 ids is within the cap; it should not be a 422 validation error.
        # (It may be 404 because the ids do not resolve, but never 422.)
        assert response.status_code != 422


class TestRequestFieldBounds:
    """Threads and string/list fields on request models are bounded."""

    def test_profile_fasta_rejects_threads_above_cap(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.profile_max_threads
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'some-id', 'threads': cap + 1},
        )
        assert response.status_code == 422

    def test_profile_fasta_rejects_threads_below_one(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'some-id', 'threads': 0},
        )
        assert response.status_code == 422

    def test_profile_vcf_rejects_threads_above_cap(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.profile_max_threads
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': 'some-id',
                'reference_id': 'some-id',
                'threads': cap + 1,
            },
        )
        assert response.status_code == 422

    def test_profile_fasta_rejects_oversized_sample_name(
        self,
        client: TestClient,
    ) -> None:
        too_long = 'x' * (WEB_BACKEND_CONFIG.defaults.sample_name_max_length + 1)
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'some-id', 'sample': too_long},
        )
        assert response.status_code == 422

    def test_profile_fasta_rejects_oversized_display_name(
        self,
        client: TestClient,
    ) -> None:
        too_long = 'x' * (WEB_BACKEND_CONFIG.defaults.display_name_max_length + 1)
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'some-id', 'input_display_name': too_long},
        )
        assert response.status_code == 422

    def test_batch_profile_vcf_rejects_oversized_path_list(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.path_list_max_length
        vcf_ids = [f'id-{i}' for i in range(cap + 1)]
        sample_names = [f's{i}' for i in range(cap + 1)]
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': vcf_ids,
                'sample_names': sample_names,
                'reference_id': 'some-id',
                'db_path': 'x.db',
            },
        )
        assert response.status_code == 422

    def test_batch_profile_fasta_rejects_threads_above_cap(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.profile_max_threads
        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': ['some-id'],
                'sample_names': ['s'],
                'db_path': 'x.db',
                'threads': cap + 1,
            },
        )
        assert response.status_code == 422

    def test_compare_rejects_oversized_path_list(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.path_list_max_length
        artifact_ids = [f'id-{i}' for i in range(cap + 1)]
        response = client.post(
            '/api/compare',
            json={'artifact_ids': artifact_ids},
        )
        assert response.status_code == 422

    def test_session_cleanup_rejects_oversized_path_list(
        self,
        client: TestClient,
    ) -> None:
        cap = WEB_BACKEND_CONFIG.defaults.path_list_max_length
        upload_ids = [f'id-{i}' for i in range(cap + 1)]
        response = client.post(
            '/api/session/cleanup',
            json={'upload_ids': upload_ids},
        )
        assert response.status_code == 422

    def test_profile_fasta_rejects_oversized_opaque_id(
        self,
        client: TestClient,
    ) -> None:
        """An opaque upload ID longer than the configured cap returns 422."""
        cap = WEB_BACKEND_CONFIG.defaults.opaque_id_max_length
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_id': 'x' * (cap + 1)},
        )
        assert response.status_code == 422

    def test_profile_vcf_rejects_oversized_min_depth(
        self,
        client: TestClient,
    ) -> None:
        """A min_depth above the configured cap returns 422."""
        cap = WEB_BACKEND_CONFIG.defaults.min_depth_max
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': 'some-id',
                'reference_id': 'some-ref',
                'min_depth': cap + 1,
            },
        )
        assert response.status_code == 422

    def test_profile_fasta_accepts_default_threads_omitted(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
    ) -> None:
        """Omitting threads entirely must remain valid (defaults applied later)."""
        fasta_id = _upload_file(client, web_sample_ref_fasta, 'fasta')
        submit = client.post(
            '/api/profile/fasta',
            json={
                'fasta_id': fasta_id,
                'input_display_name': 'orig.fasta',
                'sample': 'web-fasta',
            },
        )
        assert submit.status_code == 200

