"""Opaque-box, requirement-driven End-to-End (E2E) web suite for ResistanceProfiler.

End-to-end cross-feature integration and real-world user workflow scenarios:
- ``TestTier3CrossFeature``: Pairwise cross-feature combinations across backend, workers, DBs, and SPA.
- ``TestTier4RealWorldScenarios``: Multi-step real-world clinical/operator workflows.

Every test runs in complete isolation using ``tmp_path``-rooted directories,
``fakeredis`` / in-memory stores, synthetic or copied SQLite DBs, git-tracked
``example_data/*``, and ``pysam``-generated BAMs, never mutating ``data/``,
``/data``, ``web/frontend/dist/``, or external Redis.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import gzip
import http.server
import io
import json
import os
import signal
import socketserver
import sqlite3
import threading
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import fakeredis
import pysam
import pytest
import redis
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from rq.serializers import JSONSerializer

from respro.db.schema import create_schema
from tests.conftest import TINY_REF_NAME, TINY_REF_SEQ
from web.backend import queue as queue_module
from web.backend import rate_limit as rate_limit_module
from web.backend import worker as worker_module
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.main import create_app
from web.backend.queue import get_batch_queue, get_queue
from web.backend.services import session as session_service
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    reset_memory_stores,
)
from web.backend.startup_config import (
    ContactEmailConfig,
    ImprintConfig,
    StartupConfig,
    build_project_db_uuid_index,
    load_startup_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Mutated tiny_ref: codon 1 AAA (K) -> GAA (E) matches TestDrug rule K2E.
MUTATED_TINY_FASTA = f'>sample_k2e\nATGGAAGCT{TINY_REF_SEQ[9:]}\n'
VALID_TINY_VCF = (
    '##fileformat=VCFv4.2\n'
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
    '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
    f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
    f'{TINY_REF_NAME}\t10\t.\tT\tC\t80\tPASS\tAF=0.30;DP=200\n'
)
VALID_REF_FASTA = f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'


# ─── Shared E2E isolation & fixture helpers ───────────────────────────────────


def _reset_redis_pool_caches() -> None:
    """Clear any module-level Redis client/pool caches across backend modules."""
    reset_memory_stores()
    rate_limit_module._SAMPLE_QUOTA_COUNTER.clear()
    for mod in (session_service, rate_limit_module, queue_module):
        for fn_name in ('reset_redis_pool', 'reset_redis_client'):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                fn()
        for attr in ('_REDIS_POOL', '_REDIS_CLIENT', '_REDIS_CLIENT_CACHE', '_REDIS_UNAVAILABLE_UNTIL', '_POOL'):
            if hasattr(mod, attr):
                setattr(mod, attr, None)


@pytest.fixture(autouse=True)
def _isolate_e2e_environment(monkeypatch: pytest.MonkeyPatch):
    """Reset process-global session/rate-limit stores and ambient env before & after every test."""
    _reset_redis_pool_caches()

    for key in (
        'REDIS_URL',
        'RESPRO_WEB_DEPLOYMENT_MODE',
        'RESPRO_WEB_TRUSTED_PROXIES',
        'RESPRO_WEB_CORS_ORIGINS',
        'RESPRO_WEB_IMPRINT',
        'RESPRO_WEB_CONTACT_EMAIL',
        'RESPRO_WEB_MAX_BATCH_SIZE',
        'RESPRO_WEB_PORT',
        'RESPRO_WEB_RESULT_TTL',
        'RESPRO_WEB_SESSION_TTL',
        'RESPRO_WEB_ALLOWED_ROOTS',
        'RESPRO_WEB_DATA_DIR',
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'false')
    monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1000/minute')
    monkeypatch.setenv('RESPRO_WEB_API_RATE_LIMIT', '1000/minute')
    yield
    _reset_redis_pool_caches()


def _patch_fakeredis(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server: fakeredis.FakeServer | None = None,
) -> tuple[fakeredis.FakeStrictRedis, fakeredis.FakeServer]:
    """Wire backend Redis pools to an isolated FakeRedis server (using built-in WATCH/MULTI fallbacks)."""
    fake_server = server or fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=fake_server)

    if hasattr(session_service, '_redis_connection'):
        monkeypatch.setattr(session_service, '_redis_connection', lambda: client)

    monkeypatch.setattr(
        redis.ConnectionPool,
        'from_url',
        classmethod(
            lambda cls, _url, **kwargs: fakeredis.FakeStrictRedis(
                server=fake_server,
                decode_responses=bool(kwargs.get('decode_responses', False)),
            ).connection_pool
        ),
    )
    monkeypatch.setattr(
        redis.Redis,
        'from_url',
        classmethod(
            lambda cls, _url, **kwargs: fakeredis.FakeStrictRedis(
                server=fake_server,
                decode_responses=bool(kwargs.get('decode_responses', False)),
            )
        ),
    )
    return client, fake_server


def _build_e2e_project_db(
    db_path: Path,
    *,
    project_name: str = 'E2E Project',
    db_uuid: str | None = None,
    references: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
    example_fasta: str = '',
    website: str = 'https://respro.example.org',
    contact: str = 'curator@example.org',
    tsv_checksum: str = 'sha256:e2e-checksum',
    include_formula_component: bool = False,
    publications: list[dict[str, str]] | None = None,
) -> Path:
    """Create a schema-compliant project SQLite DB inside ``tmp_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid, example_fasta, '
            'metadata_website, metadata_contact, metadata_tsv_checksum) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (project_name, 4, db_uuid or str(uuid.uuid4()), example_fasta, website, contact, tsv_checksum),
        )
        ref_specs = references or [
            {'name': TINY_REF_NAME, 'accession': 'ACC_TINY.1', 'organism': 'TestVirus', 'length': len(TINY_REF_SEQ)}
        ]
        ref_ids: dict[str, int] = {}
        feature_ids: dict[tuple[str, str], int] = {}
        for ref in ref_specs:
            conn.execute(
                'INSERT INTO reference (project_id, name, accession, organism, length) VALUES (?, ?, ?, ?, ?)',
                (1, ref['name'], ref.get('accession', ''), ref.get('organism', ''), ref.get('length', len(TINY_REF_SEQ))),
            )
            ref_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            ref_ids[ref['name']] = ref_id
            for feat in ref.get('features', [{'name': 'gag', 'protein': 'Gag', 'feature_type': 'CDS'}]):
                conn.execute(
                    'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence, '
                    'feature_type, has_rules) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        ref_id,
                        feat['name'],
                        feat.get('protein', feat['name']),
                        feat.get('start', 0),
                        feat.get('end', len(TINY_REF_SEQ)),
                        feat.get('strand', '+'),
                        feat.get('nt_sequence', TINY_REF_SEQ),
                        feat.get('feature_type', 'CDS'),
                        1,
                    ),
                )
                feat_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
                feature_ids[(ref['name'], feat['name'])] = feat_id

        default_ref = ref_specs[0]['name']
        first_features = ref_specs[0].get('features', [{'name': 'gag'}])
        default_feature = first_features[0]['name'] if first_features else 'gag'
        rule_specs = (
            rules
            if rules is not None
            else [
                {
                    'ref_name': default_ref,
                    'feature': default_feature,
                    'drug': 'TestDrug',
                    'position': 1,
                    'reference': 'K',
                    'mutation': 'E',
                    'phenotype': 'resistant',
                    'publication': '',
                }
            ]
        )
        if include_formula_component:
            rule_specs = [
                *rule_specs,
                {
                    'ref_name': default_ref,
                    'feature': default_feature,
                    'drug': '__formula_component__',
                    'position': 2,
                    'reference': 'A',
                    'mutation': 'V',
                    'phenotype': 'resistant',
                    'publication': '',
                },
            ]

        drug_ids: dict[str, int] = {}
        first_rule_id: int | None = None
        for r in rule_specs:
            drug = r['drug']
            if drug not in drug_ids:
                conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, drug))
                drug_ids[drug] = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            feat_id = feature_ids[(r.get('ref_name', default_ref), r.get('feature', default_feature))]
            conn.execute(
                'INSERT INTO resistance_rule '
                '(feature_id, drug_id, position, reference, mutation, phenotype, ic50, fold_ic50, score, publication) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    feat_id,
                    drug_ids[drug],
                    r['position'],
                    r['reference'],
                    r['mutation'],
                    r.get('phenotype', 'resistant'),
                    r.get('ic50', ''),
                    r.get('fold_ic50', ''),
                    r.get('score', ''),
                    r.get('publication', ''),
                ),
            )
            rid = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            if first_rule_id is None:
                first_rule_id = rid

        if publications and first_rule_id is not None:
            for pub in publications:
                conn.execute(
                    'INSERT INTO publication (doi, pubmed_id, raw_input) VALUES (?, ?, ?)',
                    (pub.get('doi', ''), pub.get('pubmed_id', ''), pub.get('raw_input', '')),
                )
                pub_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
                conn.execute(
                    'INSERT INTO rule_publication (rule_id, publication_id) VALUES (?, ?)',
                    (first_rule_id, pub_id),
                )

        conn.commit()
    finally:
        conn.close()
    return db_path


def _make_startup_config(
    tmp_path: Path,
    *,
    db_builders: list[tuple[str, dict[str, Any]]] | None = None,
    deployment_mode: str = 'local',
    imprint: ImprintConfig | None = None,
    contact_email: ContactEmailConfig | None = None,
) -> StartupConfig:
    """Create an isolated StartupConfig under ``tmp_path/data``."""
    data_dir = tmp_path / 'data'
    project_databases_dir = data_dir / 'project_databases'
    uploads_dir = data_dir / 'uploads'
    results_dir = data_dir / 'results'
    for d in (project_databases_dir, uploads_dir, results_dir):
        d.mkdir(parents=True, exist_ok=True)

    builders = db_builders or [('test_project.db', {'example_fasta': MUTATED_TINY_FASTA})]
    for filename, kwargs in builders:
        _build_e2e_project_db(project_databases_dir / filename, **kwargs)

    return StartupConfig(
        project_databases_dir=project_databases_dir.resolve(),
        uploads_dir=uploads_dir.resolve(),
        results_dir=results_dir.resolve(),
        data_dir=data_dir.resolve(),
        allowed_roots=(project_databases_dir.resolve(), uploads_dir.resolve(), results_dir.resolve()),
        imprint=imprint,
        contact_email=contact_email,
        project_db_uuid_index=build_project_db_uuid_index(project_databases_dir.resolve()),
        deployment_mode=deployment_mode,
    )


def _make_app_and_queue(
    config: StartupConfig,
    *,
    is_async: bool = False,
    redis_server: fakeredis.FakeServer | None = None,
) -> tuple[Any, Queue, Queue]:
    """Build a FastAPI app with isolated RQ queues backed by FakeStrictRedis."""
    conn = fakeredis.FakeStrictRedis(server=redis_server or fakeredis.FakeServer())
    profile_q = Queue(
        WEB_BACKEND_CONFIG.defaults.profile_queue_name,
        connection=conn,
        is_async=is_async,
        serializer=JSONSerializer,
    )
    batch_q = Queue(
        WEB_BACKEND_CONFIG.defaults.batch_queue_name,
        connection=conn,
        is_async=is_async,
        serializer=JSONSerializer,
    )
    app = create_app(startup_config=config)
    app.dependency_overrides[get_queue] = lambda: profile_q
    app.dependency_overrides[get_batch_queue] = lambda: batch_q
    return app, profile_q, batch_q


def _build_pysam_bam(
    bam_path: Path,
    *,
    ref_name: str = TINY_REF_NAME,
    ref_len: int = len(TINY_REF_SEQ),
    cover_end: int = 45,
    create_index: bool = True,
) -> Path:
    """Create a real coordinate-sorted BAM (and optional ``.bai``) with partial reference coverage."""
    header = {'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'LN': ref_len, 'SN': ref_name}]}
    seq = ('ATGAAAGCTTTTGGCCCCAAATTTGGGCCC' * 10)[:cover_end]
    with pysam.AlignmentFile(bam_path, 'wb', header=header) as out:
        for i in range(15):
            seg = pysam.AlignedSegment()
            seg.query_name = f'read_{i}'
            seg.query_sequence = seq
            seg.flag = 0
            seg.reference_id = 0
            seg.reference_start = 0
            seg.mapping_quality = 60
            seg.cigar = [(0, cover_end)]
            seg.query_qualities = pysam.qualitystring_to_array('I' * cover_end)
            out.write(seg)
    if create_index:
        pysam.index(str(bam_path))
    return bam_path


def _upload_file(
    client: TestClient,
    kind: str,
    filename: str,
    content: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    resp = client.post(
        f'/api/upload/{kind}',
        files={'file': (filename, content, 'application/octet-stream')},
        headers=headers or {},
    )
    assert resp.status_code == 200, f'Upload {kind} failed ({resp.status_code}): {resp.text}'
    return resp.json()


def _write_synthetic_results_json(
    path: Path,
    *,
    sample_name: str = 'syn_sample',
    project_name: str = 'E2E Project',
    project_db_path: str = '',
    project_uuid: str = '',
    reference_name: str = TINY_REF_NAME,
    references: list[str] | None = None,
    variants: list[dict[str, Any]] | None = None,
    coverage_gaps: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a valid ``.results.json`` payload for direct comparison / upload tests."""
    run_obj: dict[str, Any] = {
        'project_name': project_name,
        'project_db_path': project_db_path,
        'project_fingerprint': project_uuid,
        'reference_name': reference_name,
        'sample_name': sample_name,
        'vcf_path': 'sample.vcf',
        'total_variants': len(variants or []),
        'variants_in_cds': len(variants or []),
        'resistance_hits': 0,
        'formula_hits': 0,
        'status': 'complete',
        'is_fasta_mode': 0,
        'coverage_assessed': 1 if coverage_gaps is not None else 0,
        'created_at': '2026-11-24T12:00:00Z',
    }
    if references is not None:
        run_obj['references'] = [{'reference_name': r} for r in references]
    payload = {
        'run': run_obj,
        'variant_result': variants if variants is not None else [],
        'coverage_gap': coverage_gaps if coverage_gaps is not None else [],
        'formula_rule_hit': [],
        'sample_classification': [],
    }
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def _record_owned_result_json(
    config: StartupConfig,
    session_hash: str,
    filename: str,
    **json_kwargs: Any,
) -> str:
    """Persist a synthetic ``.results.json`` in ``results_dir`` and register ownership."""
    out_path = config.results_dir / filename
    _write_synthetic_results_json(out_path, **json_kwargs)
    return session_service.record_artifact(
        session_hash=session_hash,
        canonical_path=out_path,
        media_type='application/json',
    )


def _start_local_http_server(directory: Path) -> tuple[socketserver.TCPServer, int]:
    """Serve *directory* on an ephemeral localhost port in a daemon thread."""

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            return

    httpd = socketserver.TCPServer(('127.0.0.1', 0), _QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port



class TestEndToEndWorkflows:
    def test_t3_compare_multi_ref_plus_null_af_plus_duplicate_aa_plus_db_display_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        db_file = tmp_path / 'data' / 'project_databases' / 'multi.db'
        hash_db_file = tmp_path / 'data' / 'project_databases' / 'db#1?.db'
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'multi.db',
                    {
                        'references': [
                            {'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'Prot 1', 'feature_type': 'mat_peptide'}]},
                            {'name': 'Ref2', 'accession': 'A2', 'features': [{'name': 'f2', 'protein': 'Prot 2', 'feature_type': 'mat_peptide'}]},
                        ],
                        'rules': [
                            {'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                            {'ref_name': 'Ref2', 'feature': 'f2', 'drug': 'D1', 'position': 2, 'reference': 'A', 'mutation': 'V'},
                        ],
                    },
                ),
                (
                    'db#1?.db',
                    {
                        'references': [
                            {'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'ProtHashQ', 'feature_type': 'mat_peptide'}]},
                        ],
                        'rules': [
                            {'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                        ],
                    },
                ),
            ],
        )
        client = TestClient(_make_app_and_queue(config)[0])
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        # Duplicate AA mapping (0.4 first, 0.3 second) + unknown AF + multi-reference display names.
        a1 = _record_owned_result_json(
            config,
            sh,
            'mr1.20261124000001000000.results.json',
            sample_name='MR1',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref2'],
            variants=[
                {'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.4, 'rule_match': 1, 'consequence': 'missense'},
                {'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.3, 'rule_match': 1, 'consequence': 'missense'},
                {'feature_name': 'f2', 'codon_pos': 1, 'ref_aa': 'A', 'alt_aa': 'V', 'allele_freq': None, 'rule_match': 0, 'consequence': 'missense'},
            ],
        )
        a2 = _record_owned_result_json(
            config,
            sh,
            'mr2.20261124000002000000.results.json',
            sample_name='MR2',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref2'],
            variants=[],
        )
        resp = client.post('/api/compare', json={'artifact_ids': [a1, a2]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data['feature_display_names'].get('f2') == 'Prot 2', (
            f'feature_display_names must include Ref2 features (WA-016): {data["feature_display_names"]}'
        )
        # Duplicate AA must aggregate deterministically (max=0.4 or capped sum <= 1.0, never overwritten to 0.3).
        af = data['matrix'][0][0]['allele_freq']
        assert af is not None and 0.4 <= af <= 1.0, f'Duplicate AA aggregation regressed (WA-016): {af!r}'
        assert data['matrix'][0][1]['allele_freq'] is None and data['matrix'][0][1]['coverage_gap'] is False

        # Secondary reference mismatch must return 400 (WA-016).
        a3 = _record_owned_result_json(
            config,
            sh,
            'mr3.20261124000003000000.results.json',
            sample_name='MR3',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref3'],
            variants=[],
        )
        assert client.post('/api/compare', json={'artifact_ids': [a1, a3]}).status_code == 400

        # WA-033: null project_db_path must not raise 500 TypeError.
        null_db_p = config.results_dir / 'nulldb.20261124000004000000.results.json'
        _write_synthetic_results_json(null_db_p, sample_name='NullDB', project_db_path='')
        null_payload = json.loads(null_db_p.read_text(encoding='utf-8'))
        null_payload['run']['project_db_path'] = None
        null_db_p.write_text(json.dumps(null_payload), encoding='utf-8')
        a_nulldb = session_service.record_artifact(session_hash=sh, canonical_path=null_db_p, media_type='application/json')
        r_null = client.post('/api/compare', json={'artifact_ids': [a_nulldb]})
        assert r_null.status_code == 200
        assert r_null.json()['feature_display_names'] == {}

        # WA-033: outside project_db_path must not be opened.
        outside_db = _build_e2e_project_db(
            tmp_path / 'outside.db',
            references=[{'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'LEAKED_OUTSIDE', 'feature_type': 'mat_peptide'}]}],
            rules=[{'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'}],
        )
        a_out = _record_owned_result_json(
            config,
            sh,
            'outdb.20261124000005000000.results.json',
            sample_name='OutDB',
            project_db_path=str(outside_db),
            reference_name='Ref1',
            variants=[{'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.5, 'rule_match': 1, 'consequence': 'missense'}],
        )
        r_out = client.post('/api/compare', json={'artifact_ids': [a_out]})
        assert r_out.status_code in (200, 400)
        assert 'LEAKED_OUTSIDE' not in r_out.text

        # WA-033: confined DB containing '#' and '?' must open via as_uri() without creating junk 'db' file.
        a_hash = _record_owned_result_json(
            config,
            sh,
            'hashdb.20261124000006000000.results.json',
            sample_name='HashDB',
            project_db_path=str(hash_db_file),
            reference_name='Ref1',
            variants=[{'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.5, 'rule_match': 1, 'consequence': 'missense'}],
        )
        r_hash = client.post('/api/compare', json={'artifact_ids': [a_hash]})
        assert r_hash.status_code == 200
        assert r_hash.json()['feature_display_names'].get('f1') == 'ProtHashQ'
        assert not (config.project_databases_dir / 'db').exists()

    def test_t3_concurrent_job_poll_atomic_artifact_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import web.backend.routes.jobs as jobs_route_module

        fake_redis, _ = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'Concurrent'},
        ).json()['job_id']

        barrier = threading.Barrier(6, timeout=0.5)
        seen_tls = threading.local()
        orig_record_artifact = jobs_route_module.record_artifact

        def _sync_record_artifact(**kwargs: Any) -> str:
            if not getattr(seen_tls, 'entered', False):
                seen_tls.entered = True
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait()
            return orig_record_artifact(**kwargs)

        monkeypatch.setattr(jobs_route_module, 'record_artifact', _sync_record_artifact)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: client.get(f'/api/jobs/{job_id}').json(), range(6)))

        first_ids = responses[0]['result']['artifact_ids']
        assert all(r['result']['artifact_ids'] == first_ids for r in responses)
        art_keys = list(fake_redis.scan_iter('respro:artifact:*'))
        assert len(art_keys) == len(first_ids), (
            f'Concurrent job polls minted duplicate orphan artifacts (WA-019): {len(art_keys)} != {len(first_ids)}'
        )

    def test_t4_scenario1_clinical_multisample_cohort_vcf_gz_bam_fasta_compare_bundle_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 1: .vcf.gz + real pysam BAM + FASTA -> reports + CSP -> /api/compare -> ZIP -> cleanup."""
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client_a = TestClient(app)
        client_b = TestClient(app)

        # Sample 1: gzipped VCF + partial-coverage BAM (codons 0..9 covered; 10..28 uncovered).
        bam_file = _build_pysam_bam(tmp_path / 'cohort1.bam', cover_end=30)
        vcf_id = _upload_file(client_a, 'vcf', 'cohort1.vcf.gz', gzip.compress(VALID_TINY_VCF.encode('utf-8')))['upload_id']
        ref_id = _upload_file(client_a, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam_id = _upload_file(client_a, 'bam', 'cohort1.bam', bam_file.read_bytes())['upload_id']

        j1 = client_a.post(
            '/api/profile/vcf',
            json={'vcf_id': vcf_id, 'reference_id': ref_id, 'bam_id': bam_id, 'database_id': 'test_project.db', 'sample': 'Clinical_Vcf'},
        ).json()['job_id']

        # Sample 2: FASTA with K2E + codon-21 mutation (GCT->ACT / A21T) so codon 21 contrasts coverage_gap vs detected variant.
        fa_seq = 'ATGGAAGCT' + TINY_REF_SEQ[9:60] + 'A' + TINY_REF_SEQ[61:]
        fa_id = _upload_file(client_a, 'fasta', 'cohort2.fasta', f'>Clinical_Fasta\n{fa_seq}\n'.encode())['upload_id']
        j2 = client_a.post(
            '/api/profile/fasta',
            json={'fasta_id': fa_id, 'database_id': 'test_project.db', 'sample': 'Clinical_Fasta'},
        ).json()['job_id']

        res1 = client_a.get(f'/api/jobs/{j1}').json()['result']
        res2 = client_a.get(f'/api/jobs/{j2}').json()['result']

        # Verify HTML report serves with strict CSP.
        rep1 = client_a.get('/api/report', params={'artifact_id': res1['report_html_path']})
        assert rep1.status_code == 200
        assert 'connect-src https://translate.googleapis.com' in rep1.headers.get('content-security-policy', '')

        # Compare both samples.
        comp = client_a.post('/api/compare', json={'artifact_ids': [res1['report_json_path'], res2['report_json_path']]})
        assert comp.status_code == 200
        comp_data = comp.json()
        assert comp_data['samples'] == ['Clinical_Vcf', 'Clinical_Fasta']
        gap_col = next(i for i, m in enumerate(comp_data['mutations']) if m['position'] == 21)
        assert comp_data['matrix'][0][gap_col]['coverage_gap'] is True
        assert comp_data['matrix'][1][gap_col]['coverage_gap'] is False and comp_data['matrix'][1][gap_col]['allele_freq'] == 1.0

        # Bundle all artifacts and verify session B isolation.
        all_arts = res1['artifact_ids'] + res2['artifact_ids']
        assert client_b.post('/api/artifact-bundle', json={'artifact_ids': all_arts}).status_code == 404
        zip_resp = client_a.post('/api/artifact-bundle', json={'artifact_ids': all_arts})
        assert zip_resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            assert len(zf.namelist()) == len(all_arts)

        # Cleanup uploads (including .bam.bai) + artifacts.
        clean = client_a.post('/api/session/cleanup', json={'upload_ids': [vcf_id, ref_id, bam_id, fa_id], 'artifact_ids': all_arts})
        assert clean.status_code == 200
        assert clean.json()['deleted_count'] == 4 + len(all_arts)
        assert list(config.uploads_dir.iterdir()) == []

    def test_t4_scenario2_batch_queue_worker_json_regeneration_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 2: Batch FASTA + VCF/BAM -> worker drains queues -> JSON artifacts -> batch regenerate -> quota."""
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '5')
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(tmp_path, db_builders=[('cohort.db', {'db_uuid': db_uuid})])
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        f1 = _upload_file(client, 'fasta', 'f1.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        v1 = _upload_file(client, 'vcf', 'v1.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        ref = _upload_file(client, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam = _upload_file(client, 'bam', 'b1.bam', _build_pysam_bam(tmp_path / 'b1.bam').read_bytes())['upload_id']

        b_fa = client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': [f1], 'db_path': 'cohort.db', 'sample_names': ['FaSample']},
        ).json()
        b_vcf = client.post(
            '/api/profile/batch/vcf',
            json={'vcf_ids': [v1], 'reference_id': ref, 'db_path': 'cohort.db', 'sample_names': ['VcfSample'], 'bam_ids': [bam]},
        ).json()

        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        json_uploads: list[str] = []
        for entry in b_fa['samples'] + b_vcf['samples']:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded'
            raw_json = client.get('/api/artifact', params={'artifact_id': st['result']['report_json_path']}).content
            json_uploads.append(_upload_file(client, 'json', f"{entry['sample_name']}.results.json", raw_json)['upload_id'])

        regen = client.post(
            '/api/regenerate/batch',
            json={'json_ids': json_uploads, 'sample_names': ['FaSample', 'VcfSample']},
        )
        assert regen.status_code == 200
        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        for entry in regen.json()['samples']:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded'
            assert st['result']['sample_name'] == entry['sample_name']

        # Quota consumed = 1 + 1 + 2 = 4 out of 5; requesting 2 more must return 429 + Retry-After.
        over = client.post(
            '/api/regenerate/batch',
            json={'json_ids': json_uploads, 'sample_names': ['FaSample', 'VcfSample']},
        )
        assert over.status_code == 429
        assert int(over.headers['retry-after']) >= 1

    def test_t4_scenario3_multidb_curation_corrupt_isolation_and_cross_ref_compare_tsv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 3: 3 curated DBs + 1 corrupt .db -> isolation, exact ref, Venn compare, TSV neutralization."""
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'db_alpha.db',
                    {
                        'references': [
                            {'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'},
                            {'name': 'REF10', 'accession': 'ACC_EXTRA', 'organism': 'Herpes'},
                        ],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': '=HYPERLINK("x")'},
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'FOS', 'position': 2, 'reference': 'A', 'mutation': 'T', 'phenotype': 'resistant'},
                        ],
                        'include_formula_component': True,
                    },
                ),
                (
                    'db_beta.db',
                    {
                        'references': [{'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'}],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': 'resistant'},
                        ],
                    },
                ),
                (
                    'db_gamma.db',
                    {
                        'references': [{'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'}],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': 'resistant'},
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'CDV', 'position': 3, 'reference': 'F', 'mutation': 'L', 'phenotype': 'intermediate'},
                        ],
                    },
                ),
            ],
        )
        (config.project_databases_dir / 'corrupt_dropin.db').write_bytes(b'corrupt!')
        client = TestClient(_make_app_and_queue(config)[0])

        catalog = client.get('/api/databases').json()['data']
        assert catalog['count'] == 3
        alpha_entry = next(i for i in catalog['items'] if i['id'] == 'db_alpha.db')
        assert alpha_entry['mutation_count'] == 2  # excludes __formula_component__

        assert client.get('/api/mutations', params={'database_id': 'db_alpha.db', 'reference': 'REF1'}).json()['data']['count'] == 2
        shared = client.get('/api/databases/shared-references', params={'ids': 'db_alpha.db,db_beta.db,db_gamma.db'}).json()['data']
        assert [s['accession'] for s in shared['items']] == ['ACC_SHARED']

        comp = client.post(
            '/api/databases/compare',
            json={'database_ids': ['db_alpha.db', 'db_beta.db', 'db_gamma.db'], 'accession': 'ACC_SHARED'},
        ).json()['data']
        assert comp['counts']['intersection'] == 1
        assert comp['counts']['total_unique'] == 3
        formula_cell = next(r['per_db']['db_alpha.db']['phenotype'] for r in comp['rows'] if r['drug'] == 'ACV')
        assert formula_cell.startswith('=HYPERLINK')

    def test_t4_scenario4_maintained_db_bootstrap_and_online_policy_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 4: S3/S4 manifest confinement + online startup policy + CSRF + Redis 503 vs health 200."""
        from respro.io import maintained_db
        from tests.conftest import write_genbank

        srv_dir = tmp_path / 'manifest_srv'
        (srv_dir / 'db_ok').mkdir(parents=True)
        write_genbank(
            srv_dir / 'ACC_SC4.gb',
            [{'id': 'ACC_SC4', 'accession': 'ACC_SC4', 'organism': 'V', 'sequence': TINY_REF_SEQ, 'features': [{'feature': 'gag', 'start': 1, 'end': len(TINY_REF_SEQ), 'strand': '+'}]}],
        )
        (srv_dir / 'db_ok' / 'rules.tsv').write_text(
            'feature\tposition\treference\tmutation\tantiviral\tphenotype\treference_identifier\n'
            'gag\t2\tK\tE\tTestDrug\tresistant\tACC_SC4\n',
            encoding='utf-8',
        )
        (srv_dir / 'db_ok' / 'metadata.json').write_text(json.dumps({'tsv_checksum': 'v1'}), encoding='utf-8')
        (srv_dir / 'manifest.json').write_text(
            json.dumps({
                'databases': [
                    {
                        'source_name': 'sc4_db',
                        'rules_path': 'db_ok/rules.tsv',
                        'metadata_path': 'db_ok/metadata.json',
                        'formula_rules_path': '',
                        'metadata': {'tsv_checksum': 'v1'},
                    }
                ]
            }),
            encoding='utf-8',
        )
        httpd, port = _start_local_http_server(srv_dir)
        try:
            base_url = f'http://127.0.0.1:{port}'
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(
                        maintained_db.CLI_CONFIG.urls,
                        github_respro_db_raw=base_url,
                        ncbi_nuccore_efetch=f'{base_url}/{{accession}}.gb',
                    ),
                ),
            )

            data_dir = tmp_path / 'data'
            monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
            monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
            monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')
            monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
            monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')

            _fake_redis, fake_server = _patch_fakeredis(monkeypatch)
            cfg = load_startup_config()
            assert (cfg.project_databases_dir / 'sc4_db.db').is_file()
            client = TestClient(
                _make_app_and_queue(cfg)[0],
                base_url='https://respro.example.org',
                raise_server_exceptions=False,
            )

            # Online CSRF protection blocks cross-site POST.
            assert client.post(
                '/api/session/cleanup',
                json={'upload_ids': [], 'artifact_ids': []},
                headers={'Sec-Fetch-Site': 'cross-site'},
            ).status_code == 403

            # Same-origin mutation succeeds.
            assert client.post(
                '/api/session/cleanup',
                json={'upload_ids': [], 'artifact_ids': []},
                headers={
                    'Host': 'respro.example.org',
                    'X-Forwarded-Proto': 'https',
                    'Origin': 'https://respro.example.org',
                    'Sec-Fetch-Site': 'same-origin',
                },
            ).status_code == 200

            # Redis outage -> health stays 200, readiness & stateful routes return 503.
            fake_server.connected = False
            assert client.get('/api/health').status_code == 200
            assert client.get('/api/readiness').status_code == 503
            assert client.get('/api/databases').status_code == 503
        finally:
            httpd.shutdown()

    def test_t4_scenario6_active_job_cancellation_process_group_and_resource_recovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 6: Active started job -> DELETE /api/jobs/{id} invokes send_stop_job_command -> cleanup."""
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, profile_q, _ = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        up_id = _upload_file(client, 'fasta', 'slow.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        job_id = client.post(
            '/api/profile/fasta',
            json={'fasta_id': up_id, 'database_id': 'test_project.db', 'sample': 'ActiveCancel'},
        ).json()['job_id']

        from rq.job import Job

        rq_job = Job.fetch(job_id, connection=profile_q.connection, serializer=JSONSerializer)
        rq_job.worker_name = 'worker-test-1'
        rq_job.set_status('started')
        rq_job.save()

        stop_calls: list[str] = []
        import rq.command as rq_command_module

        import web.backend.jobs as jobs_exec_module
        import web.backend.routes.jobs as jobs_route_module

        # Verify routes/jobs.py uses RQ send_stop_job_command on started jobs (W6 / WA-020).
        def _spy_stop(connection: Any, jid: str, **_kwargs: Any) -> None:
            del connection
            stop_calls.append(jid)
            j = Job.fetch(jid, connection=profile_q.connection, serializer=JSONSerializer)
            j.set_status('stopped')
            j.save()

        monkeypatch.setattr(rq_command_module, 'send_stop_job_command', _spy_stop)
        if hasattr(jobs_route_module, 'send_stop_job_command'):
            monkeypatch.setattr(jobs_route_module, 'send_stop_job_command', _spy_stop)

        # Behaviorally verify web/backend/jobs.py runs CLI in a new session and killpgs the whole process group on timeout (W6/WA-020).
        info_path = tmp_path / 'proc_info.txt'
        child_script = (
            'import os, subprocess, sys, time; '
            'gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
            f'f = open({str(info_path)!r}, "w", encoding="utf-8"); '
            'f.write(f"{os.getpgid(0)} {gc.pid}"); f.flush(); f.close(); '
            'time.sleep(30)'
        )
        import sys as _sys

        monkeypatch.setattr(jobs_exec_module, 'resolve_subprocess_timeout_seconds', lambda: 0.5)
        grandchild_pid: int | None = None
        child_pgid: int | None = None
        try:
            with pytest.raises(RuntimeError, match='timed out'):
                jobs_exec_module._run_respro_command([_sys.executable, '-c', child_script])
            assert info_path.is_file(), 'Child subprocess did not write process group metadata before timeout'
            pgid_raw, gc_raw = info_path.read_text(encoding='utf-8').strip().split()
            child_pgid = int(pgid_raw)
            grandchild_pid = int(gc_raw)
            assert child_pgid != os.getpgid(0), (
                'CLI subprocess must run in an isolated process group / session (W6/WA-020)'
            )
            dead = False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                except ProcessLookupError:
                    dead = True
                    break
                stat_file = Path(f'/proc/{grandchild_pid}/stat')
                if stat_file.is_file():
                    stat_text = stat_file.read_text(encoding='utf-8', errors='ignore')
                    rparen = stat_text.rfind(')')
                    if rparen != -1 and stat_text[rparen + 2:rparen + 3] == 'Z':
                        dead = True
                        break
                time.sleep(0.02)
            assert dead, f'Grandchild PID {grandchild_pid} survived timeout without killpg (W6/WA-020)'
        finally:
            if grandchild_pid is not None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.kill(grandchild_pid, signal.SIGKILL)
            if child_pgid is not None and child_pgid != os.getpgid(0):
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(child_pgid, signal.SIGKILL)

        cancel = client.delete(f'/api/jobs/{job_id}')
        assert cancel.status_code == 204
        assert stop_calls == [job_id], 'DELETE /api/jobs/{id} on started job must invoke send_stop_job_command (W6/WA-020)'

        status = client.get(f'/api/jobs/{job_id}').json()
        assert status['status'] == 'failed'
        assert status['error'] == 'Job canceled by user.'
        assert list(config.results_dir.iterdir()) == []

        clean = client.post('/api/session/cleanup', json={'upload_ids': [up_id], 'artifact_ids': []})
        assert clean.status_code == 200
        assert clean.json()['deleted_count'] == 1
        assert list(config.uploads_dir.iterdir()) == []
