"""Regression tests for database-hit rendering in reports."""

from __future__ import annotations

import pytest

from respro.db.models import (
    AnnotatedVariant,
    FeatureRecord,
    FormulaRuleHit,
    ProfilingResult,
    ResistanceRule,
    ResistanceRuleSet,
    VariantCall,
)
from respro.report.html import _load_css_text
from respro.report.plots import render_lollipop_plot_bytes


def _make_formula_only_result() -> tuple[ProfilingResult, list[FeatureRecord]]:
    feature = FeatureRecord(
        id=1,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=12,
        strand='+',
        codon_start=0,
        nt_sequence='ATGAAAGCTTAA',
    )
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
    return result, [feature]


def test_render_lollipop_plot_labels_formula_only_member_hits() -> None:
    result, features = _make_formula_only_result()

    svg_bytes = render_lollipop_plot_bytes(result, features, fmt='svg')

    assert svg_bytes is not None
    assert result.database_hit_count == 1
    assert not result.annotations[0].is_resistance_hit
    assert 'K2E' in svg_bytes.decode('utf-8')


@pytest.mark.skip(reason='Report rework in progress')
def test_report_css_expands_plot_modal_panel() -> None:
    css_text = _load_css_text()

    assert '.plot-modal-panel' in css_text
    assert 'width: min(1120px, 80vw);' in css_text