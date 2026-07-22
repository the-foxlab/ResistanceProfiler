"""
BAM-based coverage assessment for VCF mode.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pysam

from respro.core.vcf_remap import _build_query_to_cds_map
from respro.db.models import CoverageGap, FeatureMatch

logger = logging.getLogger(__name__)


def compute_coverage_gaps_from_bam(
    bam_path: Path,
    query_name: str,
    query_sequence: str,
    matches: list[FeatureMatch],
    min_depth: int,
) -> list[CoverageGap]:
    """
    Compute non-covered codon stretches by projecting BAM depth to internal CDS coordinates.

    Thin single-CHROM wrapper over :func:`compute_coverage_gaps_from_bam_multi`. Existing
    single-reference callers and tests are unchanged.

    :param bam_path: aligned BAM against the query reference used for VCF calling
    :param query_name: query reference name/header used during sequence matching
    :param query_sequence: full query sequence
    :param matches: selected feature matches for the resolved internal reference
    :param min_depth: per-base minimum depth threshold
    :return: merged non-covered codon stretches
    """
    return compute_coverage_gaps_from_bam_multi(
        bam_path=bam_path,
        per_chrom={query_name: (query_name, query_sequence, matches)},
        min_depth=min_depth,
    )


def compute_coverage_gaps_from_bam_multi(
    bam_path: Path,
    per_chrom: dict[str, tuple[str, str, list[FeatureMatch]]],
    min_depth: int,
) -> list[CoverageGap]:
    """
    Compute non-covered codon stretches across multiple CHROMs in one BAM.

    For each entry in ``per_chrom`` (keyed by CHROM, value a
    ``(query_name, query_sequence, matches)`` tuple), the BAM depth for that contig is
    projected to internal CDS coordinates and non-covered codon stretches computed.
    Results are concatenated. A BAM contig whose name has no matching FASTA record is
    simply absent from ``per_chrom`` and therefore skipped (the caller decides which
    CHROMs to include; unmatched CHROMs are dropped at routing time).

    :param bam_path: aligned BAM against the query reference(s) used for VCF calling
    :param per_chrom: mapping of CHROM → ``(query_name, query_sequence, matches)``;
        one entry per matched FASTA record
    :param min_depth: per-base minimum depth threshold
    :return: concatenated merged non-covered codon stretches across all CHROMs
    """
    if not per_chrom:
        return []

    _ensure_bam_index(bam_path)
    all_gaps: list[CoverageGap] = []
    with pysam.AlignmentFile(str(bam_path), 'rb') as bam:
        bam_references = set(bam.references)
        for chrom, (query_name, query_sequence, matches) in per_chrom.items():
            query_len = len(query_sequence)
            if query_len == 0 or not matches:
                continue
            if chrom not in bam_references:
                logger.warning(
                    'BAM has no contig %r; skipping coverage for this CHROM', chrom,
                )
                continue
            contig = _resolve_bam_contig(bam, query_name)
            depths = _depth_array_from_bam(bam, contig, query_len)
            all_gaps.extend(
                compute_coverage_gaps_from_depth(
                    depths, matches, min_depth=min_depth, query_len=query_len, chrom=chrom,
                )
            )

    all_gaps.sort(key=lambda gap: (gap.feature_name, gap.codon_start))
    return all_gaps


def _ensure_bam_index(bam_path: Path) -> None:
    """Create a BAM index in place when it is missing."""
    bai_path = bam_path.with_suffix(f'{bam_path.suffix}.bai')
    if bai_path.exists():
        return

    try:
        pysam.index(str(bam_path))
    except Exception as exc:
        raise ValueError(
            f'Failed to create BAM index for {bam_path.name!r}. Ensure the BAM is coordinate-sorted.'
        ) from exc


def compute_coverage_gaps_from_depth(
    query_depths: list[int],
    matches: list[FeatureMatch],
    min_depth: int,
    query_len: int,
    chrom: str = '',
) -> list[CoverageGap]:
    """
    Compute non-covered codon stretches from precomputed query depth values.

    :param query_depths: depth per query position (0-based)
    :param matches: selected feature matches for the resolved internal reference
    :param min_depth: per-base minimum depth threshold
    :param query_len: total query sequence length
    :param chrom: query contig name (== ReferenceGroup.query_name) to stamp onto each gap
        so per-row reference_name can be resolved by chrom in multi-reference runs; '' for
        legacy single-reference callers
    :return: merged non-covered codon stretches
    """
    gaps: list[CoverageGap] = []
    for match in matches:
        feature = match.feature
        q2c = _build_query_to_cds_map(
            match.cigar,
            match.query_start,
            match.query_end,
            match.strand,
            query_len,
            match.cds_start,
        )
        cds_to_query = {cds_pos: query_pos for query_pos, cds_pos in q2c.items()}

        codon_count = max(0, (len(feature.nt_sequence) - feature.codon_start) // 3)
        non_covered: list[int] = []
        for codon_idx in range(codon_count):
            codon_nt_start = feature.codon_start + codon_idx * 3
            if _codon_is_non_covered(codon_nt_start, cds_to_query, query_depths, min_depth):
                non_covered.append(codon_idx)

        gaps.extend(_merge_codon_gaps(feature.name, non_covered, chrom=chrom))

    gaps.sort(key=lambda gap: (gap.feature_name, gap.codon_start))
    total_non_covered = sum(gap.codon_end - gap.codon_start + 1 for gap in gaps)
    logger.info(
        'VCF/BAM coverage: %d non-covered stretch(es), %d codon position(s) total',
        len(gaps), total_non_covered,
    )
    return gaps


def _resolve_bam_contig(bam: pysam.AlignmentFile, query_name: str) -> str:
    """Resolve BAM contig name for the query reference."""
    references = list(bam.references)
    if query_name in references:
        return query_name
    if len(references) == 1:
        only_ref = references[0]
        logger.warning(
            'BAM does not contain query reference %r; using sole BAM reference %r',
            query_name,
            only_ref,
        )
        return only_ref
    raise ValueError(
        f'BAM reference {query_name!r} not found and BAM contains multiple references: {references}'
    )


def _depth_array_from_bam(bam: pysam.AlignmentFile, contig: str, query_len: int) -> list[int]:
    """Return per-base depth for one BAM contig across the query span."""
    counts = bam.count_coverage(
        contig,
        start=0,
        stop=query_len,
        quality_threshold=0,
        read_callback='all',
    )
    return [int(counts[0][i] + counts[1][i] + counts[2][i] + counts[3][i]) for i in range(query_len)]


def _codon_is_non_covered(
    codon_nt_start: int,
    cds_to_query: dict[int, int],
    query_depths: list[int],
    min_depth: int,
) -> bool:
    """Return True when any codon nucleotide is not projectable or under depth threshold."""
    for nt_offset in range(3):
        cds_pos = codon_nt_start + nt_offset
        query_pos = cds_to_query.get(cds_pos)
        if query_pos is None:
            return True
        if query_pos < 0 or query_pos >= len(query_depths):
            return True
        if query_depths[query_pos] < min_depth:
            return True
    return False


def _merge_codon_gaps(feature_name: str, codon_indices: list[int], *, chrom: str = '') -> list[CoverageGap]:
    """Merge non-covered codon indices into contiguous stretches."""
    if not codon_indices:
        return []

    sorted_positions = sorted(set(codon_indices))
    gaps: list[CoverageGap] = []
    start = sorted_positions[0]
    end = start
    for pos in sorted_positions[1:]:
        if pos == end + 1:
            end = pos
            continue
        gaps.append(CoverageGap(feature_name=feature_name, codon_start=start, codon_end=end, chrom=chrom))
        start = pos
        end = pos
    gaps.append(CoverageGap(feature_name=feature_name, codon_start=start, codon_end=end, chrom=chrom))
    return gaps
