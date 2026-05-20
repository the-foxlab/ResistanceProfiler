"""
Reference I/O — load reference sequences from the project DB or FASTA files.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from Bio import SeqIO

from respro.db.models import FeatureRecord, FeatureSegment

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


def load_feature_segments_by_feature_id(
    conn: sqlite3.Connection,
    feature_ids: list[int],
) -> dict[int, tuple[FeatureSegment, ...]]:
    """
    Load feature segment records grouped by feature id.

    :param conn: project database connection
    :param feature_ids: list of feature ids to load segments for
    :return: mapping from feature_id to ordered tuple of FeatureSegment objects
    """
    if not feature_ids:
        return {}

    placeholders = ','.join(['?'] * len(feature_ids))
    rows = conn.execute(
        f'SELECT feature_id, segment_index, start, end FROM feature_segment '
        f'WHERE feature_id IN ({placeholders}) ORDER BY feature_id, segment_index',
        feature_ids,
    ).fetchall()
    grouped: dict[int, list[FeatureSegment]] = {}
    for row in rows:
        feature_id = int(row['feature_id'])
        grouped.setdefault(feature_id, []).append(
            FeatureSegment(
                segment_index=int(row['segment_index']),
                start=int(row['start']),
                end=int(row['end']),
            )
        )
    return {feature_id: tuple(items) for feature_id, items in grouped.items()}


def load_features_for_reference(conn: sqlite3.Connection, reference_id: int) -> list[FeatureRecord]:
    """
    Load all feature records for a given reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of FeatureRecord objects
    """
    rows = conn.execute(
        'SELECT id, reference_id, name, protein, start, end, strand, codon_start, nt_sequence, aa_sequence, '
        'feature_type, parent_feature_name '
        'FROM feature WHERE reference_id = ? ORDER BY start',
        (reference_id,),
    ).fetchall()

    feature_ids = [int(row['id']) for row in rows]
    segments_by_feature = load_feature_segments_by_feature_id(conn, feature_ids)

    features = [
        FeatureRecord(
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
            parent_feature_name=(row['parent_feature_name'] or ''),
            segments=segments_by_feature.get(int(row['id']), tuple()),
        )
        for row in rows
    ]
    logger.debug('Loaded %d feature(s) for reference_id=%d', len(features), reference_id)
    return features

