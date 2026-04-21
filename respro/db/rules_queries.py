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
    :return: list of rule dicts with non-empty columns only; publication is populated from DOI
             values linked through ``rule_publication``/``publication`` when available
    """
    publication_doi_expr = (
        "COALESCE(NULLIF(rr.publication, ''), ("
        "SELECT GROUP_CONCAT(doi, '; ') FROM ("
        "SELECT DISTINCT p.doi AS doi "
        "FROM rule_publication rp "
        "JOIN publication p ON p.id = rp.publication_id "
        "WHERE rp.rule_id = rr.id AND p.doi IS NOT NULL AND p.doi != '' "
        "ORDER BY p.doi"
        ")"
        ")) AS publication"
    )

    if ref_id is not None:
        rows = conn.execute(
            'SELECT r.name AS reference_name, g.name AS gene, '
            'rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, ' + publication_doi_expr + ', rr.source, rr.comment '
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
            'SELECT r.name AS reference_name, g.name AS gene, '
            'rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, ' + publication_doi_expr + ', rr.source, rr.comment '
            'FROM resistance_rule rr '
            'JOIN gene g ON g.id = rr.gene_id '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY r.name, g.name, rr.position, d.name',
        ).fetchall()

    row_dicts = [dict(row) for row in rows]
    if not row_dicts:
        return []

    def _is_empty(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ''
        return False

    column_names = list(row_dicts[0].keys())
    non_empty_columns = [
        column_name
        for column_name in column_names
        if any(not _is_empty(row[column_name]) for row in row_dicts)
    ]

    return [
        {column_name: row[column_name] for column_name in non_empty_columns}
        for row in row_dicts
    ]


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


def list_plot_metadata_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> dict:
    """
    Return reference and gene metadata used by the web plotting layer.

    :param conn: open project DB connection
    :param ref_id: optional reference id to filter by
    :return: dict with ``references`` and ``genes`` arrays
    """
    reference_sql = (
        'SELECT DISTINCT r.id AS reference_id, r.name AS reference_name, '
        "r.accession AS reference_accession, r.organism AS reference_organism "
        'FROM reference r '
        'JOIN gene g ON g.reference_id = r.id '
    )
    gene_sql = (
        'SELECT r.id AS reference_id, r.name AS reference_name, g.name AS gene_name, '
        'LENGTH(g.aa_sequence) AS aa_length '
        'FROM gene g '
        'JOIN reference r ON r.id = g.reference_id '
    )

    params: tuple[int, ...] = ()
    if ref_id is not None:
        params = (ref_id,)
        reference_sql += 'WHERE r.id = ? '
        gene_sql += 'WHERE r.id = ? '

    reference_sql += 'ORDER BY r.name'
    gene_sql += 'ORDER BY r.name, g.name'

    reference_rows = conn.execute(reference_sql, params).fetchall()
    gene_rows = conn.execute(gene_sql, params).fetchall()

    references = []
    for row in reference_rows:
        row_dict = dict(row)
        accession = (row_dict.get('reference_accession') or '').strip()
        name = (row_dict.get('reference_name') or '').strip()
        organism = (row_dict.get('reference_organism') or '').strip()
        display_id = accession or name
        display_name = f'{display_id} ({organism})' if organism else display_id
        references.append({
            **row_dict,
            'reference_display_id': display_id,
            'reference_display_name': display_name,
        })

    genes = [dict(row) for row in gene_rows]

    return {
        'references': references,
        'genes': genes,
    }


def get_project_summary_for_display(conn: sqlite3.Connection) -> dict:
    """
    Return project-level summary and optional curator metadata.

    :param conn: open project DB connection
    :return: dict with project identity, creation metadata, and optional curated metadata fields
    """
    row = conn.execute(
        'SELECT id, name, uuid, created_at, schema_version, '
        'metadata_maintainers, metadata_contact, metadata_publication_pmid, '
        'metadata_publication_doi, metadata_website, metadata_description, '
        'metadata_maintainer_update, metadata_license, metadata_tsv_checksum '
        'FROM project ORDER BY id LIMIT 1'
    ).fetchone()
    if row is None:
        raise ValueError('Project DB contains no project metadata.')

    return dict(row)
