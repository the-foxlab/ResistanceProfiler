"""
Cache helpers for query-reference mappings stored in the project database.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from respro.db.features import load_feature_segments_by_feature_id
from respro.db.models import FeatureMatch, FeatureRecord

logger = logging.getLogger(__name__)


def sequence_checksum(sequence: str) -> str:
    """
    Compute a SHA-256 hex digest for a nucleotide sequence.

    :param sequence: nucleotide string
    :return: hex digest
    """
    return hashlib.sha256(sequence.upper().encode()).hexdigest()


def load_cached_mappings(
    conn: sqlite3.Connection,
    checksum: str,
) -> list[FeatureMatch] | None:
    """
    Load previously stored feature mappings for a query reference checksum.

    :param conn: project database connection
    :param checksum: SHA-256 of the query sequence
    :return: list of FeatureMatch objects, or None if no cache entry exists
    """
    qref = conn.execute(
        'SELECT id, name, sequence FROM query_reference WHERE checksum = ?',
        (checksum,),
    ).fetchone()
    if qref is None:
        return None

    rows = conn.execute(
        'SELECT qgm.feature_id, qgm.identity, qgm.cds_coverage, qgm.query_coverage, '
        'qgm.cds_start, qgm.query_start, qgm.query_end, qgm.strand, qgm.cigar, '
        'g.reference_id, g.name, g.protein, g.start, g.end, g.strand AS feature_strand, '
        'g.codon_start, g.nt_sequence, g.aa_sequence, '
        'r.accession AS reference_accession '
        'FROM query_feature_mapping qgm '
        'JOIN feature g ON g.id = qgm.feature_id '
        'JOIN reference r ON r.id = g.reference_id '
        'WHERE qgm.query_ref_id = ?',
        (qref['id'],),
    ).fetchall()

    feature_ids = [int(row['feature_id']) for row in rows]
    segments_by_feature = load_feature_segments_by_feature_id(conn, feature_ids)

    matches: list[FeatureMatch] = []
    for row in rows:
        feature = FeatureRecord(
            id=row['feature_id'],
            reference_id=row['reference_id'],
            name=row['name'],
            protein=row['protein'] or '',
            start=row['start'],
            end=row['end'],
            strand=row['feature_strand'],
            codon_start=row['codon_start'],
            nt_sequence=row['nt_sequence'] or '',
            aa_sequence=row['aa_sequence'] or '',
            reference_accession=row['reference_accession'] or '',
            segments=segments_by_feature.get(int(row['feature_id']), tuple()),
        )
        matches.append(FeatureMatch(
            feature=feature,
            identity=row['identity'],
            cds_coverage=row['cds_coverage'],
            query_coverage=row['query_coverage'],
            cds_start=row['cds_start'],
            query_start=row['query_start'],
            query_end=row['query_end'],
            strand=row['strand'],
            cigar=row['cigar'],
        ))

    logger.info('Loaded %d cached mapping(s) for checksum %s…', len(matches), checksum[:12])
    return matches


def store_mappings(
    conn: sqlite3.Connection,
    name: str,
    sequence: str,
    checksum: str,
    matches: list[FeatureMatch],
) -> None:
    """
    Cache feature mappings for a query reference in the project database.

    :param conn: project database connection
    :param name: human-readable name for the query reference
    :param sequence: full query nucleotide sequence
    :param checksum: SHA-256 of the sequence
    :param matches: accepted FeatureMatch results to store
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
            'INSERT OR REPLACE INTO query_feature_mapping '
            '(query_ref_id, feature_id, identity, cds_coverage, query_coverage, '
            'cds_start, query_start, query_end, strand, cigar) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                qref_id,
                match.feature.id,
                match.identity,
                match.cds_coverage,
                match.query_coverage,
                match.cds_start,
                match.query_start,
                match.query_end,
                match.strand,
                match.cigar,
            ),
        )

    conn.commit()
    logger.info('Cached %d mapping(s) for %r (checksum %s…)', len(matches), name, checksum[:12])
