"""Feature and reference persistence and loading for the project database."""

from __future__ import annotations

import logging
import re
import sqlite3

from respro.config.cli_settings import CLI_CONFIG
from respro.db.models import FeatureRecord, FeatureSegment
from respro.io.genbank import (
    ParsedGenBankFeature,
    ParsedGenBankReference,
    validate_strand,
)

logger = logging.getLogger(__name__)

_RE_NCBI_PROTEIN_ACCESSION = re.compile(
    r'^(?:[A-Z]{3}[0-9]{5}'       # e.g. AAA12345.1
    r'|[A-Z]{2}_[0-9]{6,9}'       # e.g. YP_009137097.1, NP_123456.2
    r'|[A-Z]{4}[0-9]{8,10})'      # e.g. KAFS00000001.1
    r'\.[0-9]+$'
)

_FEATURE_SELECT_SQL = (
    'SELECT g.id, g.reference_id, g.name, g.protein, g.start, g.end, g.strand, '
    'g.codon_start, g.nt_sequence, g.aa_sequence, g.feature_type, g.parent_feature_name, '
    'r.accession AS reference_accession '
    'FROM feature g '
    'LEFT JOIN reference r ON r.id = g.reference_id '
)


def load_feature_segments_by_feature_id(
    conn: sqlite3.Connection,
    feature_ids: list[int],
) -> dict[int, tuple[FeatureSegment, ...]]:
    """Load ordered CDS exon segments grouped by ``feature_id``."""
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
        fid = int(row['feature_id'])
        grouped.setdefault(fid, []).append(
            FeatureSegment(
                segment_index=int(row['segment_index']),
                start=int(row['start']),
                end=int(row['end']),
            )
        )
    return {fid: tuple(items) for fid, items in grouped.items()}


def feature_from_row(
    row: sqlite3.Row,
    segments: tuple[FeatureSegment, ...],
    *,
    id_column: str = 'id',
    strand_column: str = 'strand',
) -> FeatureRecord:
    """Hydrate one ``FeatureRecord`` from a joined ``feature`` + ``reference`` SQLite row."""
    return FeatureRecord(
        id=int(row[id_column]),
        reference_id=int(row['reference_id']),
        name=row['name'],
        protein=row['protein'] or '',
        start=int(row['start']),
        end=int(row['end']),
        strand=row[strand_column],
        codon_start=int(row['codon_start'] or 0),
        nt_sequence=row['nt_sequence'] or '',
        aa_sequence=row['aa_sequence'] or '',
        feature_type=row['feature_type'] or 'CDS',
        parent_feature_name=row['parent_feature_name'] or '',
        reference_accession=row['reference_accession'] or '',
        segments=segments,
    )


def _hydrate_features(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[FeatureRecord]:
    """Batch-load segments and build ``FeatureRecord`` instances for queried feature rows."""
    segments_by_feature = load_feature_segments_by_feature_id(
        conn, [int(row['id']) for row in rows],
    )
    return [
        feature_from_row(row, segments_by_feature.get(int(row['id']), ()))
        for row in rows
    ]


def load_features(
    conn: sqlite3.Connection,
    reference_id: int | None = None,
) -> list[FeatureRecord]:
    """Load rule-backed features (``has_rules = 1``), optionally filtered to one reference."""
    if reference_id is None:
        rows = conn.execute(
            f'{_FEATURE_SELECT_SQL} WHERE g.has_rules = 1 ORDER BY g.reference_id, g.start, g.id'
        ).fetchall()
    else:
        rows = conn.execute(
            f'{_FEATURE_SELECT_SQL} WHERE g.has_rules = 1 AND g.reference_id = ? ORDER BY g.start, g.id',
            (reference_id,),
        ).fetchall()
    return _hydrate_features(conn, rows)


def load_features_for_reference(conn: sqlite3.Connection, reference_id: int) -> list[FeatureRecord]:
    """Load rule-backed features (``has_rules = 1``) for one reference (alignment/annotation scope)."""
    features = load_features(conn, reference_id=reference_id)
    logger.debug('Loaded %d rule-backed feature(s) for reference_id=%d', len(features), reference_id)
    return features


def load_all_features_for_reference(conn: sqlite3.Connection, reference_id: int) -> list[FeatureRecord]:
    """Load every feature of one reference regardless of ``has_rules`` (reporting/overview scope)."""
    rows = conn.execute(
        f'{_FEATURE_SELECT_SQL} WHERE g.reference_id = ? ORDER BY g.start, g.id',
        (reference_id,),
    ).fetchall()
    features = _hydrate_features(conn, rows)
    logger.debug('Loaded %d total feature(s) for reference_id=%d', len(features), reference_id)
    return features


def load_genbank_records(
    conn: sqlite3.Connection,
    project_id: int,
    records: list[ParsedGenBankReference],
) -> None:
    """Insert or validate GenBank references, features, and exon segments in the project DB."""
    inserted_refs = reused_refs = 0
    inserted_features = reused_features = 0
    ncbi_url_cache: dict[str, str] = {}

    for record in records:
        reference_id, created_ref = _get_or_create_reference_id(conn, project_id, record)
        inserted_refs += int(created_ref)
        reused_refs += int(not created_ref)

        for feature in record.features:
            feature_id, created_feature = _get_or_create_feature(
                conn, reference_id, feature, ncbi_protein_url_cache=ncbi_url_cache,
            )
            _upsert_feature_segments(conn, feature_id, feature.segments)
            inserted_features += int(created_feature)
            reused_features += int(not created_feature)

    logger.info(
        'Loaded GenBank data: references +%d (reused %d), features +%d (reused %d)',
        inserted_refs, reused_refs, inserted_features, reused_features,
    )


def _is_ncbi_protein_accession(value: str) -> bool:
    """Return True for standard versioned NCBI protein accession formats."""
    token = value.strip().upper()
    return bool(token) and _RE_NCBI_PROTEIN_ACCESSION.match(token) is not None


def _resolve_ncbi_protein_url(protein_id: str, cache: dict[str, str]) -> str:
    """Return a canonical NCBI protein URL when ``protein_id`` matches a valid accession format."""
    token = protein_id.strip()
    if not token:
        return ''
    if token in cache:
        return cache[token]

    if not _is_ncbi_protein_accession(token):
        logger.debug('NCBI protein URL skipped for non-standard protein_id %r', token)
        cache[token] = ''
        return ''

    url = CLI_CONFIG.urls.ncbi_protein_page.format(protein_id=token)
    cache[token] = url
    return url


def _get_or_create_reference_id(
    conn: sqlite3.Connection,
    project_id: int,
    record: ParsedGenBankReference,
) -> tuple[int, bool]:
    """Insert a reference row or reuse an existing compatible one."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT id, accession, organism, taxonomy, length '
        'FROM reference WHERE project_id = ? AND name = ?',
        (project_id, record.name),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            'INSERT INTO reference (project_id, name, accession, organism, taxonomy, length) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (project_id, record.name, record.accession, record.organism, record.taxonomy, record.length),
        )
        return int(cur.lastrowid or 0), True

    if int(existing['length']) != record.length:
        raise ValueError(
            f'Reference {record.name!r} already exists with a different length; '
            'refusing to append incompatible data'
        )
    if record.accession and existing['accession'] and existing['accession'] != record.accession:
        raise ValueError(
            f'Reference {record.name!r} already exists with accession '
            f"{existing['accession']!r}, incoming accession is {record.accession!r}"
        )
    return int(existing['id']), False


def _get_or_create_feature(
    conn: sqlite3.Connection,
    reference_id: int,
    feature: ParsedGenBankFeature,
    *,
    ncbi_protein_url_cache: dict[str, str],
) -> tuple[int, bool]:
    """Insert a feature row or reuse an existing one, backfilling blank metadata fields."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT id, start, end, strand, codon_start, nt_sequence, aa_sequence, protein, '
        'protein_id, ncbi_protein_url, locus_tag, note, feature_type, parent_feature_name '
        'FROM feature WHERE reference_id = ? AND name = ?',
        (reference_id, feature.feature_name),
    ).fetchone()

    strand = validate_strand(feature.strand)
    ncbi_url = _resolve_ncbi_protein_url(feature.protein_id, ncbi_protein_url_cache)
    if existing is None:
        cur = conn.execute(
            'INSERT INTO feature '
            '(reference_id, name, protein, protein_id, ncbi_protein_url, locus_tag, note, '
            'start, end, strand, codon_start, nt_sequence, aa_sequence, feature_type, parent_feature_name) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                reference_id, feature.feature_name, feature.protein, feature.protein_id,
                ncbi_url, feature.locus_tag, feature.note, feature.start, feature.end,
                strand, feature.codon_start, feature.nt_sequence, feature.aa_sequence,
                feature.feature_type, feature.parent_feature_name,
            ),
        )
        return int(cur.lastrowid or 0), True

    # Core coordinates and sequence must match; an empty parent_feature_name may be filled in.
    existing_parent = (existing['parent_feature_name'] or '').strip()
    core_matches = (
        int(existing['start']) == feature.start
        and int(existing['end']) == feature.end
        and existing['strand'] == strand
        and int(existing['codon_start']) == feature.codon_start
        and (existing['nt_sequence'] or '') == feature.nt_sequence
        and (existing['aa_sequence'] or '') == feature.aa_sequence
        and (existing['feature_type'] or 'CDS') == feature.feature_type
    )
    parent_compatible = (existing_parent == feature.parent_feature_name) or (
        existing_parent == '' and bool(feature.parent_feature_name)
    )
    if not (core_matches and parent_compatible):
        raise ValueError(
            f'Feature {feature.feature_name!r} already exists for this reference with different '
            'coordinates/sequence; refusing to append incompatible data'
        )

    fillable = {
        'protein': feature.protein,
        'protein_id': feature.protein_id,
        'ncbi_protein_url': ncbi_url,
        'locus_tag': feature.locus_tag,
        'note': feature.note,
        'parent_feature_name': feature.parent_feature_name,
    }
    updates = {
        col: val for col, val in fillable.items()
        if not (existing[col] or '').strip() and val
    }
    if updates:
        set_clause = ', '.join(f'{col} = ?' for col in updates)
        conn.execute(
            f'UPDATE feature SET {set_clause} WHERE id = ?',
            (*updates.values(), int(existing['id'])),
        )

    return int(existing['id']), False


def _upsert_feature_segments(
    conn: sqlite3.Connection,
    feature_id: int,
    segments: tuple[tuple[int, int], ...],
) -> None:
    """Insert exon segments for a new feature or verify they match existing segments."""
    expected = [(idx, start, end) for idx, (start, end) in enumerate(segments)]
    existing = conn.execute(
        'SELECT segment_index, start, end FROM feature_segment WHERE feature_id = ? '
        'ORDER BY segment_index',
        (feature_id,),
    ).fetchall()

    if not existing:
        conn.executemany(
            'INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (?, ?, ?, ?)',
            [(feature_id, idx, start, end) for idx, start, end in expected],
        )
        return

    existing_triplets = [
        (int(row['segment_index']), int(row['start']), int(row['end']))
        for row in existing
    ]
    if existing_triplets != expected:
        raise ValueError(
            f'Feature id {feature_id} already exists with different CDS segment coordinates; '
            'refusing to append incompatible data'
        )


def load_reference_name(project_conn: sqlite3.Connection, reference_id: int) -> str | None:
    """Return the reference name for *reference_id*, or ``None`` if missing."""
    row = project_conn.execute(
        'SELECT name FROM reference WHERE id = ?',
        (reference_id,),
    ).fetchone()
    return str(row['name']) if row is not None else None


def load_reference_metadata(project_conn: sqlite3.Connection, reference_id: int) -> tuple[str, int]:
    """Return ``(organism, reference_length_nt)`` for *reference_id*."""
    row = project_conn.execute(
        'SELECT organism, length FROM reference WHERE id = ?',
        (reference_id,),
    ).fetchone()
    if row is None:
        return '', 0
    return row['organism'] or '', int(row['length'] or 0)
