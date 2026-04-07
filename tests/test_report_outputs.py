"""
Tests for report output generation.
"""

import json
import sqlite3
from pathlib import Path

from respro.db.models import AnnotatedVariant, Publication, ResistanceRule, VariantCall
from respro.report.results_model import ProfilingResult


def _make_result() -> ProfilingResult:
    """
    Create a minimal ProfilingResult for testing.

    :return: ProfilingResult with sample variant and annotation
    """
    var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500)
    rule = ResistanceRule(
        id=1, gene_name='gag', gene_id=1,
        drug_name='DrugA', drug_id=1,
        reference_identifier='tiny_ref',
        position=2, reference='K', mutation='E',
        phenotype='resistant',
        ic50='>10x',
        publications=[Publication(id=1, doi='', title='', pubmed_id='12345', raw_input='PMID:12345')],
    )
    ann = AnnotatedVariant(
        variant=var,
        gene_name='gag', codon_pos=2,
        ref_codon='AAA', alt_codon='GAA',
        ref_aa='K', alt_aa='E',
        consequence='missense', af_bin='high',
        rule_matches=[rule],
    )
    return ProfilingResult(
        project_name='Test', organism='test',
        reference_name='ref', reference_length_nt=12000, sample_name='S1',
        vcf_name='test.vcf',
        total_variants=1, variants_in_cds=1, resistance_hits=1,
        annotations=[ann],
    )


def _make_combined_result() -> ProfilingResult:
    """Create a ProfilingResult containing one combined codon event."""
    r = _make_result()
    r.annotations[0].is_combined_codon_event = True
    r.annotations[0].combined_member_count = 2
    return r


class TestProfilingResult:
    def test_summary_dict(self):
        r = _make_result()
        d = r.summary_dict()
        assert d['project_name'] == 'Test'
        assert d['resistance_hits'] == 1
        assert d['reference_length_nt'] == 12000

    def test_cds_annotations(self):
        r = _make_result()
        assert len(r.cds_annotations) == 1

    def test_drug_hits_json(self):
        r = _make_result()
        hits = r.annotations[0].drug_hits_json()
        assert len(hits) == 1
        assert hits[0]['drug'] == 'DrugA'
        assert hits[0]['reference_identifier'] == 'tiny_ref'
        assert hits[0]['ic50'] == '>10x'
        assert hits[0]['fold_ic50'] == ''
        assert hits[0]['publications'] == [
            {'doi': '', 'title': '', 'pubmed_id': '12345', 'raw_input': 'PMID:12345'}
        ]



class TestHtmlExport:
    def test_render_html(self):
        from respro.report.html import render_html
        r = _make_result()
        html = render_html(r)
        assert '<title>ResistanceProfiler - test.vcf</title>' in html
        assert 'ResistanceProfiler' in html
        assert 'K2E' in html or 'gag' in html

    def test_render_html_embeds_favicon(self):
        from respro.report.html import render_html

        r = _make_result()
        html = render_html(r)

        assert "rel='icon'" in html
        assert 'data:image/svg+xml;base64,' in html

    def test_potential_effects_excludes_snp_rule_for_indel_annotation(self):
        from respro.report.html import _build_potential_effects_rows

        var = VariantCall(chrom='ref', pos=10, ref='A', alt='AGGG', allele_freq=0.8, depth=120)
        ann = AnnotatedVariant(
            variant=var,
            gene_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='KG',
            consequence='insertion',
        )
        result = ProfilingResult(
            project_name='Test',
            organism='test',
            reference_name='ref',
            reference_length_nt=12000,
            sample_name='S1',
            vcf_name='test.vcf',
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
        )

        snp_rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )

        rows = _build_potential_effects_rows(result, [snp_rule])
        assert rows == []

    def test_potential_effects_keeps_indel_rule_for_indel_annotation(self):
        from respro.report.html import _build_potential_effects_rows

        var = VariantCall(chrom='ref', pos=10, ref='A', alt='AGGG', allele_freq=0.8, depth=120)
        ann = AnnotatedVariant(
            variant=var,
            gene_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='KG',
            consequence='insertion',
        )
        result = ProfilingResult(
            project_name='Test',
            organism='test',
            reference_name='ref',
            reference_length_nt=12000,
            sample_name='S1',
            vcf_name='test.vcf',
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
        )

        indel_rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='K3KG',
            phenotype='resistant',
        )

        rows = _build_potential_effects_rows(result, [indel_rule])
        assert len(rows) == 1
        assert rows[0]['drug'] == 'DrugA'
        assert rows[0]['similarity'] == 'moderate'

    def test_render_html_gene_overview(self):
        from respro.report.html import _load_gene_cards

        r = _make_result()
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE gene ('
            'name TEXT, protein TEXT, protein_id TEXT, ncbi_protein_url TEXT, '
            'locus_tag TEXT, note TEXT, aa_sequence TEXT, start INTEGER, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO gene (name, protein, protein_id, ncbi_protein_url, locus_tag, note, aa_sequence, start, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                'gag',
                'Capsid protein',
                'YP_009137097.1',
                'https://www.ncbi.nlm.nih.gov/protein/YP_009137097.1/',
                'UL23',
                'Thymidine kinase',
                'MKAFGP',
                100,
                1,
            ),
        )
        conn.commit()

        cards = _load_gene_cards(conn, r.reference_name, {'gag'})
        assert len(cards) == 1
        assert cards[0]['protein_id'] == 'YP_009137097.1'
        assert cards[0]['ncbi_protein_url'] == 'https://www.ncbi.nlm.nih.gov/protein/YP_009137097.1/'
        assert cards[0]['aa_sequence'] == 'MKAFGP'

