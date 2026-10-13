"""
Drug resolution — get-or-create drug records, deduplication, and PubChem enrichment.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.db.models import is_internal_formula_component_drug_name
from respro.io.lookup import LookupStatus
from respro.io.pubchem import lookup_drug
from respro.io.wikipedia import lookup_summary

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
    drug_cache[normalized_name] = int(row[0])
    return int(row[0])


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
    Add missing enrichment metadata to drug records.

    For each unresolved drug a fallback chain is tried: PubChem compound
    (CID) → PubChem substance (SID, typical for monoclonal antibodies) →
    Wikipedia summary. Whichever fields the chain yields are stored — a
    substance hit contributes a substance URL plus synonym text (no CID, no
    structure image), a Wikipedia hit contributes a description only.
    Drugs that already have stored data (including substance-url-only
    antibody drugs) are not queried again. Drugs where the whole chain
    *definitively* found nothing (404 at every stage) are flagged
    (``pubchem_lookup_failed``) and skipped on subsequent runs so doomed
    network calls are not repeated; transient failures (no network access,
    unexpected API responses) leave the flag cleared so the next run
    retries. All failures are logged and skipped so the database is always
    built successfully.

    :param conn: SQLite database connection (row_factory must be sqlite3.Row)
    :param project_id: project ID used to scope the drug lookup
    """

    conn.row_factory = sqlite3.Row
    drug_rows = conn.execute(
        'SELECT id, name, pubchem_cid, pubchem_url, description, structure_url, pubchem_lookup_failed '
        'FROM drug WHERE project_id = ?',
        (project_id,),
    ).fetchall()

    if not drug_rows:
        return

    # Query unresolved drugs and entries missing a description so we can
    # backfill title-based text for compounds where PubChem has no description.
    # A non-empty description means the chain already produced text (compound,
    # substance synonyms, or Wikipedia) — the drug counts as complete. Drugs
    # flagged pubchem_lookup_failed are skipped: the whole chain definitively
    # found nothing for them on a previous run.
    drugs_to_query = [
        drug for drug in drug_rows
        if (
            not int(drug['pubchem_lookup_failed'] or 0)
            and not (drug['description'] or '').strip()
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
        drug_result = lookup_drug(drug_name)
        if drug_result.status is LookupStatus.FOUND and drug_result.value is not None:
            record = drug_result.value
            cid_text = str(record.cid) if record.cid is not None else ''
            conn.execute(
                'UPDATE drug SET pubchem_cid = ?, pubchem_url = ?, description = ?, '
                'structure_url = ?, pubchem_lookup_failed = 0 WHERE id = ?',
                (cid_text, record.url, record.description, record.structure_url, drug['id']),
            )
            info_added += 1
            if record.cid is not None:
                logger.info('PubChem: added data for %r (CID %s)', drug_name, record.cid)
            else:
                logger.info('PubChem: added substance data for %r (no compound record)', drug_name)
            continue

        if drug_result.status is LookupStatus.ERROR:
            # Transient failure — the chain outcome is unknown, so leave the
            # flag cleared and let the next run retry the whole drug.
            logger.warning(
                'PubChem lookup for %r failed (transient error) — will retry on the next run',
                drug_name,
            )
            continue

        summary_result = lookup_summary(drug_name)
        if summary_result.status is LookupStatus.FOUND and summary_result.value is not None:
            conn.execute(
                'UPDATE drug SET description = ?, pubchem_lookup_failed = 0 WHERE id = ?',
                (summary_result.value, drug['id']),
            )
            info_added += 1
            logger.info('Wikipedia: added description for %r (no PubChem record)', drug_name)
            continue

        if summary_result.status is LookupStatus.ERROR:
            logger.warning(
                'Wikipedia lookup for %r failed (transient error) — will retry on the next run',
                drug_name,
            )
            continue

        conn.execute(
            'UPDATE drug SET pubchem_lookup_failed = 1 WHERE id = ?',
            (drug['id'],),
        )
        logger.warning(
            'PubChem/Wikipedia: no record found for %r — stored without enrichment data '
            '(flagged, will not be re-queried)',
            drug_name,
        )

    logger.info('PubChem: added data for %d/%d queried drug(s)', info_added, len(drugs_to_query))

