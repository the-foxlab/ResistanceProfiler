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
    Read a FASTA file and return a dict of sequences.

    :param fasta_path: path to FASTA file
    :return: dict mapping record_id to sequence string
    """
    seqs: dict[str, str] = {}
    for record in SeqIO.parse(str(fasta_path), 'fasta'):
        seqs[record.id] = str(record.seq).upper()
    return seqs


def load_reference_sequence(
    conn: sqlite3.Connection,
    reference_name: str | None = None,
) -> str:
    """
    Return the reconstructed genomic sequence for the active reference.

    If reference_name is given, look it up by name. Otherwise, use the primary
    reference.

    :param conn: SQLite database connection
    :param reference_name: optional specific reference name to load
    :return: reconstructed genomic sequence string
    """
    if reference_name is None:
        row = conn.execute(
            'SELECT id, name, length FROM reference ORDER BY id LIMIT 1',
        ).fetchone()
        if row is None:
            raise ValueError('No reference sequences in the project database')
    else:
        row = conn.execute(
            'SELECT id, name, length FROM reference WHERE name = ?',
            (reference_name,),
        ).fetchone()
        if row is None:
            available = [r['name'] for r in conn.execute('SELECT name FROM reference').fetchall()]
            raise ValueError(
                f'Reference {reference_name!r} not found. Available: {available}'
            )

    length = int(row['length'])
    sequence_chars = ['N'] * length
    gene_rows = conn.execute(
        'SELECT start, end, strand, nt_sequence FROM gene WHERE reference_id = ?',
        (row['id'],),
    ).fetchall()

    for gene in gene_rows:
        nt_sequence = (gene['nt_sequence'] or '').upper()
        if not nt_sequence:
            continue

        genomic_slice = nt_sequence
        if gene['strand'] == '-':
            genomic_slice = str(Seq(nt_sequence).reverse_complement())

        start = max(0, int(gene['start']))
        end = min(length, int(gene['end']))
        if start >= end:
            continue

        segment_length = end - start
        sequence_chars[start:end] = list(genomic_slice[:segment_length])

    sequence = ''.join(sequence_chars)
    logger.info('Using reference: %s (id=%d, length=%d)', row['name'], row['id'], len(sequence))
    return sequence


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


def resolve_reference_from_vcf(
    conn: sqlite3.Connection,
    vcf_contigs: set[str],
    user_reference: str | None = None,
) -> tuple[int, str]:
    """
    Attempt to match VCF contig names to a project reference.

    Resolution order:
    1. User-provided reference name (exact match)
    2. Exact contig name match against reference names in the DB
    3. Fall back to the first inserted reference

    :param conn: SQLite database connection
    :param vcf_contigs: set of contig names from VCF header
    :param user_reference: optional user-specified reference name
    :return: tuple of (reference_id, reference_name)
    """
    if user_reference:
        row = conn.execute(
            'SELECT id, name FROM reference WHERE name = ?',
            (user_reference,),
        ).fetchone()
        if row:
            return row['id'], row['name']
        raise ValueError(f'User-specified reference {user_reference!r} not found in project')

    for contig in vcf_contigs:
        row = conn.execute(
            'SELECT id, name FROM reference WHERE name = ?',
            (contig,),
        ).fetchone()
        if row:
            logger.info('Auto-detected reference %r from VCF contig', row['name'])
            return row['id'], row['name']

    row = conn.execute(
        'SELECT id, name FROM reference ORDER BY id LIMIT 1',
    ).fetchone()
    if row:
        logger.warning('No contig match; falling back to first reference %r', row['name'])
        return row['id'], row['name']

    raise ValueError('Cannot resolve reference from VCF contigs')

