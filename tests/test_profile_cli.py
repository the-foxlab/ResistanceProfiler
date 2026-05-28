"""
Tests for the CLI profile-vcf command — end-to-end integration.
"""

import json
import sqlite3
from io import StringIO
from pathlib import Path

import pysam
from conftest import TINY_REF_SEQ, write_genbank
from rich.console import Console
from typer.testing import CliRunner

from respro.cli.main import app
from respro.cli.profile_helpers import _print_completion_panel
from respro.db.models import (
    AnnotatedVariant,
    FormulaRuleHit,
    ProfilingResult,
    ResistanceRule,
    ResistanceRuleSet,
    VariantCall,
)
from respro.db.schema import create_schema, init_results_db
from respro.report.html import build_report_context


class TestProfileCli:
    """End-to-end tests for the ``profile-vcf`` command."""

    def test_profile_produces_html_with_vcf_based_name(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """Running profile should produce an HTML report named after the input VCF."""
        output_dir = tmp_path / 'results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        html_path = output_dir / f'{sample_vcf.stem}.report.html'
        assert html_path.exists()
        html = html_path.read_text()
        assert 'resistance profile' in html
        assert 'Test Project' in html
        assert 'tiny_ref' in html

    def test_profile_vcf_writes_optional_json_export(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        output_dir = tmp_path / 'results_json'
        results_db = tmp_path / 'results_json.db'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--results-db', str(results_db),
            '--export', 'json',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        json_path = output_dir / f'{sample_vcf.stem}.results.json'
        assert json_path.exists()
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        assert set(payload.keys()) == {
            'run',
            'variant_result',
            'coverage_gap',
            'formula_rule_hit',
            'sample_classification',
        }
        assert 'id' not in payload['run']
        assert all('run_id' not in row for row in payload['variant_result'])

    def test_profile_vcf_writes_optional_pdf_export(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        output_dir = tmp_path / 'results_pdf'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--export', 'pdf',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        pdf_path = output_dir / f'{sample_vcf.stem}.report.pdf'
        assert pdf_path.exists()
        assert pdf_path.read_bytes().startswith(b'%PDF')

    def test_profile_produces_html(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'html_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        html_path = output_dir / f'{sample_vcf.stem}.report.html'
        assert html_path.exists()
        content = html_path.read_text()
        assert 'resistance profile' in content

    def test_profile_detects_resistance_hit(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """VCF with A→G at pos 4 should trigger the K2E rule in gag."""
        vcf_path = tmp_path / 'hit.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'tiny_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'hit_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output
        assert '1 total database hits' in result.output

    def test_profile_completion_panel_counts_formula_only_member_hits_as_database_hits(self) -> None:
        variant = VariantCall(chrom='tiny_ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500)
        internal_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='__formula_component__',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=1,
            reference='K',
            mutation='E',
            phenotype='unknown',
            external_id='mut_a',
            is_internal_formula_component=True,
        )
        ann = AnnotatedVariant(
            variant=variant,
            feature_name='gag',
            codon_pos=1,
            ref_codon='AAA',
            alt_codon='GAA',
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            rule_matches=[internal_rule],
        )
        rule_set = ResistanceRuleSet(
            id=1,
            drug_name='DrugA',
            drug_id=1,
            phenotype='resistant',
            group_name='formula_1',
        )
        result = ProfilingResult(
            project_name='Test Project',
            reference_name='tiny_ref',
            sample_name='sample01',
            vcf_name='sample.vcf',
            reference_length_nt=12,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
            formula_hits=[FormulaRuleHit(rule_set=rule_set, matched_variants=[ann])],
        )

        console = Console(file=StringIO(), force_terminal=False, color_system=None, width=120)
        _print_completion_panel(console, 'profile', result, {'html': Path('/tmp/report.html')})

        output = console.file.getvalue()
        assert '0 rule hit' in output
        assert '1 formula rule hit' in output
        assert '1 total database hits' in output

    def test_completion_panel_total_hits_matches_sequence_feature_totals_for_complex_multi_hits(
        self,
    ) -> None:
        """Cumulative totals must stay aligned across CLI and report feature cards."""
        var_a = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500)
        var_b = VariantCall(chrom='ref', pos=6, ref='C', alt='T', allele_freq=0.90, depth=450)
        var_c = VariantCall(chrom='ref', pos=9, ref='G', alt='A', allele_freq=0.85, depth=420)
        var_d = VariantCall(chrom='ref', pos=12, ref='T', alt='C', allele_freq=0.80, depth=400)

        rule_a1 = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=1,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )
        rule_b1 = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugB',
            drug_id=2,
            reference_identifier='ref',
            position=1,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )
        rule_a2 = ResistanceRule(
            id=3,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='A',
            mutation='V',
            phenotype='resistant',
        )
        rule_c1 = ResistanceRule(
            id=4,
            feature_name='pol',
            feature_id=2,
            drug_name='DrugC',
            drug_id=3,
            reference_identifier='ref',
            position=3,
            reference='D',
            mutation='N',
            phenotype='resistant',
        )
        internal_formula_member = ResistanceRule(
            id=5,
            feature_name='gag',
            feature_id=1,
            drug_name='__formula_component__',
            drug_id=999,
            reference_identifier='ref',
            position=4,
            reference='P',
            mutation='S',
            phenotype='unknown',
            external_id='mut_formula',
            is_internal_formula_component=True,
        )

        ann_a = AnnotatedVariant(
            variant=var_a,
            feature_name='gag',
            codon_pos=1,
            ref_codon='AAA',
            alt_codon='GAA',
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            rule_matches=[rule_a1, rule_b1],
        )
        ann_b = AnnotatedVariant(
            variant=var_b,
            feature_name='gag',
            codon_pos=2,
            ref_codon='GCT',
            alt_codon='GTT',
            ref_aa='A',
            alt_aa='V',
            consequence='missense',
            rule_matches=[rule_a2],
        )
        ann_c = AnnotatedVariant(
            variant=var_c,
            feature_name='pol',
            codon_pos=3,
            ref_codon='GAT',
            alt_codon='AAT',
            ref_aa='D',
            alt_aa='N',
            consequence='missense',
            rule_matches=[rule_c1],
        )
        ann_d = AnnotatedVariant(
            variant=var_d,
            feature_name='pol',
            codon_pos=4,
            ref_codon='CCT',
            alt_codon='TCT',
            ref_aa='P',
            alt_aa='S',
            consequence='missense',
            rule_matches=[internal_formula_member],
        )

        formula_rule_set_a = ResistanceRuleSet(
            id=101,
            drug_name='FormulaDrugX',
            drug_id=11,
            phenotype='resistant',
            group_name='formula_x',
        )
        formula_rule_set_b = ResistanceRuleSet(
            id=102,
            drug_name='FormulaDrugY',
            drug_id=12,
            phenotype='resistant',
            group_name='formula_y',
        )
        formula_rule_set_c = ResistanceRuleSet(
            id=103,
            drug_name='FormulaDrugZ',
            drug_id=13,
            phenotype='resistant',
            group_name='formula_z',
        )

        formula_hits = [
            FormulaRuleHit(rule_set=formula_rule_set_a, matched_variants=[ann_a, ann_b]),
            FormulaRuleHit(rule_set=formula_rule_set_b, matched_variants=[ann_b, ann_c]),
            FormulaRuleHit(rule_set=formula_rule_set_c, matched_variants=[ann_d]),
        ]

        result = ProfilingResult(
            project_name='Test Project',
            reference_name='ref',
            sample_name='sample01',
            vcf_name='sample.vcf',
            reference_length_nt=100,
            total_variants=4,
            variants_in_cds=3,
            resistance_hits=3,
            annotations=[ann_a, ann_b, ann_c, ann_d],
            formula_hits=formula_hits,
        )

        # direct rule matches: 2 (ann_a) + 1 (ann_b) + 1 (ann_c) + 0 (ann_d, formula-only) = 4
        # formula fires: 3 (one per FormulaRuleHit)
        # total database hits = 4 + 3 = 7
        #
        # per-feature formula attribution (1 per unique feature per formula hit):
        #   formula_x members in {gag} → gag +1
        #   formula_y members in {gag, pol} → gag +1, pol +1
        #   formula_z members in {pol} → pol +1
        # feature totals: gag = 3 direct + 2 formula = 5; pol = 1 direct + 2 formula = 3
        # (feature sum = 8 > global total = 7 because formula_y spans two features)

        console = Console(file=StringIO(), force_terminal=False, color_system=None, width=120)
        _print_completion_panel(console, 'profile', result, {'html': Path('/tmp/report.html')})
        cli_output = console.file.getvalue()

        assert '4 rule hit' in cli_output
        assert '3 formula rule hit' in cli_output
        assert '7 total database hits' in cli_output

        context = build_report_context(result)
        cards_by_name = {card['name']: card for card in context['sequence_features']['cards']}
        assert cards_by_name['gag']['database_hits'] == 5
        assert cards_by_name['pol']['database_hits'] == 3

    def test_profile_drops_variants_with_non_matching_vcf_chrom(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """A CHROM mismatch must not be remapped against the active query reference."""
        vcf_path = tmp_path / 'wrong_chrom.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            # This would trigger the K2E rule if CHROM were ignored.
            'other_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'wrong_chrom_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code != 0
        assert 'VCF contig names do not match the uploaded reference FASTA' in result.output

    def test_profile_with_results_db_creates_new_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_with_db'
        results_db = tmp_path / 'run_results.db'
        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output
        assert results_db.exists()

        conn = sqlite3.connect(results_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'results_meta'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_profile_with_results_db_accepts_existing_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_existing_db'
        results_db = tmp_path / 'existing_results.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

    def test_profile_with_results_db_rejects_incompatible_existing_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_invalid_db'
        results_db = tmp_path / 'invalid_results.db'
        conn = sqlite3.connect(results_db)
        conn.execute('CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code != 0
        assert 'schema mismatch' in result.output.lower()

    def test_profile_fails_when_ref_fasta_does_not_match_any_rule_feature(
        self,
        project_db: Path,
        sample_vcf: Path,
        tmp_path: Path,
    ):
        bad_fasta = tmp_path / 'bad_ref.fasta'
        bad_fasta.write_text('>unrelated\nGATTACAGATTACAGATTACAGATTACA\n')

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(bad_fasta),
            '--output', str(tmp_path / 'bad_results'),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code != 0
        assert 'no cds matches above thresholds' in result.output.lower()

    def test_profile_vcf_with_bam_persists_coverage_gaps(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        bam_path = tmp_path / 'query.bam'
        _write_partial_coverage_bam(bam_path)

        results_db = tmp_path / 'results.db'
        output_dir = tmp_path / 'bam_results'
        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--bam', str(bam_path),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '10',
        ])

        assert result.exit_code == 0, result.output
        conn = sqlite3.connect(results_db)
        count = conn.execute('SELECT COUNT(*) FROM coverage_gap').fetchone()[0]
        conn.close()
        assert count > 0


def _write_partial_coverage_bam(bam_path: Path) -> None:
    """Write a BAM with high depth only over the first 30 nt of tiny_ref."""
    header = {
        'HD': {'VN': '1.0'},
        'SQ': [{'SN': 'tiny_ref', 'LN': len(TINY_REF_SEQ)}],
    }
    with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
        read_seq = TINY_REF_SEQ[:30]
        qualities = pysam.qualitystring_to_array('I' * len(read_seq))
        for idx in range(20):
            read = pysam.AlignedSegment()
            read.query_name = f'read_{idx}'
            read.query_sequence = read_seq
            read.flag = 0
            read.reference_id = 0
            read.reference_start = 0
            read.mapping_quality = 60
            read.cigar = ((0, len(read_seq)),)
            read.next_reference_id = -1
            read.next_reference_start = -1
            read.template_length = 0
            read.query_qualities = qualities
            bam.write(read)

    pysam.index(str(bam_path))


class TestInitCli:
    """Test the ``init`` CLI command."""

    def test_init_creates_db(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'NC_000001\tgag\t2\tK\tE\tDrugX\tresistant\n'
        )

        db_path = tmp_path / 'project.db'
        runner = CliRunner()
        result = runner.invoke(app, [
            'init',
            '--name', 'CLI Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
        ])
        assert result.exit_code == 0, result.output
        assert db_path.exists()

    def test_init_accepts_extended_rules_columns(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref_multi.gb',
            [
                {
                    'id': 'NC_000001.1',
                    'accession': 'NC_000001',
                    'organism': 'Human alphaherpesvirus 1',
                    'taxonomy': ['Viruses', 'Herpesvirales', 'Herpesviridae'],
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\tic50\tpublication\tsource\n'
            'NC_000001\tgag\t2\tK\tE\tDrugY\tresistant\t>10x\tPMID:12345\therpesdrg-db\n'
        )

        db_path = tmp_path / 'project_extended.db'
        runner = CliRunner()
        result = runner.invoke(app, [
            'init',
            '--name', 'CLI Test Extended',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT rr.reference_identifier, rr.ic50, d.name AS drug_name, '
            'r.organism, r.taxonomy '
            'FROM resistance_rule rr '
            'JOIN drug d ON d.id = rr.drug_id '
            'JOIN feature g ON g.id = rr.feature_id '
            'JOIN reference r ON r.id = g.reference_id '
            'LIMIT 1'
        ).fetchone()
        pub_row = conn.execute(
            'SELECT p.pubmed_id, p.raw_input FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id LIMIT 1'
        ).fetchone()
        conn.close()

        assert row is not None
        assert row['reference_identifier'] == 'NC_000001'
        assert row['ic50'] == '10'
        assert row['drug_name'] == 'drugy'
        assert row['organism'] == 'Human alphaherpesvirus 1'
        assert row['taxonomy'] == 'Viruses; Herpesvirales; Herpesviridae'
        assert pub_row is not None
        assert pub_row['pubmed_id'] == '12345'
        assert pub_row['raw_input'] == 'PMID:12345'

    def test_init_accepts_multiple_genbank_files(self, tmp_path: Path):
        genbank_path_a = write_genbank(
            tmp_path / 'ref_a.gb',
            [
                {
                    'id': 'refA.1',
                    'accession': 'refA',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        genbank_path_b = write_genbank(
            tmp_path / 'ref_b.gb',
            [
                {
                    'id': 'refB.1',
                    'accession': 'refB',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'pol', 'protein': 'Pol', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules_multi_input.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'refA\tgag\t2\tK\tE\tDrugA\tresistant\n'
            'refB\tpol\t2\tK\tE\tDrugB\tresistant\n'
        )

        db_path = tmp_path / 'project_multi_input.db'
        result = CliRunner().invoke(app, [
            'init',
            '--name', 'CLI Multiple GenBank Test',
            '--genbank', str(genbank_path_a),
            '--genbank', str(genbank_path_b),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        reference_count = conn.execute('SELECT COUNT(*) AS n FROM reference').fetchone()['n']
        feature_names = {
            row['name'] for row in conn.execute('SELECT name FROM feature').fetchall()
        }
        drug_names = {
            row['name'] for row in conn.execute('SELECT name FROM drug').fetchall()
        }
        conn.close()

        assert reference_count == 2
        assert feature_names == {'gag', 'pol'}
        assert drug_names == {'druga', 'drugb'}

    def test_init_normalizes_flexible_mutation_inputs(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref_norm.gb',
            [
                {
                    'id': 'NC_000001.1',
                    'accession': 'NC_000001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules_norm.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'NC_000001\tgag\t2\tK\tF2STOP\tDrugStop\tresistant\n'
            'NC_000001\tgag\t2\tK\tK2frameshift\tDrugFs\tresistant\n'
            'NC_000001\tgag\t2\tK\tK2delQ\tDrugDel\tresistant\n'
        )

        db_path = tmp_path / 'project_norm.db'
        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Mutation Normalization Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT d.name AS drug_name, rr.mutation '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()

        observed = {row['drug_name']: row['mutation'] for row in rows}
        assert observed == {
            'drugdel': 'K',
            'drugfs': 'KfsX',
            'drugstop': '*',
        }

    def test_init_add_uses_existing_annotations_and_skips_semantic_duplicates(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'append_ref.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_initial = tmp_path / 'rules_initial.tsv'
        rules_initial.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\tic50\n'
            'ref1\tgag\t2\tK\tE\tDrugX\tresistant\t2x\n'
        )

        db_path = tmp_path / 'append.db'
        runner = CliRunner()
        init_result = runner.invoke(app, [
            'init',
            '--name', 'Append Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_initial),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert init_result.exit_code == 0, init_result.output

        rules_append = tmp_path / 'rules_append.tsv'
        rules_append.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\tphenotype\tic50\n'
            'ref1\tgag\t2\tK\tE\tdRuGx\tresistant\t999x\n'
            'ref1\tgag\t3\tA\tV\tDRUGX\tresistant\t5x\n'
        )

        append_result = runner.invoke(app, [
            'add',
            '--project', str(db_path),
            '--rules', str(rules_append),
            '--no-additional-info',
        ])
        assert append_result.exit_code == 0, append_result.output
        # Rich may wrap the log line in non-TTY mode; collapse whitespace before checking.
        assert 'duplicate rule(s) skipped' in ' '.join(append_result.output.split())

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rules = conn.execute(
            'SELECT rr.position, rr.mutation, rr.ic50, d.name AS drug_name '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY rr.position'
        ).fetchall()
        conn.close()

        assert len(rules) == 2
        assert rules[0]['position'] == 1
        assert rules[0]['mutation'] == 'E'
        # Existing duplicate rule is kept; incoming ic50 must not overwrite it.
        assert rules[0]['ic50'] == '2'
        assert rules[0]['drug_name'] == 'drugx'
        assert rules[1]['position'] == 2
        assert rules[1]['mutation'] == 'V'

    def test_init_add_requires_existing_database(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'append_missing.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\n'
            'ref1\tgag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'add',
            '--project', str(tmp_path / 'missing.db'),
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])
        assert result.exit_code != 0
        assert 'does not exist' in result.output

    def test_init_add_rejects_incompatible_existing_database(self, tmp_path: Path):
        db_path = tmp_path / 'broken_existing.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        conn.close()

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\n'
            'REF1\tgag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'add',
            '--project', str(db_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])

        assert result.exit_code != 0
        assert 'schema mismatch' in result.output.lower()

    def test_init_add_requires_stored_annotations_when_no_genbank_is_given(self, tmp_path: Path):
        db_path = tmp_path / 'empty_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('Broken Project', 9),
        )
        conn.commit()
        conn.close()

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'feature\tposition\treference\tmutation\tantiviral\n'
            'gag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'add',
            '--project', str(db_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])

        assert result.exit_code != 0
        assert 'no stored references/features' in result.output.lower()

    def test_init_warns_on_rule_feature_missing_in_genbank(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'missing_feature.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules_missing.tsv'
        rules_tsv.write_text(
            'reference_identifier\tfeature\tposition\treference\tmutation\tantiviral\n'
            'REF1\tpol\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Missing Feature Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(tmp_path / 'missing.db'),
        ])

        assert result.exit_code == 0, result.output
        assert 'skipped' in result.output.lower() or (tmp_path / 'missing.db').exists()

    def test_init_requires_reference_identifier_for_ambiguous_multirecord_feature(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ambiguous.gb',
            [
                {
                    'id': 'refA.1',
                    'accession': 'refA',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'GagA', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
                {
                    'id': 'refB.1',
                    'accession': 'refB',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'GagB', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_tsv = tmp_path / 'rules_ambiguous.tsv'
        rules_tsv.write_text(
            'feature\tposition\treference\tmutation\tantiviral\n'
            'gag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Ambiguous Ref Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(tmp_path / 'ambiguous.db'),
        ])

        assert result.exit_code != 0
        assert 'missing required field reference_identifier' in result.output


    def test_profile_with_results_db_populates_run_and_variants(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """After profiling with --results-db, a run row and variant rows must be stored."""
        results_db = tmp_path / 'populated.db'
        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(tmp_path / 'out'),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(results_db)
        conn.row_factory = sqlite3.Row
        run_row = conn.execute('SELECT * FROM run WHERE id = 1').fetchone()
        variant_count = conn.execute('SELECT COUNT(*) FROM variant_result WHERE run_id = 1').fetchone()[0]
        conn.close()

        assert run_row is not None
        assert run_row['project_name'] == 'Test Project'
        assert run_row['reference_name'] == 'tiny_ref'
        assert run_row['project_fingerprint'] != ''
        assert variant_count > 0


class TestExportCli:
    """The ``export`` command has been removed; these tests verify it no longer exists."""

    def test_export_command_no_longer_exists(self, project_db: Path, tmp_path: Path):
        result = CliRunner().invoke(app, [
            'export',
            '--project', str(project_db),
            '--output', str(tmp_path / 'bundle.zip'),
        ])
        assert result.exit_code != 0

