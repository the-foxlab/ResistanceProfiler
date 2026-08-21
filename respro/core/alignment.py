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
from respro.db.models import FeatureMatch, FeatureRecord, FeatureSegment, IntronInterval

logger = logging.getLogger(__name__)

_RE_CIGAR = re.compile(r'(\d+)([MIDNSHP=X])')

__all__ = [
    'IntronInterval',
    'classify_introns',
    'exon_junction_cds_offsets',
    'match_query_to_features',
    'load_features',
    'parse_cigar',
    'cigar_to_coordinate_map',
]

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
        strand = '+' if h.strand == 1 else '-'
        cigar = _normalize_mappy_cigar(h.cigar_str, strand)

        # Classify intron I-ops by exon-junction position for spliced features.
        # Produces an exon-only CIGAR (intron I ops removed) plus intron
        # intervals carried on the FeatureMatch. query_start in each interval is
        # relative to the alignment's coding-orientation query region start
        # (i.e. relative to the first base the CIGAR consumes in the query);
        # downstream strand-aware consumers convert to forward-strand coords.
        junctions = exon_junction_cds_offsets(feature)
        intron_tolerance = cfg.intron_junction_tolerance
        cigar, intron_intervals = classify_introns(cigar, junctions, intron_tolerance)
        intron_lengths = [iv.length for iv in intron_intervals]

        identity, cds_coverage = _recompute_exon_metrics(
            h, cds, feature, intron_lengths, cigar,
        )
        query_coverage = (h.r_en - h.r_st) / len(query_upper)

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
            intron_intervals=tuple(intron_intervals),
        ))

    return matches


def _recompute_exon_metrics(
    h: mappy.Alignment,
    cds: str,
    feature: FeatureRecord,
    intron_lengths: list[int],
    exon_only_cigar: str,
) -> tuple[float, float]:
    """
    Recompute identity and CDS coverage over exons only for spliced features.

    When introns were classified, the intron ``D`` ops (deletions in the CDS
    query = intron spans in the genome reference) would normally be counted in
    ``h.blen`` and crash identity. However mappy reports ``h.blen`` as the
    query (CDS) block length, which already excludes ``D`` ops, so the intron
    does not inflate it. Identity is therefore computed as ``h.mlen`` over the
    aligned exon span (sum of ``M`` and ``D`` ops in the exon-only CIGAR),
    which equals the coding bases assessed. CDS coverage is the aligned exon
    length over the sum of exon lengths (the true coding span), clamped to
    [0.0, 1.0]. Using the exon-only CIGAR avoids mappy soft-clipping inflating
    coverage above 1.0.

    :param h: mappy primary alignment hit
    :param cds: feature CDS sequence (spliced, coding orientation)
    :param feature: feature record (for exon lengths)
    :param intron_lengths: lengths of classified intron I ops
    :param exon_only_cigar: exon-only CIGAR (intron I ops removed)
    :return: ``(identity, cds_coverage)`` both in [0.0, 1.0]
    """
    aligned_cds = sum(int(n) for n, op in re.findall(r'(\d+)([MD])', exon_only_cigar))
    if intron_lengths:
        # Exon-only identity: matches over the assessed coding span. mlen
        # already excludes intron D ops; aligned_cds is the exon M+D span.
        identity = h.mlen / aligned_cds if aligned_cds > 0 else 0.0
    else:
        identity = h.mlen / h.blen if h.blen else 0.0

    exon_span = sum(seg.end - seg.start for seg in feature._coding_segments) or len(cds)
    cds_coverage = aligned_cds / exon_span if exon_span else 0.0
    return identity, min(cds_coverage, 1.0)


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


def exon_junction_cds_offsets(feature: FeatureRecord) -> list[int]:
    """
    Return the 0-based CDS offsets at which each exon junction occurs.

    A junction offset is the cumulative coding length up to the end of an exon;
    it is the CDS position at which the next exon begins. For a single-segment
    feature the list is empty.

    Junctions are derived from :attr:`FeatureRecord.segments` in **genomic
    5'->3' order** (segment_index order), not from ``_coding_segments``. This
    matches the normalized CIGAR's walking order: for '+' strand the CIGAR
    walks genomic order (== coding order), and for '-' strand
    :func:`_normalize_mappy_cigar` reverses the CIGAR so it also walks genomic
    5'->3' order. Using ``_coding_segments`` (which reverses for '-' strand)
    would mismatch the CIGAR and misclassify introns.

    :param feature: feature record with optional ``segments``
    :return: ordered list of exon-junction CDS offsets (one per internal boundary)
    """
    segments = feature.segments if feature.segments else (
        FeatureSegment(segment_index=0, start=feature.start, end=feature.end),
    )
    offsets: list[int] = []
    cumulative = 0
    for segment in segments[:-1]:
        cumulative += segment.end - segment.start
        offsets.append(cumulative)
    return offsets


def classify_introns(
    cigar: str,
    junction_offsets: list[int],
    tolerance: int,
) -> tuple[str, list[IntronInterval]]:
    """
    Classify CIGAR ``I`` ops as introns by exon-junction position.

    Walks the normalized CIGAR (CDS=reference, genome=query) tracking the CDS
    position consumed by ``M`` and ``D`` ops. An ``I`` op (insertion in the
    query/genome) is classified as an intron iff **both** hold:

    1. Its CDS position coincides with a known exon-junction offset within
       ``tolerance`` nt (the primary classifier — no standalone length heuristic).
    2. Its length is strictly greater than ``tolerance``.

    The length guard is derived from ``tolerance`` rather than carried as a
    separate knob: an insertion of length ≤ tolerance can never be
    misclassified as an intron, regardless of where it lands relative to the
    junction, while real introns (typically hundreds/thousands of nt, always
    > tolerance) always satisfy the guard. Such intron ``I`` ops are removed
    from the returned exon-only CIGAR and recorded as :class:`IntronInterval`
    entries; real coding insertions (any ``I`` op not near a junction, or of
    length ≤ tolerance) are kept.

    Adjacent same-operation runs in the exon-only CIGAR are merged so downstream
    consumers receive a clean CIGAR with no spurious length-1 adjacency.

    :param cigar: normalized CIGAR string (CDS=reference, genome=query)
    :param junction_offsets: exon-junction CDS offsets (from
        :func:`exon_junction_cds_offsets`); empty for single-exon features
    :param tolerance: maximum CDS-position distance for an ``I`` op to be
        classified as an intron, and the strict lower bound on intron length
        (from ``alignment.intron_junction_tolerance``)
    :return: ``(exon_only_cigar, intron_intervals)`` where intron_intervals is
        ordered by CDS junction position
    """
    if not junction_offsets:
        return cigar, []

    junctions = sorted(junction_offsets)
    introns: list[IntronInterval] = []
    exon_ops: list[tuple[int, str]] = []

    cds_pos = 0
    query_pos = 0  # tracked for IntronInterval.query_start (relative to cigar start)
    for length, op in parse_cigar(cigar):
        if op == 'I':
            near_junction = any(
                abs(cds_pos - j) <= tolerance for j in junctions
            )
            if near_junction and length > tolerance:
                introns.append(IntronInterval(
                    cds_junction_pos=cds_pos,
                    query_start=query_pos,
                    length=length,
                ))
            else:
                exon_ops.append((length, op))
                query_pos += length
            continue

        if op == 'D':
            cds_pos += length
        elif op == 'M':
            cds_pos += length
            query_pos += length

        exon_ops.append((length, op))

    merged = _merge_adjacent_ops(exon_ops)
    exon_only_cigar = ''.join(f'{length}{op}' for length, op in merged)
    return exon_only_cigar, introns


def _merge_adjacent_ops(ops: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Merge adjacent CIGAR operations of the same type into single runs."""
    if not ops:
        return []
    merged: list[tuple[int, str]] = [ops[0]]
    for length, op in ops[1:]:
        last_len, last_op = merged[-1]
        if op == last_op:
            merged[-1] = (last_len + length, op)
        else:
            merged.append((length, op))
    return merged


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



