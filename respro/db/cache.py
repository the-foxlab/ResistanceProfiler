"""Privacy-minimal query-reference alignment cache stored in the project database."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

from respro.db.features import feature_from_row, load_feature_segments_by_feature_id
from respro.db.models import FeatureMatch, IntronInterval

logger = logging.getLogger(__name__)


def sequence_checksum(sequence: str) -> str:
    """Compute a case-insensitive SHA-256 hex digest for a nucleotide sequence."""
    return hashlib.sha256(sequence.upper().encode()).hexdigest()


def _serialize_intron_intervals(intervals: tuple[IntronInterval, ...]) -> str:
    return json.dumps([
        {
            'cds_junction_pos': iv.cds_junction_pos,
            'query_start': iv.query_start,
            'length': iv.length,
        }
        for iv in intervals
    ])


def _deserialize_intron_intervals(blob: str | None) -> tuple[IntronInterval, ...]:
    if not blob:
        return ()
    try:
        records = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return ()
    return tuple(
        IntronInterval(
            cds_junction_pos=int(rec['cds_junction_pos']),
            query_start=int(rec['query_start']),
            length=int(rec['length']),
        )
        for rec in records
    )


def load_cached_mappings(
    conn: sqlite3.Connection,
    checksum: str,
) -> list[FeatureMatch] | None:
    """Load cached ``FeatureMatch`` rows for ``checksum``, or ``None`` on cache miss."""
    qref = conn.execute(
        'SELECT id FROM query_reference WHERE checksum = ?',
        (checksum,),
    ).fetchone()
    if qref is None:
        return None

    rows = conn.execute(
        'SELECT qgm.feature_id, qgm.identity, qgm.cds_coverage, qgm.query_coverage, '
        'qgm.cds_start, qgm.query_start, qgm.query_end, qgm.strand, qgm.cigar, '
        'qgm.intron_intervals, '
        'g.reference_id, g.name, g.protein, g.start, g.end, g.strand AS feature_strand, '
        'g.codon_start, g.nt_sequence, g.aa_sequence, g.feature_type, g.parent_feature_name, '
        'r.accession AS reference_accession '
        'FROM query_feature_mapping qgm '
        'JOIN feature g ON g.id = qgm.feature_id '
        'JOIN reference r ON r.id = g.reference_id '
        'WHERE qgm.query_ref_id = ? AND g.has_rules = 1',
        (qref['id'],),
    ).fetchall()

    segments_by_feature = load_feature_segments_by_feature_id(
        conn, [int(row['feature_id']) for row in rows],
    )
    matches = [
        FeatureMatch(
            feature=feature_from_row(
                row,
                segments_by_feature.get(int(row['feature_id']), ()),
                id_column='feature_id',
                strand_column='feature_strand',
            ),
            identity=row['identity'],
            cds_coverage=row['cds_coverage'],
            query_coverage=row['query_coverage'],
            cds_start=row['cds_start'],
            query_start=row['query_start'],
            query_end=row['query_end'],
            strand=row['strand'],
            cigar=row['cigar'],
            intron_intervals=_deserialize_intron_intervals(row['intron_intervals']),
        )
        for row in rows
    ]
    logger.info('Loaded %d cached mapping(s) for checksum %s…', len(matches), checksum[:12])
    return matches


def store_mappings(
    conn: sqlite3.Connection,
    checksum: str,
    matches: list[FeatureMatch],
) -> None:
    """Persist sequence checksum and CIGAR feature mappings (never headers or raw sequence)."""
    conn.execute('INSERT OR IGNORE INTO query_reference (checksum) VALUES (?)', (checksum,))
    qref_id = conn.execute(
        'SELECT id FROM query_reference WHERE checksum = ?', (checksum,),
    ).fetchone()['id']

    conn.executemany(
        'INSERT OR REPLACE INTO query_feature_mapping '
        '(query_ref_id, feature_id, identity, cds_coverage, query_coverage, '
        'cds_start, query_start, query_end, strand, cigar, intron_intervals) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                qref_id,
                m.feature.id,
                m.identity,
                m.cds_coverage,
                m.query_coverage,
                m.cds_start,
                m.query_start,
                m.query_end,
                m.strand,
                m.cigar,
                _serialize_intron_intervals(m.intron_intervals),
            )
            for m in matches
        ],
    )
    conn.commit()
    logger.info('Cached %d mapping(s) for checksum %s…', len(matches), checksum[:12])
