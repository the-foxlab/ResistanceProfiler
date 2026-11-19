"""
Reference reconciliation tests — baseline emission, call overlay, source labels.

Expectations are derived independently: each case states the supplied reference,
the VCF calls, and the expected internal-reference alleles/frequencies from the
TODO reconciliation tables (built by applying the calls to the supplied sequence
and diffing against the internal reference), never from the implementation.
Positions in comments are 1-based VCF-style; VariantCall positions are 0-based.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
from _sequence_oracle import (
    V1_FRAME_OFFSET_CASES,
    V3_BLOCK_SUB_CASES,
    V3_MULTIALLELIC_BASELINE_CASE,
    OracleFeatureSpec,
    OracleHaplotype,
    apply_sequence_edits,
    derive_fixed_coding_haplotype_effect,
    oracle_codon_mixture_frequencies,
    reverse_complement_dna,
)
from Bio.Seq import Seq
from conftest import TINY_REF_NAME, TINY_REF_SEQ, make_profiling_result

from respro.core.annotation import annotate_variants
from respro.core.vcf_to_reference import (
    REFERENCE_DERIVED,
    VCF_DERIVED,
    reconcile_variants,
    reconcile_variants_for_matches,
)
from respro.db.models import (
    AnnotatedVariant,
    FeatureMatch,
    FeatureRecord,
    VariantCall,
)
from respro.db.results import load_run, reconstruct_annotations
from respro.db.schema import create_schema, init_results_db


def _feature(internal: str, *, strand: str = '+', feature_id: int = 1) -> FeatureRecord:
    return FeatureRecord(
        id=feature_id,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=len(internal),
        strand=strand,
        codon_start=0,
        nt_sequence=internal,
    )


def _match(
    feature: FeatureRecord,
    supplied: str,
    cigar: str,
    *,
    strand: str = '+',
) -> FeatureMatch:
    return FeatureMatch(
        feature=feature,
        identity=0.99,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=len(supplied),
        strand=strand,
        cigar=cigar,
    )


def _snp(pos: int, ref: str, alt: str, af: float = 1.0) -> VariantCall:
    return VariantCall(chrom='ref', pos=pos, ref=ref, alt=alt, allele_freq=af, depth=50)


def _tokens(out: list[VariantCall]) -> list[tuple]:
    return sorted(
        (v.pos, v.ref, v.alt, round(v.allele_freq, 3), v.source) for v in out
    )


def _run_plus(
    internal: str,
    supplied: str,
    cigar: str,
    calls: list[VariantCall],
) -> tuple[list[tuple], list[str]]:
    """Reconcile on a '+'-strand feature and return (tokens, warnings)."""
    out, warnings = reconcile_variants_for_matches(
        calls, [_match(_feature(internal), supplied, cigar)], supplied,
    )
    return _tokens(out), warnings


class TestTier0IdenticalReferences:
    """Identical supplied/internal references: reconciliation is a no-op baseline."""

    def test_snp_call_emits_legacy_remap_output(self) -> None:
        out, warnings = _run_plus('ACGTACGT', 'ACGTACGT', '8M', [_snp(1, 'C', 'G')])
        assert out == [(1, 'C', 'G', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_no_calls_emit_no_events(self) -> None:
        out, warnings = _run_plus('ACGTACGT', 'ACGTACGT', '8M', [])
        assert out == []
        assert warnings == []


class TestMainReconciliationTable:
    """The main TODO examples table (single-nucleotide position)."""

    def test_baseline_only_emits_reference_derived_at_af_1(self) -> None:
        # internal A, supplied G, no call -> A>G at 1.0 (assumption)
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_full_reversion_emits_no_variant(self) -> None:
        # internal A, supplied G, G>A at 1.0 -> no variant
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'A')])
        assert out == []
        assert warnings == []

    def test_partial_reversion_keeps_call_frequency(self) -> None:
        # internal A, supplied G, G>A at 0.5 -> A>G at 0.5
        out, _ = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'A', af=0.5)])
        assert out == [(0, 'A', 'G', 0.5, VCF_DERIVED)]

    def test_multi_allelic_residual_frequencies(self) -> None:
        # internal A, supplied G, G>C at 0.3 -> A>G at 0.7 and A>C at 0.3
        out, _ = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'C', af=0.3)])
        assert out == [(0, 'A', 'C', 0.3, VCF_DERIVED), (0, 'A', 'G', 0.7, VCF_DERIVED)]

    def test_below_cutoff_call_reverts_to_pure_baseline(self) -> None:
        # The CLI removes calls below min-AF before reconciliation; the position
        # then reverts to the pure baseline at AF 1.0.
        # internal A, supplied G, G>A at 0.1 with min_af=0.5 -> A>G at 1.0
        raw_calls = [_snp(0, 'G', 'A', af=0.1)]
        min_af = 0.5
        in_cutoff = [v for v in raw_calls if v.allele_freq >= min_af]
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', in_cutoff)
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_length_changing_replacement_baseline_only(self) -> None:
        # Table 1 Row 6: internal A vs supplied GG (with flanking C..TT), no call
        # Alignment walk decomposes A->GG into substitution A>G + insertion A>AG at 1.0.
        out, warnings = _run_plus('CATT', 'CGGTT', '2M1I2M', [])
        assert out == [
            (1, 'A', 'AG', 1.0, REFERENCE_DERIVED),
            (1, 'A', 'G', 1.0, REFERENCE_DERIVED),
        ]
        assert warnings == []

    def test_length_changing_replacement_full_deletion_counter(self) -> None:
        # Table 1 Row 7: internal A vs supplied GG, call G2del at 1.0 -> A>G at 1.0 (vcf_derived)
        raw, warnings = reconcile_variants_for_matches(
            [_snp(1, 'GG', 'G', af=1.0)],
            [_match(_feature('CATT'), 'CGGTT', '2M1I2M')],
            'CGGTT',
        )
        assert warnings == []
        assert _tokens(raw) == [(1, 'A', 'G', 1.0, VCF_DERIVED)]
        assert raw[0].user_pos == 1
        assert raw[0].user_ref == 'GG'
        assert raw[0].user_alt == 'G'

    def test_length_changing_replacement_partial_deletion_counter(self) -> None:
        # Table 1 Row 8: internal A vs supplied GG, call G2del at 0.5 ->
        # A>G at 0.5 (call state) + residual A->GG at 0.5 (A>G@0.5 + A>AG@0.5),
        # aggregated to A>G@1.0 and A>AG@0.5, both vcf_derived.
        out, warnings = _run_plus(
            'CATT', 'CGGTT', '2M1I2M', [_snp(1, 'GG', 'G', af=0.5)],
        )
        assert warnings == []
        assert out == [
            (1, 'A', 'AG', 0.5, VCF_DERIVED),
            (1, 'A', 'G', 1.0, VCF_DERIVED),
        ]


class TestIndelInteractions:
    """The non-canceling indel interaction table (Tier 2 local windows)."""

    def test_exact_cancellation_at_full_frequency(self) -> None:
        # internal ACGT, supplied ACGGT (G inserted before T), call deletes one G
        out, warnings = _run_plus(
            'ACGT', 'ACGGT', '3M1I1M', [_snp(2, 'GG', 'G')],
        )
        assert out == []
        assert warnings == []

    def test_partial_cancellation_emits_residual_state(self) -> None:
        # same at AF 0.5: per-allele state cancels, residual keeps the insertion
        out, _ = _run_plus('ACGT', 'ACGGT', '3M1I1M', [_snp(2, 'GG', 'G', af=0.5)])
        assert out == [(2, 'G', 'GG', 0.5, VCF_DERIVED)]

    def test_adjacent_deletions_merge(self) -> None:
        # internal ACGT, supplied ACT (G deleted), call deletes supplied T:
        # sample ACT - T = AC -> net deletion of internal GT (anchored C2>CGT)
        out, _ = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(1, 'CT', 'C')])
        assert out == [(1, 'CGT', 'C', 1.0, VCF_DERIVED)]

    def test_repeat_separated_deletions_merge(self) -> None:
        # Table 2 Row 2: internal ACGTT, supplied ACTT (G3del), call T4del@1.0
        # (0-based pos 2 TT>T in ACTT): sample ACT -> net GT3del@1.0 (0-based pos 1 CGT>C).
        out, warnings = _run_plus('ACGTT', 'ACTT', '2M1D2M', [_snp(2, 'TT', 'T', af=1.0)])
        assert warnings == []
        assert out == [(1, 'CGT', 'C', 1.0, VCF_DERIVED)]
        applied = 'ACGTT'[:1] + 'C' + 'ACGTT'[4:]
        assert applied == 'ACT'

    def test_insertion_counters_baseline_deletion_with_net_insertion(self) -> None:
        # Table 2 Row 4: internal ACGT, supplied ACT (G3del), call C2>CGG@1.0
        # (0-based pos 1 C>CGG): sample ACGGT -> net C2>CG@1.0 (0-based pos 1 C>CG).
        out, warnings = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(1, 'C', 'CGG', af=1.0)])
        assert warnings == []
        assert out == [(1, 'C', 'CG', 1.0, VCF_DERIVED)]
        applied = 'ACGT'[:1] + 'CG' + 'ACGT'[2:]
        assert applied == 'ACGGT'

    def test_partial_af_insertion_countering_baseline_deletion(self) -> None:
        # Table 2 Row 5: internal ACGT, supplied ACT (G3del), call C2>CGG@0.4 ->
        # per-allele C2>CG@0.4 and residual G3del@0.6, both vcf_derived.
        out, warnings = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(1, 'C', 'CGG', af=0.4)])
        assert warnings == []
        assert out == [
            (1, 'C', 'CG', 0.4, VCF_DERIVED),
            (1, 'CG', 'C', 0.6, VCF_DERIVED),
        ]

    def test_multi_base_insertion_filling_deleted_column_preserves_sequence_order(self) -> None:
        # Internal ACGT, supplied ACT (G deleted at col 2), call C2>CGA@1.0
        # inserting 'GA' after 'C' -> sample is ACGAT (not ACAGT), net G3>GA@1.0.
        out, warnings = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(1, 'C', 'CGA', af=1.0)])
        assert warnings == []
        assert out == [(2, 'G', 'GA', 1.0, VCF_DERIVED)]
        applied = 'ACGT'[:2] + 'GA' + 'ACGT'[3:]
        assert applied == 'ACGAT'

    def test_multi_base_baseline_deletion_cancelled_by_insertion(self) -> None:
        # Internal ACGTT, supplied AT (3-bp deletion of CGT at cols 1-3),
        # call A1>ACGT@1.0 restores CGT -> sample ACGTT, no net variants.
        out, warnings = _run_plus('ACGTT', 'AT', '1M3D1M', [_snp(0, 'A', 'ACGT', af=1.0)])
        assert warnings == []
        assert out == []

    def test_compatible_overlapping_insertion_and_deletion_records_are_unverified_by_policy(
        self,
    ) -> None:
        # Table 2 Row 8: internal ACGT, supplied ACGT, separate records C2>CA@1.0
        # (0-based pos 1 C>CA) and C2del@1.0 (0-based pos 0 AC>A). Literal edits
        # would be compatible, but overlapping spans without phase are unverified.
        calls = [
            VariantCall(
                chrom='ref', pos=1, ref='C', alt='CA',
                allele_freq=1.0, depth=50, record_index=0,
            ),
            VariantCall(
                chrom='ref', pos=0, ref='AC', alt='A',
                allele_freq=1.0, depth=50, record_index=1,
            ),
        ]
        out, warnings = _run_plus('ACGT', 'ACGT', '4M', calls)
        assert out == []
        assert any('Overlapping VCF records' in w for w in warnings)

    def test_insertions_merge_into_one(self) -> None:
        # internal AT, supplied ACGT (CG inserted), call inserts G after C:
        # sample ACGGT -> net insertion of CGG after A (anchored A1>ACGG)
        out, _ = _run_plus('AT', 'ACGT', '1M2I1M', [_snp(1, 'C', 'CG')])
        assert out == [(0, 'A', 'ACGG', 1.0, VCF_DERIVED)]

    def test_baseline_deletion_plus_call_deletion_merge(self) -> None:
        # internal ACGT, supplied ACT (G deleted), call deletes supplied C:
        # sample AT -> net deletion of internal CG (anchored A1>ACG)
        out, _ = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(0, 'AC', 'A')])
        assert out == [(0, 'ACG', 'A', 1.0, VCF_DERIVED)]

    def test_non_overlapping_separate_records_emit_independently(self) -> None:
        # identical references, C2del and C6del: two independent events
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'CG', 'C'), _snp(5, 'CG', 'C')],
        )
        assert out == [(1, 'CG', 'C', 1.0, VCF_DERIVED), (5, 'CG', 'C', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_incompatible_alleles_at_one_position_are_unverified(self) -> None:
        # identical references, C>G and C>A at the same position (AF mass 2.0)
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'C', 'G'), _snp(1, 'C', 'A')],
        )
        assert out == []
        assert any('allele frequencies sum' in w for w in warnings)

    def test_partially_overlapping_separate_records_are_unverified(self) -> None:
        # identical references: record A spans cols 1-2 (CG>CA), record B col 2
        # (G>A); spans overlap with combined AF 1.2 -> unverified, baseline kept
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'CG', 'CA', af=0.6), _snp(2, 'G', 'A', af=0.6)],
        )
        assert out == []
        assert any('Overlapping VCF records' in w for w in warnings)

    def test_ref_mismatch_skips_call_and_keeps_baseline(self) -> None:
        # REF block does not match the supplied reference
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'T', 'A')])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert any('does not match the supplied reference' in w for w in warnings)

    def test_multiple_ref_mismatches_emit_aggregate_warning(self) -> None:
        # two positions whose VCF REF disagrees with the supplied reference:
        # per-position details plus one aggregate diagnosis
        out, warnings = _run_plus(
            'ACGT', 'GCGA', '4M',
            [_snp(0, 'T', 'A'), _snp(3, 'C', 'G')],
        )
        details = [w for w in warnings if 'does not match the supplied reference' in w]
        assert len(details) == 2
        assert any('probably called against a different reference' in w for w in warnings)

    def test_single_ref_mismatch_has_no_aggregate_warning(self) -> None:
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'T', 'A')])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert not any('probably called against a different reference' in w for w in warnings)

    def test_call_anchored_on_inserted_base_applies_to_pending(self) -> None:
        # internal AT, supplied ACGT (CG inserted); the call substitutes the
        # second inserted base (a pending slot): sample coding A[CA]T -> A>ACA
        out, warnings = _run_plus('AT', 'ACGT', '1M2I1M', [_snp(2, 'G', 'A')])
        assert out == [(0, 'A', 'ACA', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_call_outside_aligned_region_is_skipped(self) -> None:
        # call anchored beyond the match's aligned region: the REF block cannot
        # match the (empty) supplied slice, so the call is skipped and the
        # baseline is retained
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(10, 'A', 'G')])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert any('skipped' in w for w in warnings)


class TestReverseStrandOrientation:
    """Minus-strand features: both coding effect and genomic allele orientation.

    The supplied reference maps reverse complement against the feature; each
    expectation is derived by applying the edit to the supplied sequence and
    reverse-complementing into coding orientation.
    """

    @staticmethod
    def _minus_feature(internal_coding: str) -> FeatureRecord:
        return _feature(internal_coding, strand='-')

    def test_identical_rc_pair_yields_no_baseline_events(self) -> None:
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), genomic, '8M', strand='-')],
            genomic,
        )
        assert _tokens(out) == []
        assert warnings == []

    def test_minus_feature_baseline_snp_genomic_orientation(self) -> None:
        # internal coding ACGGTACG, genomic CGTACCGT; supplied genomic pos 2
        # T>A -> coding col 6 G>T; emitted genomic allele T>A at pos 2
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:2] + ('A' if genomic[2] != 'A' else 'C') + genomic[3:]
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), supplied, '8M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(2, genomic[2], supplied[2], 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_minus_feature_partial_reversion(self) -> None:
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:2] + ('A' if genomic[2] != 'A' else 'C') + genomic[3:]
        call = _snp(2, supplied[2], genomic[2], af=0.5)
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '8M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(2, genomic[2], supplied[2], 0.5, VCF_DERIVED)]

    def test_minus_feature_terminal_deletion_lands_at_coding_start(self) -> None:
        # deleting the forward-LAST supplied base deletes the coding-FIRST base;
        # the emitted genomic deletion anchors at the correct end (guards the
        # past reversed-CIGAR indel bug)
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:-1]
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), supplied, '1D7M', strand='-')],
            supplied,
        )
        # genomic ref CGTACCGT: deleted base is pos 7 (T); anchored at pos 6
        assert _tokens(out) == [(6, 'GT', 'G', 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_minus_feature_merged_insertion(self) -> None:
        # baseline insertion (forward C after genomic pos 3) + call inserting
        # another C -> one merged insertion A>ACC
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + 'C' + genomic[4:]
        call = _snp(4, 'C', 'CC')
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '4M1I4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(3, 'A', 'ACC', 1.0, VCF_DERIVED)]

    def test_minus_feature_full_cancellation(self) -> None:
        # baseline deletion of genomic pos 4 fully reverted by the call at 1.0
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + genomic[5:]
        call = _snp(3, 'A', 'AC')
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '3M1D4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == []

    def test_minus_feature_partial_reversion_of_deletion(self) -> None:
        # same at AF 0.5: the deletion survives at residual 0.5
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + genomic[5:]
        call = _snp(3, 'A', 'AC', af=0.5)
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '3M1D4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(3, 'AC', 'A', 0.5, VCF_DERIVED)]

    def test_table3_row1_exact_rc_pair_yields_zero_events(self) -> None:
        # Table 3 Row 1: coding internal AAA, supplied TTT (RC-mapped), no call -> none
        for feat_strand in ('+', '-'):
            feat = _feature('AAA', strand=feat_strand)
            out, warnings = reconcile_variants_for_matches(
                [], [_match(feat, 'TTT', '3M', strand='-')], 'TTT',
            )
            assert out == []
            assert warnings == []
            assert annotate_variants(out, [feat]) == []

    def test_table3_row2_rc_baseline_snp_coding_and_genomic_orientation(self) -> None:
        # Table 3 Row 2: coding internal AAA (K), supplied TCT (RC is AGA -> R).
        # Coding effect is A2>G@1.0 (K1R) for both feature strands; emitted genomic
        # allele is A>G on '+' feature and T>C on '-' feature at 0-based pos 1.
        for feat_strand, expected_ref, expected_alt in (('+', 'A', 'G'), ('-', 'T', 'C')):
            feat = _feature('AAA', strand=feat_strand)
            out, warnings = reconcile_variants_for_matches(
                [], [_match(feat, 'TCT', '3M', strand='-')], 'TCT',
            )
            assert warnings == []
            assert _tokens(out) == [(1, expected_ref, expected_alt, 1.0, REFERENCE_DERIVED)]
            anns = annotate_variants(out, [feat])
            assert len(anns) == 1
            assert (anns[0].ref_codon, anns[0].alt_codon, anns[0].ref_aa, anns[0].alt_aa) == (
                'AAA', 'AGA', 'K', 'R',
            )

    def test_table3_row3_rc_called_snp_coding_and_genomic_orientation(self) -> None:
        # Table 3 Row 3: coding internal AAA (K), supplied TTT (RC-mapped),
        # call T2>C@0.5 (0-based pos 1 T>C) -> coding A2>G@0.5 (K1R).
        call = _snp(1, 'T', 'C', af=0.5)
        for feat_strand, expected_ref, expected_alt in (('+', 'A', 'G'), ('-', 'T', 'C')):
            feat = _feature('AAA', strand=feat_strand)
            out, warnings = reconcile_variants_for_matches(
                [call], [_match(feat, 'TTT', '3M', strand='-')], 'TTT',
            )
            assert warnings == []
            assert _tokens(out) == [(1, expected_ref, expected_alt, 0.5, VCF_DERIVED)]
            anns = annotate_variants(out, [feat])
            assert len(anns) == 1
            assert (anns[0].ref_codon, anns[0].alt_codon, anns[0].ref_aa, anns[0].alt_aa) == (
                'AAA', 'AGA', 'K', 'R',
            )

    def test_table3_row4_palindromic_rc_terminal_deletion_lands_at_coding_start(self) -> None:
        # Table 3 Row 4: coding internal ACGT vs supplied ACGT (palindromic, RC-mapped),
        # call T4del@1.0 (0-based pos 2 GT>G deletes last supplied base T -> sample ACG,
        # RC(ACG) = CGT -> coding first base A deleted).
        feat_minus = _feature('ACGT', strand='-')
        out_minus, warnings = reconcile_variants_for_matches(
            [_snp(2, 'GT', 'G', af=1.0)],
            [_match(feat_minus, 'ACGT', '4M', strand='-')],
            'ACGT',
        )
        assert warnings == []
        # On '-' feature, internal genomic is RC(ACGT) = ACGT; deleting coding pos 0
        # (genomic pos 3 'T') anchors at genomic pos 2 ('G'): ref='GT', alt='G'.
        assert _tokens(out_minus) == [(2, 'GT', 'G', 1.0, VCF_DERIVED)]

        # With a leading codon (coding ATGACGTAA, 3 codons), deleting the 'A' of ACGT
        # (coding idx 3) is representable and annotatable on both '+' and '-' features.
        internal_9 = 'ATGACGTAA'
        supplied_9 = str(Seq(internal_9).reverse_complement())  # TTACGTCAT
        # In TTACGTCAT, the ACGT locus is at forward indices 2..5 (ACGT); deleting
        # its last base T (forward pos 5, anchored at pos 4 GT>G) deletes coding idx 3 ('A').
        call_9 = _snp(4, 'GT', 'G', af=1.0)
        for feat_strand, exp_pos, exp_ref, exp_alt in (
            ('+', 2, 'GA', 'G'),
            ('-', 4, 'GT', 'G'),
        ):
            feat = _feature(internal_9, strand=feat_strand)
            out, warn = reconcile_variants_for_matches(
                [call_9], [_match(feat, supplied_9, '9M', strand='-')], supplied_9,
            )
            assert warn == []
            assert _tokens(out) == [(exp_pos, exp_ref, exp_alt, 1.0, VCF_DERIVED)]
            anns = annotate_variants(out, [feat])
            assert len(anns) == 1
            assert anns[0].consequence == 'frameshift'

    def test_table3_row5_non_palindromic_rc_deletion_coding_and_genomic(self) -> None:
        # Table 3 Row 5: coding internal ACG (with trailing codon AAA -> ACGAAA, T K),
        # supplied at locus is CGT (full supplied TTT + CGT = TTTCGT, RC = ACGAAA).
        # Call G2del@1.0 on the CGT locus (forward pos 3..5: pos 3 CG>C deletes middle G
        # of CGT -> sample CT, RC(CT) = AG -> coding C2del@1.0).
        internal = 'ACGAAA'
        supplied = 'TTTCGT'  # RC(ACGAAA)
        call = _snp(3, 'CG', 'C', af=1.0)
        for feat_strand, exp_pos, exp_ref, exp_alt in (
            ('+', 0, 'AC', 'A'),
            ('-', 3, 'CG', 'C'),
        ):
            feat = _feature(internal, strand=feat_strand)
            out, warnings = reconcile_variants_for_matches(
                [call], [_match(feat, supplied, '6M', strand='-')], supplied,
            )
            assert warnings == []
            assert _tokens(out) == [(exp_pos, exp_ref, exp_alt, 1.0, VCF_DERIVED)]
            anns = annotate_variants(out, [feat])
            assert len(anns) == 1
            assert (anns[0].ref_codon, anns[0].ref_aa, anns[0].alt_aa, anns[0].consequence) == (
                'ACG', 'T', 'TfsX', 'frameshift',
            )

    @pytest.mark.parametrize('match_strand', ['+', '-'])
    @pytest.mark.parametrize('feature_strand', ['+', '-'])
    def test_four_way_match_and_feature_strand_combinations(
        self, match_strand: str, feature_strand: str,
    ) -> None:
        # Independent oracle verification across all 4 (match.strand, feature.strand)
        # combinations: coding internal is ATGAAAGGG (M K G), coding sample has
        # baseline A>C at coding idx 4 (codon 1 ACA -> T) and a called 1-bp deletion
        # at coding idx 7 (middle G of codon 2 GGG -> frameshift GfsX).
        internal_coding = 'ATGAAAGGG'
        supplied_coding = 'ATGACAGGG'  # baseline A>C at coding idx 4
        sample_coding = 'ATGACAGG'     # plus deletion of coding idx 7 ('G')

        internal_genomic = (
            internal_coding
            if feature_strand == '+'
            else str(Seq(internal_coding).reverse_complement())
        )
        supplied_seq = (
            supplied_coding
            if match_strand == '+'
            else str(Seq(supplied_coding).reverse_complement())
        )
        expected_sample_genomic = (
            sample_coding
            if feature_strand == '+'
            else str(Seq(sample_coding).reverse_complement())
        )

        # In supplied_seq, delete the middle base of the GGG/CCC codon:
        if match_strand == '+':
            # supplied_seq = ATGACAGGG; delete pos 7 ('G'), anchored at pos 6 ('GG' -> 'G')
            call = _snp(6, 'GG', 'G', af=1.0)
        else:
            # supplied_seq = CCCTGTCAT; coding idx 7 maps to forward pos 1 ('C'),
            # anchored at pos 0 ('CC' -> 'C')
            call = _snp(0, 'CC', 'C', af=1.0)

        feat = _feature(internal_coding, strand=feature_strand)
        match = _match(feat, supplied_seq, '9M', strand=match_strand)
        out, warnings = reconcile_variants_for_matches([call], [match], supplied_seq)
        assert warnings == []
        assert len(out) == 2

        # Apply emitted genomic VariantCalls (descending pos order) to internal_genomic:
        reconstructed = internal_genomic
        for var in sorted(out, key=lambda v: v.pos, reverse=True):
            assert reconstructed[var.pos:var.pos + len(var.ref)] == var.ref
            reconstructed = (
                reconstructed[:var.pos] + var.alt + reconstructed[var.pos + len(var.ref):]
            )
        assert reconstructed == expected_sample_genomic

        # Assert coding-orientation annotations match the oracle directly:
        anns = annotate_variants(out, [feat])
        by_codon = {a.codon_pos: a for a in anns}
        assert set(by_codon) == {1, 2}
        assert (
            by_codon[1].ref_codon,
            by_codon[1].alt_codon,
            by_codon[1].ref_aa,
            by_codon[1].alt_aa,
            by_codon[1].consequence,
        ) == ('AAA', 'ACA', 'K', 'T', 'missense')
        assert (
            by_codon[2].ref_codon,
            by_codon[2].ref_aa,
            by_codon[2].alt_aa,
            by_codon[2].consequence,
        ) == ('GGG', 'G', 'GfsX', 'frameshift')


class TestRouting:
    """reconcile_variants routes per CHROM and restores the VCF CHROM."""

    def test_chrom_restored_and_dropped_chroms_reported(self) -> None:
        from respro.core.query import QueryRecord

        internal = 'ACGT'
        feature = _feature(internal)
        supplied = 'GCGT'
        match = _match(feature, supplied, '4M')
        record = QueryRecord(query_name='ref', query_sequence=supplied, feature_matches=[match])

        out, warnings, dropped = reconcile_variants([_snp(0, 'G', 'T')], [record])
        assert [(v.pos, v.ref, v.alt, v.source) for v in out] == [(0, 'A', 'T', VCF_DERIVED)]
        assert out[0].chrom == 'ref'
        assert dropped == []

        ghost = dc_replace(_snp(0, 'G', 'T'), chrom='ghost')
        out2, _, dropped2 = reconcile_variants([_snp(0, 'G', 'T'), ghost], [record])
        assert [(v.pos, v.ref, v.alt, v.source) for v in out2] == [(0, 'A', 'T', VCF_DERIVED)]
        assert dropped2 == ['ghost']

    def test_no_usable_chrom_raises(self) -> None:
        from respro.core.query import QueryRecord

        with pytest.raises(ValueError, match='No VCF CHROM matches'):
            reconcile_variants(
                [_snp(0, 'G', 'A')],
                [QueryRecord(query_name='other', query_sequence='ACGT', feature_matches=[])],
            )


class TestSourcePersistence:
    """The source label survives the results-DB round trip (R2)."""

    def _result(self, source: str):
        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='ref', pos=4, ref='A', alt='T', allele_freq=1.0, depth=100,
                source=source,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense',
        )
        return make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.vcf',
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )

    def test_variant_source_round_trips_through_results_db(self, tmp_path: Path) -> None:
        import uuid

        from respro.db.results import save_run

        project_db = tmp_path / 'project.db'
        proj_conn = create_schema(project_db)
        proj_conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('T', 6, str(uuid.uuid4())),
        )
        proj_conn.commit()

        results_db = tmp_path / 'results.db'
        conn = init_results_db(results_db)
        run_id = save_run(conn, project_db, proj_conn, self._result(VCF_DERIVED))
        run_row, variant_rows = load_run(conn, run_id)
        anns = reconstruct_annotations(variant_rows)
        assert anns[0].variant.source == VCF_DERIVED
        conn.close()
        proj_conn.close()

    def test_legacy_results_db_gains_variant_source_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'legacy.db'
        # Create a results DB without the variant_source column, then reopen it:
        # the optional-column migration must add it.
        conn = create_schema(db_path)  # project schema, not results — build manually
        conn.close()

        raw = sqlite3.connect(str(db_path))
        raw.executescript(
            """
            CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                project_db_path TEXT DEFAULT '',
                reference_name TEXT DEFAULT '',
                vcf_path TEXT DEFAULT ''
            );
            CREATE TABLE variant_result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                chrom TEXT NOT NULL,
                pos INTEGER NOT NULL,
                ref TEXT NOT NULL,
                alt TEXT NOT NULL
            );
            """
        )
        raw.commit()
        raw.close()

        conn = init_results_db(db_path)
        columns = {
            row['name']
            for row in conn.execute('PRAGMA table_info(variant_result)').fetchall()
        }
        assert 'variant_source' in columns
        conn.close()


class TestVariantSourceSurfacing:
    """The source label surfaces in report rows (R5)."""

    def test_source_label_mapping(self) -> None:
        from respro.report._row_helpers import variant_source_label

        def ann_with(source: str) -> AnnotatedVariant:
            return AnnotatedVariant(
                variant=VariantCall(chrom='c', pos=0, ref='A', alt='G', source=source),
            )

        assert variant_source_label(ann_with(VCF_DERIVED)) == 'VCF derived'
        assert variant_source_label(ann_with(REFERENCE_DERIVED)) == 'Reference derived'
        assert variant_source_label(ann_with('')) == ''

    def test_all_mutations_rows_carry_source_and_flag(self) -> None:
        from respro.report.html import build_report_context

        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='ref', pos=4, ref='A', alt='T', allele_freq=1.0, depth=100,
                source=REFERENCE_DERIVED,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense', af_bin='high',
        )
        result = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.vcf', is_fasta_mode=False,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx['all_mutations']['has_variant_source_column'] is True
        assert ctx['all_mutations']['rows'][0]['variant_source'] == 'Reference derived'

    def test_fasta_mode_hides_variant_source(self) -> None:
        from respro.report.html import build_report_context

        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='gag', pos=4, ref='A', alt='T', allele_freq=1.0),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense', af_bin='high', is_fasta_mode=True,
        )
        result = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.fasta', is_fasta_mode=True,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx['all_mutations']['has_variant_source_column'] is False
        assert ctx['all_mutations']['rows'][0]['variant_source'] == ''


class TestBamAssessabilityMask:
    """Deferred BAM coverage gate (R4): annotate everything, mask, then match."""

    def test_excluded_positions_cover_gap_codons(self) -> None:
        from respro.cli.profile_helpers import _bam_excluded_positions
        from respro.db.models import CoverageGap, ReferenceGroup

        feature = _feature('ATGAAGAAA')
        rg = ReferenceGroup(
            reference_name='ref', reference_id=1, organism='',
            reference_length_nt=9, query_name='ref', query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
            features=[feature], rules=[], formula_rules=[], rule_feature_names=set(),
        )
        # codon 1 (0-based) = nt positions 3-5
        gaps = [CoverageGap(feature_name='gag', codon_start=1, codon_end=1, chrom='ref')]
        excluded = _bam_excluded_positions(rg, gaps)
        assert excluded == {('ref', 3), ('ref', 4), ('ref', 5)}

    def test_uncovered_variant_excluded_from_report_covered_retained(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        """A variant inside a non-assessable BAM codon is emitted and annotated
        first, then removed before rule matching; a covered variant is retained."""
        import re

        import pysam
        from typer.testing import CliRunner

        from respro.cli.main import app

        bam_path = tmp_path / 'query.bam'
        header = {'HD': {'VN': '1.0'}, 'SQ': [{'SN': TINY_REF_NAME, 'LN': len(TINY_REF_SEQ)}]}
        with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
            read_seq = TINY_REF_SEQ[:30]
            qualities = pysam.qualitystring_to_array('I' * len(read_seq))
            for idx in range(20):
                read = pysam.AlignedSegment()
                read.query_name = f'read_{idx}'
                read.query_sequence = read_seq
                read.flag = 0
                read.reference_id = 0
                read.reference_start = 0
                read.mapping_quality = 60
                read.cigar = ((0, len(read_seq)),)
                read.next_reference_id = -1
                read.template_length = 0
                read.query_qualities = qualities
                bam.write(read)
        pysam.index(str(bam_path))

        # covered: 0-based 3 (codon 1, K>E rule fires); uncovered: 0-based 33
        # (codon 11, nt 33-35, zero BAM depth)
        vcf_path = tmp_path / 'masked.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
            f'{TINY_REF_NAME}\t34\t.\tG\tT\t100\tPASS\tAF=0.90;DP=500\n',
            encoding='utf-8',
        )

        def _run(with_bam: bool, name: str) -> str:
            out_dir = tmp_path / name
            args = [
                'vcf', '--project', str(project_db), '--vcf', str(vcf_path),
                '--ref-fasta', str(sample_ref_fasta), '--output', str(out_dir),
                '--min-af', '0.01', '--min-depth', '10',
            ]
            if with_bam:
                args += ['--bam', str(bam_path)]
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, result.output
            return list(out_dir.glob('*.html'))[0].read_text()

        html_without_bam = _run(False, 'out_no_bam')
        # Control: without a BAM nothing is masked, both variants are reported.
        assert 'K2E' in html_without_bam
        assert 'A12S' in html_without_bam

        html_with_bam = _run(True, 'out_bam')
        # The covered resistance hit is retained; the uncovered variant is
        # excluded from final annotations and cannot appear (or fire rules).
        assert 'K2E' in html_with_bam
        assert 'A12S' not in html_with_bam
        # Coverage gaps are preserved for reporting.
        assert re.search(r'(?i)coverage', html_with_bam)


class TestCodonContextThroughBaseline:
    """R4: baseline differences participate in codon inference exactly once.

    The acceptance case: internal codon AAA (K), supplied codon ACA (baseline
    middle-base difference), flanking in-cutoff calls at the first and last
    codon positions. The reconciled sample codon is TCG (Ser) — built from the
    two calls AND the baseline middle base — never TAG (stop), which is what
    the internal-reference codon context alone would produce.
    """

    def test_baseline_middle_diff_participates_exactly_once(self) -> None:
        internal = 'AAAAAAAAA'  # codons: AAA AAA AAA (K K K)
        # supplied codon 1 = ACA: the middle base differs from the internal
        # reference (baseline A>C at 0-based col 1); the in-cutoff calls supply
        # the first (A>T at col 0) and last (A>G at col 2) bases. The sample
        # codon is therefore TCG — internal context alone would give TAG.
        supplied = 'ACAAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        calls = [
            _snp(0, 'A', 'T', af=1.0),
            _snp(2, 'A', 'G', af=1.0),
        ]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [
            (0, 'A', 'T', 1.0, VCF_DERIVED),
            (1, 'A', 'C', 1.0, REFERENCE_DERIVED),
            (2, 'A', 'G', 1.0, VCF_DERIVED),
        ]

        anns = annotate_variants(out, [feature])
        effects = {(a.ref_aa, a.codon_pos + 1, a.alt_aa, a.alt_codon) for a in anns}
        # The combined codon state TCG (Ser) must fire; no TAG (stop) effect
        # may appear — the internal codon context alone would give TAG.
        assert ('K', 1, 'S', 'TCG') in effects
        assert not any(aa == '*' for (_r, _p, aa, _c) in effects)

    def test_single_flanking_call_keeps_baseline_at_full_frequency(self) -> None:
        """One flanking call next to a baseline difference: the baseline diff is
        present in every haplotype (no call touches its column), so it keeps
        AF 1.0 and is not re-emitted by the call's state walk."""
        internal = 'AAAAAAAAA'
        supplied = 'ACAAAAAA'  # baseline middle diff at col 1 only
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        calls = [_snp(0, 'A', 'T', af=1.0)]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [
            (0, 'A', 'T', 1.0, VCF_DERIVED),
            (1, 'A', 'C', 1.0, REFERENCE_DERIVED),
        ]

    def test_reversion_to_internal_codon_does_not_reapply_supplied_diff(self) -> None:
        """Reverting a baseline codon difference to the internal base must not
        re-apply the cancelled supplied base via query_ref_codon.

        Internal codon 0 = AAA (K), supplied codon 0 = ACA. Calls A1>T@1.0 and
        C2>A@1.0 produce sample codon TAA (*, stop_gained) — never TCA (S)."""
        internal = 'AAAAAAAAA'
        supplied = 'ACAAAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        calls = [
            _snp(0, 'A', 'T', af=1.0),
            _snp(1, 'C', 'A', af=1.0),
        ]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)
        assert warnings == []
        assert _tokens(out) == [(0, 'A', 'T', 1.0, VCF_DERIVED)]

        anns = annotate_variants(out, [feature])
        assert len(anns) == 1
        ann = anns[0]
        assert (ann.ref_codon, ann.alt_codon, ann.ref_aa, ann.alt_aa, ann.consequence) == (
            'AAA', 'TAA', 'K', '*', 'stop_gained',
        )

    def test_partial_af_frechet_fallback_uses_internal_codon(self) -> None:
        """When Fréchet rejects co-occurrence of a call and a residual baseline
        difference (lower = 0), the single-SNP fallback must annotate against
        the internal codon AAA, not the supplied codon ACA."""
        internal = 'AAAAAAAAA'
        supplied = 'ACAAAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        calls = [
            _snp(0, 'A', 'T', af=0.3),
            _snp(1, 'C', 'A', af=0.7),
        ]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)
        assert warnings == []
        assert _tokens(out) == [
            (0, 'A', 'T', 0.3, VCF_DERIVED),
            (1, 'A', 'C', 0.3, VCF_DERIVED),
        ]

        anns = annotate_variants(out, [feature])
        by_pos = {a.variant.pos: a for a in anns}
        assert (
            by_pos[0].ref_codon,
            by_pos[0].alt_codon,
            by_pos[0].ref_aa,
            by_pos[0].alt_aa,
            by_pos[0].consequence,
        ) == ('AAA', 'TAA', 'K', '*', 'stop_gained')
        assert (
            by_pos[1].ref_codon,
            by_pos[1].alt_codon,
            by_pos[1].ref_aa,
            by_pos[1].alt_aa,
            by_pos[1].consequence,
        ) == ('AAA', 'ACA', 'K', 'T', 'missense')

    def test_indel_in_baseline_divergent_codon_retains_internal_ref_aa(self) -> None:
        """An indel sharing a codon with a baseline SNP must retain the internal
        ref_aa ('K' from AAA) rather than adopting the supplied codon's 'T' (ACA)."""
        from respro.core.rules import match_rules
        from respro.db.models import ResistanceRule

        internal = 'AAAAAAAAA'
        supplied = 'ACAAAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        # In-frame 3-bp insertion anchored at codon 0 boundary (pos 2, A>AGGG -> K1KG)
        calls = [_snp(2, 'A', 'AGGG', af=1.0)]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)
        assert warnings == []

        anns = annotate_variants(out, [feature])
        ins_anns = [a for a in anns if a.consequence == 'insertion']
        assert len(ins_anns) == 1
        assert ins_anns[0].ref_codon == 'AAA'
        assert ins_anns[0].ref_aa == 'K'
        assert ins_anns[0].alt_aa == 'KG'

        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1, drug_name='DrugA', drug_id=1,
            reference_identifier='ref', position=0, reference='K', mutation='KG',
            phenotype='resistant',
        )
        match_rules(anns, [rule])
        assert ins_anns[0].is_resistance_hit

    def test_baseline_snp_ref_codon_is_internal_codon(self) -> None:
        """A single baseline SNP has ref_codon equal to the internal codon AAA,
        not the supplied codon ACA."""
        internal = 'AAAAAAAAA'
        supplied = 'ACAAAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')

        out, warnings = reconcile_variants_for_matches([], [match], supplied)
        assert warnings == []
        anns = annotate_variants(out, [feature])
        assert len(anns) == 1
        assert (anns[0].ref_codon, anns[0].alt_codon, anns[0].ref_aa, anns[0].alt_aa) == (
            'AAA', 'ACA', 'K', 'T',
        )

    def test_reverse_strand_and_multiallelic_codon_rule_matching(self) -> None:
        """Minus-strand feature with a baseline middle-base difference and a
        multiallelic VCF call at codon pos 0 reconciles, annotates, and matches
        resistance rules without double-applying the baseline difference."""
        from respro.core.rules import match_rules
        from respro.db.models import ResistanceRule

        # Coding internal: AAA AAA (K K); genomic internal ('-' strand): TTT TTT.
        # Coding supplied: ACA AAA (baseline A>C at coding idx 1, genomic pos 4 T>G).
        internal_coding = 'AAAAAA'
        supplied_genomic = str(Seq('ACAAAA').reverse_complement())  # TTTTGT
        feature = _feature(internal_coding, strand='-')
        match = _match(feature, supplied_genomic, '6M', strand='-')

        # Multiallelic call at genomic pos 5 (coding idx 0): T>A@0.5 (coding A>T -> TCA/S)
        # and T>C@0.5 (coding A>G -> GCA/A), both on the same record_index=0.
        calls = [
            VariantCall(
                chrom='ref', pos=5, ref='T', alt='A',
                allele_freq=0.5, depth=100, record_index=0,
            ),
            VariantCall(
                chrom='ref', pos=5, ref='T', alt='C',
                allele_freq=0.5, depth=100, record_index=0,
            ),
        ]
        out, warnings = reconcile_variants_for_matches(calls, [match], supplied_genomic)
        assert warnings == []
        anns = annotate_variants(out, [feature])

        rule_s = ResistanceRule(
            id=1, feature_name='gag', feature_id=1, drug_name='DrugS', drug_id=1,
            reference_identifier='ref', position=0, reference='K', mutation='S',
            phenotype='resistant',
        )
        rule_a = ResistanceRule(
            id=2, feature_name='gag', feature_id=1, drug_name='DrugA', drug_id=2,
            reference_identifier='ref', position=0, reference='K', mutation='A',
            phenotype='resistant',
        )
        match_rules(anns, [rule_s, rule_a])
        matched_drugs = {r.drug_name for a in anns for r in a.rule_matches}
        assert matched_drugs == {'DrugS', 'DrugA'}


class TestBamMaskCombinedStateExclusion:
    """R4: after excluding one co-codon member, no combined state may use it."""

    def test_excluded_member_cannot_contribute_to_combined_state(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        """Two co-codon SNPs in a non-assessable BAM codon are excluded together.

        The BAM coverage gate is codon-level: a co-codon member can only be
        excluded when its whole codon is non-assessable, so after the mask no
        combined state can reference an excluded member — the retained-vs-
        excluded split across codons is covered by
        ``test_uncovered_variant_excluded_from_report_covered_retained``.
        Without a BAM both members are annotated and the combined machinery
        runs; with the BAM neither member reaches final rule matching.
        """
        import pysam
        from typer.testing import CliRunner

        from respro.cli.main import app

        # BAM covers only nt 6..29 (depth 20); codon 1 (nt 3-5) has depth 0.
        bam_path = tmp_path / 'query.bam'
        header = {'HD': {'VN': '1.0'}, 'SQ': [{'SN': TINY_REF_NAME, 'LN': len(TINY_REF_SEQ)}]}
        with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
            read_seq = TINY_REF_SEQ[6:30]
            qualities = pysam.qualitystring_to_array('I' * len(read_seq))
            for idx in range(20):
                read = pysam.AlignedSegment()
                read.query_name = f'read_{idx}'
                read.query_sequence = read_seq
                read.flag = 0
                read.reference_id = 0
                read.reference_start = 6
                read.mapping_quality = 60
                read.cigar = ((0, len(read_seq)),)
                read.next_reference_id = -1
                read.template_length = 0
                read.query_qualities = qualities
                bam.write(read)
        pysam.index(str(bam_path))

        # Two co-codon members in codon 1 (0-based nt 3 and 5, codon AAA).
        vcf_path = tmp_path / 'combined.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
            f'{TINY_REF_NAME}\t6\t.\tA\tT\t100\tPASS\tAF=0.90;DP=500\n',
            encoding='utf-8',
        )

        def _run(with_bam: bool, name: str) -> str:
            out_dir = tmp_path / name
            args = [
                'vcf', '--project', str(project_db), '--vcf', str(vcf_path),
                '--ref-fasta', str(sample_ref_fasta), '--output', str(out_dir),
                '--min-af', '0.01', '--min-depth', '10',
            ]
            if with_bam:
                args += ['--bam', str(bam_path)]
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, result.output
            return list(out_dir.glob('*.html'))[0].read_text()

        html_without_bam = _run(False, 'out_combined_no_bam')
        # Control: without a BAM both members are annotated and the combined
        # machinery participates (combined-codon row highlighting present).
        assert 'K2E' in html_without_bam
        assert 'mutation-row--combined' in html_without_bam

        html_with_bam = _run(True, 'out_combined_bam')
        # With the BAM the whole codon is non-assessable: neither member may
        # appear in the final annotations, so no combined state can use one.
        assert 'K2E' not in html_with_bam
        assert 'K2N' not in html_with_bam
        # The class name also appears in the report stylesheet; compare row
        # occurrences: the masked run must have no combined-codon row left.
        assert html_with_bam.count('mutation-row--combined') < (
            html_without_bam.count('mutation-row--combined')
        )


class TestFastaVcfEquivalence:
    """R6: matching FASTA/VCF fixed haplotypes produce equivalent effects."""

    def test_fixed_haplotype_equivalent_effects_and_rule_hits(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        """The same fixed sample profiled as a consensus FASTA and as a VCF
        against an identical supplied reference yields equivalent annotated
        effects and rule hits."""
        from typer.testing import CliRunner

        from respro.cli.main import app

        # Sample: K2E at 0-based nt 3 (the rule position) plus a neutral
        # downstream SNP at 0-based nt 10.
        sample_seq = (
            TINY_REF_SEQ[:3] + 'G' + TINY_REF_SEQ[4:10] + 'C' + TINY_REF_SEQ[11:]
        )
        assert sample_seq[3] == 'G' and sample_seq[10] == 'C'

        fasta_path = tmp_path / 'sample.fasta'
        fasta_path.write_text(f'>tiny_ref\n{sample_seq}\n', encoding='utf-8')
        out_fasta = tmp_path / 'out_fasta'
        result_fasta = CliRunner().invoke(app, [
            'fasta', '--project', str(project_db), '--fasta', str(fasta_path),
            '--output', str(out_fasta), '--export', 'tsv',
        ])
        assert result_fasta.exit_code == 0, result_fasta.output

        vcf_path = tmp_path / 'sample.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=1.0;DP=100\n'
            f'{TINY_REF_NAME}\t11\t.\tT\tC\t100\tPASS\tAF=1.0;DP=100\n',
            encoding='utf-8',
        )
        out_vcf = tmp_path / 'out_vcf'
        result_vcf = CliRunner().invoke(app, [
            'vcf', '--project', str(project_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(sample_ref_fasta), '--output', str(out_vcf),
            '--min-af', '0.01', '--min-depth', '0', '--export', 'tsv',
        ])
        assert result_vcf.exit_code == 0, result_vcf.output

        tsv_fasta = list(out_fasta.glob('*.results.tsv'))
        tsv_vcf = list(out_vcf.glob('*.results.tsv'))
        assert tsv_fasta and tsv_vcf

        def _effects(tsv_path: Path) -> set[tuple]:
            lines = tsv_path.read_text().strip().splitlines()
            header = lines[0].split('\t')
            idx_gene = header.index('gene')
            idx_nt = header.index('nt_mut')
            idx_aa = header.index('aa_effects')
            idx_hit = header.index('in_database')
            return {
                (row[idx_gene], row[idx_nt], row[idx_aa], row[idx_hit])
                for row in (line.split('\t') for line in lines[1:])
            }

        effects_fasta = _effects(tsv_fasta[0])
        effects_vcf = _effects(tsv_vcf[0])
        # Equivalent fixed haplotypes: same genes, nucleotide changes, amino
        # acid effects and database-hit flags (frequency formatting may differ
        # by mode, so it is not compared).
        assert effects_fasta == effects_vcf
        assert any(row[3] == 'yes' for row in effects_vcf)


class TestFrameOffsetReconciliation:
    """F1: one coordinate frame for snapshots, walk and covered columns.

    ``codon_start`` shifts the coding frame; snapshot keys are full-CDS
    columns while the walk and the occupied/covered sets are coding-indexed.
    Mixing the frames corrupted frequency accounting (residual 1.0 instead of
    0.5) and crashed the trailing-insertion anchor.
    """

    def test_frame_offset_residual_frequency(self) -> None:
        """A partial reversion at a frame-offset position keeps the caller's AF.

        Internal ``CAAAGAAAAA`` (frame 1), supplied ``CGAAGAAAAA``: baseline
        A>G at coding col 0; the call G>A at AF 0.5 reverts it on half the
        molecules -> A>G at 0.5, not 1.0."""
        internal = 'CAAAGAAAAA'
        supplied = 'CGAAGAAAAA'
        feature = _feature(internal)
        feature.codon_start = 1
        match = _match(feature, supplied, '10M')
        calls = [_snp(1, 'G', 'A', af=0.5)]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [(1, 'A', 'G', 0.5, VCF_DERIVED)]

    def test_frame_offset_trailing_insertion(self) -> None:
        """An insertion past the last coding base does not crash with a frame
        offset (the trailing anchor must use coding coordinates)."""
        internal = 'CAAAGAAAAA'
        supplied = 'CAAAGAAAAA'
        feature = _feature(internal)
        feature.codon_start = 1
        match = _match(feature, supplied, '10M')
        calls = [_snp(9, 'A', 'AT')]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [(9, 'A', 'AT', 1.0, VCF_DERIVED)]

    def test_v1_acceptance_codon_start_1_exact_case(self) -> None:
        """V1 acceptance: codon_start=1, CDS CAAAGAAAAA, 10M, 0-based query pos 2 A>C."""
        internal = 'CAAAGAAAAA'
        supplied = 'CAAAGAAAAA'
        feature = _feature(internal)
        feature.codon_start = 1
        match = _match(feature, supplied, '10M')
        call = _snp(2, 'A', 'C', af=1.0)

        exp_ref_codon, exp_alt_codon, exp_ref_aa, exp_alt_aa, exp_conseq = (
            derive_fixed_coding_haplotype_effect(
                internal_coding=internal,
                supplied_seq=supplied,
                match_strand='+',
                codon_start=1,
                query_edits=[(2, 'A', 'C')],
                codon_idx=0,
            )
        )
        assert (exp_ref_codon, exp_alt_codon, exp_ref_aa, exp_alt_aa) == ('AAA', 'ACA', 'K', 'T')

        out, warnings = reconcile_variants_for_matches([call], [match], supplied)
        assert warnings == []
        assert len(out) == 1
        assert out[0].query_ref_codon == exp_ref_codon
        anns = annotate_variants(out, [feature])
        assert len(anns) == 1
        assert (anns[0].ref_codon, anns[0].alt_codon, anns[0].ref_aa, anns[0].alt_aa, anns[0].consequence) == (
            exp_ref_codon, exp_alt_codon, exp_ref_aa, exp_alt_aa, exp_conseq,
        )

    @pytest.mark.parametrize(
        ('codon_start', 'coding_internal', 'coding_idx', 'new_coding_base', 'strand'),
        V1_FRAME_OFFSET_CASES,
    )
    def test_frame_offsets_zero_one_two_both_strands_match_oracle(
        self,
        codon_start: int,
        coding_internal: str,
        coding_idx: int,
        new_coding_base: str,
        strand: str,
    ) -> None:
        """Frame offsets 0/1/2 on both '+' and '-' strands match the independent oracle."""
        spec = OracleFeatureSpec(
            segments=((0, len(coding_internal)),),
            strand=strand,
            codon_start=codon_start,
        )
        feature = _feature(coding_internal, strand=strand)
        feature.codon_start = codon_start

        supplied_seq = (
            coding_internal if strand == '+' else reverse_complement_dna(coding_internal)
        )
        match = _match(feature, supplied_seq, f'{len(coding_internal)}M', strand=strand)

        query_pos = spec.cds_to_genomic(coding_idx)
        assert query_pos is not None
        query_ref = supplied_seq[query_pos]
        query_alt = (
            new_coding_base if strand == '+' else reverse_complement_dna(new_coding_base)
        )
        target_codon_idx = (coding_idx - codon_start) // 3
        exp = derive_fixed_coding_haplotype_effect(
            internal_coding=coding_internal,
            supplied_seq=supplied_seq,
            match_strand=strand,
            codon_start=codon_start,
            query_edits=[(query_pos, query_ref, query_alt)],
            codon_idx=target_codon_idx,
        )

        out, warnings = reconcile_variants_for_matches(
            [_snp(query_pos, query_ref, query_alt)], [match], supplied_seq,
        )
        assert warnings == []
        assert len(out) == 1
        anns = annotate_variants(out, [feature])
        assert len(anns) == 1
        assert (anns[0].ref_codon, anns[0].alt_codon, anns[0].ref_aa, anns[0].alt_aa, anns[0].consequence) == exp

    def test_pre_translation_start_position_has_no_codon_index_or_query_codon(self) -> None:
        """A nucleotide before codon_start returns None codon_index/offset and empty query_ref_codon."""
        from respro.core.vcf_to_reference import _attach_supplied_codon, _build_match_alignment

        internal = 'CAAAGAAAAA'
        for strand in ('+', '-'):
            feature = _feature(internal, strand=strand)
            feature.codon_start = 1
            spec = OracleFeatureSpec(segments=((0, len(internal)),), strand=strand, codon_start=1)
            pre_start_pos = spec.cds_to_genomic(0)
            assert pre_start_pos is not None
            assert spec.codon_index(pre_start_pos) is None
            assert spec.codon_offset(pre_start_pos) is None
            assert feature.codon_index(pre_start_pos) is None
            assert feature.codon_position_in_codon(pre_start_pos) is None

            sup_seq = internal if strand == '+' else reverse_complement_dna(internal)
            match = _match(feature, sup_seq, '10M', strand=strand)
            ref_b = sup_seq[pre_start_pos]
            alt_b = 'T' if ref_b != 'T' else 'G'
            pre_var = _snp(pre_start_pos, ref_b, alt_b)

            # Reconciliation emits no coding variant for non-coding frame prefix.
            out, warnings = reconcile_variants_for_matches([pre_var], [match], sup_seq)
            assert warnings == []
            assert out == []

            # _attach_supplied_codon and annotate_variants attach no codon context.
            ctx = _build_match_alignment(match, sup_seq)
            assert ctx is not None
            _attach_supplied_codon(ctx, pre_var)
            assert pre_var.query_ref_codon == ''
            assert pre_var.query_codon_feature_id == 0

            anns = annotate_variants([pre_var], [feature])
            assert len(anns) == 1
            assert anns[0].ref_codon == ''
            assert anns[0].alt_codon == ''
            assert anns[0].ref_aa == ''
            assert anns[0].alt_aa == ''

    def test_unmapped_codon_base_leaves_query_ref_codon_empty(self) -> None:
        """When any base in a codon is unmapped ('-') in the supplied reference,
        _attach_supplied_codon leaves query_ref_codon empty."""
        # Internal AAA TTT GGG; supplied AA- TTT GGG (col 2 deleted in codon 0).
        # SNP at col 0 (A>C) sits in codon 0 where col 2 has sup == '-'.
        internal = 'AAATTTGGG'
        supplied = 'AATTTGGG'
        feature = _feature(internal)
        match = _match(feature, supplied, '2M1D6M')
        call = _snp(0, 'A', 'C', af=1.0)

        out, warnings = reconcile_variants_for_matches([call], [match], supplied)
        assert warnings == []
        snp_variants = [v for v in out if len(v.ref) == 1 and len(v.alt) == 1]
        assert len(snp_variants) == 1
        assert snp_variants[0].query_ref_codon == ''
        assert snp_variants[0].query_codon_feature_id == 0


class TestPendingEditsAtomic:
    """F2: pending-string edits apply per column in descending char order.

    Removing several inserted bases with ascending indexes shifted the
    remaining indexes and left a false insertion behind."""

    def test_multi_base_pending_deletion(self) -> None:
        """Deleting two of three inserted bases leaves exactly the third.

        Internal ``ATGAAA``, supplied ``ACCGTGAAA`` (CG inserted), call
        ``CCG>C`` at 0-based pos 1: sample ``ACTGAAA`` -> net insertion of C
        after the first base (``A>AC``), not ``A>ACG``."""
        internal = 'ATGAAA'
        supplied = 'ACCGTGAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '1M3I5M')
        calls = [_snp(1, 'CCG', 'C')]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [(0, 'A', 'AC', 1.0, VCF_DERIVED)]

    def test_full_pending_deletion_restores_internal(self) -> None:
        """Deleting all inserted bases restores the internal reference: no
        variant is emitted (previously a false ``A>AG`` insertion survived)."""
        internal = 'AT'
        supplied = 'ACGT'
        feature = _feature(internal)
        match = _match(feature, supplied, '1M2I1M')
        calls = [_snp(0, 'ACG', 'A')]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert out == []


class TestNStretchSuppression:
    """F4: baseline stretches containing N are unresolvable (R1/R2)."""

    def test_partial_n_codon_suppressed_with_warning(self) -> None:
        """Internal ``AAAAAA`` vs supplied ``AANNAA``: no invented IUPAC
        baseline alleles; one warning for the N stretch."""
        internal = 'AAAAAA'
        supplied = 'AANNAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '6M')

        out, warnings = reconcile_variants_for_matches([], [match], supplied)

        assert out == []
        assert len(warnings) == 1
        assert 'ambiguous N' in warnings[0]

    def test_n_stretch_does_not_suppress_clear_flanks(self) -> None:
        """Baseline differences outside the N stretch are still emitted."""
        internal = 'AAAAAAAAAA'
        supplied = 'GANNAAAAAA'  # first-base diff + NN stretch
        feature = _feature(internal)
        match = _match(feature, supplied, '10M')

        out, warnings = reconcile_variants_for_matches([], [match], supplied)

        assert _tokens(out) == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert len(warnings) == 1

    def test_full_nnn_codon_emits_warning_and_no_variants(self) -> None:
        """A full NNN codon in the supplied reference must still emit an
        ambiguous-N warning even though _variants_from_alignment skips NNN codons."""
        internal = 'AAAAAAAAA'
        supplied = 'AAANNNAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')

        out, warnings = reconcile_variants_for_matches([], [match], supplied)

        assert out == []
        assert len(warnings) == 1
        assert 'ambiguous N' in warnings[0]

    def test_inserted_nnn_stretch_suppressed_with_warning(self) -> None:
        """An inserted NNN stretch in the supplied reference (pending payload)
        must be suppressed and warned, not emitted as a reference_derived insertion."""
        internal = 'AAAAAA'
        supplied = 'AAANNNAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '3M3I3M')

        out, warnings = reconcile_variants_for_matches([], [match], supplied)

        assert out == []
        assert len(warnings) == 1
        assert 'ambiguous N' in warnings[0]


class TestIndelRepresentation:
    """F5: walk-position emission pinned; every event is sequence-valid."""

    def test_repeat_insertion_emits_at_walk_anchor_and_reconstructs_sample(
        self) -> None:
        """An insertion into a repeated base emits at the walk's anchor (not
        left-normalized); applying the event to the internal reference
        reconstructs the sample sequence (metamorphic check)."""
        internal = 'AAAAAA'
        supplied = 'AAAAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '6M')
        calls = [_snp(3, 'A', 'AA')]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [(3, 'A', 'AA', 1.0, VCF_DERIVED)]
        applied = internal[:3] + 'A' + internal[3:]
        assert applied == internal + 'A'

    def test_emitted_indels_reconstruct_sample(self) -> None:
        """Metamorphic property: applying each emitted event to the internal
        reference yields the reconciled sample state, for a cancellation and a
        merged deletion from the worked tables."""
        # Cancellation: internal ACGT, supplied ACGGT, call deletes one G.
        feature = _feature('ACGT')
        match = _match(feature, 'ACGGT', '3M1I1M')
        out, _ = reconcile_variants_for_matches(
            [_snp(2, 'GG', 'G')], [match], 'ACGGT',
        )
        assert out == []

        # Merged deletions: internal ACGT, supplied ACT, call deletes T.
        feature2 = _feature('ACGT')
        match2 = _match(feature2, 'ACT', '2M1D1M')
        out2, _ = reconcile_variants_for_matches(
            [_snp(1, 'CT', 'C')], [match2], 'ACT',
        )
        assert len(out2) == 1
        var = out2[0]
        applied = 'ACGT'[:var.pos] + var.alt + 'ACGT'[var.pos + len(var.ref):]
        assert applied == 'AC'  # sample: both T and G deleted


class TestRecordIdentity:
    """F6: separate records are distinct phase-unknown events; one record's
    ALTs are mutually exclusive states."""

    def test_separate_records_same_position_rejected(self, tmp_path: Path) -> None:
        """Two records C>G and C>A at the same position (AF 0.3 each) overlap
        and are unverified — the old (chrom, pos, ref) grouping merged them
        into one pseudo-multi-allelic record and emitted both."""
        from respro.io.vcf import parse_vcf

        vcf_path = tmp_path / 'two_records.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'c\t2\t.\tC\tG\t100\tPASS\tAF=0.3;DP=100\n'
            'c\t2\t.\tC\tA\t100\tPASS\tAF=0.3;DP=100\n',
            encoding='utf-8',
        )
        variants = parse_vcf(vcf_path)
        assert len(variants) == 2
        assert variants[0].record_index != variants[1].record_index

        feature = _feature('ACGTACGT')
        match = _match(feature, 'ACGTACGT', '8M')
        out, warnings = reconcile_variants_for_matches(variants, [match], 'ACGTACGT')

        assert out == []
        assert any('Overlapping VCF records' in w for w in warnings)

    def test_multi_allelic_single_record_emits_per_alt_states(
        self, tmp_path: Path,
    ) -> None:
        """One record with two ALTs (AF 0.3 each) emits both states plus the
        0.4 residual — record identity keeps them in one allele set."""
        from respro.io.vcf import parse_vcf

        vcf_path = tmp_path / 'multi_alt.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'c\t2\t.\tC\tG,A\t100\tPASS\tAF=0.3,0.3;DP=100\n',
            encoding='utf-8',
        )
        variants = parse_vcf(vcf_path)
        assert len(variants) == 2
        assert variants[0].record_index == variants[1].record_index

        feature = _feature('ACGTACGT')
        match = _match(feature, 'ACGTACGT', '8M')
        out, warnings = reconcile_variants_for_matches(variants, [match], 'ACGTACGT')

        assert warnings == []
        assert _tokens(out) == [
            (1, 'C', 'A', 0.3, VCF_DERIVED),
            (1, 'C', 'G', 0.3, VCF_DERIVED),
        ]


class TestNonOverlappingRecordsInSharedWindow:
    """Overlap policy compares supplied spans, not projected slot columns."""

    def test_flanking_records_across_insertion_reconcile(self) -> None:
        """Two records at distinct supplied positions flanking an inserted
        base share a slot column but do not overlap: both reconcile.

        Internal ``ATGAAA``, supplied ``ACGTGAAA`` (CG inserted), calls
        C>A at 0-based pos 1 and T>C at pos 3, AF 0.7 each. Per-allele
        semantics: insertion A>AG@0.7, SNP T>C@0.7, residual insertion
        A>CG@0.3."""
        internal = 'ATGAAA'
        supplied = 'ACGTGAAA'
        feature = _feature(internal)
        match = _match(feature, supplied, '1M2I5M')
        calls = [_snp(1, 'C', 'A', af=0.7), _snp(3, 'T', 'C', af=0.7)]

        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)

        assert warnings == []
        assert _tokens(out) == [
            (0, 'A', 'AAG', 0.7, VCF_DERIVED),
            (0, 'A', 'ACG', 0.3, VCF_DERIVED),
            (1, 'T', 'C', 0.7, VCF_DERIVED),
        ]


class TestBamGateEventSpan:
    """F7: the BAM gate assesses every genomic position an event depends on."""

    def test_deletion_whose_payload_is_excluded_is_dropped(self) -> None:
        """A deletion anchored at a covered position but deleting excluded
        bases is removed from final annotations."""
        from respro.cli.profile_helpers import _variant_touches_excluded_positions

        deletion = VariantCall(chrom='ref', pos=2, ref='ACCC', alt='A', allele_freq=1.0)
        excluded = {('ref', 3), ('ref', 4), ('ref', 5)}
        assert _variant_touches_excluded_positions(deletion, excluded) is True

        covered_deletion = VariantCall(chrom='ref', pos=6, ref='ACC', alt='A', allele_freq=1.0)
        assert _variant_touches_excluded_positions(covered_deletion, excluded) is False

        insertion = VariantCall(chrom='ref', pos=2, ref='A', alt='AGGG', allele_freq=1.0)
        assert _variant_touches_excluded_positions(insertion, excluded) is False


class TestJsonVariantSourceGating:
    """F9: the JSON export carries variant_source in VCF mode only (R5)."""

    def _result(self, *, is_fasta_mode: bool):
        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='gag', pos=4, ref='A', alt='T', allele_freq=1.0,
                source='' if is_fasta_mode else VCF_DERIVED,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense', is_fasta_mode=is_fasta_mode,
        )
        return make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s', is_fasta_mode=is_fasta_mode,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )

    def test_fasta_json_omits_variant_source(self, tmp_path: Path) -> None:
        import json

        from respro.report.non_html_exports import write_json

        path = tmp_path / 'out.json'
        write_json(self._result(is_fasta_mode=True), path)
        payload = json.loads(path.read_text())
        assert all('variant_source' not in row for row in payload['variant_result'])

    def test_vcf_json_carries_variant_source(self, tmp_path: Path) -> None:
        import json

        from respro.report.non_html_exports import write_json

        path = tmp_path / 'out.json'
        write_json(self._result(is_fasta_mode=False), path)
        payload = json.loads(path.read_text())
        assert all(
            row.get('variant_source') == VCF_DERIVED
            for row in payload['variant_result']
        )


class TestEmptyVcfBaselineCli:
    """Empty VCF against a single-record or header-contig-matched FASTA profiles
    the baseline differences (Table 1 Row 1) without profiling unrelated multi-record
    FASTA entries."""

    def test_empty_vcf_single_record_fasta_emits_reference_derived_baseline(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        from respro.cli.main import app

        # Divergent supplied reference: K2E at 0-based nt 3 (A->G).
        divergent_seq = TINY_REF_SEQ[:3] + 'G' + TINY_REF_SEQ[4:]
        ref_fasta = tmp_path / 'divergent.fasta'
        ref_fasta.write_text(f'>{TINY_REF_NAME}\n{divergent_seq}\n', encoding='utf-8')

        empty_vcf = tmp_path / 'empty.vcf'
        empty_vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n',
            encoding='utf-8',
        )
        out_dir = tmp_path / 'out_empty_vcf'
        result = CliRunner().invoke(app, [
            'vcf', '--project', str(project_db), '--vcf', str(empty_vcf),
            '--ref-fasta', str(ref_fasta), '--output', str(out_dir),
            '--export', 'tsv',
        ])
        assert result.exit_code == 0, result.output
        tsv = list(out_dir.glob('*.results.tsv'))[0].read_text()
        assert 'K2E' in tsv
        assert 'Reference derived' in tsv

    def test_empty_vcf_multi_record_fasta_without_contig_header_errors(
        self,
        project_db: Path,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        from respro.cli.main import app

        ref_fasta = tmp_path / 'multi.fasta'
        ref_fasta.write_text(
            f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n>extra_ref\n{TINY_REF_SEQ}\n',
            encoding='utf-8',
        )
        empty_vcf = tmp_path / 'empty.vcf'
        empty_vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n',
            encoding='utf-8',
        )
        out_dir = tmp_path / 'out_multi_empty'
        result = CliRunner().invoke(app, [
            'vcf', '--project', str(project_db), '--vcf', str(empty_vcf),
            '--ref-fasta', str(ref_fasta), '--output', str(out_dir),
        ])
        assert result.exit_code != 0
        assert 'no variant records' in result.output


class TestEqualLengthBlockSubstitutionsV3:
    """V3: equal-length block substitutions, haplotype linkage, joint frequency, and persistence."""

    @pytest.mark.parametrize('case', V3_BLOCK_SUB_CASES, ids=lambda c: c['name'])
    def test_block_substitution_oracle_cases(self, case: dict) -> None:
        internal = case['internal_coding']
        feature = _feature(internal, strand=case['strand'])
        match = _match(feature, internal, f'{len(internal)}M', strand=case['match_strand'])
        call = VariantCall(
            chrom='ref',
            pos=case['pos'],
            ref=case['ref'],
            alt=case['alt'],
            allele_freq=case['af'],
            depth=100,
        )

        out, warnings = reconcile_variants_for_matches([call], [match], internal)
        assert warnings == []
        assert len(out) == case['expected_atom_count']
        assert all(v.record_index >= 0 and v.alt_index >= 0 for v in out)
        assert all(v.haplotype_links == ((out[0].record_index, out[0].alt_index, case['af']),) for v in out)

        if 'expected_codon_pos' in case or 'expected_codon_positions' in case:
            sample_coding = apply_sequence_edits(internal, [(case['pos'], case['ref'], case['alt'])])
            anns = annotate_variants(out, [feature])
            assert len(anns) == case['expected_atom_count']
            codon_positions = case.get(
                'expected_codon_positions',
                (case['expected_codon_pos'],) if 'expected_codon_pos' in case else (),
            )
            for codon_pos in codon_positions:
                oracle_mix = oracle_codon_mixture_frequencies(
                    [
                        OracleHaplotype(coding_seq=sample_coding, freq=case['af']),
                        OracleHaplotype(coding_seq=internal, freq=1.0 - case['af']),
                    ],
                    codon_start=0,
                    codon_idx=codon_pos,
                )
                exp_codon = sample_coding[codon_pos * 3:codon_pos * 3 + 3]
                exp_aa, exp_freq = oracle_mix[exp_codon]
                codon_anns = [a for a in anns if a.codon_pos == codon_pos]
                assert codon_anns
                if 'expected_alt_aa' in case:
                    assert exp_aa == case['expected_alt_aa']
                if 'expected_ref_aa' in case:
                    assert any(a.ref_aa == case['expected_ref_aa'] for a in codon_anns)
                assert any(
                    a.alt_aa == exp_aa
                    and a.alt_codon == exp_codon
                    and a.single_exchange_aa_freq == pytest.approx(exp_freq)
                    for a in codon_anns
                )
                # No impossible solo states may be accepted or carry positive lower.
                for a in codon_anns:
                    for state in a.combined_states:
                        assert state.lower == pytest.approx(
                            oracle_mix.get(state.alt_codon, ('?', 0.0))[1]
                        )

    def test_multiallelic_block_over_baseline_divergent_codon_exact_states(
        self, tmp_path: Path,
    ) -> None:
        """Internal ATGAAATTT vs supplied ATGGCATTT + one record GC->AT@0.4,AC@0.3 resolves ATA=0.4, ACA=0.3, GCA=0.3."""
        from respro.io.vcf import parse_vcf

        case = V3_MULTIALLELIC_BASELINE_CASE
        internal = case['internal_coding']
        supplied = case['supplied_coding']
        vcf_path = tmp_path / 'baseline_multi.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f"ref\t{case['pos'] + 1}\t.\t{case['ref']}\tAT,AC\t100\tPASS\tAF=0.4,0.3;DP=100\n",
            encoding='utf-8',
        )
        calls = parse_vcf(vcf_path)
        assert [(c.record_index, c.alt_index) for c in calls] == [(0, 0), (0, 1)]

        feature = _feature(internal)
        match = _match(feature, supplied, f'{len(internal)}M')
        out, warnings = reconcile_variants_for_matches(calls, [match], supplied)
        assert warnings == []

        hap_at = apply_sequence_edits(supplied, [(case['pos'], case['ref'], 'AT')])
        hap_ac = apply_sequence_edits(supplied, [(case['pos'], case['ref'], 'AC')])
        oracle_mix = oracle_codon_mixture_frequencies(
            [
                OracleHaplotype(coding_seq=hap_at, freq=0.4),
                OracleHaplotype(coding_seq=hap_ac, freq=0.3),
                OracleHaplotype(coding_seq=supplied, freq=0.3),
            ],
            codon_start=0,
            codon_idx=case['codon_idx'],
        )
        anns = annotate_variants(out, [feature])
        state_lower = {
            s.alt_codon: s.lower
            for a in anns
            for s in a.combined_states
            if s.accepted
        }
        for promoted in anns:
            if promoted.single_exchange_aa_freq > 0:
                state_lower[promoted.alt_codon] = promoted.single_exchange_aa_freq

        assert state_lower.get('ATA') == pytest.approx(oracle_mix['ATA'][1], abs=1e-6)
        assert state_lower.get('ACA') == pytest.approx(oracle_mix['ACA'][1], abs=1e-6)
        assert state_lower.get('GCA') == pytest.approx(oracle_mix['GCA'][1], abs=1e-6)
        assert all(
            c not in state_lower or state_lower[c] == pytest.approx(0.0, abs=1e-9)
            for c in ('AAA', 'GTA', 'GAA')
        )

    def test_unchanged_slot_at_baseline_divergent_position_not_claimed_by_equal_len_block(
        self,
    ) -> None:
        """call_base_cols is gated on alt_len < ref_len: an equal-length block
        that leaves a baseline-divergent slot unchanged keeps that baseline at 1.0 reference_derived."""
        internal = 'ATGAAATTT'
        supplied = 'ATGACATTT'  # baseline A>C at 0-based col 4
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        # Call AC>TC at 0-based pos 3 (AF 0.4): changes col 3 (A>T), leaves col 4 (C>C) unchanged.
        call = _snp(3, 'AC', 'TC', af=0.4)

        out, warnings = reconcile_variants_for_matches([call], [match], supplied)
        assert warnings == []
        assert _tokens(out) == [
            (3, 'A', 'T', 0.4, VCF_DERIVED),
            (4, 'A', 'C', 1.0, REFERENCE_DERIVED),
        ]

    def test_reverse_strand_equal_length_block_substitution(self) -> None:
        """Minus-strand equal-length block substitution at AF 0.6 gives exact coding effect."""
        internal_coding = 'ATGAAATTT'  # codon 1 = AAA (K)
        internal_genomic = reverse_complement_dna(internal_coding)  # AAATTTCAT
        # Mutate genomic pos 3..4 ('TT' -> 'AC') -> genomic AAAACTCAT -> coding ATGAGTTAT (codon 1 = AGT = S).
        feature = _feature(internal_coding, strand='-')
        match = _match(feature, internal_genomic, '9M', strand='-')
        call = VariantCall(chrom='ref', pos=3, ref='TT', alt='AC', allele_freq=0.6, depth=50)

        out, warnings = reconcile_variants_for_matches([call], [match], internal_genomic)
        assert warnings == []
        assert len(out) == 2

        sample_coding = reverse_complement_dna(
            apply_sequence_edits(internal_genomic, [(3, 'TT', 'AC')])
        )
        oracle_mix = oracle_codon_mixture_frequencies(
            [
                OracleHaplotype(coding_seq=sample_coding, freq=0.6),
                OracleHaplotype(coding_seq=internal_coding, freq=0.4),
            ],
            codon_start=0,
            codon_idx=1,
        )
        anns = annotate_variants(out, [feature])
        assert len(anns) == 2
        assert all(
            a.alt_codon == 'AGT'
            and a.alt_aa == 'S'
            and a.single_exchange_aa_freq == pytest.approx(oracle_mix['AGT'][1])
            for a in anns
        )

    def test_mutually_exclusive_multi_alt_block_with_shared_atom(self, tmp_path: Path) -> None:
        """Two ALTs in one record sharing an atom (AA>CG@0.5, AA>CT@0.3) keep both haplotype links."""
        from respro.io.vcf import parse_vcf

        internal = 'ATGAAATTT'
        vcf_path = tmp_path / 'multi_block.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'ref\t4\t.\tAA\tCG,CT\t100\tPASS\tAF=0.5,0.3;DP=100\n',
            encoding='utf-8',
        )
        calls = parse_vcf(vcf_path)
        assert [c.alt_index for c in calls] == [0, 1]

        feature = _feature(internal)
        match = _match(feature, internal, '9M')
        out, warnings = reconcile_variants_for_matches(calls, [match], internal)
        assert warnings == []

        by_pos_alt = {(v.pos, v.alt): v for v in out}
        shared = by_pos_alt[(3, 'C')]
        assert shared.allele_freq == pytest.approx(0.8)
        assert [(r, a) for r, a, _ in shared.haplotype_links] == [(0, 0), (0, 1)]
        assert [af for _, _, af in shared.haplotype_links] == pytest.approx([0.5, 0.3])
        assert [(r, a) for r, a, _ in by_pos_alt[(4, 'G')].haplotype_links] == [(0, 0)]
        assert by_pos_alt[(4, 'G')].haplotype_links[0][2] == pytest.approx(0.5)
        assert [(r, a) for r, a, _ in by_pos_alt[(4, 'T')].haplotype_links] == [(0, 1)]
        assert by_pos_alt[(4, 'T')].haplotype_links[0][2] == pytest.approx(0.3)

        hap_cg = apply_sequence_edits(internal, [(3, 'AA', 'CG')])
        hap_ct = apply_sequence_edits(internal, [(3, 'AA', 'CT')])
        oracle_mix = oracle_codon_mixture_frequencies(
            [
                OracleHaplotype(coding_seq=hap_cg, freq=0.5),
                OracleHaplotype(coding_seq=hap_ct, freq=0.3),
                OracleHaplotype(coding_seq=internal, freq=0.2),
            ],
            codon_start=0,
            codon_idx=1,
        )

        anns = annotate_variants(out, [feature])
        by_ann = {(a.variant.pos, a.variant.alt): a for a in anns}
        c_ann = by_ann[(3, 'C')]
        # CGA (R@0.5) promoted; CTA (L@0.3) in combined_states; impossible solo CAA absent.
        assert c_ann.alt_codon == 'CGA'
        assert c_ann.alt_aa == 'R'
        assert c_ann.single_exchange_aa_freq == pytest.approx(oracle_mix['CGA'][1])
        cta_states = [s for s in c_ann.combined_states if s.alt_codon == 'CTA']
        assert len(cta_states) == 1
        assert cta_states[0].lower == pytest.approx(oracle_mix['CTA'][1])
        assert cta_states[0].accepted is True
        assert all(s.alt_codon != 'CAA' for s in c_ann.combined_states)

    def test_baseline_divergent_block_residual_links(self) -> None:
        """Internal AAA vs supplied GCA, call GC>AT@0.6 -> ATA@0.6 and GCA@0.4."""
        internal = 'ATGAAATTT'
        supplied = 'ATGGCATTT'  # baseline col 3 A>G, col 4 A>C
        feature = _feature(internal)
        match = _match(feature, supplied, '9M')
        call = VariantCall(chrom='ref', pos=3, ref='GC', alt='AT', allele_freq=0.6, depth=100)

        out, warnings = reconcile_variants_for_matches([call], [match], supplied)
        assert warnings == []

        hap_call = apply_sequence_edits(supplied, [(3, 'GC', 'AT')])
        oracle_mix = oracle_codon_mixture_frequencies(
            [
                OracleHaplotype(coding_seq=hap_call, freq=0.6),
                OracleHaplotype(coding_seq=supplied, freq=0.4),
            ],
            codon_start=0,
            codon_idx=1,
        )
        anns = annotate_variants(out, [feature])
        effects: dict[str, float] = {}
        for a in anns:
            if a.single_exchange_aa_freq > 0:
                effects[a.alt_codon] = a.single_exchange_aa_freq
            for s in a.combined_states:
                if s.accepted:
                    effects[s.alt_codon] = s.lower
        assert effects['ATA'] == pytest.approx(oracle_mix['ATA'][1])
        assert effects['GCA'] == pytest.approx(oracle_mix['GCA'][1])

    def test_length_changing_replacement_still_follows_indel_path(self) -> None:
        """A length change (pos 3 AA>G) still produces a deletion annotation (frameshift)."""
        internal = 'ATGAAATTT'
        feature = _feature(internal)
        match = _match(feature, internal, '9M')
        call = VariantCall(chrom='ref', pos=3, ref='AA', alt='G', allele_freq=1.0, depth=100)

        out, warnings = reconcile_variants_for_matches([call], [match], internal)
        assert warnings == []
        anns = annotate_variants(out, [feature])
        assert any(a.consequence == 'frameshift' for a in anns)

    def test_haplotype_links_round_trip_results_db_json_and_legacy_migration(
        self, tmp_path: Path,
    ) -> None:
        """haplotype_links survive SQLite save_run/reconstruct_annotations, JSON write/load,
        and legacy results DB optional-column migration."""
        import json
        import uuid

        from respro.db.results import load_run_from_json, save_run
        from respro.report.non_html_exports import write_json

        links = ((0, 0, 0.5), (0, 1, 0.3))
        feature = _feature('ATGAAATTT')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='ref', pos=3, ref='A', alt='C', allele_freq=0.8, depth=100,
                source=VCF_DERIVED, record_index=0, alt_index=0, haplotype_links=links,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAA', alt_codon='CGA', ref_aa='K', alt_aa='R',
            consequence='missense',
        )
        result = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.vcf', is_fasta_mode=False,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAATTT',
            feature_matches=[_match(feature, 'ATGAAATTT', '9M')],
        )

        project_db = tmp_path / 'project.db'
        proj_conn = create_schema(project_db)
        proj_conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('T', 6, str(uuid.uuid4())),
        )
        proj_conn.commit()

        # SQLite round-trip
        results_db = tmp_path / 'results.db'
        conn = init_results_db(results_db)
        run_id = save_run(conn, project_db, proj_conn, result)
        _run_row, variant_rows = load_run(conn, run_id)
        reconstructed_db = reconstruct_annotations(variant_rows)
        assert reconstructed_db[0].variant.haplotype_links == links
        conn.close()

        # JSON round-trip
        json_path = tmp_path / 'out.results.json'
        write_json(result, json_path, project_conn=proj_conn, project_db_path=project_db)
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        assert 'haplotype_links' in payload['variant_result'][0]
        _r, json_rows, _g, _f, _c = load_run_from_json(json_path)
        reconstructed_json = reconstruct_annotations(json_rows)
        assert reconstructed_json[0].variant.haplotype_links == links
        proj_conn.close()

        # Legacy results DB migration
        legacy_db = tmp_path / 'legacy.db'
        raw = sqlite3.connect(str(legacy_db))
        raw.executescript(
            """
            CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                project_db_path TEXT DEFAULT '',
                reference_name TEXT DEFAULT '',
                vcf_path TEXT DEFAULT ''
            );
            CREATE TABLE variant_result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                chrom TEXT NOT NULL,
                pos INTEGER NOT NULL,
                ref TEXT NOT NULL,
                alt TEXT NOT NULL
            );
            """
        )
        raw.commit()
        raw.close()
        migrated = init_results_db(legacy_db)
        cols = {
            row['name'] for row in migrated.execute('PRAGMA table_info(variant_result)').fetchall()
        }
        assert 'haplotype_links' in cols
        migrated.close()
