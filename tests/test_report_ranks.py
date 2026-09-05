"""Tests for rank-coloured verbatim phenotype labels in report rendering (T5)."""

from __future__ import annotations

from respro.db.models import (
    AnnotatedVariant,
    ProfilingResult,
    Publication,
    ResistanceRule,
    VariantCall,
)
from respro.report.html import _phenotype_badge_class, _build_rule_metrics, render_html
from respro.report.palette import PHENOTYPE_COLOURS
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
    """PHENOTYPE_COLOURS is keyed by rank."""

    def test_rank_colours_present(self):
        assert PHENOTYPE_COLOURS[1] == '#27ae60'
        assert PHENOTYPE_COLOURS[5] == '#e74c3c'
        assert PHENOTYPE_COLOURS[-1] == '#334142'
        assert PHENOTYPE_COLOURS[0] == '#bdc3c7'


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
