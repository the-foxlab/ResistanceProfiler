"""
Tests for BAM-to-internal-reference coverage projection in VCF mode.
"""

from __future__ import annotations

from respro.core.vcf_coverage import compute_coverage_gaps_from_depth
from respro.db.models import GeneMatch, GeneRecord


def _make_gene() -> GeneRecord:
    return GeneRecord(
        id=1,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=9,
        strand='+',
        codon_start=0,
        nt_sequence='ATGAAATTT',
    )


def _make_match(gene: GeneRecord, cigar: str, query_len: int) -> GeneMatch:
    return GeneMatch(
        gene=gene,
        identity=1.0,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=query_len,
        strand='+',
        cigar=cigar,
        cds_start=0,
    )


class TestVcfCoverageProjection:
    def test_cds_start_offset_is_respected_for_projection(self) -> None:
        gene = _make_gene()
        # Alignment starts at CDS offset 3, so query nt 0 maps to CDS nt 3.
        match = GeneMatch(
            gene=gene,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=6,
            strand='+',
            cigar='6M',
            cds_start=3,
        )
        # Good depth only over mapped region (query 0..5).
        depths = [20] * 6

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=6)

        # Codon 0 (CDS nt 0..2) is unmappable, codons 1-2 are covered.
        assert len(gaps) == 1
        assert (gaps[0].codon_start, gaps[0].codon_end) == (0, 0)

    def test_marks_codon_non_covered_when_internal_nt_not_projectable(self) -> None:
        gene = _make_gene()
        # One deletion in query relative to CDS: codon 2 loses one projected nt.
        match = _make_match(gene, cigar='4M1D4M', query_len=8)
        depths = [30] * 8

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=8)

        assert len(gaps) == 1
        assert gaps[0].gene_name == 'gag'
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 1

    def test_marks_codon_non_covered_when_depth_below_threshold(self) -> None:
        gene = _make_gene()
        match = _make_match(gene, cigar='9M', query_len=9)
        depths = [20, 20, 20, 20, 20, 20, 20, 2, 20]

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=9)

        assert len(gaps) == 1
        assert gaps[0].codon_start == 2
        assert gaps[0].codon_end == 2

    def test_merges_adjacent_non_covered_codons(self) -> None:
        gene = _make_gene()
        match = _make_match(gene, cigar='9M', query_len=9)
        # Codon 2 and 3 are below depth threshold.
        depths = [20, 20, 20, 2, 2, 2, 1, 1, 1]

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=9)

        assert len(gaps) == 1
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 2
