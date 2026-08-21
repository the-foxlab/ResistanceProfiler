"""
Tests for intron-aware CIGAR classification in spliced-feature alignment.

When a user supplies an unspliced (whole-genome) query, minimap2 reports the
intron between two CDS exons as a single large ``I`` op in the normalized CIGAR
(CDS=reference, genome=query). These tests pin the behaviour that classifies
such ``I`` ops as introns by exon-junction CDS position (not by length) so that
downstream consumers never see the intron as a coding insertion.
"""

from __future__ import annotations

import pytest

from respro.core.alignment import (
    classify_introns,
    exon_junction_cds_offsets,
    match_query_to_features,
)
from respro.core.fasta_to_vcf import fasta_to_vcf
from respro.core.vcf_remap import _build_query_to_cds_map, remap_variants
from respro.db.models import FeatureRecord, FeatureSegment, IntronInterval, VariantCall

# ──────────────────────────────────────────────────────────────────────
# exon_junction_cds_offsets
# ──────────────────────────────────────────────────────────────────────

class TestExonJunctionCdsOffsets:
    def test_single_segment_feature_has_no_junctions(self) -> None:
        """A feature with one segment (or no segments) has no exon junctions."""
        from respro.db.models import FeatureRecord

        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=0, end=30, strand='+', codon_start=0,
        )
        assert exon_junction_cds_offsets(feature) == []

    def test_two_segment_forward_strand(self) -> None:
        """Forward strand: junction offset = length of first coding segment."""
        from respro.db.models import FeatureRecord, FeatureSegment

        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=0, end=18, strand='+', codon_start=0,
            segments=(
                FeatureSegment(segment_index=0, start=0, end=6),
                FeatureSegment(segment_index=1, start=12, end=18),
            ),
        )
        # First exon is 6 nt (0..6), so the junction is at CDS offset 6.
        assert exon_junction_cds_offsets(feature) == [6]

    def test_two_segment_reverse_strand(self) -> None:
        """Reverse strand: junction offsets use genomic (segment_index) order,
        NOT _coding_segments reversed order, because the normalized CIGAR for
        '-' strand is reversed and walks genomic 5'->3' order. With unequal
        segment lengths the two orderings yield different junction offsets, so
        this test discriminates the chosen convention."""
        from respro.db.models import FeatureRecord, FeatureSegment

        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=0, end=26, strand='-', codon_start=0,
            segments=(
                FeatureSegment(segment_index=0, start=0, end=6),    # 6 nt (genomic 5')
                FeatureSegment(segment_index=1, start=18, end=26),  # 8 nt (genomic 3')
            ),
        )
        # Genomic order (segment_index): first exon 6 nt → junction at CDS offset 6.
        # Coding order (_coding_segments reversed): first exon 8 nt → junction 8.
        # The normalized '-' CIGAR walks genomic order, so junction must be 6.
        assert exon_junction_cds_offsets(feature) == [6]

    def test_three_segment_forward_strand(self) -> None:
        """Three exons of lengths 4, 5, 6 → junctions at 4 and 9."""
        from respro.db.models import FeatureRecord, FeatureSegment

        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=0, end=40, strand='+', codon_start=0,
            segments=(
                FeatureSegment(segment_index=0, start=0, end=4),
                FeatureSegment(segment_index=1, start=10, end=15),
                FeatureSegment(segment_index=2, start=30, end=36),
            ),
        )
        assert exon_junction_cds_offsets(feature) == [4, 9]


# ──────────────────────────────────────────────────────────────────────
# classify_introns
# ──────────────────────────────────────────────────────────────────────

class TestClassifyIntrons:
    def test_no_junctions_returns_cigar_unchanged(self) -> None:
        """A single-exon feature: no I op is ever classified as an intron."""
        cigar, introns = classify_introns(
            cigar='10M3I5M', junction_offsets=[], tolerance=5,
        )
        assert cigar == '10M3I5M'
        assert introns == []

    def test_intron_at_junction_is_removed(self) -> None:
        """A 3902-nt I op exactly at the exon junction is classified as intron."""
        # 6M (exon 1) + 3902I (intron) + 7M (exon 2), junction at CDS offset 6.
        cigar, introns = classify_introns(
            cigar='6M3902I7M', junction_offsets=[6], tolerance=5,
        )
        # Adjacent M runs merge after intron removal → clean 13M.
        assert cigar == '13M'
        assert len(introns) == 1
        assert introns[0].cds_junction_pos == 6
        assert introns[0].length == 3902

    def test_intron_within_tolerance_is_removed(self) -> None:
        """An I op whose CDS position is within ±tolerance of the junction is
        classified as an intron (mappy may place the junction a few nt off)."""
        # Junction at 100; the I op sits at CDS position 102 (2 nt inside tolerance 5).
        # 102M consumed before I then 3902I then 50M.
        cigar, introns = classify_introns(
            cigar='102M3902I50M', junction_offsets=[100], tolerance=5,
        )
        assert cigar == '152M'
        assert len(introns) == 1

    def test_coding_insertion_inside_exon_is_kept(self) -> None:
        """A small I op NOT near any junction is a real coding insertion."""
        # Junction at 100; 3I at CDS position 5 (deep in exon 1, |5-100|=95 > 5).
        # 6I at CDS position 108 (|108-100|=8 > 5) → also kept (beyond tolerance).
        cigar, introns = classify_introns(
            cigar='5M3I103M6I50M', junction_offsets=[100], tolerance=5,
        )
        assert '3I' in cigar
        assert '6I' in cigar
        assert introns == []

    def test_coding_insertion_far_from_junction_is_kept(self) -> None:
        """A 3-nt insertion deep inside exon 1 (far from junction) is kept."""
        # Junction at 100; 3I at CDS position 5.
        cigar, introns = classify_introns(
            cigar='5M3I95M2000I50M', junction_offsets=[100], tolerance=5,
        )
        assert '3I' in cigar
        assert '2000I' not in cigar
        assert len(introns) == 1
        assert introns[0].length == 2000

    def test_multiple_introns_multiple_junctions(self) -> None:
        """Three exons → two junctions; two intron I ops both removed."""
        # Exons 40, 50, 60 nt. Junctions at 40 and 90.
        # 40M 100I 50M 200I 60M
        cigar, introns = classify_introns(
            cigar='40M100I50M200I60M', junction_offsets=[40, 90], tolerance=5,
        )
        assert cigar == '150M'
        assert len(introns) == 2
        assert introns[0].cds_junction_pos == 40
        assert introns[0].length == 100
        assert introns[1].cds_junction_pos == 90
        assert introns[1].length == 200

    def test_i_op_beyond_tolerance_is_kept(self) -> None:
        """An I op at a position more than `tolerance` from any junction is kept."""
        # Junction at 6; I op at CDS position 20 (tolerance 5 → 20-6=14 > 5).
        cigar, introns = classify_introns(
            cigar='20M50I10M', junction_offsets=[6], tolerance=5,
        )
        assert cigar == '20M50I10M'
        assert introns == []

    def test_deletion_ops_advance_cds_position(self) -> None:
        """D ops consume CDS positions; an intron I op after a D must still be
        detected at the right junction offset."""
        # 30M 1D 10M (consumes 41 CDS nt: 30+1+10) then 3902I then 50M, junction at 41.
        cigar, introns = classify_introns(
            cigar='30M1D10M3902I50M', junction_offsets=[41], tolerance=5,
        )
        assert '3902I' not in cigar
        assert len(introns) == 1
        assert introns[0].cds_junction_pos == 41

    def test_small_insertion_within_tolerance_is_kept(self) -> None:
        """A real coding insertion whose CDS position is within ±tolerance of a
        junction must NOT be classified as an intron if its length is ≤ tolerance.
        This is the edge-case guard: a 3-nt insertion near an exon boundary is a
        real variant, not an intron."""
        # Junction at 62; 3I at CDS position 60 (|60-62|=2 ≤ tolerance 5), and
        # length 3 ≤ tolerance 5 → kept as a real insertion.
        cigar, introns = classify_introns(
            cigar='60M3I2M200I50M', junction_offsets=[62], tolerance=5,
        )
        assert '3I' in cigar, '3-nt insertion near junction must be kept (length ≤ tolerance)'
        # The 200I is at CDS pos 62 (60M + 2M = 62; the 3I does not advance cds_pos).
        # Its query_start is 65 (60M + 3I consumes 3 query nt + 2M = 65).
        assert introns == [IntronInterval(cds_junction_pos=62, query_start=65, length=200)]

    def test_large_insertion_within_tolerance_is_classified_as_intron(self) -> None:
        """An I op within tolerance AND longer than tolerance is an intron."""
        # Junction at 62; 200I at CDS position 60 (within tolerance 5), length 200 > 5.
        cigar, introns = classify_introns(
            cigar='60M200I2M50M', junction_offsets=[62], tolerance=5,
        )
        assert '200I' not in cigar
        assert len(introns) == 1
        assert introns[0].length == 200

    def test_insertion_exactly_at_tolerance_boundary_is_kept(self) -> None:
        """An I op of length exactly equal to tolerance is kept (guard is strict >).
        Only length strictly greater than tolerance is classified as an intron."""
        cigar, introns = classify_introns(
            cigar='62M5I50M', junction_offsets=[62], tolerance=5,
        )
        assert '5I' in cigar
        assert introns == []

    def test_insertion_one_above_tolerance_boundary_is_intron(self) -> None:
        """An I op of length tolerance+1 at the junction is classified as an intron."""
        cigar, introns = classify_introns(
            cigar='62M6I50M', junction_offsets=[62], tolerance=5,
        )
        assert '6I' not in cigar
        assert len(introns) == 1
        assert introns[0].length == 6


# ──────────────────────────────────────────────────────────────────────
# match_query_to_features — end-to-end intron classification
# ──────────────────────────────────────────────────────────────────────

def _spliced_feature() -> FeatureRecord:
    """A two-exon forward-strand CDS: exon1 0..30, intron 30..60, exon2 60..90.

    The stored nt_sequence is the spliced CDS (exon1+exon2 = 60 nt).
    """
    exon1 = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt
    exon2 = 'AAGCTTTTGGCCCCAAATTTGGGCCCAAT'[:30]  # 30 nt (pad if needed)
    # Ensure exactly 30 nt each.
    exon1 = (exon1 + 'A' * 30)[:30]
    exon2 = (exon2 + 'A' * 30)[:30]
    return FeatureRecord(
        id=1, reference_id=1, name='spl', protein='Spl',
        start=0, end=90, strand='+', codon_start=0,
        nt_sequence=exon1 + exon2,
        segments=(
            FeatureSegment(segment_index=0, start=0, end=30),
            FeatureSegment(segment_index=1, start=60, end=90),
        ),
    )


class TestMatchQueryToFeaturesSpliced:
    def test_unspliced_query_classifies_intron_and_reports_exon_identity(self) -> None:
        """An unspliced query (exon1+intron+exon2) yields an exon-only CIGAR,
        one intron interval, identity≈1.0 (not crashed by the intron), and
        cds_coverage=1.0 over the exon span."""
        feature = _spliced_feature()
        intron = 'N' * 30  # 30-nt intron between the two exons
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]

        # The intron I op must be removed from the exon-only CIGAR.
        assert 'I' not in m.cigar or '30I' not in m.cigar
        # One intron interval recorded at the exon junction (CDS offset 30).
        assert len(m.intron_intervals) == 1
        assert m.intron_intervals[0].cds_junction_pos == 30
        assert m.intron_intervals[0].length == 30
        # Identity reflects exons only (perfect match → ~1.0), not crashed by intron.
        assert m.identity > 0.95
        # CDS coverage is 1.0 over the exon span (60 nt).
        assert m.cds_coverage == 1.0

    def test_spliced_query_no_intron_op_classified(self) -> None:
        """A spliced query (exons only, no intron) leaves the CIGAR with no
        intron intervals — the happy path is unchanged."""
        feature = _spliced_feature()
        query = feature.nt_sequence  # already spliced

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.intron_intervals == ()
        assert m.identity > 0.95

    def test_reverse_strand_spliced_feature_classifies_intron(self) -> None:
        """A '-' strand two-exon CDS aligned against an unspliced query must
        classify the intron at the correct exon junction. This is the HCMV
        UL89 scenario: the normalized CIGAR is reversed for '-' strand, so
        junction offsets must be in genomic (segment_index) order, not coding
        order, to match the CIGAR's walking order."""
        from Bio.Seq import Seq

        exon1 = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt, genomic 5' segment
        exon2 = 'AAGCTTTTGGCCCCAAATTTGGGCCCAAT'[:30]
        exon2 = (exon2 + 'A' * 30)[:30]
        # '-' strand: the spliced CDS is revcomp(exon1+exon2) read 5'->3'.
        spliced_cds = str(Seq(exon1 + exon2).reverse_complement())
        feature = FeatureRecord(
            id=2, reference_id=1, name='spl_minus', protein='SplM',
            start=0, end=90, strand='-', codon_start=0,
            nt_sequence=spliced_cds,
            segments=(
                FeatureSegment(segment_index=0, start=0, end=30),
                FeatureSegment(segment_index=1, start=60, end=90),
            ),
        )
        # Unspliced query: exon1 + intron + exon2 (genomic 5'->3').
        intron = 'N' * 30
        query = exon1 + intron + exon2

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        # The intron must be classified (one interval), not left as a giant I op.
        assert len(m.intron_intervals) == 1, (
            f'expected 1 intron interval for - strand spliced feature, got {m.intron_intervals}'
        )
        assert m.intron_intervals[0].length == 30
        # Exon-only CIGAR must not contain the 30I intron op.
        assert '30I' not in m.cigar
        # Identity reflects exons only.
        assert m.identity > 0.95
        assert m.cds_coverage == 1.0


# ──────────────────────────────────────────────────────────────────────
# fasta_to_vcf — intron must not become a giant insertion variant
# ──────────────────────────────────────────────────────────────────────

class TestFastaToVcfIntron:
    def test_unspliced_query_emits_no_intron_insertion_variant(self) -> None:
        """The prove-it test for the real bug: an unspliced query (exon1 +
        intron + exon2) must NOT emit a giant ~intron-length frameshift
        insertion variant. Only genuine exon SNPs (if any) are emitted."""
        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        variants, gaps = fasta_to_vcf(query, matches)

        # No variant may have an ALT longer than a few bases (no 30-nt intron insertion).
        intron_insertions = [v for v in variants if len(v.alt) - len(v.ref) >= 30]
        assert intron_insertions == [], (
            f'Expected no intron-length insertion variant, got {intron_insertions}'
        )
        # A perfect unspliced query (exons match exactly) yields zero variants.
        assert variants == []

    def test_unspliced_query_reports_exon_only_identity_and_coverage(self) -> None:
        """For a perfect unspliced query, identity≈1.0 and cds_coverage=1.0
        (computed over exons, not the genomic span including the intron)."""
        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0, abs=1e-6)
        assert m.cds_coverage == pytest.approx(1.0, abs=1e-6)
        assert m.intron_intervals, 'spliced alignment should carry intron intervals'

    def test_spliced_query_happy_path_unchanged(self) -> None:
        """A spliced query (exons only, no intron) follows the unchanged happy
        path: no intron I op is present, so nothing is classified as an intron
        and identity/coverage reflect a perfect match."""
        feature = _spliced_feature()
        query = feature.nt_sequence  # spliced CDS, no intron

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.intron_intervals == ()
        assert m.identity == pytest.approx(1.0, abs=1e-6)
        variants, _ = fasta_to_vcf(query, matches)
        assert variants == []

    def test_unspliced_query_with_exon_snp_emits_only_the_snp(self) -> None:
        """A SNP in exon 2 of an unspliced query is emitted; the intron is not."""
        feature = _spliced_feature()
        intron = 'N' * 30
        # Introduce one SNP at exon 2 position 45 (CDS offset 45).
        exon2_snp = (
            feature.nt_sequence[:30]
            + intron
            + feature.nt_sequence[30:45]
            + ('T' if feature.nt_sequence[45] != 'T' else 'A')
            + feature.nt_sequence[46:]
        )
        matches = match_query_to_features(exon2_snp, [feature])
        assert len(matches) == 1
        variants, _ = fasta_to_vcf(exon2_snp, matches)

        # Exactly one variant (the exon-2 SNP), no intron insertion.
        intron_insertions = [v for v in variants if len(v.alt) - len(v.ref) >= 30]
        assert intron_insertions == []
        assert len(variants) == 1

    def test_real_coding_insertion_inside_exon_is_still_emitted(self) -> None:
        """A genuine 3-nt coding insertion inside exon 1 (not at the junction)
        is still emitted as a variant — intron classification must not suppress
        real in-exon insertions. Tested on a spliced query (no intron present)
        so the insertion is the only indel; the intron-classification path is
        a no-op here (no junction I op), proving it never suppresses real I ops."""
        feature = _spliced_feature()
        # Insert 3 nt 'AAA' at CDS offset 10 (inside exon 1, far from junction 30).
        query = feature.nt_sequence[:10] + 'AAA' + feature.nt_sequence[10:]

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        # No intron classified (the 3I is at offset 10, not near junction 30).
        assert matches[0].intron_intervals == ()
        variants, _ = fasta_to_vcf(query, matches)

        # The 3-nt insertion should appear as an insertion variant (alt longer than ref).
        insertions = [v for v in variants if len(v.alt) > len(v.ref)]
        assert any(len(v.alt) - len(v.ref) == 3 for v in insertions), (
            f'Expected a 3-nt coding insertion variant, got {variants}'
        )


# ──────────────────────────────────────────────────────────────────────
# VCF remap — intron-aware query-to-CDS coordinate map
# ──────────────────────────────────────────────────────────────────────

class TestVcfRemapIntron:
    def test_exon2_variant_in_unspliced_query_remaps_to_second_segment(self) -> None:
        """A VCF variant in exon 2 of an unspliced query (exon1+intron+exon2)
        must remap to the correct second-segment genomic position, not be
        shifted by the intron length or land in the intron query span."""
        feature = _spliced_feature()  # exon1 0..30, exon2 60..90 (genomic)
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert len(m.intron_intervals) == 1

        # A variant at the first base of exon 2 in the query.
        # Query layout: exon1 [0..30) + intron [30..60) + exon2 [60..90).
        # First exon-2 base is at query position 60.
        exon2_first_base = query[60]
        var = VariantCall(
            chrom='c', pos=60, ref=exon2_first_base, alt='T', allele_freq=1.0, depth=50,
        )
        remapped, warnings = remap_variants([var], [m], query)

        assert not warnings
        assert len(remapped) == 1
        # Exon 2 starts at genomic position 60; first exon-2 base → genomic 60.
        assert remapped[0].pos == 60

    def test_variant_inside_intron_query_span_is_excluded(self) -> None:
        """A VCF variant whose query position falls inside the intron span is
        not remapped (it is non-coding)."""
        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        m = matches[0]
        # Variant at query position 45 (middle of the intron span 30..60).
        var = VariantCall(chrom='c', pos=45, ref='N', alt='A', allele_freq=1.0, depth=50)
        remapped, warnings = remap_variants([var], [m], query)

        assert remapped == []

    def test_build_query_to_cds_map_skips_intron_query_span(self) -> None:
        """The query-to-CDS map must skip intron query positions and offset
        exon-2 CDS positions past the intron span."""
        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]

        matches = match_query_to_features(query, [feature])
        m = matches[0]

        q2c = _build_query_to_cds_map(
            m.cigar, m.query_start, m.query_end, m.strand, len(query), m.cds_start,
            m.intron_intervals,
        )
        # Exon 1: query 0..30 → CDS 0..30.
        assert q2c[0] == 0
        assert q2c[29] == 29
        # Intron query positions 30..59 must NOT be in the map.
        for qpos in range(30, 60):
            assert qpos not in q2c, f'intron query pos {qpos} should not map to a CDS pos'
        # Exon 2: query 60 → CDS 30 (first base of exon 2 in the spliced CDS).
        assert q2c[60] == 30
        assert q2c[89] == 59

    def test_reverse_strand_exon_variants_remap_correctly(self) -> None:
        """'-' strand spliced feature against an unspliced query: exon-1 and
        exon-2 VCF variants must remap to the correct genomic segments, and
        intron-span variants must be excluded. This is the HCMV UL89 scenario
        (the motivating bug)."""
        from Bio.Seq import Seq

        exon1 = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt, genomic seg0 [0..30)
        exon2 = 'AAGCTTTTGGCCCCAAATTTGGGCCCAAT'[:30]
        exon2 = (exon2 + 'A' * 30)[:30]
        spliced_cds = str(Seq(exon1 + exon2).reverse_complement())
        feature = FeatureRecord(
            id=3, reference_id=1, name='splm', protein='SplM',
            start=0, end=90, strand='-', codon_start=0,
            nt_sequence=spliced_cds,
            segments=(
                FeatureSegment(segment_index=0, start=0, end=30),
                FeatureSegment(segment_index=1, start=60, end=90),
            ),
        )
        intron = 'N' * 30
        query = exon1 + intron + exon2  # unspliced, genomic 5'->3'

        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert len(m.intron_intervals) == 1, (
            f'expected 1 intron interval, got {m.intron_intervals}'
        )

        q2c = _build_query_to_cds_map(
            m.cigar, m.query_start, m.query_end, m.strand, len(query), m.cds_start,
            m.intron_intervals,
        )

        # Intron query positions [30..60) must NOT be in the map.
        for qpos in range(30, 60):
            assert qpos not in q2c, f'intron query pos {qpos} should not map to a CDS pos'

        # For '-' strand the spliced CDS is revcomp(exon1+exon2); coding 5'->3'
        # order is seg1 (genomic exon2) then seg0 (genomic exon1). The normalized
        # CIGAR walks genomic 5'->3' (seg0 then seg1), so:
        #   query [0..30)  = genomic exon1 (seg0) → CDS offsets [30..60)
        #   query [60..90) = genomic exon2 (seg1) → CDS offsets [0..30)
        exon1_cds = {q2c[q] for q in range(0, 30) if q in q2c}
        exon2_cds = {q2c[q] for q in range(60, 90) if q in q2c}
        assert exon1_cds, 'exon-1 query positions must map to CDS positions'
        assert exon2_cds, 'exon-2 query positions must map to CDS positions'
        assert all(30 <= c < 60 for c in exon1_cds), (
            f'exon-1 (genomic seg0) CDS offsets must be in [30,60), got {exon1_cds}'
        )
        assert all(0 <= c < 30 for c in exon2_cds), (
            f'exon-2 (genomic seg1) CDS offsets must be in [0,30), got {exon2_cds}'
        )

        # End-to-end remap: an exon-2 variant (query pos 60, first base of seg1)
        # maps to CDS offset 29 (last coding base of seg1) → genomic 60, inside
        # segment 1 [60..90). A regression that mis-remaps to seg0 must fail.
        var_exon2 = VariantCall(
            chrom='c', pos=60, ref=query[60], alt='T', allele_freq=1.0, depth=50,
        )
        remapped, warnings = remap_variants([var_exon2], [m], query)
        assert not warnings, f'unexpected warnings: {warnings}'
        assert len(remapped) == 1, f'expected 1 remapped variant, got {remapped}'
        genomic = remapped[0].pos
        assert genomic == 60, (
            f'exon-2 variant should remap to genomic 60 (seg1), got {genomic}'
        )

        # An intron-span variant is excluded.
        var_intron = VariantCall(
            chrom='c', pos=45, ref='N', alt='A', allele_freq=1.0, depth=50,
        )
        remapped_intron, _ = remap_variants([var_intron], [m], query)
        assert remapped_intron == []

    def test_adjacent_m_runs_merged_in_exon_only_cigar(self) -> None:
        """After removing an intron I op, adjacent M runs are merged."""
        cigar, introns = classify_introns(
            cigar='6M3902I7M', junction_offsets=[6], tolerance=5,
        )
        # 6M + 7M should merge to 13M for a clean exon-only CIGAR.
        assert cigar == '13M'


# ──────────────────────────────────────────────────────────────────────
# Cache round-trip for intron_intervals
# ──────────────────────────────────────────────────────────────────────

class TestCacheIntronRoundTrip:
    """Storing and reloading a FeatureMatch with intron_intervals must preserve
    the intron intervals and the exon-only CIGAR exactly."""

    def test_store_load_preserves_intron_intervals(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
        from respro.db.schema import create_schema, open_project_db

        db_path = tmp_path / 'test_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)', ('Test', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref1', 90),
        )
        feature = _spliced_feature()
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, '
            'codon_start, nt_sequence, aa_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                feature.reference_id, feature.name, feature.protein,
                feature.start, feature.end, feature.strand,
                feature.codon_start, feature.nt_sequence, feature.aa_sequence,
            ),
        )
        for seg in feature.segments:
            conn.execute(
                'INSERT INTO feature_segment (feature_id, segment_index, start, end) '
                'VALUES (?, ?, ?, ?)',
                (feature.id, seg.segment_index, seg.start, seg.end),
            )
        conn.commit()

        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]
        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        assert matches[0].intron_intervals, 'spliced alignment should carry intron intervals'

        chk = sequence_checksum(query)
        store_mappings(conn, 'unspliced_ref', query, chk, matches)
        conn.close()

        # Re-open via open_project_db to exercise the optional-column migration.
        conn2 = open_project_db(db_path)
        loaded = load_cached_mappings(conn2, chk)
        conn2.close()

        assert loaded is not None
        assert len(loaded) == 1
        m = loaded[0]
        assert m.cigar == matches[0].cigar
        assert m.intron_intervals == matches[0].intron_intervals
        assert len(m.intron_intervals) == 1
        iv = m.intron_intervals[0]
        assert iv.cds_junction_pos == 30
        assert iv.query_start == 30
        assert iv.length == 30

    def test_legacy_rows_without_intron_column_backfill_as_empty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A row inserted before the intron_intervals column existed must load
        with an empty intron_intervals tuple after migration."""
        from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
        from respro.db.schema import create_schema, open_project_db

        db_path = tmp_path / 'test_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)', ('Test', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref1', 30),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, '
            'nt_sequence, aa_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (1, 'gag', 'Gag', 0, 30, '+', 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC', 'MKAFGPKFGP'),
        )
        conn.commit()

        feature = FeatureRecord(
            id=1, reference_id=1, name='gag', protein='Gag',
            start=0, end=30, strand='+', codon_start=0,
            nt_sequence='ATGAAAGCTTTTGGCCCCAAATTTGGGCCC', aa_sequence='MKAFGPKFGP',
            segments=tuple(),
        )
        from respro.db.models import FeatureMatch
        legacy_match = FeatureMatch(
            feature=feature, identity=1.0, cds_coverage=1.0, query_coverage=1.0,
            cds_start=0, query_start=0, query_end=30, strand='+', cigar='30M',
            intron_intervals=tuple(),
        )
        query = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
        chk = sequence_checksum(query)
        store_mappings(conn, 'legacy_ref', query, chk, [legacy_match])
        conn.close()

        conn2 = open_project_db(db_path)
        loaded = load_cached_mappings(conn2, chk)
        conn2.close()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].intron_intervals == tuple()


# ──────────────────────────────────────────────────────────────────────
# Alignment visualization for spliced features (verification)
# ──────────────────────────────────────────────────────────────────────

class TestAlignmentVisualizationSpliced:
    """The per-codon alignment snippet must render exon1→exon2 with no intron
    gap row, because the exon-only CIGAR + intron-stripped region leave only
    coding bases."""

    def test_snippet_has_no_intron_gap_and_exon2_aligns(self) -> None:
        from respro.report.alignment_visualization import build_feature_alignments

        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]
        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.intron_intervals, 'spliced alignment should carry intron intervals'

        alns = build_feature_alignments(query, [m])
        assert 'spl' in alns
        aln = alns['spl']
        # No gap row spanning ~30 intron bases: the aligned strings must be
        # exactly the spliced CDS length (60) with no long '-' run.
        assert len(aln.aligned_ref) == len(aln.aligned_query)
        # The longest run of '-' in aligned_ref must be far shorter than the
        # 30-nt intron (at most a few nt for any unaligned CDS tail).
        import re as _re
        longest_gap = max((len(run) for run in _re.findall(r'-+', aln.aligned_ref)), default=0)
        assert longest_gap < 10, f'intron gap leaked into snippet: {longest_gap}-nt gap'
        # Exon 2 query bases must align to exon 2 CDS bases (first exon-2 base
        # is the last char of the spliced CDS region after stripping).
        # The aligned query must contain exon2 sequence contiguously.
        exon2 = feature.nt_sequence[30:]
        assert exon2 in aln.aligned_query.replace('-', ''), 'exon 2 not aligned contiguously'

    def test_identity_is_exon_only_not_genomic(self) -> None:
        """match.identity carried into the report must reflect exon-only identity
        (~1.0 for a perfect exons match), not the genomic 33.96%."""
        feature = _spliced_feature()
        intron = 'N' * 30
        query = feature.nt_sequence[:30] + intron + feature.nt_sequence[30:]
        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        assert matches[0].identity > 0.99
