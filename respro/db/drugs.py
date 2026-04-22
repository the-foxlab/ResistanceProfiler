"""
Drug resolution — get-or-create drug records, deduplication, and PubChem enrichment.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from respro.db.models import is_internal_formula_component_drug_name
from respro.io.pubchem import lookup_drug

logger = logging.getLogger(__name__)


def _drug_badge_color(name: str) -> str:
    """
    Return a deterministic badge color for a normalized drug name.

    The mapping is stable across runs and machines so repeated imports keep
    consistent visual identity in generated reports.
    """
    digest = hashlib.sha1(name.encode('utf-8')).hexdigest()
    hue = int(digest[0:2], 16) * 360 // 255
    saturation = 58 + (int(digest[2:4], 16) % 21)  # 58..78
    lightness = 38 + (int(digest[4:6], 16) % 14)   # 38..51

    c = (1 - abs(2 * lightness / 100 - 1)) * (saturation / 100)
    x = c * (1 - abs((hue / 60) % 2 - 1))
    m = lightness / 100 - c / 2

    if hue < 60:
        r1, g1, b1 = c, x, 0
    elif hue < 120:
        r1, g1, b1 = x, c, 0
    elif hue < 180:
        r1, g1, b1 = 0, c, x
    elif hue < 240:
        r1, g1, b1 = 0, x, c
    elif hue < 300:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x

    r = round((r1 + m) * 255)
    g = round((g1 + m) * 255)
    b = round((b1 + m) * 255)
    return f'#{r:02x}{g:02x}{b:02x}'


def _get_or_create_drug_id(
    conn: sqlite3.Connection,
    project_id: int,
    drug_name: str,
    drug_cache: dict[str, int],
) -> int:
    """
    Get the drug ID for a given drug name, creating a new drug record if needed.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param drug_name: name of the drug
    :param drug_cache: cache of drug names to drug IDs
    :return: drug ID
    """
    normalized_name = drug_name.strip().lower()
    if normalized_name in drug_cache:
        return drug_cache[normalized_name]

    conn.row_factory = sqlite3.Row
    badge_color = _drug_badge_color(normalized_name)

    row = conn.execute(
        'SELECT id, name, badge_color FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized_name),
    ).fetchone()
    if row is not None:
        if row['name'] != normalized_name or not (row['badge_color'] or '').strip():
            conn.execute(
                'UPDATE drug SET name = ?, badge_color = ? WHERE id = ?',
                (normalized_name, badge_color, row['id']),
            )
        drug_cache[normalized_name] = int(row['id'])
        return int(row['id'])

    cur = conn.execute(
        'INSERT OR IGNORE INTO drug (project_id, name, badge_color) VALUES (?, ?, ?)',
        (project_id, normalized_name, badge_color),
    )
    drug_id = cur.lastrowid
    if drug_id:
        drug_cache[normalized_name] = drug_id
        return drug_id

    row = conn.execute(
        'SELECT id FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized_name),
    ).fetchone()
    drug_cache[normalized_name] = row[0]
    return row[0]


def _consolidate_drug_names_to_lowercase(conn: sqlite3.Connection, project_id: int) -> None:
    """Collapse case-only duplicate drug rows and keep lowercase canonical names."""
    conn.row_factory = sqlite3.Row
    groups = conn.execute(
        'SELECT LOWER(name) AS normalized_name, COUNT(*) AS cnt '
        'FROM drug WHERE project_id = ? GROUP BY LOWER(name) HAVING COUNT(*) > 1',
        (project_id,),
    ).fetchall()

    for group in groups:
        normalized_name = group['normalized_name']
        rows = conn.execute(
            'SELECT id FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id',
            (project_id, normalized_name),
        ).fetchall()
        keep_id = int(rows[0]['id'])
        duplicate_ids = [int(row['id']) for row in rows[1:]]

        for duplicate_id in duplicate_ids:
            conn.execute(
                'UPDATE resistance_rule SET drug_id = ? WHERE drug_id = ?',
                (keep_id, duplicate_id),
            )
            conn.execute('DELETE FROM drug WHERE id = ?', (duplicate_id,))

        conn.execute(
            'UPDATE drug SET name = ? WHERE id = ?',
            (normalized_name, keep_id),
        )

    conn.execute(
        'UPDATE drug SET name = LOWER(name) WHERE project_id = ? AND name != LOWER(name)',
        (project_id,),
    )


def _get_drugs_from_pubchem(conn: sqlite3.Connection, project_id: int) -> None:
    """
    Add missing PubChem metadata to drug records.

    Queries PubChem by drug name and writes back the CID, canonical URL, and
    a short description for each matched compound. Drugs that already have
    complete PubChem data are not queried again. Failures — including no
    network access, unrecognised drug names, or unexpected API responses — are
    logged and skipped so the database is always built successfully.

    :param conn: SQLite database connection (row_factory must be sqlite3.Row)
    :param project_id: project ID used to scope the drug lookup
    """

    conn.row_factory = sqlite3.Row
    drug_rows = conn.execute(
        'SELECT id, name, pubchem_cid, pubchem_url, description, structure_url '
        'FROM drug WHERE project_id = ?',
        (project_id,),
    ).fetchall()

    if not drug_rows:
        return

    # Query unresolved drugs and entries missing a description so we can
    # backfill title-based text for compounds where PubChem has no description.
    drugs_to_query = [
        drug for drug in drug_rows
        if (
            (not (drug['pubchem_cid'] or '').strip() or not (drug['description'] or '').strip())
            and not is_internal_formula_component_drug_name(drug['name'] or '')
        )
    ]

    already_present = len(drug_rows) - len(drugs_to_query)
    if not drugs_to_query:
        logger.info('PubChem: all %d drug(s) already have stored data', len(drug_rows))
        return

    logger.info('PubChem: querying data for %d drug(s)', len(drugs_to_query))
    if already_present:
        logger.info('PubChem: skipped %d drug(s) with stored data', already_present)

    info_added = 0

    for drug in drugs_to_query:
        drug_name = drug['name']
        record = lookup_drug(drug_name)
        if record is None:
            logger.warning(
                'PubChem: no record found for %r — stored without PubChem data',
                drug_name,
            )
            continue

        conn.execute(
            'UPDATE drug SET pubchem_cid = ?, pubchem_url = ?, description = ?, structure_url = ? WHERE id = ?',
            (str(record.cid), record.url, record.description, record.structure_url, drug['id']),
        )
        info_added += 1
        logger.info('PubChem: added data for %r (CID %s)', drug_name, record.cid)

    logger.info('PubChem: added data for %d/%d queried drug(s)', info_added, len(drugs_to_query))

