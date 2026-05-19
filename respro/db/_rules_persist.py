"""
Database persistence helpers for resistance rule imports — existence checks and gene lookup.
"""

from __future__ import annotations

import sqlite3


def _rule_exists(
    conn: sqlite3.Connection,
    *,
    gene_id: int,
    drug_id: int,
    reference_identifier: str,
    position: int,
    reference: str,
    mutation: str,
) -> bool:
    """Return True when a semantically identical rule is already stored."""
    row = conn.execute(
        'SELECT id FROM resistance_rule '
        'WHERE gene_id = ? AND drug_id = ? AND reference_identifier = ? '
        'AND position = ? AND reference = ? AND mutation = ? '
        'LIMIT 1',
        (gene_id, drug_id, reference_identifier, position, reference, mutation),
    ).fetchone()
    return row is not None


def _external_rule_id_exists(conn: sqlite3.Connection, external_id: str) -> bool:
    """Return True when an atomic external rule id is already stored."""
    row = conn.execute(
        'SELECT id FROM resistance_rule WHERE external_id = ? LIMIT 1',
        (external_id,),
    ).fetchone()
    return row is not None


def _load_rule_ids_by_external_id(
    conn: sqlite3.Connection,
    external_ids: set[str],
) -> dict[str, int]:
    """Return a mapping from atomic external rule ids to resistance_rule row ids."""
    if not external_ids:
        return {}

    placeholders = ','.join('?' * len(external_ids))
    rows = conn.execute(
        f'SELECT id, external_id FROM resistance_rule WHERE external_id IN ({placeholders})',
        sorted(external_ids),
    ).fetchall()
    return {row['external_id']: int(row['id']) for row in rows}


def _formula_rule_exists(
    conn: sqlite3.Connection,
    *,
    formula_id: str,
    drug_id: int,
    normalized_expression: str,
) -> tuple[bool, bool]:
    """Return whether a formula id or a canonical drug-level expression already exists."""
    id_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule WHERE formula_id = ? LIMIT 1',
        (formula_id,),
    ).fetchone() is not None
    expression_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule '
        'WHERE drug_id = ? AND normalized_expression = ? LIMIT 1',
        (drug_id, normalized_expression),
    ).fetchone() is not None
    return id_exists, expression_exists


def _build_gene_lookup(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """
    Build a gene lookup table from the project database.

    :param conn: SQLite database connection
    :return: dictionary mapping gene names to lists of gene rows
    """
    gene_lookup_rows = conn.execute(
        """
        SELECT
            g.id AS gene_id,
            g.name AS gene_name,
            g.protein AS protein,
            g.protein_id AS protein_id,
            g.locus_tag AS locus_tag,
            g.aa_sequence AS aa_sequence,
            r.name AS reference_name,
            r.accession AS reference_accession
        FROM gene g
        JOIN reference r ON r.id = g.reference_id
        WHERE NOT (
            g.feature_type = 'CDS'
            AND EXISTS (
                SELECT 1 FROM gene child
                WHERE child.reference_id = g.reference_id
                  AND child.parent_gene_name = g.name
                  AND child.feature_type = 'mat_peptide'
            )
        )
        """
    ).fetchall()

    genes_by_name: dict[str, list[dict]] = {}
    for row in gene_lookup_rows:
        entries = [
            (row['gene_name'], 0),
            (row['locus_tag'], 1),
            (row['protein_id'], 2),
            (row['protein'], 3),
        ]
        seen_keys: set[str] = set()
        for raw_key, alias_rank in entries:
            key = (raw_key or '').strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            genes_by_name.setdefault(key, []).append(
                {
                    'gene_id': row['gene_id'],
                    'gene_name': row['gene_name'],
                    'aa_sequence': row['aa_sequence'],
                    'reference_name': row['reference_name'],
                    'reference_accession': row['reference_accession'],
                    'alias_rank': alias_rank,
                }
            )

    return genes_by_name
