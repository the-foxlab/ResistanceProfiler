"""
Tests for report output generation.
"""

import sqlite3

from respro.db.models import AnnotatedVariant, CoverageGap, GeneMatch, GeneRecord, Publication, ResistanceRule, VariantCall
from respro.db.models import ProfilingResult
from respro.report.alignment_visualization import build_alignment_html, build_gene_alignments
from respro.report.html import build_report_context
from respro.report.html import _build_potential_effects_rows
from respro.report.html import _load_gene_cards
from respro.report.html import render_html


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
        assert b'#6b7280' in svg
        assert b'opacity: 0.12' in svg


class TestAlignmentVisualization:
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
        assert "<span class='aln-cell aln-affected'>C</span>" in html
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
        assert "<span class='aln-cell aln-affected'>C</span>" in html
        assert "<span class='aln-cell aln-affected'>-</span>" in html

