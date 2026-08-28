"""
Read-only query helpers for resistance rules — reusable without any CLI dependency.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from respro.db._rules_formula import _FORMULA_OPERATORS as _LOGIC_OPERATORS
from respro.db._rules_formula import _RE_FORMULA_TOKEN as _RE_LOGIC_TOKEN
from respro.db.models import (
    FormulaRuleRuntime,
    Publication,
    ResistanceRule,
    is_internal_formula_component_drug_name,
)

logger = logging.getLogger(__name__)


def _is_empty_cell(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ''
    return False


def _feature_display_name_sql(feature_alias: str) -> str:
    """Return SQL expression for feature display names (mat_peptide -> protein when present)."""
    return (
        f"CASE WHEN {feature_alias}.feature_type = 'mat_peptide' "
        f"AND {feature_alias}.protein IS NOT NULL AND {feature_alias}.protein != '' "
        f"THEN {feature_alias}.protein ELSE {feature_alias}.name END"
    )


def _load_drug_alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return normalized drug-name -> alias mappings from the drug table."""
    rows = conn.execute(
        "SELECT name, alias FROM drug "
        "WHERE project_id = (SELECT id FROM project ORDER BY id LIMIT 1) "
        "AND alias IS NOT NULL AND alias != '' ORDER BY LOWER(name), alias"
    ).fetchall()
    alias_map: dict[str, str] = {}
    for row in rows:
        name = (row['name'] or '').strip().lower()
        alias = (row['alias'] or '').strip()
        if name and alias:
            alias_map[name] = alias
    return alias_map


def _load_drug_group_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return normalized drug-name -> drug-group mappings from interpretation_algorithm."""
    try:
        row = conn.execute(
            "SELECT config_json FROM interpretation_algorithm "
            "WHERE project_id = (SELECT id FROM project ORDER BY id LIMIT 1) "
            "AND algorithm_name = 'drug_groups' LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return {}

    if row is None:
        return {}

    config = json.loads(row['config_json'])
    groups = config.get('groups', {})
    group_map: dict[str, str] = {}
    for group_name in sorted(groups, key=lambda value: value.lower()):
        members = groups.get(group_name, [])
        for member in sorted(members, key=lambda value: value.lower()):
            normalized_member = member.strip().lower()
            if normalized_member:
                group_map[normalized_member] = group_name
    return group_map


def load_rules(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRule]:
    """
    Load all resistance rules for features belonging to a reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of ResistanceRule objects
    """
    rows = conn.execute(
        """
        SELECT
            rr.id, g.name AS feature_name, rr.feature_id,
            d.name AS drug_name, rr.drug_id,
            d.pubchem_url, d.description,
            rr.external_id,
            rr.reference_identifier,
            rr.position, rr.reference, rr.mutation,
            rr.phenotype, rr.clinical_phenotype, rr.ic50, rr.fold_ic50, rr.score, rr.source, rr.comment
        FROM resistance_rule rr
        JOIN feature g ON g.id = rr.feature_id
        JOIN drug d ON d.id = rr.drug_id
        WHERE g.reference_id = ?
        ORDER BY g.name, rr.position
        """,
        (reference_id,),
    ).fetchall()

    rules = [_rule_from_row(row) for row in rows]
    if rules:
        _attach_publications_to_rules(conn, rules)

    logger.info('Loaded %d resistance rule(s)', len(rules))
    return rules


def load_formula_rules(conn: sqlite3.Connection, reference_id: int) -> list[FormulaRuleRuntime]:
    """
    Load formula rules for one reference and include only same-reference member rules.

    Formulas that reference at least one atomic rule outside the active reference are
    skipped with a warning.
    """
    formula_rows = conn.execute(
        """
        SELECT DISTINCT
            fr.id,
            fr.formula_id,
            fr.label,
            fr.normalized_expression,
            fr.phenotype,
            fr.clinical_phenotype,
            fr.ic50,
            fr.fold_ic50,
            fr.score,
            fr.source,
            fr.comment,
            d.id AS drug_id,
            d.name AS drug_name,
            d.pubchem_url,
            d.description
        FROM resistance_formula_rule fr
        JOIN drug d ON d.id = fr.drug_id
        JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id
        JOIN resistance_rule rr ON rr.id = frm.rule_id
        JOIN feature g ON g.id = rr.feature_id
        WHERE g.reference_id = ?
        ORDER BY fr.id
        """,
        (reference_id,),
    ).fetchall()

    if not formula_rows:
        return []

    formulas: dict[int, FormulaRuleRuntime] = {}
    for row in formula_rows:
        formulas[int(row['id'])] = FormulaRuleRuntime(
            id=int(row['id']),
            formula_id=row['formula_id'] or '',
            label=row['label'] or '',
            normalized_expression=row['normalized_expression'] or '',
            drug_name=row['drug_name'] or '',
            drug_id=int(row['drug_id']),
            phenotype=row['phenotype'] or '',
            clinical_phenotype=row['clinical_phenotype'] or '',
            ic50=row['ic50'] or '',
            fold_ic50=row['fold_ic50'] or '',
            score=row['score'] or '',
            source=row['source'] or '',
            comment=row['comment'] or '',
            pubchem_url=row['pubchem_url'] or '',
            description=row['description'] or '',
        )

    placeholders = ','.join('?' * len(formulas))
    member_rows = conn.execute(
        f"""
        SELECT
            frm.formula_rule_id,
            rr.id,
            rr.external_id,
            rr.reference_identifier,
            rr.position,
            rr.reference,
            rr.mutation,
            rr.phenotype,
            rr.clinical_phenotype,
            rr.ic50,
            rr.fold_ic50,
            rr.score,
            rr.source,
            rr.comment,
            rr.drug_id,
            d.name AS drug_name,
            d.pubchem_url,
            d.description,
            g.name AS feature_name,
            g.id AS feature_id,
            g.reference_id AS member_reference_id
        FROM resistance_formula_rule_member frm
        JOIN resistance_rule rr ON rr.id = frm.rule_id
        JOIN drug d ON d.id = rr.drug_id
        JOIN feature g ON g.id = rr.feature_id
        WHERE frm.formula_rule_id IN ({placeholders})
        ORDER BY frm.formula_rule_id, rr.external_id, rr.id
        """,
        list(formulas.keys()),
    ).fetchall()

    formulas_with_cross_reference_members: set[int] = set()
    for row in member_rows:
        formula_rule_id = int(row['formula_rule_id'])
        if int(row['member_reference_id']) != reference_id:
            formulas_with_cross_reference_members.add(formula_rule_id)
            continue
        external_id = row['external_id'] or ''
        if external_id == '':
            continue
        formulas[formula_rule_id].member_rules[external_id] = ResistanceRule(
            id=int(row['id']),
            feature_name=row['feature_name'] or '',
            feature_id=int(row['feature_id']),
            drug_name=row['drug_name'] or '',
            drug_id=int(row['drug_id']),
            external_id=external_id,
            reference_identifier=row['reference_identifier'] or '',
            position=int(row['position']),
            reference=row['reference'] or '',
            mutation=row['mutation'] or '',
            phenotype=row['phenotype'] or '',
            clinical_phenotype=row['clinical_phenotype'] or '',
            ic50=row['ic50'] or '',
            fold_ic50=row['fold_ic50'] or '',
            score=row['score'] or '',
            source=row['source'] or '',
            comment=row['comment'] or '',
            pubchem_url=row['pubchem_url'] or '',
            description=row['description'] or '',
            is_internal_formula_component=is_internal_formula_component_drug_name(row['drug_name'] or ''),
        )

    if formulas_with_cross_reference_members:
        skipped = sorted(formulas_with_cross_reference_members)
        logger.warning(
            '%d formula rule(s) skipped — cross-reference members are not allowed: %s',
            len(skipped),
            ', '.join(formulas[formula_id].formula_id for formula_id in skipped),
        )
        for formula_id in skipped:
            formulas.pop(formula_id, None)

    if formulas:
        _attach_publications_to_formula_rules(conn, list(formulas.values()))

    logger.info('Loaded %d formula rule(s)', len(formulas))
    return list(formulas.values())


def list_rules_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> list[dict]:
    """
    Return resistance rules as plain dicts suitable for tabular display.

    Rows are ordered by feature name, position, then drug name.
    When ``ref_id`` is given, only rules for features belonging to that reference are returned.

    :param conn: open project DB connection
    :param ref_id: optional reference id to filter by
    :return: list of rule dicts with non-empty columns only; publication is populated from DOI
             values linked through ``rule_publication``/``publication`` when available
    """
    drug_group_map = _load_drug_group_map(conn)
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
    feature_display_expr = _feature_display_name_sql('g')

    if ref_id is not None:
        rows = conn.execute(
            f'SELECT r.name AS reference_name, {feature_display_expr} AS feature, '
            'rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, \'\' AS drug_group, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, rr.score, ' + publication_doi_expr + ', rr.source, rr.comment '
            'FROM resistance_rule rr '
            'JOIN feature g ON g.id = rr.feature_id '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'WHERE g.reference_id = ? '
            'ORDER BY r.name, feature, rr.position, d.name',
            (ref_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f'SELECT r.name AS reference_name, {feature_display_expr} AS feature, '
            'rr.position, rr.reference, rr.mutation, '
            'd.name AS drug, \'\' AS drug_group, rr.phenotype, rr.clinical_phenotype, '
            'rr.ic50, rr.fold_ic50, rr.score, ' + publication_doi_expr + ', rr.source, rr.comment '
            'FROM resistance_rule rr '
            'JOIN feature g ON g.id = rr.feature_id '
            'JOIN reference r ON r.id = g.reference_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY r.name, feature, rr.position, d.name',
        ).fetchall()

    row_dicts = []
    for row in rows:
        drug_name = row['drug'] or ''
        if is_internal_formula_component_drug_name(drug_name):
            continue
        row_dict = dict(row)
        row_dict['drug_group'] = drug_group_map.get(drug_name.strip().lower(), '')
        row_dicts.append(row_dict)
    if not row_dicts:
        return []

    column_names = list(row_dicts[0].keys())
    non_empty_columns = [
        column_name
        for column_name in column_names
        if any(not _is_empty_cell(row[column_name]) for row in row_dicts)
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
    drug_group_map = _load_drug_group_map(conn)
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
        "JOIN feature g ON g.id = rr.feature_id "
        "JOIN reference r ON r.id = g.reference_id "
        'WHERE frm.formula_rule_id = fr.id '
        'ORDER BY r.name LIMIT 1), \'\') AS reference_name, '
        'd.name AS drug, \'\' AS drug_group, fr.formula_id, fr.label, fr.normalized_expression, '
        'fr.phenotype, fr.clinical_phenotype, fr.ic50, fr.fold_ic50, fr.score, '
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
            'JOIN feature g ON g.id = rr.feature_id '
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

    for row in row_dicts:
        drug_name = str(row.get('drug', '') or '')
        row['drug_group'] = drug_group_map.get(drug_name.strip().lower(), '')

    labels_by_formula_id = _load_formula_member_labels_for_display(conn, ref_id=ref_id)
    for row in row_dicts:
        formula_id = str(row.get('formula_id', '') or '')
        expression = str(row.get('normalized_expression', '') or '')
        if expression and formula_id and formula_id in labels_by_formula_id:
            row['normalized_expression'] = _replace_formula_expression_for_display(
                expression,
                labels_by_formula_id[formula_id],
            )

    column_names = list(row_dicts[0].keys())
    non_empty_columns = [
        column_name
        for column_name in column_names
        if any(not _is_empty_cell(row[column_name]) for row in row_dicts)
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
    feature_display_expr = _feature_display_name_sql('g')
    sql = (
        f'SELECT fr.formula_id, rr.external_id, {feature_display_expr} AS feature_name, '
        'rr.position, rr.reference, rr.mutation '
        'FROM resistance_formula_rule fr '
        'JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
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
        label = f"{row['feature_name']}:{row['reference']}{position}{row['mutation']}"
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


def _publication_from_row(row: sqlite3.Row) -> Publication:
    """Build one Publication object from a SQLite row."""
    return Publication(
        id=int(row['id']),
        doi=row['doi'] or '',
        title=row['title'] or '',
        pubmed_id=row['pubmed_id'] or '',
        raw_input=row['raw_input'] or '',
        first_author=row['first_author'] or '',
        year=row['year'] or '',
        journal=row['journal'] or '',
    )


def _fetch_publications_by_owner(
    conn: sqlite3.Connection,
    owner_ids: list[int],
    *,
    link_table: str,
    owner_column: str,
) -> dict[int, list[Publication]]:
    """Fetch publications grouped by owner id (rule or formula rule)."""
    if not owner_ids:
        return {}

    placeholders = ','.join('?' * len(owner_ids))
    rows = conn.execute(
        f'SELECT lp.{owner_column} AS owner_id, p.id, p.doi, p.title, p.pubmed_id, '
        f'p.raw_input, p.first_author, p.year, p.journal '
        f'FROM {link_table} lp '
        f'JOIN publication p ON p.id = lp.publication_id '
        f'WHERE lp.{owner_column} IN ({placeholders})',
        owner_ids,
    ).fetchall()

    grouped: dict[int, list[Publication]] = {}
    for row in rows:
        grouped.setdefault(int(row['owner_id']), []).append(_publication_from_row(row))
    return grouped


def _rule_from_row(row: sqlite3.Row) -> ResistanceRule:
    """Build one ResistanceRule object from a SQLite row."""
    return ResistanceRule(
        id=row['id'],
        feature_name=row['feature_name'],
        feature_id=row['feature_id'],
        drug_name=row['drug_name'],
        drug_id=row['drug_id'],
        external_id=row['external_id'] or '',
        reference_identifier=row['reference_identifier'] or '',
        position=row['position'],
        reference=row['reference'] or '',
        mutation=row['mutation'],
        phenotype=row['phenotype'],
        clinical_phenotype=row['clinical_phenotype'] or '',
        ic50=row['ic50'] or '',
        fold_ic50=row['fold_ic50'] or '',
        score=row['score'] or '',
        source=row['source'] or '',
        comment=row['comment'] or '',
        pubchem_url=row['pubchem_url'] or '',
        description=row['description'] or '',
        is_internal_formula_component=is_internal_formula_component_drug_name(row['drug_name'] or ''),
    )


def _attach_publications_to_rules(
    conn: sqlite3.Connection,
    rules: list[ResistanceRule],
) -> None:
    """
    Batch-load publications for a list of rules and assign them in place.

    :param conn: SQLite database connection
    :param rules: list of ResistanceRule objects to enrich
    """
    rule_ids = [rule.id for rule in rules]
    pubs_by_rule = _fetch_publications_by_owner(
        conn,
        rule_ids,
        link_table='rule_publication',
        owner_column='rule_id',
    )
    for rule in rules:
        rule.publications = pubs_by_rule.get(rule.id, [])


def _attach_publications_to_formula_rules(
    conn: sqlite3.Connection,
    formula_rules: list[FormulaRuleRuntime],
) -> None:
    """Batch-load publications for formula rules and assign them in place."""
    formula_ids = [formula_rule.id for formula_rule in formula_rules]
    pubs_by_formula = _fetch_publications_by_owner(
        conn,
        formula_ids,
        link_table='resistance_formula_rule_publication',
        owner_column='formula_rule_id',
    )
    for formula_rule in formula_rules:
        formula_rule.publications = pubs_by_formula.get(formula_rule.id, [])


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
    Return reference and feature metadata used by the web plotting layer.

    :param conn: open project DB connection
    :param ref_id: optional reference id to filter by
    :return: dict with ``references`` and ``features`` arrays
    """
    drug_aliases = _load_drug_alias_map(conn)
    drug_groups = _load_drug_group_map(conn)
    reference_sql = (
        'SELECT DISTINCT r.id AS reference_id, r.name AS reference_name, '
        "r.accession AS reference_accession, r.organism AS reference_organism "
        'FROM reference r '
        'JOIN feature g ON g.reference_id = r.id '
    )
    feature_display_expr = _feature_display_name_sql('g')
    feature_sql = (
        f'SELECT r.id AS reference_id, r.name AS reference_name, {feature_display_expr} AS feature_name, '
        'LENGTH(g.aa_sequence) AS aa_length '
        'FROM feature g '
        'JOIN reference r ON r.id = g.reference_id '
    )

    params: tuple[int, ...] = ()
    if ref_id is not None:
        params = (ref_id,)
        reference_sql += 'WHERE r.id = ? '
        feature_sql += 'WHERE r.id = ? '

    reference_sql += 'ORDER BY r.name'
    feature_sql += 'ORDER BY r.name, feature_name'

    reference_rows = conn.execute(reference_sql, params).fetchall()
    feature_rows = conn.execute(feature_sql, params).fetchall()

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

    features = [dict(row) for row in feature_rows]

    return {
        'references': references,
        'features': features,
        'drug_aliases': drug_aliases,
        'drug_groups': drug_groups,
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
        'metadata_maintainer_update, metadata_license, metadata_tsv_checksum, '
        'example_fasta '
        'FROM project ORDER BY id LIMIT 1'
    ).fetchone()
    if row is None:
        raise ValueError('Project DB contains no project metadata.')

    return dict(row)


def get_project_example_fasta(conn: sqlite3.Connection) -> str | None:
    """
    Return the stored example consensus FASTA text, or ``None`` when none is stored.

    :param conn: open project DB connection
    :return: FASTA text (single record) or ``None`` if the ``example_fasta`` column is empty
    """
    row = conn.execute('SELECT example_fasta FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Project DB contains no project metadata.')
    value = row['example_fasta']
    return value if value else None
