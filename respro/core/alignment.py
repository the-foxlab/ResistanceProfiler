"""
Sequence alignment — align user-provided sequences against internal CDS annotations.

This module uses the minimap2 backend via ``mappy`` to map CDS sequences against
the query sequence and produce CIGAR-based coordinate mappings for downstream
pipeline stages.
"""

from __future__ import annotations

import logging
import re
import sqlite3

import mappy

from respro.config.cli_settings import CLI_CONFIG
from respro.db.features import load_feature_segments_by_feature_id
from respro.db.models import FeatureMatch, FeatureRecord

logger = logging.getLogger(__name__)

_RE_CIGAR = re.compile(r'(\d+)([MIDNSHP=X])')

# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def match_query_to_features(
    query_sequence: str,
    features: list[FeatureRecord],
    *,
    threads: int = 1,
) -> list[FeatureMatch]:
    """
    Match a query nucleotide sequence against internal feature sequences.

    Coverage metrics (``cds_coverage`` and ``query_coverage``) are computed and
    included in results.

    :param query_sequence: user-provided nucleotide sequence
    :param features: feature records to screen (typically only those with rules)
    :param threads: number of mapper threads forwarded to mappy as ``n_threads``
    :return: accepted FeatureMatch list sorted by identity descending
    """
    matches = _match_with_mappy(query_sequence, features, threads)

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


def load_features(
    conn: sqlite3.Connection,
    reference_id: int | None = None,
    *,
    with_rules: bool = False,
) -> list[FeatureRecord]:
    """
    Load annotated CDS features, optionally restricted to those carrying resistance rules.

    When ``with_rules`` is False (the default) all annotated CDS features are returned,
    including those without resistance rules. This is used by multi-record VCF query
    resolution so that FASTA records aligning to a reference whose features carry no
    rules are still detected (orphan case) and reported, rather than silently dropped.

    When ``with_rules`` is True, only features that have at least one resistance rule
    are returned, via a ``JOIN resistance_rule`` clause (``SELECT DISTINCT`` deduplicates
    features that have multiple rules). This is the FASTA-mode semantics where ruleless
    features are irrelevant.

    :param conn: project database connection
    :param reference_id: optional internal reference id filter
    :param with_rules: when True, load only features that have at least one resistance rule
    :return: list of FeatureRecord objects (ruled and, by default, ruleless)
    """
    rule_join = 'JOIN resistance_rule rr ON rr.feature_id = g.id ' if with_rules else ''
    if reference_id is None:
        rows = conn.execute(
            'SELECT DISTINCT g.id, g.reference_id, g.name, g.protein, '
            'g.start, g.end, g.strand, g.codon_start, g.nt_sequence, g.aa_sequence, '
            'r.accession AS reference_accession '
            'FROM feature g '
            'JOIN reference r ON r.id = g.reference_id '
            f'{rule_join}'
            'ORDER BY g.reference_id, g.start',
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT DISTINCT g.id, g.reference_id, g.name, g.protein, '
            'g.start, g.end, g.strand, g.codon_start, g.nt_sequence, g.aa_sequence, '
            'r.accession AS reference_accession '
            'FROM feature g '
            'JOIN reference r ON r.id = g.reference_id '
            f'{rule_join}'
            'WHERE g.reference_id = ? '
            'ORDER BY g.start',
            (reference_id,),
        ).fetchall()
    feature_ids = [int(row['id']) for row in rows]
    segments_by_feature = load_feature_segments_by_feature_id(conn, feature_ids)
    return [
        FeatureRecord(
            id=r['id'],
            reference_id=r['reference_id'],
            name=r['name'],
            protein=r['protein'] or '',
            start=r['start'],
            end=r['end'],
            strand=r['strand'],
            codon_start=r['codon_start'],
            nt_sequence=r['nt_sequence'] or '',
            aa_sequence=r['aa_sequence'] or '',
            reference_accession=r['reference_accession'] or '',
            segments=segments_by_feature.get(int(r['id']), tuple()),
        )
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────
# Alignment backend
# ──────────────────────────────────────────────────────────────────────

def _match_with_mappy(
    query_sequence: str,
    features: list[FeatureRecord],
    threads: int,
) -> list[FeatureMatch]:
    """
    Run mappy (minimap2) feature matching.

    Indexes the query with settings from ``CLI_CONFIG.alignment``, then maps
    each CDS against the index. The mappy CIGAR (feature=query,
    genome=reference) is converted to the pipeline convention (genome=query,
    CDS=reference) by swapping I and D. Coordinate fields use mappy's
    ``r_st``/``r_en`` and ``q_st`` directly, compatible with
    ``cigar_to_coordinate_map`` and ``_build_query_to_cds_map`` for both
    strand orientations.
    """
    query_upper = query_sequence.upper()
    cfg = CLI_CONFIG.alignment
    aligner_kwargs: dict[str, int | str | tuple[int, ...]] = {
        'seq': query_upper,
        'preset': cfg.preset,
        'k': cfg.k,
        'w': cfg.w,
        'best_n': cfg.best_n,
        'n_threads': max(1, threads),
    }
    # Build scoring tuple: (A, B, O1, E1, O2, E2).
    aligner_kwargs['scoring'] = (
        cfg.match_score,
        cfg.mismatch_penalty,
        cfg.gap_open_penalty,
        cfg.gap_extension_penalty_1,
        cfg.gap_open_penalty_2,
        cfg.gap_extension_penalty_2,
    )
    aligner = mappy.Aligner(**aligner_kwargs)
    if not aligner:
        raise RuntimeError('mappy: failed to build index for query sequence')

    matches: list[FeatureMatch] = []
    for feature in features:
        if not feature.nt_sequence:
            continue

        cds = feature.nt_sequence.upper()
        hits = list(aligner.map(cds))
        primary = [h for h in hits if h.is_primary]

        if not primary:
            logger.debug(
                '%s — Feature %r: no mappy hit',
                feature.reference_accession or str(feature.reference_id), feature.name,
            )
            continue

        h = primary[0]
        identity = h.mlen / h.blen if h.blen else 0.0
        cds_coverage = (h.q_en - h.q_st) / len(cds)
        query_coverage = (h.r_en - h.r_st) / len(query_upper)

        strand = '+' if h.strand == 1 else '-'
        cigar = _normalize_mappy_cigar(h.cigar_str, strand)

        matches.append(FeatureMatch(
            feature=feature,
            identity=identity,
            cds_coverage=cds_coverage,
            query_coverage=query_coverage,
            query_start=h.r_st,
            query_end=h.r_en,
            strand=strand,
            cigar=cigar,
            cds_start=h.q_st,
        ))

    return matches


def _swap_cigar_indels(cigar: str) -> str:
    """
    Swap I and D operations in a CIGAR string.

    Converts from mappy convention (feature=query, genome=reference) to the
    pipeline convention (CDS=reference, genome=query).

    :param cigar: CIGAR string with I/D in mappy orientation
    :return: CIGAR string with I and D swapped
    """
    return re.sub(
        r'(\d+)([ID])',
        lambda m: m.group(1) + ('D' if m.group(2) == 'I' else 'I'),
        cigar,
    )


def _reverse_cigar_operations(cigar: str) -> str:
    """
    Reverse the operation order of a CIGAR string.

    :param cigar: CIGAR string
    :return: CIGAR with reversed operation order
    """
    ops = re.findall(r'(\d+)([MIDNSHP=X])', cigar)
    return ''.join(f'{length}{op}' for length, op in reversed(ops))


def _normalize_mappy_cigar(cigar: str, strand: str) -> str:
    """
    Convert a mappy CIGAR to pipeline CDS-vs-query convention.

    mappy reports CIGAR for feature=query, genome=reference. The pipeline uses
    CDS=reference, genome=query, so I/D are swapped. For reverse-strand hits,
    the query region is reverse-complemented before FASTA codon-walking and
    therefore requires reversed CIGAR operation order to keep indel positions
    in coding order.

    :param cigar: mappy CIGAR string
    :param strand: '+' or '-'
    :return: normalized CIGAR string for downstream pipeline use
    """
    swapped = _swap_cigar_indels(cigar)
    if strand == '-':
        return _reverse_cigar_operations(swapped)
    return swapped


# ──────────────────────────────────────────────────────────────────────
# CIGAR helpers
# ──────────────────────────────────────────────────────────────────────

def parse_cigar(cigar: str) -> list[tuple[int, str]]:
    """
    Parse a CIGAR string into ``(length, operation)`` tuples.

    :param cigar: CIGAR string, e.g. ``'10M2I5M1D8M'``
    :return: list of (length, operation) pairs
    """
    return [(int(m.group(1)), m.group(2)) for m in _RE_CIGAR.finditer(cigar)]


def cigar_to_coordinate_map(cigar: str, query_start: int) -> dict[int, int | None]:
    """
    Convert a CIGAR string to a CDS-position → query-position map.

    Positions are 0-based nucleotide offsets.  A ``None`` value means the CDS
    position has no corresponding query position (deletion in the query).

    :param cigar: CIGAR string from alignment
    :param query_start: 0-based start position in query
    :return: mapping from CDS nt position to query nt position
    """
    coord_map: dict[int, int | None] = {}
    cds_pos = 0
    query_pos = query_start

    for length, op in parse_cigar(cigar):
        if op == 'M':
            for _ in range(length):
                coord_map[cds_pos] = query_pos
                cds_pos += 1
                query_pos += 1
        elif op == 'I':
            query_pos += length
        elif op == 'D':
            for _ in range(length):
                coord_map[cds_pos] = None
                cds_pos += 1

    return coord_map



