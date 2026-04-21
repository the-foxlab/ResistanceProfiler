"""API tests for the web backend."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue

from web.backend.main import create_app
from web.backend.queue import get_queue
from web.backend.startup_config import StartupConfig


@pytest.fixture()
def sync_queue():
    """An in-process RQ queue backed by fakeredis that executes jobs synchronously."""
    connection = fakeredis.FakeRedis()
    return Queue('profiling', connection=connection, is_async=False)


@pytest.fixture()
def startup_config(project_db: Path, tmp_path: Path) -> StartupConfig:
    """Startup config fixture for startup-managed path mode."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    return StartupConfig(
        project_db=project_db.resolve(),
        results_db=(data_dir / 'results.db').resolve(),
        data_dir=data_dir.resolve(),
        allowed_roots=(data_dir.resolve(), project_db.parent.resolve()),
        api_token='test-token',
    )


@pytest.fixture()
def auth_headers(startup_config: StartupConfig) -> dict[str, str]:
    """Authorization header for protected API routes."""
    return {'Authorization': f'Bearer {startup_config.api_token}'}


@pytest.fixture()
def client(sync_queue: Queue, startup_config: StartupConfig):
    """TestClient with queue override and startup config injected."""
    app = create_app(startup_config=startup_config)
    app.dependency_overrides[get_queue] = lambda: sync_queue
    return TestClient(app)


class TestWebApi:
    def test_cors_uses_wildcard_when_token_is_configured(self, startup_config: StartupConfig) -> None:
        client = TestClient(create_app(startup_config=startup_config))
        response = client.options(
            '/api/health',
            headers={
                'Origin': 'https://respro.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )

        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == '*'

    def test_cors_uses_localhost_defaults_without_token(
        self,
        startup_config: StartupConfig,
    ) -> None:
        no_token_config = StartupConfig(
            project_db=startup_config.project_db,
            results_db=startup_config.results_db,
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
            project_db=startup_config.project_db,
            results_db=startup_config.results_db,
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

    def test_rules_endpoint(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        rules_response = client.get(
            '/api/rules',
            headers=auth_headers,
        )
        assert rules_response.status_code == 200
        rules = rules_response.json()['data']['items']
        assert len(rules) >= 1
        assert rules[0]['gene'] == 'gag'

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
        conn = sqlite3.connect(startup_config.project_db)
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
        sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/fasta',
            json={
                'fasta_path': str(sample_ref_fasta),
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
        assert result['sample_name'] == 'web-fasta'
        assert result['run_id'] >= 1
        assert result['report_html_path'].endswith('.report.html')
        assert Path(result['report_html_path']).is_file()

    def test_profile_vcf(
        self,
        client: TestClient,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        auth_headers: dict[str, str],
    ) -> None:
        submit = client.post(
            '/api/profile/vcf',
            json={
                'vcf_path': str(sample_vcf),
                'ref_fasta_path': str(sample_ref_fasta),
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
        assert result['sample_name'] == 'web-vcf'
        assert result['run_id'] >= 1
        assert result['report_html_path'].endswith('.report.html')
        assert Path(result['report_html_path']).is_file()

    def test_profile_vcf_reports_reference_mismatch_clearly(
        self,
        client: TestClient,
        sample_ref_fasta: Path,
        tmp_path: Path,
        auth_headers: dict[str, str],
    ) -> None:
        mismatch_vcf = tmp_path / 'mismatch.vcf'
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
                'ref_fasta_path': str(sample_ref_fasta),
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
        assert uploaded_path.parent == startup_config.data_dir / '.uploads'
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
        assert uploaded_path.parent == startup_config.data_dir / '.uploads'
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
        assert uploaded_path.parent == startup_config.data_dir / '.uploads'
        assert uploaded_path.read_bytes() == bam_data

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
            project_db=startup_config.project_db,
            results_db=startup_config.results_db,
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
            project_db=startup_config.project_db,
            results_db=startup_config.results_db,
            data_dir=startup_config.data_dir,
            allowed_roots=startup_config.allowed_roots,
            api_token='',
        )
        client = TestClient(create_app(startup_config=no_token_config))

        first = client.post(
            '/api/upload/fasta?token=token-a',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )
        second = client.post(
            '/api/upload/fasta?token=token-a',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )
        third = client.post(
            '/api/upload/fasta?token=token-b',
            files={'file': ('sample.fasta', b'>seq\nATCG\n', 'text/plain')},
        )

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()['detail'] == 'Upload rate limit exceeded. Try again later.'
        assert third.status_code == 200

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

        report_path = startup_config.data_dir / 'session-result.report.html'
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
        assert bam_index_path.parent == startup_config.data_dir / '.uploads'

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

