"""Tests for strict phenotype labels in the CLI classify command (T6)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from respro.cli.main import app
from respro.db.results import load_classifications
from respro.db.schema import init_results_db, open_results_db


@pytest.fixture()
def results_db(tmp_path: Path) -> Path:
    """Create a results DB with one run to classify."""
    db_path = tmp_path / 'results.db'
    conn = init_results_db(db_path)
    conn.execute(
        'INSERT INTO run (project_name, project_db_path, reference_name, vcf_path) '
        "VALUES ('T', '/tmp/p.db', 'ref', '/tmp/sample.vcf')"
    )
    conn.commit()
    conn.close()
    return db_path


class TestClassifyStrictLabels:
    """`respro classify` rejects old S/I/R shorthand and accepts labels/ranks."""

    def test_rejects_old_shorthand_r(self, results_db: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--phenotype', 'r',
            ],
        )
        assert result.exit_code != 0
        assert 'Unknown phenotype label' in result.output

    def test_rejects_old_shorthand_s(self, results_db: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--phenotype', 's',
            ],
        )
        assert result.exit_code != 0
        assert 'Unknown phenotype label' in result.output

    def test_rejects_old_shorthand_res(self, results_db: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--phenotype', 'res',
            ],
        )
        assert result.exit_code != 0
        assert 'Unknown phenotype label' in result.output

    def test_bare_rank_resolves_to_canonical_label(self, results_db: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--phenotype', '3',
            ],
        )
        assert result.exit_code == 0, result.output
        conn = open_results_db(results_db)
        rows = load_classifications(conn, 1)
        conn.close()
        assert rows[0]['phenotype'] == 'low-level resistance'

    def test_verbatim_label_stored_lowercased(self, results_db: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--phenotype', 'High-Level Resistance',
            ],
        )
        assert result.exit_code == 0, result.output
        conn = open_results_db(results_db)
        rows = load_classifications(conn, 1)
        conn.close()
        assert rows[0]['phenotype'] == 'high-level resistance'

    def test_empty_phenotype_allowed(self, results_db: Path) -> None:
        # Empty phenotype is allowed (rank 0); but a non-phenotype field must be set.
        result = CliRunner().invoke(
            app,
            [
                'classify', '--results-db', str(results_db), '--run-id', '1',
                '--drug', 'ACV', '--ic50', '5.0',
            ],
        )
        assert result.exit_code == 0, result.output
