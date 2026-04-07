"""
Reference I/O — load reference sequences from the project DB or FASTA files.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from Bio import SeqIO
from Bio.Seq import Seq

from respro.db.models import GeneRecord

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


def load_genes_for_reference(conn: sqlite3.Connection, reference_id: int) -> list[GeneRecord]:
    """
    Load all gene records for a given reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of GeneRecord objects
    """
    rows = conn.execute(
        'SELECT id, reference_id, name, protein, start, end, strand, codon_start, nt_sequence, aa_sequence '
        'FROM gene WHERE reference_id = ? ORDER BY start',
        (reference_id,),
    ).fetchall()

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
        )
        for row in rows
    ]
    logger.debug('Loaded %d gene(s) for reference_id=%d', len(genes), reference_id)
    return genes

