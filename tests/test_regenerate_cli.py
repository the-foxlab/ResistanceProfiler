"""
Tests for the CLI runs commands (regenerate, classify, sync) and explore run listing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from respro.cli.main import app
from respro.core.rules import load_rules
from respro.db.results import save_run
from respro.db.results import (
    load_classifications,
    load_combo_rule_hits,
    load_coverage_gaps,
    load_run,
    reconstruct_annotations,
    reconstruct_combo_rule_hits,
)
from respro.db.schema import init_results_db, open_project_db
from respro.db.schema import open_results_db
from respro.db.models import (
    AnnotatedVariant,
    ComboRuleHit,
    ProfilingResult,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
)
from respro.io.reference import load_genes_for_reference
from respro.report.html import export_results


def _run_profile(project_db: Path, sample_vcf: Path, sample_ref_fasta: Path, results_db: Path, tmp_path: Path) -> None:
    """Helper: run vcf with --results-db and assert success."""
    result = CliRunner().invoke(app, [
        'vcf',
        '--project', str(project_db),
        '--vcf', str(sample_vcf),
        '--ref-fasta', str(sample_ref_fasta),
        '--results-db', str(results_db),
        '--output', str(tmp_path / 'profile_out'),
        '--min-af', '0.01',
        '--min-depth', '0',
    ])
    assert result.exit_code == 0, result.output


class TestExploreRuns:
    def test_explore_shows_stored_runs(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'explore', '--results', str(results_db),
        ])

        assert result.exit_code == 0, result.output
        assert '1' in result.output
        assert 'tiny_ref' in result.output

    def test_explore_empty_db_reports_no_results(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'empty.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'explore', '--results', str(results_db),
        ])

        assert result.exit_code == 0, result.output
        assert 'No stored results found' in result.output

    def test_explore_missing_result_db_is_an_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, [
            'explore', '--results', str(tmp_path / 'nonexistent.db'),
        ])

        assert result.exit_code != 0

    def test_explore_no_flag_is_an_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ['explore'])

        assert result.exit_code != 0


class TestRegenerate:
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
            '--run-id', '1',
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
            '--run-id', '1',
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
            '--run-id', '1',
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
            '--run-id', '999',
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
            '--run-id', '1',
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
            '--run-id', '1',
            '--project', str(project_db),
            '--out', str(out_dir),
        ])

        assert result.exit_code == 0, result.output
        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'combo_regen_test' in html
        assert 'TestDrug' in html

    def test_regenerate_surfaces_manual_classifications_in_html_and_json(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        classify_result = CliRunner().invoke(app, [
            'classify',
            '--result-db', str(results_db),
            '--run-id', '1',
            '--drug', 'Acyclovir',
            '--phenotype', 'resistant',
            '--note', 'manual review',
            '--source', 'lab report',
        ])
        assert classify_result.exit_code == 0, classify_result.output

        results_conn = open_results_db(results_db)
        run_dict, variant_rows = load_run(results_conn, 1)
        coverage_gaps = load_coverage_gaps(results_conn, 1)
        combo_rows = load_combo_rule_hits(results_conn, 1)
        sample_classifications = load_classifications(results_conn, 1)

        project_conn = open_project_db(project_db)
        ref_row = project_conn.execute(
            'SELECT id, organism, length FROM reference WHERE name = ?',
            (run_dict['reference_name'],),
        ).fetchone()
        assert ref_row is not None

        annotations = reconstruct_annotations(variant_rows)
        combo_hits = reconstruct_combo_rule_hits(combo_rows, annotations)
        profiling_result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=ref_row['organism'] or '',
            reference_name=run_dict['reference_name'],
            reference_length_nt=int(ref_row['length'] or 0),
            sample_name=run_dict.get('sample_name', ''),
            vcf_name=run_dict['vcf_path'],
            run_timestamp=run_dict.get('created_at', ''),
            total_variants=run_dict.get('total_variants', 0),
            variants_in_cds=run_dict.get('variants_in_cds', 0),
            resistance_hits=run_dict.get('resistance_hits', 0),
            annotations=annotations,
            combo_hits=combo_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
        )

        out_dir = tmp_path / 'regenerated_with_classification'
        rules = load_rules(project_conn, int(ref_row['id']))
        genes = load_genes_for_reference(project_conn, int(ref_row['id']))
        outputs = export_results(
            profiling_result,
            out_dir,
            genes=genes,
            rule_gene_names={rule.gene_name for rule in rules},
            project_conn=project_conn,
            rules=rules,
        )
        results_conn.close()
        project_conn.close()

        assert 'html' in outputs
        assert 'json' in outputs

        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'Manual classifications' in html
        assert 'Acyclovir' in html
        assert 'manual review' in html

        json_files = list(out_dir.glob('*.results.json'))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert 'sample_classifications' in payload
        assert len(payload['sample_classifications']) == 1
        assert payload['sample_classifications'][0]['drug'] == 'Acyclovir'
        assert payload['sample_classifications'][0]['note'] == 'manual review'


class TestClassify:
    def test_classify_appends_row(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'classify',
            '--result-db', str(results_db),
            '--run-id', '1',
            '--phenotype', 'resistant',
            '--drug', 'Acyclovir',
            '--note', 'manual review',
        ])

        assert result.exit_code == 0, result.output
        assert 'Classification #1 saved' in result.output

    def test_classify_requires_at_least_one_value(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'classify',
            '--result-db', str(results_db),
            '--run-id', '1',
            '--drug', 'Acyclovir',
        ])

        assert result.exit_code != 0
        assert 'at least one' in result.output.lower() or 'required' in result.output.lower()

    def test_classify_unknown_run_id_raises_error(
        self,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'empty.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'classify',
            '--result-db', str(results_db),
            '--run-id', '999',
            '--drug', 'Acyclovir',
            '--phenotype', 'resistant',
        ])

        assert result.exit_code != 0
        assert '999' in result.output


class TestSync:
    def test_sync_updates_all_runs_when_no_run_id(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'sync',
            '--result-db', str(results_db),
            '--project', str(project_db),
        ])

        assert result.exit_code == 0, result.output
        assert 'synced' in result.output.lower()

    def test_sync_fingerprint_mismatch_raises_error(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        conn = sqlite3.connect(results_db)
        conn.execute("UPDATE run SET project_fingerprint = 'deadbeef' WHERE id = 1")
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, [
            'sync',
            '--result-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
        ])

        assert result.exit_code != 0
        assert 'fingerprint' in result.output.lower()


class TestExploreRules:
    def test_rules_shows_rules(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'explore', '--rules', str(project_db),
        ])

        assert result.exit_code == 0, result.output
        # project_db is built from conftest fixtures; should have at least one rule.
        # Verify table output contains expected columns and at least one rule
        assert 'Reference' in result.output
        assert 'Gene' in result.output
        assert 'Drug' in result.output
        assert 'TestDrug' in result.output

    def test_rules_with_reference_filter(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'explore', '--rules', str(project_db),
            '--reference', 'tiny_ref',
        ])

        assert result.exit_code == 0, result.output

    def test_rules_unknown_reference_raises_error(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'explore', '--rules', str(project_db),
            '--reference', 'nonexistent_genome',
        ])

        assert result.exit_code != 0
        assert 'No reference matching' in result.output

