"""
Tests for codon-aware annotation logic.
"""

from respro.core.vcf_annotation import (
    annotate_variants,
    assign_af_bins,
    normalize_mutation,
    reverse_complement,
    translate_codon,
    _classify_snp_consequence,
)
from respro.db.models import AnnotatedVariant, GeneRecord, VariantCall


# ─── translate_codon ──────────────────────────────────────────────────

class TestTranslateCodon:
    def test_start_codon(self):
        assert translate_codon('ATG') == 'M'

    def test_stop_codons(self):
        assert translate_codon('TAA') == '*'
        assert translate_codon('TAG') == '*'
        assert translate_codon('TGA') == '*'

    def test_common_codons(self):
        assert translate_codon('GCT') == 'A'
        assert translate_codon('AAA') == 'K'
        assert translate_codon('GAG') == 'E'

    def test_lowercase(self):
        assert translate_codon('atg') == 'M'

    def test_invalid_length(self):
        assert translate_codon('AT') == '?'
        assert translate_codon('ATGC') == '?'

    def test_ambiguous(self):
        assert translate_codon('NNN') == 'X'


# ─── reverse_complement ──────────────────────────────────────────────

class TestReverseComplement:
    def test_basic(self):
        assert reverse_complement('ATGC') == 'GCAT'

    def test_palindrome(self):
        assert reverse_complement('AATT') == 'AATT'


# ─── _classify_consequence ───────────────────────────────────────────

class TestClassifyConsequence:
    def test_synonymous(self):
        assert _classify_snp_consequence('A', 'A', 10) == 'synonymous'

    def test_missense(self):
        assert _classify_snp_consequence('K', 'E', 10) == 'missense'

    def test_nonsense(self):
        assert _classify_snp_consequence('K', '*', 10) == 'stop_gained'

    def test_stop_loss(self):
        assert _classify_snp_consequence('*', 'K', 10) == 'stop_loss'
    def test_start_loss(self):
        assert _classify_snp_consequence('M', 'G', 0) == 'start_lost'

    def test_unknown(self):
        assert _classify_snp_consequence('?', 'K', 10) == 'unknown'


# ─── normalize_mutation ──────────────────────────────────────────────

class TestNormalizeMutation:
    def test_standard_missense_full_notation(self):
        assert normalize_mutation('F67L') == 'L'
        assert normalize_mutation('f67l') == 'L'

    def test_stop_gained_notation(self):
        assert normalize_mutation('F67*') == '*'
        assert normalize_mutation('F67stop') == '*'
        assert normalize_mutation('F67STOP') == '*'
        assert normalize_mutation('F67ter') is None

    def test_stop_lost_notation(self):
        assert normalize_mutation('*67L') == 'L'

    def test_synonymous_notation(self):
        assert normalize_mutation('F67F') == 'F'

    def test_frameshift_notation(self):
        assert normalize_mutation('F67fs') == 'fsX'
        assert normalize_mutation('F67frameshift') == 'fsX'
        assert normalize_mutation('F67fsATFF*') == 'fsX'
        assert normalize_mutation('F67fsX') == 'fsX'
        assert normalize_mutation('frameshift') == 'fsX'

    def test_deletion_notation(self):
        assert normalize_mutation('F67del') is None
        assert normalize_mutation('delF67') is None
        assert normalize_mutation('del67') is None
        assert normalize_mutation('del') is None

    def test_insertion_and_deletion_rewrite_notation(self):
        assert normalize_mutation('F50FGG') == 'F50FGG'
        assert normalize_mutation('FGG50F') == 'FGG50F'

    def test_hgvs_like_insertion_and_deletion_forms(self):
        assert normalize_mutation('p.F50insGG') is None
        assert normalize_mutation('F50_F51insGG') == 'F50FGG'
        assert normalize_mutation('F50delGG') == 'FGG50F'

    def test_bare_insertion_uses_row_context(self):
        assert normalize_mutation('insGG', reference='F', position_1based=50) == 'F50FGG'
        assert normalize_mutation('insGG') is None

    def test_wildcard_notation(self):
        assert normalize_mutation('any') == 'any'
        assert normalize_mutation('x') == 'any'
        assert normalize_mutation('X') == 'any'

    def test_bare_star_is_stop(self):
        assert normalize_mutation('*') == '*'

    def test_single_letter(self):
        assert normalize_mutation('L') == 'L'
        assert normalize_mutation('e') == 'E'

    def test_unrecognized(self):
        assert normalize_mutation('') is None
        assert normalize_mutation('   ') is None
        assert normalize_mutation('weird_token') is None


# ─── annotate_variants — forward strand ──────────────────────────────

class TestAnnotateVariantsForward:
    """Test annotation on a simple forward-strand gene."""

    def test_missense_in_codon_2(self, tiny_gene, tiny_ref_seq):
        """Position 3 (A→G) is the first base of codon 2 (AAA→GAA = K→E)."""
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.codon_pos == 1  # 0-based: codon index 1 = 2nd codon
        assert ann.ref_codon == 'AAA'
        assert ann.alt_codon == 'GAA'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'

    def test_synonymous_in_codon_3(self, tiny_gene, tiny_ref_seq):
        """Position 8 (T→C) is the third base of codon 3 (GCT→GCC = A→A)."""
        var = VariantCall(chrom='ref', pos=8, ref='T', alt='C', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.codon_pos == 2  # 0-based: codon index 2 = 3rd codon
        assert ann.ref_aa == 'A'
        assert ann.alt_aa == 'A'
        assert ann.consequence == 'synonymous'

    def test_variant_outside_gene(self, tiny_gene, tiny_ref_seq):
        """Variant at position 89 is outside the 87-nt gene."""
        var = VariantCall(chrom='ref', pos=89, ref='N', alt='A', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        assert results[0].gene_name == ''

    def test_deletion_in_gene_annotated(self, tiny_gene, tiny_ref_seq):
        """A 1-base deletion in the gene is now annotated (frameshift)."""
        var = VariantCall(chrom='ref', pos=3, ref='AA', alt='A', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        assert results[0].gene_name == 'gag'
        assert results[0].consequence == 'frameshift'

    def test_combines_two_high_af_snps_in_same_codon(self, tiny_gene, tiny_ref_seq):
        """Two SNPs in one codon with AF > 0.7 are annotated as one codon event."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.80, depth=100),
        ]

        results = annotate_variants(variants, [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.codon_pos == 1
        assert ann.ref_codon == 'AAA'
        assert ann.alt_codon == 'GGA'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'G'
        assert ann.consequence == 'missense'
        assert ann.variant.allele_freq == 0.80

    def test_does_not_combine_when_af_is_exactly_threshold(self, tiny_gene, tiny_ref_seq):
        """AF must be strictly greater than 0.7 for codon-level SNP combination."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.70, depth=100),
        ]

        results = annotate_variants(variants, [tiny_gene])
        assert len(results) == 2

    def test_does_not_combine_when_any_snp_is_low_af(self, tiny_gene, tiny_ref_seq):
        """A single low-AF SNP keeps per-variant annotation behavior."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.20, depth=100),
        ]

        results = annotate_variants(variants, [tiny_gene])
        assert len(results) == 2


class TestAnnotateVariantsReverse:
    """Test annotation on a reverse-strand gene."""

    def test_reverse_strand_codon(self):
        # 12-nt gene on minus strand.
        # Genomic: A A A A A A A A C A T G  (pos 0–11)
        # revcomp: C A T G T T T T T T T T  (coding orientation)
        # Codons:  CAT GTT TTT TTT → H V F F

        ref_seq = 'AAAAAAAACATG'
        nt_coding = reverse_complement(ref_seq)  # coding orientation for minus strand
        gene = GeneRecord(
            id=1, reference_id=1, name='rev_gene', protein='RevP',
            start=0, end=12, strand='-', codon_start=0,
            nt_sequence=nt_coding,
        )

        # Variant at pos 11 (G→C): first nt on minus strand
        # CDS codon 0 = CAT → translate H
        # Alt: complement(C) = G → codon CAG → translate Q? No, wait.
        # cds_variant_pos = (end-1) - pos = 11 - 11 = 0
        # mut = revcomp('C') = 'G'
        # codon 0, pos 0: CAT → GAT → translate D? Let me re-derive.
        # Actually: nt_coding = revcomp('AAAAAAAACATG') = 'CATGTTTTTTTTT'
        # Wait, len is 12: revcomp of 'AAAAAAAACATG' (12 chars)
        # = revcomp each: T,G,C -> complement of GTACTTTTTTTT reversed
        # Let me just compute: Seq('AAAAAAAACATG').reverse_complement() = 'CATGTTTTTTTTT'
        # No: revcomp('AAAAAAAACATG') = complement reversed
        # complement: TTTTTTTGTAC -> reversed: CATGTTTTTTT... hmm
        # 'AAAAAAAACATG' → complement: 'TTTTTTTGTAC' → reverse: 'CATGTTTTTTT'
        # Wait that's 11 chars. Let me just be precise:
        # A A A A A A A A C A T G (12 chars)
        # complement: T T T T T T T T G T A C
        # reverse:     C A T G T T T T T T T T (12 chars)
        # Codons: CAT GTT TTT TTT → H V F F
        #
        # Variant at genomic pos 11 (0-based), ref G→alt C
        # cds_variant_pos = 11 - 11 = 0 → codon 0, pos 0
        # CDS-space: ref codon = CAT, mut base = revcomp('C') = 'G'
        # alt codon = GAT → D
        # ref_aa = H, alt_aa = D → missense

        var = VariantCall(chrom='ref', pos=11, ref='G', alt='C', allele_freq=0.8, depth=200)
        results = annotate_variants([var], [gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'rev_gene'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'H'
        assert ann.alt_aa == 'D'
        assert ann.consequence == 'missense'


# ─── annotate_variants — insertions ──────────────────────────────────

class TestAnnotateInsertions:
    """Test insertion annotation."""

    def test_inframe_insertion(self, tiny_gene, tiny_ref_seq):
        """A 3-base insertion (triplet) → inframe_insertion."""
        # pos 3, ref A, alt AGGG → insert GGG (3 bases, in-frame)
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='AGGG', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.consequence == 'insertion'

    def test_frameshift_insertion(self, tiny_gene, tiny_ref_seq):
        """A 1-base insertion → frameshift."""
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='AG', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.consequence == 'frameshift'
        assert ann.alt_aa == 'fsX'

    def test_frameshift_insertion_2base(self, tiny_gene, tiny_ref_seq):
        """A 2-base insertion → frameshift."""
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='AGG', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        assert results[0].consequence == 'frameshift'


# ─── annotate_variants — deletions ───────────────────────────────────

class TestAnnotateDeletions:
    """Test deletion annotation."""

    def test_inframe_deletion(self, tiny_gene, tiny_ref_seq):
        """A 3-base deletion (triplet) → inframe_deletion."""
        # pos 3, ref AAAG (4 chars), alt A (1 char) → 3 bases deleted, in-frame
        var = VariantCall(chrom='ref', pos=3, ref='AAAG', alt='A', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.consequence == 'deletion'

    def test_frameshift_deletion(self, tiny_gene, tiny_ref_seq):
        """A 1-base deletion → frameshift."""
        var = VariantCall(chrom='ref', pos=3, ref='AA', alt='A', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'gag'
        assert ann.consequence == 'frameshift'
        assert ann.alt_aa == 'fsX'

    def test_frameshift_deletion_2base(self, tiny_gene, tiny_ref_seq):
        """A 2-base deletion → frameshift."""
        var = VariantCall(chrom='ref', pos=3, ref='AAA', alt='A', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        assert results[0].consequence == 'frameshift'

    def test_variant_outside_gene_indel(self, tiny_gene, tiny_ref_seq):
        """An indel outside the gene gets no gene annotation."""
        var = VariantCall(chrom='ref', pos=89, ref='NN', alt='N', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_gene])

        assert len(results) == 1
        assert results[0].gene_name == ''


# ─── annotate_variants — divergent user reference ────────────────────

class TestAnnotateDivergentReference:
    """SNP annotation can use query codon context while ref_aa stays internal."""

    def test_query_codon_changes_alt_aa_for_snp(self):
        """
        Internal codon CGA (R) plus SNP C->T gives TGA (*).
        With query codon CGG (R), the same SNP gives TGG (W).
        """
        # 9-nt gene, 3 codons, forward strand.
        # Internal CDS: ATG CGA AAA → M R K
        internal_seq = 'ATGCGAAAA'
        gene = GeneRecord(
            id=1, reference_id=1, name='test_gene', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        # Variant at genomic pos 3 (first base of codon 1), C→T.
        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100, query_ref_codon='CGG')
        results = annotate_variants([var], [gene])

        assert len(results) == 1
        ann = results[0]
        assert ann.gene_name == 'test_gene'
        assert ann.codon_pos == 1
        assert ann.ref_aa == 'R'
        assert ann.alt_codon == 'TGG'
        assert ann.alt_aa == 'W'
        assert ann.consequence == 'missense'

    def test_identical_codon_path(self):
        """Baseline SNP behavior for an internal CDS codon."""
        internal_seq = 'ATGCGAAAA'
        gene = GeneRecord(
            id=1, reference_id=1, name='test_gene', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100
        )
        results = annotate_variants([var], [gene])

        ann = results[0]
        assert ann.ref_aa == 'R'   # CGA -> R
        assert ann.alt_aa == '*'   # TGA -> stop
        assert ann.consequence == 'stop_gained'

    def test_annotation_uses_internal_without_query_codon(self):
        """Without query codon, SNP annotation stays on the internal CDS."""
        internal_seq = 'ATGCGAAAA'
        gene = GeneRecord(
            id=1, reference_id=1, name='test_gene', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100,
        )
        results = annotate_variants([var], [gene])

        ann = results[0]
        assert ann.ref_aa == 'R'
        assert ann.alt_aa == '*'   # internal CGA → TGA → stop
        assert ann.consequence == 'stop_gained'


class TestCodonStartOffset:
    def test_codon_start_shift_forward_gene(self):
        """codon_start offset shifts codon indexing for rule-compatible positions."""
        gene = GeneRecord(
            id=1,
            reference_id=1,
            name='offset_gene',
            protein='Offset',
            start=0,
            end=10,
            strand='+',
            codon_start=1,
            nt_sequence='NAAAGAAAAA',
        )
        # Position 1 is first coding base (codon AAA), A->G => GAA (K->E)
        var = VariantCall(chrom='ref', pos=1, ref='A', alt='G', allele_freq=0.8, depth=100)
        ann = annotate_variants([var], [gene])[0]

        assert ann.codon_pos == 0
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'


# ─── assign_af_bins ───────────────────────────────────────────────────

def _make_ann(af: float) -> AnnotatedVariant:
    var = VariantCall(chrom='c', pos=0, ref='A', alt='G', allele_freq=af, depth=100)
    return AnnotatedVariant(variant=var)


class TestAssignAfBins:
    """Tests for VCF-mode and FASTA-mode AF binning."""

    def test_vcf_high(self) -> None:
        anns = assign_af_bins([_make_ann(1.0)])
        assert anns[0].af_bin == 'high'

    def test_vcf_intermediate(self) -> None:
        anns = assign_af_bins([_make_ann(0.5)])
        assert anns[0].af_bin == 'intermediate'

    def test_vcf_low(self) -> None:
        anns = assign_af_bins([_make_ann(0.05)])
        assert anns[0].af_bin == 'low'

    def test_vcf_unknown_below_threshold(self) -> None:
        anns = assign_af_bins([_make_ann(0.001)])
        assert anns[0].af_bin == 'unknown'

    # FASTA-mode bins: high=1.0, intermediate=0.5, low=0.33/0.25
    _FASTA_BINS = {
        'high': (0.75, 1.0),
        'intermediate': (0.35, 0.74),
        'low': (0.01, 0.34),
    }

    def test_fasta_bins_high(self) -> None:
        anns = assign_af_bins([_make_ann(1.0)], bins=self._FASTA_BINS)
        assert anns[0].af_bin == 'high'

    def test_fasta_bins_intermediate(self) -> None:
        anns = assign_af_bins([_make_ann(0.5)], bins=self._FASTA_BINS)
        assert anns[0].af_bin == 'intermediate'

    def test_fasta_bins_low_one_third(self) -> None:
        """1/3 frequency (3-way IUPAC) maps to low."""
        anns = assign_af_bins([_make_ann(round(1 / 3, 10))], bins=self._FASTA_BINS)
        assert anns[0].af_bin == 'low'

    def test_fasta_bins_low_one_quarter(self) -> None:
        """1/4 frequency (4-way IUPAC) maps to low."""
        anns = assign_af_bins([_make_ann(0.25)], bins=self._FASTA_BINS)
        assert anns[0].af_bin == 'low'
