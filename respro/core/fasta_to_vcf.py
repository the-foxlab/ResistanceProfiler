"""FASTA consensus profiling — nucleotide-level variant emission from aligned sequence."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from Bio.Seq import Seq

from respro.core.coordinates import (
    aligned_cds_length,
    build_ref_positions,
    gapped_strings_from_cigar,
    strip_introns_from_region,
)
from respro.db.models import CoverageGap, FeatureMatch, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

_IUPAC_OPTIONS: dict[str, frozenset[str]] = {
    'A': frozenset('A'),
    'C': frozenset('C'),
    'G': frozenset('G'),
    'T': frozenset('T'),
    'R': frozenset('AG'),
    'Y': frozenset('CT'),
    'S': frozenset('GC'),
    'W': frozenset('AT'),
    'K': frozenset('GT'),
    'M': frozenset('AC'),
    'B': frozenset('CGT'),
    'D': frozenset('AGT'),
    'H': frozenset('ACT'),
    'V': frozenset('ACG'),
    'N': frozenset('ACGT'),
}


def fasta_to_vcf(
    query_seq: str,
    matches: list[FeatureMatch],
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """Convert aligned FASTA consensus differences into VariantCall records and coverage gaps."""
    variants: list[VariantCall] = []
    coverage_gaps: list[CoverageGap] = []
    for match in matches:
        feature_variants, feature_gaps = _profile_feature_to_variants(query_seq, match)
        variants.extend(feature_variants)
        coverage_gaps.extend(feature_gaps)

    logger.info(
        'FASTA to VCF: %d variant call(s) from %d feature(s), %d non-covered stretch(es) '
        '(%d codon position(s) total)',
        len(variants), len(matches), len(coverage_gaps),
        sum(gap.codon_end - gap.codon_start + 1 for gap in coverage_gaps),
    )
    return variants, coverage_gaps


def _profile_feature_to_variants(
    query_seq: str,
    match: FeatureMatch,
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """Project one aligned feature match to VariantCall records plus coverage gaps."""
    feature = match.feature
    if not feature.nt_sequence:
        logger.warning('Feature %r has no stored CDS sequence — skipping', feature.name)
        return [], []

    region = query_seq[match.query_start:match.query_end].upper()
    if match.strand == '-':
        region = str(Seq(region).reverse_complement())
    region = strip_introns_from_region(region, match.intron_intervals)

    aligned_ref, aligned_query = gapped_strings_from_cigar(
        feature.nt_sequence.upper(),
        region,
        match.cigar,
        match.cds_start,
    )
    return _variants_from_alignment(
        aligned_ref,
        aligned_query,
        feature,
        covered_cds_start=match.cds_start,
        covered_cds_end=match.cds_start + aligned_cds_length(match.cigar),
    )


def _variants_from_alignment(
    aligned_ref: str,
    aligned_query: str,
    feature: FeatureRecord,
    covered_cds_start: int | None = None,
    covered_cds_end: int | None = None,
    ref_positions: Sequence[tuple[str, str, str]] | None = None,
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """Emit nucleotide-level VariantCall records and coverage gaps from gapped CDS strings."""
    positions = build_ref_positions(aligned_ref, aligned_query) if ref_positions is None else ref_positions
    return variants_from_columns(
        positions,
        feature,
        covered_cds_start=covered_cds_start,
        covered_cds_end=covered_cds_end,
    )


def variants_from_columns(
    ref_positions: Sequence[tuple[str, str, str]],
    feature: FeatureRecord,
    covered_cds_start: int | None = None,
    covered_cds_end: int | None = None,
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """Walk per-reference-base ``(ref, query, pending_ins)`` columns to emit variants and gaps."""
    frame = feature.codon_start
    coding = ref_positions[frame:]
    ref_bases = ''.join(ref_base for ref_base, _, _ in coding)

    gap_codon_indices = _non_assessable_codon_indices(
        coding, frame, covered_cds_start, covered_cds_end,
    )
    non_assessable = set(gap_codon_indices)

    variants: list[VariantCall] = []
    ref_idx = 0
    while ref_idx < len(coding):
        ref_base, query_base, ins_before = coding[ref_idx]

        if ref_idx // 3 in non_assessable:
            ref_idx = _advance_past_deletion(coding, ref_idx) if query_base == '-' else ref_idx + 1
            continue

        if ins_before:
            variant = make_coding_insertion(feature, ref_bases, ref_idx, ins_before)
            if variant is not None:
                variants.append(variant)

        if query_base == '-':
            del_end = _deletion_run_end(coding, ref_idx)
            if _deletion_within_coverage(ref_idx, del_end, frame, covered_cds_start, covered_cds_end):
                variant = make_coding_deletion(feature, ref_bases, ref_idx, del_end)
                if variant is not None:
                    variants.append(variant)
            ref_idx = del_end + 1
            continue

        if ref_base != query_base:
            query_codon = _query_codon_at(coding, ref_idx)
            clean_codon = query_codon if '-' not in query_codon else ''
            for alt_base, af in _iupac_alt_bases(ref_base, query_base):
                var = make_coding_variant(feature, ref_idx, ref_base, alt_base, af=af)
                var.query_ref_codon = clean_codon
                var.query_codon_feature_id = feature.id
                variants.append(var)

        ref_idx += 1

    return variants, CoverageGap.merge(feature.name, gap_codon_indices)


def _deletion_run_end(coding: Sequence[tuple[str, str, str]], start_idx: int) -> int:
    """Return the inclusive end index of consecutive query-gap positions starting at ``start_idx``."""
    end_idx = start_idx
    while end_idx + 1 < len(coding) and coding[end_idx + 1][1] == '-':
        end_idx += 1
    return end_idx


def _advance_past_deletion(coding: Sequence[tuple[str, str, str]], start_idx: int) -> int:
    """Return the first index after a deletion run starting at ``start_idx``."""
    return _deletion_run_end(coding, start_idx) + 1


def _non_assessable_codon_indices(
    coding: Sequence[tuple[str, str, str]],
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> list[int]:
    """Return codon indices outside the aligned span or consisting entirely of ``NNN``."""
    gaps: list[int] = []
    for codon_idx in range(len(coding) // 3):
        nt_start = frame + codon_idx * 3
        outside = (
            covered_cds_start is not None
            and covered_cds_end is not None
            and (nt_start < covered_cds_start or nt_start + 3 > covered_cds_end)
        )
        if outside:
            gaps.append(codon_idx)
            continue
        codon_slice = coding[codon_idx * 3:codon_idx * 3 + 3]
        if ''.join(q for _, q, _ in codon_slice).upper() == 'NNN':
            gaps.append(codon_idx)
    return gaps


def _query_codon_at(coding: Sequence[tuple[str, str, str]], coding_nt_idx: int) -> str:
    """Return the 3-base query codon covering ``coding_nt_idx``, or empty if incomplete."""
    codon_start = (coding_nt_idx // 3) * 3
    if codon_start + 3 > len(coding):
        return ''
    return ''.join(coding[codon_start + offset][1] for offset in range(3))


def _iupac_alt_bases(ref_base: str, query_base: str) -> list[tuple[str, float]]:
    """Expand an IUPAC query base into ``(alt_base, af)`` pairs with fractional frequencies."""
    ref_upper = ref_base.upper()
    options = _IUPAC_OPTIONS.get(query_base.upper(), frozenset((query_base.upper(),)))
    non_ref_alts = sorted(base for base in options if base != ref_upper)
    if not non_ref_alts:
        return []
    # Probability mass splits across the full option set (ref included), not just non-ref ALTs.
    af_each = 1.0 / len(options)
    return [(alt, af_each) for alt in non_ref_alts]


def _deletion_within_coverage(
    start_idx: int,
    end_idx: int,
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> bool:
    """Return whether an entire deletion run lies inside the aligned CDS span."""
    if covered_cds_start is None or covered_cds_end is None:
        return True
    return (frame + start_idx) >= covered_cds_start and (frame + end_idx + 1) <= covered_cds_end


def _coding_nt_genomic_pos(feature: FeatureRecord, coding_nt_idx: int) -> int:
    """Return the 0-based internal genomic position of one coding nucleotide index."""
    genomic_pos = feature.cds_to_genomic_position(feature.codon_start + coding_nt_idx)
    if genomic_pos is None:
        raise ValueError(
            f'Coding nucleotide index {coding_nt_idx} is outside CDS for feature {feature.name!r}'
        )
    return genomic_pos


def make_coding_variant(
    feature: FeatureRecord,
    coding_nt_idx: int,
    ref: str,
    alt: str,
    af: float = 1.0,
) -> VariantCall:
    """Build a synthetic VariantCall anchored at one coding nucleotide index (genomic orientation)."""
    if feature.strand == '-':
        ref = str(Seq(ref).reverse_complement())
        alt = str(Seq(alt).reverse_complement())
    return VariantCall(
        chrom=feature.name,
        pos=_coding_nt_genomic_pos(feature, coding_nt_idx),
        ref=ref,
        alt=alt,
        allele_freq=af,
        depth=0,
        filter_status='PASS',
    )


def make_coding_insertion(
    feature: FeatureRecord,
    ref_bases: Sequence[str],
    idx: int,
    inserted_bases: str,
) -> VariantCall | None:
    """Build a VCF-anchored insertion call placed immediately before coding index ``idx``."""
    # Plus-strand VCF anchors on the preceding coding base; minus-strand genomic left-anchoring
    # sits on the current coding base (which becomes the genomic base preceding the RC insertion).
    anchor_idx = idx - 1 if feature.strand == '+' else idx
    if anchor_idx < 0:
        return None
    anchor_nt = ref_bases[anchor_idx]
    if feature.strand == '-':
        # RC(inserted + anchor) == RC(anchor) + RC(inserted), preserving VCF left-anchor order.
        return make_coding_variant(feature, anchor_idx, anchor_nt, inserted_bases + anchor_nt)
    return make_coding_variant(feature, anchor_idx, anchor_nt, anchor_nt + inserted_bases)


def make_coding_deletion(
    feature: FeatureRecord,
    ref_bases: Sequence[str],
    start_idx: int,
    end_idx: int,
) -> VariantCall | None:
    """Build a VCF-anchored deletion call spanning coding indices ``start_idx..end_idx`` (inclusive)."""
    deleted_bases = ''.join(ref_bases[start_idx:end_idx + 1])
    if not deleted_bases:
        return None
    # Plus-strand anchors before start_idx; minus-strand genomic left-anchor is after end_idx.
    anchor_idx = start_idx - 1 if feature.strand == '+' else end_idx + 1
    if anchor_idx < 0 or anchor_idx >= len(ref_bases):
        return None
    anchor_nt = ref_bases[anchor_idx]
    if feature.strand == '-':
        # RC(deleted + anchor) == RC(anchor) + RC(deleted), preserving VCF left-anchor order.
        return make_coding_variant(feature, anchor_idx, deleted_bases + anchor_nt, anchor_nt)
    return make_coding_variant(feature, anchor_idx, anchor_nt + deleted_bases, anchor_nt)
