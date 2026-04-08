"""
Sequence matching — align user-provided sequences to internal CDS annotations.

Uses Biopython's C-accelerated PairwiseAligner for semi-global alignment of each
candidate CDS against the query sequence, computes identity/coverage, builds a
CIGAR string for coordinate mapping, and optionally caches results in the project
database for fast re-runs with the same reference.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from multiprocessing import Pool

from Bio.Align import PairwiseAligner
from Bio.Seq import Seq

from respro.db.models import GeneRecord

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Data containers
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GeneMatch:
    """Result of aligning a query sequence to an internal gene CDS."""

    gene: GeneRecord
    identity: float
    cds_coverage: float   # fraction of CDS bases covered by the alignment
    query_coverage: float  # fraction of query bases consumed by the alignment
    query_start: int
    query_end: int
    strand: str
    cigar: str
    cds_start: int = 0  # first aligned CDS position (0-based); used to reconstruct gapped strings


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def match_query_to_genes(
    query_sequence: str,
    genes: list[GeneRecord],
    *,
    min_identity: float = 0.80,
    min_coverage: float = 0.90,
    cores: int = 1,
) -> list[GeneMatch]:
    """
    Match a query nucleotide sequence against internal gene CDS sequences.

    Each gene's ``nt_sequence`` (coding orientation) is aligned against both
    strands of the query.  Matches that meet the identity and coverage
    thresholds are returned, sorted by identity descending.

    A match is accepted when ``identity >= min_identity`` AND at least one of:

    - ``cds_coverage >= min_coverage`` — the alignment covers enough of the CDS
      (typical for full-length query sequences such as whole-genome FASTAs)
    - ``query_coverage >= min_coverage`` — the query is almost entirely consumed
      by the alignment (correct for short partial sequences such as Sanger reads
      or amplicons that span only part of a gene)

    When ``cores > 1`` each gene is aligned in a separate worker process via
    ``multiprocessing.Pool``, which bypasses the GIL for the C-accelerated
    ``PairwiseAligner`` and scales well with a large gene panel.

    :param query_sequence: user-provided nucleotide sequence
    :param genes: gene records to screen (typically only those with rules)
    :param min_identity: minimum nucleotide identity to accept
    :param min_coverage: minimum CDS or query coverage fraction required
    :param cores: number of worker processes (1 = serial)
    :return: accepted GeneMatch list sorted by identity descending
    """
    query_upper = query_sequence.upper()
    query_rc = str(Seq(query_upper).reverse_complement())

    args = [(gene, query_upper, query_rc, min_identity, min_coverage) for gene in genes]

    if cores > 1:
        with Pool(processes=cores) as pool:
            raw: list[GeneMatch | None] = pool.map(_align_gene_worker, args)
    else:
        raw = [_align_gene_worker(a) for a in args]

    matches: list[GeneMatch] = []
    for gene, result in zip(genes, raw):
        ref = gene.reference_accession or str(gene.reference_id)
        if result is None:
            logger.debug('%s — Gene %r: no qualifying match', ref, gene.name)
        else:
            logger.info(
                '%s — Gene %r matched: identity=%.1f%%, cds_coverage=%.1f%%, '
                'query_coverage=%.1f%%, strand=%s',
                ref, result.gene.name, result.identity * 100,
                result.cds_coverage * 100, result.query_coverage * 100, result.strand,
            )
            matches.append(result)

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
        )
        for r in rows
    ]


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


# ──────────────────────────────────────────────────────────────────────
# Internal alignment helpers
# ──────────────────────────────────────────────────────────────────────

@dataclass
class _AlignResult:
    identity: float
    cds_coverage: float
    query_coverage: float
    query_start: int
    query_end: int
    strand: str
    cigar: str
    cds_start: int = 0


def _align_gene_worker(
    args: tuple[GeneRecord, str, str, float, float],
) -> GeneMatch | None:
    """
    Align one gene against both strands of the query sequence.

    Designed as a top-level picklable function for ``multiprocessing.Pool``.

    :param args: (gene, query_upper, query_rc, min_identity, min_coverage)
    :return: GeneMatch if thresholds are met, else None
    """
    gene, query_upper, query_rc, min_identity, min_coverage = args

    if not gene.nt_sequence:
        return None

    cds = gene.nt_sequence.upper()
    fwd = _align_cds_to_query(cds, query_upper, '+')
    rev = _align_cds_to_query(cds, query_rc, '-')
    best = fwd if fwd.identity >= rev.identity else rev

    # For reverse-strand hits convert coordinates back to forward query
    if best.strand == '-':
        query_len = len(query_upper)
        best = _AlignResult(
            identity=best.identity,
            cds_coverage=best.cds_coverage,
            query_coverage=best.query_coverage,
            query_start=query_len - best.query_end,
            query_end=query_len - best.query_start,
            strand='-',
            cigar=best.cigar,
            cds_start=best.cds_start,
        )

    # Accept when identity passes AND either coverage metric meets the threshold.
    # query_coverage handles short partial sequences (Sanger reads, amplicons)
    # that fully consume the query but cover only part of the CDS.
    if best.identity < min_identity:
        return None
    if best.cds_coverage < min_coverage and best.query_coverage < min_coverage:
        return None

    return GeneMatch(
        gene=gene,
        identity=best.identity,
        cds_coverage=best.cds_coverage,
        query_coverage=best.query_coverage,
        query_start=best.query_start,
        query_end=best.query_end,
        strand=best.strand,
        cigar=best.cigar,
        cds_start=best.cds_start,
    )


def _align_cds_to_query(cds: str, query: str, strand: str) -> _AlignResult:
    """
    Local-align a CDS against a query sequence.

    :param cds: CDS nucleotide sequence (coding orientation)
    :param query: query nucleotide sequence (one strand)
    :param strand: '+' or '-' label for this orientation
    :return: alignment result
    """
    aligner = PairwiseAligner()
    aligner.mode = 'local'
    aligner.match_score = 1.0
    aligner.mismatch_score = -1.0
    aligner.open_gap_score = -2.0
    aligner.extend_gap_score = -0.5

    alignments = aligner.align(query, cds)
    try:
        best = alignments[0]
    except IndexError:
        return _AlignResult(0.0, 0.0, 0.0, 0, 0, strand, '')
    cigar, identity, cds_aligned, q_start, q_end, cds_start = _alignment_to_cigar(best, query, cds)
    cds_coverage = cds_aligned / len(cds) if cds else 0.0
    query_coverage = (q_end - q_start) / len(query) if query else 0.0

    return _AlignResult(identity, cds_coverage, query_coverage, q_start, q_end, strand, cigar, cds_start)


def _alignment_to_cigar(
    alignment,
    query: str,
    cds: str,
) -> tuple[str, float, int, int, int, int]:
    """
    Convert a Biopython Alignment object to a CIGAR string.

    The CIGAR is expressed relative to the CDS:
    - ``M`` = aligned pair (match or mismatch)
    - ``I`` = insertion in query (bases in query not in CDS)
    - ``D`` = deletion in query (CDS bases with no query counterpart)

    :return: (cigar, identity, cds_bases_aligned, query_start, query_end, cds_start)
    """
    query_blocks = alignment.aligned[0]
    cds_blocks = alignment.aligned[1]

    if len(query_blocks) == 0:
        return '', 0.0, 0, 0, 0, 0

    q_start = int(query_blocks[0][0])
    q_end = int(query_blocks[-1][1])
    cds_start = int(cds_blocks[0][0])

    cigar_ops: list[str] = []
    matches = 0
    total_aligned = 0
    cds_aligned = 0

    for i, (q_block, c_block) in enumerate(zip(query_blocks, cds_blocks)):
        q_s, q_e = int(q_block[0]), int(q_block[1])
        c_s, c_e = int(c_block[0]), int(c_block[1])
        block_len = q_e - q_s

        for j in range(block_len):
            if query[q_s + j] == cds[c_s + j]:
                matches += 1
        total_aligned += block_len
        cds_aligned += (c_e - c_s)
        cigar_ops.append(f'{block_len}M')

        # Gaps between consecutive alignment blocks
        if i + 1 < len(query_blocks):
            next_q_s = int(query_blocks[i + 1][0])
            next_c_s = int(cds_blocks[i + 1][0])
            q_gap = next_q_s - q_e
            c_gap = next_c_s - c_e

            if c_gap > 0:
                cigar_ops.append(f'{c_gap}D')
                cds_aligned += c_gap
            if q_gap > 0:
                cigar_ops.append(f'{q_gap}I')

    cigar = ''.join(cigar_ops)
    identity = matches / total_aligned if total_aligned > 0 else 0.0
    return cigar, identity, cds_aligned, q_start, q_end, cds_start

