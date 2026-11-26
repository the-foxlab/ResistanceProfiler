"""Stage 2/3 security, middleware, confinement, and lifecycle regression tests (M2B)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import fakeredis
import pytest
import redis
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from rq import Queue
from rq.serializers import JSONSerializer

from tests.conftest import MINIMAL_BAM_BYTES, TINY_REF_NAME, TINY_REF_SEQ
from tests.test_web_api import startup_config as startup_config  # noqa: F401
from web.backend import main as main_module
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.main import create_app
from web.backend.queue import get_batch_queue, get_queue
from web.backend.rate_limit import _SAMPLE_QUOTA_COUNTER
from web.backend.services import session as session_service
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    SessionBackendError,
    hash_session_token,
    record_artifact,
    record_upload,
    reset_memory_stores,
)
from web.backend.startup_config import ImprintConfig, StartupConfig


@pytest.fixture(autouse=True)
def _reset_state_each_test(monkeypatch: pytest.MonkeyPatch) -> Any:
    reset_memory_stores()
    _SAMPLE_QUOTA_COUNTER.clear()
    monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'false')
    yield
    reset_memory_stores()
    _SAMPLE_QUOTA_COUNTER.clear()


def _make_client(
    config: StartupConfig,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    connection = fakeredis.FakeRedis()
    sync_queue = Queue('profiling', connection=connection, is_async=False, serializer=JSONSerializer)
    app = create_app(startup_config=config)
    app.dependency_overrides[get_queue] = lambda: sync_queue
    app.dependency_overrides[get_batch_queue] = lambda: sync_queue
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _establish_session(client: TestClient) -> str:
    resp = client.post('/api/session/cleanup', json={'upload_ids': [], 'artifact_ids': []})
    assert resp.status_code == 200, resp.text
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    return hash_session_token(token)


class TestPreParseMultipartAdmission:
    """S2 / WA-014: Streaming multipart upload admission before Starlette spools."""

    @pytest.mark.parametrize(
        ('endpoint', 'limit_key', 'filename'),
        [
            ('/api/upload/fasta', '/api/upload/fasta', 'big.fasta'),
            ('/api/upload/vcf', '/api/upload/vcf', 'big.vcf'),
            ('/api/upload/json', '/api/upload/json', 'big.json'),
            ('/api/upload/bam', '/api/upload/bam', 'big.bam'),
        ],
    )
    def test_oversized_uploads_rejected_before_save_upload_stream(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
        limit_key: str,
        filename: str,
    ) -> None:
        save_calls = 0

        async def _spy_save(*_args: Any, **_kwargs: Any) -> tuple[Path, int]:
            nonlocal save_calls
            save_calls += 1
            raise AssertionError('save_upload_stream should not run for rejected oversized upload')

        monkeypatch.setattr('web.backend.routes.upload.save_upload_stream', _spy_save)
        monkeypatch.setitem(main_module._UPLOAD_LIMITS_BY_PATH, limit_key, 64)
        client = _make_client(startup_config)

        resp = client.post(
            endpoint,
            files={'file': (filename, b'A' * 128, 'application/octet-stream')},
        )
        assert resp.status_code == 413
        assert save_calls == 0
        assert list(startup_config.uploads_dir.iterdir()) == []

    def test_declared_content_length_over_1gib_bam_limit_rejected_early(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        save_calls = 0

        async def _spy_save(*_args: Any, **_kwargs: Any) -> tuple[Path, int]:
            nonlocal save_calls
            save_calls += 1
            raise AssertionError('save_upload_stream should not run')

        monkeypatch.setattr('web.backend.routes.upload.save_upload_stream', _spy_save)
        client = _make_client(startup_config)
        too_big = WEB_BACKEND_CONFIG.defaults.upload_max_bam_size + (128 * 1024)
        resp = client.post(
            '/api/upload/bam',
            content=b'not-even-sent',
            headers={
                'Content-Type': 'multipart/form-data; boundary=testbound',
                'Content-Length': str(too_big),
            },
        )
        assert resp.status_code == 413
        assert save_calls == 0

    def test_multipart_multiple_parts_extra_fields_and_bad_content_type_rejected_early(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        save_calls = 0

        async def _spy_save(*_args: Any, **_kwargs: Any) -> tuple[Path, int]:
            nonlocal save_calls
            save_calls += 1
            raise AssertionError('save_upload_stream should not run')

        monkeypatch.setattr('web.backend.routes.upload.save_upload_stream', _spy_save)
        client = _make_client(startup_config)

        two_files = client.post(
            '/api/upload/fasta',
            files=[
                ('file', ('a.fa', b'>s\nACGT\n', 'text/plain')),
                ('file', ('b.fa', b'>s\nACGT\n', 'text/plain')),
            ],
        )
        extra_field = client.post(
            '/api/upload/fasta',
            data={'extra': '1'},
            files={'file': ('a.fa', b'>s\nACGT\n', 'text/plain')},
        )
        wrong_field = client.post(
            '/api/upload/fasta',
            files={'other': ('a.fa', b'>s\nACGT\n', 'text/plain')},
        )
        bad_ct = client.post(
            '/api/upload/fasta',
            content=b'>s\nACGT\n',
            headers={'Content-Type': 'application/json'},
        )
        for resp in (two_files, extra_field, wrong_field, bad_ct):
            assert resp.status_code == 400
        assert save_calls == 0
        assert list(startup_config.uploads_dir.iterdir()) == []

    def test_slowapi_upload_rate_limit_still_enforced_under_admission_middleware(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1/minute')
        client = _make_client(startup_config)

        first = client.post(
            '/api/upload/fasta',
            files={'file': ('a.fa', f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'.encode(), 'text/plain')},
        )
        assert first.status_code == 200
        second = client.post(
            '/api/upload/fasta',
            files={'file': ('b.fa', f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'.encode(), 'text/plain')},
        )
        assert second.status_code == 429
        assert int(second.headers['retry-after']) >= 1


class TestCrossTypeAndRootConfinement:
    """WA-004 & WA-033: Expected file_type enforcement and narrowed allowed_roots."""

    def test_cross_type_upload_ids_rejected_with_400_on_all_profile_and_regenerate_routes(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = _make_client(startup_config)
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0].name
        fasta_id = client.post(
            '/api/upload/fasta',
            files={'file': ('s.fa', f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'.encode(), 'text/plain')},
        ).json()['upload_id']
        vcf_id = client.post(
            '/api/upload/vcf',
            files={'file': ('v.vcf', b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n', 'text/plain')},
        ).json()['upload_id']
        bam_id = client.post(
            '/api/upload/bam',
            files={'file': ('b.bam', MINIMAL_BAM_BYTES, 'application/octet-stream')},
        ).json()['upload_id']

        responses = [
            client.post('/api/profile/fasta', json={'fasta_id': vcf_id, 'database_id': default_db}),
            client.post('/api/profile/vcf', json={'vcf_id': fasta_id, 'reference_id': fasta_id, 'database_id': default_db}),
            client.post('/api/profile/vcf', json={'vcf_id': vcf_id, 'reference_id': vcf_id, 'database_id': default_db}),
            client.post('/api/profile/vcf', json={'vcf_id': vcf_id, 'reference_id': fasta_id, 'bam_id': fasta_id, 'database_id': default_db}),
            client.post('/api/profile/batch/fasta', json={'fasta_ids': [bam_id], 'sample_names': ['S1'], 'db_path': default_db}),
            client.post('/api/profile/batch/vcf', json={'vcf_ids': [fasta_id], 'reference_id': fasta_id, 'sample_names': ['S1'], 'db_path': default_db}),
            client.post('/api/profile/batch/vcf', json={'vcf_ids': [vcf_id], 'reference_id': fasta_id, 'bam_ids': [vcf_id], 'sample_names': ['S1'], 'db_path': default_db}),
            client.post('/api/regenerate/json', json={'json_id': bam_id, 'database_id': default_db}),
            client.post('/api/regenerate/batch', json={'json_ids': [fasta_id], 'sample_names': ['S1'], 'database_id': default_db}),
        ]
        for resp in responses:
            assert resp.status_code == 400, resp.text
            assert str(startup_config.data_dir) not in resp.text

    def test_upload_in_results_dir_and_artifact_in_uploads_dir_are_rejected_400(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = _make_client(startup_config)
        session_hash = _establish_session(client)
        default_db = sorted(startup_config.project_databases_dir.glob('*.db'))[0].name

        misplaced_upload = startup_config.results_dir / 'wrong_root.fasta'
        misplaced_upload.write_text(f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n')
        bad_upload_id = record_upload(
            session_hash=session_hash, canonical_path=misplaced_upload, file_type='fasta',
        )

        misplaced_artifact = startup_config.uploads_dir / 'wrong_root.report.html'
        misplaced_artifact.write_text('<html></html>')
        bad_artifact_id = record_artifact(
            session_hash=session_hash, canonical_path=misplaced_artifact, media_type='text/html',
        )

        assert client.post('/api/profile/fasta', json={'fasta_id': bad_upload_id, 'database_id': default_db}).status_code == 400
        assert client.get('/api/report', params={'artifact_id': bad_artifact_id}).status_code == 400
        assert client.get('/api/artifact', params={'artifact_id': bad_artifact_id}).status_code == 400

    def test_compare_confines_project_db_even_under_custom_allowed_roots(
        self,
        startup_config: StartupConfig,
        tmp_path: Path,
    ) -> None:
        outside_db = tmp_path / 'outside.db'
        shutil_conn = sqlite3.connect(outside_db)
        shutil_conn.execute('CREATE TABLE reference (id INTEGER PRIMARY KEY, name TEXT, accession TEXT, organism TEXT)')
        shutil_conn.execute('CREATE TABLE feature (id INTEGER PRIMARY KEY, reference_id INTEGER, name TEXT, protein TEXT, feature_type TEXT)')
        shutil_conn.execute("INSERT INTO reference VALUES (1, 'Ref1', 'A1', 'Virus')")
        shutil_conn.execute("INSERT INTO feature VALUES (1, 1, 'f1', 'LEAKED_DB_PROTEIN', 'mat_peptide')")
        shutil_conn.commit()
        shutil_conn.close()

        custom_config = replace(startup_config, allowed_roots=(tmp_path,))
        client = _make_client(custom_config)
        session_hash = _establish_session(client)

        res_json = startup_config.results_dir / 'cmp.20261124000001000000.results.json'
        res_json.write_text(
            json.dumps({
                'run': {
                    'sample_name': 'S1',
                    'project_name': 'Demo',
                    'project_db_path': str(outside_db),
                    'reference_name': 'Ref1',
                    'references': [{'reference_name': 'Ref1'}],
                },
                'variant_result': [
                    {'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.5, 'rule_match': 1, 'consequence': 'missense'},
                ],
                'coverage_gap': [],
                'formula_rule_hit': [],
                'sample_classification': [],
            }),
            encoding='utf-8',
        )
        art_id = record_artifact(session_hash=session_hash, canonical_path=res_json, media_type='application/json')
        resp = client.post('/api/compare', json={'artifact_ids': [art_id]})
        assert resp.status_code in (200, 400)
        assert 'LEAKED_DB_PROTEIN' not in resp.text


class TestSessionBackendOutageAndUploadCleanup:
    """WA-005: Online-mode Redis outage -> 503 and upload file cleanup."""

    def test_online_redis_outage_returns_503_on_stateful_routes_while_health_stays_200(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_service, '_redis_connection', lambda: None)
        online_config = replace(startup_config, deployment_mode='online')
        client = _make_client(online_config, raise_server_exceptions=False)

        assert client.get('/api/health').status_code == 200
        assert client.get('/api/databases').status_code == 503
        assert client.post('/api/session/cleanup', json={'upload_ids': [], 'artifact_ids': []}).status_code == 503

    def test_upload_cleanup_unlinks_saved_file_on_record_upload_error(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _make_client(startup_config, raise_server_exceptions=False)
        _establish_session(client)

        def _raise_backend(**_kwargs: Any) -> str:
            raise SessionBackendError('Session store unavailable.')

        monkeypatch.setattr('web.backend.routes.upload.record_upload', _raise_backend)
        resp_503 = client.post(
            '/api/upload/fasta',
            files={'file': ('a.fa', f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'.encode(), 'text/plain')},
        )
        assert resp_503.status_code == 503
        assert list(startup_config.uploads_dir.iterdir()) == []

        def _raise_runtime(**_kwargs: Any) -> str:
            raise RuntimeError(f'disk failure in {startup_config.uploads_dir}')

        monkeypatch.setattr('web.backend.routes.upload.record_upload', _raise_runtime)
        resp_500 = client.post(
            '/api/upload/fasta',
            files={'file': ('b.fa', f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'.encode(), 'text/plain')},
        )
        assert resp_500.status_code == 500
        assert resp_500.json()['detail'] == 'The upload failed on the server.'
        assert str(startup_config.uploads_dir) not in resp_500.text
        assert list(startup_config.uploads_dir.iterdir()) == []


class TestStatelessExemptionCorsAndCookies:
    """WA-008 & W5/WA-043: Outermost CORS, stateless exemptions, and Set-Cookie appending."""

    def test_cors_outermost_wraps_inner_errors_and_uses_explicit_methods_headers(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://app.example.org')
        monkeypatch.setattr(session_service, '_redis_connection', lambda: None)
        online_config = replace(startup_config, deployment_mode='online')
        client = _make_client(online_config, raise_server_exceptions=False)

        preflight = client.options(
            '/api/databases',
            headers={
                'Origin': 'https://app.example.org',
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Content-Type',
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get('access-control-allow-origin') == 'https://app.example.org'
        assert preflight.headers.get('access-control-allow-credentials') != 'true'
        allow_methods = {m.strip() for m in preflight.headers.get('access-control-allow-methods', '').split(',')}
        assert allow_methods == {'GET', 'POST', 'DELETE', 'OPTIONS'}
        assert '*' not in preflight.headers.get('access-control-allow-methods', '')

        err_503 = client.get('/api/databases', headers={'Origin': 'https://app.example.org'})
        assert err_503.status_code == 503
        assert err_503.headers.get('access-control-allow-origin') == 'https://app.example.org'

    def test_stateless_routes_mint_no_sessions_and_stateful_routes_append_cookies(
        self,
        startup_config: StartupConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)

        dist_dir = tmp_path / 'dist'
        (dist_dir / 'assets').mkdir(parents=True)
        (dist_dir / 'index.html').write_text('<!doctype html><html></html>', encoding='utf-8')
        (dist_dir / 'theme-init.js').write_text('console.log(1);', encoding='utf-8')
        (dist_dir / 'assets' / 'app.js').write_text('console.log(2);', encoding='utf-8')
        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: dist_dir)

        cfg = replace(startup_config, imprint=ImprintConfig(kind='path', html='<p>Imprint</p>'))
        app = create_app(startup_config=cfg)

        @app.get('/api/_test_extra_cookie')
        def _extra_cookie() -> JSONResponse:
            resp = JSONResponse({'ok': True})
            resp.set_cookie('extra_cookie', 'val')
            return resp

        app.router.routes.insert(0, app.router.routes.pop())

        client = TestClient(app)
        for method, path in (
            ('OPTIONS', '/api/databases'),
            ('GET', '/api/health'),
            ('GET', '/api/readiness'),
            ('GET', '/api/ui/config'),
            ('GET', '/api/ui/legal'),
            ('GET', '/api/ui/contact'),
            ('GET', '/api/branding/logo.svg'),
            ('GET', '/legal'),
            ('GET', '/theme-init.js'),
            ('GET', '/assets/app.js'),
            ('GET', '/'),
            ('GET', '/api/does-not-exist'),
        ):
            r = client.request(method, path)
            assert SESSION_COOKIE_NAME not in r.headers.get('set-cookie', ''), f'{method} {path} set cookie'

        assert list(fake_client.scan_iter('respro:session:*')) == []

        extra = client.get('/api/_test_extra_cookie')
        assert extra.status_code == 200
        cookies = extra.headers.get_list('set-cookie')
        assert any('extra_cookie=val' in c for c in cookies)
        assert any(f'{SESSION_COOKIE_NAME}=' in c for c in cookies)


class TestSecurityHeadersCspAndOriginCsrf:
    """WA-009, WA-011, S5-B9: CSP policies, theme-init.js, assets guard, CSRF, and 500 handler."""

    def test_dist_without_assets_dir_boots_and_serves_theme_init_js(
        self,
        startup_config: StartupConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        dist_dir = tmp_path / 'dist_no_assets'
        dist_dir.mkdir()
        (dist_dir / 'index.html').write_text('<!doctype html><html><body>SPA</body></html>', encoding='utf-8')
        (dist_dir / 'theme-init.js').write_text('window.__theme=1;', encoding='utf-8')
        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: dist_dir)

        cfg = replace(startup_config, imprint=ImprintConfig(kind='path', html='<p>Legal</p>'))
        client = _make_client(cfg)

        theme = client.get('/theme-init.js')
        assert theme.status_code == 200
        assert 'javascript' in theme.headers.get('content-type', '').lower()
        assert 'window.__theme=1;' in theme.text

        spa = client.get('/')
        assert spa.status_code == 200
        spa_csp = spa.headers.get('content-security-policy', '')
        assert "script-src 'self'" in spa_csp
        assert "script-src 'self' 'unsafe-inline'" not in spa_csp
        assert 'https://uni-freiburg.de' in spa_csp
        assert 'https://*.uni-freiburg.de' in spa_csp
        assert 'https://www.uniklinik-freiburg.de' in spa_csp
        assert 'https://pubchem.ncbi.nlm.nih.gov' in spa_csp
        assert spa.headers.get('x-content-type-options') == 'nosniff'
        assert spa.headers.get('referrer-policy') == 'no-referrer'
        assert spa.headers.get('x-frame-options') == 'DENY'

        legal = client.get('/legal')
        assert legal.status_code == 200
        assert "script-src 'none'" in legal.headers.get('content-security-policy', '')
        assert "frame-ancestors 'none'" in legal.headers.get('content-security-policy', '')
        assert legal.headers.get('x-frame-options') == 'DENY'

    def test_report_route_emits_sameorigin_and_strict_report_csp(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = _make_client(startup_config)
        session_hash = _establish_session(client)
        report = startup_config.results_dir / 'sample.report.html'
        report.write_text('<html><body>Report</body></html>', encoding='utf-8')
        art_id = record_artifact(session_hash=session_hash, canonical_path=report, media_type='text/html')

        resp = client.get('/api/report', params={'artifact_id': art_id})
        assert resp.status_code == 200
        assert resp.headers.get('x-frame-options') == 'SAMEORIGIN'
        csp = resp.headers.get('content-security-policy', '')
        for token in (
            "frame-ancestors 'self'",
            'connect-src https://translate.googleapis.com',
            "form-action 'none'",
            "base-uri 'none'",
            "object-src 'none'",
            'https://pubchem.ncbi.nlm.nih.gov',
        ):
            assert token in csp
        assert 'connect-src https://translate.googleapis.com' in csp
        assert "connect-src 'self'" not in csp

    def test_rejected_upload_admission_does_not_mint_session(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)
        client = _make_client(startup_config)

        bad_ct = client.post('/api/upload/fasta', content=b'>s\nACGT\n', headers={'Content-Type': 'text/plain'})
        assert bad_ct.status_code == 400
        assert 'set-cookie' not in bad_ct.headers
        assert list(fake_client.scan_iter('respro:session:*')) == []

        oversized = client.post(
            '/api/upload/vcf',
            content=b'',
            headers={
                'Content-Type': 'multipart/form-data; boundary=abc',
                'Content-Length': str(10 * 1024 * 1024),
            },
        )
        assert oversized.status_code == 413
        assert 'set-cookie' not in oversized.headers
        assert list(fake_client.scan_iter('respro:session:*')) == []

    def test_readiness_returns_503_on_malformed_redis_url(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _make_client(startup_config)
        monkeypatch.setenv('REDIS_URL', 'not-a-valid-redis-url')
        resp = client.get('/api/readiness')
        assert resp.status_code == 503
        assert 'redis_unreachable' in resp.json()['data']['diagnostics']

    def test_online_csrf_blocks_cross_site_and_foreign_origin_but_allows_same_origin(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_client = fakeredis.FakeStrictRedis()
        monkeypatch.setattr(session_service, '_redis_connection', lambda: fake_client)
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        online_config = replace(startup_config, deployment_mode='online')
        client = _make_client(online_config)

        for bad_headers in (
            {'Sec-Fetch-Site': 'cross-site'},
            {'Sec-Fetch-Site': 'same-site'},
            {'Origin': 'https://evil.example'},
            {'Origin': 'null'},
            {'Referer': 'https://evil.example/attack'},
        ):
            resp = client.post(
                '/api/session/cleanup',
                json={'upload_ids': [], 'artifact_ids': []},
                headers=bad_headers,
            )
            assert resp.status_code == 403, f'Expected 403 for {bad_headers}, got {resp.status_code}'

        ok = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            headers={
                'Host': 'respro.example.org',
                'X-Forwarded-Proto': 'https',
                'Origin': 'https://respro.example.org',
                'Sec-Fetch-Site': 'same-origin',
            },
        )
        assert ok.status_code == 200
        assert client.get('/api/databases', headers={'Origin': 'https://evil.example'}).status_code == 200

    def test_unhandled_exception_handler_returns_static_generic_500(
        self,
        startup_config: StartupConfig,
    ) -> None:
        app = create_app(startup_config=startup_config)

        @app.get('/api/_test_boom')
        def _boom() -> dict[str, str]:
            raise RuntimeError(f'secret crash at {startup_config.data_dir}')

        app.router.routes.insert(0, app.router.routes.pop())

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get('/api/_test_boom')
        assert resp.status_code == 500
        assert resp.json() == {'detail': 'Internal server error.'}
        assert str(startup_config.data_dir) not in resp.text


class TestReadinessRedactionAndCatalogSanitization:
    """WA-010, WA-011, WA-034, WA-032, S5-B3."""

    def test_readiness_redacts_redis_credentials_in_logs(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = 'very-secret-pw-1234'
        redis_url = f'redis://default:{secret}@127.0.0.1:6379/0'
        monkeypatch.setenv('REDIS_URL', redis_url)

        mock_client = Mock()
        mock_client.ping.side_effect = redis.ConnectionError(f'Connection failed to {redis_url}')
        monkeypatch.setattr('web.backend.routes.health.redis.Redis.from_url', lambda *_a, **_kw: mock_client)

        client = _make_client(startup_config)
        with caplog.at_level(logging.DEBUG, logger='web.backend.routes.health'):
            resp = client.get('/api/readiness')
        assert resp.status_code == 503
        assert secret not in caplog.text
        assert '***' in caplog.text

    def test_catalog_sanitizes_sqlite_and_os_errors(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _make_client(startup_config)

        def _raise_sqlite(*_a: Any, **_kw: Any) -> dict[str, Any]:
            raise sqlite3.OperationalError(f'no such table in {startup_config.project_databases_dir}/secret.db')

        monkeypatch.setattr('web.backend.routes.catalog.list_rules', _raise_sqlite)
        resp = client.get('/api/rules')
        assert resp.status_code == 400
        assert resp.json()['detail'] == 'Project database is unavailable or corrupt.'
        assert str(startup_config.project_databases_dir) not in resp.text

    def test_query_and_path_max_length_bounds_reject_oversized_inputs_422(
        self,
        startup_config: StartupConfig,
    ) -> None:
        client = _make_client(startup_config)
        too_long_id = 'a' * 257
        too_long_ids = 'db.db,' * 800

        assert client.get('/api/report', params={'artifact_id': too_long_id}).status_code == 422
        assert client.get('/api/artifact', params={'artifact_id': too_long_id}).status_code == 422
        assert client.get(f'/api/jobs/{too_long_id}').status_code == 422
        assert client.delete(f'/api/jobs/{too_long_id}').status_code == 422
        assert client.get('/api/rules', params={'database_id': too_long_id}).status_code == 422
        assert client.get('/api/rules', params={'reference': too_long_id}).status_code == 422
        assert client.get('/api/mutations', params={'database_id': too_long_id}).status_code == 422
        assert client.get('/api/databases/shared-references', params={'ids': too_long_ids}).status_code == 422
        assert client.get('/api/databases/reference-accessions', params={'ids': too_long_ids}).status_code == 422

    @pytest.mark.parametrize('bad_port', ['0', '65536', '-1', 'abc'])
    def test_resolve_web_port_rejects_invalid_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad_port: str,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_PORT', bad_port)
        with pytest.raises(ValueError):
            main_module._resolve_web_port()


class TestLifespanGracefulShutdown:
    """WA-018: Lifespan starts background threads and stops/joins them on shutdown."""

    def test_lifespan_signals_stop_event_and_joins_background_threads(
        self,
        startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '3600')
        before = set(threading.enumerate())
        app = create_app(startup_config=startup_config)
        with TestClient(app) as client:
            assert client.get('/api/health').status_code == 200
            live_bg = [
                t
                for t in threading.enumerate()
                if t not in before
                and t.is_alive()
                and 'portal' not in t.name.lower()
                and 'anyio' not in t.name.lower()
            ]
            assert len(live_bg) >= 2
        after = [t for t in threading.enumerate() if t not in before and t.is_alive()]
        assert after == []
