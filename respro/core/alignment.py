"""Sequence alignment — map internal CDS features onto a user query sequence with mappy."""

from __future__ import annotations

import logging
import re

import mappy

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.coordinates import (
    aligned_cds_length,
    classify_introns,
    exon_junction_cds_offsets,
    parse_cigar,
)
from respro.db.models import FeatureMatch, FeatureRecord

logger = logging.getLogger(__name__)


def match_query_to_features(
    query_sequence: str,
    features: list[FeatureRecord],
    *,
    threads: int = 1,
    cfg: CliConfig = CLI_CONFIG,
) -> list[FeatureMatch]:
    """Align internal CDS features against ``query_sequence`` and return hits sorted by identity."""
    matches = _match_with_mappy(query_sequence, features, threads, cfg=cfg)
    for m in matches:
        ref = m.feature.reference_accession or str(m.feature.reference_id)
        logger.info(
            '%s — Feature %r matched: identity=%.1f%%, cds_coverage=%.1f%%, '
            'query_coverage=%.1f%%, strand=%s',
            ref, m.feature.name, m.identity * 100,
            m.cds_coverage * 100, m.query_coverage * 100, m.strand,
        )
    matches.sort(key=lambda m: -m.identity)
    return matches


def _match_with_mappy(
    query_sequence: str,
    features: list[FeatureRecord],
    threads: int,
    cfg: CliConfig = CLI_CONFIG,
) -> list[FeatureMatch]:
    """Index ``query_sequence`` with mappy and map each feature CDS onto it."""
    query_upper = query_sequence.upper()
    align_cfg = cfg.alignment
    aligner = mappy.Aligner(
        seq=query_upper,
        preset=align_cfg.preset,
        k=align_cfg.k,
        w=align_cfg.w,
        best_n=align_cfg.best_n,
        n_threads=max(1, threads),
        scoring=(
            align_cfg.match_score,
            align_cfg.mismatch_penalty,
            align_cfg.gap_open_penalty,
            align_cfg.gap_extension_penalty_1,
            align_cfg.gap_open_penalty_2,
            align_cfg.gap_extension_penalty_2,
        ),
    )
    if not aligner:
        raise RuntimeError('mappy: failed to build index for query sequence')

    matches: list[FeatureMatch] = []
    for feature in features:
        if not feature.nt_sequence:
            continue

        cds = feature.nt_sequence.upper()
        primary = [h for h in aligner.map(cds) if h.is_primary]
        if not primary:
            logger.debug(
                '%s — Feature %r: no mappy hit',
                feature.reference_accession or str(feature.reference_id),
                feature.name,
            )
            continue

        hit = primary[0]
        strand = '+' if hit.strand == 1 else '-'
        cigar = _normalize_mappy_cigar(hit.cigar_str, strand)
        cigar, intron_intervals = classify_introns(
            cigar,
            exon_junction_cds_offsets(feature),
            align_cfg.intron_junction_tolerance,
        )
        identity, cds_coverage = _recompute_exon_metrics(
            hit, cds, feature, bool(intron_intervals), cigar,
        )

        matches.append(FeatureMatch(
            feature=feature,
            identity=identity,
            cds_coverage=cds_coverage,
            query_coverage=(hit.r_en - hit.r_st) / len(query_upper),
            query_start=hit.r_st,
            query_end=hit.r_en,
            strand=strand,
            cigar=cigar,
            cds_start=hit.q_st,
            intron_intervals=tuple(intron_intervals),
        ))

    return matches


def _recompute_exon_metrics(
    hit: mappy.Alignment,
    cds: str,
    feature: FeatureRecord,
    has_introns: bool,
    exon_only_cigar: str,
) -> tuple[float, float]:
    """Compute identity and CDS coverage over aligned exons only (excluding introns/soft-clips)."""
    aligned_cds = aligned_cds_length(exon_only_cigar)
    if has_introns:
        identity = hit.mlen / aligned_cds if aligned_cds > 0 else 0.0
    else:
        identity = hit.mlen / hit.blen if hit.blen else 0.0

    exon_span = feature.length_nt or len(cds)
    cds_coverage = aligned_cds / exon_span if exon_span else 0.0
    return identity, min(cds_coverage, 1.0)


def _swap_cigar_indels(cigar: str) -> str:
    """Swap I and D ops to convert from mappy (CDS=query) to pipeline (CDS=reference) convention."""
    return re.sub(
        r'(\d+)([ID])',
        lambda m: m.group(1) + ('D' if m.group(2) == 'I' else 'I'),
        cigar,
    )


def _reverse_cigar_operations(cigar: str) -> str:
    """Reverse CIGAR operation order so reverse-strand alignments walk in coding 5'->3' order."""
    return ''.join(f'{length}{op}' for length, op in reversed(parse_cigar(cigar)))


def _normalize_mappy_cigar(cigar: str, strand: str) -> str:
    """Convert a mappy CIGAR string into pipeline CDS-reference coding-orientation order."""
    swapped = _swap_cigar_indels(cigar)
    return _reverse_cigar_operations(swapped) if strand == '-' else swapped
