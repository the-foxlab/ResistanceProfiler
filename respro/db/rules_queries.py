"""
Read-only query helpers for resistance rules — reusable without any CLI dependency.
"""

from __future__ import annotations

import re
import sqlite3


_RE_LOGIC_TOKEN = re.compile(
    r'\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bXOR\b|[A-Za-z0-9_.:-]+',
    re.IGNORECASE,
)
_LOGIC_OPERATORS = {'AND', 'OR', 'NOT', 'XOR'}


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


def list_formula_rules_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> list[dict]:
    """
    Return formula rules as plain dicts suitable for tabular display.

    Rows are ordered by reference name, drug name, then formula identifier.
    When ``ref_id`` is given, only formulas with at least one member rule in that reference are returned.

    :param conn: open project DB connection
    :param ref_id: optional reference id to filter by
    :return: list of formula rule dicts with non-empty columns only
    """
    publication_doi_expr = (
        "COALESCE(("
        "SELECT GROUP_CONCAT(doi, '; ') FROM ("
        "SELECT DISTINCT p.doi AS doi "
        "FROM resistance_formula_rule_publication frp "
        "JOIN publication p ON p.id = frp.publication_id "
        "WHERE frp.formula_rule_id = fr.id AND p.doi IS NOT NULL AND p.doi != '' "
        "ORDER BY p.doi"
        ")"
        "), '') AS publication"
    )

    base_sql = (
        'SELECT '
        "COALESCE((SELECT r.name "
        "FROM resistance_formula_rule_member frm "
        "JOIN resistance_rule rr ON rr.id = frm.rule_id "
        "JOIN gene g ON g.id = rr.gene_id "
        "JOIN reference r ON r.id = g.reference_id "
        'WHERE frm.formula_rule_id = fr.id '
        'ORDER BY r.name LIMIT 1), \'\') AS reference_name, '
        'd.name AS drug, fr.formula_id, fr.label, fr.normalized_expression, '
        'fr.phenotype, fr.clinical_phenotype, fr.ic50, fr.fold_ic50, '
        + publication_doi_expr + ', '
        'fr.source, fr.comment, '
        'COALESCE((SELECT COUNT(*) FROM resistance_formula_rule_member frm '
        'WHERE frm.formula_rule_id = fr.id), 0) AS member_count '
        'FROM resistance_formula_rule fr '
        'JOIN drug d ON d.id = fr.drug_id '
    )

    if ref_id is not None:
        rows = conn.execute(
            base_sql
            + 'WHERE EXISTS (SELECT 1 FROM resistance_formula_rule_member frm '
            'JOIN resistance_rule rr ON rr.id = frm.rule_id '
            'JOIN gene g ON g.id = rr.gene_id '
            'WHERE frm.formula_rule_id = fr.id AND g.reference_id = ?) '
            'ORDER BY reference_name, d.name, fr.formula_id',
            (ref_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            base_sql + 'ORDER BY reference_name, d.name, fr.formula_id'
        ).fetchall()

    row_dicts = [dict(row) for row in rows]
    if not row_dicts:
        return []

    labels_by_formula_id = _load_formula_member_labels_for_display(conn, ref_id=ref_id)
    for row in row_dicts:
        formula_id = str(row.get('formula_id', '') or '')
        expression = str(row.get('normalized_expression', '') or '')
        if expression and formula_id and formula_id in labels_by_formula_id:
            row['normalized_expression'] = _replace_formula_expression_for_display(
                expression,
                labels_by_formula_id[formula_id],
            )

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


def _load_formula_member_labels_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> dict[str, dict[str, str]]:
    """Return formula_id -> (member_id -> display label) mapping for expression rendering."""
    sql = (
        'SELECT fr.formula_id, rr.external_id, g.name AS gene_name, '
        'rr.position, rr.reference, rr.mutation '
        'FROM resistance_formula_rule fr '
        'JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN gene g ON g.id = rr.gene_id '
    )
    params: tuple[int, ...] = ()
    if ref_id is not None:
        sql += 'WHERE g.reference_id = ? '
        params = (ref_id,)
    sql += 'ORDER BY fr.formula_id, rr.external_id'

    rows = conn.execute(sql, params).fetchall()
    labels: dict[str, dict[str, str]] = {}
    for row in rows:
        formula_id = str(row['formula_id'] or '')
        member_id = str(row['external_id'] or '')
        if formula_id == '' or member_id == '':
            continue

        position = int(row['position']) + 1
        label = f"{row['gene_name']}:{row['reference']}{position}{row['mutation']}"
        labels.setdefault(formula_id, {})[member_id] = label
    return labels


def _replace_formula_expression_for_display(
    expression: str,
    labels_by_member_id: dict[str, str],
) -> str:
    """Replace formula member IDs in one expression by human-readable mutation labels."""
    rendered: list[str] = []
    last_index = 0
    for match in _RE_LOGIC_TOKEN.finditer(expression):
        rendered.append(expression[last_index:match.start()])
        token = match.group(0)
        upper_token = token.upper()
        if token in {'(', ')'} or upper_token in _LOGIC_OPERATORS:
            rendered.append(token)
        else:
            rendered.append(labels_by_member_id.get(token, token))
        last_index = match.end()

    rendered.append(expression[last_index:])
    return ''.join(rendered)


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
