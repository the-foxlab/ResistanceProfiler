"""
Tests for the CLI regenerate command.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from respro.cli import app
from respro.db.results import save_run
from respro.db.schema import init_results_db, open_project_db
from respro.db.models import (
    AnnotatedVariant,
    ComboRuleHit,
    ProfilingResult,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
)


def _run_profile(project_db: Path, sample_vcf: Path, sample_ref_fasta: Path, results_db: Path, tmp_path: Path) -> None:
    """Helper: run profile-vcf with --results-db and assert success."""
    result = CliRunner().invoke(app, [
        'profile-vcf',
        '--project', str(project_db),
        '--vcf', str(sample_vcf),
        '--ref-fasta', str(sample_ref_fasta),
        '--results-db', str(results_db),
        '--output', str(tmp_path / 'profile_out'),
        '--min-af', '0.01',
        '--min-depth', '0',
    ])
    assert result.exit_code == 0, result.output


class TestRegenerateListCommand:
    def test_list_shows_stored_runs(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--list',
        ])

        assert result.exit_code == 0, result.output
        assert '1' in result.output
        assert 'tiny_ref' in result.output

    def test_list_with_empty_db_reports_no_results(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'empty.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--list',
        ])

        assert result.exit_code == 0, result.output
        assert 'No stored results found' in result.output

    def test_list_and_identifier_together_is_an_error(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'db.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--list',
            '--identifier', '1',
        ])

        assert result.exit_code != 0
        assert 'either' in result.output.lower() or 'not both' in result.output.lower()

    def test_no_action_flag_is_an_error(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'db.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
        ])

        assert result.exit_code != 0


class TestRegenerateByIdentifier:
    def test_regenerate_generates_html_report(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        out_dir = tmp_path / 'regenerated'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '1',
            '--project', str(project_db),
            '--out', str(out_dir),
        ])

        assert result.exit_code == 0, result.output
        assert 'Regenerated run #1' in result.output
        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        assert 'ResistanceProfiler' in html_files[0].read_text()

    def test_regenerate_missing_project_flag_is_an_error(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'db.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '1',
            '--out', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert '--project' in result.output

    def test_regenerate_missing_out_flag_is_an_error(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'db.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '1',
            '--project', str(project_db),
        ])

        assert result.exit_code != 0
        assert '--out' in result.output

    def test_regenerate_unknown_run_id_raises_error(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'db.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '999',
            '--project', str(project_db),
            '--out', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert '999' in result.output

    def test_regenerate_fingerprint_mismatch_raises_error(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        # Tamper with the stored fingerprint so it no longer matches.
        conn = sqlite3.connect(results_db)
        conn.execute("UPDATE run SET project_fingerprint = 'deadbeef' WHERE id = 1")
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '1',
            '--project', str(project_db),
            '--out', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'fingerprint' in result.output.lower()

    def test_regenerate_restores_persisted_combo_rule_hits(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(project_db)

        variant = VariantCall(chrom='tiny_ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=100)
        ann = AnnotatedVariant(
            variant=variant,
            gene_name='gag',
            codon_pos=1,
            ref_codon='AAA',
            alt_codon='GAA',
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
        )
        rule_set = ResistanceRuleSet(
            id=1,
            drug_name='TestDrug',
            drug_id=1,
            phenotype='resistant',
            group_name='combo_regen_test',
        )
        rule_set.members = [
            ResistanceRuleSetMember(
                id=1,
                rule_set_id=1,
                gene_name='gag',
                gene_id=1,
                reference_identifier='tiny_ref',
                position=1,
                reference='K',
                mutation='E',
            ),
            ResistanceRuleSetMember(
                id=2,
                rule_set_id=1,
                gene_name='gag',
                gene_id=1,
                reference_identifier='tiny_ref',
                position=5,
                reference='A',
                mutation='V',
            ),
        ]
        result_obj = ProfilingResult(
            project_name='Test Project',
            reference_name='tiny_ref',
            sample_name='sample_combo',
            vcf_name='sample.vcf',
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
            combo_hits=[ComboRuleHit(rule_set=rule_set, matched_variants=[ann])],
        )
        save_run(results_conn, project_db.resolve(), project_conn, result_obj)
        project_conn.close()
        results_conn.close()

        out_dir = tmp_path / 'regenerated_combo'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--result-db', str(results_db),
            '--identifier', '1',
            '--project', str(project_db),
            '--out', str(out_dir),
        ])

        assert result.exit_code == 0, result.output
        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'combo_regen_test' in html
        assert 'TestDrug' in html

