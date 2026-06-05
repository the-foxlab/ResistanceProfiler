"""
FASTA consensus profiling — nucleotide-level variant emission from aligned sequence.

Converts FASTA consensus differences into nucleotide-level VariantCall records
that mirror VCF format and feed into the shared annotation pipeline for amino acid
consequence interpretation.

Handles:
- SNPs (with IUPAC expansion to multiple ALTs, adjusted AF)
- Insertions (ref gaps accumulated into single variant)
- Deletions (query gaps accumulated into single variant)
- Coverage gaps (N-run codons reported as CoverageGap)

All variant interpretation (codon translation, amino acid consequences, etc.)
delegates to the shared `annotate_variants()` pipeline.
"""

from __future__ import annotations

import logging
import re

from Bio.Seq import Seq

from respro.core.vcf_coverage import _merge_codon_gaps
from respro.db.models import CoverageGap, FeatureMatch, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)


def fasta_to_vcf(
    query_seq: str,
    matches: list[FeatureMatch],
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """
    Convert FASTA consensus differences into a VCF-like VariantCall stream.

    This path emits nucleotide-level VariantCall records and leaves amino-acid
    consequence interpretation to the shared annotation pipeline.

    :param query_seq: full query FASTA sequence (forward strand, upper case)
    :param matches: feature matches from sequence alignment
    :return: (variant calls, coverage gaps)
    """
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

    aligned_cds_len = sum(int(n) for n, op in re.findall(r'(\d+)([MD])', match.cigar))
    covered_cds_start = match.cds_start
    covered_cds_end = match.cds_start + aligned_cds_len

    aligned_ref, aligned_query = _gapped_strings_from_cigar(
        feature.nt_sequence.upper(),
        region,
        match.cigar,
        match.cds_start,
    )
    return _variants_from_alignment(
        aligned_ref,
        aligned_query,
        feature,
        covered_cds_start=covered_cds_start,
        covered_cds_end=covered_cds_end,
    )


def _variants_from_alignment(
    aligned_ref: str,
    aligned_query: str,
    feature: FeatureRecord,
    covered_cds_start: int | None = None,
    covered_cds_end: int | None = None,
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """
    Emit nucleotide-level VariantCall records from aligned CDS vs query.

    Finds three types of differences:
    - SNPs: ref_base != query_base (neither is a gap)
    - Insertions: ref_base is '-' (query has bases)
    - Deletions: query_base is '-' (ref has bases)

    IUPAC bases in the query are expanded to multiple ALTs with adjusted frequency.
    N-runs ('NNN') in codons are reported as CoverageGaps.

    :param aligned_ref: reference CDS with gap characters
    :param aligned_query: query CDS with gap characters
    :param feature: feature record with stored nt_sequence
    :param covered_cds_start: CDS nt start of aligned region (for gap detection)
    :param covered_cds_end: CDS nt end of aligned region (for gap detection)
    :return: (variant calls, coverage gaps)
    """
    frame = feature.codon_start
    variants: list[VariantCall] = []
    gap_codon_indices: list[int] = []

    # Parse alignment into per-ref-position tuples: (ref, query, insertions_before_this_ref)
    ref_positions: list[tuple[str, str, str]] = []
    pending_ins = ''
    for ref_base, query_base in zip(aligned_ref, aligned_query):
        if ref_base == '-':
            pending_ins += query_base
        else:
            ref_positions.append((ref_base.upper(), query_base.upper(), pending_ins.upper()))
            pending_ins = ''

    coding = ref_positions[frame:]  # Skip non-coding frame offset

    # Detect non-assessable codons for coverage gaps
    codon_count = len(coding) // 3
    for codon_idx in range(codon_count):
        codon_start = codon_idx * 3
        codon_slice = coding[codon_start:codon_start + 3]

        if _codon_outside_coverage(
            codon_idx=codon_idx,
            frame=frame,
            covered_cds_start=covered_cds_start,
            covered_cds_end=covered_cds_end,
        ):
            gap_codon_indices.append(codon_idx)
            continue

        query_codon = ''.join(q for _, q, _ in codon_slice)
        if query_codon.upper() == 'NNN':
            gap_codon_indices.append(codon_idx)

    non_assessable_codons = set(gap_codon_indices)

    # Emit variants by iterating ungapped ref positions
    ref_idx = 0
    while ref_idx < len(coding):
        ref_base, query_base, ins_before = coding[ref_idx]
        codon_idx = ref_idx // 3

        if codon_idx in non_assessable_codons:
            if query_base == '-':
                del_end = ref_idx
                while del_end + 1 < len(coding) and coding[del_end + 1][1] == '-':
                    del_end += 1
                ref_idx = del_end + 1
                continue
            ref_idx += 1
            continue

        # Handle insertion before this reference position
        if ins_before:
            variant = _make_fasta_insertion_from_alignment(feature, coding, ref_idx, ins_before)
            if variant:
                variants.append(variant)

        # Handle SNPs (including IUPAC expansion)
        if query_base != '-' and ref_base != query_base:
            query_codon = _get_query_codon(coding, ref_idx)
            for alt_base, af in _iupac_alt_bases(ref_base, query_base):
                var = _make_variant_from_coding_nt(feature, ref_idx, ref_base, alt_base, af=af)
                var.query_ref_codon = query_codon if '-' not in query_codon else ''
                variants.append(var)

        # Handle deletion at this position (accumulate run of deletions)
        if query_base == '-':
            del_end = ref_idx
            while del_end + 1 < len(coding) and coding[del_end + 1][1] == '-':
                del_end += 1
            if _deletion_run_within_coverage(
                start_idx=ref_idx,
                end_idx=del_end,
                frame=frame,
                covered_cds_start=covered_cds_start,
                covered_cds_end=covered_cds_end,
            ):
                variant = _make_fasta_deletion_from_alignment(feature, coding, ref_idx, del_end)
                if variant:
                    variants.append(variant)
            ref_idx = del_end + 1
            continue

        ref_idx += 1

    return variants, _merge_codon_gaps(feature.name, gap_codon_indices)


def _get_query_codon(coding: list[tuple[str, str, str]], coding_nt_idx: int) -> str:
    """Return the query codon string for one coding-nt index when available."""
    codon_start = (coding_nt_idx // 3) * 3
    if codon_start + 3 > len(coding):
        return ''
    return ''.join(coding[codon_start + offset][1] for offset in range(3))


def _iupac_alt_bases(ref_base: str, query_base: str) -> list[tuple[str, float]]:
    """Expand an IUPAC query base into ALT alleles with fractional frequencies."""
    iupac_options = {
        'A': {'A'},
        'C': {'C'},
        'G': {'G'},
        'T': {'T'},
        'R': {'A', 'G'},
        'Y': {'C', 'T'},
        'S': {'G', 'C'},
        'W': {'A', 'T'},
        'K': {'G', 'T'},
        'M': {'A', 'C'},
        'B': {'C', 'G', 'T'},
        'D': {'A', 'G', 'T'},
        'H': {'A', 'C', 'T'},
        'V': {'A', 'C', 'G'},
        'N': {'A', 'C', 'G', 'T'},
    }
    ref_base_upper = ref_base.upper()
    options = iupac_options.get(query_base.upper(), {query_base.upper()})
    non_ref_alts = sorted(base for base in options if base != ref_base_upper)
    if not non_ref_alts:
        return []
    af_each = 1.0 / len(non_ref_alts)
    return [(alt, af_each) for alt in non_ref_alts]


def _gapped_strings_from_cigar(
    cds: str,
    region: str,
    cigar: str,
    cds_start: int,
) -> tuple[str, str]:
    """
    Reconstruct gapped alignment strings from a CIGAR string without re-aligning.

    Leading and trailing unaligned CDS bases (when coverage < 100%) are represented
    as query gaps so nucleotide-level difference walking covers the full CDS.

    CIGAR operations are CDS-relative: M=match/mismatch, I=insertion in query, D=deletion in query.

    :param cds: full internal CDS nucleotide sequence
    :param region: extracted query region (query[query_start:query_end], coding strand)
    :param cigar: CIGAR string from sequence matching
    :param cds_start: 0-based CDS position where the alignment begins
    :return: (aligned_ref, aligned_query) of equal length
    """
    aligned_ref: list[str] = []
    aligned_query: list[str] = []

    # Unaligned CDS bases before alignment start
    if cds_start > 0:
        aligned_ref.append(cds[:cds_start])
        aligned_query.append('-' * cds_start)

    ref_pos = cds_start
    query_pos = 0
    for n_str, op in re.findall(r'(\d+)([MID])', cigar):
        n = int(n_str)
        if op == 'M':
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append(region[query_pos:query_pos + n])
            ref_pos += n
            query_pos += n
        elif op == 'I':  # insertion in query
            aligned_ref.append('-' * n)
            aligned_query.append(region[query_pos:query_pos + n])
            query_pos += n
        elif op == 'D':  # deletion in query
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append('-' * n)
            ref_pos += n

    # Trailing unaligned CDS bases
    if ref_pos < len(cds):
        aligned_ref.append(cds[ref_pos:])
        aligned_query.append('-' * (len(cds) - ref_pos))

    return ''.join(aligned_ref), ''.join(aligned_query)


def _codon_outside_coverage(
    *,
    codon_idx: int,
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> bool:
    """Return whether one codon falls outside the aligned/assessable CDS span."""
    if covered_cds_start is None or covered_cds_end is None:
        return False
    codon_nt_start = frame + codon_idx * 3
    codon_nt_end = codon_nt_start + 3
    return codon_nt_start < covered_cds_start or codon_nt_end > covered_cds_end


def _deletion_run_within_coverage(
    *,
    start_idx: int,
    end_idx: int,
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> bool:
    """Return whether an entire deletion run is inside the aligned/assessable CDS span."""
    if covered_cds_start is None or covered_cds_end is None:
        return True
    deletion_nt_start = frame + start_idx
    deletion_nt_end = frame + end_idx + 1
    return deletion_nt_start >= covered_cds_start and deletion_nt_end <= covered_cds_end



def _codon_genomic_pos(feature: FeatureRecord, codon_idx: int) -> int:
    """
    Return the 0-based internal genomic position of the first NT in a codon.

    :param feature: feature record
    :param codon_idx: 0-based codon index in the protein
    :return: 0-based genomic position on the internal reference
    """
    genomic_pos = feature.cds_to_genomic_position(feature.codon_start + codon_idx * 3)
    if genomic_pos is None:
        raise ValueError(f'Codon index {codon_idx} is outside CDS for feature {feature.name!r}')
    return genomic_pos


def _coding_nt_genomic_pos(feature: FeatureRecord, coding_nt_idx: int) -> int:
    """
    Return the 0-based internal genomic position of one coding nucleotide index.

    :param feature: feature record
    :param coding_nt_idx: 0-based nucleotide index in coding orientation (after codon_start)
    :return: 0-based genomic position on the internal reference
    """
    genomic_pos = feature.cds_to_genomic_position(feature.codon_start + coding_nt_idx)
    if genomic_pos is None:
        raise ValueError(
            f'Coding nucleotide index {coding_nt_idx} is outside CDS for feature {feature.name!r}'
        )
    return genomic_pos


def _make_variant_from_coding_nt(
    feature: FeatureRecord,
    coding_nt_idx: int,
    ref: str,
    alt: str,
    af: float = 1.0,
) -> VariantCall:
    """
    Build a synthetic VariantCall anchored at one coding nucleotide position.

    For minus-strand features, reverse-complements ref/alt to match genomic orientation.
    """
    ref_out = ref
    alt_out = alt

    # For minus-strand features, nucleotides are in coding orientation but positions
    # will be reversed by cds_to_genomic_position. Apply reverse-complement to
    # nucleotides to maintain the invariant that ref=genomic_ref, alt=genomic_alt.
    if feature.strand == '-':
        ref_out = str(Seq(ref).reverse_complement())
        alt_out = str(Seq(alt).reverse_complement())

    return VariantCall(
        chrom=feature.name,
        pos=_coding_nt_genomic_pos(feature, coding_nt_idx),
        ref=ref_out,
        alt=alt_out,
        allele_freq=af,
        depth=0,
        filter_status='PASS',
    )


def _make_fasta_insertion_from_alignment(
    feature: FeatureRecord,
    coding: list[tuple[str, str, str]],
    idx: int,
    inserted_bases: str,
) -> VariantCall | None:
    """
    Build an insertion variant from position where ref is non-gap.

    The anchor is the base immediately before the insertion in the codon walk.
    For plus-strand, use the previous position; for minus, use current position.
    """
    if feature.strand == '+':
        anchor_idx = idx - 1
        if anchor_idx < 0:
            return None
        anchor_ref_nt = coding[anchor_idx][0]
    else:
        anchor_idx = idx
        anchor_ref_nt = coding[anchor_idx][0]

    if feature.strand == '-':
        anchor_genomic = str(Seq(anchor_ref_nt).reverse_complement())
        inserted_genomic = str(Seq(inserted_bases).reverse_complement())
        return VariantCall(
            chrom=feature.name,
            pos=_coding_nt_genomic_pos(feature, anchor_idx),
            ref=anchor_genomic,
            alt=anchor_genomic + inserted_genomic,
            allele_freq=1.0,
            depth=0,
            filter_status='PASS',
        )

    return _make_variant_from_coding_nt(
        feature,
        anchor_idx,
        anchor_ref_nt,
        anchor_ref_nt + inserted_bases,
    )


def _make_fasta_deletion_from_alignment(
    feature: FeatureRecord,
    coding: list[tuple[str, str, str]],
    start_idx: int,
    end_idx: int,
) -> VariantCall | None:
    """
    Build a deletion variant from a run of consecutive query gaps.

    Collects all deleted ref bases from start_idx to end_idx (inclusive).
    Anchors to the base immediately before (plus-strand) or after (minus-strand).
    """
    deleted_bases = ''.join(coding[i][0] for i in range(start_idx, end_idx + 1))
    if not deleted_bases:
        return None

    if feature.strand == '+':
        anchor_idx = start_idx - 1
        if anchor_idx < 0:
            return None
        anchor_ref_nt = coding[anchor_idx][0]
    else:
        anchor_idx = end_idx + 1
        if anchor_idx >= len(coding):
            return None
        anchor_ref_nt = coding[anchor_idx][0]

    if feature.strand == '-':
        anchor_genomic = str(Seq(anchor_ref_nt).reverse_complement())
        deleted_genomic = str(Seq(deleted_bases).reverse_complement())
        return VariantCall(
            chrom=feature.name,
            pos=_coding_nt_genomic_pos(feature, anchor_idx),
            ref=anchor_genomic + deleted_genomic,
            alt=anchor_genomic,
            allele_freq=1.0,
            depth=0,
            filter_status='PASS',
        )

    return _make_variant_from_coding_nt(
        feature,
        anchor_idx,
        anchor_ref_nt + deleted_bases,
        anchor_ref_nt,
    )
