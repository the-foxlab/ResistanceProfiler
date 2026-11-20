"""CIGAR parsing, intron classification, and query<->CDS coordinate projection."""

from __future__ import annotations

import re
from collections.abc import Sequence

from respro.db.models import FeatureMatch, FeatureRecord, FeatureSegment, IntronInterval

_RE_CIGAR = re.compile(r'(\d+)([MIDNSHP=X])')


def parse_cigar(cigar: str) -> list[tuple[int, str]]:
    """Parse a CIGAR string into ``(length, operation)`` pairs."""
    return [(int(m.group(1)), m.group(2)) for m in _RE_CIGAR.finditer(cigar)]


def aligned_cds_length(cigar: str) -> int:
    """Return total CDS bases consumed by a pipeline CIGAR (sum of ``M`` and ``D`` ops)."""
    return sum(length for length, op in parse_cigar(cigar) if op in ('M', 'D'))


def match_rank(match: FeatureMatch) -> tuple[float, float, float, str]:
    """Canonical best-match ordering: identity desc, cds_coverage desc, query_coverage desc, name asc."""
    return (-match.identity, -match.cds_coverage, -match.query_coverage, match.feature.name)


def exon_junction_cds_offsets(feature: FeatureRecord) -> list[int]:
    """Return 0-based CDS offsets of internal exon junctions in stored ``segments`` order."""
    segments = feature.segments or (
        FeatureSegment(segment_index=0, start=feature.start, end=feature.end),
    )
    offsets: list[int] = []
    cumulative = 0
    for segment in segments[:-1]:
        cumulative += segment.end - segment.start
        offsets.append(cumulative)
    return offsets


def _merge_adjacent_ops(ops: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Collapse consecutive CIGAR operations of the same type."""
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


def classify_introns(
    cigar: str,
    junction_offsets: list[int],
    tolerance: int,
) -> tuple[str, list[IntronInterval]]:
    """Strip intron ``I`` ops near known exon junctions into ``IntronInterval`` entries.

    An ``I`` op is classified as an intron iff its CDS position lies within ``tolerance`` nt
    of a known exon junction AND its length is strictly greater than ``tolerance``.
    Returned ``IntronInterval.query_start`` is the exon-only coding-orientation offset.
    """
    if not junction_offsets:
        return cigar, []

    junctions = sorted(junction_offsets)
    introns: list[IntronInterval] = []
    exon_ops: list[tuple[int, str]] = []

    cds_pos = 0
    query_pos = 0
    for length, op in parse_cigar(cigar):
        if op == 'I':
            near_junction = any(abs(cds_pos - j) <= tolerance for j in junctions)
            if near_junction and length > tolerance:
                introns.append(IntronInterval(cds_junction_pos=cds_pos, query_start=query_pos, length=length))
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

    exon_only_cigar = ''.join(f'{length}{op}' for length, op in _merge_adjacent_ops(exon_ops))
    return exon_only_cigar, introns


def full_intron_spans(
    intron_intervals: Sequence[IntronInterval],
    offset: int = 0,
) -> list[tuple[int, int]]:
    """Convert exon-only ``IntronInterval`` entries to full unspliced-region ``[start, end)`` spans."""
    spans: list[tuple[int, int]] = []
    cumulative = 0
    for iv in sorted(intron_intervals, key=lambda item: item.query_start):
        full_start = offset + iv.query_start + cumulative
        spans.append((full_start, full_start + iv.length))
        cumulative += iv.length
    return spans


def strip_introns_from_region(
    region: str,
    intron_intervals: tuple[IntronInterval, ...],
) -> str:
    """Remove intron spans from a coding-orientation query slice so an exon-only CIGAR aligns it."""
    if not intron_intervals:
        return region
    drop = [False] * len(region)
    for full_start, full_end in full_intron_spans(intron_intervals):
        for i in range(max(0, full_start), min(full_end, len(region))):
            drop[i] = True
    return ''.join(base for base, is_intron in zip(region, drop) if not is_intron)


def cigar_to_coordinate_map(cigar: str, query_start: int) -> dict[int, int | None]:
    """Map local 0-based CDS positions to query positions (``None`` on query deletions)."""
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


def _shift_past_introns(
    cds_to_query: dict[int, int | None],
    intron_spans: list[tuple[int, int]],
) -> dict[int, int | None]:
    """Shift exon-only mapped query positions past preceding introns in the same orientation space."""
    if not intron_spans:
        return cds_to_query
    spans = sorted(intron_spans)
    full_spans = full_intron_spans([
        IntronInterval(cds_junction_pos=0, query_start=start, length=end - start)
        for start, end in spans
    ])

    corrected: dict[int, int | None] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is None:
            corrected[cds_pos] = None
            continue
        shift = sum(end - start for start, end in spans if start <= qpos)
        new_pos = qpos + shift
        if any(full_start <= new_pos < full_end for full_start, full_end in full_spans):
            corrected[cds_pos] = None
            continue
        corrected[cds_pos] = new_pos
    return corrected


def build_query_to_cds_map(
    cigar: str,
    query_start: int,
    query_end: int,
    strand: str,
    query_len: int,
    cds_start: int = 0,
    intron_intervals: tuple[IntronInterval, ...] = (),
) -> dict[int, int]:
    """Build an intron-aware ``{forward_query_pos: cds_pos}`` map from an exon-only CIGAR."""
    # For '-' strand, cigar_to_coordinate_map operates in RC coordinates, so intron shifting
    # must run in RC space before projecting positions back to forward-strand coordinates.
    orient_start = query_start if strand == '+' else (query_len - query_end)
    intron_spans = [
        (orient_start + iv.query_start, orient_start + iv.query_start + iv.length)
        for iv in intron_intervals
    ]
    cds_to_orient = cigar_to_coordinate_map(cigar, orient_start)
    if intron_spans:
        cds_to_orient = _shift_past_introns(cds_to_orient, intron_spans)

    query_to_cds: dict[int, int] = {}
    for local_cds_pos, orient_pos in cds_to_orient.items():
        if orient_pos is None:
            continue
        forward_qpos = orient_pos if strand == '+' else (query_len - 1 - orient_pos)
        query_to_cds[forward_qpos] = local_cds_pos + cds_start
    return query_to_cds


def gapped_strings_from_cigar(
    cds: str,
    region: str,
    cigar: str,
    cds_start: int,
) -> tuple[str, str]:
    """Reconstruct equal-length gapped ``(aligned_ref, aligned_query)`` strings over the full CDS."""
    aligned_ref: list[str] = []
    aligned_query: list[str] = []

    if cds_start > 0:
        aligned_ref.append(cds[:cds_start])
        aligned_query.append('-' * cds_start)

    ref_pos = cds_start
    query_pos = 0
    for n, op in parse_cigar(cigar):
        if op == 'M':
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append(region[query_pos:query_pos + n])
            ref_pos += n
            query_pos += n
        elif op == 'I':
            aligned_ref.append('-' * n)
            aligned_query.append(region[query_pos:query_pos + n])
            query_pos += n
        elif op == 'D':
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append('-' * n)
            ref_pos += n

    if ref_pos < len(cds):
        aligned_ref.append(cds[ref_pos:])
        aligned_query.append('-' * (len(cds) - ref_pos))

    return ''.join(aligned_ref), ''.join(aligned_query)


def build_ref_positions(
    aligned_ref: str,
    aligned_query: str,
) -> list[tuple[str, str, str]]:
    """Collapse gapped alignment strings into ``(ref_base, query_base, pending_insertion)`` per ref base."""
    ref_positions: list[tuple[str, str, str]] = []
    pending_ins = ''
    for ref_base, query_base in zip(aligned_ref, aligned_query):
        if ref_base == '-':
            pending_ins += query_base
        else:
            ref_positions.append((ref_base.upper(), query_base.upper(), pending_ins.upper()))
            pending_ins = ''
    return ref_positions
