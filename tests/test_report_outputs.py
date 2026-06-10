"""
Tests for report output generation.
"""

import json
import sqlite3

import matplotlib.pyplot as plt
from Bio.Seq import Seq

from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FeatureMatch,
    FeatureRecord,
    FeatureSegment,
    ProfilingResult,
    Publication,
    ResistanceRule,
    VariantCall,
)
from respro.db.report_queries import load_feature_cards
from respro.report.alignment_visualization import (
    _affected_nt_positions,
    _apply_vcf_overlay,
    build_alignment_html,
    build_feature_alignments,
)
from respro.report.html import (
    _build_potential_effects_rows,
    build_report_context,
    render_html,
)
from respro.report.non_html_exports import _build_pdf_drug_rows, export_results


def _make_combined_result() -> ProfilingResult:
    """Create a ProfilingResult containing one combined codon event."""
    r = _make_result()
    r.annotations[0].is_combined_codon_event = True
    r.annotations[0].combined_member_count = 2
    return r


def _make_result() -> ProfilingResult:
    """
    Create a minimal ProfilingResult for testing.

    :return: ProfilingResult with sample variant and annotation
    """
    var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500)
    rule = ResistanceRule(
        id=1, feature_name='gag', feature_id=1,
        drug_name='DrugA', drug_id=1,
        reference_identifier='tiny_ref',
        position=2, reference='K', mutation='E',
        phenotype='resistant',
        ic50='>10x',
        publications=[Publication(id=1, doi='', title='', pubmed_id='12345', raw_input='PMID:12345')],
    )
    ann = AnnotatedVariant(
        variant=var,
        feature_name='gag', codon_pos=2,
        ref_codon='AAA', alt_codon='GAA',
        ref_aa='K', alt_aa='E',
        consequence='missense', af_bin='high',
        rule_matches=[rule],
    )
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
    return ProfilingResult(
        project_name='Test', organism='test',
        reference_name='ref', reference_length_nt=12000, sample_name='S1',
        vcf_name='test.vcf',
        total_variants=1, variants_in_cds=1, resistance_hits=1,
        annotations=[ann],
        query_sequence='ATGAAAGCTTAA',
        feature_matches=[
            FeatureMatch(
                feature=feature,
                identity=1.0,
                cds_coverage=1.0,
                query_coverage=1.0,
                query_start=0,
                query_end=12,
                strand='+',
                cigar='12M',
                cds_start=0,
            )
        ],
    )


class TestBuildReportContext:
    def test_db_hit_positions_and_rules_in_summary(self) -> None:
        # _make_result has 1 annotation with 1 rule → 1 position, 1 rule
        r = _make_result()
        ctx = build_report_context(r)
        assert ctx['database_hits']['count'] == 1
        assert ctx['summary']['db_hits_summary']['single_rule_hits'] == 1

    def test_multiple_rules_per_position(self) -> None:
        # Two rules on the same variant → 2 database hit rows (one per rule)
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule_a = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E', phenotype='resistant',
        )
        rule_b = ResistanceRule(
            id=2, feature_name='gag', feature_id=1,
            drug_name='DrugB', drug_id=2, reference_identifier='ref',
            position=2, reference='K', mutation='E', phenotype='sensitive',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=2,
            ref_codon='AAA', alt_codon='GAA',
            ref_aa='K', alt_aa='E',
            consequence='missense', af_bin='high',
            rule_matches=[rule_a, rule_b],
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1,
            annotations=[ann],
        )
        ctx = build_report_context(r)
        assert ctx['database_hits']['count'] == 2    # 2 database hit rows (one per drug/rule)
        assert ctx['summary']['db_hits_summary']['single_rule_hits'] == 2

    def test_publication_citations_deduplicate_without_publication_ids(self) -> None:
        r = _make_result()
        r.annotations[0].rule_matches[0].publications = [
            Publication(id=0, doi='', title='', pubmed_id='11111', raw_input='PMID:11111'),
            Publication(id=0, doi='', title='', pubmed_id='11111', raw_input='PMID:11111'),
            Publication(id=0, doi='', title='', pubmed_id='22222', raw_input='PMID:22222'),
        ]

        ctx = build_report_context(r)

        assert len(ctx['database_hits']['bibliography']) == 2
        # pub_citations is a list of citation objects with 'num' key
        assert len(ctx['database_hits']['rows'][0]['pub_citations']) == 2
        assert ctx['database_hits']['rows'][0]['pub_citations'][0]['num'] == 1
        assert ctx['database_hits']['rows'][0]['pub_citations'][1]['num'] == 2

    def test_stat_note_rendered_in_html(self) -> None:
        r = _make_result()
        html = render_html(r)
        # Check that summary stats are rendered
        assert 'Sequence Assessment' in html
        assert 'Total mutations' in html
        assert 'Single rule hits' in html
        assert 'Matching entries' in html

    def test_db_hits_phenotype_prefers_phenotype_over_clinical(self) -> None:
        # Rule with phenotype='resistant', clinical_phenotype='intermediate'
        # → row should show both metrics but drug summary counts it correctly.
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='resistant', clinical_phenotype='intermediate',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule],
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1, annotations=[ann],
        )
        ctx = build_report_context(r)
        # Verify the row has metrics for both phenotypes
        row = ctx['database_hits']['rows'][0]
        metrics_dict = {m['label']: m['value'] for m in row['metrics']}
        assert metrics_dict.get('Phenotype') == 'resistant'
        assert metrics_dict.get('Clinical phenotype') == 'intermediate'
        # Verify drug table counts it correctly: 1 resistant, 0 intermediate
        assert ctx['summary']['drug_table']['rows'][0]['resistant_count'] == 1
        assert ctx['summary']['drug_table']['rows'][0]['intermediate_count'] == 0

    def test_db_hits_falls_back_to_clinical_phenotype_when_phenotype_unknown(self) -> None:
        # Rule with phenotype='unknown', clinical_phenotype='resistant'
        # → row should show both metrics, drug table uses clinical phenotype.
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='unknown', clinical_phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule],
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1, annotations=[ann],
        )
        html = render_html(r)
        # Clinical phenotype should be shown in the HTML
        assert 'Clinical phenotype' in html

    def test_drug_interpretation_by_ic50_prefers_resistant_when_any_hit_meets_threshold(self) -> None:
        low_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            ic50='6.0',
        )
        high_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=3,
            reference='A',
            mutation='V',
            phenotype='unknown',
            ic50='12.0',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=2,
            variants_in_cds=2,
            resistance_hits=2,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[low_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.9, depth=180),
                    feature_name='gag',
                    codon_pos=3,
                    ref_aa='A',
                    alt_aa='V',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[high_rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert ctx['summary']['drug_table']['rows'][0]['assessment'] == 'resistant'
        # Single method: has_assessment=True but has_final_assessment=False
        assert ctx['summary']['drug_table']['has_assessment'] is True
        assert ctx['summary']['drug_table']['has_final_assessment'] is False
        # Method assessments should have badge classes for single method
        ma = ctx['summary']['drug_table']['rows'][0]['method_assessments']
        assert len(ma) == 1
        assert ma[0]['assessment_badge_class'] == 'phenotype--resistant'

    def test_drug_interpretation_by_fold_ic50_marks_intermediate_when_no_resistant_hit(self) -> None:
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            fold_ic50='6.5',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_fold_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert ctx['summary']['drug_table']['rows'][0]['assessment'] == 'intermediate'
        # Single method: has_assessment=True but has_final_assessment=False
        assert ctx['summary']['drug_table']['has_final_assessment'] is False
        ma = ctx['summary']['drug_table']['rows'][0]['method_assessments']
        assert len(ma) == 1
        assert ma[0]['assessment_badge_class'] == 'phenotype--intermediate'

    def test_ic50_value_column_displayed_for_by_ic50_method(self) -> None:
        low_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            ic50='6.0',
        )
        high_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=3,
            reference='A',
            mutation='V',
            phenotype='unknown',
            ic50='12.0',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=2,
            variants_in_cds=2,
            resistance_hits=2,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[low_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.9, depth=180),
                    feature_name='gag',
                    codon_pos=3,
                    ref_aa='A',
                    alt_aa='V',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[high_rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_row = ctx['summary']['drug_table']['rows'][0]
        # Highest IC50 should be 12.0 (the max of 6.0 and 12.0)
        assert drug_row['ic50_display'] == '12'
        # Verify method_labels include value column info for by_ic50
        ic50_label = next(ml for ml in ctx['summary']['drug_table']['method_labels'] if ml['method'] == 'by_ic50')
        assert ic50_label['value_header'] == 'Highest IC50'
        assert ic50_label['value_field'] == 'ic50_display'
        # Verify col_count includes the value column (+1 for the Highest IC50 column)
        # Single method: no final Assessment column
        assert ctx['summary']['drug_table']['col_count'] == 4  # Drug, Hits, Highest IC50, IC50 Assessment

    def test_ic50_value_column_shows_dash_when_no_ic50_values(self) -> None:
        # A rule with phenotype but no IC50 value, using by_ic50 method
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_row = ctx['summary']['drug_table']['rows'][0]
        # No IC50 values → should show em dash
        assert drug_row['ic50_display'] == '\u2014'

    def test_fold_ic50_value_column_displayed_for_by_fold_ic50_method(self) -> None:
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            fold_ic50='6.5',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_fold_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_row = ctx['summary']['drug_table']['rows'][0]
        # Highest Fold IC50 should be 6.5
        assert drug_row['fold_ic50_display'] == '6.5'
        # Verify method_labels include value column info for by_fold_ic50
        fold_label = next(ml for ml in ctx['summary']['drug_table']['method_labels'] if ml['method'] == 'by_fold_ic50')
        assert fold_label['value_header'] == 'Highest Fold IC50'
        assert fold_label['value_field'] == 'fold_ic50_display'
        # Verify col_count includes the value column
        # Single method: no final Assessment column
        assert ctx['summary']['drug_table']['col_count'] == 4  # Drug, Hits, Highest Fold IC50, Fold IC50 Assessment

    def test_multi_method_drug_interpretation_shows_final_assessment_column(self) -> None:
        """With 2+ drug_interpretation methods, has_final_assessment is True and Assessment column appears."""
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
            ic50='8.0',
            fold_ic50='6.0',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                }),
            ),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_table = ctx['summary']['drug_table']
        # Two methods: has_final_assessment is True, has_assessment is True
        assert drug_table['has_assessment'] is True
        assert drug_table['has_final_assessment'] is True
        # Two method labels: by_phenotype + by_ic50
        assert len(drug_table['method_labels']) == 2
        # col_count includes Drug, Hits, Resistant/Intermediate/Sensitive (3),
        # by_phenotype assessment (1), Highest IC50 value (1), by_ic50 assessment (1), + Final Assessment (1) = 9
        assert drug_table['col_count'] == 9
        # The final assessment should be 'resistant' (most severe across methods)
        assert drug_table['rows'][0]['assessment'] == 'resistant'
        # Per-method assessments should NOT have badge class in multi-method
        # (badge_class is set on each method assessment, but template renders as plain text)
        for ma in drug_table['rows'][0]['method_assessments']:
            assert 'assessment_badge_class' in ma

    def test_single_method_drug_interpretation_hides_final_assessment_column(self) -> None:
        """With a single drug_interpretation method, has_final_assessment is False and
        per-method assessment entries have badge classes."""
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='sensitive',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_table = ctx['summary']['drug_table']
        # Single method: has_assessment=True but has_final_assessment=False
        assert drug_table['has_assessment'] is True
        assert drug_table['has_final_assessment'] is False
        # col_count: Drug + Hits + 3 phenotype cols + by_phenotype assessment = 6
        assert drug_table['col_count'] == 6
        # Method assessments should have badge classes
        ma = drug_table['rows'][0]['method_assessments']
        assert len(ma) == 1
        assert ma[0]['assessment'] == 'sensitive'
        assert ma[0]['assessment_badge_class'] == 'phenotype--sensitive'

    def test_drug_alias_is_rendered_from_drug_table_alias_column(self) -> None:
        result = _make_result()

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE drug (name TEXT, alias TEXT)')
        conn.execute("INSERT INTO drug (name, alias) VALUES ('DrugA', 'DRA')")
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert ctx['summary']['drug_table']['rows'][0]['name'] == 'DrugA (DRA)'
        assert ctx['summary']['drug_table']['rows'][0]['summary_name'] == 'DRA'
        assert 'DRA' in ctx['summary']['narrative']

    def test_effect_as_resistant_adds_metadata_hit_row(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=18, ref='C', alt='CA', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='PfsX',
                    consequence='frameshift',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        metadata_row = next(
            (
                row for row in ctx['database_hits']['rows']
                if row['drug_key'] == 'Aciclovir' and row['source'] == 'Metadata algorithm'
            ),
            None,
        )
        assert metadata_row is not None
        assert (
            'frameshift interpreted as resistant by metadata algorithm (UL23, NC_001806).'
            in metadata_row['comment']
        )
        aciclovir_row = next(
            row for row in ctx['summary']['drug_table']['rows'] if row['name'] == 'Aciclovir'
        )
        assert aciclovir_row['assessment'] == ''

    def test_effect_as_resistant_matches_reference_accession_without_version(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806.2',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806.2', pos=18, ref='C', alt='CA', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='PfsX',
                    consequence='frameshift',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        metadata_row = next(
            (
                row for row in ctx['database_hits']['rows']
                if row['drug_key'] == 'Aciclovir' and row['source'] == 'Metadata algorithm'
            ),
            None,
        )
        assert metadata_row is not None
        assert (
            'frameshift interpreted as resistant by metadata algorithm (UL23, NC_001806.2).'
            in metadata_row['comment']
        )

    def test_effect_as_resistant_shows_nothing_without_known_phenotypes(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=18, ref='C', alt='CA', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='PfsX',
                    consequence='frameshift',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert not any(row['source'] == 'Metadata algorithm' for row in ctx['database_hits']['rows'])

    def test_effect_as_resistant_does_not_fire_for_non_matching_consequence(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=18, ref='C', alt='A', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='T',
                    consequence='missense',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift', 'stop_gained'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert not any(row['source'] == 'Metadata algorithm' for row in ctx['database_hits']['rows'])

    def test_effect_as_resistant_does_not_fire_for_reference_mismatch(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001999',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001999', pos=18, ref='C', alt='CA', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='PfsX',
                    consequence='frameshift',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert not any(row['source'] == 'Metadata algorithm' for row in ctx['database_hits']['rows'])

    def test_effect_as_resistant_matches_stop_gained(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=50, ref='C', alt='T', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=17,
                    ref_aa='Q',
                    alt_aa='*',
                    consequence='stop_gained',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['stop_gained'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        metadata_row = next(
            (
                row for row in ctx['database_hits']['rows']
                if row['drug_key'] == 'Aciclovir' and row['source'] == 'Metadata algorithm'
            ),
            None,
        )
        assert metadata_row is not None
        assert (
            'premature stop interpreted as resistant by metadata algorithm (UL23, NC_001806).'
            in metadata_row['comment']
        )

    def test_effect_as_resistant_does_not_match_missense(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=18, ref='C', alt='A', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=6,
                    ref_aa='P',
                    alt_aa='T',
                    consequence='missense',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift', 'stop_gained'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        assert not any(row['source'] == 'Metadata algorithm' for row in ctx['database_hits']['rows'])

    def test_effect_as_resistant_multiple_effects_match(self) -> None:
        result = ProfilingResult(
            project_name='T',
            reference_name='NC_001806',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='NC_001806', pos=30, ref='A', alt='ACGT', allele_freq=0.95, depth=200),
                    feature_name='UL23',
                    codon_pos=10,
                    ref_aa='K',
                    alt_aa='Kdel',
                    consequence='deletion',
                    af_bin='high',
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift', 'deletion'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        metadata_row = next(
            (
                row for row in ctx['database_hits']['rows']
                if row['drug_key'] == 'Aciclovir' and row['source'] == 'Metadata algorithm'
            ),
            None,
        )
        assert metadata_row is not None
        assert (
            'in-frame deletion interpreted as resistant by metadata algorithm (UL23, NC_001806).'
            in metadata_row['comment']
        )

    def test_similarity_hits_counts_unique_positions(self) -> None:
        # Two rules at the same position for different drugs
        # → similarity_entries has 2 rows (one per drug/rule)
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule_a = ResistanceRule(
            id=10, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I', phenotype='resistant',
        )
        rule_b = ResistanceRule(
            id=11, feature_name='gag', feature_id=1,
            drug_name='DrugB', drug_id=2, reference_identifier='ref',
            position=5, reference='L', mutation='I', phenotype='intermediate',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        ctx = build_report_context(r, rules=[rule_a, rule_b])
        assert ctx['similarity_entries']['count'] == 2      # 2 similarity entries for the mutation
        assert len(ctx['similarity_entries']['rows']) == 2  # one row per rule

    def test_similarity_phenotype_prefers_phenotype_over_clinical(self) -> None:
        # Similarity row with phenotype='resistant', clinical_phenotype='intermediate'
        # → row shows both phenotypes in metrics.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='resistant', clinical_phenotype='intermediate',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        ctx = build_report_context(r, rules=[rule])
        # Verify similarity entry row has both phenotypes
        assert len(ctx['similarity_entries']['rows']) == 1
        row = ctx['similarity_entries']['rows'][0]
        metrics_dict = {m['label']: m['value'] for m in row.get('metrics', [])}
        assert metrics_dict.get('Phenotype') == 'resistant'
        assert metrics_dict.get('Clinical phenotype') == 'intermediate'

    def test_similarity_clinical_phenotype_column_rendered_when_available(self) -> None:
        # When a similarity rule has a non-unknown clinical_phenotype,
        # the 'Clinical phenotype' column must appear in the rendered HTML.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='unknown', clinical_phenotype='resistant',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        html = render_html(r, rules=[rule])
        assert 'Clinical phenotype' in html
        # The similarity section must exist when there are similarity entries
        assert 'section-similarity' in html

    def test_similarity_clinical_phenotype_column_hidden_when_all_unknown(self) -> None:
        # When all rules across ALL sections have clinical_phenotype='unknown' the column
        # must be omitted everywhere — same 'if available' behaviour as 'Mutations in database'.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, feature_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='unknown',  # clinical_phenotype defaults to 'unknown'
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        html = render_html(r, rules=[rule])
        context = build_report_context(r, rules=[rule])
        assert context['similarity_entries']['has_phenotype_metrics'] is False
        assert context['similarity_entries']['has_clinical_phenotype_metrics'] is False
        assert 'Phenotype / Clinical phenotype' not in html


class TestPdfExports:
    def test_similarity_clinical_phenotype_shown_when_db_hits_have_it(self) -> None:
        # If db_hits carry a non-unknown clinical_phenotype, the similarity section must
        # ALSO show the Clinical phenotype column even if those similarity rules have 'unknown'.
        var_hit = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule_hit = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='resistant', clinical_phenotype='resistant',
        )
        ann_hit = AnnotatedVariant(
            variant=var_hit, feature_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule_hit],
        )
        # Similarity hit at a different position — rule has clinical_phenotype='unknown'
        var_sim = VariantCall(chrom='ref', pos=15, ref='C', alt='T', allele_freq=0.5, depth=100)
        rule_sim = ResistanceRule(
            id=2, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='resistant',  # clinical_phenotype='unknown' (default)
        )
        ann_sim = AnnotatedVariant(
            variant=var_sim, feature_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=2, variants_in_cds=2, resistance_hits=1,
            annotations=[ann_hit, ann_sim],
        )
        html = render_html(r, rules=[rule_hit, rule_sim])
        sim_start = html.find('section-similarity')
        assert sim_start != -1
        # Clinical phenotype column must appear in the similarity section too
        assert 'Clinical phenotype' in html[sim_start:]

    def test_potential_effects_excludes_snp_rule_for_indel_annotation(self):

        var = VariantCall(chrom='ref', pos=10, ref='A', alt='AGGG', allele_freq=0.8, depth=120)
        ann = AnnotatedVariant(
            variant=var,
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )

        context = _build_potential_effects_rows(result, [snp_rule])
        assert context['rows'] == []

    def test_potential_effects_keeps_indel_rule_for_indel_annotation(self):
        var = VariantCall(chrom='ref', pos=10, ref='A', alt='AGGG', allele_freq=0.8, depth=120)
        ann = AnnotatedVariant(
            variant=var,
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='K3KG',
            phenotype='resistant',
        )

        context = _build_potential_effects_rows(result, [indel_rule])
        rows = context['rows']
        assert len(rows) == 1
        assert rows[0]['drug'] == 'DrugA'
        assert rows[0]['similarity'] == 'moderate'

    def test_render_html_feature_overview(self):

        r = _make_result()
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE feature ('
            'name TEXT, protein TEXT, protein_id TEXT, ncbi_protein_url TEXT, '
            'locus_tag TEXT, note TEXT, nt_sequence TEXT, aa_sequence TEXT, start INTEGER, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO feature (name, protein, protein_id, ncbi_protein_url, locus_tag, note, nt_sequence, aa_sequence, start, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                'gag',
                'Capsid protein',
                'YP_009137097.1',
                'https://www.ncbi.nlm.nih.gov/protein/YP_009137097.1/',
                'UL23',
                'Thymidine kinase',
                'ATGAAAGCTTAA',
                'MKAFGP',
                100,
                1,
            ),
        )
        conn.commit()

        cards = load_feature_cards(conn, r.reference_name, {'gag'})
        assert len(cards) == 1
        assert cards[0]['protein_id'] == 'YP_009137097.1'
        assert cards[0]['ncbi_protein_url'] == 'https://www.ncbi.nlm.nih.gov/protein/YP_009137097.1/'
        assert cards[0]['aa_sequence'] == 'MKAFGP'

    def test_build_report_context_tracks_unassessed_rule_positions(self):
        r = _make_result()
        r.coverage_gaps = [CoverageGap(feature_name='gag', codon_start=2, codon_end=2)]
        rules = [
            ResistanceRule(
                id=2,
                feature_name='gag',
                feature_id=1,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='tiny_ref',
                position=2,
                reference='K',
                mutation='E',
                phenotype='resistant',
            ),
            ResistanceRule(
                id=3,
                feature_name='gag',
                feature_id=1,
                drug_name='DrugB',
                drug_id=2,
                reference_identifier='tiny_ref',
                position=5,
                reference='A',
                mutation='V',
                phenotype='resistant',
            ),
        ]

        context = build_report_context(r, rules=rules)
        narrative = context['summary']['narrative']
        assert 'could not be assessed' in narrative
        assert 'gag' in narrative.lower()

    def test_render_html_shows_unassessed_rule_tile_without_detail_table(self):
        r = _make_result()
        r.coverage_gaps = [CoverageGap(feature_name='gag', codon_start=2, codon_end=2)]
        rules = [
            ResistanceRule(
                id=2,
                feature_name='gag',
                feature_id=1,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='tiny_ref',
                position=2,
                reference='K',
                mutation='E',
                phenotype='resistant',
            ),
            ResistanceRule(
                id=3,
                feature_name='gag',
                feature_id=1,
                drug_name='DrugB',
                drug_id=2,
                reference_identifier='tiny_ref',
                position=5,
                reference='A',
                mutation='V',
                phenotype='resistant',
            ),
        ]

        html = render_html(r, rules=rules)
        assert 'Unassessed rule positions' not in html
        assert 'id=\'section-unassessed\'' not in html

    def test_build_report_context_reports_rule_positions_for_vcf_mode_without_gaps(self):
        variant = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=200)
        ann = AnnotatedVariant(
            variant=variant,
            feature_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
            is_fasta_mode=False,
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
            coverage_gaps=[],
        )
        rules = [
            ResistanceRule(
                id=11,
                feature_name='gag',
                feature_id=1,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='tiny_ref',
                position=2,
                reference='K',
                mutation='E',
                phenotype='resistant',
            ),
        ]

        context = build_report_context(result, rules=rules)
        assert 'could not be assessed' not in context['summary']['narrative']

    def test_build_report_context_sorts_db_hits_by_drug_then_resistance_then_ic50(self):
        resistant_rule = ResistanceRule(
            id=21,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
            ic50='2',
        )
        intermediate_rule = ResistanceRule(
            id=22,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=3,
            reference='A',
            mutation='V',
            phenotype='intermediate',
            ic50='10',
        )
        high_ic50_unknown_rule = ResistanceRule(
            id=23,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugB',
            drug_id=2,
            reference_identifier='tiny_ref',
            position=4,
            reference='L',
            mutation='I',
            phenotype='unknown',
            clinical_phenotype='unknown',
            ic50='25',
        )
        low_ic50_unknown_rule = ResistanceRule(
            id=24,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugB',
            drug_id=2,
            reference_identifier='tiny_ref',
            position=5,
            reference='P',
            mutation='S',
            phenotype='unknown',
            clinical_phenotype='unknown',
            ic50='5',
        )

        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=4,
            variants_in_cds=4,
            resistance_hits=4,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[resistant_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.9, depth=180),
                    feature_name='gag',
                    codon_pos=3,
                    ref_aa='A',
                    alt_aa='V',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[intermediate_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=9, ref='C', alt='T', allele_freq=0.85, depth=170),
                    feature_name='gag',
                    codon_pos=4,
                    ref_aa='L',
                    alt_aa='I',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[high_ic50_unknown_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=12, ref='C', alt='A', allele_freq=0.8, depth=160),
                    feature_name='gag',
                    codon_pos=5,
                    ref_aa='P',
                    alt_aa='S',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[low_ic50_unknown_rule],
                ),
            ],
        )

        context = build_report_context(result)
        rows = context['database_hits']['rows']

        assert [row['drug'] for row in rows] == ['DrugA', 'DrugA', 'DrugB', 'DrugB']
        metrics_by_drug = {
            drug: [{m['label']: m['value'] for m in row['metrics']} for row in rows if row['drug'] == drug]
            for drug in {'DrugA', 'DrugB'}
        }
        assert {m['Phenotype'] for m in metrics_by_drug['DrugA']} == {'resistant', 'intermediate'}
        assert {m['IC50'] for m in metrics_by_drug['DrugB']} == {'25', '5'}

    def test_render_html_includes_drug_badges(self) -> None:
        r = _make_result()
        html = render_html(r)
        assert 'db-hit-pill--single' in html

    def test_render_html_includes_table_filter_controls_js(self) -> None:
        r = _make_result()
        html = render_html(r)
        assert 'createFacetedTable' in html
        assert 'mutation-filter-menu' in html

    def test_render_html_includes_expandable_alignment_rows(self) -> None:
        r = _make_result()

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE feature ('
            'name TEXT, start INTEGER, end INTEGER, strand TEXT, codon_start INTEGER, nt_sequence TEXT, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO feature (name, start, end, strand, codon_start, nt_sequence, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('gag', 0, 12, '+', 0, 'ATGAAAGCTTAA', 1),
        )
        conn.commit()

        html = render_html(r, project_conn=conn)

        assert 'mutation-row--expandable' in html
        assert 'mutation-alignment-row' in html
        assert 'aln-block' in html
        assert 'aln-affected' in html
        assert "aln-cell aln-mutation" not in html
        assert 'Coding orientation:' in html

    def test_build_report_context_includes_summary_text(self) -> None:
        hit_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='tiny_ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
            clinical_phenotype='resistant',
            fold_ic50='>10x',
        )
        hit_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
            feature_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
            rule_matches=[hit_rule],
        )

        sim_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100),
            feature_name='gag',
            codon_pos=5,
            ref_aa='L',
            alt_aa='V',
            consequence='missense',
            af_bin='high',
        )
        high_impact_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=10, ref='A', alt='AG', allele_freq=0.8, depth=120),
            feature_name='gag',
            codon_pos=6,
            ref_aa='P',
            alt_aa='PfsX',
            consequence='frameshift',
            af_bin='high',
        )
        sim_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugB',
            drug_id=2,
            reference_identifier='tiny_ref',
            position=5,
            reference='L',
            mutation='I',
            phenotype='intermediate',
            clinical_phenotype='resistant',
            ic50='5-10 uM',
        )

        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=3,
            variants_in_cds=3,
            resistance_hits=1,
            organism='Human alphaherpesvirus 1',
            annotations=[hit_ann, sim_ann, high_impact_ann],
        )

        context = build_report_context(result, rules=[hit_rule, sim_rule])
        text = context['summary']['narrative']
        assert 'no final drug interpretation algorithm is configured' in text
        assert 'high-impact variant' in text
        assert 'Human alphaherpesvirus 1' in text

    def test_build_report_context_mentions_coverage_gaps(self) -> None:
        hit_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=10, ref='A', alt='T', allele_freq=0.8, depth=100),
            feature_name='pol',
            codon_pos=1,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            organism='Test organism',
            annotations=[hit_ann],
            coverage_gaps=[
                CoverageGap(feature_name='gag', codon_start=5, codon_end=10),
                CoverageGap(feature_name='rt', codon_start=20, codon_end=25),
            ],
        )

        context = build_report_context(result)
        text = context['summary']['narrative']
        assert 'could not be assessed' in text
        assert 'incomplete sequence data' in text
        assert 'gag' in text.lower()
        assert 'rt' in text.lower()

    def test_render_html_includes_summary_translation_controls(self) -> None:
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)')
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            ('drug_interpretation', '{}'),
        )
        conn.commit()

        html = render_html(_make_result(), project_conn=conn)
        assert 'Interpretation Summary' in html
        assert 'data-lang="en"' in html
        assert 'data-lang="de"' in html
        assert 'data-lang="fr"' in html
        assert 'data-lang="es"' in html
        assert '>EN<' in html
        assert '>DE<' in html

    def test_render_html_highlights_nt_and_aa_changed_segments(self) -> None:
        r = _make_result()
        html = render_html(r)

        assert 'A4G' in html
        assert 'K3E' in html

    def test_render_html_highlights_insertion_segments_in_table(self) -> None:
        var = VariantCall(chrom='ref', pos=3, ref='C', alt='CG', allele_freq=0.9, depth=300)
        ann = AnnotatedVariant(
            variant=var,
            feature_name='gag',
            codon_pos=2,
            ref_codon='AAA',
            alt_codon='AAAGGG',
            ref_aa='K',
            alt_aa='KG',
            consequence='insertion',
            af_bin='high',
        )
        r = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
        )

        html = render_html(r)
        assert 'C4CG' in html
        assert 'K3KG' in html

    def test_render_html_highlights_frameshift_indel_segments_in_table(self) -> None:
        ins = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='A', alt='AG', allele_freq=0.9, depth=300),
            feature_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='KfsX',
            consequence='frameshift',
            af_bin='high',
        )
        dele = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=6, ref='AC', alt='A', allele_freq=0.8, depth=250),
            feature_name='gag',
            codon_pos=3,
            ref_aa='P',
            alt_aa='PfsX',
            consequence='frameshift',
            af_bin='high',
        )
        r = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=2,
            variants_in_cds=2,
            resistance_hits=0,
            annotations=[ins, dele],
        )

        html = render_html(r)
        assert 'A4AG' in html
        assert 'AC7A' in html

    def test_render_html_fasta_frameshift_uses_indel_nt_not_fsx_token(self) -> None:
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=47663, ref='GG', alt='G', allele_freq=0.8, depth=250),
            feature_name='UL23',
            codon_pos=47,
            ref_aa='P',
            alt_aa='PfsX',
            consequence='frameshift',
            af_bin='high',
            is_fasta_mode=True,
        )
        r = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=100000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=0,
            annotations=[ann],
        )

        html = render_html(r)
        assert 'GG47664<u><strong>fsX</strong></u>' not in html
        assert 'GG47664G' in html

    def test_render_html_uses_alignment_title_for_fasta_mode(self) -> None:
        r = _make_result()
        r.annotations[0].is_fasta_mode = True

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE feature ('
            'name TEXT, start INTEGER, end INTEGER, strand TEXT, codon_start INTEGER, nt_sequence TEXT, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO feature (name, start, end, strand, codon_start, nt_sequence, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('gag', 0, 12, '+', 0, 'ATGAAAGCTTAA', 1),
        )
        conn.commit()

        html = render_html(r, project_conn=conn)
        assert 'Alignment' in html

    def test_lollipop_svg_contains_non_covered_legend(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        r.coverage_gaps = [CoverageGap(feature_name='gag', codon_start=2, codon_end=2)]
        features = [
            FeatureRecord(
                id=1,
                reference_id=1,
                name='gag',
                protein='Gag',
                start=0,
                end=12,
                strand='+',
                codon_start=0,
                nt_sequence='ATGAAAGCTTAA',
            ),
        ]

        svg = render_lollipop_plot_bytes(r, features, fmt='svg')
        assert svg is not None
        assert b'non covered' in svg
        assert b'#6b7280' in svg
        assert b'opacity: 0.12' in svg

    def test_lollipop_svg_omits_non_covered_legend_without_coverage_gaps(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        r.coverage_gaps = []
        features = [
            FeatureRecord(
                id=1,
                reference_id=1,
                name='gag',
                protein='Gag',
                start=0,
                end=12,
                strand='+',
                codon_start=0,
                nt_sequence='ATGAAAGCTTAA',
            ),
        ]

        svg = render_lollipop_plot_bytes(r, features, fmt='svg')
        assert svg is not None
        assert b'non covered' not in svg

    def test_lollipop_svg_omits_intron_legend_without_split_feature(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        features = [
            FeatureRecord(
                id=1,
                reference_id=1,
                name='gag',
                protein='Gag',
                start=0,
                end=12,
                strand='+',
                codon_start=0,
                nt_sequence='ATGAAAGCTTAA',
            ),
        ]

        svg = render_lollipop_plot_bytes(r, features, fmt='svg')
        assert svg is not None
        assert b'Intron (non-coding)' not in svg


class TestSplitFeaturePlotRendering:
    def test_genome_overview_draws_one_block_per_segment_in_genomic_order(self) -> None:
        from respro.report.plots import _draw_genome_overview

        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='UL30',
            protein='UL30',
            start=10,
            end=36,
            strand='-',
            codon_start=0,
            nt_sequence='A' * 12,
            segments=(
                FeatureSegment(segment_index=0, start=30, end=36),
                FeatureSegment(segment_index=1, start=10, end=16),
            ),
        )

        fig, ax = plt.subplots()
        try:
            _draw_genome_overview(ax, [feature], {feature.name}, reference_length_nt=50)

            rects = ax.patches
            assert len(rects) == 2
            assert [rect.get_x() for rect in rects] == [11, 31]
            assert [rect.get_width() for rect in rects] == [6, 6]
            assert [text.get_text() for text in ax.texts] == ['UL30']
        finally:
            plt.close(fig)

    def test_feature_track_draws_one_block_per_segment_in_genomic_order(self) -> None:
        from respro.report.plots import _draw_feature_track

        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='UL30',
            protein='UL30',
            start=10,
            end=36,
            strand='-',
            codon_start=0,
            nt_sequence='A' * 12,
            segments=(
                FeatureSegment(segment_index=0, start=30, end=36),
                FeatureSegment(segment_index=1, start=10, end=16),
            ),
        )

        fig, ax = plt.subplots()
        try:
            _draw_feature_track(ax, feature)

            rects = ax.patches
            assert len(rects) == 2
            # Full feature box (start=10 → x=11, width=26) then intron gap (16–30 → x=17, width=14)
            assert [rect.get_x() for rect in rects] == [11, 17]
            assert [rect.get_width() for rect in rects] == [26, 14]
            assert [text.get_text() for text in ax.texts] == ['← UL30 ←']
        finally:
            plt.close(fig)


class TestAlignmentVisualization:
    def test_fasta_alignment_renders_match_bars_from_aligned_query(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='FASTA',
            protein='F',
            start=0,
            end=9,
            strand='+',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments('AAATCCGGG', [match])['FASTA']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CCC', alt='TCC'),
            feature_name='FASTA',
            codon_pos=1,
            ref_codon='CCC',
            alt_codon='TCC',
            ref_aa='P',
            alt_aa='S',
            consequence='missense',
            af_bin='high',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-match-cell'>|</span>") == 8

    def test_fasta_snp_highlights_the_existing_aligned_base(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='FASTASNP',
            protein='F',
            start=0,
            end=9,
            strand='+',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments('AAATCCGGG', [match])['FASTASNP']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CCC', alt='TCC'),
            feature_name='FASTASNP',
            codon_pos=1,
            ref_codon='CCC',
            alt_codon='TCC',
            ref_aa='P',
            alt_aa='S',
            consequence='missense',
            af_bin='high',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert "<span class='aln-cell aln-affected'>T</span>" in html

    def test_fasta_synonymous_snp_still_highlights_anchor_base(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='FASTASYN',
            protein='F',
            start=0,
            end=9,
            strand='+',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments('AAATCCGGG', [match])['FASTASYN']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='T'),
            feature_name='FASTASYN',
            codon_pos=1,
            ref_codon='GGC',
            alt_codon='GGC',
            ref_aa='G',
            alt_aa='G',
            consequence='synonymous',
            af_bin='high',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count('aln-cell aln-affected') == 1
        assert "<span class='aln-cell aln-affected'>T</span>" in html

    def test_vcf_alignment_renders_match_bars_from_overlay_query(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='VCF',
            protein='V',
            start=100,
            end=109,
            strand='+',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments('AAACCCGGG', [match])['VCF']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=103, ref='C', alt='T'),
            feature_name='VCF',
            codon_pos=1,
            consequence='missense',
            af_bin='high',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-match-cell'>|</span>") == 8

    def test_highlight_uses_real_cigar_alignment_window(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='UL23',
            protein='UL23',
            start=0,
            end=15,
            strand='+',
            codon_start=0,
            nt_sequence='ATGACCCCCAAGGCC',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=15,
            strand='+',
            cigar='8M1D6M',
            cds_start=0,
        )
        alignments = build_feature_alignments('ATGACCCC AAGGCC'.replace(' ', ''), [match])

        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=7, ref='CC', alt='C'),
            feature_name='UL23',
            codon_pos=2,
            consequence='deletion',
            ref_codon='CCC',
            ref_aa='P',
            alt_aa='P',
        )

        html = str(build_alignment_html(ann, alignments['UL23'], context_codons=2))
        assert "aln-cell aln-affected" in html
        assert "aln-mutation" not in html
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_fasta_frameshift_deletion_highlights_gap_column_in_alignment(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='UL23',
            protein='UL23',
            start=0,
            end=19,
            strand='+',
            codon_start=0,
            nt_sequence='TAGCGTGGGCATTTTCTG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=18,
            strand='+',
            cigar='6M1D12M',
            cds_start=0,
        )
        alignment = build_feature_alignments('TAGCGTGGCATTTTCTG', [match])['UL23']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=5, ref='TG', alt='T'),
            feature_name='UL23',
            codon_pos=1,
            consequence='frameshift',
            ref_aa='P',
            alt_aa='PfsX',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=2))
        assert html.count('aln-cell aln-affected') == 1
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_fasta_insertion_uses_existing_alignment_without_duplication(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='UL23INS',
            protein='UL23',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGCCCAAAGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=13,
            strand='+',
            cigar='4M1I8M',
            cds_start=0,
        )
        alignment = build_feature_alignments('ATGCGCCCAAAGGG', [match])['UL23INS']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='CG'),
            feature_name='UL23INS',
            codon_pos=1,
            consequence='insertion',
            ref_aa='P',
            alt_aa='P',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count('aln-cell aln-affected') == 1
        assert html.count("<span class='aln-cell aln-affected'>G</span>") == 1

    def test_fasta_reverse_frameshift_deletion_highlights_gap_column_in_alignment(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='REVDEL',
            protein='REVDEL',
            start=0,
            end=9,
            strand='-',
            codon_start=0,
            nt_sequence='ATGGGGTTT',
        )
        coding_query = 'ATGGGTTT'
        query = str(Seq(coding_query).reverse_complement())
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=len(query),
            strand='-',
            cigar='3M1D5M',
            cds_start=0,
        )
        alignment = build_feature_alignments(query, [match])['REVDEL']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='CC', alt='C'),
            feature_name='REVDEL',
            codon_pos=1,
            consequence='frameshift',
            ref_aa='G',
            alt_aa='GfsX',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count('aln-cell aln-affected') == 1
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_reverse_feature_codon_spacing_follows_cds_direction(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='REV',
            protein='REV',
            start=0,
            end=9,
            strand='-',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignments = build_feature_alignments('AAACCCGGG', [match])

        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='C', alt='T'),
            feature_name='REV',
            codon_pos=1,
            consequence='missense',
            ref_codon='CCC',
            alt_codon='CTC',
            ref_aa='P',
            alt_aa='L',
        )

        html = str(build_alignment_html(ann, alignments['REV'], context_codons=1))
        assert (
            "<span class='aln-cell'>C</span><span class='aln-cell'>C</span>"
            "<span class='aln-cell'>C</span><span class='aln-sep'></span>"
        ) in html

    def test_affected_nt_positions_combined_codon_event_highlights_all_differing_positions(self) -> None:
        """
        For a combined codon event (two SNPs in one codon), _affected_nt_positions
        should return all positions where ref_codon differs from alt_codon, not just
        the anchor SNP's position.

        Feature: ATG TCT AAA AAA (positions 0-11 on + strand)
        Codon 1 = TCT at coding positions 3,4,5
        Combined event: TCT → ACG (positions 3 and 5 differ)
        The anchor SNP is at position 3, but position 5 must also be highlighted.
        """
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='COMB',
            protein='C',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGTCTAAAAAA',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_feature_alignments('ATGACAAAAAAA', [match])['COMB']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='T', alt='A'),
            feature_name='COMB',
            codon_pos=1,
            ref_codon='TCT',
            alt_codon='ACG',
            ref_aa='S',
            alt_aa='T',
            consequence='missense',
            is_combined_codon_event=True,
            combined_member_count=2,
        )
        codon_nt_start = alignment.codon_start + ann.codon_pos * 3
        affected = _affected_nt_positions(ann, alignment, codon_nt_start)
        # On + strand, coding positions 3 and 5 differ (T→A at pos 0 of codon,
        # T→G at pos 2 of codon). Position 4 (C→C) is unchanged.
        assert 3 in affected
        assert 5 in affected
        assert 4 not in affected

    def test_affected_nt_positions_combined_codon_reverse_strand(self) -> None:
        """
        Combined codon event on reverse strand must correctly map coding
        positions to native positions.
        """
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='RCOMB',
            protein='R',
            start=0,
            end=9,
            strand='-',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        coding_query = 'AAATCCGGG'
        query = str(Seq(coding_query).reverse_complement())
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments(query, [match])['RCOMB']
        # On - strand, codon 1 coding positions 3,4,5 map to native 5,4,3
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=5, ref='C', alt='T'),
            feature_name='RCOMB',
            codon_pos=1,
            ref_codon='CCC',
            alt_codon='CTC',
            ref_aa='P',
            alt_aa='L',
            consequence='missense',
            is_combined_codon_event=True,
            combined_member_count=2,
        )
        codon_nt_start = alignment.codon_start + ann.codon_pos * 3
        affected = _affected_nt_positions(ann, alignment, codon_nt_start)
        # Native positions for coding 3,4,5 on - strand feature_length=9:
        # coding 3 → native 5, coding 4 → native 4, coding 5 → native 3
        # CCC→CTC: C→C at idx0 (coding 3→native 5), C→T at idx1 (coding 4→native 4), C→C at idx2
        assert 4 in affected
        assert 5 not in affected
        assert 3 not in affected

    def test_apply_vcf_overlay_combined_codon_event_overlays_all_differing_positions(self) -> None:
        """
        For a combined codon event in VCF mode, _apply_vcf_overlay must overlay
        all positions where ref_codon differs from alt_codon, not just the anchor SNP.
        """
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='VOVER',
            protein='V',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGTCTAAAAAA',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        # VCF mode: query is identical to reference (no variants pre-aligned)
        alignment = build_feature_alignments('ATGTCTAAAAAA', [match])['VOVER']
        # Combined event: TCT → ACG, anchor is the first SNP at pos 3
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='T', alt='A'),
            feature_name='VOVER',
            codon_pos=1,
            ref_codon='TCT',
            alt_codon='ACG',
            ref_aa='S',
            alt_aa='T',
            consequence='missense',
            is_combined_codon_event=True,
            combined_member_count=2,
            is_fasta_mode=False,
        )
        # Use the full alignment as the window for simplicity
        ref_window = alignment.aligned_ref
        query_window = alignment.aligned_query
        coding_positions = alignment.aln_coding_pos
        native_positions = alignment.aln_native_pos
        native_anchor_positions = alignment.aln_native_anchor_pos

        new_ref, new_query, _, _, _ = _apply_vcf_overlay(
            ann, alignment,
            ref_window, query_window,
            coding_positions, native_positions, native_anchor_positions,
        )
        # After overlay, positions 3 (coding) should be A, position 5 should be G
        # Reference has TCT at coding 3,4,5; overlay should change to ACG
        for aln_idx, cpos in enumerate(coding_positions):
            if cpos == 3:
                assert new_query[aln_idx] == 'A', (
                    f'Coding pos 3 should be A after overlay, got {new_query[aln_idx]}'
                )
            elif cpos == 4:
                assert new_query[aln_idx] == 'C', (
                    f'Coding pos 4 should stay C after overlay, got {new_query[aln_idx]}'
                )

    def test_apply_vcf_overlay_combined_codon_event_reverse_strand(self) -> None:
        """
        For a combined codon event on the minus strand, _apply_vcf_overlay must
        complement the alt base before writing it into the native-orientation display.
        """
        from Bio.Seq import Seq

        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='RCOMB2',
            protein='R',
            start=0,
            end=9,
            strand='-',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        # Coding query identical to reference (VCF mode — no variants pre-aligned)
        coding_query = 'AAACCCGGG'
        query = str(Seq(coding_query).reverse_complement())
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments(query, [match])['RCOMB2']
        # Combined event in coding: CCC → CTC at codon 1 (coding positions 3,4,5)
        # idx=1: C→T, coding pos 4
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=5, ref='C', alt='T'),
            feature_name='RCOMB2',
            codon_pos=1,
            ref_codon='CCC',
            alt_codon='CTC',
            ref_aa='P',
            alt_aa='L',
            consequence='missense',
            is_combined_codon_event=True,
            combined_member_count=2,
            is_fasta_mode=False,
        )
        ref_window = alignment.aligned_ref
        query_window = alignment.aligned_query
        coding_positions = alignment.aln_coding_pos
        native_positions = alignment.aln_native_pos
        native_anchor_positions = alignment.aln_native_anchor_pos

        new_ref, new_query, _, _, _ = _apply_vcf_overlay(
            ann, alignment,
            ref_window, query_window,
            coding_positions, native_positions, native_anchor_positions,
        )
        # On - strand with feature_length=9:
        # coding position 4 maps to native position 4 (9-1-4=4, same by symmetry)
        # In native display at coding_pos=4: ref shows G (complement of coding C),
        # alt should show A (complement of coding T), NOT T.
        for aln_idx, cpos in enumerate(coding_positions):
            if cpos == 4:
                assert new_ref[aln_idx] == 'G', (
                    f'At coding pos 4, ref should be G, got {new_ref[aln_idx]}'
                )
                assert new_query[aln_idx] == 'A', (
                    f'At coding pos 4, query should be A (complement of coding T), got {new_query[aln_idx]}'
                )


class TestCoverageGapPlotBounds:
    def test_reverse_strand_gap_bounds_include_full_terminal_codons(self) -> None:
        from respro.report.plots import _coverage_gap_nt_bounds

        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='REV',
            protein='Rev',
            start=100,
            end=130,
            strand='-',
            codon_start=0,
            nt_sequence='A' * 30,
        )
        gap = CoverageGap(feature_name='REV', codon_start=0, codon_end=2)

        start, end = _coverage_gap_nt_bounds(feature, gap)
        assert (start, end) == (121, 130)

    def test_vcf_snp_overlay_switches_base_and_highlights_anchor(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='G',
            protein='G',
            start=100,
            end=109,
            strand='+',
            codon_start=0,
            nt_sequence='AAACCCGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_feature_alignments('AAACCCGGG', [match])['G']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=103, ref='C', alt='T'),
            feature_name='G',
            codon_pos=1,
            consequence='missense',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert "<span class='aln-label'>Query</span>" in html
        assert "<span class='aln-cell aln-affected'>T</span>" in html

    def test_vcf_deletion_overlay_places_gap_after_anchor(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='D',
            protein='D',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGCCCAAAGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_feature_alignments('ATGCCCAAAGGG', [match])['D']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CC', alt='C'),
            feature_name='D',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_vcf_deletion_does_not_highlight_anchor_cell(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='DANCHOR',
            protein='D',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGCCCAAAGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_feature_alignments('ATGCCCAAAGGG', [match])['DANCHOR']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CC', alt='C'),
            feature_name='DANCHOR',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        # One affected alignment column (deleted base) rendered on Query line only.
        assert html.count('aln-cell aln-affected') == 1

    def test_vcf_insertion_does_not_highlight_anchor_cell(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='IANCHOR',
            protein='I',
            start=0,
            end=12,
            strand='+',
            codon_start=0,
            nt_sequence='ATGCCCAAAGGG',
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_feature_alignments('ATGCCCAAAGGG', [match])['IANCHOR']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='CG'),
            feature_name='IANCHOR',
            codon_pos=1,
            consequence='insertion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        # One affected alignment column (inserted base) rendered on Query line only.
        assert html.count('aln-cell aln-affected') == 1

    def test_vcf_long_deletion_expands_alignment_context(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='DLONG',
            protein='D',
            start=0,
            end=30,
            strand='+',
            codon_start=0,
            nt_sequence='A' * 30,
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=30,
            strand='+',
            cigar='30M',
            cds_start=0,
        )
        alignment = build_feature_alignments('A' * 30, [match])['DLONG']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='AAAAAAAAAA', alt='A'),
            feature_name='DLONG',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-affected'>-</span>") == 9

    def test_vcf_long_deletion_expands_alignment_context_reverse_strand(self) -> None:
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='DLONGREV',
            protein='D',
            start=100,
            end=130,
            strand='-',
            codon_start=0,
            nt_sequence='A' * 30,
        )
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=30,
            strand='+',
            cigar='30M',
            cds_start=0,
        )
        alignment = build_feature_alignments('A' * 30, [match])['DLONGREV']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=120, ref='AAAAAAAAAA', alt='A'),
            feature_name='DLONGREV',
            codon_pos=3,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-affected'>-</span>") == 9

class TestAssignFeatureTracks:
    def test_while_loop_uses_plotted_segment_span(self) -> None:
        """_assign_feature_tracks uses plotted segment bounds, not full feature.end."""
        from respro.report.plots import _assign_feature_tracks

        # feature_a: genomic 10–100 but plotted segments only 10–20 and 30–40 → plot_end=40
        # feature_b: genomic 50–70, no segments → plot_start=50
        # New code: track_stops[0]=40 <= 50, so feature_b fits on track 0.
        # Old code would have used feature_a.end=100 > 50, forcing track 1.
        feature_a = FeatureRecord(
            id=1, reference_id=1, name='fa', protein='FA',
            start=10, end=100, strand='+', codon_start=0,
            segments=(
                FeatureSegment(segment_index=0, start=10, end=20),
                FeatureSegment(segment_index=1, start=30, end=40),
            ),
        )
        feature_b = FeatureRecord(
            id=2, reference_id=1, name='fb', protein='FB',
            start=50, end=70, strand='+', codon_start=0,
        )

        tracks = _assign_feature_tracks([feature_a, feature_b])

        assert tracks['fa'] == 0
        assert tracks['fb'] == 0

    def test_overlapping_features_are_placed_on_separate_tracks(self) -> None:
        """Features whose plotted spans overlap land on different tracks."""
        from respro.report.plots import _assign_feature_tracks

        feature_a = FeatureRecord(
            id=1, reference_id=1, name='fa', protein='FA',
            start=0, end=50, strand='+', codon_start=0,
        )
        feature_b = FeatureRecord(
            id=2, reference_id=1, name='fb', protein='FB',
            start=30, end=80, strand='+', codon_start=0,
        )

        tracks = _assign_feature_tracks([feature_a, feature_b])

        assert tracks['fa'] == 0
        assert tracks['fb'] == 1
class TestMatPeptidePlotLogic:
    """Tests for mat_peptide plot row layout and rendering helpers."""

    def _make_parent_cds(self) -> FeatureRecord:
        return FeatureRecord(
            id=10, reference_id=1, name='pol', protein='Pol',
            start=0, end=3000, strand='+', codon_start=0,
            nt_sequence='A' * 3000,
        )

    def _make_mat_peptide(
        self,
        name: str,
        start: int,
        end: int,
        parent_name: str = 'pol',
        feat_id: int = 20,
    ) -> FeatureRecord:
        return FeatureRecord(
            id=feat_id, reference_id=1, name=name, protein=name,
            start=start, end=end, strand='+', codon_start=0,
            feature_type='mat_peptide', parent_feature_name=parent_name,
            nt_sequence='A' * (end - start),
        )

    def _make_mat_peptide_result(self, feature_name: str) -> ProfilingResult:
        var = VariantCall(chrom='ref', pos=100, ref='A', alt='G', allele_freq=0.9, depth=500)
        rule = ResistanceRule(
            id=1, feature_name=feature_name, feature_id=20,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='K', mutation='E', phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name=feature_name, codon_pos=5,
            ref_codon='AAA', alt_codon='GAA', ref_aa='K', alt_aa='E',
            consequence='missense', af_bin='high', rule_matches=[rule],
        )
        return ProfilingResult(
            project_name='Test', organism='test',
            reference_name='ref', reference_length_nt=5000,
            sample_name='S1', vcf_name='test.vcf',
            total_variants=1, variants_in_cds=1, resistance_hits=1,
            annotations=[ann],
        )

    def _make_mat_peptide_result_for_features(self, feature_names: list[str]) -> ProfilingResult:
        annotations: list[AnnotatedVariant] = []
        for idx, feature_name in enumerate(feature_names, start=1):
            var = VariantCall(
                chrom='ref',
                pos=100 + idx,
                ref='A',
                alt='G',
                allele_freq=0.9,
                depth=500,
            )
            rule = ResistanceRule(
                id=idx,
                feature_name=feature_name,
                feature_id=20 + idx,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='ref',
                position=5,
                reference='K',
                mutation='E',
                phenotype='resistant',
            )
            annotations.append(
                AnnotatedVariant(
                    variant=var,
                    feature_name=feature_name,
                    codon_pos=5,
                    ref_codon='AAA',
                    alt_codon='GAA',
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                )
            )

        return ProfilingResult(
            project_name='Test',
            organism='test',
            reference_name='ref',
            reference_length_nt=5000,
            sample_name='S1',
            vcf_name='test.vcf',
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            resistance_hits=len(annotations),
            annotations=annotations,
        )

    def test_genome_overview_highlights_parent_cds_for_mat_peptide_selection(self) -> None:
        """When a mat_peptide has variants, genome overview highlights its parent CDS."""
        from respro.report.plots import _build_lollipop_figure

        parent = self._make_parent_cds()
        mp = self._make_mat_peptide('p2', 100, 500)
        result = self._make_mat_peptide_result('p2')

        fig = _build_lollipop_figure(result, [parent, mp])
        try:
            assert fig is not None
            overview_ax = list(fig.axes)[-1]
            label_texts = [t.get_text() for t in overview_ax.texts]
            assert 'pol' in label_texts
        finally:
            if fig:
                plt.close(fig)

    def test_same_parent_mat_peptides_render_parent_track_and_per_peptide_rows(self) -> None:
        """Same-parent mat_peptides share one parent CDS track and keep per-peptide rows."""
        from respro.report.plots import _build_lollipop_figure

        parent = self._make_parent_cds()
        mp1 = self._make_mat_peptide('p2', 0, 500, feat_id=20)
        mp2 = self._make_mat_peptide('p7', 500, 1000, feat_id=21)
        result = self._make_mat_peptide_result_for_features(['p2', 'p7'])

        fig = _build_lollipop_figure(result, [parent, mp1, mp2])
        try:
            assert fig is not None
            assert len(fig.axes) == 6
            titles = [ax.get_title(loc='left') or ax.get_title() for ax in fig.axes]
            assert titles.count('Genome overview') == 1
            assert titles.count('CDS overview') + titles.count('Mature Peptide overview') == 3
            assert 'p2 variants' in titles
            assert 'p7 variants' in titles

            parent_tracks = [ax for ax in fig.axes if ax.get_title(loc='left') == 'CDS overview' and ax.get_ylim()[1] > 1.2]
            assert len(parent_tracks) == 1
            parent_texts = [t.get_text() for t in parent_tracks[0].texts]
            assert 'p2' in parent_texts
            assert 'p7' in parent_texts
        finally:
            if fig:
                plt.close(fig)

    def test_different_parent_mat_peptides_render_two_parent_tracks(self) -> None:
        """Different-parent mat_peptides render one parent track per parent in a single figure."""
        from respro.report.plots import _build_lollipop_figure

        parent_a = self._make_parent_cds()
        parent_b = FeatureRecord(
            id=11, reference_id=1, name='env', protein='Env',
            start=3000, end=6000, strand='+', codon_start=0,
            nt_sequence='A' * 3000,
        )
        mp1 = self._make_mat_peptide('p2', 0, 500, parent_name='pol', feat_id=20)
        mp2 = self._make_mat_peptide('gp120', 3000, 4500, parent_name='env', feat_id=21)
        result = self._make_mat_peptide_result_for_features(['p2', 'gp120'])

        fig = _build_lollipop_figure(result, [parent_a, parent_b, mp1, mp2])
        try:
            assert fig is not None
            assert len(fig.axes) == 7
            titles = [ax.get_title(loc='left') or ax.get_title() for ax in fig.axes]
            assert titles.count('Genome overview') == 1
            assert titles.count('CDS overview') + titles.count('Mature Peptide overview') == 4
            assert 'p2 variants' in titles
            assert 'gp120 variants' in titles

            parent_tracks = [ax for ax in fig.axes if ax.get_title(loc='left') == 'CDS overview' and ax.get_ylim()[1] > 1.2]
            assert len(parent_tracks) == 2
            parent_track_texts = [[t.get_text() for t in ax.texts] for ax in parent_tracks]
            assert any('p2' in texts for texts in parent_track_texts)
            assert any('gp120' in texts for texts in parent_track_texts)
        finally:
            if fig:
                plt.close(fig)

    def test_cds_track_draws_dotted_separators_for_mat_peptide_overlays(self) -> None:
        """_draw_feature_track with mat_peptide_overlays adds vlines at mat_peptide boundaries."""
        from respro.report.plots import _draw_feature_track

        parent = self._make_parent_cds()
        mp1 = self._make_mat_peptide('p2', 0, 500, feat_id=20)
        mp2 = self._make_mat_peptide('p7', 500, 1000, feat_id=21)

        fig, ax = plt.subplots()
        try:
            _draw_feature_track(ax, parent, mat_peptide_overlays=[mp1, mp2])
            # Boundaries: mp1.start+1=1, mp2.start+1=501, mp2.end+1=1001 → 3 vlines
            vline_xs = set()
            for collection in ax.collections:
                if not hasattr(collection, 'get_segments'):
                    continue
                for segment in collection.get_segments():
                    if len(segment) != 2:
                        continue
                    x0, y0 = segment[0]
                    x1, y1 = segment[1]
                    if x0 == x1 and y0 != y1:
                        vline_xs.add(int(round(float(x0))))
            assert len(vline_xs) == 3
            assert 1 in vline_xs
            assert 501 in vline_xs
            assert 1001 in vline_xs
        finally:
            plt.close(fig)

    def test_cds_track_labels_mat_peptide_overlays_above_track(self) -> None:
        """_draw_feature_track writes text labels for each mat_peptide overlay."""
        from respro.report.plots import _draw_feature_track

        parent = self._make_parent_cds()
        mp1 = self._make_mat_peptide('p2', 0, 500, feat_id=20)
        mp2 = self._make_mat_peptide('p7', 500, 1000, feat_id=21)

        fig, ax = plt.subplots()
        try:
            _draw_feature_track(ax, parent, mat_peptide_overlays=[mp1, mp2])
            text_labels = [t.get_text() for t in ax.texts]
            assert 'p2' in text_labels
            assert 'p7' in text_labels
        finally:
            plt.close(fig)

    def test_cds_track_labels_use_rule_feature_names(self) -> None:
        """Labels use the overlay feature name for deterministic plot annotation text."""
        from respro.report.plots import _draw_feature_track

        parent = self._make_parent_cds()
        mp = self._make_mat_peptide('p2', 0, 500, feat_id=20)

        fig, ax = plt.subplots()
        try:
            _draw_feature_track(
                ax, parent, mat_peptide_overlays=[mp], rule_feature_names={'P2'},
            )
            text_labels = [t.get_text() for t in ax.texts]
            assert 'p2' in text_labels
        finally:
            plt.close(fig)

    def test_mat_peptide_rows_include_feature_track_lollipop_and_genome(self) -> None:
        """Single mat_peptide layout yields lollipop row, two track rows, and genome row."""
        from respro.report.plots import _build_lollipop_figure

        parent = self._make_parent_cds()
        mp = self._make_mat_peptide('p2', 100, 500)
        result = self._make_mat_peptide_result('p2')

        fig = _build_lollipop_figure(result, [parent, mp])
        try:
            assert fig is not None
            assert len(fig.axes) == 4
            titles = [ax.get_title(loc='left') or ax.get_title() for ax in fig.axes]
            assert titles.count('Genome overview') == 1
            assert titles.count('CDS overview') + titles.count('Mature Peptide overview') == 2
            assert titles.count('p2 variants') == 1
        finally:
            if fig:
                plt.close(fig)

    def test_mat_peptide_lollipop_title_uses_feature_variants_and_parent_labels_peptide(self) -> None:
        """Lollipop title follows '<feature> variants' while parent CDS track keeps peptide labels."""
        from respro.report.plots import _build_lollipop_figure

        parent = self._make_parent_cds()
        mp = self._make_mat_peptide('p2', 100, 500)
        result = self._make_mat_peptide_result('p2')

        fig = _build_lollipop_figure(result, [parent, mp])
        try:
            assert fig is not None
            lollipop_ax = fig.axes[0]
            title_text = lollipop_ax.get_title(loc='left') or lollipop_ax.get_title()
            assert title_text == 'p2 variants'

            parent_track = next(
                ax
                for ax in fig.axes
                if ax.get_title(loc='left') == 'CDS overview' and ax.get_ylim()[1] > 1.2
            )
            parent_track_labels = [t.get_text() for t in parent_track.texts]
            assert 'p2' in parent_track_labels
        finally:
            if fig:
                plt.close(fig)
class TestMatPeptideDisplayName:
    """Tests for mat_peptide display name resolution in reports."""

    def _make_mat_peptide_feature(
        self,
        name: str = 'pol_mat_peptide_1',
        protein: str = 'Protease',
    ) -> FeatureRecord:
        return FeatureRecord(
            id=1, reference_id=1, name=name, protein=protein,
            start=0, end=300, strand='+', codon_start=0,
            feature_type='mat_peptide',
            nt_sequence='A' * 300,
        )

    def _make_cds_feature(
        self,
        name: str = 'gag',
        protein: str = 'Group-specific antigen',
    ) -> FeatureRecord:
        return FeatureRecord(
            id=2, reference_id=1, name=name, protein=protein,
            start=0, end=300, strand='+', codon_start=0,
            feature_type='CDS',
            nt_sequence='A' * 300,
        )

    def _make_result_for_feature(self, feature_name: str) -> ProfilingResult:
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=500)
        rule = ResistanceRule(
            id=1, feature_name=feature_name, feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=1, reference='K', mutation='E', phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=var, feature_name=feature_name, codon_pos=1,
            ref_codon='AAA', alt_codon='GAA', ref_aa='K', alt_aa='E',
            consequence='missense', af_bin='high', rule_matches=[rule],
        )
        return ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1,
            annotations=[ann],
        )

    # -- FeatureRecord.display_name unit tests --

    def test_display_name_mat_peptide_with_protein(self) -> None:
        feature = self._make_mat_peptide_feature(name='pol_mat_peptide_1', protein='Protease')
        assert feature.display_name == 'Protease'

    def test_display_name_mat_peptide_without_protein(self) -> None:
        feature = FeatureRecord(
            id=1, reference_id=1, name='pol_mat_peptide_1', protein='',
            start=0, end=300, strand='+', codon_start=0,
            feature_type='mat_peptide',
        )
        assert feature.display_name == 'pol_mat_peptide_1'

    def test_display_name_cds_with_protein_returns_name(self) -> None:
        feature = self._make_cds_feature(name='gag', protein='Group-specific antigen')
        assert feature.display_name == 'gag'

    # -- build_report_context integration tests --

    def test_db_hit_rows_use_protein_for_mat_peptide(self) -> None:
        feature = self._make_mat_peptide_feature(name='pol_mat_peptide_1', protein='Protease')
        result = self._make_result_for_feature('pol_mat_peptide_1')
        ctx = build_report_context(result, features=[feature])
        assert ctx['database_hits']['rows'][0]['mutation_groups'][0]['feature'] == 'Protease'

    def test_cds_rows_use_protein_for_mat_peptide(self) -> None:
        feature = self._make_mat_peptide_feature(name='pol_mat_peptide_1', protein='Protease')
        result = self._make_result_for_feature('pol_mat_peptide_1')
        ctx = build_report_context(result, features=[feature])
        assert ctx['all_mutations']['rows'][0]['feature'] == 'Protease'

    def test_db_hit_rows_use_name_for_cds(self) -> None:
        feature = self._make_cds_feature(name='gag', protein='Group-specific antigen')
        result = self._make_result_for_feature('gag')
        ctx = build_report_context(result, features=[feature])
        assert ctx['database_hits']['rows'][0]['mutation_groups'][0]['feature'] == 'gag'


class TestReportHardening:
    """Critical report quality and data integrity tests."""

    def test_html_report_structure_completeness(self) -> None:
        result = _make_result()
        html = render_html(result)

        assert '<header class="report-header">' in html
        assert 'id="tab-summary"' in html
        assert 'id="tab-database-hits"' in html
        assert 'id="tab-all-mutations"' in html
        assert 'class="db-hit-table"' in html
        assert 'class="mutation-table"' in html
        assert 'id="plot-modal"' in html

    def test_json_export_structure_and_schema(self, tmp_path) -> None:
        result = _make_result()
        output_html_path = tmp_path / 'sample.report.html'
        outputs = export_results(
            result,
            tmp_path,
            extra_export_formats={'json'},
            output_html_path=output_html_path,
        )

        json_path = outputs['json']
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        assert set(payload.keys()) == {
            'run',
            'variant_result',
            'coverage_gap',
            'formula_rule_hit',
            'sample_classification',
        }
        assert isinstance(payload['run'], dict)
        assert isinstance(payload['variant_result'], list)
        assert isinstance(payload['coverage_gap'], list)
        assert isinstance(payload['formula_rule_hit'], list)
        assert isinstance(payload['sample_classification'], list)

    def test_report_consistency_across_exports(self, tmp_path) -> None:
        result = _make_result()
        output_html_path = tmp_path / 'sample.report.html'
        outputs = export_results(
            result,
            tmp_path,
            extra_export_formats={'json'},
            output_html_path=output_html_path,
        )

        html = output_html_path.read_text(encoding='utf-8')
        payload = json.loads(outputs['json'].read_text(encoding='utf-8'))

        assert payload['run']['sample_name'] == result.sample_name
        assert payload['run']['reference_name'] == result.reference_name
        assert payload['run']['sample_name'] in html
        assert payload['run']['reference_name'] in html
        assert str(len(payload['variant_result'])) in html

    def test_report_handles_empty_results(self) -> None:
        result = ProfilingResult(
            project_name='Test',
            reference_name='ref',
            reference_length_nt=1000,
            sample_name='empty',
            vcf_name='empty.vcf',
            total_variants=0,
            variants_in_cds=0,
            resistance_hits=0,
            annotations=[],
        )

        html = render_html(result)
        assert 'No database hits found for this sample.' in html
        assert 'No similarity matches found for this sample.' in html

    def test_report_metrics_display_based_on_available_data(self) -> None:
        variant = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500)

        phenotype_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugPhenotype',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )
        ic50_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugIc50',
            drug_id=2,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            ic50='15',
        )
        clinical_rule = ResistanceRule(
            id=3,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugClinical',
            drug_id=3,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            clinical_phenotype='intermediate',
        )

        def _result_for(rule: ResistanceRule) -> ProfilingResult:
            ann = AnnotatedVariant(
                variant=variant,
                feature_name='gag',
                codon_pos=2,
                ref_aa='K',
                alt_aa='E',
                consequence='missense',
                af_bin='high',
                rule_matches=[rule],
            )
            return ProfilingResult(
                project_name='T',
                reference_name='ref',
                reference_length_nt=1000,
                total_variants=1,
                variants_in_cds=1,
                resistance_hits=1,
                annotations=[ann],
            )

        phenotype_context = build_report_context(_result_for(phenotype_rule))
        assert phenotype_context['database_hits']['has_phenotype_metrics'] is True
        assert phenotype_context['database_hits']['has_ic50_metrics'] is False
        assert phenotype_context['database_hits']['has_clinical_phenotype_metrics'] is False

        ic50_context = build_report_context(_result_for(ic50_rule))
        assert ic50_context['database_hits']['has_ic50_metrics'] is True

        clinical_context = build_report_context(_result_for(clinical_rule))
        assert clinical_context['database_hits']['has_clinical_phenotype_metrics'] is True

        html_ic50 = render_html(_result_for(ic50_rule))
        assert 'IC50 / Fold IC50' in html_ic50

        html_clinical = render_html(_result_for(clinical_rule))
        assert 'Phenotype / Clinical phenotype' in html_clinical


class TestPdfDrugRows:
    """Tests for _build_pdf_drug_rows field mapping."""

    def test_pdf_drug_rows_include_method_badge_classes_single_method(self) -> None:
        """Single-method rows must include method_badge_classes_by_method with normalized classes."""
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
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
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_table = ctx['summary']['drug_table']
        pdf_rows = _build_pdf_drug_rows(drug_table)

        assert len(pdf_rows) >= 1
        row = pdf_rows[0]
        # method_badge_classes_by_method must be present and use PDF CSS class format
        assert 'method_badge_classes_by_method' in row
        badge_classes = row['method_badge_classes_by_method']
        assert 'by_phenotype' in badge_classes
        # Badge class should be normalized to PDF format (is-* not phenotype--*)
        assert badge_classes['by_phenotype'] == 'is-resistant'
        # ic50_display and fold_ic50_display must be present (em dash when no values)
        assert 'ic50_display' in row
        assert 'fold_ic50_display' in row
        assert row['ic50_display'] == '\u2014'
        assert row['fold_ic50_display'] == '\u2014'

    def test_pdf_drug_rows_include_value_fields_with_ic50(self) -> None:
        """Rows with IC50 data must include ic50_display with the highest value."""
        low_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='unknown',
            ic50='6.0',
        )
        high_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=3,
            reference='A',
            mutation='V',
            phenotype='unknown',
            ic50='12.0',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=2,
            variants_in_cds=2,
            resistance_hits=2,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[low_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.9, depth=180),
                    feature_name='gag',
                    codon_pos=3,
                    ref_aa='A',
                    alt_aa='V',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[high_rule],
                ),
            ],
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
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_table = ctx['summary']['drug_table']
        pdf_rows = _build_pdf_drug_rows(drug_table)

        row = pdf_rows[0]
        assert row['ic50_display'] == '12'
        assert row['fold_ic50_display'] == '\u2014'
        # method_labels must include value_header and value_field for by_ic50
        ic50_label = next(
            ml for ml in drug_table['method_labels'] if ml['method'] == 'by_ic50'
        )
        assert ic50_label['value_header'] == 'Highest IC50'
        assert ic50_label['value_field'] == 'ic50_display'

    def test_pdf_drug_rows_multi_method_badge_classes(self) -> None:
        """Multi-method rows must include method_badge_classes_by_method for all methods."""
        rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='ref',
            position=2,
            reference='K',
            mutation='E',
            phenotype='resistant',
            ic50='8.0',
            fold_ic50='6.0',
        )
        result = ProfilingResult(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
                    feature_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[rule],
                ),
            ],
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
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                }),
            ),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'drug_interpretation',
                json.dumps({
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 5.0},
                }),
            ),
        )
        conn.commit()

        ctx = build_report_context(result, project_conn=conn)
        drug_table = ctx['summary']['drug_table']
        pdf_rows = _build_pdf_drug_rows(drug_table)

        row = pdf_rows[0]
        assert 'method_badge_classes_by_method' in row
        badge_classes = row['method_badge_classes_by_method']
        # Both methods should be present
        assert 'by_phenotype' in badge_classes
        assert 'by_ic50' in badge_classes
        assert badge_classes['by_phenotype'] == 'is-resistant'
        # by_ic50 sees IC50=8.0 which is < 10.0 resistant threshold but >= 5.0 intermediate
        assert badge_classes['by_ic50'] == 'is-intermediate'
        # Final assessment badge class should also be present and normalized
        assert row['assessment_badge_class'] == 'is-resistant'

