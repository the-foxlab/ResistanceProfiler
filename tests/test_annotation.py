"""
Tests for codon-aware annotation logic.
"""

from respro.cli.profile_helpers import _suppress_ruleless_overlap_annotations
from respro.core.annotation import (
    _annotate_combined_snp_codon,
    _annotate_variant_in_feature,
    _classify_snp_consequence,
    annotate_variants,
    assign_af_bins,
    normalize_mutation,
    reverse_complement,
    translate_codon,
)
from respro.db.models import AnnotatedVariant, FeatureRecord, VariantCall

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

    def test_ins_any_token_normalized(self):
        assert normalize_mutation('ins_any') == 'INS_any'

    def test_ins_any_case_insensitive(self):
        assert normalize_mutation('INS_ANY') == 'INS_any'
        assert normalize_mutation('Ins_Any') == 'INS_any'
        assert normalize_mutation('ins_Any') == 'INS_any'

    def test_ins_any_is_not_confused_with_bare_ins(self):
        # Regression guard: specific bare insertion still works normally
        assert normalize_mutation('insGG', reference='F', position_1based=50) == 'F50FGG'

    def test_rejects_wildcard_notation(self):
        assert normalize_mutation('any') is None
        assert normalize_mutation('x') is None
        assert normalize_mutation('X') is None

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
    """Test annotation on a simple forward-strand feature."""

    def test_missense_in_codon_2(self, tiny_feature, tiny_ref_seq):
        """Position 3 (A→G) is the first base of codon 2 (AAA→GAA = K→E)."""
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [tiny_feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.feature_name == 'gag'
        assert ann.codon_pos == 1  # 0-based: codon index 1 = 2nd codon
        assert ann.ref_codon == 'AAA'
        assert ann.alt_codon == 'GAA'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'

    def test_synonymous_in_codon_3(self, tiny_feature, tiny_ref_seq):
        """Position 8 (T→C) is the third base of codon 3 (GCT→GCC = A→A)."""
        var = VariantCall(chrom='ref', pos=8, ref='T', alt='C', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.feature_name == 'gag'
        assert ann.codon_pos == 2  # 0-based: codon index 2 = 3rd codon
        assert ann.ref_aa == 'A'
        assert ann.alt_aa == 'A'
        assert ann.consequence == 'synonymous'

    def test_variant_outside_feature(self, tiny_feature, tiny_ref_seq):
        """Variant at position 89 is outside the 87-nt feature."""
        var = VariantCall(chrom='ref', pos=89, ref='N', alt='A', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_feature])

        assert len(results) == 1
        assert results[0].feature_name == ''

    def test_frameshift_deletion_in_feature_is_annotated(self, tiny_feature, tiny_ref_seq):
        """1-nt deletion at a codon boundary produces a frameshift annotation."""
        # tiny_feature codon 1: pos 3–5, AAA (K); 1-nt deletion → frameshift
        var = VariantCall(chrom='ref', pos=3, ref='AA', alt='A', allele_freq=0.5, depth=50)
        results = annotate_variants([var], [tiny_feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.feature_name == 'gag'
        assert ann.codon_pos == 1
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'KfsX'
        assert ann.consequence == 'frameshift'

    def test_combines_two_high_af_snps_in_same_codon(self, tiny_feature, tiny_ref_seq):
        """Two SNPs in one codon with AF > 0.7 are annotated as one codon event."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.80, depth=100),
        ]

        results = annotate_variants(variants, [tiny_feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.codon_pos == 1
        assert ann.ref_codon == 'AAA'
        assert ann.alt_codon == 'GGA'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'G'
        assert ann.consequence == 'missense'
        assert ann.variant.allele_freq == 0.80

    def test_does_not_combine_when_af_is_exactly_threshold(self, tiny_feature, tiny_ref_seq):
        """AF must be strictly greater than 0.7 for codon-level SNP combination."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.70, depth=100),
        ]

        results = annotate_variants(variants, [tiny_feature])
        assert len(results) == 2

    def test_does_not_combine_when_any_snp_is_low_af(self, tiny_feature, tiny_ref_seq):
        """A single low-AF SNP keeps per-variant annotation behavior."""
        variants = [
            VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.90, depth=100),
            VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.20, depth=100),
        ]

        results = annotate_variants(variants, [tiny_feature])
        assert len(results) == 2

    def test_marks_annotations_as_fasta_mode_when_requested(self, tiny_feature, tiny_ref_seq):
        """All emitted annotations should carry is_fasta_mode=True in FASTA flow."""
        var = VariantCall(chrom='ref', pos=3, ref='A', alt='G', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [tiny_feature], is_fasta_mode=True)

        assert len(results) == 1
        assert results[0].is_fasta_mode is True


class TestAnnotateVariantsReverse:
    """Test annotation on a reverse-strand feature."""

    def test_reverse_strand_codon(self):
        # 12-nt feature on minus strand.
        # Genomic: A A A A A A A A C A T G  (pos 0–11)
        # revcomp: C A T G T T T T T T T T  (coding orientation)
        # Codons:  CAT GTT TTT TTT → H V F F

        ref_seq = 'AAAAAAAACATG'
        nt_coding = reverse_complement(ref_seq)  # coding orientation for minus strand
        feature = FeatureRecord(
            id=1, reference_id=1, name='rev_feature', protein='RevP',
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
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.feature_name == 'rev_feature'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'H'
        assert ann.alt_aa == 'D'
        assert ann.consequence == 'missense'


class TestAnnotateCombinedSnpCodon:
    def test_raises_clear_error_when_anchor_has_no_codon_index(self):
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='split_feature',
            protein='SplitP',
            start=0,
            end=9,
            strand='+',
            nt_sequence='ATGAAA',
        )
        variant = VariantCall(chrom='ref', pos=99, ref='A', alt='G', allele_freq=0.9, depth=100)

        try:
            _annotate_combined_snp_codon([variant], feature)
        except ValueError as exc:
            assert str(exc) == (
                "Combined SNP annotation requires coding codon index for feature 'split_feature' at "
                'genomic position 99'
            )
        else:
            raise AssertionError('Expected ValueError for missing codon index')

    def test_raises_clear_error_when_member_has_no_codon_position(self):
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='split_feature',
            protein='SplitP',
            start=0,
            end=9,
            strand='+',
            nt_sequence='ATGAAA',
        )
        variants = [
            VariantCall(chrom='ref', pos=0, ref='A', alt='G', allele_freq=0.9, depth=100),
            VariantCall(chrom='ref', pos=99, ref='A', alt='C', allele_freq=0.9, depth=100),
        ]

        try:
            _annotate_combined_snp_codon(variants, feature)
        except ValueError as exc:
            assert str(exc) == (
                "Combined SNP annotation requires coding codon position for feature 'split_feature' at "
                'genomic position 99'
            )
        else:
            raise AssertionError('Expected ValueError for missing codon position')



# ─── annotate_variants — divergent user reference ────────────────────

class TestAnnotateDivergentReference:
    """SNP annotation can use query codon context while ref_aa stays internal."""

    def test_query_codon_changes_alt_aa_for_snp(self):
        """
        Internal codon CGA (R) plus SNP C->T gives TGA (*).
        With query codon CGG (R), the same SNP gives TGG (W).
        """
        # 9-nt feature, 3 codons, forward strand.
        # Internal CDS: ATG CGA AAA → M R K
        internal_seq = 'ATGCGAAAA'
        feature = FeatureRecord(
            id=1, reference_id=1, name='test_feature', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        # Variant at genomic pos 3 (first base of codon 1), C→T.
        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100, query_ref_codon='CGG')
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.feature_name == 'test_feature'
        assert ann.codon_pos == 1
        assert ann.ref_aa == 'R'
        assert ann.alt_codon == 'TGG'
        assert ann.alt_aa == 'W'
        assert ann.consequence == 'missense'

    def test_identical_codon_path(self):
        """Baseline SNP behavior for an internal CDS codon."""
        internal_seq = 'ATGCGAAAA'
        feature = FeatureRecord(
            id=1, reference_id=1, name='test_feature', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100
        )
        results = annotate_variants([var], [feature])

        ann = results[0]
        assert ann.ref_aa == 'R'   # CGA -> R
        assert ann.alt_aa == '*'   # TGA -> stop
        assert ann.consequence == 'stop_gained'

    def test_annotation_uses_internal_without_query_codon(self):
        """Without query codon, SNP annotation stays on the internal CDS."""
        internal_seq = 'ATGCGAAAA'
        feature = FeatureRecord(
            id=1, reference_id=1, name='test_feature', protein='TestP',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence=internal_seq,
        )

        var = VariantCall(
            chrom='ref', pos=3, ref='C', alt='T',
            allele_freq=0.9, depth=100,
        )
        results = annotate_variants([var], [feature])

        ann = results[0]
        assert ann.ref_aa == 'R'
        assert ann.alt_aa == '*'   # internal CGA → TGA → stop
        assert ann.consequence == 'stop_gained'


class TestCodonStartOffset:
    def test_codon_start_shift_forward_feature(self):
        """codon_start offset shifts codon indexing for rule-compatible positions."""
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='offset_feature',
            protein='Offset',
            start=0,
            end=10,
            strand='+',
            codon_start=1,
            nt_sequence='NAAAGAAAAA',
        )
        # Position 1 is first coding base (codon AAA), A->G => GAA (K->E)
        var = VariantCall(chrom='ref', pos=1, ref='A', alt='G', allele_freq=0.8, depth=100)
        ann = annotate_variants([var], [feature])[0]

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


# ─── Helper ──────────────────────────────────────────────────────────

def _make_feature(nt_sequence: str, strand: str = '+') -> FeatureRecord:
    end = len(nt_sequence)
    return FeatureRecord(
        id=1, reference_id=1, name='feature', protein='P',
        start=0, end=end, strand=strand, codon_start=0,
        nt_sequence=nt_sequence,
        # For minus-strand features, nt_sequence is stored in coding orientation
        # (reverse-complement of the genomic slice), but we override below.
    )


# ─── Insertion annotation ─────────────────────────────────────────────

class TestInsertionAnnotation:
    """Codon-aware annotation for VCF insertions."""

    def _fwd_feature(self) -> FeatureRecord:
        # ATG GGG TTT → M G F (9 nt, forward strand)
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='+', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def _rev_feature(self) -> FeatureRecord:
        # Minus-strand feature with coding sequence ATG GGG TTT → M G F
        # Genomic sequence is revcomp('ATGGGGTTT') = 'AAACCCCAT'
        # But nt_sequence stores coding orientation: 'ATGGGGTTT'
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='-', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def test_inframe_insertion_at_codon_boundary(self) -> None:
        """In-frame insertion at codon 0 boundary: anchor M, inserted G → alt_aa MG."""
        feature = self._fwd_feature()
        # VCF anchor is the preceding nucleotide in genomic 5'->3': boundary after codon 0 -> pos=2.
        var = VariantCall(chrom='c', pos=2, ref='G', alt='GGGG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.feature_name == 'feature'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MG'
        assert ann.consequence == 'insertion'

    def test_inframe_insertion_second_codon(self) -> None:
        """In-frame insertion at codon 1 boundary: anchor G, inserted G → alt_aa GG."""
        feature = self._fwd_feature()
        # Boundary after codon 1 -> anchor at pos=5.
        var = VariantCall(chrom='c', pos=5, ref='G', alt='GGGG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'G'
        assert ann.alt_aa == 'GG'
        assert ann.consequence == 'insertion'

    def test_frameshift_insertion(self) -> None:
        """1-nt insertion at codon boundary is annotated as frameshift."""
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=2, ref='G', alt='GG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'

    def test_inframe_insertion_at_mid_codon_is_complex(self) -> None:
        """In-frame insertion anchored mid-codon is now split into insertion annotation."""
        feature = self._fwd_feature()
        # pos=1 is mid-codon for codon 0 under VCF-anchor semantics.
        var = VariantCall(chrom='c', pos=1, ref='T', alt='TGGG', allele_freq=0.9, depth=100)
        anns = _annotate_variant_in_feature(var, feature)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MG'
        assert ann.consequence == 'insertion'

    def test_mid_codon_non_inframe_insertion_is_frameshift(self) -> None:
        """Non-in-frame insertion is frameshift even when anchored mid-codon."""
        feature = self._fwd_feature()
        # pos=1 is frame_offset 1 within codon 0; 1-nt payload insertion -> frameshift
        var = VariantCall(chrom='c', pos=1, ref='T', alt='TG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'

    def test_inframe_insertion_negative_strand(self) -> None:
        """In-frame insertion on a negative-strand feature uses revcomp of inserted bases."""
        feature = self._rev_feature()
        # Boundary after codon 0 on '-' strand maps to genomic anchor pos=5.
        var = VariantCall(chrom='c', pos=5, ref='C', alt='CGGG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MP'
        assert ann.consequence == 'insertion'

    def test_insertion_uses_query_anchor_codon_positive_strand(self) -> None:
        """Forward-strand feature: query_ref_codon overrides internal CDS for anchor AA."""
        feature = self._fwd_feature()
        # Internal codon 1 = 'GGG' → G; user's query codon 1 = 'AGG' → R (divergent anchor).
        # Rule in the DB says reference='R', mutation='RG' → only matches when anchor comes from query.
        var = VariantCall(
            chrom='c', pos=5, ref='G', alt='GGGG', allele_freq=0.9, depth=100,
            query_ref_codon='AGG',
        )
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'R'
        assert ann.alt_aa == 'RG'
        assert ann.consequence == 'insertion'

    def test_insertion_uses_query_anchor_codon_negative_strand(self) -> None:
        """Minus-strand feature: query_ref_codon is expected in CDS orientation."""
        feature = self._rev_feature()
        # Minus-strand feature coding 'ATGGGGTTT' (M G F).
        # Anchor at CDS pos 3 (start of codon 1) → genomic pos = (9-1)-3 = 5.
        # Genomic base at 5 = 'C' (from AAACCCCAT, the genomic forward for this feature).
        # Insert 'CCC' on forward strand → RC = 'GGG' → G (glycine in coding).
        # query_ref_codon='AGG' → R instead of internal G.
        var = VariantCall(
            chrom='c', pos=5, ref='C', alt='CCCC', allele_freq=0.9, depth=100,
            query_ref_codon='AGG',
        )
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'R'
        assert ann.alt_aa == 'RG'
        assert ann.consequence == 'insertion'


# ─── Deletion annotation ──────────────────────────────────────────────

class TestDeletionAnnotation:
    """Codon-aware annotation for VCF deletions."""

    def _fwd_feature(self) -> FeatureRecord:
        # ATG GGG TTT → M G F (9 nt, forward strand)
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='+', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def test_inframe_deletion(self) -> None:
        """3-nt deletion at codon 0 boundary: ref_aa MG (deleted G), alt_aa M."""
        feature = self._fwd_feature()
        # Boundary after codon 0 -> anchor at pos=2.
        var = VariantCall(chrom='c', pos=2, ref='GTGG', alt='G', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.feature_name == 'feature'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'MW'
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'

    def test_inframe_deletion_multi_codon(self) -> None:
        """6-nt deletion spans two payload codons: ref_aa has three AAs, alt_aa has anchor only."""
        feature = self._fwd_feature()
        # Boundary after codon 0, deleting 6-nt payload.
        var = VariantCall(chrom='c', pos=2, ref='GTGGGGT', alt='G', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.codon_pos == 0
        assert ann.ref_aa == 'MWG'
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'

    def test_frameshift_deletion(self) -> None:
        """1-nt deletion at codon boundary is annotated as frameshift."""
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=2, ref='GT', alt='G', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'

    def test_inframe_deletion_at_mid_codon_is_complex(self) -> None:
        """In-frame deletion anchored mid-codon is now split into deletion annotation."""
        feature = self._fwd_feature()
        # pos=1 is frame_offset 1 within codon 0
        var = VariantCall(chrom='c', pos=1, ref='TGGG', alt='T', allele_freq=0.9, depth=100)
        anns = _annotate_variant_in_feature(var, feature)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.ref_aa == 'MG'
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'

    def test_mid_codon_non_inframe_deletion_is_frameshift(self) -> None:
        """Non-in-frame deletion is frameshift even when anchored mid-codon."""
        feature = self._fwd_feature()
        # pos=1 is frame_offset 1 within codon 0; delete 1 nt payload -> frameshift
        var = VariantCall(chrom='c', pos=1, ref='TG', alt='T', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'

    def test_inframe_deletion_negative_strand(self) -> None:
        """In-frame deletion on a negative-strand feature uses revcomp of deleted bases."""
        feature = FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='-', codon_start=0, nt_sequence='ATGGGGTTT',
        )
        # Boundary after codon 0 on '-' strand maps to genomic anchor pos=2.
        var = VariantCall(chrom='c', pos=2, ref='AAAA', alt='A', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'MF'
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'

    def test_deletion_uses_query_anchor_codon_positive_strand(self) -> None:
        """Forward-strand feature: query_ref_codon overrides internal CDS for anchor AA."""
        feature = self._fwd_feature()
        # Internal codon 1 = 'GGG' → G; user's query codon 1 = 'AGG' → R (divergent anchor).
        # Deletion of 'GGG' (next codon, G) from anchor codon 1.
        var = VariantCall(
            chrom='c', pos=5, ref='GGGG', alt='G', allele_freq=0.9, depth=100,
            query_ref_codon='AGG',
        )
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'RG'   # anchor from query (R) + deleted G
        assert ann.alt_aa == 'R'
        assert ann.consequence == 'deletion'

    def test_deletion_uses_query_anchor_codon_negative_strand(self) -> None:
        """Minus-strand feature: query_ref_codon (CDS orientation) used as anchor."""
        feature = FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='-', codon_start=0, nt_sequence='ATGGGGTTT',
        )
        # Boundary after codon 0 on '-' strand maps to genomic anchor pos=2.
        var = VariantCall(
            chrom='c', pos=2, ref='AAAA', alt='A', allele_freq=0.9, depth=100,
            query_ref_codon='AGG',
        )
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'RF'   # anchor R (from query) + deleted F
        assert ann.alt_aa == 'R'
        assert ann.consequence == 'deletion'


# ─── Frameshift annotation ────────────────────────────────────────────

class TestFrameshiftAnnotation:
    """Frameshift annotations store anchor AA and use anchored alt_aa token (e.g. GfsX)."""

    def _fwd_feature(self) -> FeatureRecord:
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=9, strand='+', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def test_frameshift_insertion_stores_anchor_aa(self) -> None:
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='G', alt='GG', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.codon_pos == 1
        assert ann.ref_aa == 'G'   # codon GGG → G
        assert ann.alt_aa == 'GfsX'
        assert ann.consequence == 'frameshift'
        assert ann.ref_codon == 'GGG'

    def test_frameshift_deletion_stores_anchor_aa(self) -> None:
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='GG', alt='G', allele_freq=0.9, depth=100)
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'G'
        assert ann.alt_aa == 'GfsX'
        assert ann.consequence == 'frameshift'

    def test_frameshift_through_annotate_variants(self) -> None:
        """annotate_variants correctly includes frameshift results."""
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=0, ref='AT', alt='A', allele_freq=0.8, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        assert results[0].consequence == 'frameshift'

    def test_frameshift_uses_query_anchor_codon(self) -> None:
        """query_ref_codon used as anchor codon for frameshift too."""
        feature = self._fwd_feature()
        # Internal codon 1 = 'GGG' → G; user's query codon 1 = 'AGG' → R.
        var = VariantCall(
            chrom='c', pos=3, ref='GG', alt='G', allele_freq=0.9, depth=100,
            query_ref_codon='AGG',
        )
        ann = _annotate_variant_in_feature(var, feature)[0]

        assert ann.ref_aa == 'R'   # from query, not internal G
        assert ann.alt_aa == 'RfsX'
        assert ann.consequence == 'frameshift'


# ─── _suppress_ruleless_overlap_annotations ───────────────────────────

class TestSuppressRulelessOverlapAnnotations:
    """Test filtering of spurious annotations from overlapping ruleless features."""

    def test_overlapping_features_removes_ruleless_when_ruled_exists(self) -> None:
        """
        Variant at overlap of two features: one with rules, one without.
        Result: only the ruled feature annotation survives.
        """
        # feature A: positions 0–29, with rules
        feature_a = FeatureRecord(
            id=1,
            reference_id=1,
            name='FeatureA',
            protein='ProteinA',
            start=0,
            end=30,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'AAA' * 9,
        )

        # feature B: positions 10–40, no rules
        feature_b = FeatureRecord(
            id=2,
            reference_id=1,
            name='FeatureB',
            protein='ProteinB',
            start=10,
            end=40,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'GGG' * 10,
        )

        # Variant at position 15, falls in both features
        var = VariantCall(chrom='ref', pos=15, ref='A', alt='G', allele_freq=0.9, depth=100)
        annotations = annotate_variants([var], [feature_a, feature_b])

        # Should have two annotations (one per feature)
        assert len(annotations) == 2
        feature_names = {ann.feature_name for ann in annotations}
        assert feature_names == {'FeatureA', 'FeatureB'}

        # Filter with FeatureA in rule_feature_names
        rule_feature_names = {'FeatureA'}
        filtered = _suppress_ruleless_overlap_annotations(annotations, rule_feature_names)

        # Only FeatureA should survive
        assert len(filtered) == 1
        assert filtered[0].feature_name == 'FeatureA'

    def test_overlapping_features_keeps_both_when_neither_has_rules(self) -> None:
        """
        Variant at overlap of two features, neither with rules.
        Result: both annotations survive (no ruled features to filter against).
        """
        # feature A: positions 0–30, no rules
        feature_a = FeatureRecord(
            id=1,
            reference_id=1,
            name='FeatureA',
            protein='ProteinA',
            start=0,
            end=30,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'AAA' * 9,
        )

        # feature B: positions 10–40, no rules
        feature_b = FeatureRecord(
            id=2,
            reference_id=1,
            name='FeatureB',
            protein='ProteinB',
            start=10,
            end=40,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'GGG' * 10,
        )

        # Variant at position 15, falls in both features
        var = VariantCall(chrom='ref', pos=15, ref='A', alt='G', allele_freq=0.9, depth=100)
        annotations = annotate_variants([var], [feature_a, feature_b])

        # Should have two annotations
        assert len(annotations) == 2

        # Filter with empty rule_feature_names (neither feature has rules)
        rule_feature_names: set[str] = set()
        filtered = _suppress_ruleless_overlap_annotations(annotations, rule_feature_names)

        # Both should survive since neither has rules
        assert len(filtered) == 2
        feature_names = {ann.feature_name for ann in filtered}
        assert feature_names == {'FeatureA', 'FeatureB'}

    def test_single_feature_annotation_always_passes(self) -> None:
        """
        Single-feature annotations should always pass through unchanged,
        regardless of rule_feature_names.
        """
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='FeatureX',
            protein='ProteinX',
            start=0,
            end=30,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'AAA' * 9,
        )

        # Variant within single feature
        var = VariantCall(chrom='ref', pos=5, ref='A', alt='G', allele_freq=0.9, depth=100)
        annotations = annotate_variants([var], [feature])

        # Should have one annotation
        assert len(annotations) == 1

        # Filter with FeatureX not in rule_feature_names
        rule_feature_names: set[str] = set()
        filtered = _suppress_ruleless_overlap_annotations(annotations, rule_feature_names)

        # Should survive unchanged
        assert len(filtered) == 1
        assert filtered[0].feature_name == 'FeatureX'

    def test_variant_outside_all_features_passes(self) -> None:
        """
        Variants outside all features (feature_name='') should pass through unchanged.
        """
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='FeatureA',
            protein='ProteinA',
            start=0,
            end=30,
            strand='+',
            codon_start=0,
            nt_sequence='ATG' + 'AAA' * 9,
        )

        # Variant far outside the feature
        var = VariantCall(chrom='ref', pos=100, ref='A', alt='G', allele_freq=0.9, depth=100)
        annotations = annotate_variants([var], [feature])

        # Should have one annotation with empty feature_name
        assert len(annotations) == 1
        assert annotations[0].feature_name == ''

        # Filter with any rule_feature_names
        rule_feature_names = {'FeatureA'}
        filtered = _suppress_ruleless_overlap_annotations(annotations, rule_feature_names)

        # Should survive unchanged
        assert len(filtered) == 1
        assert filtered[0].feature_name == ''

    def test_overlapping_with_copied_variant_objects_keeps_only_ruled(self) -> None:
        """
        Overlapping annotations may carry copied VariantCall instances for one locus.
        Result: suppression must still keep only ruled features.
        """
        ruled_variant = VariantCall(
            chrom='ref', pos=2299, ref='A', alt='G', allele_freq=0.9, depth=100,
        )
        ruleless_variant = VariantCall(
            chrom='ref', pos=2299, ref='A', alt='G', allele_freq=0.9, depth=100,
        )

        annotations = [
            AnnotatedVariant(variant=ruled_variant, feature_name='gag-pol_5'),
            AnnotatedVariant(variant=ruleless_variant, feature_name='gag-pol_6'),
        ]

        filtered = _suppress_ruleless_overlap_annotations(annotations, {'gag-pol_5'})

        assert len(filtered) == 1
        assert filtered[0].feature_name == 'gag-pol_5'


# ─── Mid-codon in-frame indel splitting ───────────────────────────────

class TestMidCodonInframeIndelSplit:
    """
    Mid-codon in-frame indels produce missense + indel annotations
    instead of a single inframe_complex annotation.
    """

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _fwd_feature(nt_sequence: str = 'ATGGGGTTT') -> FeatureRecord:
        """Forward-strand feature. Default: ATG GGG TTT → M G F."""
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=len(nt_sequence), strand='+', codon_start=0,
            nt_sequence=nt_sequence,
        )

    @staticmethod
    def _rev_feature(nt_sequence: str = 'ATGGGGTTT') -> FeatureRecord:
        """Minus-strand feature. nt_sequence is in coding orientation."""
        return FeatureRecord(
            id=1, reference_id=1, name='feature', protein='P',
            start=0, end=len(nt_sequence), strand='-', codon_start=0,
            nt_sequence=nt_sequence,
        )

    # ── forward-strand insertion tests ─────────────────────────────────

    def test_inframe_insertion_mid_codon_frame1_synonymous_anchor(self) -> None:
        """
        3-nt insertion at frame_offset=1 where anchor codon does NOT change.

        Feature: ATGGGGTTT (M G F), pos=1, ref='T', alt='TGGG'
        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Inserted bases (coding) = GGG
        Query anchor codon = AT + G = ATG → M (synonymous)
        Insertion payload: GG + displaced G = GGG → G
        → Only insertion annotation: ref_aa=M, alt_aa=MG
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=1, ref='T', alt='TGGG', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'insertion'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MG'

    def test_inframe_insertion_mid_codon_frame0_missense_anchor(self) -> None:
        """
        3-nt insertion at frame_offset=0 where anchor codon changes.

        Feature: ATGGGGTTT (M G F), pos=3, ref='G', alt='GCCC'
        Anchor codon 1 = GGG → G, preserved=1 (G)
        Inserted bases = CCC
        Query anchor codon = G + CC = GCC → A
        → Missense: G→A at codon 1
        → Insertion: ref_aa=G, alt_aa=GR (payload: C + displaced GG = CGG → R)
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='G', alt='GCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(missense) == 1
        assert len(insertion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'G'
        assert m.alt_aa == 'A'
        assert m.ref_codon == 'GGG'
        assert m.alt_codon == 'GCC'

        i = insertion[0]
        assert i.codon_pos == 1
        assert i.ref_aa == 'G'
        assert i.alt_aa == 'GR'

    def test_inframe_insertion_mid_codon_frame1_missense_anchor(self) -> None:
        """
        3-nt insertion at frame_offset=1 where anchor codon changes.

        Feature: ATGAAAGGG (M K G), pos=4, ref='A', alt='ACCC'
        Anchor codon 1 = AAA → K, preserved=2 (AA)
        Inserted bases = CCC
        Query anchor codon = AA + C = AAC → N
        → Missense: K→N at codon 1
        → Insertion: ref_aa=K, alt_aa=KP (payload: CC + displaced A = CCA → P)
        """
        feature = self._fwd_feature('ATGAAAGGG')
        var = VariantCall(chrom='c', pos=4, ref='A', alt='ACCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(missense) == 1
        assert len(insertion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'K'
        assert m.alt_aa == 'N'

        i = insertion[0]
        assert i.codon_pos == 1
        assert i.ref_aa == 'K'
        assert i.alt_aa == 'KP'

    def test_inframe_insertion_mid_codon_frame0_stop_gained(self) -> None:
        """
        3-nt insertion at frame_offset=0 where anchor codon becomes a stop codon.

        Feature: ATGTGGAAG (M W K), pos=3, ref='T', alt='TAAG'
        Anchor codon 1 = TGG → W, preserved=1 (T)
        Inserted bases = AAG
        Query anchor codon = T + AA = TAA → * (stop)
        → stop_gained: W→* at codon 1
        → Insertion: ref_aa=W, alt_aa=WG (payload: G + displaced GG = GGG → G)
        """
        feature = self._fwd_feature('ATGTGGAAG')
        var = VariantCall(chrom='c', pos=3, ref='T', alt='TAAG', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        stop = [a for a in results if a.consequence == 'stop_gained']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(stop) == 1
        assert len(insertion) == 1

        s = stop[0]
        assert s.codon_pos == 1
        assert s.ref_aa == 'W'
        assert s.alt_aa == '*'
        assert s.ref_codon == 'TGG'
        assert s.alt_codon == 'TAA'

        i = insertion[0]
        assert i.codon_pos == 1
        assert i.ref_aa == 'W'
        assert i.alt_aa == 'WG'

    def test_inframe_insertion_mid_codon_6nt(self) -> None:
        """
        6-nt insertion at frame_offset=1 produces 2 inserted AAs.

        Feature: ATGAAAGGG (M K G), pos=4, ref='A', alt='ACCCCCC'
        Anchor codon 1 = AAA → K, preserved=2 (AA)
        Inserted bases = CCCCCC (6 bases)
        Query anchor codon = AA + C = AAC → N
        → Missense: K→N at codon 1
        → Insertion: ref_aa=K, alt_aa=KPP
          (payload: CCCCC + displaced A = CCCCCA → CCC|CCA → P|P → PP)
        """
        feature = self._fwd_feature('ATGAAAGGG')
        var = VariantCall(chrom='c', pos=4, ref='A', alt='ACCCCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(missense) == 1
        assert len(insertion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'K'
        assert m.alt_aa == 'N'

        i = insertion[0]
        assert i.codon_pos == 1
        assert i.ref_aa == 'K'
        assert i.alt_aa == 'KPP'

    # ── forward-strand deletion tests ──────────────────────────────────

    def test_inframe_deletion_mid_codon_frame1_synonymous_anchor(self) -> None:
        """
        3-nt deletion at frame_offset=1 where anchor codon does NOT change.

        Feature: ATGGGGTTT (M G F), pos=1, ref='TGGG', alt='T'
        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Deleted bases = GGG
        Query anchor codon = AT + G(5) = ATG → M (synonymous)
        Deleted codons: 1 (GGG → G)
        → Only deletion annotation: ref_aa=MG, alt_aa=M
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=1, ref='TGGG', alt='T', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'deletion'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'MG'
        assert ann.alt_aa == 'M'

    def test_inframe_deletion_mid_codon_frame0_missense_anchor(self) -> None:
        """
        3-nt deletion at frame_offset=0 where anchor codon changes.

        Feature: ATGCGAAAA (M R K), pos=3, ref='CGAA', alt='C'
        Anchor codon 1 = CGA → R, preserved=1 (C)
        Deleted bases = GAA
        Query anchor codon = C + A(7) + A(8) = CAA → Q
        → Missense: R→Q at codon 1
        → Deletion: ref_aa=RK, alt_aa=R (deleted codon 2: AAA → K)
        """
        feature = self._fwd_feature('ATGCGAAAA')
        var = VariantCall(chrom='c', pos=3, ref='CGAA', alt='C', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(missense) == 1
        assert len(deletion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'R'
        assert m.alt_aa == 'Q'
        assert m.ref_codon == 'CGA'
        assert m.alt_codon == 'CAA'

        d = deletion[0]
        assert d.codon_pos == 1
        assert d.ref_aa == 'RK'
        assert d.alt_aa == 'R'

    def test_inframe_deletion_mid_codon_frame1_missense_anchor(self) -> None:
        """
        3-nt deletion at frame_offset=1 where anchor codon changes.

        Feature: ATGATGCAATTTAAA (M M Q F K), pos=4, ref='TGCA', alt='T'
        Anchor codon 1 = ATG → M, preserved=2 (AT)
        Deleted bases = GCA
        Query anchor codon = AT + A(8) = ATA → I
        → Missense: M→I at codon 1
        → Deletion: ref_aa=MQ, alt_aa=M (deleted codon 2: CAA → Q)
        """
        feature = self._fwd_feature('ATGATGCAATTTAAA')
        var = VariantCall(chrom='c', pos=4, ref='TGCA', alt='T', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(missense) == 1
        assert len(deletion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'M'
        assert m.alt_aa == 'I'
        assert m.ref_codon == 'ATG'
        assert m.alt_codon == 'ATA'

        d = deletion[0]
        assert d.codon_pos == 1
        assert d.ref_aa == 'MQ'
        assert d.alt_aa == 'M'

    def test_inframe_deletion_mid_codon_6nt(self) -> None:
        """
        6-nt deletion at frame_offset=1 removes 2 codons.

        Feature: ATGATGCAATTTAAA (M M Q F K), pos=4, ref='TGCAATT', alt='T'
        Anchor codon 1 = ATG → M, preserved=2 (AT)
        Deleted bases = GCAATT (6 bases)
        Query anchor codon = AT + A(11) = ATA → I
        → Missense: M→I at codon 1
        → Deletion: ref_aa=MQF, alt_aa=M (deleted codons 2-3: CAA→Q, TTT→F)
        """
        feature = self._fwd_feature('ATGATGCAATTTAAA')
        # pos=4, ref='TGCAATT' = T(4)G(5)C(6)A(7)A(8)T(9)T(10), alt='T'
        # Deleted bases = GCAATT (6 bases)
        var = VariantCall(chrom='c', pos=4, ref='TGCAATT', alt='T', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(missense) == 1
        assert len(deletion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'M'
        assert m.alt_aa == 'I'

        d = deletion[0]
        assert d.codon_pos == 1
        assert d.ref_aa == 'MQF'
        assert d.alt_aa == 'M'

    # ── negative-strand tests ──────────────────────────────────────────

    def test_inframe_insertion_mid_codon_negative_strand(self) -> None:
        """
        3-nt mid-codon insertion on minus strand.

        Feature: ATGGGGTTT (M G F) on '-' strand.
        Genomic: AAACCCCAT (revcomp of ATGGGGTTT)

        For frame_offset=1 in codon 0, anchor_coding_pos=1.
        On '-' strand: anchor_coding_pos = coding_variant_pos - ref_len.
        For insertion (ref_len=1): coding_variant_pos = 2.
        Genomic pos for cds_pos=2: (9-1) - 2 = 6 (C in genomic).

        Insert 'CAA' in coding orientation → genomic insertion = revcomp('CAA') = 'TTG'
        VCF: pos=6, ref='C', alt='CTTG'

        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Inserted bases (coding) = CAA
        Query anchor codon = AT + C = ATC → I
        → start_lost: M→I at codon 0 (start codon disrupted)
        → Insertion: ref_aa=M, alt_aa=MK (payload: AA + displaced G = AAG → K)
        """
        feature = self._rev_feature()
        var = VariantCall(chrom='c', pos=6, ref='C', alt='CTTG', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        anchor_change = [a for a in results if a.consequence == 'start_lost']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(anchor_change) == 1
        assert len(insertion) == 1

        m = anchor_change[0]
        assert m.codon_pos == 0
        assert m.ref_aa == 'M'
        assert m.alt_aa == 'I'

        i = insertion[0]
        assert i.codon_pos == 0
        assert i.ref_aa == 'M'
        assert i.alt_aa == 'MK'

    def test_inframe_deletion_mid_codon_negative_strand(self) -> None:
        """
        3-nt mid-codon deletion on minus strand.

        Feature: ATGGGGTTT (M G F) on '-' strand.
        Genomic: AAACCCCAT

        For frame_offset=1 in codon 0, anchor_coding_pos=1.
        On '-' strand: anchor_coding_pos = coding_variant_pos - ref_len.
        For deletion (ref_len=4): coding_variant_pos = 5.
        Genomic pos for cds_pos=5: (9-1) - 5 = 3 (C in genomic).

        VCF: pos=3, ref='CCCC', alt='C'
        Genomic at pos 3,4,5,6 = C,C,C,C → deleted CCC at pos 4,5,6
        Deleted bases (coding) = revcomp('CCC') = 'GGG'

        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Query anchor codon = AT + G(5) = ATG → M (synonymous)
        Deleted codons: 1 (GGG → G)
        → Only deletion annotation: ref_aa=MG, alt_aa=M
        """
        feature = self._rev_feature()
        var = VariantCall(chrom='c', pos=3, ref='CCCC', alt='C', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'deletion'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'MG'
        assert ann.alt_aa == 'M'

    # ── edge cases ─────────────────────────────────────────────────────

    def test_mid_codon_insertion_near_cds_end(self) -> None:
        """
        Mid-codon insertion in the last codon still produces valid annotations.

        Feature: ATGGGGTTT (M G F), pos=6, ref='T', alt='TCCC'
        pos=6 is the first base of codon 2 (TTT → F), frame_offset=0
        Anchor codon 2 = TTT → F, preserved=1 (T)
        Inserted bases = CCC
        Query anchor codon = T + CC = TCC → S
        → Missense: F→S at codon 2
        → Insertion: ref_aa=F, alt_aa=FL (payload: C + displaced TT = CTT → L)
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=6, ref='T', alt='TCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(missense) == 1
        assert len(insertion) == 1

        m = missense[0]
        assert m.codon_pos == 2
        assert m.ref_aa == 'F'
        assert m.alt_aa == 'S'

        i = insertion[0]
        assert i.codon_pos == 2
        assert i.ref_aa == 'F'
        assert i.alt_aa == 'FL'

    def test_mid_codon_deletion_near_cds_end(self) -> None:
        """
        Mid-codon deletion in the penultimate codon.

        Feature: ATGGGGTTT (M G F), pos=3, ref='GGGT', alt='G'
        pos=3 is the first base of codon 1 (GGG → G), frame_offset=0
        Anchor codon 1 = GGG → G, preserved=1 (G)
        Deleted bases = GGT (pos 4,5,6)
        Query anchor codon = G + T(7) + T(8) = GTT → V
        → Missense: G→V at codon 1
        → Deletion: ref_aa=GF, alt_aa=G (deleted codon 2: TTT → F)
        """
        feature = self._fwd_feature()
        # pos=3, ref='GGGT' means G(3)G(4)G(5)T(6), alt='G'
        # Deleted bases = GGT (pos 4,5,6)
        var = VariantCall(chrom='c', pos=3, ref='GGGT', alt='G', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(missense) == 1
        assert len(deletion) == 1

        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'G'
        assert m.alt_aa == 'V'

        d = deletion[0]
        assert d.codon_pos == 1
        assert d.ref_aa == 'GF'
        assert d.alt_aa == 'G'

    def test_mid_codon_insertion_with_query_ref_codon(self) -> None:
        """
        Mid-codon insertion uses query_ref_codon for the anchor AA in both annotations.

        Feature: ATGGGGTTT (M G F), pos=3, ref='G', alt='GCCC'
        Internal codon 1 = GGG → G; query_ref_codon = 'AGG' → R
        The missense annotation uses query_ref_codon for ref_codon and ref_aa.
        The query anchor codon is reconstructed from the query_ref_codon + insertion.
        The insertion annotation uses the query anchor AA (R) as ref_aa.
        """
        feature = self._fwd_feature()
        var = VariantCall(
            chrom='c', pos=3, ref='G', alt='GCCC',
            allele_freq=0.9, depth=100, query_ref_codon='AGG',
        )
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(missense) == 1
        assert len(insertion) == 1

        # Missense uses query ref codon for ref_codon and ref_aa
        m = missense[0]
        assert m.codon_pos == 1
        assert m.ref_aa == 'R'  # from query AGG → R
        # Query anchor codon from AGG with CC at pos 1,2: ACC → T
        assert m.alt_aa == 'T'
        assert m.ref_codon == 'AGG'  # query ref codon
        assert m.alt_codon == 'ACC'

        # Insertion uses query anchor AA
        i = insertion[0]
        assert i.codon_pos == 1
        assert i.ref_aa == 'R'  # from query AGG → R
        assert i.alt_aa == 'RR'  # R + inserted AA (payload: C + displaced GG = CGG → R)

    def test_mid_codon_deletion_with_query_ref_codon(self) -> None:
        """
        Mid-codon deletion uses query_ref_codon for the anchor AA.

        Feature: ATGGGGTTT (M G F), pos=1, ref='TGGG', alt='T'
        Internal codon 0 = ATG → M; query_ref_codon = 'ATA' → I
        The query anchor codon is reconstructed: AT + G(5) = ATG → M.
        Since I ≠ M, a missense annotation is emitted.
        The deletion annotation uses the query anchor AA (I) as ref_aa.
        """
        feature = self._fwd_feature()
        var = VariantCall(
            chrom='c', pos=1, ref='TGGG', alt='T',
            allele_freq=0.9, depth=100, query_ref_codon='ATA',
        )
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        missense = [a for a in results if a.consequence == 'missense']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(missense) == 1
        assert len(deletion) == 1

        m = missense[0]
        assert m.codon_pos == 0
        assert m.ref_aa == 'I'  # from query ATA → I
        assert m.alt_aa == 'M'  # reconstructed ATG → M
        assert m.ref_codon == 'ATA'
        assert m.alt_codon == 'ATG'

        d = deletion[0]
        assert d.codon_pos == 0
        # Query anchor codon ATA → I, deleted codon GGG → G
        assert d.ref_aa == 'IG'
        assert d.alt_aa == 'I'

    def test_mid_codon_insertion_synonymous_anchor_only_insertion(self) -> None:
        """
        When the anchor codon change is synonymous, only the insertion annotation is emitted.

        Feature: ATGGGGTTT (M G F), pos=1, ref='T', alt='TGGG'
        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Query anchor codon = AT + G = ATG → M (synonymous)
        → Only insertion: ref_aa=M, alt_aa=MG
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=1, ref='T', alt='TGGG', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        # Only insertion, no missense (anchor codon is synonymous)
        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'insertion'
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MG'

    def test_mid_codon_deletion_synonymous_anchor_only_deletion(self) -> None:
        """
        When the anchor codon change is synonymous, only the deletion annotation is emitted.

        Feature: ATGGGGTTT (M G F), pos=1, ref='TGGG', alt='T'
        Anchor codon 0 = ATG → M, preserved=2 (AT)
        Query anchor codon = AT + G(5) = ATG → M (synonymous)
        → Only deletion: ref_aa=MG, alt_aa=M
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=1, ref='TGGG', alt='T', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        # Only deletion, no missense (anchor codon is synonymous)
        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'deletion'
        assert ann.ref_aa == 'MG'
        assert ann.alt_aa == 'M'

    def test_mid_codon_insertion_start_lost(self) -> None:
        """
        Mid-codon insertion at codon 0 where anchor codon change loses the start codon.

        Feature: ATGAAAGGG (M K G), pos=0, ref='A', alt='ATAA'
        pos=0 is the first base of codon 0 (ATG → M), frame_offset=0
        Anchor codon 0 = ATG → M, preserved=1 (A)
        Inserted bases = TAA
        Query anchor codon = A + TA = ATA → I
        → start_lost: M→I at codon 0
        → Insertion: ref_aa=M, alt_aa=MM (payload: A + displaced TG = ATG → M)
        """
        feature = self._fwd_feature('ATGAAAGGG')
        var = VariantCall(chrom='c', pos=0, ref='A', alt='ATAA', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        start_lost = [a for a in results if a.consequence == 'start_lost']
        insertion = [a for a in results if a.consequence == 'insertion']
        assert len(start_lost) == 1
        assert len(insertion) == 1

        s = start_lost[0]
        assert s.codon_pos == 0
        assert s.ref_aa == 'M'
        assert s.alt_aa == 'I'

        i = insertion[0]
        assert i.codon_pos == 0
        assert i.ref_aa == 'M'
        assert i.alt_aa == 'MM'

    def test_mid_codon_deletion_start_lost(self) -> None:
        """
        Mid-codon deletion at codon 0 where anchor codon change loses the start codon.

        Feature: ATGAAAGGG (M K G), pos=0, ref='ATGA', alt='A'
        pos=0, frame_offset=0, preserved=1 (A)
        Deleted bases = TGA
        Query anchor codon = A + A(4) + A(5) = AAA → K
        → start_lost: M→K at codon 0
        → Deletion: ref_aa=MK, alt_aa=M (deleted codon 1: AAA → K)
        """
        feature = self._fwd_feature('ATGAAAGGG')
        var = VariantCall(chrom='c', pos=0, ref='ATGA', alt='A', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        start_lost = [a for a in results if a.consequence == 'start_lost']
        deletion = [a for a in results if a.consequence == 'deletion']
        assert len(start_lost) == 1
        assert len(deletion) == 1

        s = start_lost[0]
        assert s.codon_pos == 0
        assert s.ref_aa == 'M'
        assert s.alt_aa == 'K'

        d = deletion[0]
        assert d.codon_pos == 0
        assert d.ref_aa == 'MK'
        assert d.alt_aa == 'M'

    def test_both_split_annotations_share_same_variant(self) -> None:
        """
        Both annotations from a split mid-codon indel reference the same VariantCall.
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='G', alt='GCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        # Both annotations must reference the same variant object
        assert results[0].variant is var
        assert results[1].variant is var

    def test_both_split_annotations_share_same_codon_pos(self) -> None:
        """
        Both annotations from a split mid-codon indel have the same codon_pos.
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='G', alt='GCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        assert len(results) == 2
        assert results[0].codon_pos == results[1].codon_pos
        assert results[0].codon_pos == 1

    def test_split_annotations_not_combined_snp_event(self) -> None:
        """
        Split mid-codon indel annotations are NOT marked as combined SNP events.
        """
        feature = self._fwd_feature()
        var = VariantCall(chrom='c', pos=3, ref='G', alt='GCCC', allele_freq=0.9, depth=100)
        results = annotate_variants([var], [feature])

        for ann in results:
            assert ann.is_combined_codon_event is False

    def test_snp_with_gapped_query_ref_codon_uses_internal_cds(self) -> None:
        """
        When a SNP shares a codon with a deletion, query_ref_codon may contain gaps.
        The SNP should fall back to the internal CDS codon for alt_aa computation.

        Feature: ATGCAAGTTTAA (M Q V *)
        Codon 1 = CAA → Q
        SNP at position 3 (first base of codon 1): C→G
        Gapped query_ref_codon = 'G--' (positions 4,5 are gaps from deletion)
        With fix: query_ref_codon is cleared, falls back to internal CAA
        alt_codon = GAA → E, so Q1E (missense)
        """
        feature = self._fwd_feature('ATGCAAGTTTAA')
        var = VariantCall(
            chrom='c', pos=3, ref='C', alt='G',
            allele_freq=0.9, depth=100,
            query_ref_codon='G--',
        )
        results = annotate_variants([var], [feature])
        assert len(results) == 1
        ann = results[0]
        assert ann.consequence == 'missense'
        assert ann.ref_aa == 'Q'
        assert ann.alt_aa == 'E'
        assert ann.codon_pos == 1


# ─── Combined codon event display fields ─────────────────────────────

class TestCombinedCodonEventDisplayFields:
    """
    Verify that AnnotatedVariant fields used by display logic are set correctly
    for combined codon events (multiple SNPs within one codon).
    """

    @staticmethod
    def _fwd_feature(seq: str = 'ATGTCTAAAAAA') -> FeatureRecord:
        """Forward-strand feature with sensible default (M S K K)."""
        return FeatureRecord(
            id=1, reference_id=1, name='testf', protein='TestF',
            start=0, end=12, strand='+', codon_start=0,
            nt_sequence=seq,
        )

    def test_combined_snp_codon_sets_flag_and_count(self) -> None:
        """
        Two SNPs in the same codon produce one combined AnnotatedVariant
        with is_combined_codon_event=True and combined_member_count=2.
        """
        # Feature: ATG TCT AAA AAA (M S K K)
        # Codon 1 (0-based) = TCT → S
        # Two SNPs at positions 3 and 5: T→A, T→G
        # Together: TCT → AGC (S → S, synonymous)
        feature = self._fwd_feature()
        variants = [
            VariantCall(chrom='c', pos=3, ref='T', alt='A', allele_freq=0.95, depth=100),
            VariantCall(chrom='c', pos=5, ref='T', alt='G', allele_freq=0.95, depth=100),
        ]
        results = annotate_variants(variants, [feature])
        assert len(results) == 1
        ann = results[0]
        assert ann.is_combined_codon_event is True
        assert ann.combined_member_count == 2

    def test_combined_snp_codon_has_codon_level_fields(self) -> None:
        """
        The combined annotation carries full ref_codon and alt_codon
        so that display code can format codon-level nt_change (e.g. TCT2ACG).

        Feature: ATG TCT AAA AAA (M S K K)
        Codon 1 (0-based) = TCT → S
        SNP at pos 3 (1st base): T→A → codon becomes ACT
        SNP at pos 5 (3rd base): T→G → codon becomes ACG
        Combined: TCT → ACG (S → T, missense)
        """
        feature = self._fwd_feature()
        variants = [
            VariantCall(chrom='c', pos=3, ref='T', alt='A', allele_freq=0.95, depth=100),
            VariantCall(chrom='c', pos=5, ref='T', alt='G', allele_freq=0.95, depth=100),
        ]
        results = annotate_variants(variants, [feature])
        ann = results[0]
        assert ann.ref_codon == 'TCT'
        assert ann.alt_codon == 'ACG'
        assert ann.codon_pos == 1
        # Display logic uses: f'{ann.ref_codon}{ann.codon_pos + 1}{ann.alt_codon}'
        # This would produce 'TCT2ACG'
        assert f'{ann.ref_codon}{ann.codon_pos + 1}{ann.alt_codon}' == 'TCT2ACG'

    def test_single_snp_not_combined(self) -> None:
        """A single SNP in a codon is NOT a combined event."""
        feature = self._fwd_feature()
        variants = [
            VariantCall(chrom='c', pos=3, ref='T', alt='A', allele_freq=0.95, depth=100),
        ]
        results = annotate_variants(variants, [feature])
        assert len(results) == 1
        ann = results[0]
        assert ann.is_combined_codon_event is False
        assert ann.combined_member_count == 1
