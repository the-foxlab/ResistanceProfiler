"""
Sequence alignment — align user-provided sequences against internal CDS annotations.

This module uses the minimap2 backend via ``mappy`` to map CDS sequences against
the query sequence and produce CIGAR-based coordinate mappings for downstream
pipeline stages.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3

import mappy

from respro.db.models import GeneMatch, GeneRecord, GeneSegment

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def match_query_to_genes(
    query_sequence: str,
    genes: list[GeneRecord],
    *,
    min_identity: float = 0.9,
    threads: int = 1,
) -> list[GeneMatch]:
    """
    Match a query nucleotide sequence against internal gene CDS sequences.

    A match is accepted when ``identity >= min_identity``. Coverage metrics
    (``cds_coverage`` and ``query_coverage``) are computed and included in results.

    :param query_sequence: user-provided nucleotide sequence
    :param genes: gene records to screen (typically only those with rules)
    :param min_identity: minimum nucleotide identity to accept
    :param threads: number of mapper threads forwarded to mappy as ``n_threads``
    :return: accepted GeneMatch list sorted by identity descending
    """
    matches = _match_with_mappy(query_sequence, genes, min_identity, threads)

    for m in matches:
        ref = m.gene.reference_accession or str(m.gene.reference_id)
        logger.info(
            '%s — Gene %r matched: identity=%.1f%%, cds_coverage=%.1f%%, '
            'query_coverage=%.1f%%, strand=%s',
            ref, m.gene.name, m.identity * 100,
            m.cds_coverage * 100, m.query_coverage * 100, m.strand,
        )

    matches.sort(key=lambda m: -m.identity)
    return matches


def load_genes_with_rules(
    conn: sqlite3.Connection,
    reference_id: int | None = None,
) -> list[GeneRecord]:
    """
    Load only genes that have at least one resistance rule.

    :param conn: project database connection
    :param reference_id: optional internal reference id filter
    :return: list of GeneRecord objects
    """
    if reference_id is None:
        rows = conn.execute(
            'SELECT DISTINCT g.id, g.reference_id, g.name, g.protein, '
            'g.start, g.end, g.strand, g.codon_start, g.nt_sequence, g.aa_sequence, '
            'r.accession AS reference_accession '
            'FROM gene g '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN resistance_rule rr ON rr.gene_id = g.id '
            'ORDER BY g.reference_id, g.start',
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT DISTINCT g.id, g.reference_id, g.name, g.protein, '
            'g.start, g.end, g.strand, g.codon_start, g.nt_sequence, g.aa_sequence, '
            'r.accession AS reference_accession '
            'FROM gene g '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN resistance_rule rr ON rr.gene_id = g.id '
            'WHERE g.reference_id = ? '
            'ORDER BY g.start',
            (reference_id,),
        ).fetchall()
    gene_ids = [int(row['id']) for row in rows]
    segments_by_gene = _load_gene_segments_by_gene_id(conn, gene_ids)
    return [
        GeneRecord(
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
            segments=segments_by_gene.get(int(r['id']), tuple()),
        )
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────
# Alignment backend
# ──────────────────────────────────────────────────────────────────────

def _match_with_mappy(
    query_sequence: str,
    genes: list[GeneRecord],
    min_identity: float,
    threads: int,
) -> list[GeneMatch]:
    """
    Run mappy (minimap2) gene matching.

    Indexes the query once using an adaptive minimap2 preset, then maps each
    CDS against the index. The mappy CIGAR (gene=query, genome=reference) is
    converted to the pipeline convention (genome=query, CDS=reference) by
    swapping I and D operations.  Coordinate fields ``query_start``/``query_end``
    and ``cds_start`` are mapped from mappy's ``r_st``/``r_en`` and ``q_st``
    directly, compatible with ``cigar_to_coordinate_map`` and
    ``_build_query_to_cds_map`` for both strand orientations.
    """
    query_upper = query_sequence.upper()
    preset = 'sr' if len(query_upper) < 5000 else 'map-ont'
    aligner_kwargs: dict[str, int | str] = {
        'seq': query_upper,
        'preset': preset,
        'n_threads': max(1, threads),
    }
    if preset == 'sr':
        # Short queries need denser minimizers to retain local matches.
        if len(query_upper) < 100:
            aligner_kwargs['k'] = 7
            aligner_kwargs['w'] = 2
        else:
            aligner_kwargs['k'] = 11
            aligner_kwargs['w'] = 5
    aligner = mappy.Aligner(**aligner_kwargs)
    if not aligner:
        raise RuntimeError('mappy: failed to build index for query sequence')

    matches: list[GeneMatch] = []
    for gene in genes:
        if not gene.nt_sequence:
            continue

        cds = gene.nt_sequence.upper()
        hits = list(aligner.map(cds))
        primary = [h for h in hits if h.is_primary]

        if not primary:
            logger.debug(
                '%s — Gene %r: no mappy hit',
                gene.reference_accession or str(gene.reference_id), gene.name,
            )
            continue

        h = primary[0]
        identity = h.mlen / h.blen if h.blen else 0.0
        cds_coverage = (h.q_en - h.q_st) / len(cds)
        query_coverage = (h.r_en - h.r_st) / len(query_upper)
        if identity < min_identity:
            logger.debug(
                '%s — Gene %r: identity %.2f below threshold',
                gene.reference_accession or str(gene.reference_id),
                gene.name,
                identity,
            )
            continue

        strand = '+' if h.strand == 1 else '-'
        cigar = _normalize_mappy_cigar(h.cigar_str, strand)

        matches.append(GeneMatch(
            gene=gene,
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

    Converts from mappy convention (gene=query, genome=reference) to the
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

    mappy reports CIGAR for gene=query, genome=reference. The pipeline uses
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
    return [(int(m.group(1)), m.group(2)) for m in re.compile(r'(\d+)([MIDNSHP=X])').finditer(cigar)]


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


def sequence_checksum(sequence: str) -> str:
    """
    Compute a SHA-256 hex digest for a nucleotide sequence.

    :param sequence: nucleotide string
    :return: hex digest
    """
    return hashlib.sha256(sequence.upper().encode()).hexdigest()


# ──────────────────────────────────────────────────────────────────────
# DB caching
# ──────────────────────────────────────────────────────────────────────

def load_cached_mappings(
    conn: sqlite3.Connection,
    checksum: str,
) -> list[GeneMatch] | None:
    """
    Load previously stored gene mappings for a query reference checksum.

    :param conn: project database connection
    :param checksum: SHA-256 of the query sequence
    :return: list of GeneMatch objects, or None if no cache entry exists
    """
    qref = conn.execute(
        'SELECT id, name, sequence FROM query_reference WHERE checksum = ?',
        (checksum,),
    ).fetchone()
    if qref is None:
        return None

    rows = conn.execute(
        'SELECT qgm.gene_id, qgm.identity, qgm.cds_coverage, qgm.query_coverage, '
        'qgm.query_start, qgm.query_end, qgm.strand, qgm.cigar, '
        'g.reference_id, g.name, g.protein, g.start, g.end, g.strand AS gene_strand, '
        'g.codon_start, g.nt_sequence, g.aa_sequence, '
        'r.accession AS reference_accession '
        'FROM query_gene_mapping qgm '
        'JOIN gene g ON g.id = qgm.gene_id '
        'JOIN reference r ON r.id = g.reference_id '
        'WHERE qgm.query_ref_id = ?',
        (qref['id'],),
    ).fetchall()

    gene_ids = [int(row['gene_id']) for row in rows]
    segments_by_gene = _load_gene_segments_by_gene_id(conn, gene_ids)

    matches: list[GeneMatch] = []
    for r in rows:
        gene = GeneRecord(
            id=r['gene_id'],
            reference_id=r['reference_id'],
            name=r['name'],
            protein=r['protein'] or '',
            start=r['start'],
            end=r['end'],
            strand=r['gene_strand'],
            codon_start=r['codon_start'],
            nt_sequence=r['nt_sequence'] or '',
            aa_sequence=r['aa_sequence'] or '',
            reference_accession=r['reference_accession'] or '',
            segments=segments_by_gene.get(int(r['gene_id']), tuple()),
        )
        matches.append(GeneMatch(
            gene=gene,
            identity=r['identity'],
            cds_coverage=r['cds_coverage'],
            query_coverage=r['query_coverage'],
            query_start=r['query_start'],
            query_end=r['query_end'],
            strand=r['strand'],
            cigar=r['cigar'],
        ))

    logger.info('Loaded %d cached mapping(s) for checksum %s…', len(matches), checksum[:12])
    return matches


def _load_gene_segments_by_gene_id(
    conn: sqlite3.Connection,
    gene_ids: list[int],
) -> dict[int, tuple[GeneSegment, ...]]:
    """Return gene_id -> ordered tuple of GeneSegment objects."""
    if not gene_ids:
        return {}

    placeholders = ','.join(['?'] * len(gene_ids))
    rows = conn.execute(
        f'SELECT gene_id, segment_index, start, end FROM gene_segment '
        f'WHERE gene_id IN ({placeholders}) ORDER BY gene_id, segment_index',
        gene_ids,
    ).fetchall()
    grouped: dict[int, list[GeneSegment]] = {}
    for row in rows:
        gene_id = int(row['gene_id'])
        grouped.setdefault(gene_id, []).append(
            GeneSegment(
                segment_index=int(row['segment_index']),
                start=int(row['start']),
                end=int(row['end']),
            )
        )
    return {gene_id: tuple(items) for gene_id, items in grouped.items()}


def store_mappings(
    conn: sqlite3.Connection,
    name: str,
    sequence: str,
    checksum: str,
    matches: list[GeneMatch],
) -> None:
    """
    Cache gene mappings for a query reference in the project database.

    :param conn: project database connection
    :param name: human-readable name for the query reference
    :param sequence: full query nucleotide sequence
    :param checksum: SHA-256 of the sequence
    :param matches: accepted GeneMatch results to store
    """
    conn.execute(
        'INSERT OR IGNORE INTO query_reference (name, sequence, length, checksum) '
        'VALUES (?, ?, ?, ?)',
        (name, sequence.upper(), len(sequence), checksum),
    )
    qref_id = conn.execute(
        'SELECT id FROM query_reference WHERE checksum = ?', (checksum,),
    ).fetchone()['id']

    for match in matches:
        conn.execute(
            'INSERT OR REPLACE INTO query_gene_mapping '
            '(query_ref_id, gene_id, identity, cds_coverage, query_coverage, '
            'query_start, query_end, strand, cigar) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                qref_id,
                match.gene.id,
                match.identity,
                match.cds_coverage,
                match.query_coverage,
                match.query_start,
                match.query_end,
                match.strand,
                match.cigar,
            ),
        )

    conn.commit()
    logger.info('Cached %d mapping(s) for %r (checksum %s…)', len(matches), name, checksum[:12])



