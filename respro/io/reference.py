"""
Reference I/O — load reference sequences from the project DB or FASTA files.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from Bio import SeqIO

from respro.db.models import GeneRecord, GeneSegment

logger = logging.getLogger(__name__)


def read_fasta(fasta_path: Path) -> dict[str, str]:
    """
    Read a FASTA file and return a dict of sequences, normalized to DNA.

    Normalization steps:
    - RNA sequences (containing U) converted to DNA (U→T)
    - Trailing N/n bases removed (represent missing tail information)

    :param fasta_path: path to FASTA file
    :return: dict mapping record_id to upper-case DNA sequence string
    """
    seqs: dict[str, str] = {}
    for record in SeqIO.parse(str(fasta_path), 'fasta'):
        seq = str(record.seq).upper().replace('U', 'T').rstrip('N')
        seqs[record.id] = seq
    return seqs


def load_gene_segments_by_gene_id(
    conn: sqlite3.Connection,
    gene_ids: list[int],
) -> dict[int, tuple[GeneSegment, ...]]:
    """
    Load gene segment records grouped by gene id.

    :param conn: project database connection
    :param gene_ids: list of gene ids to load segments for
    :return: mapping from gene_id to ordered tuple of GeneSegment objects
    """
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


def load_genes_for_reference(conn: sqlite3.Connection, reference_id: int) -> list[GeneRecord]:
    """
    Load all gene records for a given reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of GeneRecord objects
    """
    rows = conn.execute(
        'SELECT id, reference_id, name, protein, start, end, strand, codon_start, nt_sequence, aa_sequence, '
        'feature_type, parent_gene_name '
        'FROM gene WHERE reference_id = ? ORDER BY start',
        (reference_id,),
    ).fetchall()

    gene_ids = [int(row['id']) for row in rows]
    segments_by_gene = load_gene_segments_by_gene_id(conn, gene_ids)

    genes = [
        GeneRecord(
            id=row['id'],
            reference_id=row['reference_id'],
            name=row['name'],
            protein=row['protein'],
            start=row['start'],
            end=row['end'],
            strand=row['strand'],
            codon_start=row['codon_start'],
            nt_sequence=row['nt_sequence'] or '',
            aa_sequence=row['aa_sequence'] or '',
            feature_type=(row['feature_type'] or 'CDS'),
            parent_gene_name=(row['parent_gene_name'] or ''),
            segments=segments_by_gene.get(int(row['id']), tuple()),
        )
        for row in rows
    ]
    logger.debug('Loaded %d gene(s) for reference_id=%d', len(genes), reference_id)
    return genes

