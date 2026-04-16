"""
Read-only query helpers for resistance rules — reusable without any CLI dependency.
"""

from __future__ import annotations

import sqlite3


def list_rules_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> list[dict]:
    """
    Return resistance rules as plain dicts suitable for tabular display.

    Rows are ordered by gene name, position, then drug name.
    When ``ref_id`` is given, only rules for genes belonging to that reference are returned.

    :param conn: open project DB connection
    :param ref_id: optional reference id to filter by
    :return: list of rule dicts with keys: reference_name, gene, position, reference, mutation,
             drug, phenotype, clinical_phenotype, ic50, fold_ic50, source, comment
    """
    if ref_id is not None:
        rows = conn.execute(
            'SELECT r.name AS reference_name, g.name AS gene, rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, rr.source, rr.comment '
            'FROM resistance_rule rr '
            'JOIN gene g ON g.id = rr.gene_id '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'WHERE g.reference_id = ? '
            'ORDER BY r.name, g.name, rr.position, d.name',
            (ref_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT r.name AS reference_name, g.name AS gene, rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, rr.source, rr.comment '
            'FROM resistance_rule rr '
            'JOIN gene g ON g.id = rr.gene_id '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY r.name, g.name, rr.position, d.name',
        ).fetchall()

    return [dict(row) for row in rows]


def list_references_for_display(conn: sqlite3.Connection) -> list[dict]:
    """
    Return all references in the project as plain dicts.

    :param conn: open project DB connection
    :return: list of reference dicts with keys: id, name, organism, accession
    """
    rows = conn.execute(
        'SELECT id, name, organism, accession FROM reference ORDER BY name'
    ).fetchall()
    return [dict(row) for row in rows]
