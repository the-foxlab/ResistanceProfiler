"""
Sync recovery without the query-reference cache.

``respro sync`` re-annotates stored runs; for FASTA-mode runs it must recover
the profiled feature set from persisted run data (the ``profiled_feature``
table), not from the project-DB query cache — the cache is privacy-minimal and
stores only checksums and CIGAR mappings, so no query header or sequence can
be recovered from it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ
from typer.testing import CliRunner

from respro.cli.main import app


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _add_zero_hit_drug(project_db: Path) -> None:
    """Add OtherDrug with a rule the sample never hits, plus by_phenotype assessment."""
    conn = sqlite3.connect(project_db)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO drug (project_id, name) VALUES (?, ?)", (1, 'OtherDrug'))
    other_drug_id = conn.execute(
        "SELECT id FROM drug WHERE name = 'OtherDrug'"
    ).fetchone()['id']
    conn.execute(
        'INSERT INTO resistance_rule (feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, other_drug_id, 10, 'A', 'V', 'resistant'),
    )
    conn.execute(
        'INSERT INTO interpretation_algorithm (project_id, algorithm_name, config_json) '
        'VALUES (?, ?, ?)',
        (1, 'drug_interpretation', json.dumps({'name': 'drug_interpretation', 'method': 'by_phenotype'})),
    )
    conn.commit()
    conn.close()


def _profile_fasta(project_db: Path, tmp_path: Path) -> tuple[Path, Path]:
    # One-SNP consensus: produces a TestDrug hit at codon 1 so OtherDrug
    # (rule at codon 10, never hit) is assessed as susceptible.
    query_seq = list(TINY_REF_SEQ)
    query_seq[4] = 'G' if query_seq[4] != 'G' else 'C'
    query_fasta = tmp_path / 'query.fasta'
    query_fasta.write_text(f'>patient_sample\n{"".join(query_seq)}\n')

    results_db = tmp_path / 'results.db'
    run_result = CliRunner().invoke(app, [
        'fasta',
        '--project', str(project_db),
        '--fasta', str(query_fasta),
        '--results-db', str(results_db),
        '--output', str(tmp_path / 'fasta_out'),
    ])
    assert run_result.exit_code == 0, run_result.output
    return results_db, query_fasta


def _purge_query_cache(project_db: Path) -> None:
    """Remove every cached query reference and mapping from the project DB."""
    conn = sqlite3.connect(project_db)
    conn.execute('DELETE FROM query_feature_mapping')
    conn.execute('DELETE FROM query_reference')
    conn.commit()
    conn.close()


class TestSyncWithoutQueryCache:
    def test_sync_fasta_run_succeeds_with_purged_cache(
        self, project_db: Path, tmp_path: Path
    ) -> None:
        results_db, _ = _profile_fasta(project_db, tmp_path)
        _purge_query_cache(project_db)

        result = CliRunner().invoke(app, [
            'manage', 'results', str(results_db),
            '--sync', str(project_db),
        ])

        assert result.exit_code == 0, _strip_ansi(result.output)
        assert 'synced' in result.output.lower()

    def test_sync_preserves_zero_hit_susceptible_drugs(
        self, project_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _add_zero_hit_drug(project_db)
        results_db, _ = _profile_fasta(project_db, tmp_path)
        _purge_query_cache(project_db)

        monkeypatch.chdir(tmp_path)
        sync_result = CliRunner().invoke(app, [
            'manage', 'results', str(results_db),
            '--sync', str(project_db),
        ])
        assert sync_result.exit_code == 0, _strip_ansi(sync_result.output)

        html_files = list(tmp_path.glob('*.html'))
        assert html_files, 'sync must write an HTML report'
        html = html_files[0].read_text()
        assert 'TestDrug' in html
        assert 'OtherDrug' in html
        assert 'Drugs assessed as susceptible' in html
