"""Tests for the /api/compare endpoint and comparison matrix service."""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue

from web.backend.models import CompareCell, CompareResponse
from web.backend.services.compare import build_comparison_matrix
from web.backend.startup_config import (
    StartupConfig,
    build_project_db_uuid_index,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_result_json(
    path: Path,
    project_name: str = 'test-db',
    sample_name: str = 'sample',
    reference_name: str = 'NC_001802.1',
    variant_result: list[dict] | None = None,
    coverage_gap: list[dict] | None = None,
    formula_rule_hit: list[dict] | None = None,
    project_db_path: str = '',
) -> Path:
    """Write a minimal .results.json file and return its path."""
    variant_result = variant_result or []
    coverage_gap = coverage_gap or []
    formula_rule_hit = formula_rule_hit or []
    payload = {
        'run': {
            'project_name': project_name,
            'sample_name': sample_name,
            'reference_name': reference_name,
            'project_db_path': project_db_path,
            'created_at': '2026-01-01T00:00:00',
            'status': 'complete',
        },
        'variant_result': variant_result,
        'coverage_gap': coverage_gap,
        'formula_rule_hit': formula_rule_hit,
        'sample_classification': [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return path


def _variant(
    feature_name: str = 'gene_a',
    chrom: str = 'gene_a',
    codon_pos: int = 0,
    ref_aa: str = 'K',
    alt_aa: str = 'N',
    allele_freq: float = 1.0,
    rule_match: int = 0,
) -> dict:
    """Build a variant_result entry."""
    return {
        'af_bin': 'high',
        'allele_freq': allele_freq,
        'alt': 'A',
        'alt_aa': alt_aa,
        'alt_codon': 'AAC',
        'chrom': chrom,
        'codon_pos': codon_pos,
        'consequence': 'missense',
        'depth': 100,
        'drug_hits': '[]',
        'feature_name': feature_name,
        'id': None,
        'pos': 100 + codon_pos * 3,
        'ref': 'G',
        'ref_aa': ref_aa,
        'ref_codon': 'AAA',
        'rule_match': rule_match,
    }


def _formula_hit(matched_variants: list[dict]) -> dict:
    """Build a formula_rule_hit entry from a list of matched_variant dicts."""
    hit_data = {
        'drug': 'test-drug',
        'matched_variants': matched_variants,
    }
    return {'hit_json': json.dumps(hit_data), 'id': None}


def _coverage_gap_entry(
    feature_name: str = 'gene_a',
    codon_start: int = 0,
    codon_end: int = 10,
) -> dict:
    """Build a coverage_gap entry (0-based inclusive)."""
    return {
        'feature_name': feature_name,
        'codon_start': codon_start,
        'codon_end': codon_end,
        'id': None,
    }


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """Create a temporary results directory."""
    d = tmp_path / 'results'
    d.mkdir()
    return d


@pytest.fixture()
def path_validators(results_dir: Path):
    """Return the standard path validation callables used by the app."""
    from web.backend.main import _is_allowed_artifact_path
    from web.backend.startup_config import is_path_within_allowed_roots

    def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
        return is_path_within_allowed_roots(path, roots)

    return _is_within, _is_allowed_artifact_path


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------

class TestBuildComparisonMatrixBasic:
    """Test basic matrix assembly with overlapping and unique mutations."""

    def test_two_samples_overlapping_and_unique_mutations(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'sample1.20260601000000000001.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.9, rule_match=1),
                _variant(feature_name='gene_a', codon_pos=200, ref_aa='M', alt_aa='L', allele_freq=0.8, rule_match=0),
            ],
        )
        p2 = _make_result_json(
            results_dir / 'sample2.20260602000000000002.results.json',
            sample_name='s2',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.5, rule_match=0),
                _variant(feature_name='gene_b', codon_pos=50, ref_aa='G', alt_aa='T', allele_freq=1.0, rule_match=1),
            ],
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)

        assert isinstance(result, CompareResponse)
        assert result.samples == ['s1', 's2']

        # Mutations sorted by (feature, position, ref_aa, alt_aa)
        # gene_a K103N (codon_pos=102 -> pos=103), gene_a M201L (codon_pos=200 -> pos=201), gene_b G51T (pos=51)
        assert len(result.mutations) == 3
        assert result.mutations[0].feature == 'gene_a'
        assert result.mutations[0].position == 103
        assert result.mutations[0].label == 'K103N'
        assert result.mutations[1].feature == 'gene_a'
        assert result.mutations[1].position == 201
        assert result.mutations[1].label == 'M201L'
        assert result.mutations[2].feature == 'gene_b'
        assert result.mutations[2].position == 51
        assert result.mutations[2].label == 'G51T'

        assert result.mutation_labels == ['K103N', 'M201L', 'G51T']
        assert result.features == ['gene_a', 'gene_b']
        assert result.feature_map == [0, 0, 1]

        # Sample s1: K103N=0.9/db_hit, M201L=0.8/no_hit, G51T=0.0 (not detected, no gap)
        assert result.matrix[0][0] == CompareCell(allele_freq=0.9, db_hit=True)
        assert result.matrix[0][1] == CompareCell(allele_freq=0.8, db_hit=False)
        assert result.matrix[0][2] == CompareCell(allele_freq=0.0, db_hit=False)

        # Sample s2: K103N=0.5/no_hit, M201L=0.0 (not detected, no gap), G51T=1.0/db_hit
        assert result.matrix[1][0] == CompareCell(allele_freq=0.5, db_hit=False)
        assert result.matrix[1][1] == CompareCell(allele_freq=0.0, db_hit=False)
        assert result.matrix[1][2] == CompareCell(allele_freq=1.0, db_hit=True)

        # db_hit_map: True if any sample has db_hit for that column
        # K103N (sample 1 hit), M201L (no hit), G51T (sample 2 hit)
        assert result.db_hit_map == [True, False, True]

        # mutation_tick_labels: always AA-only, no feature prefix
        assert result.mutation_tick_labels == ['K103N', 'M201L', 'G51T']

        # consequences: collected from variant_result entries
        assert result.consequences == ['missense', 'missense', 'missense']

        # feature_display_names: empty when no project_db_path
        assert result.feature_display_names == {}

    def test_sample_name_fallback_to_file_stem(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'mysample.20260601000000000001.results.json',
            project_name='db',
            sample_name='',
        )
        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        # sample_name is empty string, should fall back to derived name
        assert result.samples[0] == 'mysample'


class TestBuildComparisonMatrixMixedDbRejection:
    """Test that samples from different databases are rejected."""

    def test_different_project_names_raises(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            project_name='hiv-db',
            sample_name='s1',
        )
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            project_name='hsv-db',
            sample_name='s2',
        )

        with pytest.raises(ValueError, match='Cannot compare results from different databases'):
            build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)


class TestBuildComparisonMatrixMixedReferenceRejection:
    """Test that samples from different references are rejected."""

    def test_different_reference_names_raises(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            project_name='hiv-db',
            sample_name='s1',
            reference_name='NC_001802.1',
        )
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            project_name='hiv-db',
            sample_name='s2',
            reference_name='K03455.1',
        )

        with pytest.raises(ValueError, match='Cannot compare results from different references'):
            build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)

    def test_same_reference_succeeds(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            reference_name='NC_001802.1',
            variant_result=[
                _variant(codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
            ],
        )
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            sample_name='s2',
            reference_name='NC_001802.1',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert result.references == ['NC_001802.1', 'NC_001802.1']


class TestBuildComparisonMatrixCoverageGap:
    """Test that coverage gaps produce allele_freq=None cells."""

    def test_coverage_gap_produces_none_allele_freq(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        # Sample 1: detects mutation at gene_a codon_pos=102 (display pos 103), no coverage gap
        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.9),
            ],
        )
        # Sample 2: has a coverage gap covering gene_a codon_pos 100-110, no variant at 102
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            sample_name='s2',
            variant_result=[],
            coverage_gap=[
                _coverage_gap_entry(feature_name='gene_a', codon_start=100, codon_end=110),
            ],
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)

        # Only one mutation: gene_a K103N
        assert len(result.mutations) == 1
        assert result.mutations[0].feature == 'gene_a'
        assert result.mutations[0].position == 103

        # s1: detected, allele_freq=0.9
        assert result.matrix[0][0] == CompareCell(allele_freq=0.9, db_hit=False)
        # s2: in coverage gap, allele_freq=None
        assert result.matrix[1][0] == CompareCell(allele_freq=None, db_hit=False)

        # No db_hits in this scenario
        assert result.db_hit_map == [False]

    def test_position_outside_coverage_gap_gets_zero(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        # Sample 1: detects mutation at gene_a codon_pos=50 (display pos 51)
        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=50, ref_aa='A', alt_aa='V', allele_freq=0.7),
            ],
        )
        # Sample 2: has coverage gap at gene_a 100-110, but mutation is at 50 (pos 51)
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            sample_name='s2',
            variant_result=[],
            coverage_gap=[
                _coverage_gap_entry(feature_name='gene_a', codon_start=100, codon_end=110),
            ],
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert len(result.mutations) == 1
        # s2: position 51 is NOT in gap (gap is 101-111 in 1-based), so allele_freq=0.0
        assert result.matrix[1][0] == CompareCell(allele_freq=0.0, db_hit=False)


class TestBuildComparisonMatrixFormulaHit:
    """Test that formula_rule_hit matched_variants contribute db_hit flags."""

    def test_formula_hit_marks_db_hit(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        # Variant at codon_pos=102 (pos 103) with rule_match=0, but formula hit includes it
        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(
                    feature_name='gene_a',
                    codon_pos=102,
                    ref_aa='K',
                    alt_aa='N',
                    allele_freq=1.0,
                    rule_match=0,
                ),
            ],
            formula_rule_hit=[
                _formula_hit([
                    {
                        'feature': 'gene_a',
                        'codon_pos': 103,
                        'ref_aa': 'K',
                        'alt_aa': 'N',
                        'allele_freq': 1.0,
                    },
                ]),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert len(result.mutations) == 1
        # Formula hit should mark it as db_hit=True even though rule_match=0
        assert result.matrix[0][0] == CompareCell(allele_freq=1.0, db_hit=True)
        assert result.db_hit_map == [True]

    def test_formula_hit_does_not_add_new_mutation_key(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Formula-only variants should NOT create new mutation columns.

        A formula hit variant that does NOT appear in any sample's
        variant_result should not produce a mutation column (it would
        have allele_freq=0.0 for all samples with unknown consequence).
        """
        is_within, is_allowed = path_validators

        # A formula hit variant that does NOT appear in variant_result
        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=1.0),
            ],
            formula_rule_hit=[
                _formula_hit([
                    {
                        'feature': 'gene_a',
                        'codon_pos': 201,
                        'ref_aa': 'T',
                        'alt_aa': 'Y',
                        'allele_freq': 1.0,
                    },
                ]),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        # Should have 1 mutation: K103N (from variant_result only)
        # T201Y from formula hit should NOT create a new column
        assert len(result.mutations) == 1
        assert result.mutations[0].label == 'K103N'
        # The formula-only variant's db_hit status does not apply to a column
        # that does not exist
        assert result.db_hit_map == [False]


class TestBuildComparisonMatrixPathValidation:
    """Test path validation in build_comparison_matrix."""

    def test_path_not_ending_in_results_json(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators
        bad_path = results_dir / 'sample.report.html'
        bad_path.write_text('<html></html>')

        with pytest.raises(ValueError, match='must end with .results.json'):
            build_comparison_matrix([bad_path], results_dir, is_within, is_allowed)

    def test_path_outside_results_dir(
        self,
        results_dir: Path,
        path_validators,
        tmp_path: Path,
    ) -> None:
        is_within, is_allowed = path_validators
        outside_dir = tmp_path / 'outside'
        outside_dir.mkdir()
        outside_file = outside_dir / 'evil.results.json'
        outside_file.write_text('{}')

        with pytest.raises(ValueError, match='outside allowed results directory'):
            build_comparison_matrix([outside_file], results_dir, is_within, is_allowed)

    def test_path_not_found(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators
        missing = results_dir / 'nonexistent.20260601.results.json'

        with pytest.raises(ValueError, match='not found'):
            build_comparison_matrix([missing], results_dir, is_within, is_allowed)


# ---------------------------------------------------------------------------
# Endpoint-level tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def startup_config_with_results(
    project_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> StartupConfig:
    """Startup config with a results directory and API token."""
    monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'http://localhost:5173')
    data_dir = tmp_path / 'data'
    project_databases_dir = data_dir / 'project_databases'
    uploads_dir = data_dir / 'uploads'
    results_dir = data_dir / 'results'
    project_databases_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    import shutil
    bundled_project_db = project_databases_dir / project_db.name
    shutil.copy2(project_db, bundled_project_db)

    return StartupConfig(
        project_databases_dir=project_databases_dir.resolve(),
        uploads_dir=uploads_dir.resolve(),
        results_dir=results_dir.resolve(),
        data_dir=data_dir.resolve(),
        allowed_roots=(project_databases_dir.resolve(), uploads_dir.resolve(), results_dir.resolve()),
        api_token='test-token',
        project_db_uuid_index=build_project_db_uuid_index(project_databases_dir.resolve()),
    )


@pytest.fixture()
def auth_headers_for_compare(startup_config_with_results: StartupConfig) -> dict[str, str]:
    return {'Authorization': f'Bearer {startup_config_with_results.api_token}'}


@pytest.fixture()
def client_for_compare(startup_config_with_results: StartupConfig) -> TestClient:
    from web.backend.main import create_app
    from web.backend.queue import get_batch_queue, get_queue

    sync_queue = Queue('profiling', connection=fakeredis.FakeRedis(), is_async=False)
    app = create_app(startup_config=startup_config_with_results)
    app.dependency_overrides[get_queue] = lambda: sync_queue
    app.dependency_overrides[get_batch_queue] = lambda: sync_queue
    return TestClient(app)


class TestCompareEndpoint:
    """Integration tests for POST /api/compare."""

    def test_compare_endpoint_basic(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        results_dir = startup_config_with_results.results_dir

        _make_result_json(
            results_dir / 's1.20260601.results.json',
            project_name='db',
            sample_name='s1',
            reference_name='NC_001802.1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=0.95, rule_match=1),
            ],
        )
        _make_result_json(
            results_dir / 's2.20260602.results.json',
            project_name='db',
            sample_name='s2',
            reference_name='NC_001802.1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=0.3, rule_match=0),
                _variant(feature_name='gene_b', codon_pos=5, ref_aa='A', alt_aa='V', allele_freq=1.0, rule_match=1),
            ],
        )

        response = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 's1.20260601.results.json'),
                    str(results_dir / 's2.20260602.results.json'),
                ],
            },
            headers=auth_headers_for_compare,
        )

        assert response.status_code == 200
        data = response.json()
        assert data['samples'] == ['s1', 's2']
        assert data['references'] == ['NC_001802.1', 'NC_001802.1']
        assert len(data['mutations']) == 2
        assert data['mutation_labels'] == ['R11K', 'A6V']
        assert data['mutation_tick_labels'] == ['R11K', 'A6V']
        assert data['consequences'] == ['missense', 'missense']
        assert isinstance(data['feature_display_names'], dict)
        # s1 has R11K with rule_match=1 (db_hit), s2 has A6V with rule_match=1 (db_hit)
        assert data['db_hit_map'] == [True, True]

    def test_compare_endpoint_invalid_path(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        response = client_for_compare.post(
            '/api/compare',
            json={
                'paths': ['/etc/passwd'],
            },
            headers=auth_headers_for_compare,
        )
        # Path outside results dir -> 400
        assert response.status_code == 400

    def test_compare_endpoint_missing_file(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        results_dir = startup_config_with_results.results_dir
        missing = str(results_dir / 'nonexistent.20260601.results.json')

        response = client_for_compare.post(
            '/api/compare',
            json={'paths': [missing]},
            headers=auth_headers_for_compare,
        )
        assert response.status_code == 404

    def test_compare_endpoint_empty_paths(
        self,
        client_for_compare: TestClient,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        response = client_for_compare.post(
            '/api/compare',
            json={'paths': []},
            headers=auth_headers_for_compare,
        )
        assert response.status_code == 400

    def test_compare_endpoint_different_databases(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        results_dir = startup_config_with_results.results_dir

        _make_result_json(
            results_dir / 'hiv_s1.20260601.results.json',
            project_name='hiv-db',
            sample_name='hiv1',
        )
        _make_result_json(
            results_dir / 'hsv_s2.20260602.results.json',
            project_name='hsv-db',
            sample_name='hsv1',
        )

        response = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 'hiv_s1.20260601.results.json'),
                    str(results_dir / 'hsv_s2.20260602.results.json'),
                ],
            },
            headers=auth_headers_for_compare,
        )
        assert response.status_code == 400
        assert 'different databases' in response.json()['detail'].lower()

    def test_compare_endpoint_different_references(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        results_dir = startup_config_with_results.results_dir

        _make_result_json(
            results_dir / 'ref1_s1.20260601.results.json',
            project_name='db',
            sample_name='s1',
            reference_name='NC_001802.1',
        )
        _make_result_json(
            results_dir / 'ref2_s2.20260602.results.json',
            project_name='db',
            sample_name='s2',
            reference_name='K03455.1',
        )

        response = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 'ref1_s1.20260601.results.json'),
                    str(results_dir / 'ref2_s2.20260602.results.json'),
                ],
            },
            headers=auth_headers_for_compare,
        )
        assert response.status_code == 400
        assert 'different references' in response.json()['detail'].lower()

    def test_compare_endpoint_non_synonymous_only(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        results_dir = startup_config_with_results.results_dir

        _make_result_json(
            results_dir / 's1.20260601.results.json',
            project_name='db',
            sample_name='s1',
            reference_name='NC_001802.1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.9, rule_match=0),
                    'consequence': 'missense',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='T',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'synonymous',
                },
            ],
        )
        _make_result_json(
            results_dir / 's2.20260602.results.json',
            project_name='db',
            sample_name='s2',
            reference_name='NC_001802.1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.3, rule_match=0),
                    'consequence': 'missense',
                },
            ],
        )

        # Without filter: 2 mutations
        response_all = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 's1.20260601.results.json'),
                    str(results_dir / 's2.20260602.results.json'),
                ],
            },
            headers=auth_headers_for_compare,
        )
        assert response_all.status_code == 200
        assert len(response_all.json()['mutations']) == 2

        # With non_synonymous_only: 1 mutation (synonymous filtered)
        response_filtered = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 's1.20260601.results.json'),
                    str(results_dir / 's2.20260602.results.json'),
                ],
                'non_synonymous_only': True,
            },
            headers=auth_headers_for_compare,
        )
        assert response_filtered.status_code == 200
        data = response_filtered.json()
        assert len(data['mutations']) == 1
        assert data['mutations'][0]['label'] == 'R11K'
        assert data['consequences'] == ['missense']

    def test_compare_endpoint_db_hits_only(
        self,
        client_for_compare: TestClient,
        startup_config_with_results: StartupConfig,
        auth_headers_for_compare: dict[str, str],
    ) -> None:
        """POST /api/compare with db_hits_only flag filters the matrix."""
        results_dir = startup_config_with_results.results_dir

        _make_result_json(
            results_dir / 's1.20260601.results.json',
            project_name='db',
            sample_name='s1',
            reference_name='NC_001802.1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                         allele_freq=0.9, rule_match=1),
                _variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='Y',
                         allele_freq=1.0, rule_match=0),
            ],
        )
        _make_result_json(
            results_dir / 's2.20260602.results.json',
            project_name='db',
            sample_name='s2',
            reference_name='NC_001802.1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                         allele_freq=0.3, rule_match=0),
            ],
        )

        # Without filter: 2 mutations
        response_all = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 's1.20260601.results.json'),
                    str(results_dir / 's2.20260602.results.json'),
                ],
            },
            headers=auth_headers_for_compare,
        )
        assert response_all.status_code == 200
        assert len(response_all.json()['mutations']) == 2

        # With db_hits_only: 1 mutation (only the one with a db_hit across any sample)
        response_filtered = client_for_compare.post(
            '/api/compare',
            json={
                'paths': [
                    str(results_dir / 's1.20260601.results.json'),
                    str(results_dir / 's2.20260602.results.json'),
                ],
                'db_hits_only': True,
            },
            headers=auth_headers_for_compare,
        )
        assert response_filtered.status_code == 200
        data = response_filtered.json()
        assert len(data['mutations']) == 1
        assert data['mutations'][0]['label'] == 'R11K'
        assert data['db_hit_map'] == [True]

    """Test label disambiguation when two features share the same mutation position."""

    def test_overlapping_features_prefix_label(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """When two features have the same AA position, labels get feature prefix."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.9),
                _variant(feature_name='gene_b', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.7),
            ],
        )
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            sample_name='s2',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=102, ref_aa='K', alt_aa='N', allele_freq=0.5),
            ],
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        # Two distinct mutations at the same position from different features
        assert len(result.mutations) == 2
        # Labels should be prefixed with feature name since they collide
        assert 'gene_a:K103N' in result.mutation_labels
        assert 'gene_b:K103N' in result.mutation_labels
        # tick_labels are always AA-only even when collision occurs
        assert result.mutation_tick_labels == ['K103N', 'K103N']
        # consequences from variant_result entries
        assert result.consequences == ['missense', 'missense']


class TestBuildComparisonMatrixConsequences:
    """Test consequence collection for mutation columns."""

    def test_consequences_from_variant_result(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Consequences are collected from variant_result entries."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'missense',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='Y',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'stop_gained',
                },
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert len(result.mutations) == 2
        assert result.consequences == ['missense', 'stop_gained']

    def test_consequence_unknown_for_empty_consequence_field(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Mutations with empty consequence field default to 'unknown'."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
                    'consequence': '',
                },
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert len(result.mutations) == 1
        assert result.consequences == ['unknown']

    def test_consequence_from_first_sample_with_data(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """First sample with the mutation provides the consequence."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.9, rule_match=0),
                    'consequence': 'missense',
                },
            ],
        )
        p2 = _make_result_json(
            results_dir / 's2.20260602.results.json',
            sample_name='s2',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.5, rule_match=0),
                    'consequence': 'synonymous',
                },
            ],
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        # First sample's consequence is used
        assert result.consequences == ['missense']


class TestBuildComparisonMatrixTickLabels:
    """Test mutation_tick_labels always show AA-only format."""

    def test_tick_labels_no_collision(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """When no label collisions, tick labels match mutation labels."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert result.mutation_labels == ['R11K']
        assert result.mutation_tick_labels == ['R11K']

    def test_tick_labels_with_collision(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Tick labels remain AA-only even when mutation_labels have feature prefix."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=0.9),
                _variant(feature_name='gene_b', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=0.7),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        # mutation_labels include feature prefix due to collision
        assert result.mutation_labels == ['gene_a:R11K', 'gene_b:R11K']
        # tick_labels are always AA-only
        assert result.mutation_tick_labels == ['R11K', 'R11K']


class TestBuildComparisonMatrixFeatureDisplayNames:
    """Test feature display name resolution from project database."""

    def test_empty_when_no_project_db_path(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Return empty dict when project_db_path is not set."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            project_db_path='',
            variant_result=[
                _variant(codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert result.feature_display_names == {}

    def test_empty_when_db_file_missing(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Return empty dict when project_db_path points to a non-existent file."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            project_db_path='/nonexistent/path.db',
            variant_result=[
                _variant(codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert result.feature_display_names == {}

    def test_display_names_from_project_db(
        self,
        results_dir: Path,
        path_validators,
        tmp_path: Path,
    ) -> None:
        """Resolve display names for mat_peptide features from project DB."""
        is_within, is_allowed = path_validators

        from respro.db.schema import create_schema

        db_path = tmp_path / 'test_display.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('p', 6, 'u1')")
        conn.execute("INSERT INTO reference (project_id, name, length) VALUES (1, 'NC_001802.1', 1000)")
        conn.execute(
            "INSERT INTO feature (reference_id, name, protein, start, end, strand, "
            "nt_sequence, feature_type) VALUES (1, 'gag', 'Gag', 0, 100, '+', 'ATG', 'CDS')"
        )
        conn.execute(
            "INSERT INTO feature (reference_id, name, protein, start, end, strand, "
            "nt_sequence, feature_type) VALUES "
            "(1, 'pol_mat_peptide_1', 'Protease', 100, 200, '+', 'ATG', 'mat_peptide')"
        )
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (1, 'DrugA')"
        )
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, reference, "
            "mutation, phenotype) VALUES (1, 1, 1, 'K', 'E', 'resistant')"
        )
        conn.commit()
        conn.close()

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            reference_name='NC_001802.1',
            project_db_path=str(db_path),
            variant_result=[
                _variant(feature_name='gag', codon_pos=10, ref_aa='R', alt_aa='K', allele_freq=1.0),
            ],
        )

        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert result.feature_display_names == {
            'gag': 'gag',
            'pol_mat_peptide_1': 'Protease',
        }


class TestBuildComparisonMatrixNonSynonymousOnly:
    """Test non_synonymous_only filtering."""

    def test_filters_synonymous_mutations(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.9, rule_match=0),
                    'consequence': 'missense',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='T',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'synonymous',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=100, ref_aa='M', alt_aa='L',
                               allele_freq=0.7, rule_match=1),
                    'consequence': 'stop_gained',
                },
            ],
        )

        # Without filter: all 3 mutations
        result_all = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert len(result_all.mutations) == 3
        assert result_all.mutation_labels == ['R11K', 'T51T', 'M101L']
        assert result_all.consequences == ['missense', 'synonymous', 'stop_gained']

        # With filter: only 2 non-synonymous
        result_filtered = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, non_synonymous_only=True,
        )
        assert len(result_filtered.mutations) == 2
        assert result_filtered.mutation_labels == ['R11K', 'M101L']
        assert result_filtered.consequences == ['missense', 'stop_gained']
        assert result_filtered.matrix == [result_all.matrix[0][:1] + result_all.matrix[0][2:]]

    def test_non_synonymous_preserves_db_hit_map(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.9, rule_match=1),
                    'consequence': 'missense',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='T',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'synonymous',
                },
            ],
        )

        result = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, non_synonymous_only=True,
        )
        assert len(result.mutations) == 1
        assert result.mutations[0].label == 'R11K'
        assert result.db_hit_map == [True]

    def test_non_synonymous_empty_when_all_synonymous(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='R',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'synonymous',
                },
            ],
        )

        result = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, non_synonymous_only=True,
        )
        assert len(result.mutations) == 0
        assert result.matrix == [[]]
        assert result.consequences == []
        assert result.features == []
        assert result.feature_map == []


class TestBuildComparisonMatrixDbHitsOnly:
    """Test db_hits_only filtering."""

    def test_filters_non_db_hit_mutations(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                         allele_freq=0.9, rule_match=1),
                _variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='Y',
                         allele_freq=1.0, rule_match=0),
                _variant(feature_name='gene_a', codon_pos=100, ref_aa='M', alt_aa='L',
                         allele_freq=0.7, rule_match=0),
            ],
        )

        # Without filter: all 3 mutations
        result_all = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert len(result_all.mutations) == 3
        assert result_all.db_hit_map == [True, False, False]

        # With filter: only the db_hit mutation (R11K with rule_match=1)
        result_filtered = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, db_hits_only=True,
        )
        assert len(result_filtered.mutations) == 1
        assert result_filtered.mutations[0].label == 'R11K'
        assert result_filtered.db_hit_map == [True]
        assert result_filtered.matrix == [result_all.matrix[0][:1]]

    def test_db_hits_only_preserves_consequences(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                {
                    **_variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                               allele_freq=0.9, rule_match=1),
                    'consequence': 'missense',
                },
                {
                    **_variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='Y',
                               allele_freq=1.0, rule_match=0),
                    'consequence': 'stop_gained',
                },
            ],
        )

        result = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, db_hits_only=True,
        )
        assert len(result.mutations) == 1
        assert result.consequences == ['missense']

    def test_db_hits_only_empty_when_no_hits(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 's1.20260601.results.json',
            sample_name='s1',
            variant_result=[
                _variant(feature_name='gene_a', codon_pos=10, ref_aa='R', alt_aa='K',
                         allele_freq=0.9, rule_match=0),
                _variant(feature_name='gene_a', codon_pos=50, ref_aa='T', alt_aa='Y',
                         allele_freq=1.0, rule_match=0),
            ],
        )

        result = build_comparison_matrix(
            [p1], results_dir, is_within, is_allowed, db_hits_only=True,
        )
        assert len(result.mutations) == 0
        assert result.matrix == [[]]
        assert result.consequences == []
        assert result.db_hit_map == []
        assert result.features == []
        assert result.feature_map == []


class TestBuildComparisonMatrixSampleDisambiguation:
    """Test sample label disambiguation when names collide."""

    def test_no_collision_different_names(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Two samples with different sample_name values keep their names unchanged."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'alpha.20260601000000000001.results.json',
            sample_name='alpha',
        )
        p2 = _make_result_json(
            results_dir / 'beta.20260602000000000002.results.json',
            sample_name='beta',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert result.samples == ['alpha', 'beta']

    def test_collision_same_sample_name(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Two samples with same sample_name get disambiguated with filename."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'file_a.20260601000000000001.results.json',
            sample_name='sample',
        )
        p2 = _make_result_json(
            results_dir / 'file_b.20260602000000000002.results.json',
            sample_name='sample',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert result.samples == ['sample (file_a)', 'sample (file_b)']

    def test_three_way_collision(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Three samples with same sample_name all get disambiguated."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'run1.20260601000000000001.results.json',
            sample_name='mysample',
        )
        p2 = _make_result_json(
            results_dir / 'run2.20260602000000000002.results.json',
            sample_name='mysample',
        )
        p3 = _make_result_json(
            results_dir / 'run3.20260603000000000003.results.json',
            sample_name='mysample',
        )

        result = build_comparison_matrix([p1, p2, p3], results_dir, is_within, is_allowed)
        assert result.samples == [
            'mysample (run1)',
            'mysample (run2)',
            'mysample (run3)',
        ]

    def test_empty_sample_name_fallback(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Empty sample_name falls back to derived filename; collision still disambiguates."""
        is_within, is_allowed = path_validators

        # Single sample with empty sample_name -> uses derived name
        p1 = _make_result_json(
            results_dir / 'isolated.20260601000000000001.results.json',
            sample_name='',
        )
        result = build_comparison_matrix([p1], results_dir, is_within, is_allowed)
        assert result.samples == ['isolated']

    def test_empty_sample_name_collision_with_matching_derived(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Two samples: one with explicit name matching the other's derived name."""
        is_within, is_allowed = path_validators

        # Both resolve to 'alpha': one via sample_name, one via fallback
        p1 = _make_result_json(
            results_dir / 'alpha.20260601000000000001.results.json',
            sample_name='alpha',
        )
        p2 = _make_result_json(
            results_dir / 'alpha.20260602000000000002.results.json',
            sample_name='',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        # Both have name='alpha', so disambiguation kicks in
        assert result.samples == ['alpha (alpha)', 'alpha (alpha)']

    def test_mixed_collision_and_unique(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Mixed: two collide, one is unique — only colliding pair is disambiguated."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'dup_a.20260601000000000001.results.json',
            sample_name='dup',
        )
        p2 = _make_result_json(
            results_dir / 'dup_b.20260602000000000002.results.json',
            sample_name='dup',
        )
        p3 = _make_result_json(
            results_dir / 'unique.20260603000000000003.results.json',
            sample_name='unique',
        )

        result = build_comparison_matrix([p1, p2, p3], results_dir, is_within, is_allowed)
        assert result.samples == ['dup (dup_a)', 'dup (dup_b)', 'unique']

    def test_deduplication_same_path_passed_twice(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Same result file passed twice should only appear once."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'mysample.20260601000000000001.results.json',
            sample_name='mysample',
        )

        result = build_comparison_matrix([p1, p1], results_dir, is_within, is_allowed)
        assert result.samples == ['mysample']
        assert result.sample_disambiguation_note != ''
        assert 'merged' in result.sample_disambiguation_note.lower()

    def test_no_note_when_no_collision_or_dedup(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """No disambiguation note when all samples have unique names and no duplicates."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'alpha.20260601000000000001.results.json',
            sample_name='alpha',
        )
        p2 = _make_result_json(
            results_dir / 'beta.20260602000000000002.results.json',
            sample_name='beta',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert result.sample_disambiguation_note == ''

    def test_note_mentions_collision_when_names_collide(
        self,
        results_dir: Path,
        path_validators,
    ) -> None:
        """Disambiguation note mentions collisions when sample names collide."""
        is_within, is_allowed = path_validators

        p1 = _make_result_json(
            results_dir / 'file_a.20260601000000000001.results.json',
            sample_name='sample',
        )
        p2 = _make_result_json(
            results_dir / 'file_b.20260602000000000002.results.json',
            sample_name='sample',
        )

        result = build_comparison_matrix([p1, p2], results_dir, is_within, is_allowed)
        assert 'filename' in result.sample_disambiguation_note.lower()
