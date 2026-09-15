"""
Tests for the combined-SNP module (candidate-state enumeration + Fréchet bounds).

``enumerate_candidate_codon_states`` builds the cartesian product of
{ref base, each ALT} at every variant-bearing codon position — the shared state
space for both the Fréchet and BAM paths.
"""

from __future__ import annotations

from pathlib import Path

import pysam
import pytest

from respro.core.annotation import annotate_variants
from respro.core.combined_snp import (
    BamCooccurrence,
    _annotate_combined_snp_codon,
    _compute_codon_frechet_states,
    compute_codon_cooccurrence,
    enumerate_candidate_codon_states,
)
from respro.db.models import FeatureRecord, VariantCall

# ─── enumerate_candidate_codon_states ──────────────────────────────────────


class TestEnumerateCandidateCodonStates:
    """the candidate-state enumerator builds the correct cartesian product."""

    def test_two_positions_biallelic_yields_four_candidates(self) -> None:
        """ALTs T@0 and T@1 on ref AAA → exactly {AAA, TAA, ATA, TTA}."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        assert len(candidates) == 4
        alt_codons = sorted(c.alt_codon for c in candidates)
        assert alt_codons == ['AAA', 'ATA', 'TAA', 'TTA']

    def test_two_positions_member_indices(self) -> None:
        """Each candidate carries the correct member_indices (pos_idx*100+alt_idx)."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        by_codon = {c.alt_codon: c for c in candidates}
        # AAA = ref at both positions → no members
        assert by_codon['AAA'].member_indices == ()
        # TAA = ALT at pos 0 only → member 0*100+0 = 0
        assert by_codon['TAA'].member_indices == (0,)
        # ATA = ALT at pos 1 only → member 1*100+0 = 100
        assert by_codon['ATA'].member_indices == (100,)
        # TTA = ALT at both → members 0 and 100
        assert by_codon['TTA'].member_indices == (0, 100)

    def test_three_positions_yields_eight_candidates(self) -> None:
        """ALTs at all three codon positions → 2^3 = 8 candidates."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
            {'codon_pos': 2, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        assert len(candidates) == 8

    def test_multiallelic_position_yields_right_product(self) -> None:
        """A position with 2 ALTs + ref (3 options) × 1 ALT + ref (2 options) = 6."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.5), ('G', 0.3)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        assert len(candidates) == 6  # 3 × 2
        # Verify the multiallelic position's two ALTs both appear.
        alt_codons = sorted(c.alt_codon for c in candidates)
        # pos0 options: A(ref), T, G ; pos1 options: A(ref), T
        # AAA, ATA, GAA, GTA, TAA, TTA
        assert alt_codons == ['AAA', 'ATA', 'GAA', 'GTA', 'TAA', 'TTA']

    def test_multiallelic_member_indices_distinguish_alts(self) -> None:
        """Two ALTs at the same position get distinct member_ids (0 and 1)."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.5), ('G', 0.3)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        by_codon = {c.alt_codon: c for c in candidates}
        # TAA carries ALT T at pos0 (member_id 0) → 0*100+0 = 0
        assert by_codon['TAA'].member_indices == (0,)
        # GAA carries ALT G at pos0 (member_id 1) → 0*100+1 = 1
        assert by_codon['GAA'].member_indices == (1,)

    def test_choices_expose_per_position_base_and_member_id(self) -> None:
        """Each candidate exposes the (base, member_id) choice per variant position."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        by_codon = {c.alt_codon: c for c in candidates}
        # TTA: T at pos0 (member 0), T at pos1 (member 100)
        tta = by_codon['TTA']
        assert len(tta.choices) == 2
        # choices ordered by position index
        assert tta.choices[0] == ('T', 0)
        assert tta.choices[1] == ('T', 100)
        # AAA: ref at both → member_id -1
        aaa = by_codon['AAA']
        assert aaa.choices[0] == ('A', -1)
        assert aaa.choices[1] == ('A', -1)

    def test_alt_aa_is_translated(self) -> None:
        """Each candidate's alt_aa is the translation of its alt_codon."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('G', 0.8)]},
            {'codon_pos': 1, 'alts': [('A', 0.8)]},
        ]
        candidates = enumerate_candidate_codon_states(variants_at_codon, 'AAA')
        by_codon = {c.alt_codon: c for c in candidates}
        # AAA → K (lysine), GAA → E (glutamic acid)
        assert by_codon['AAA'].alt_aa == 'K'
        assert by_codon['GAA'].alt_aa == 'E'

    def test_single_position_returns_empty(self) -> None:
        """A single variant position is not combinable → empty list."""
        variants_at_codon = [{'codon_pos': 0, 'alts': [('T', 0.8)]}]
        assert enumerate_candidate_codon_states(variants_at_codon, 'AAA') == []

    def test_out_of_range_position_returns_empty(self) -> None:
        """A codon_pos outside [0, 3) → empty list."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 5, 'alts': [('T', 0.8)]},
        ]
        assert enumerate_candidate_codon_states(variants_at_codon, 'AAA') == []

    def test_invalid_ref_codon_length_returns_empty(self) -> None:
        """A ref_codon that is not 3 bases → empty list."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        assert enumerate_candidate_codon_states(variants_at_codon, 'AA') == []


# ─── Fréchet refactor: byte-identical output ────────────────────────────────


class TestFrechetRefactorGolden:
    """the refactored ``_compute_codon_frechet_states`` (using the enumerator)
    produces identical CodonState lists to the pre-refactor version on the
    existing golden cases. The existing ``TestFrechet*`` tests in
    ``test_annotation.py`` pin the exact output; this class adds a few direct
    shape/identity checks to guard the refactor seam."""

    def test_two_position_states_match_expected_shape(self) -> None:
        """Two biallelic positions at 0.8/0.8 → 4 states, one accepted (TTA)."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.8)]},
            {'codon_pos': 1, 'alts': [('T', 0.8)]},
        ]
        states = _compute_codon_frechet_states(variants_at_codon, 'AAA', 0.6666666666666666)
        assert len(states) == 4
        accepted = [s for s in states if s.accepted]
        # Only TTA (both ALTs) has lower = 0.8+0.8-1 = 0.6, forced_fraction = 0.6/0.8 = 0.75 >= 2/3
        assert len(accepted) == 1
        assert accepted[0].alt_codon == 'TTA'
        assert accepted[0].lower == pytest.approx(0.6)
        assert accepted[0].upper == pytest.approx(0.8)

    def test_three_position_states_match_expected_shape(self) -> None:
        """Three biallelic positions at 0.9 each → 8 states, one accepted."""
        variants_at_codon = [
            {'codon_pos': 0, 'alts': [('T', 0.9)]},
            {'codon_pos': 1, 'alts': [('T', 0.9)]},
            {'codon_pos': 2, 'alts': [('T', 0.9)]},
        ]
        states = _compute_codon_frechet_states(variants_at_codon, 'AAA', 0.6666666666666666)
        assert len(states) == 8
        accepted = [s for s in states if s.accepted]
        assert len(accepted) == 1
        assert accepted[0].alt_codon == 'TTT'
        # lower = 0.9*3 - 2 = 0.7, upper = 0.9, forced = 0.7/0.9 ≈ 0.778 >= 2/3
        assert accepted[0].lower == pytest.approx(0.7)


# ─── BAM co-occurrence resolver ────────────────────────────────────────────


def _write_bam(
    bam_path: Path,
    contig_len: int,
    reads: list[dict],
) -> None:
    """Write a synthetic BAM.

    Each read dict: {
        'qname': str, 'seq': str, 'start': int, 'mapq': int,
        'is_reverse': bool (default False), 'flag': int (default 0),
    }
    The read is a simple M match over its full sequence.
    """
    header = {'HD': {'VN': '1.0'}, 'SQ': [{'SN': 'ref', 'LN': contig_len}]}
    with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
        for r in reads:
            seq = r['seq']
            a = pysam.AlignedSegment()
            a.query_name = r['qname']
            a.query_sequence = seq
            a.flag = r.get('flag', 0) | (16 if r.get('is_reverse', False) else 0)
            a.reference_id = 0
            a.reference_start = r['start']
            a.mapping_quality = r.get('mapq', 60)
            a.cigar = ((0, len(seq)),)
            a.query_qualities = pysam.qualitystring_to_array('I' * len(seq))
            a.next_reference_id = -1
            a.next_reference_start = -1
            a.template_length = 0
            bam.write(a)
    pysam.index(str(bam_path))


class TestComputeCodonCooccurrence:
    """BAM read-backed exact co-occurrence counting for combined codons."""

    def test_single_end_double_alt_yields_two_states(self, tmp_path: Path) -> None:
        """10 reads: 8 carry both ALTs at codon positions 0 and 1, 2 carry only
        the pos-0 ALT → double-ALT state at 0.8, single-ALT state at 0.2."""
        # ref codon AAA; ALT T at query pos 0, ALT T at query pos 1.
        # Read sequence covers query positions 0..2 (codon) + flanking.
        # 8 reads: TTA (both ALTs); 2 reads: TAA (only pos-0 ALT).
        reads = []
        for i in range(8):
            reads.append({'qname': f'r{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0})
        for i in range(2):
            reads.append({'qname': f's{i}', 'seq': 'TAA' + 'A' * 27, 'start': 0})
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam,
                contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates,
                strand='+',
                min_mapq=20,
                min_depth=5,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        assert set(by_codon.keys()) == {'TTA', 'TAA'}
        assert by_codon['TTA'].lower == pytest.approx(0.8)
        assert by_codon['TTA'].upper == pytest.approx(0.8)
        assert by_codon['TTA'].forced_fraction == pytest.approx(1.0)
        assert by_codon['TTA'].accepted is True
        assert by_codon['TAA'].lower == pytest.approx(0.2)

    def test_low_mapq_read_excluded(self, tmp_path: Path) -> None:
        """A read with mapping_quality=10 is excluded when min_mapq=20."""
        reads = [
            {'qname': 'good1', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'good2', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'low', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'mapq': 10},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=2,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # Only the 2 good reads (both TTA) survive → TTA at 1.0
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_thin_evidence_returns_empty(self, tmp_path: Path) -> None:
        """A codon with only 5 spanning molecules returns [] when min_depth=10."""
        reads = [
            {'qname': f'r{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0}
            for i in range(5)
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=10,
            )
        finally:
            bam.close()

        assert states == []

    def test_non_spanning_read_excluded(self, tmp_path: Path) -> None:
        """A read that does not span all variant query positions is excluded."""
        # Variant at query pos 0 and pos 20. A read covering only 0..9 does not
        # span pos 20 and is excluded.
        reads = [
            # spanning read (0..29)
            {'qname': 'span', 'seq': 'T' + 'A' * 19 + 'T' + 'A' * 9, 'start': 0},
            # non-spanning read (0..9 only)
            {'qname': 'short', 'seq': 'T' + 'A' * 9, 'start': 0},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 20)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=1,
            )
        finally:
            bam.close()

        # Only the spanning read counts; it carries T at both → TTA at 1.0
        by_codon = {s.alt_codon: s for s in states}
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_paired_end_agree_counts_once(self, tmp_path: Path) -> None:
        """5 pairs where both mates agree on the double-ALT codon → lower=1.0
        (5 molecules, not 10 reads)."""
        reads = []
        for i in range(5):
            # both mates same qname, same codon TTA, different positions
            reads.append({'qname': f'pair{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 99})
            reads.append({'qname': f'pair{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 147})
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=3,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_paired_end_disagree_rejected(self, tmp_path: Path) -> None:
        """1 pair where mates disagree → that pair rejected; 4 agreeing pairs →
        denominator 4, lower=1.0."""
        reads = []
        # 4 agreeing pairs (TTA/TTA)
        for i in range(4):
            reads.append({'qname': f'ok{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 99})
            reads.append({'qname': f'ok{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 147})
        # 1 disagreeing pair (TTA vs TAA)
        reads.append({'qname': 'bad', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 99})
        reads.append({'qname': 'bad', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'flag': 147})
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=3,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_other_codon_tallied_no_state(self, tmp_path: Path) -> None:
        """A read whose base at a variant position is not in the VCF ALT set is
        tallied as 'other' and produces no CodonState."""
        # ref AAA; ALT T at pos 0, ALT T at pos 1.
        # 8 reads TTA (both ALTs); 2 reads GGA (base G not in VCF at either pos).
        reads = []
        for i in range(8):
            reads.append({'qname': f'r{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0})
        for i in range(2):
            reads.append({'qname': f'o{i}', 'seq': 'GGA' + 'A' * 27, 'start': 0})
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=5,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # Only TTA (8/10); GGA is "other" and produces no state.
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(0.8)

    def test_supplementary_alignment_excluded(self, tmp_path: Path) -> None:
        """A supplementary alignment (same qname, same span, different codon) is
        excluded from the co-occurrence count — only the primary is assessed.

        Without the primary-only filter the supplementary would join the primary
        under one query_name (len==2), and the paired-end agree/reject branch
        would reject the molecule (the two records disagree on the codon),
        wrongly dropping a genuine primary observation. With the filter the
        primary stands alone and is counted.

        Setup: mol1 = primary TTA + supplementary TAA; mol2 = primary TAA.
        With the filter: 2 primary molecules (TTA, TAA) → TTA 0.5, TAA 0.5.
        Without: mol1 rejected (disagree), leaving only mol2 (TAA) → TAA 1.0,
        and TTA absent — the wrong outcome that drops a real primary TTA."""
        reads = [
            {'qname': 'mol1', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 0},
            {'qname': 'mol1', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'flag': 0x800},
            {'qname': 'mol2', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'flag': 0},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=2,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # The primary TTA of mol1 survives (supplementary excluded); mol2 is TAA.
        # 2 primary molecules → TTA 0.5, TAA 0.5.
        assert set(by_codon.keys()) == {'TTA', 'TAA'}
        assert by_codon['TTA'].lower == pytest.approx(0.5)
        assert by_codon['TAA'].lower == pytest.approx(0.5)

    def test_secondary_alignment_excluded(self, tmp_path: Path) -> None:
        """A secondary alignment (flag 0x100) is excluded — only the primary is
        assessed, so a read is never double-counted via its alternative mapping.

        Setup: mol1 = primary TTA + secondary TAA (same qname); mol2 = primary TTA.
        With the filter: 2 primary molecules (TTA, TTA) → TTA 1.0.
        Without: mol1's two records disagree (TTA vs TAA) → rejected, leaving
        only mol2 → TTA 1.0 — coincidentally the same frequency, so this case
        alone can't detect the bug; the supplementary test above is the
        discriminator. Here we assert the secondary does not inflate the
        denominator or introduce a spurious TAA state."""
        reads = [
            {'qname': 'mol1', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 0},
            {'qname': 'mol1', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'flag': 0x100},
            {'qname': 'mol2', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'flag': 0},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=2,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # 2 primary TTA reads (the secondary is excluded) → TTA at 1.0, no TAA.
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_qcfail_read_excluded(self, tmp_path: Path) -> None:
        """A read flagged QCFAIL (0x200) is excluded from the co-occurrence count."""
        reads = [
            {'qname': 'good1', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'good2', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'qcfail', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'mapq': 60, 'flag': 0x200},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=2,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # Only the 2 good reads (both TTA) survive → TTA at 1.0
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)

    def test_duplicate_read_excluded(self, tmp_path: Path) -> None:
        """A read flagged duplicate (0x400) is excluded from the co-occurrence count."""
        reads = [
            {'qname': 'good1', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'good2', 'seq': 'TTA' + 'A' * 27, 'start': 0, 'mapq': 60},
            {'qname': 'dup', 'seq': 'TAA' + 'A' * 27, 'start': 0, 'mapq': 60, 'flag': 0x400},
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)

        candidates = enumerate_candidate_codon_states(
            [{'codon_pos': 0, 'alts': [('T', 0.8)]},
             {'codon_pos': 1, 'alts': [('T', 0.8)]}],
            'AAA',
        )
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            states = compute_codon_cooccurrence(
                bam=bam, contig='ref',
                codon_query_positions=[(0, 0), (1, 1)],
                candidate_states=candidates, strand='+', min_mapq=20, min_depth=2,
            )
        finally:
            bam.close()

        by_codon = {s.alt_codon: s for s in states}
        # Only the 2 good reads (both TTA) survive → TTA at 1.0
        assert set(by_codon.keys()) == {'TTA'}
        assert by_codon['TTA'].lower == pytest.approx(1.0)


# ─── BAM path integration into _annotate_combined_snp_codon ────────────────


def _make_feature(name: str = 'gag', strand: str = '+') -> FeatureRecord:
    """A 30-nt CDS feature (10 codons), all AAA, starting at genomic pos 0."""
    seq = 'AAA' * 10
    return FeatureRecord(
        id=1,
        reference_id=1,
        name=name,
        protein=name,
        start=0,
        end=30,
        strand=strand,
        nt_sequence=seq,
        aa_sequence='K' * 10,
        codon_start=0,
    )


class TestBamPathAnnotation:
    """The BAM path produces observed frequencies; the Fréchet path produces
    estimated frequencies. ``freq_method`` records the provenance."""

    def test_bam_path_observed_frequency(self, tmp_path: Path) -> None:
        """A BAM showing 70% co-occurrence reports CodonState.lower=0.7,
        freq_method='observed' (not the Fréchet 0.6 lower bound)."""
        # Two SNPs at codon 0 (positions 0 and 1), ref AAA, ALTs T@0 and T@1.
        # Fréchet at 0.8/0.8 → lower=0.6. BAM: 7 reads TTA, 3 reads TAA → 0.7/0.3.
        feature = _make_feature()
        variants = [
            VariantCall(chrom='ref', pos=0, ref='A', alt='T', allele_freq=0.8, depth=100),
            VariantCall(chrom='ref', pos=1, ref='A', alt='T', allele_freq=0.8, depth=100),
        ]
        # BAM: 7 reads TTA (both ALTs), 3 reads TAA (only pos-0 ALT).
        reads = []
        for i in range(7):
            reads.append({'qname': f'r{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0})
        for i in range(3):
            reads.append({'qname': f's{i}', 'seq': 'TAA' + 'A' * 27, 'start': 0})
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            ctx = BamCooccurrence(
                bam=bam,
                contig='ref',
                cds_to_query_by_feature={"gag": {0: 0, 1: 1, 2: 2}},
                min_mapq=20,
                min_depth=5,
            )
            anns = _annotate_combined_snp_codon(variants, feature, bam_cooccurrence=ctx)
        finally:
            bam.close()

        assert len(anns) == 2
        # All annotations are observed.
        assert all(a.freq_method == 'observed' for a in anns)
        assert all(a.is_combined_codon_event for a in anns)
        # The double-ALT state TTA at 0.7 appears: for the member whose
        # single-exchange codon was observed it is in combined_states; for the
        # member whose single was not observed it is promoted to the single
        # (alt_codon == TTA) and dropped from combined_states to avoid a
        # duplicate. Either way, the 0.7 frequency is reported.
        found_07 = False
        for ann in anns:
            if any(s.alt_codon == 'TTA' and s.lower == pytest.approx(0.7) for s in ann.combined_states):
                found_07 = True
            if ann.alt_codon == 'TTA' and ann.single_exchange_aa_freq == pytest.approx(0.7):
                found_07 = True
        assert found_07, 'TTA at 0.7 must appear in combined_states or as the promoted single'

    def test_frechet_path_estimated_frequency(self) -> None:
        """Without a BAM, the Fréchet path runs and sets freq_method='estimated'."""
        feature = _make_feature()
        variants = [
            VariantCall(chrom='ref', pos=0, ref='A', alt='T', allele_freq=0.8, depth=100),
            VariantCall(chrom='ref', pos=1, ref='A', alt='T', allele_freq=0.8, depth=100),
        ]
        anns = _annotate_combined_snp_codon(variants, feature)
        assert len(anns) == 2
        assert all(a.freq_method == 'estimated' for a in anns)
        # Fréchet lower bound 0.6 for the double-ALT state.
        for ann in anns:
            tta_states = [s for s in ann.combined_states if s.alt_codon == 'TTA']
            assert len(tta_states) == 1
            assert tta_states[0].lower == pytest.approx(0.6)

    def test_bam_thin_evidence_falls_back_to_single(self, tmp_path: Path) -> None:
        """A codon whose BAM spanning-molecule count is below min_depth emits
        single-event annotations (is_combined_codon_event=False, empty
        combined_states, freq_method='observed')."""
        feature = _make_feature()
        variants = [
            VariantCall(chrom='ref', pos=0, ref='A', alt='T', allele_freq=0.8, depth=100),
            VariantCall(chrom='ref', pos=1, ref='A', alt='T', allele_freq=0.8, depth=100),
        ]
        # Only 3 reads — below min_depth=5.
        reads = [
            {'qname': f'r{i}', 'seq': 'TTA' + 'A' * 27, 'start': 0}
            for i in range(3)
        ]
        bam_path = tmp_path / 'sample.bam'
        _write_bam(bam_path, contig_len=30, reads=reads)
        bam = pysam.AlignmentFile(str(bam_path), 'rb')
        try:
            ctx = BamCooccurrence(
                bam=bam, contig='ref', cds_to_query_by_feature={"gag": {0: 0, 1: 1, 2: 2}},
                min_mapq=20, min_depth=5,
            )
            anns = _annotate_combined_snp_codon(variants, feature, bam_cooccurrence=ctx)
        finally:
            bam.close()

        assert len(anns) == 2
        assert all(a.is_combined_codon_event is False for a in anns)
        assert all(a.combined_states == [] for a in anns)
        assert all(a.freq_method == 'observed' for a in anns)

    def test_non_combined_annotation_is_observed(self) -> None:
        """A non-combined single-SNP annotation has freq_method='observed' in
        both modes (no BAM needed)."""
        feature = _make_feature()
        variants = [
            VariantCall(chrom='ref', pos=0, ref='A', alt='G', allele_freq=0.9, depth=100),
        ]
        anns = annotate_variants(variants, [feature])
        assert len(anns) == 1
        assert anns[0].freq_method == 'observed'
        assert anns[0].is_combined_codon_event is False
