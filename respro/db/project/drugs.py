"""
Drug resolution — get-or-create drug records, deduplication, and PubChem enrichment.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.io.pubchem import lookup_drug

logger = logging.getLogger(__name__)


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
    row = conn.execute(
        'SELECT id, name FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized_name),
    ).fetchone()
    if row is not None:
        if row['name'] != normalized_name:
            conn.execute(
                'UPDATE drug SET name = ? WHERE id = ?',
                (normalized_name, row['id']),
            )
        drug_cache[normalized_name] = int(row['id'])
        return int(row['id'])

    cur = conn.execute(
        'INSERT OR IGNORE INTO drug (project_id, name) VALUES (?, ?)',
        (project_id, normalized_name),
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
            conn.execute(
                'UPDATE resistance_rule_set SET drug_id = ? WHERE drug_id = ?',
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

    # A non-empty pubchem_cid means the drug was already resolved; description
    # may legitimately be absent for some compounds and must not trigger a retry.
    drugs_to_query = [
        drug for drug in drug_rows if not (drug['pubchem_cid'] or '').strip()
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

