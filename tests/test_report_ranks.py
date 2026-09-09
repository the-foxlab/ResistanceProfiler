"""Tests for rank-coloured verbatim phenotype labels in report rendering (T5)."""

from __future__ import annotations

import json
import sqlite3

from respro.db.models import (
    AnnotatedVariant,
    FeatureMatch,
    FeatureRecord,
    ProfilingResult,
    ReferenceGroup,
    ResistanceRule,
    VariantCall,
)
from respro.db.phenotype_ranks import RANK_COLOURS
from respro.report.html import (
    _build_rule_metrics,
    _phenotype_badge_class,
    build_report_context,
    render_html,
)
from tests.conftest import make_profiling_result


class TestPhenotypeBadgeClassByRank:
    """_phenotype_badge_class infers the CSS class from the label's rank."""

    def test_rank_5_label(self):
        assert _phenotype_badge_class('resistant') == 'phenotype--resistant'
        assert _phenotype_badge_class('HRI') == 'phenotype--resistant'
        assert _phenotype_badge_class('high-level resistance') == 'phenotype--resistant'

    def test_rank_1_label(self):
        assert _phenotype_badge_class('susceptible') == 'phenotype--susceptible'
        assert _phenotype_badge_class('sensitive') == 'phenotype--susceptible'

    def test_rank_3_label(self):
        assert _phenotype_badge_class('low-level resistance') == 'phenotype--low-level'

    def test_rank_4_label(self):
        assert _phenotype_badge_class('intermediate') == 'phenotype--intermediate'

    def test_contradictory_label(self):
        assert _phenotype_badge_class('contradictory') == 'phenotype--contradictory'

    def test_empty_returns_empty(self):
        assert _phenotype_badge_class('') == ''
        assert _phenotype_badge_class('unknown') == 'phenotype--unknown'


class TestPaletteRankColours:
    """The canonical rank→colour mapping lives in respro.db.phenotype_ranks."""

    def test_rank_colours_present(self):
        assert RANK_COLOURS[1] == '#27ae60'
        assert RANK_COLOURS[5] == '#e74c3c'
        assert RANK_COLOURS[-1] == '#64748b'
        assert RANK_COLOURS[0] == '#bdc3c7'


class TestBuildRuleMetricsVerbatimLabel:
    """_build_rule_metrics displays the verbatim label with a rank-inferred badge."""

    def test_hri_label_shown_with_resistant_badge(self):
        metrics = _build_rule_metrics('hri', '', '', '', '')
        pheno = next(m for m in metrics if m['label'] == 'Phenotype')
        assert pheno['value'] == 'hri'
        assert pheno['badge_class'] == 'phenotype--resistant'

    def test_low_level_label_shown_with_low_level_badge(self):
        metrics = _build_rule_metrics('low-level resistance', '', '', '', '')
        pheno = next(m for m in metrics if m['label'] == 'Phenotype')
        assert pheno['value'] == 'low-level resistance'
        assert pheno['badge_class'] == 'phenotype--low-level'

    def test_empty_phenotype_no_metric(self):
        metrics = _build_rule_metrics('', '', '', '', '')
        assert not any(m['label'] == 'Phenotype' for m in metrics)

    def test_unknown_phenotype_no_metric(self):
        metrics = _build_rule_metrics('unknown', '', '', '', '')
        assert not any(m['label'] == 'Phenotype' for m in metrics)


class TestRenderHtmlRankColouredLabel:
    """render_html shows the verbatim label coloured by inferred rank."""

    def test_hri_label_rendered_with_resistant_badge(self):
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='hri',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule],
        )
        r = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1, annotations=[ann],
        )
        html = render_html(r, similarity_high=1, similarity_moderate=0)
        assert 'hri' in html
        assert 'phenotype--resistant' in html


class TestMultiReferenceStrongestWinsAssessment:
    """AUD-003: when a drug has hits under multiple references with differing
    per-(reference, drug) overrides, the final assessment is the strongest-wins
    result across all references — not the alphabetically-first reference's."""

    def test_strongest_override_wins_across_references(self) -> None:
        """DrugA has hits under refA and refB. refA's override makes it
        'intermediate' (rank 4); refB's override makes it 'resistant' (rank 5).
        The final assessment must be 'resistant' (strongest wins), NOT
        'intermediate' (alphabetically-first refA)."""
        rule_a = ResistanceRule(
            id=1, feature_name='gagA', feature_id=1, drug_name='DrugA', drug_id=1,
            reference_identifier='refA', position=2, reference='K', mutation='E',
            phenotype='resistant', ic50='6.0',
        )
        rule_b = ResistanceRule(
            id=2, feature_name='polB', feature_id=2, drug_name='DrugA', drug_id=1,
            reference_identifier='refB', position=2, reference='K', mutation='E',
            phenotype='resistant', ic50='6.0',
        )
        ann_a = AnnotatedVariant(
            variant=VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G',
                                allele_freq=0.95, depth=500),
            feature_name='gagA', codon_pos=2, ref_codon='AAA', alt_codon='GAA',
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule_a],
        )
        ann_b = AnnotatedVariant(
            variant=VariantCall(chrom='chrom_b', pos=3, ref='A', alt='G',
                                allele_freq=0.95, depth=500),
            feature_name='polB', codon_pos=2, ref_codon='AAA', alt_codon='GAA',
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule_b],
        )
        feat_a = FeatureRecord(
            id=1, reference_id=1, name='gagA', protein='GagA', start=0, end=12,
            strand='+', codon_start=0, nt_sequence='ATGAAAGCTTAA',
        )
        feat_b = FeatureRecord(
            id=2, reference_id=2, name='polB', protein='PolB', start=0, end=12,
            strand='+', codon_start=0, nt_sequence='ATGAAAGCTTAA',
        )
        references = [
            ReferenceGroup(
                reference_name='refA', reference_id=1, organism='Organism A',
                reference_length_nt=1000, query_name='chrom_a',
                query_sequence='ATGAAAGCTTAA', features=[feat_a],
                rule_feature_names={'gagA'},
                feature_matches=[
                    FeatureMatch(
                        feature=feat_a, identity=1.0, cds_coverage=1.0,
                        query_coverage=1.0, query_start=0, query_end=12, strand='+',
                        cigar='12M', cds_start=0,
                    ),
                ],
            ),
            ReferenceGroup(
                reference_name='refB', reference_id=2, organism='Organism B',
                reference_length_nt=1000, query_name='chrom_b',
                query_sequence='ATGAAAGCTTAA', features=[feat_b],
                rule_feature_names={'polB'},
                feature_matches=[
                    FeatureMatch(
                        feature=feat_b, identity=1.0, cds_coverage=1.0,
                        query_coverage=1.0, query_start=0, query_end=12, strand='+',
                        cigar='12M', cds_start=0,
                    ),
                ],
            ),
        ]
        result = ProfilingResult(
            project_name='Multi', organism='Organism A',
            sample_name='samp', vcf_name='in.vcf',
            total_variants=2, variants_in_cds=2, resistance_hits=2,
            annotations=[ann_a, ann_b],
            references=references,
        )
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute(
            'CREATE TABLE interpretation_algorithm '
            '(id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)'
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {
                        'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0,
                    },
                    'drug_thresholds': [
                        # refA (alphabetically first): IC50 6.0 ≥ 2.0 and < 10.0
                        # → intermediate (rank 4)
                        {'reference': 'refA', 'drug': 'DrugA',
                         'thresholds': {'susceptible': 0.0, 'intermediate': 2.0,
                                        'resistant': 10.0}},
                        # refB: IC50 6.0 ≥ 5.0 → resistant (rank 5)
                        {'reference': 'refB', 'drug': 'DrugA',
                         'thresholds': {'susceptible': 0.0, 'intermediate': 2.0,
                                        'resistant': 5.0}},
                    ],
                }),
            ),
        )
        conn.commit()
        ctx = build_report_context(
            result, similarity_high=1, similarity_moderate=0, project_conn=conn,
            features=[f for rg in result.references for f in rg.features],
        )
        drug_table = ctx['summary']['drug_table']
        drug_row = next(r for r in drug_table['rows'] if r['name'] == 'DrugA')
        # Strongest-wins: refA's override yields 'resistant' (rank 5), refB's
        # yields 'intermediate' (rank 4). The final assessment must be
        # 'resistant', not 'intermediate' (the alphabetically-first refA result
        # is also resistant here, so this additionally pins refA's resolution).
        assert drug_row['assessment'] == 'resistant'
