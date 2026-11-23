"""BAM-based coverage assessment for VCF mode — project per-base read depth to CDS codons."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path

import pysam

from respro.config.cli_settings import CLI_CONFIG
from respro.core.combined_snp import BamCooccurrence
from respro.core.coordinates import build_query_to_cds_map
from respro.db.models import CoverageGap, FeatureMatch

logger = logging.getLogger(__name__)


def compute_coverage_gaps_from_bam(
    bam_path: Path,
    query_name: str,
    query_sequence: str,
    matches: list[FeatureMatch],
    min_depth: int,
    bam_base_quality_threshold: int = CLI_CONFIG.codon.bam_base_quality_threshold,
) -> list[CoverageGap]:
    """Compute non-covered codon stretches for a single-CHROM BAM."""
    return compute_coverage_gaps_from_bam_multi(
        bam_path=bam_path,
        per_chrom={query_name: (query_name, query_sequence, matches)},
        min_depth=min_depth,
        bam_base_quality_threshold=bam_base_quality_threshold,
    )


def compute_coverage_gaps_from_bam_multi(
    bam_path: Path,
    per_chrom: dict[str, tuple[str, str, list[FeatureMatch]]],
    min_depth: int,
    bam_base_quality_threshold: int = CLI_CONFIG.codon.bam_base_quality_threshold,
) -> list[CoverageGap]:
    """Compute merged non-covered codon stretches across all matched CHROMs in one BAM."""
    if not per_chrom:
        return []

    _ensure_bam_index(bam_path)
    all_gaps: list[CoverageGap] = []
    allow_sole_fallback = len(per_chrom) == 1
    with pysam.AlignmentFile(str(bam_path), 'rb') as bam:
        for chrom, (query_name, query_sequence, matches) in per_chrom.items():
            query_len = len(query_sequence)
            if query_len == 0 or not matches:
                continue
            contig = _try_resolve_bam_contig(bam, query_name, allow_sole_fallback=allow_sole_fallback)
            if contig is None:
                logger.warning(
                    'BAM has no contig %r; marking %d feature(s) on this CHROM as uncovered',
                    chrom, len(matches),
                )
                depths: list[int] = []
            else:
                depths = _depth_array_from_bam(
                    bam, contig, query_len,
                    bam_base_quality_threshold=bam_base_quality_threshold,
                )
            all_gaps.extend(
                compute_coverage_gaps_from_depth(
                    depths, matches, min_depth=min_depth, query_len=query_len, chrom=chrom,
                )
            )

    all_gaps.sort(key=lambda gap: (gap.feature_name, gap.codon_start))
    return all_gaps


def _ensure_bam_index(bam_path: Path) -> None:
    """Create a ``.bai`` index in place if missing."""
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
    """Project per-base query depths through each match's CIGAR into non-covered codon runs."""
    gaps: list[CoverageGap] = []
    for match in matches:
        feature = match.feature
        q2c = build_query_to_cds_map(
            match.cigar,
            match.query_start,
            match.query_end,
            match.strand,
            query_len,
            match.cds_start,
            match.intron_intervals,
        )
        cds_to_query = {cds_pos: query_pos for query_pos, cds_pos in q2c.items()}

        codon_count = max(0, (len(feature.nt_sequence) - feature.codon_start) // 3)
        non_covered = [
            codon_idx
            for codon_idx in range(codon_count)
            if _codon_is_non_covered(
                feature.codon_start + codon_idx * 3, cds_to_query, query_depths, min_depth,
            )
        ]
        gaps.extend(CoverageGap.merge(feature.name, non_covered, chrom=chrom))

    gaps.sort(key=lambda gap: (gap.feature_name, gap.codon_start))
    total_non_covered = sum(gap.codon_end - gap.codon_start + 1 for gap in gaps)
    logger.info(
        'VCF/BAM coverage: %d non-covered stretch(es), %d codon position(s) total',
        len(gaps), total_non_covered,
    )
    return gaps


def _try_resolve_bam_contig(
    bam: pysam.AlignmentFile,
    query_name: str,
    *,
    allow_sole_fallback: bool,
) -> str | None:
    """Resolve the BAM contig matching ``query_name``, optionally falling back to a sole contig."""
    references = list(bam.references)
    if query_name in references:
        return query_name
    if allow_sole_fallback and len(references) == 1:
        only_ref = references[0]
        logger.warning(
            'BAM does not contain query reference %r; using sole BAM reference %r',
            query_name, only_ref,
        )
        return only_ref
    return None


def _depth_array_from_bam(
    bam: pysam.AlignmentFile,
    contig: str,
    query_len: int,
    bam_base_quality_threshold: int = CLI_CONFIG.codon.bam_base_quality_threshold,
) -> list[int]:
    """Return total A+C+G+T read depth at each 0-based position of ``contig``."""
    counts = bam.count_coverage(
        contig,
        start=0,
        stop=query_len,
        quality_threshold=bam_base_quality_threshold,
        read_callback='all',
    )
    covered_len = min(query_len, len(counts[0])) if counts else 0
    depths = [int(counts[0][i] + counts[1][i] + counts[2][i] + counts[3][i]) for i in range(covered_len)]
    if covered_len < query_len:
        depths.extend([0] * (query_len - covered_len))
    return depths


def _codon_is_non_covered(
    codon_nt_start: int,
    cds_to_query: dict[int, int],
    query_depths: list[int],
    min_depth: int,
) -> bool:
    """Return True if any nucleotide of the codon is unmapped or below ``min_depth``."""
    for nt_offset in range(3):
        query_pos = cds_to_query.get(codon_nt_start + nt_offset)
        if query_pos is None or query_pos < 0 or query_pos >= len(query_depths):
            return True
        if query_depths[query_pos] < min_depth:
            return True
    return False


def build_bam_cooccurrence_by_chrom(
    bam_path: Path,
    per_chrom: dict[str, tuple[str, str, list[FeatureMatch]]],
    min_mapq: int,
    min_depth: int,
) -> dict[str, BamCooccurrence]:
    """Build per-CHROM ``BamCooccurrence`` contexts; closes all handles if any CHROM fails."""
    if not per_chrom:
        return {}
    _ensure_bam_index(bam_path)
    allow_sole_fallback = len(per_chrom) == 1
    by_chrom: dict[str, BamCooccurrence] = {}
    with ExitStack() as stack:
        for chrom, (query_name, query_sequence, matches) in per_chrom.items():
            query_len = len(query_sequence)
            if query_len == 0 or not matches:
                continue

            bam = pysam.AlignmentFile(str(bam_path), 'rb')
            contig = _try_resolve_bam_contig(bam, query_name, allow_sole_fallback=allow_sole_fallback)
            if contig is None:
                bam.close()
                logger.warning('BAM has no contig %r; skipping BAM co-occurrence for this CHROM', chrom)
                continue
            stack.enter_context(bam)

            cds_to_query_by_feature: dict[str, dict[int, int]] = {}
            match_strand_by_feature: dict[str, str] = {}
            for match in matches:
                q2c = build_query_to_cds_map(
                    match.cigar,
                    match.query_start,
                    match.query_end,
                    match.strand,
                    query_len,
                    match.cds_start,
                    match.intron_intervals,
                )
                cds_to_query_by_feature[match.feature.name] = {
                    cds_pos: query_pos for query_pos, cds_pos in q2c.items()
                }
                match_strand_by_feature[match.feature.name] = match.strand

            by_chrom[chrom] = BamCooccurrence(
                bam=bam,
                contig=contig,
                cds_to_query_by_feature=cds_to_query_by_feature,
                min_mapq=min_mapq,
                min_depth=min_depth,
                match_strand_by_feature=match_strand_by_feature,
            )
        # All handles opened cleanly; transfer ownership to the caller.
        stack.pop_all()
    return by_chrom


def close_bam_cooccurrence_handles(by_chrom: dict[str, BamCooccurrence] | None) -> None:
    """Close all ``pysam.AlignmentFile`` handles held by a co-occurrence dict."""
    if not by_chrom:
        return
    for chrom, ctx in by_chrom.items():
        try:
            ctx.bam.close()
        except Exception as exc:
            logger.warning('Failed to close BAM handle for %r: %s', chrom, exc)
