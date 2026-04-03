"""
Tests for report output generation.
"""

import json
from pathlib import Path

from respro.db.models import AnnotatedVariant, ResistanceRule, VariantCall
from respro.report.results_model import ProfilingResult
from respro.report.export import to_tsv_string, write_tsv


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
        ic50='>10x', publication='PMID:12345',
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
        reference_name='ref', sample_name='S1',
        vcf_path='test.vcf',
        total_variants=1, variants_in_cds=1, resistance_hits=1,
        annotations=[ann],
    )


class TestProfilingResult:
    def test_summary_dict(self):
        r = _make_result()
        d = r.summary_dict()
        assert d['project_name'] == 'Test'
        assert d['resistance_hits'] == 1

    def test_to_json(self):
        r = _make_result()
        j = r.to_json()
        data = json.loads(j)
        assert len(data['variants']) == 1
        assert data['variants'][0]['alt_aa'] == 'E'

    def test_cds_annotations(self):
        r = _make_result()
        assert len(r.cds_annotations) == 1

    def test_hit_annotations(self):
        r = _make_result()
        assert len(r.hit_annotations) == 1

    def test_drug_hits_json(self):
        r = _make_result()
        hits = r.annotations[0].drug_hits_json()
        assert len(hits) == 1
        assert hits[0]['drug'] == 'DrugA'
        assert hits[0]['reference_identifier'] == 'tiny_ref'
        assert hits[0]['ic50'] == '>10x'


class TestTsvExport:
    def test_tsv_string(self):
        r = _make_result()
        tsv = to_tsv_string(r)
        lines = tsv.strip().split('\n')
        assert len(lines) == 2  # header + 1 row
        assert 'gag' in lines[1]

    def test_write_tsv_file(self, tmp_path: Path):
        r = _make_result()
        out = write_tsv(r, tmp_path / 'test.tsv')
        assert out.exists()
        content = out.read_text()
        assert 'missense' in content


class TestHtmlExport:
    def test_render_html(self):
        from respro.report.html import render_html
        r = _make_result()
        html = render_html(r)
        assert 'ResistanceProfiler' in html
        assert 'K2E' in html or 'gag' in html

