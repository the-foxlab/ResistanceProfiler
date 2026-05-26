"""
Tests for CLI commands (regenerate, classify) and manage database/results flows.
"""

from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from conftest import TINY_REF_SEQ, write_genbank
from typer.testing import CliRunner

from respro.cli.init import init_project
from respro.cli.main import app
from respro.core.rules import load_rules
from respro.db.models import (
    AnnotatedVariant,
    FormulaRuleHit,
    ProfilingResult,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
)
from respro.db.results import (
    load_classifications,
    load_coverage_gaps,
    load_formula_rule_hits,
    load_run,
    reconstruct_annotations,
    reconstruct_formula_rule_hits,
    save_run,
)
from respro.db.schema import init_results_db, open_project_db, open_results_db
from respro.io.reference import load_features_for_reference
from respro.report.non_html_exports import export_results


def _strip_ansi(text: str) -> str:
    """Return text with ANSI escape sequences removed."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _init_split_project(
    tmp_path: Path,
    *,
    feature_name: str,
    strand: str,
    segments: list[tuple[int, int]],
    reference_aa: str,
    mutation_aa: str,
) -> Path:
    gb_path = tmp_path / f'{feature_name}.gb'
    record = SeqRecord(Seq('GCT' * 120), id='tiny_ref', name='tiny_ref', description='')
    record.annotations['molecule_type'] = 'DNA'
    record.annotations['accessions'] = ['tiny_ref']
    record.features = [
        SeqFeature(
            CompoundLocation(
                [
                    FeatureLocation(start, end, strand=1 if strand == '+' else -1)
                    for start, end in segments
                ]
            ),
            type='CDS',
            qualifiers={
                'gene': [feature_name],
                'product': ['DNA polymerase'],
                'codon_start': ['1'],
            },
        )
    ]
    with open(gb_path, 'w') as handle:
        SeqIO.write([record], handle, 'genbank')

    rules_tsv = tmp_path / f'{feature_name}.tsv'
    rules_tsv.write_text(
        textwrap.dedent(
            f'''\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            {feature_name}\ttiny_ref\t2\t{reference_aa}\t{mutation_aa}\tDrugA\tresistant
            '''
        ),
        encoding='utf-8',
    )

    db_path = tmp_path / f'{feature_name}.db'
    init_project(
        db_path=db_path,
        name='split-test',
        genbank_paths=[gb_path],
        rules_tsv=rules_tsv,
        additional_info=False,
    )
    return db_path


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
            'manage', 'results', str(results_db), '--list',
        ])

        assert result.exit_code == 0, result.output
        assert '1' in result.output
        assert 'tiny_ref' in result.output

    def test_explore_empty_db_reports_no_results(self, tmp_path: Path) -> None:
        results_db = tmp_path / 'empty.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'manage', 'results', str(results_db), '--list',
        ])

        assert result.exit_code == 0, result.output
        assert 'No stored results found' in result.output

    def test_explore_missing_result_db_is_an_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, [
            'manage', 'results', str(tmp_path / 'nonexistent.db'), '--list',
        ])

        assert result.exit_code != 0

    def test_explore_no_flag_is_an_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ['manage'])

        assert result.exit_code != 0

    def test_explore_info_shows_non_empty_project_metadata(self, project_db: Path) -> None:
        conn = open_project_db(project_db)
        conn.execute(
            "UPDATE project SET name = ?, uuid = ?, metadata_maintainers = ?, metadata_contact = ?, "
            "metadata_license = ?, metadata_website = '' WHERE id = 1",
            (
                'Metadata Test DB',
                '123e4567-e89b-12d3-a456-426614174000',
                'Alice; Bob',
                'team@example.org',
                'MIT',
            ),
        )
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, ['manage', 'database', str(project_db), '--info'])

        assert result.exit_code == 0, result.output
        assert 'Metadata Test DB' in result.output
        assert '123e4567-e89b-12d3-a456-426614174000' in result.output
        assert 'Alice; Bob' in result.output
        assert 'team@example.org' in result.output
        assert 'MIT' in result.output
        assert 'Website' not in result.output

    def test_explore_runs_delete_removes_run(self, project_db: Path, sample_vcf: Path, sample_ref_fasta: Path, tmp_path: Path) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        delete_result = CliRunner().invoke(
            app,
            ['manage', 'results', str(results_db), '--delete', '1', '--force'],
        )
        assert delete_result.exit_code == 0, delete_result.output
        assert 'Deleted run 1' in delete_result.output

        list_result = CliRunner().invoke(app, ['manage', 'results', str(results_db), '--list'])
        assert list_result.exit_code == 0, list_result.output
        assert 'No stored results found' in list_result.output


class TestRegenerate:
    @pytest.mark.skip(reason='Report rework in progress')
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
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
            '--output', str(out_dir),
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
            '--results-db', str(results_db),
            '--run-id', '1',
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert '--project' in _strip_ansi(result.output)

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
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
        ])

        assert result.exit_code != 0
        assert '--output' in _strip_ansi(result.output)

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
            '--results-db', str(results_db),
            '--run-id', '999',
            '--project', str(project_db),
            '--output', str(tmp_path / 'out'),
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
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'uuid mismatch' in result.output.lower()

    @pytest.mark.skip(reason='Report rework in progress')
    def test_regenerate_from_json_generates_html_report(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        profile_out = tmp_path / 'profile_out'
        results_db = tmp_path / 'results.db'
        profile_result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(profile_out),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--export', 'json',
        ])
        assert profile_result.exit_code == 0, profile_result.output

        json_files = list(profile_out.glob('*.results.json'))
        assert len(json_files) == 1

        out_dir = tmp_path / 'regenerated_from_json'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--json', str(json_files[0]),
            '--project', str(project_db),
            '--output', str(out_dir),
            '--export', 'tabular',
        ])

        assert result.exit_code == 0, result.output
        html_files = list(out_dir.glob('*.html'))
        tabular_files = list(out_dir.glob('*.mutations.tsv'))
        assert len(html_files) == 1
        assert len(tabular_files) == 1

    @pytest.mark.skip(reason='Report rework in progress')
    def test_regenerate_from_json_generates_pdf_report(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        profile_out = tmp_path / 'profile_out_pdf'
        results_db = tmp_path / 'results_pdf.db'
        profile_result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(profile_out),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--export', 'json',
        ])
        assert profile_result.exit_code == 0, profile_result.output

        json_files = list(profile_out.glob('*.results.json'))
        assert len(json_files) == 1

        out_dir = tmp_path / 'regenerated_from_json_pdf'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--json', str(json_files[0]),
            '--project', str(project_db),
            '--output', str(out_dir),
            '--export', 'pdf',
            '--export', 'tabular',
        ])

        assert result.exit_code == 0, result.output
        pdf_files = list(out_dir.glob('*.report.pdf'))
        tabular_files = list(out_dir.glob('*.mutations.tsv'))
        assert len(pdf_files) == 1
        assert len(tabular_files) == 1

    def test_regenerate_from_json_rejects_invalid_json(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        invalid_json = tmp_path / 'invalid.results.json'
        invalid_json.write_text('{"run": {"project_name": "x"}}', encoding='utf-8')

        result = CliRunner().invoke(app, [
            'regenerate',
            '--json', str(invalid_json),
            '--project', str(project_db),
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'invalid results json' in result.output.lower()

    def test_regenerate_from_json_uuid_mismatch_raises_error(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        profile_out = tmp_path / 'profile_out'
        results_db = tmp_path / 'results.db'
        profile_result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(profile_out),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--export', 'json',
        ])
        assert profile_result.exit_code == 0, profile_result.output

        json_path = list(profile_out.glob('*.results.json'))[0]
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        payload['run']['project_fingerprint'] = 'not-the-active-uuid'
        tampered_path = tmp_path / 'tampered.results.json'
        tampered_path.write_text(json.dumps(payload), encoding='utf-8')

        result = CliRunner().invoke(app, [
            'regenerate',
            '--json', str(tampered_path),
            '--project', str(project_db),
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        output_lower = result.output.lower()
        assert 'uuid mismatch' in output_lower
        assert 'database updates currently do not allow' in output_lower

    @pytest.mark.skip(reason='Report rework in progress')
    def test_regenerate_restores_persisted_formula_rule_hits(
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
            feature_name='gag',
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
                feature_name='gag',
                feature_id=1,
                reference_identifier='tiny_ref',
                position=1,
                reference='K',
                mutation='E',
            ),
            ResistanceRuleSetMember(
                id=2,
                rule_set_id=1,
                feature_name='gag',
                feature_id=1,
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
            formula_hits=[FormulaRuleHit(rule_set=rule_set, matched_variants=[ann])],
        )
        save_run(results_conn, project_db.resolve(), project_conn, result_obj)
        project_conn.close()
        results_conn.close()

        out_dir = tmp_path / 'regenerated_combo'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
            '--output', str(out_dir),
        ])

        assert result.exit_code == 0, result.output
        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'combo_regen_test' in html
        assert 'TestDrug' in html

    def test_regenerate_handles_negative_strand_split_feature_roundtrip(self, tmp_path: Path) -> None:
        project_db = _init_split_project(
            tmp_path,
            feature_name='split_neg',
            strand='-',
            segments=[(30, 48), (90, 108)],
            reference_aa='S',
            mutation_aa='A',
        )
        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(project_db)

        try:
            ref_row = project_conn.execute(
                'SELECT id, length FROM reference WHERE name = ?',
                ('tiny_ref',),
            ).fetchone()
            assert ref_row is not None

            rules = load_rules(project_conn, int(ref_row['id']))
            features = load_features_for_reference(project_conn, int(ref_row['id']))
            assert len(features) == 1
            assert len(features[0].segments) == 2

            ann = AnnotatedVariant(
                variant=VariantCall(
                    chrom='tiny_ref',
                    pos=95,
                    ref='G',
                    alt='C',
                    allele_freq=0.98,
                    depth=400,
                ),
                feature_name='split_neg',
                codon_pos=1,
                ref_codon='AGC',
                alt_codon='GCC',
                ref_aa='S',
                alt_aa='A',
                consequence='missense',
                af_bin='high',
                rule_matches=rules,
            )
            save_run(
                results_conn,
                project_db.resolve(),
                project_conn,
                ProfilingResult(
                    project_name='split-test',
                    reference_name='tiny_ref',
                    reference_length_nt=int(ref_row['length'] or 0),
                    sample_name='split-neg-sample',
                    vcf_name='split-neg.vcf',
                    total_variants=1,
                    variants_in_cds=1,
                    resistance_hits=1,
                    annotations=[ann],
                ),
            )
        finally:
            project_conn.close()
            results_conn.close()

        out_dir = tmp_path / 'regenerated_split_neg'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(project_db),
            '--output', str(out_dir),
        ])

        assert result.exit_code == 0, result.output
        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text(encoding='utf-8')
        assert 'split_neg' in html
        assert 'split-neg-sample' in html

    @pytest.mark.skip(reason='Report rework in progress')
    def test_regenerate_surfaces_manual_classifications_in_html(
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
            '--results-db', str(results_db),
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
        combo_rows = load_formula_rule_hits(results_conn, 1)
        sample_classifications = load_classifications(results_conn, 1)

        project_conn = open_project_db(project_db)
        ref_row = project_conn.execute(
            'SELECT id, organism, length FROM reference WHERE name = ?',
            (run_dict['reference_name'],),
        ).fetchone()
        assert ref_row is not None

        annotations = reconstruct_annotations(variant_rows)
        formula_hits = reconstruct_formula_rule_hits(combo_rows, annotations)
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
            formula_hits=formula_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
        )

        out_dir = tmp_path / 'regenerated_with_classification'
        rules = load_rules(project_conn, int(ref_row['id']))
        features = load_features_for_reference(project_conn, int(ref_row['id']))
        outputs = export_results(
            profiling_result,
            out_dir,
            features=features,
            rule_feature_names={rule.feature_name for rule in rules},
            project_conn=project_conn,
            rules=rules,
        )
        results_conn.close()
        project_conn.close()

        assert 'html' in outputs

        html_files = list(out_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'Manual classification' in html
        assert 'Acyclovir' in html
        assert 'User comment:' in html
        assert 'manual review' in html
        assert html.index('Interpretation summary') < html.index('Manual classification')


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
            '--results-db', str(results_db),
            '--run-id', '1',
            '--phenotype', 'resistant',
            '--drug', 'Acyclovir',
            '--note', 'manual review',
        ])

        assert result.exit_code == 0, result.output
        assert 'Classification saved for run #1' in result.output

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
            '--results-db', str(results_db),
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
            '--results-db', str(results_db),
            '--run-id', '999',
            '--drug', 'Acyclovir',
            '--phenotype', 'resistant',
        ])

        assert result.exit_code != 0
        assert '999' in result.output

    def test_classify_replaces_existing_classification(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        first = CliRunner().invoke(app, [
            'classify',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--drug', 'Acyclovir',
            '--phenotype', 'resistant',
            '--note', 'first note',
        ])
        assert first.exit_code == 0, first.output

        second = CliRunner().invoke(app, [
            'classify',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--drug', 'Acyclovir',
            '--phenotype', 'sensitive',
            '--note', 'updated note',
        ])
        assert second.exit_code == 0, second.output

        conn = open_results_db(results_db)
        rows = load_classifications(conn, 1)
        conn.close()

        assert len(rows) == 1
        assert rows[0]['phenotype'] == 'sensitive'
        assert rows[0]['note'] == 'updated note'


class TestSync:
    def test_sync_updates_all_runs_via_manage_results_sync(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        results_db = tmp_path / 'results.db'
        _run_profile(project_db, sample_vcf, sample_ref_fasta, results_db, tmp_path)

        result = CliRunner().invoke(app, [
            'manage', 'results', str(results_db),
            '--sync', str(project_db),
        ])

        assert result.exit_code == 0, result.output
        assert 'synced' in result.output.lower()

    def test_sync_fingerprint_mismatch_is_reported_and_skipped(
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
            'manage', 'results', str(results_db),
            '--sync', str(project_db),
        ])

        assert result.exit_code == 0, result.output
        assert 'fingerprint' in result.output.lower()


class TestExploreRules:
    def test_rules_shows_rules(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'manage', 'database', str(project_db), '--rules',
        ])

        assert result.exit_code == 0, result.output
        # project_db is built from conftest fixtures; should have at least one rule.
        # Verify table output contains expected columns and at least one rule
        assert 'Reference' in result.output
        assert 'Feature' in result.output
        assert 'Drug' in result.output
        assert 'TestDrug' in result.output

    def test_rules_with_reference_filter(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'manage', 'database', str(project_db), '--rules',
            '--reference', 'tiny_ref',
        ])

        assert result.exit_code == 0, result.output

    def test_rules_unknown_reference_raises_error(
        self,
        project_db: Path,
    ) -> None:
        result = CliRunner().invoke(app, [
            'manage', 'database', str(project_db), '--rules',
            '--reference', 'nonexistent_genome',
        ])

        assert result.exit_code != 0
        assert 'No reference matching' in result.output

    def test_rules_output_includes_browse_compatible_optional_columns(
        self,
        project_db: Path,
    ) -> None:
        conn = sqlite3.connect(project_db)
        conn.execute(
            "UPDATE resistance_rule SET publication = ?, comment = ? WHERE id = 1",
            ('10.1000/example-doi', 'example-comment'),
        )
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, [
            'manage', 'database', str(project_db), '--rules',
        ])

        assert result.exit_code == 0, result.output
        assert 'DOI' in result.output
        assert 'Comment' in result.output
        assert '10.1000/example-doi' in result.output
        assert 'example-comment' in result.output

    def test_rules_lists_single_and_combi_with_rendered_expression(self, tmp_path: Path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'tiny_ref',
                    'accession': 'tiny_ref',
                    'sequence': TINY_REF_SEQ,
                    'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id\n'
            'gag\ttiny_ref\t2\tK\tE\tDrugA\tunknown\tgroup_1\tmut_k2e\n'
            'gag\ttiny_ref\t6\tP\tV\tDrugA\tunknown\tgroup_1\tmut_p6v\n',
            encoding='utf-8',
        )
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(
            'group_id\tantiviral\texpression\tphenotype\n'
            'group_1\tDrugA\tmut_k2e AND mut_p6v\tresistant\n',
            encoding='utf-8',
        )

        db_path = tmp_path / 'project.db'
        init_project(
            db_path=db_path,
            name='Combo Project',
            genbank_paths=[genbank_path],
            rules_tsv=rules_tsv,
            formula_rules_tsv=formula_tsv,
            additional_info=False,
        )

        both = CliRunner().invoke(app, ['manage', 'database', str(db_path), '--rules'])
        assert both.exit_code == 0, both.output
        assert 'Single rules' in both.output
        assert 'Combination rules' in both.output
        assert 'gag:K2E' in both.output
        assert 'gag:P6V' in both.output
        assert 'AND' in both.output

        combi_only = CliRunner().invoke(
            app,
            ['manage', 'database', str(db_path), '--rules', '--list-combi'],
        )
        assert combi_only.exit_code == 0, combi_only.output
        assert 'Combination rules' in combi_only.output
        assert 'Single rules' not in combi_only.output

        single_only = CliRunner().invoke(
            app,
            ['manage', 'database', str(db_path), '--rules', '--list-single'],
        )
        assert single_only.exit_code == 0, single_only.output
        assert 'Single rules' in single_only.output
        assert 'Combination rules' not in single_only.output

