"""
Feature and reference loading — insert or reuse GenBank-derived rows in the project database.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from respro.config.cli_settings import CLI_CONFIG
from respro.io.genbank import (
    ParsedGenBankFeature,
    ParsedGenBankReference,
    validate_strand,
)

logger = logging.getLogger(__name__)

# Compiled patterns for NCBI protein accession detection.
_RE_NCBI_PROTEIN_ACCESSION = re.compile(
    r'^(?:[A-Z]{3}[0-9]{5}'       # e.g. AAA12345.1
    r'|[A-Z]{2}_[0-9]{6,9}'       # e.g. YP_009137097.1, NP_123456.2
    r'|[A-Z]{4}[0-9]{8,10})'      # e.g. KAFS00000001.1
    r'\.[0-9]+$'
)


def _load_genbank_records(
    conn: sqlite3.Connection,
    project_id: int,
    records: list[ParsedGenBankReference],
) -> None:
    """
    Load references and CDS/feature annotations from parsed GenBank records.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param records: list of ParsedGenBankReference objects
    """
    inserted_refs = 0
    reused_refs = 0
    inserted_features = 0
    reused_features = 0
    ncbi_protein_url_cache: dict[str, str] = {}

    for record in records:
        reference_id, created_ref = _get_or_create_reference_id(conn, project_id, record)
        if created_ref:
            inserted_refs += 1
        else:
            reused_refs += 1

        for feature in record.features:
            feature_id, created_feature = _get_or_create_feature(
                conn,
                reference_id,
                feature,
                ncbi_protein_url_cache=ncbi_protein_url_cache,
            )
            _upsert_feature_segments(conn, feature_id, feature.segments)
            if created_feature:
                inserted_features += 1
            else:
                reused_features += 1

    logger.info(
        'Loaded GenBank data: references +%d (reused %d), features +%d (reused %d)',
        inserted_refs,
        reused_refs,
        inserted_features,
        reused_features,
    )


def _is_ncbi_protein_accession(value: str) -> bool:
    """Return True for common NCBI protein accession formats with version suffix."""
    token = value.strip().upper()
    return bool(token) and _RE_NCBI_PROTEIN_ACCESSION.match(token) is not None


def _resolve_ncbi_protein_url(
    protein_id: str,
    cache: dict[str, str],
) -> str:
    """Return a canonical NCBI protein URL from protein_id when accession looks valid."""
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
    """Insert a reference or reuse an existing compatible one."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT id, accession, organism, taxonomy, length '
        'FROM reference WHERE project_id = ? AND name = ?',
        (project_id, record.name),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            'INSERT INTO reference '
            '(project_id, name, accession, organism, taxonomy, length) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                project_id,
                record.name,
                record.accession,
                record.organism,
                record.taxonomy,
                record.length,
            ),
        )
        return int(cur.lastrowid), True

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
    """Insert a feature row or validate that an existing one is compatible."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT start, end, strand, codon_start, nt_sequence, aa_sequence, protein, '
        'protein_id, ncbi_protein_url, locus_tag, note, feature_type, parent_feature_name '
        'FROM feature WHERE reference_id = ? AND name = ?',
        (reference_id, feature.feature_name),
    ).fetchone()

    strand = validate_strand(feature.strand)
    ncbi_protein_url = _resolve_ncbi_protein_url(feature.protein_id, ncbi_protein_url_cache)
    if existing is None:
        cur = conn.execute(
            'INSERT INTO feature '
            '(reference_id, name, protein, protein_id, ncbi_protein_url, locus_tag, note, '
            'start, end, strand, codon_start, nt_sequence, aa_sequence, feature_type, parent_feature_name) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                reference_id,
                feature.feature_name,
                feature.protein,
                feature.protein_id,
                ncbi_protein_url,
                feature.locus_tag,
                feature.note,
                feature.start,
                feature.end,
                strand,
                feature.codon_start,
                feature.nt_sequence,
                feature.aa_sequence,
                feature.feature_type,
                feature.parent_feature_name,
            ),
        )
        return int(cur.lastrowid), True

    same_feature = (
        int(existing['start']) == feature.start
        and int(existing['end']) == feature.end
        and existing['strand'] == strand
        and int(existing['codon_start']) == feature.codon_start
        and (existing['nt_sequence'] or '') == feature.nt_sequence
        and (existing['aa_sequence'] or '') == feature.aa_sequence
        and (existing['feature_type'] or 'CDS') == feature.feature_type
        and (existing['parent_feature_name'] or '') == feature.parent_feature_name
    )
    if not same_feature:
        can_fill_parent_feature_name = (
            (existing['parent_feature_name'] or '').strip() == ''
            and feature.parent_feature_name
            and int(existing['start']) == feature.start
            and int(existing['end']) == feature.end
            and existing['strand'] == strand
            and int(existing['codon_start']) == feature.codon_start
            and (existing['nt_sequence'] or '') == feature.nt_sequence
            and (existing['aa_sequence'] or '') == feature.aa_sequence
            and (existing['feature_type'] or 'CDS') == feature.feature_type
        )
        if can_fill_parent_feature_name:
            conn.execute(
                'UPDATE feature SET parent_feature_name = ? WHERE reference_id = ? AND name = ?',
                (feature.parent_feature_name, reference_id, feature.feature_name),
            )
        else:
            raise ValueError(
                f'Feature {feature.feature_name!r} already exists for this reference with different '
                'coordinates/sequence; refusing to append incompatible data'
            )

    update_needed = False
    if not (existing['protein'] or '').strip() and feature.protein:
        update_needed = True
    if not (existing['protein_id'] or '').strip() and feature.protein_id:
        update_needed = True
    if not (existing['ncbi_protein_url'] or '').strip() and ncbi_protein_url:
        update_needed = True
    if not (existing['locus_tag'] or '').strip() and feature.locus_tag:
        update_needed = True
    if not (existing['note'] or '').strip() and feature.note:
        update_needed = True
    if not (existing['parent_feature_name'] or '').strip() and feature.parent_feature_name:
        update_needed = True

    if update_needed:
        conn.execute(
            'UPDATE feature SET protein = ?, protein_id = ?, ncbi_protein_url = ?, locus_tag = ?, note = ?, '
            'parent_feature_name = ? '
            'WHERE reference_id = ? AND name = ?',
            (
                (existing['protein'] or '').strip() or feature.protein,
                (existing['protein_id'] or '').strip() or feature.protein_id,
                (existing['ncbi_protein_url'] or '').strip() or ncbi_protein_url,
                (existing['locus_tag'] or '').strip() or feature.locus_tag,
                (existing['note'] or '').strip() or feature.note,
                (existing['parent_feature_name'] or '').strip() or feature.parent_feature_name,
                reference_id,
                feature.feature_name,
            ),
        )

    row = conn.execute(
        'SELECT id FROM feature WHERE reference_id = ? AND name = ?',
        (reference_id, feature.feature_name),
    ).fetchone()
    if row is None:
        raise ValueError(f'Feature {feature.feature_name!r} could not be reloaded after lookup')
    return int(row['id']), False


def _upsert_feature_segments(
    conn: sqlite3.Connection,
    feature_id: int,
    segments: tuple[tuple[int, int], ...],
) -> None:
    """Insert or validate persisted CDS segment rows for one feature."""
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


