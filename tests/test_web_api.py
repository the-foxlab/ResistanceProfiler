"""API tests for the web backend."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import textwrap
import zipfile
from pathlib import Path
from unittest.mock import Mock

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue
from rq.exceptions import NoSuchJobError

from tests.conftest import TINY_REF_NAME, TINY_REF_SEQ
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.main import _resolve_proxy_settings, create_app
from web.backend.queue import get_batch_queue, get_queue
from web.backend.startup_config import StartupConfig, _validate_startup_policy


@pytest.fixture()
def sync_queue():
    """An in-process RQ queue backed by fakeredis that executes jobs synchronously."""
    connection = fakeredis.FakeRedis()
    return Queue('profiling', connection=connection, is_async=False)


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
        api_token='test-token',
    )


@pytest.fixture()
def auth_headers(startup_config: StartupConfig) -> dict[str, str]:
    """Authorization header for protected API routes."""
    return {'Authorization': f'Bearer {startup_config.api_token}'}


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


class TestWebApi:
    def test_startup_policy_allows_docker_bind_without_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_HOST', '0.0.0.0')
        monkeypatch.delenv('RESPRO_WEB_CORS_ORIGINS', raising=False)

        _validate_startup_policy(api_token='')

    def test_startup_policy_requires_token_for_non_local_bind_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_HOST', '10.0.0.5')
        monkeypatch.delenv('RESPRO_WEB_CORS_ORIGINS', raising=False)

        with pytest.raises(RuntimeError, match='RESPRO_WEB_API_TOKEN'):
            _validate_startup_policy(api_token='')

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

    def test_cors_uses_configured_origins_when_token_is_set(
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

    def test_cors_raises_when_token_set_without_cors_origins(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv('RESPRO_WEB_CORS_ORIGINS', raising=False)
        with pytest.raises(RuntimeError, match='RESPRO_WEB_CORS_ORIGINS'):
            create_app(startup_config=startup_config)

    def test_cors_uses_localhost_defaults_without_token(
        self,
        startup_config: StartupConfig,
    ) -> None:
        no_token_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            api_token='',
        )
        client = TestClient(create_app(startup_config=no_token_config))

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
        no_token_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            api_token='',
        )
        client = TestClient(create_app(startup_config=no_token_config))

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
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        rq_status: str,
        expected_api_status: str,
    ) -> None:
        job = Mock()
        job.get_status.return_value = rq_status
        job.return_value.return_value = {'report_html_path': '/tmp/example.report.html'}
        job.exc_info = None
        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.get('/api/jobs/test-job-id', headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload['job_id'] == 'test-job-id'
        assert payload['status'] == expected_api_status
        if expected_api_status == 'succeeded':
            assert payload['result'] == {'report_html_path': '/tmp/example.report.html'}
        else:
            assert payload['result'] is None

    def test_job_status_failed_without_exc_info_returns_stable_error_message(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = Mock()
        job.get_status.return_value = 'failed'
        job.return_value.return_value = None
        job.exc_info = None
        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.get('/api/jobs/test-job-id', headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'failed'
        assert payload['error'] == 'The operation failed on the server.'

    def test_job_status_missing_id_returns_404_with_stable_payload(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise_no_such_job(*_args, **_kwargs):
            raise NoSuchJobError('missing')

        monkeypatch.setattr('web.backend.main.Job.fetch', _raise_no_such_job)

        response = client.get('/api/jobs/missing-job-id', headers=auth_headers)

        assert response.status_code == 404
        assert response.json() == {'detail': 'Job not found.'}

    def test_rules_endpoint(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        rules_response = client.get(
            '/api/rules',
            headers=auth_headers,
        )
        assert rules_response.status_code == 200
        rules = rules_response.json()['data']['items']
        assert len(rules) >= 1
        assert rules[0]['feature'] == 'gag'

    def test_databases_endpoint(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        response = client.get('/api/databases', headers=auth_headers)
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
        auth_headers: dict[str, str],
    ) -> None:
        project_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        conn = sqlite3.connect(project_db)
        conn.execute(
            'UPDATE project SET metadata_maintainers = ?, metadata_contact = ?, metadata_license = ? WHERE id = 1',
            ('Alice; Bob', 'team@example.org', 'MIT'),
        )
        conn.commit()
        conn.close()

        response = client.get('/api/databases', headers=auth_headers)
        assert response.status_code == 200
        database = response.json()['data']['items'][0]
        assert database['metadata']['maintainers'] == 'Alice; Bob'
        assert database['metadata']['contact'] == 'team@example.org'
        assert database['metadata']['license'] == 'MIT'

    def test_mutations_endpoint_alias(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        response = client.get('/api/mutations', headers=auth_headers)
        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['count'] >= 1
        assert 'formula_items' in payload
        assert 'formula_count' in payload
        assert 'formula_columns' in payload
        if payload['formula_count'] > 0:
            assert 'normalized_expression' in payload['formula_columns']

    def test_rules_endpoint_ignores_undefined_reference_filter(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.get(
            '/api/rules',
            params={
                'reference': 'undefined',
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['count'] >= 1

    def test_profile_fasta(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/fasta',
            json={
                'fasta_path': str(web_sample_ref_fasta),
                'input_display_name': 'original-upload.fasta',
                'sample': 'web-fasta',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200
        job_id = submit.json()['job_id']
        assert job_id

        status = client.get(f'/api/jobs/{job_id}', headers=auth_headers)
        assert status.status_code == 200
        payload = status.json()
        assert payload['status'] == 'succeeded'
        result = payload['result']
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        assert result['database_id'] == default_db.name
        assert result['database_path'] == str(default_db.resolve())
        assert result['sample_name'] == 'web-fasta'
        assert result['run_id'] is None
        assert result['input_path'] == str(web_sample_ref_fasta.resolve())
        assert Path(result['report_html_path']).name.startswith('original-upload.')
        assert result['report_html_path'].endswith('.report.html')
        assert result['report_json_path'].endswith('.results.json')
        assert result['report_pdf_path'].endswith('.report.pdf')
        assert Path(result['report_html_path']).is_file()
        assert Path(result['report_json_path']).is_file()
        assert Path(result['report_pdf_path']).is_file()
        report_payload = json.loads(Path(result['report_json_path']).read_text(encoding='utf-8'))
        assert report_payload['run']['vcf_path'] == 'original-upload.fasta'

    def test_profile_fasta_path_outside_uploads_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        outside_path = tmp_path / 'outside.fasta'
        outside_path.write_text('>seq1\nATCG\n')
        response = client.post(
            '/api/profile/fasta',
            json={'fasta_path': str(outside_path)},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert 'outside allowed upload directory' in response.json()['detail']

    def test_profile_vcf(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'input_display_name': 'original-upload.vcf',
                'sample': 'web-vcf',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200
        job_id = submit.json()['job_id']
        assert job_id

        status = client.get(f'/api/jobs/{job_id}', headers=auth_headers)
        assert status.status_code == 200
        payload = status.json()
        assert payload['status'] == 'succeeded'
        result = payload['result']
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        assert result['sample_name'] == 'web-vcf'
        assert result['run_id'] is None
        assert result['database_id'] == default_db.name
        assert result['database_path'] == str(default_db.resolve())
        assert result['input_path'] == str(web_sample_vcf.resolve())
        assert result['reference_fasta_path'] == str(web_sample_ref_fasta.resolve())
        assert Path(result['report_html_path']).name.startswith('original-upload.')
        assert result['report_html_path'].endswith('.report.html')
        assert result['report_json_path'].endswith('.results.json')
        assert result['report_pdf_path'].endswith('.report.pdf')
        assert Path(result['report_html_path']).is_file()
        assert Path(result['report_json_path']).is_file()
        assert Path(result['report_pdf_path']).is_file()
        report_payload = json.loads(Path(result['report_json_path']).read_text(encoding='utf-8'))
        assert report_payload['run']['vcf_path'] == 'original-upload.vcf'

    def test_artifact_download_serves_pdf_from_results_dir(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'input_display_name': 'download-name-check.vcf',
                'sample': 'artifact-pdf',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200

        job_id = submit.json()['job_id']
        payload: dict[str, object] | None = None
        for _ in range(10):
            status = client.get(f'/api/jobs/{job_id}', headers=auth_headers)
            assert status.status_code == 200
            payload = status.json()
            if payload['status'] in ('succeeded', 'failed'):
                break

        assert payload is not None
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert isinstance(result, dict)
        report_pdf_path = result['report_pdf_path']

        artifact = client.get(
            '/api/artifact',
            params={'path': report_pdf_path},
            headers=auth_headers,
        )

        assert artifact.status_code == 200
        assert artifact.headers['content-type'].startswith('application/pdf')
        assert 'filename="download-name-check.pdf"' in artifact.headers['content-disposition']
        assert artifact.content.startswith(b'%PDF')

    def test_artifact_download_rejects_uploads_dir_file(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.get(
            '/api/artifact',
            params={'path': str(web_sample_vcf)},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert 'outside allowed results directory' in response.json()['detail']

    def test_artifact_bundle_download_packs_multiple_results_artifacts(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'input_display_name': 'bundle-name-check.vcf',
                'sample': 'artifact-bundle',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200

        job_id = submit.json()['job_id']
        payload: dict[str, object] | None = None
        for _ in range(10):
            status = client.get(f'/api/jobs/{job_id}', headers=auth_headers)
            assert status.status_code == 200
            payload = status.json()
            if payload['status'] in ('succeeded', 'failed'):
                break

        assert payload is not None
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert isinstance(result, dict)

        bundle = client.post(
            '/api/artifact-bundle',
            json={
                'paths': [
                    result['report_json_path'],
                    result['report_pdf_path'],
                ],
            },
            headers=auth_headers,
        )

        assert bundle.status_code == 200
        assert bundle.headers['content-type'] == 'application/zip'
        assert 'respro-batch-artifacts.zip' in bundle.headers['content-disposition']

        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            assert 'bundle-name-check.json' in names
            assert 'bundle-name-check.pdf' in names
            report_payload = json.loads(archive.read('bundle-name-check.json').decode('utf-8'))
            assert report_payload['run']['sample_name'] == 'artifact-bundle'

    def test_artifact_bundle_rejects_paths_outside_results_dir(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.post(
            '/api/artifact-bundle',
            json={'paths': [str(web_sample_vcf)]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert 'outside allowed results directory' in response.json()['detail']

    def test_profile_vcf_path_outside_uploads_rejected(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        outside_vcf = tmp_path / 'outside.vcf'
        outside_vcf.write_text(
            '##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n'
        )
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(outside_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert 'outside allowed upload directory' in response.json()['detail']

    def test_profile_vcf_ref_fasta_outside_uploads_rejected(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        outside_fasta = tmp_path / 'outside.fasta'
        outside_fasta.write_text('>seq1\nATCG\n')
        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(outside_fasta),
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert 'outside allowed upload directory' in response.json()['detail']

    def test_profile_vcf_repeated_runs_keep_distinct_report_artifacts(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        first_submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'web-vcf-repeat',
            },
            headers=auth_headers,
        )
        assert first_submit.status_code == 200
        first_payload = client.get(f"/api/jobs/{first_submit.json()['job_id']}", headers=auth_headers).json()
        assert first_payload['status'] == 'succeeded'
        first_result = first_payload['result']

        second_submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'web-vcf-repeat',
            },
            headers=auth_headers,
        )
        assert second_submit.status_code == 200
        second_payload = client.get(f"/api/jobs/{second_submit.json()['job_id']}", headers=auth_headers).json()
        assert second_payload['status'] == 'succeeded'
        second_result = second_payload['result']

        assert first_result['report_html_path'] != second_result['report_html_path']
        assert first_result['report_json_path'] != second_result['report_json_path']
        assert first_result['report_pdf_path'] != second_result['report_pdf_path']
        assert Path(first_result['report_html_path']).is_file()
        assert Path(second_result['report_html_path']).is_file()

    def test_profile_vcf_uses_requested_database_id(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        primary_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        alternate_db = startup_config.project_databases_dir / 'alternate.db'
        shutil.copy2(primary_db, alternate_db)

        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'database_id': alternate_db.name,
                'sample': 'web-vcf-alt',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200

        status = client.get(f"/api/jobs/{submit.json()['job_id']}", headers=auth_headers)
        assert status.status_code == 200
        payload = status.json()
        assert payload['status'] == 'succeeded'
        result = payload['result']
        assert result['database_id'] == alternate_db.name
        assert result['database_path'] == str(alternate_db.resolve())
        assert result['input_path'] == str(web_sample_vcf.resolve())
        assert result['reference_fasta_path'] == str(web_sample_ref_fasta.resolve())

    def test_profile_vcf_reports_reference_mismatch_clearly(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        mismatch_vcf = startup_config.uploads_dir / 'mismatch.vcf'
        mismatch_vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'other_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(mismatch_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'web-vcf-mismatch',
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200

        status = client.get(f"/api/jobs/{submit.json()['job_id']}", headers=auth_headers)
        assert status.status_code == 200
        payload = status.json()
        assert payload['status'] == 'failed'
        assert payload['error'] == 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'

    def test_cancel_job_returns_404_for_unknown_id(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.delete('/api/jobs/does-not-exist', headers=auth_headers)
        assert response.status_code == 404
        assert response.json()['detail'] == 'Job not found.'

    def test_cancel_queued_job_calls_job_cancel(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = Mock()
        job.get_status.return_value = 'queued'

        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.delete('/api/jobs/test-job-id', headers=auth_headers)
        assert response.status_code == 204
        job.cancel.assert_called_once_with()

    def test_cancel_started_job_calls_kill_worker_when_available(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = Mock()
        job.get_status.return_value = 'started'

        monkeypatch.setattr('web.backend.main.Job.fetch', lambda *_args, **_kwargs: job)

        response = client.delete('/api/jobs/test-job-id', headers=auth_headers)
        assert response.status_code == 204
        job.kill_worker.assert_called_once_with()

    def test_profile_vcf_enqueues_with_job_timeout_and_retry_defaults(
        self,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        queue = Mock()
        enqueued = Mock()
        enqueued.id = 'test-job-id'
        queue.enqueue.return_value = enqueued

        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: queue
        app.dependency_overrides[get_batch_queue] = lambda: queue
        client = TestClient(app)

        response = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'queue-defaults',
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        _args, kwargs = queue.enqueue.call_args
        assert kwargs['job_timeout'] == WEB_BACKEND_CONFIG.defaults.job_timeout_seconds
        if WEB_BACKEND_CONFIG.defaults.job_retry_max > 0:
            assert kwargs['retry'].max == WEB_BACKEND_CONFIG.defaults.job_retry_max
        else:
            assert 'retry' not in kwargs

    def test_protected_route_requires_auth(self, client: TestClient) -> None:
        response = client.get('/api/rules')
        assert response.status_code == 401

    def test_upload_fasta_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        fasta_data = b'>seq1\nATCGATCG\n>seq2\nATCGATCG\n'
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', fasta_data, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'fasta'
        assert payload['size_bytes'] == len(fasta_data)
        assert payload['file_path']
        # Verify file is in upload directory
        uploaded_path = Path(payload['file_path'])
        assert uploaded_path.exists()
        assert uploaded_path.parent == startup_config.uploads_dir
        assert uploaded_path.read_bytes() == fasta_data

    def test_upload_vcf_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        vcf_data = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('sample.vcf', vcf_data, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'vcf'
        assert payload['size_bytes'] == len(vcf_data)
        assert payload['file_path']
        # Verify file is in upload directory
        uploaded_path = Path(payload['file_path'])
        assert uploaded_path.exists()
        assert uploaded_path.parent == startup_config.uploads_dir
        assert uploaded_path.read_bytes() == vcf_data

    def test_upload_bam_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        # Minimal valid BGZF block header plus EOF payload.
        bam_data = bytes.fromhex('1f8b08040000000000ff0600424302001b000300000000000000')
        response = client.post(
            '/api/upload/bam',
            files={'file': ('sample.bam', bam_data, 'application/octet-stream')},
            headers=auth_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload['file_type'] == 'bam'
        assert payload['size_bytes'] == len(bam_data)
        assert payload['file_path']
        # Verify file is in upload directory
        uploaded_path = Path(payload['file_path'])
        assert uploaded_path.exists()
        assert uploaded_path.parent == startup_config.uploads_dir
        assert uploaded_path.read_bytes() == bam_data

    def test_upload_json_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
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
            headers=auth_headers,
        )
        assert response.status_code == 200
        upload_payload = response.json()
        assert upload_payload['file_type'] == 'json'
        uploaded_path = Path(upload_payload['file_path'])
        assert uploaded_path.is_file()
        assert uploaded_path.parent == startup_config.uploads_dir

    def test_upload_json_invalid_payload_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.post(
            '/api/upload/json',
            files={'file': ('invalid.results.json', b'{"run": {}}', 'application/json')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert 'Unsupported JSON format' in payload['detail']

    def test_upload_json_requires_auth(self, client: TestClient) -> None:
        response = client.post(
            '/api/upload/json',
            files={'file': ('sample.results.json', b'{}', 'application/json')},
        )
        assert response.status_code == 401

    def test_regenerate_from_json(
        self,
        client: TestClient,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit_profile = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'regen-web-vcf',
            },
            headers=auth_headers,
        )
        assert submit_profile.status_code == 200
        profile_job_id = submit_profile.json()['job_id']

        profile_status = client.get(f'/api/jobs/{profile_job_id}', headers=auth_headers)
        assert profile_status.status_code == 200
        profile_payload = profile_status.json()
        assert profile_payload['status'] == 'succeeded'
        json_path = profile_payload['result']['report_json_path']

        submit_regen = client.post(
            '/api/regenerate/json',
            json={'json_path': json_path},
            headers=auth_headers,
        )
        assert submit_regen.status_code == 200
        regen_job_id = submit_regen.json()['job_id']

        regen_status = client.get(f'/api/jobs/{regen_job_id}', headers=auth_headers)
        assert regen_status.status_code == 200
        regen_payload = regen_status.json()
        assert regen_payload['status'] == 'succeeded'
        result = regen_payload['result']
        assert result['mode'] == 'regenerate-json'
        assert result['report_html_path'].endswith('.report.html')
        assert result['report_json_path'].endswith('.results.json')
        assert result['report_pdf_path'].endswith('.report.pdf')
        assert Path(result['report_html_path']).is_file()
        assert Path(result['report_json_path']).is_file()
        assert Path(result['report_pdf_path']).is_file()

    def test_regenerate_from_json_uuid_mismatch_fails(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit_profile = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(web_sample_vcf),
                'ref_fasta_path': str(web_sample_ref_fasta),
                'sample': 'regen-web-vcf-mismatch',
            },
            headers=auth_headers,
        )
        assert submit_profile.status_code == 200
        profile_job_id = submit_profile.json()['job_id']
        profile_status = client.get(f'/api/jobs/{profile_job_id}', headers=auth_headers)
        assert profile_status.status_code == 200
        profile_payload = profile_status.json()
        assert profile_payload['status'] == 'succeeded'

        source_json = Path(profile_payload['result']['report_json_path'])
        tampered_json = startup_config.uploads_dir / 'tampered.results.json'
        tampered_payload = json.loads(source_json.read_text(encoding='utf-8'))
        tampered_payload['run']['project_fingerprint'] = 'mismatching-uuid'
        tampered_json.write_text(json.dumps(tampered_payload), encoding='utf-8')

        submit_regen = client.post(
            '/api/regenerate/json',
            json={'json_path': str(tampered_json)},
            headers=auth_headers,
        )
        assert submit_regen.status_code == 200
        regen_status = client.get(f"/api/jobs/{submit_regen.json()['job_id']}", headers=auth_headers)
        assert regen_status.status_code == 200
        payload = regen_status.json()
        assert payload['status'] == 'failed'
        assert 'UUID mismatch' in payload['error']

    def test_regenerate_json_requires_auth(self, client: TestClient) -> None:
        response = client.post('/api/regenerate/json', json={'json_path': '/tmp/foo.results.json'})
        assert response.status_code == 401

    def test_upload_fasta_with_empty_file_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('empty.fasta', b'', 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert 'empty' in payload['detail'].lower()

    def test_upload_vcf_with_invalid_content_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_vcf = b'this is not a valid vcf file\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'

    def test_upload_fasta_with_binary_content_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_fasta = b'>seq\nATCG\x00ATCG\n'
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('invalid.fasta', invalid_fasta, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported FASTA format. Upload a plain-text FASTA file.'

    def test_upload_vcf_without_chrom_header_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_vcf = b'##fileformat=VCFv4.2\n1\t10\t.\tA\tG\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'

    def test_upload_vcf_with_binary_content_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_vcf = b'##fileformat=VCFv4.2\n#CHROM\tPOS\x00\n'
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Upload a plain-text VCF file.'

    def test_upload_vcf_with_excessive_line_length_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        long_alt = 'A' * 100_001
        invalid_vcf = f'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n1\t10\t.\tA\t{long_alt}\n'.encode()
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('invalid.vcf', invalid_vcf, 'text/plain')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported VCF format. Input contains an excessively long line.'

    def test_upload_bam_with_invalid_magic_bytes_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_bam = b'\xff\xff' + b'\x00' * 100
        response = client.post(
            '/api/upload/bam',
            files={'file': ('invalid.bam', invalid_bam, 'application/octet-stream')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

    def test_upload_bam_with_invalid_bgzf_structure_rejected(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        invalid_bam = b'\x1f\x8b\x08\x00' + b'\x00' * 64
        response = client.post(
            '/api/upload/bam',
            files={'file': ('invalid-structure.bam', invalid_bam, 'application/octet-stream')},
            headers=auth_headers,
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload['detail'] == 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

    def test_upload_fasta_requires_auth(self, client: TestClient) -> None:
        response = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )
        assert response.status_code == 401

    def test_upload_vcf_requires_auth(self, client: TestClient) -> None:
        response = client.post(
            '/api/upload/vcf',
            files={'file': ('sample.vcf', b'##fileformat=VCFv4.2\n', 'text/plain')},
        )
        assert response.status_code == 401

    def test_upload_rate_limit_applies_per_ip_without_token(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1/minute')
        no_token_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            api_token='',
        )
        client = TestClient(create_app(startup_config=no_token_config))

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

    def test_upload_rate_limit_applies_per_token(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1/minute')
        no_token_config = StartupConfig(
            project_databases_dir=startup_config.project_databases_dir,
            uploads_dir=startup_config.uploads_dir,
            results_dir=startup_config.results_dir,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            api_token='',
        )
        client = TestClient(create_app(startup_config=no_token_config))

        first = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
            headers={'Authorization': 'Bearer token-a'},
        )
        second = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
            headers={'Authorization': 'Bearer token-a'},
        )
        third = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
            headers={'Authorization': 'Bearer token-b'},
        )

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()['detail'] == 'Upload rate limit exceeded. Try again later.'
        assert third.status_code == 429

    def test_ui_config_uses_env_override_for_max_batch_size(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '7')
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)

        response = client.get('/api/ui/config', headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()['data']
        assert payload['batch_max_samples'] == 7
        assert payload['sample_limit_per_minute'] == 7

    def test_ui_config_requires_auth_when_api_token_is_set(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = TestClient(create_app(startup_config=startup_config))

        response = client.get('/api/ui/config')

        assert response.status_code == 401

    def test_open_report_rejects_paths_outside_results_dir(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        upload_path = startup_config.uploads_dir / 'not-a-report.report.html'
        upload_path.write_text('<html><body>not allowed</body></html>')

        response = client.get('/api/report', params={'path': str(upload_path)}, headers=auth_headers)

        assert response.status_code == 400
        assert response.json()['detail'] == 'Report path is outside allowed output directory.'

    def test_open_report_rejects_non_report_html_types(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        report_path = startup_config.results_dir / 'not-a-report.html'
        report_path.write_text('<html><body>wrong suffix</body></html>')

        response = client.get('/api/report', params={'path': str(report_path)}, headers=auth_headers)

        assert response.status_code == 400
        assert response.json()['detail'] == 'Unsupported report type. Allowed: .report.html.'

    def test_session_cleanup_deletes_uploaded_and_report_files(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        upload_response = client.post(
            '/api/upload/fasta',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
            headers=auth_headers,
        )
        assert upload_response.status_code == 200
        uploaded_path = Path(upload_response.json()['file_path'])
        assert uploaded_path.exists()

        report_path = startup_config.results_dir / 'session-result.report.html'
        report_path.write_text('<html><body>report</body></html>')
        assert report_path.exists()

        cleanup_response = client.post(
            '/api/session/cleanup',
            json={
                'upload_paths': [str(uploaded_path)],
                'report_paths': [str(report_path)],
            },
            headers=auth_headers,
        )
        assert cleanup_response.status_code == 200
        assert cleanup_response.json()['deleted_count'] == 2
        assert not uploaded_path.exists()
        assert not report_path.exists()

    def test_session_cleanup_deletes_generated_bam_index_sidecar(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
    ) -> None:
        bam_data = bytes.fromhex('1f8b08040000000000ff0600424302001b000300000000000000')
        upload_response = client.post(
            '/api/upload/bam',
            files={'file': ('sample.bam', bam_data, 'application/octet-stream')},
            headers=auth_headers,
        )
        assert upload_response.status_code == 200
        bam_path = Path(upload_response.json()['file_path'])
        assert bam_path.exists()

        bam_index_path = bam_path.with_suffix('.bam.bai')
        bam_index_path.write_bytes(b'index')
        assert bam_index_path.exists()
        assert bam_index_path.parent == startup_config.uploads_dir

        cleanup_response = client.post(
            '/api/session/cleanup',
            json={
                'upload_paths': [str(bam_path)],
                'report_paths': [],
            },
            headers=auth_headers,
        )
        assert cleanup_response.status_code == 200
        assert cleanup_response.json()['deleted_count'] == 2
        assert not bam_path.exists()
        assert not bam_index_path.exists()


# ---------------------------------------------------------------------------
# Batch profiling endpoint tests
# ---------------------------------------------------------------------------


class TestBatchProfileEndpoints:
    def test_batch_vcf_submit_success(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        project_db: Path,
        auth_headers: dict[str, str],
    ) -> None:
        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_paths': [str(web_sample_vcf), str(vcf2)],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['batch-a.vcf', 'batch-b.vcf'],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
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
        auth_headers: dict[str, str],
    ) -> None:
        fasta2 = startup_config.uploads_dir / 'sample2.fasta'
        fasta2.write_text(web_sample_ref_fasta.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_paths': [str(web_sample_ref_fasta), str(fasta2)],
                'sample_names': ['fasta-a', 'fasta-b'],
                'input_display_names': ['batch-a.fasta', 'batch-b.fasta'],
                'db_path': default_db.name,
            },
            headers=auth_headers,
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
        auth_headers: dict[str, str],
    ) -> None:
        vcf2 = startup_config.uploads_dir / 'sample2.vcf'
        vcf2.write_text(web_sample_vcf.read_text())
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        submit = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_paths': [str(web_sample_vcf), str(vcf2)],
                'sample_names': ['sample-a', 'sample-b'],
                'input_display_names': ['duplicate-name.vcf', 'duplicate-name.vcf'],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
        )
        assert submit.status_code == 200
        submitted = submit.json()['samples']

        result_paths: list[str] = []
        for sample in submitted:
            status = client.get(f"/api/jobs/{sample['job_id']}", headers=auth_headers)
            assert status.status_code == 200
            payload = status.json()
            assert payload['status'] == 'succeeded'
            result_paths.append(payload['result']['report_json_path'])

        bundle = client.post(
            '/api/artifact-bundle',
            json={'paths': result_paths},
            headers=auth_headers,
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
        auth_headers: dict[str, str],
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        vcf_path = str(web_sample_vcf)
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_paths': [vcf_path] * 26,
                'sample_names': [f'sample-{i}' for i in range(26)],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
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
        auth_headers: dict[str, str],
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_paths': [str(web_sample_vcf), str(web_sample_vcf)],
                'sample_names': ['only-one'],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_batch_vcf_max_batch_size_uses_env_override(
        self,
        startup_config: StartupConfig,
        sync_queue: Queue,
        web_sample_vcf: Path,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        app = create_app(startup_config=startup_config)
        app.dependency_overrides[get_queue] = lambda: sync_queue
        app.dependency_overrides[get_batch_queue] = lambda: sync_queue
        client = TestClient(app)
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]

        response = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_paths': [str(web_sample_vcf), str(web_sample_vcf)],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422
        assert 'maximum of 1 samples per batch' in response.json()['detail']

    def test_batch_fasta_exceeds_max_size(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        web_sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        fasta_path = str(web_sample_ref_fasta)
        response = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_paths': [fasta_path] * 26,
                'sample_names': [f'fasta-{i}' for i in range(26)],
                'db_path': default_db.name,
            },
            headers=auth_headers,
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
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        missing_vcf = startup_config.uploads_dir / 'missing.vcf'
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
                'vcf_paths': [str(web_sample_vcf), str(missing_vcf)],
                'sample_names': ['sample-a', 'sample-b'],
                'reference_fasta_path': str(web_sample_ref_fasta),
                'db_path': default_db.name,
            },
            headers=auth_headers,
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
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        missing_fasta = startup_config.uploads_dir / 'missing.fasta'
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
                'fasta_paths': [str(web_sample_ref_fasta), str(missing_fasta)],
                'sample_names': ['fasta-a', 'fasta-b'],
                'db_path': default_db.name,
            },
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "FASTA file not found for sample 'fasta-b'." in response.json()['detail']
        assert enqueue_calls == 0

    def test_batch_fasta_sample_quota_uses_default_redis_url_when_env_missing(
        self,
        client: TestClient,
        startup_config: StartupConfig,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0]
        missing_fasta = startup_config.uploads_dir / 'missing.fasta'
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
                'fasta_paths': [str(missing_fasta)],
                'sample_names': ['fasta-a'],
                'db_path': default_db.name,
            },
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert redis_urls
        assert redis_urls[-1] == WEB_BACKEND_CONFIG.defaults.redis_url

