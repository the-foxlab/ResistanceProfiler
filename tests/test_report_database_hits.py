"""Regression tests for database-hit rendering in reports."""

from __future__ import annotations

import pytest

from respro.db.models import (
    AnnotatedVariant,
    FeatureRecord,
    FormulaRuleHit,
    ProfilingResult,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    VariantCall,
)
from respro.report.html import _build_database_hits_rows, _load_css_text
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


def test_report_css_expands_plot_modal_panel() -> None:
    css_text = _load_css_text()

    assert '.plot-modal-panel' in css_text
    assert 'width: 80vw;' in css_text


# ─── _build_database_hits_rows ────────────────────────────────────────────────

def _make_variant(pos: int = 3, af: float = 0.9) -> VariantCall:
    return VariantCall(chrom='ref', pos=pos, ref='A', alt='G', allele_freq=af, depth=200)


def _make_annotation(
    variant: VariantCall,
    feature: str = 'UL23',
    codon_pos: int = 1,
    ref_aa: str = 'K',
    alt_aa: str = 'R',
    af_bin: str = 'high',
    rules: list[ResistanceRule] | None = None,
) -> AnnotatedVariant:
    return AnnotatedVariant(
        variant=variant,
        feature_name=feature,
        codon_pos=codon_pos,
        ref_codon='AAA',
        alt_codon='AGA',
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence='missense',
        af_bin=af_bin,
        rule_matches=rules or [],
    )


def _make_single_rule(drug: str = 'Aciclovir', source: str = 'HerpesDrugDB') -> ResistanceRule:
    return ResistanceRule(
        id=1,
        feature_name='UL23',
        feature_id=1,
        drug_name=drug,
        drug_id=1,
        reference_identifier='HSV1',
        position=1,
        reference='K',
        mutation='R',
        phenotype='resistant',
        source=source,
    )


class TestBuildDatabaseHitsRows:
    def test_single_rule_produces_one_row(self) -> None:
        rule = _make_single_rule()
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        ctx = _build_database_hits_rows(result)

        assert ctx['count'] == 1
        assert len(ctx['rows']) == 1

    def test_single_rule_row_fields(self) -> None:
        rule = _make_single_rule(drug='Aciclovir', source='HerpesDrugDB')
        ann = _make_annotation(_make_variant(), af_bin='high', rules=[rule])
        result = ProfilingResult(annotations=[ann])

        row = _build_database_hits_rows(result)['rows'][0]

        assert row['drug'] == 'Aciclovir'
        assert row['mutation_groups'] == [{'feature': 'UL23', 'muts': ['K2R']}]
        assert row['af_bin'] == 'high'
        assert row['source'] == 'HerpesDrugDB'

    def test_single_rule_phenotype_in_metrics(self) -> None:
        rule = _make_single_rule()
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        row = _build_database_hits_rows(result)['rows'][0]

        assert any(m['label'] == 'Phenotype' and m['value'] == 'resistant' for m in row['metrics'])

    def test_unknown_phenotype_excluded_from_metrics(self) -> None:
        rule = ResistanceRule(
            id=2, feature_name='UL23', feature_id=1, drug_name='DrugX', drug_id=1,
            reference_identifier='HSV1', position=1, reference='K', mutation='R',
            phenotype='unknown', clinical_phenotype='unknown',
        )
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        row = _build_database_hits_rows(result)['rows'][0]

        assert row['metrics'] == []

    def test_ic50_fold_ic50_score_included_when_present(self) -> None:
        rule = ResistanceRule(
            id=3, feature_name='UL23', feature_id=1, drug_name='DrugX', drug_id=1,
            reference_identifier='HSV1', position=1, reference='K', mutation='R',
            phenotype='unknown', ic50='0.5 µM', fold_ic50='3.2', score='8',
        )
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        row = _build_database_hits_rows(result)['rows'][0]
        metric_labels = [m['label'] for m in row['metrics']]

        assert 'IC50' in metric_labels
        assert 'Fold IC50' in metric_labels
        assert 'Score' in metric_labels

    def test_formula_rule_af_bin_is_always_high(self) -> None:
        internal_rule = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='__formula_component__',
            drug_id=1, reference_identifier='HSV1', position=1,
            reference='K', mutation='R', phenotype='unknown',
            external_id='mut_a', is_internal_formula_component=True,
        )
        ann = _make_annotation(_make_variant(), af_bin='intermediate', rules=[internal_rule])
        rule_set = ResistanceRuleSet(
            id=1, drug_name='Aciclovir', drug_id=1, phenotype='resistant', group_name='combo_1',
        )
        result = ProfilingResult(
            annotations=[ann],
            formula_hits=[FormulaRuleHit(rule_set=rule_set, matched_variants=[ann])],
        )

        row = _build_database_hits_rows(result)['rows'][0]

        assert row['af_bin'] == 'high'

    def test_formula_rule_mutations_joined_as_list(self) -> None:
        internal_a = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='__formula_component__',
            drug_id=1, reference_identifier='HSV1', position=1,
            reference='K', mutation='R', phenotype='unknown',
            external_id='mut_a', is_internal_formula_component=True,
        )
        internal_b = ResistanceRule(
            id=2, feature_name='UL23', feature_id=1, drug_name='__formula_component__',
            drug_id=1, reference_identifier='HSV1', position=5,
            reference='T', mutation='A', phenotype='unknown',
            external_id='mut_b', is_internal_formula_component=True,
        )
        ann_a = _make_annotation(_make_variant(pos=3), rules=[internal_a])
        ann_b = _make_annotation(
            _make_variant(pos=15), codon_pos=5, ref_aa='T', alt_aa='A', rules=[internal_b],
        )
        rule_set = ResistanceRuleSet(
            id=1, drug_name='Aciclovir', drug_id=1, phenotype='resistant', group_name='combo_1',
        )
        result = ProfilingResult(
            annotations=[ann_a, ann_b],
            formula_hits=[FormulaRuleHit(rule_set=rule_set, matched_variants=[ann_a, ann_b])],
        )

        row = _build_database_hits_rows(result)['rows'][0]

        assert row['mutation_groups'] == [{'feature': 'UL23', 'muts': ['K2R', 'T6A']}]

    def test_formula_component_rules_excluded_from_single_rows(self) -> None:
        internal_rule = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='__formula_component__',
            drug_id=1, reference_identifier='HSV1', position=1,
            reference='K', mutation='R', phenotype='unknown',
            external_id='mut_a', is_internal_formula_component=True,
        )
        ann = _make_annotation(_make_variant(), rules=[internal_rule])
        rule_set = ResistanceRuleSet(
            id=1, drug_name='Aciclovir', drug_id=1, phenotype='resistant', group_name='combo_1',
        )
        result = ProfilingResult(
            annotations=[ann],
            formula_hits=[FormulaRuleHit(rule_set=rule_set, matched_variants=[ann])],
        )

        ctx = _build_database_hits_rows(result)

        # exactly one row from the formula hit; the internal component rule must not produce a row
        assert ctx['count'] == 1
        assert ctx['rows'][0]['drug'] == 'Aciclovir'

    def test_rows_sorted_by_drug_name(self) -> None:
        rule_z = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='Zovirax', drug_id=1,
            reference_identifier='HSV1', position=1, reference='K', mutation='R',
            phenotype='resistant',
        )
        rule_a = ResistanceRule(
            id=2, feature_name='UL23', feature_id=1, drug_name='Aciclovir', drug_id=2,
            reference_identifier='HSV1', position=2, reference='T', mutation='A',
            phenotype='resistant',
        )
        ann_z = _make_annotation(_make_variant(pos=3), rules=[rule_z])
        ann_a = _make_annotation(_make_variant(pos=6), codon_pos=2, ref_aa='T', alt_aa='A', rules=[rule_a])
        result = ProfilingResult(annotations=[ann_z, ann_a])

        rows = _build_database_hits_rows(result)['rows']

        assert rows[0]['drug'] == 'Aciclovir'
        assert rows[1]['drug'] == 'Zovirax'

    def test_has_publications_false_when_no_pubs(self) -> None:
        rule = _make_single_rule()
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        ctx = _build_database_hits_rows(result)

        assert not ctx['has_publications']

    def test_has_publications_true_when_pub_present(self) -> None:
        pub = Publication(id=1, doi='10.1/test', title='Test paper', pubmed_id='', raw_input='')
        rule = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='Aciclovir', drug_id=1,
            reference_identifier='HSV1', position=1, reference='K', mutation='R',
            phenotype='resistant', publications=[pub],
        )
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        ctx = _build_database_hits_rows(result)

        assert ctx['has_publications']
        row = ctx['rows'][0]
        assert row['pub_citations'][0]['num'] == 1
        assert row['pub_citations'][0]['url'] == 'https://doi.org/10.1/test'
        bib = ctx['bibliography'][0]
        assert bib['label'] == 'Test paper'
        assert bib['url'] == 'https://doi.org/10.1/test'

    def test_publication_deduplication(self) -> None:
        pub = Publication(id=1, doi='10.1/test', title='Paper', pubmed_id='', raw_input='')
        rule = ResistanceRule(
            id=1, feature_name='UL23', feature_id=1, drug_name='Aciclovir', drug_id=1,
            reference_identifier='HSV1', position=1, reference='K', mutation='R',
            phenotype='resistant',
            publications=[pub, pub],  # same pub twice
        )
        ann = _make_annotation(_make_variant(), rules=[rule])
        result = ProfilingResult(annotations=[ann])

        ctx = _build_database_hits_rows(result)

        assert len(ctx['rows'][0]['pub_citations']) == 1
        assert len(ctx['bibliography']) == 1

    def test_display_names_applied_to_mutation_label(self) -> None:
        rule = _make_single_rule()
        ann = _make_annotation(_make_variant(), feature='UL23', rules=[rule])
        result = ProfilingResult(annotations=[ann])

        row = _build_database_hits_rows(result, display_names={'UL23': 'Thymidine kinase'})['rows'][0]

        assert row['mutation_groups'] == [{'feature': 'Thymidine kinase', 'muts': ['K2R']}]

    def test_empty_result_returns_empty_context(self) -> None:
        result = ProfilingResult()

        ctx = _build_database_hits_rows(result)

        assert ctx['rows'] == []
        assert ctx['count'] == 0
        assert not ctx['has_publications']
