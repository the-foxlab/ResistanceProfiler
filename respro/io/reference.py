"""Reference FASTA reader normalizing RNA (U->T) and trimming trailing N tails."""

from __future__ import annotations

from pathlib import Path

from Bio import SeqIO


def read_fasta(fasta_path: Path) -> dict[str, str]:
    """Read a FASTA file into ``{record_id: uppercase_dna_sequence}``."""
    return {
        record.id: str(record.seq).upper().replace('U', 'T').rstrip('N')
        for record in SeqIO.parse(str(fasta_path), 'fasta')
    }
