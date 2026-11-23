"""Drug resolution — get-or-create rows, lowercase consolidation, and PubChem/Wikipedia enrichment."""

from __future__ import annotations

import logging
import sqlite3

from respro.db.models import is_internal_formula_component_drug_name
from respro.io.http import LookupStatus
from respro.io.pubchem import lookup_drug
from respro.io.wikipedia import lookup_summary

logger = logging.getLogger(__name__)


def get_or_create_drug_id(
    conn: sqlite3.Connection,
    project_id: int,
    drug_name: str,
    drug_cache: dict[str, int],
) -> int:
    """Return the drug ID for *drug_name* (lowercased), creating a row if absent."""
    normalized = drug_name.strip().lower()
    if normalized in drug_cache:
        return drug_cache[normalized]

    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT id, name FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized),
    ).fetchone()
    if row is not None:
        if row['name'] != normalized:
            conn.execute('UPDATE drug SET name = ? WHERE id = ?', (normalized, row['id']))
        drug_id = int(row['id'])
        drug_cache[normalized] = drug_id
        return drug_id

    cur = conn.execute(
        'INSERT OR IGNORE INTO drug (project_id, name) VALUES (?, ?)',
        (project_id, normalized),
    )
    if cur.lastrowid:
        drug_id = int(cur.lastrowid)
        drug_cache[normalized] = drug_id
        return drug_id

    row = conn.execute(
        'SELECT id FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized),
    ).fetchone()
    drug_id = int(row[0])
    drug_cache[normalized] = drug_id
    return drug_id


def consolidate_drug_names_to_lowercase(conn: sqlite3.Connection, project_id: int) -> None:
    """Merge case-only duplicate drug rows and normalize all names to lowercase."""
    conn.row_factory = sqlite3.Row
    groups = conn.execute(
        'SELECT LOWER(name) AS normalized_name FROM drug WHERE project_id = ? '
        'GROUP BY LOWER(name) HAVING COUNT(*) > 1',
        (project_id,),
    ).fetchall()

    for group in groups:
        normalized = group['normalized_name']
        rows = conn.execute(
            'SELECT id, alias, pubchem_cid, pubchem_url, description, structure_url '
            'FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id',
            (project_id, normalized),
        ).fetchall()
        keep_row = dict(rows[0])
        keep_id = int(keep_row['id'])
        for row in rows[1:]:
            dup_id = int(row['id'])
            for col in ('alias', 'pubchem_cid', 'pubchem_url', 'description', 'structure_url'):
                if not (keep_row[col] or '').strip() and (row[col] or '').strip():
                    conn.execute(f'UPDATE drug SET {col} = ? WHERE id = ?', (row[col], keep_id))
                    keep_row[col] = row[col]
            conn.execute('UPDATE resistance_rule SET drug_id = ? WHERE drug_id = ?', (keep_id, dup_id))
            dup_formulas = conn.execute(
                'SELECT id, normalized_expression FROM resistance_formula_rule WHERE drug_id = ?',
                (dup_id,),
            ).fetchall()
            for frow in dup_formulas:
                existing_f = conn.execute(
                    'SELECT id FROM resistance_formula_rule WHERE drug_id = ? AND normalized_expression = ?',
                    (keep_id, frow['normalized_expression']),
                ).fetchone()
                if existing_f is not None:
                    dup_fid = int(frow['id'])
                    keep_fid = int(existing_f['id'])
                    conn.execute(
                        'INSERT OR IGNORE INTO resistance_formula_rule_publication '
                        '(formula_rule_id, publication_id) '
                        'SELECT ?, publication_id FROM resistance_formula_rule_publication '
                        'WHERE formula_rule_id = ?',
                        (keep_fid, dup_fid),
                    )
                    conn.execute(
                        'DELETE FROM resistance_formula_rule_publication WHERE formula_rule_id = ?',
                        (dup_fid,),
                    )
                    conn.execute(
                        'INSERT OR IGNORE INTO resistance_formula_rule_member '
                        '(formula_rule_id, rule_id) '
                        'SELECT ?, rule_id FROM resistance_formula_rule_member '
                        'WHERE formula_rule_id = ?',
                        (keep_fid, dup_fid),
                    )
                    conn.execute(
                        'DELETE FROM resistance_formula_rule_member WHERE formula_rule_id = ?',
                        (dup_fid,),
                    )
                    conn.execute('DELETE FROM resistance_formula_rule WHERE id = ?', (dup_fid,))
                else:
                    conn.execute(
                        'UPDATE resistance_formula_rule SET drug_id = ? WHERE id = ?',
                        (keep_id, int(frow['id'])),
                    )
            conn.execute('DELETE FROM drug WHERE id = ?', (dup_id,))
        conn.execute('UPDATE drug SET name = ? WHERE id = ?', (normalized, keep_id))

    conn.execute(
        'UPDATE drug SET name = LOWER(name) WHERE project_id = ? AND name != LOWER(name)',
        (project_id,),
    )


def enrich_drugs_from_pubchem(conn: sqlite3.Connection, project_id: int) -> None:
    """Backfill drug metadata via PubChem compound/substance -> Wikipedia fallback chain."""
    conn.row_factory = sqlite3.Row
    drug_rows = conn.execute(
        'SELECT id, name, pubchem_cid, pubchem_url, description, structure_url, pubchem_lookup_failed '
        'FROM drug WHERE project_id = ?',
        (project_id,),
    ).fetchall()
    if not drug_rows:
        return

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

        # Flag definitive 404 misses so future runs skip doomed lookups.
        conn.execute('UPDATE drug SET pubchem_lookup_failed = 1 WHERE id = ?', (drug['id'],))
        logger.warning(
            'PubChem/Wikipedia: no record found for %r — stored without enrichment data '
            '(flagged, will not be re-queried)',
            drug_name,
        )

    logger.info('PubChem: added data for %d/%d queried drug(s)', info_added, len(drugs_to_query))
