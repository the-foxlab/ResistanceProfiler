"""
Tests for report output generation.
"""

import sqlite3

import matplotlib.pyplot as plt
from Bio.Seq import Seq

from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    GeneMatch,
    GeneRecord,
    GeneSegment,
    ProfilingResult,
    Publication,
    ResistanceRule,
    VariantCall,
)
from respro.report.alignment_visualization import build_alignment_html, build_gene_alignments
from respro.report.html import (
    _build_potential_effects_rows,
    _load_gene_cards,
    build_report_context,
    render_html,
)
from respro.report.non_html_exports import _build_pdf_mutation_entries


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
    gene = GeneRecord(
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
        gene_matches=[
            GeneMatch(
                gene=gene,
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



class TestBuildReportContext:
    def test_db_hit_positions_and_rules_in_summary(self) -> None:
        # _make_result has 1 annotation with 1 rule → 1 position, 1 rule
        r = _make_result()
        ctx = build_report_context(r)
        assert ctx['summary']['database_hits'] == 1
        assert ctx['summary']['db_hit_rules'] == 1

    def test_multiple_rules_per_position(self) -> None:
        # Two rules on the same variant → 1 position, 2 rules
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule_a = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E', phenotype='resistant',
        )
        rule_b = ResistanceRule(
            id=2, gene_name='gag', gene_id=1,
            drug_name='DrugB', drug_id=2, reference_identifier='ref',
            position=2, reference='K', mutation='E', phenotype='sensitive',
        )
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=2,
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
        assert ctx['summary']['database_hits'] == 1    # 1 position
        assert ctx['summary']['db_hit_rules'] == 2     # 2 rules matched

    def test_publication_citations_deduplicate_without_publication_ids(self) -> None:
        r = _make_result()
        r.annotations[0].rule_matches[0].publications = [
            Publication(id=0, doi='', title='', pubmed_id='11111', raw_input='PMID:11111'),
            Publication(id=0, doi='', title='', pubmed_id='11111', raw_input='PMID:11111'),
            Publication(id=0, doi='', title='', pubmed_id='22222', raw_input='PMID:22222'),
        ]

        ctx = build_report_context(r)

        assert len(ctx['bibliography']) == 2
        assert ctx['db_hit_rows'][0]['pub_citations'] == [1, 2]

    def test_stat_note_rendered_in_html(self) -> None:
        r = _make_result()
        html = render_html(r)
        # Tile must show both counts
        assert '1 position' in html
        assert '1 rule' in html

    def test_db_hits_phenotype_prefers_phenotype_over_clinical(self) -> None:
        # Rule with phenotype='resistant', clinical_phenotype='intermediate'
        # → should count as resistant only, NOT as both resistant and intermediate.
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='resistant', clinical_phenotype='intermediate',
        )
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule],
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1, annotations=[ann],
        )
        ctx = build_report_context(r)
        assert ctx['summary']['resistant_hits'] == 1
        assert ctx['summary']['intermediate_hits'] == 0  # not double-counted

    def test_db_hits_falls_back_to_clinical_phenotype_when_phenotype_unknown(self) -> None:
        # Rule with phenotype='unknown', clinical_phenotype='resistant'
        # → should count as resistant via fallback.
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='unknown', clinical_phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule],
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=1, annotations=[ann],
        )
        ctx = build_report_context(r)
        assert ctx['summary']['resistant_hits'] == 1
        assert ctx['summary']['intermediate_hits'] == 0

    def test_similarity_hits_counts_unique_positions(self) -> None:
        # Two rules at the same (gene, codon_pos) for different drugs
        # → similarity_hits = 1 unique position, similarity_rules = 2
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule_a = ResistanceRule(
            id=10, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I', phenotype='resistant',
        )
        rule_b = ResistanceRule(
            id=11, gene_name='gag', gene_id=1,
            drug_name='DrugB', drug_id=2, reference_identifier='ref',
            position=5, reference='L', mutation='I', phenotype='intermediate',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        ctx = build_report_context(r, rules=[rule_a, rule_b])
        assert ctx['summary']['similarity_hits'] == 1      # 1 unique position
        assert ctx['summary']['similarity_rules'] == 1     # 1 unique observed mutation (V), matched by 2 drugs

    def test_similarity_phenotype_prefers_phenotype_over_clinical(self) -> None:
        # Similarity row with phenotype='resistant', clinical_phenotype='intermediate'
        # → counted as resistant only.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='resistant', clinical_phenotype='intermediate',
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        ctx = build_report_context(r, rules=[rule])
        assert ctx['summary']['similarity_resistant'] == 1
        assert ctx['summary']['similarity_intermediate'] == 0  # not double-counted

    def test_similarity_clinical_phenotype_column_rendered_when_available(self) -> None:
        # When a similarity rule has a non-unknown clinical_phenotype, the
        # 'Clinical phenotype' column must appear in the rendered HTML — analogous
        # to the 'Mutations in database' section.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, gene_name='gag', gene_id=1,
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
        # The clinical badge must appear inside the similarity section
        sim_start = html.find('section-similarity')
        assert sim_start != -1
        assert 'badge-resistant' in html[sim_start:]

    def test_similarity_clinical_phenotype_column_hidden_when_all_unknown(self) -> None:
        # When all rules across ALL sections have clinical_phenotype='unknown' the column
        # must be omitted everywhere — same 'if available' behaviour as 'Mutations in database'.
        var = VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100)
        ann = AnnotatedVariant(
            variant=var, gene_name='gag', codon_pos=5,
            ref_aa='L', alt_aa='V', consequence='missense', af_bin='high',
        )
        rule = ResistanceRule(
            id=10, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='resistant',  # clinical_phenotype defaults to 'unknown'
        )
        r = ProfilingResult(
            project_name='T', reference_name='ref', reference_length_nt=1000,
            total_variants=1, variants_in_cds=1, resistance_hits=0, annotations=[ann],
        )
        html = render_html(r, rules=[rule])
        assert 'Clinical phenotype' not in html


class TestPdfExports:
    def test_pdf_mutation_entries_include_effect_badge_class(self) -> None:
        result = _make_result()
        report_context = build_report_context(result)

        groups = _build_pdf_mutation_entries(result, report_context)

        assert groups
        assert groups[0]['mutations']
        assert groups[0]['mutations'][0]['effect_badge_class'] == 'badge-missense'

    def test_pdf_direct_db_hits_do_not_embed_similarity(self) -> None:
        result = _make_result()
        report_context = build_report_context(result)

        groups = _build_pdf_mutation_entries(result, report_context)

        first_hit = groups[0]['mutations'][0]['db_hits'][0]
        assert 'similarity' not in first_hit
        assert 'similarity_badge_class' not in first_hit

    def test_similarity_clinical_phenotype_shown_when_db_hits_have_it(self) -> None:
        # If db_hits carry a non-unknown clinical_phenotype, the similarity section must
        # ALSO show the Clinical phenotype column even if those similarity rules have 'unknown'.
        var_hit = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=300)
        rule_hit = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=2, reference='K', mutation='E',
            phenotype='resistant', clinical_phenotype='resistant',
        )
        ann_hit = AnnotatedVariant(
            variant=var_hit, gene_name='gag', codon_pos=2,
            ref_aa='K', alt_aa='E', consequence='missense', af_bin='high',
            rule_matches=[rule_hit],
        )
        # Similarity hit at a different position — rule has clinical_phenotype='unknown'
        var_sim = VariantCall(chrom='ref', pos=15, ref='C', alt='T', allele_freq=0.5, depth=100)
        rule_sim = ResistanceRule(
            id=2, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='ref',
            position=5, reference='L', mutation='I',
            phenotype='resistant',  # clinical_phenotype='unknown' (default)
        )
        ann_sim = AnnotatedVariant(
            variant=var_sim, gene_name='gag', codon_pos=5,
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

    def test_build_report_context_tracks_unassessed_rule_positions(self):
        r = _make_result()
        r.coverage_gaps = [CoverageGap(gene_name='gag', codon_start=2, codon_end=2)]
        rules = [
            ResistanceRule(
                id=2,
                gene_name='gag',
                gene_id=1,
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
                gene_name='gag',
                gene_id=1,
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
        assert context['summary']['rule_positions_total'] == 2
        assert context['summary']['unassessed_rule_positions'] == 1

    def test_render_html_shows_unassessed_rule_tile_without_detail_table(self):
        r = _make_result()
        r.coverage_gaps = [CoverageGap(gene_name='gag', codon_start=2, codon_end=2)]
        rules = [
            ResistanceRule(
                id=2,
                gene_name='gag',
                gene_id=1,
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
                gene_name='gag',
                gene_id=1,
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
        assert 'Unassessed rule positions' in html
        assert 'of 2 total positions (missing coverage)' in html
        assert 'id=\'section-unassessed\'' not in html

    def test_build_report_context_reports_rule_positions_for_vcf_mode_without_gaps(self):
        variant = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=200)
        ann = AnnotatedVariant(
            variant=variant,
            gene_name='gag',
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
                gene_name='gag',
                gene_id=1,
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
        assert context['summary']['rule_positions_total'] == 1
        assert context['summary']['unassessed_rule_positions'] == 0

    def test_build_report_context_sorts_db_hits_by_drug_then_resistance_then_ic50(self):
        resistant_rule = ResistanceRule(
            id=21,
            gene_name='gag',
            gene_id=1,
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
            gene_name='gag',
            gene_id=1,
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
            gene_name='gag',
            gene_id=1,
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
            gene_name='gag',
            gene_id=1,
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
                    gene_name='gag',
                    codon_pos=2,
                    ref_aa='K',
                    alt_aa='E',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[resistant_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.9, depth=180),
                    gene_name='gag',
                    codon_pos=3,
                    ref_aa='A',
                    alt_aa='V',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[intermediate_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=9, ref='C', alt='T', allele_freq=0.85, depth=170),
                    gene_name='gag',
                    codon_pos=4,
                    ref_aa='L',
                    alt_aa='I',
                    consequence='missense',
                    af_bin='high',
                    rule_matches=[high_ic50_unknown_rule],
                ),
                AnnotatedVariant(
                    variant=VariantCall(chrom='ref', pos=12, ref='C', alt='A', allele_freq=0.8, depth=160),
                    gene_name='gag',
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
        rows = context['db_hit_rows']

        assert [row['drug'] for row in rows] == ['DrugA', 'DrugA', 'DrugB', 'DrugB']
        assert rows[0]['phenotype'] == 'resistant'
        assert rows[1]['phenotype'] == 'intermediate'
        assert rows[2]['ic50'] == '25'
        assert rows[3]['ic50'] == '5'

    def test_render_html_includes_drug_badges(self) -> None:
        r = _make_result()
        html = render_html(r)
        assert 'class=\'badge drug-badge\'' in html

    def test_render_html_includes_table_filter_controls_js(self) -> None:
        r = _make_result()
        html = render_html(r)
        assert 'Filter:' in html
        assert 'installTableFilterControls' in html

    def test_render_html_includes_expandable_alignment_rows(self) -> None:
        r = _make_result()

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE gene ('
            'name TEXT, start INTEGER, end INTEGER, strand TEXT, codon_start INTEGER, nt_sequence TEXT, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO gene (name, start, end, strand, codon_start, nt_sequence, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('gag', 0, 12, '+', 0, 'ATGAAAGCTTAA', 1),
        )
        conn.commit()

        html = render_html(r, project_conn=conn)

        assert 'expandable-row' in html
        assert 'detail-row' in html
        assert 'Pseudo alignment' in html
        assert 'aln-block' in html
        assert 'aln-affected' in html
        assert "aln-cell aln-mutation" not in html
        assert 'Coding orientation:' in html

    def test_build_report_context_includes_summary_text(self) -> None:
        hit_rule = ResistanceRule(
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
            clinical_phenotype='resistant',
            fold_ic50='>10x',
        )
        hit_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.95, depth=200),
            gene_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
            rule_matches=[hit_rule],
        )

        sim_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=6, ref='A', alt='T', allele_freq=0.5, depth=100),
            gene_name='gag',
            codon_pos=5,
            ref_aa='L',
            alt_aa='V',
            consequence='missense',
            af_bin='high',
        )
        high_impact_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=10, ref='A', alt='AG', allele_freq=0.8, depth=120),
            gene_name='gag',
            codon_pos=6,
            ref_aa='P',
            alt_aa='PfsX',
            consequence='frameshift',
            af_bin='high',
        )
        sim_rule = ResistanceRule(
            id=2,
            gene_name='gag',
            gene_id=1,
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
        text = context['summary_text_en']
        assert 'database hit' in text
        assert 'DrugA' in text
        assert 'biochemical similarity' in text
        assert 'high-impact variant' in text
        assert 'Human alphaherpesvirus 1' in text
        assert 'GAG' in text

    def test_build_report_context_mentions_coverage_gaps(self) -> None:
        hit_ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=10, ref='A', alt='T', allele_freq=0.8, depth=100),
            gene_name='pol',
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
                CoverageGap(gene_name='gag', codon_start=5, codon_end=10),
                CoverageGap(gene_name='rt', codon_start=20, codon_end=25),
            ],
        )

        context = build_report_context(result)
        text = context['summary_text_en']
        assert 'coverage gaps' in text
        assert 'could not be fully assessed' in text
        assert 'GAG' in text
        assert 'RT' in text

    def test_render_html_includes_summary_translation_controls(self) -> None:
        html = render_html(_make_result())
        assert 'Interpretation summary' in html
        assert 'data-lang=\'en\'' in html
        assert 'data-lang=\'de\'' in html
        assert 'data-lang=\'fr\'' in html
        assert 'data-lang=\'es\'' in html
        assert 'English' in html
        assert 'Google Translate' in html

    def test_render_html_highlights_nt_and_aa_changed_segments(self) -> None:
        r = _make_result()
        html = render_html(r)

        assert 'A4<u><strong>G</strong></u>' in html
        assert 'K3<u><strong>E</strong></u>' in html

    def test_render_html_highlights_insertion_segments_in_table(self) -> None:
        var = VariantCall(chrom='ref', pos=3, ref='C', alt='CG', allele_freq=0.9, depth=300)
        ann = AnnotatedVariant(
            variant=var,
            gene_name='gag',
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
        assert 'C4C<u><strong>G</strong></u>' in html
        assert 'K3K<u><strong>G</strong></u>' in html

    def test_render_html_highlights_frameshift_indel_segments_in_table(self) -> None:
        ins = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='A', alt='AG', allele_freq=0.9, depth=300),
            gene_name='gag',
            codon_pos=2,
            ref_aa='K',
            alt_aa='KfsX',
            consequence='frameshift',
            af_bin='high',
        )
        dele = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=6, ref='AC', alt='A', allele_freq=0.8, depth=250),
            gene_name='gag',
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
        assert 'A4A<u><strong>G</strong></u>' in html
        assert 'A<u><strong>C</strong></u>7A' in html

    def test_render_html_fasta_frameshift_uses_indel_nt_not_fsx_token(self) -> None:
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=47663, ref='GG', alt='G', allele_freq=0.8, depth=250),
            gene_name='UL23',
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
        assert 'G<u><strong>G</strong></u>47664G' in html

    def test_render_html_uses_alignment_title_for_fasta_mode(self) -> None:
        r = _make_result()
        r.annotations[0].is_fasta_mode = True

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE reference (id INTEGER, name TEXT)')
        conn.execute(
            'CREATE TABLE gene ('
            'name TEXT, start INTEGER, end INTEGER, strand TEXT, codon_start INTEGER, nt_sequence TEXT, reference_id INTEGER'
            ')'
        )
        conn.execute('INSERT INTO reference (id, name) VALUES (?, ?)', (1, 'ref'))
        conn.execute(
            'INSERT INTO gene (name, start, end, strand, codon_start, nt_sequence, reference_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('gag', 0, 12, '+', 0, 'ATGAAAGCTTAA', 1),
        )
        conn.commit()

        html = render_html(r, project_conn=conn)
        assert 'Alignment' in html

    def test_lollipop_svg_contains_non_covered_legend(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        r.coverage_gaps = [CoverageGap(gene_name='gag', codon_start=2, codon_end=2)]
        genes = [
            GeneRecord(
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

        svg = render_lollipop_plot_bytes(r, genes, fmt='svg')
        assert svg is not None
        assert b'non covered' in svg
        assert b'#6b7280' in svg
        assert b'opacity: 0.12' in svg

    def test_lollipop_svg_omits_non_covered_legend_without_coverage_gaps(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        r.coverage_gaps = []
        genes = [
            GeneRecord(
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

        svg = render_lollipop_plot_bytes(r, genes, fmt='svg')
        assert svg is not None
        assert b'non covered' not in svg

    def test_lollipop_svg_omits_intron_legend_without_split_gene(self):
        from respro.report.plots import render_lollipop_plot_bytes

        r = _make_result()
        genes = [
            GeneRecord(
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

        svg = render_lollipop_plot_bytes(r, genes, fmt='svg')
        assert svg is not None
        assert b'Intron (non-coding)' not in svg


class TestSplitGenePlotRendering:
    def test_genome_overview_draws_one_block_per_segment_in_genomic_order(self) -> None:
        from respro.report.plots import _draw_genome_overview

        gene = GeneRecord(
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
                GeneSegment(segment_index=0, start=30, end=36),
                GeneSegment(segment_index=1, start=10, end=16),
            ),
        )

        fig, ax = plt.subplots()
        try:
            _draw_genome_overview(ax, [gene], {gene.name}, reference_length_nt=50)

            rects = ax.patches
            assert len(rects) == 2
            assert [rect.get_x() for rect in rects] == [11, 31]
            assert [rect.get_width() for rect in rects] == [6, 6]
            assert [text.get_text() for text in ax.texts] == ['UL30']
        finally:
            plt.close(fig)

    def test_gene_track_draws_one_block_per_segment_in_genomic_order(self) -> None:
        from respro.report.plots import _draw_gene_track

        gene = GeneRecord(
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
                GeneSegment(segment_index=0, start=30, end=36),
                GeneSegment(segment_index=1, start=10, end=16),
            ),
        )

        fig, ax = plt.subplots()
        try:
            _draw_gene_track(ax, gene)

            rects = ax.patches
            assert len(rects) == 2
            # Full gene box (start=10 → x=11, width=26) then intron gap (16–30 → x=17, width=14)
            assert [rect.get_x() for rect in rects] == [11, 17]
            assert [rect.get_width() for rect in rects] == [26, 14]
            assert [text.get_text() for text in ax.texts] == ['← UL30 ←']
        finally:
            plt.close(fig)


class TestAlignmentVisualization:
    def test_fasta_alignment_renders_match_bars_from_aligned_query(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_gene_alignments('AAATCCGGG', [match])['FASTA']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CCC', alt='TCC'),
            gene_name='FASTA',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_gene_alignments('AAATCCGGG', [match])['FASTASNP']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CCC', alt='TCC'),
            gene_name='FASTASNP',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_gene_alignments('AAATCCGGG', [match])['FASTASYN']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='T'),
            gene_name='FASTASYN',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_gene_alignments('AAACCCGGG', [match])['VCF']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=103, ref='C', alt='T'),
            gene_name='VCF',
            codon_pos=1,
            consequence='missense',
            af_bin='high',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-match-cell'>|</span>") == 8

    def test_highlight_uses_real_cigar_alignment_window(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=15,
            strand='+',
            cigar='8M1D6M',
            cds_start=0,
        )
        alignments = build_gene_alignments('ATGACCCC AAGGCC'.replace(' ', ''), [match])

        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=7, ref='CC', alt='C'),
            gene_name='UL23',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=18,
            strand='+',
            cigar='6M1D12M',
            cds_start=0,
        )
        alignment = build_gene_alignments('TAGCGTGGCATTTTCTG', [match])['UL23']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=5, ref='TG', alt='T'),
            gene_name='UL23',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=13,
            strand='+',
            cigar='4M1I8M',
            cds_start=0,
        )
        alignment = build_gene_alignments('ATGCGCCCAAAGGG', [match])['UL23INS']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='CG'),
            gene_name='UL23INS',
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
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=len(query),
            strand='-',
            cigar='3M1D5M',
            cds_start=0,
        )
        alignment = build_gene_alignments(query, [match])['REVDEL']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='CC', alt='C'),
            gene_name='REVDEL',
            codon_pos=1,
            consequence='frameshift',
            ref_aa='G',
            alt_aa='GfsX',
            is_fasta_mode=True,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count('aln-cell aln-affected') == 1
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_reverse_gene_codon_spacing_follows_cds_direction(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignments = build_gene_alignments('AAACCCGGG', [match])

        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='C', alt='T'),
            gene_name='REV',
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


class TestCoverageGapPlotBounds:
    def test_reverse_strand_gap_bounds_include_full_terminal_codons(self) -> None:
        from respro.report.plots import _coverage_gap_nt_bounds

        gene = GeneRecord(
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
        gap = CoverageGap(gene_name='REV', codon_start=0, codon_end=2)

        start, end = _coverage_gap_nt_bounds(gene, gap)
        assert (start, end) == (121, 130)

    def test_vcf_snp_overlay_switches_base_and_highlights_anchor(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=9,
            strand='+',
            cigar='9M',
            cds_start=0,
        )
        alignment = build_gene_alignments('AAACCCGGG', [match])['G']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=103, ref='C', alt='T'),
            gene_name='G',
            codon_pos=1,
            consequence='missense',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert "<span class='aln-label'>Query</span>" in html
        assert "<span class='aln-cell aln-affected'>T</span>" in html

    def test_vcf_deletion_overlay_places_gap_after_anchor(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_gene_alignments('ATGCCCAAAGGG', [match])['D']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CC', alt='C'),
            gene_name='D',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert "<span class='aln-cell aln-affected'>-</span>" in html

    def test_vcf_deletion_does_not_highlight_anchor_cell(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_gene_alignments('ATGCCCAAAGGG', [match])['DANCHOR']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='CC', alt='C'),
            gene_name='DANCHOR',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        # One affected alignment column (deleted base) rendered on Query line only.
        assert html.count('aln-cell aln-affected') == 1

    def test_vcf_insertion_does_not_highlight_anchor_cell(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=12,
            strand='+',
            cigar='12M',
            cds_start=0,
        )
        alignment = build_gene_alignments('ATGCCCAAAGGG', [match])['IANCHOR']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='C', alt='CG'),
            gene_name='IANCHOR',
            codon_pos=1,
            consequence='insertion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        # One affected alignment column (inserted base) rendered on Query line only.
        assert html.count('aln-cell aln-affected') == 1

    def test_vcf_long_deletion_expands_alignment_context(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=30,
            strand='+',
            cigar='30M',
            cds_start=0,
        )
        alignment = build_gene_alignments('A' * 30, [match])['DLONG']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=3, ref='AAAAAAAAAA', alt='A'),
            gene_name='DLONG',
            codon_pos=1,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-affected'>-</span>") == 9

    def test_vcf_long_deletion_expands_alignment_context_reverse_strand(self) -> None:
        gene = GeneRecord(
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
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=30,
            strand='+',
            cigar='30M',
            cds_start=0,
        )
        alignment = build_gene_alignments('A' * 30, [match])['DLONGREV']
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=120, ref='AAAAAAAAAA', alt='A'),
            gene_name='DLONGREV',
            codon_pos=3,
            consequence='deletion',
            is_fasta_mode=False,
        )

        html = str(build_alignment_html(ann, alignment, context_codons=1))
        assert html.count("<span class='aln-cell aln-affected'>-</span>") == 9

