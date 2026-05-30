"""
Reference I/O — load reference sequences from the project DB or FASTA files.
"""

from __future__ import annotations

from pathlib import Path

from Bio import SeqIO


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

