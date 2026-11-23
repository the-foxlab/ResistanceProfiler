"""Reference FASTA reader normalizing RNA (U->T) and trimming trailing N tails."""

from __future__ import annotations

from pathlib import Path

from Bio import SeqIO


def read_fasta(fasta_path: Path) -> dict[str, str]:
    """Read a FASTA file into ``{record_id: uppercase_dna_sequence}``."""
    seqs: dict[str, str] = {}
    with fasta_path.open(encoding='utf-8') as fh:
        for record in SeqIO.parse(fh, 'fasta'):
            if record.id in seqs:
                raise ValueError(f'Duplicate FASTA record identifier {record.id!r} in {fasta_path}')
            seqs[record.id] = str(record.seq).upper().replace('U', 'T').rstrip('N')
    return seqs
