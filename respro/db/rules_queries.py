"""Read-only project DB queries for rules, formula rules, references, and display views."""

from __future__ import annotations

import logging
import sqlite3

from respro.core.formula import FORMULA_OPERATORS, RE_FORMULA_TOKEN
from respro.db.models import (
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    is_internal_formula_component_drug_name,
)
from respro.db.report_queries import load_drug_alias_map, load_drug_class_map

logger = logging.getLogger(__name__)


def _is_empty_cell(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == '')


def _strip_empty_columns(row_dicts: list[dict]) -> list[dict]:
    """Drop columns that are empty across every row in *row_dicts*."""
    if not row_dicts:
        return []
    keep = [
        col for col in row_dicts[0]
        if any(not _is_empty_cell(row[col]) for row in row_dicts)
    ]
    return [{col: row[col] for col in keep} for row in row_dicts]


def _feature_display_name_sql(alias: str) -> str:
    """SQL expression resolving mat_peptide display names to their protein label when present."""
    return (
        f"CASE WHEN {alias}.feature_type = 'mat_peptide' "
        f"AND {alias}.protein IS NOT NULL AND {alias}.protein != '' "
        f"THEN {alias}.protein ELSE {alias}.name END"
    )


def load_rules(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRule]:
    """Load all atomic ``ResistanceRule`` rows (with publications) for one reference."""
    rows = conn.execute(
        'SELECT rr.id, g.name AS feature_name, rr.feature_id, '
        'd.name AS drug_name, rr.drug_id, d.pubchem_url, d.description, '
        'rr.external_id, rr.reference_identifier, rr.position, rr.reference, rr.mutation, '
        'rr.phenotype, rr.clinical_phenotype, rr.ic50, rr.fold_ic50, rr.score, rr.source, rr.comment '
        'FROM resistance_rule rr '
        'JOIN feature g ON g.id = rr.feature_id '
        'JOIN drug d ON d.id = rr.drug_id '
        'WHERE g.reference_id = ? '
        'ORDER BY g.name, rr.position',
        (reference_id,),
    ).fetchall()

    rules = [_rule_from_row(row) for row in rows]
    if rules:
        pubs = _fetch_publications_by_owner(
            conn, [r.id for r in rules], link_table='rule_publication', owner_column='rule_id',
        )
        for rule in rules:
            rule.publications = pubs.get(rule.id, [])

    logger.info('Loaded %d resistance rule(s)', len(rules))
    return rules


def load_formula_rules(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRuleSet]:
    """Load formula rules scoped to *reference_id*, skipping cross-reference formulas."""
    formula_rows = conn.execute(
        'SELECT DISTINCT fr.id, fr.formula_id, fr.label, fr.normalized_expression, '
        'fr.phenotype, fr.clinical_phenotype, fr.ic50, fr.fold_ic50, fr.score, '
        'fr.source, fr.comment, d.id AS drug_id, d.name AS drug_name, d.pubchem_url, d.description '
        'FROM resistance_formula_rule fr '
        'JOIN drug d ON d.id = fr.drug_id '
        'JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
        'WHERE g.reference_id = ? ORDER BY fr.id',
        (reference_id,),
    ).fetchall()
    if not formula_rows:
        return []

    formulas: dict[int, ResistanceRuleSet] = {}
    formula_ids_by_pk: dict[int, str] = {}
    for row in formula_rows:
        pk = int(row['id'])
        formula_id = row['formula_id'] or ''
        formula_ids_by_pk[pk] = formula_id
        formulas[pk] = ResistanceRuleSet(
            id=pk,
            drug_name=row['drug_name'] or '',
            drug_id=int(row['drug_id']),
            phenotype=row['phenotype'] or '',
            clinical_phenotype=row['clinical_phenotype'] or '',
            ic50=row['ic50'] or '',
            fold_ic50=row['fold_ic50'] or '',
            score=row['score'] or '',
            source=row['source'] or '',
            group_name=(row['label'] or '') or formula_id,
            pubchem_url=row['pubchem_url'] or '',
            description=row['description'] or '',
            comment=row['comment'] or '',
            logic_expression=row['normalized_expression'] or '',
        )

    placeholders = ','.join('?' * len(formulas))
    member_rows = conn.execute(
        'SELECT frm.formula_rule_id, rr.external_id, rr.reference_identifier, rr.position, '
        'rr.reference, rr.mutation, g.name AS feature_name, g.id AS feature_id, '
        'g.reference_id AS member_reference_id '
        'FROM resistance_formula_rule_member frm '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
        f'WHERE frm.formula_rule_id IN ({placeholders}) '
        'ORDER BY frm.formula_rule_id, rr.external_id, rr.id',
        list(formulas),
    ).fetchall()

    cross_ref_formulas: set[int] = set()
    for row in member_rows:
        formula_pk = int(row['formula_rule_id'])
        if int(row['member_reference_id']) != reference_id:
            cross_ref_formulas.add(formula_pk)
            continue
        external_id = row['external_id'] or ''
        if not external_id:
            continue
        members = formulas[formula_pk].members
        members.append(ResistanceRuleSetMember(
            id=len(members) + 1,
            rule_set_id=formula_pk,
            feature_name=row['feature_name'] or '',
            feature_id=int(row['feature_id']),
            reference_identifier=row['reference_identifier'] or '',
            position=int(row['position']),
            reference=row['reference'] or '',
            mutation=row['mutation'] or '',
            external_id=external_id,
        ))

    if cross_ref_formulas:
        skipped = sorted(cross_ref_formulas)
        logger.warning(
            '%d formula rule(s) skipped — cross-reference members are not allowed: %s',
            len(skipped), ', '.join(formula_ids_by_pk[pk] for pk in skipped),
        )
        for pk in skipped:
            formulas.pop(pk, None)

    result = list(formulas.values())
    if result:
        pubs = _fetch_publications_by_owner(
            conn, [r.id for r in result],
            link_table='resistance_formula_rule_publication', owner_column='formula_rule_id',
        )
        for rule_set in result:
            rule_set.publications = pubs.get(rule_set.id, [])

    logger.info('Loaded %d formula rule(s)', len(result))
    return result


def list_rules_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> list[dict]:
    """Return atomic resistance rules as plain display dicts (empty columns omitted)."""
    drug_group_map = load_drug_class_map(conn)
    publication_doi_expr = (
        "COALESCE(NULLIF(rr.publication, ''), ("
        "SELECT GROUP_CONCAT(doi, '; ') FROM ("
        "SELECT DISTINCT p.doi AS doi FROM rule_publication rp "
        "JOIN publication p ON p.id = rp.publication_id "
        "WHERE rp.rule_id = rr.id AND p.doi IS NOT NULL AND p.doi != '' ORDER BY p.doi"
        "))) AS publication"
    )
    where_sql = 'WHERE g.reference_id = ? ' if ref_id is not None else ''
    params: tuple[int, ...] = (ref_id,) if ref_id is not None else ()
    rows = conn.execute(
        f"SELECT r.name AS reference_name, {_feature_display_name_sql('g')} AS feature, "
        "rr.position, rr.reference, rr.mutation, d.name AS drug, '' AS drug_group, "
        'rr.phenotype, rr.clinical_phenotype, rr.ic50, rr.fold_ic50, rr.score, '
        f'{publication_doi_expr}, rr.source, rr.comment '
        'FROM resistance_rule rr '
        'JOIN feature g ON g.id = rr.feature_id '
        'JOIN reference r ON r.id = g.reference_id '
        'JOIN drug d ON d.id = rr.drug_id '
        f'{where_sql}ORDER BY r.name, feature, rr.position, d.name',
        params,
    ).fetchall()

    row_dicts: list[dict] = []
    for row in rows:
        drug_name = row['drug'] or ''
        if is_internal_formula_component_drug_name(drug_name):
            continue
        item = dict(row)
        item['drug_group'] = drug_group_map.get(drug_name.strip().lower(), '')
        row_dicts.append(item)
    return _strip_empty_columns(row_dicts)


def list_formula_rules_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> list[dict]:
    """Return formula resistance rules as plain display dicts (empty columns omitted)."""
    drug_group_map = load_drug_class_map(conn)
    publication_doi_expr = (
        "COALESCE((SELECT GROUP_CONCAT(doi, '; ') FROM ("
        "SELECT DISTINCT p.doi AS doi FROM resistance_formula_rule_publication frp "
        "JOIN publication p ON p.id = frp.publication_id "
        "WHERE frp.formula_rule_id = fr.id AND p.doi IS NOT NULL AND p.doi != '' ORDER BY p.doi"
        ")), '') AS publication"
    )
    where_sql = (
        'WHERE EXISTS (SELECT 1 FROM resistance_formula_rule_member frm '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
        'WHERE frm.formula_rule_id = fr.id AND g.reference_id = ?) '
        if ref_id is not None else ''
    )
    params: tuple[int, ...] = (ref_id,) if ref_id is not None else ()
    rows = conn.execute(
        "SELECT COALESCE((SELECT r.name FROM resistance_formula_rule_member frm "
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
        'JOIN reference r ON r.id = g.reference_id '
        "WHERE frm.formula_rule_id = fr.id ORDER BY r.name LIMIT 1), '') AS reference_name, "
        "d.name AS drug, '' AS drug_group, fr.formula_id, fr.label, fr.normalized_expression, "
        f'fr.phenotype, fr.clinical_phenotype, fr.ic50, fr.fold_ic50, fr.score, {publication_doi_expr}, '
        'fr.source, fr.comment, '
        'COALESCE((SELECT COUNT(*) FROM resistance_formula_rule_member frm '
        'WHERE frm.formula_rule_id = fr.id), 0) AS member_count '
        'FROM resistance_formula_rule fr '
        'JOIN drug d ON d.id = fr.drug_id '
        f'{where_sql}ORDER BY reference_name, d.name, fr.formula_id',
        params,
    ).fetchall()

    row_dicts = [dict(row) for row in rows]
    if not row_dicts:
        return []

    labels_by_formula = _load_formula_member_labels(conn, ref_id=ref_id)
    for row in row_dicts:
        drug_name = str(row.get('drug', '') or '')
        row['drug_group'] = drug_group_map.get(drug_name.strip().lower(), '')
        formula_id = str(row.get('formula_id', '') or '')
        expression = str(row.get('normalized_expression', '') or '')
        if expression and formula_id in labels_by_formula:
            row['normalized_expression'] = _render_formula_expression(
                expression, labels_by_formula[formula_id],
            )
    return _strip_empty_columns(row_dicts)


def _load_formula_member_labels(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> dict[str, dict[str, str]]:
    where_sql = 'WHERE g.reference_id = ? ' if ref_id is not None else ''
    params: tuple[int, ...] = (ref_id,) if ref_id is not None else ()
    rows = conn.execute(
        f"SELECT fr.formula_id, rr.external_id, {_feature_display_name_sql('g')} AS feature_name, "
        'rr.position, rr.reference, rr.mutation '
        'FROM resistance_formula_rule fr '
        'JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id '
        'JOIN resistance_rule rr ON rr.id = frm.rule_id '
        'JOIN feature g ON g.id = rr.feature_id '
        f'{where_sql}ORDER BY fr.formula_id, rr.external_id',
        params,
    ).fetchall()
    labels: dict[str, dict[str, str]] = {}
    for row in rows:
        formula_id = str(row['formula_id'] or '')
        member_id = str(row['external_id'] or '')
        if formula_id and member_id:
            label = f"{row['feature_name']}:{row['reference']}{int(row['position']) + 1}{row['mutation']}"
            labels.setdefault(formula_id, {})[member_id] = label
    return labels


def _render_formula_expression(expression: str, labels_by_member: dict[str, str]) -> str:
    rendered: list[str] = []
    last_idx = 0
    for match in RE_FORMULA_TOKEN.finditer(expression):
        rendered.append(expression[last_idx:match.start()])
        token = match.group(0)
        if token in {'(', ')'} or token.upper() in FORMULA_OPERATORS:
            rendered.append(token)
        else:
            rendered.append(labels_by_member.get(token, token))
        last_idx = match.end()
    rendered.append(expression[last_idx:])
    return ''.join(rendered)


def _fetch_publications_by_owner(
    conn: sqlite3.Connection,
    owner_ids: list[int],
    *,
    link_table: str,
    owner_column: str,
) -> dict[int, list[Publication]]:
    if not owner_ids:
        return {}
    placeholders = ','.join('?' * len(owner_ids))
    rows = conn.execute(
        f'SELECT lp.{owner_column} AS owner_id, p.id, p.doi, p.title, p.pubmed_id, '
        'p.raw_input, p.first_author, p.year, p.journal '
        f'FROM {link_table} lp JOIN publication p ON p.id = lp.publication_id '
        f'WHERE lp.{owner_column} IN ({placeholders})',
        owner_ids,
    ).fetchall()
    grouped: dict[int, list[Publication]] = {}
    for row in rows:
        grouped.setdefault(int(row['owner_id']), []).append(Publication(
            id=int(row['id']),
            doi=row['doi'] or '',
            title=row['title'] or '',
            pubmed_id=row['pubmed_id'] or '',
            raw_input=row['raw_input'] or '',
            first_author=row['first_author'] or '',
            year=row['year'] or '',
            journal=row['journal'] or '',
        ))
    return grouped


def _rule_from_row(row: sqlite3.Row) -> ResistanceRule:
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


def list_references_for_display(conn: sqlite3.Connection) -> list[dict]:
    """Return all project references ordered by name."""
    rows = conn.execute('SELECT id, name, organism, accession FROM reference ORDER BY name').fetchall()
    return [dict(row) for row in rows]


def list_plot_metadata_for_display(
    conn: sqlite3.Connection,
    ref_id: int | None = None,
) -> dict:
    """Return reference, feature, drug-alias, and drug-group metadata for browsing/plotting."""
    where_sql = 'WHERE r.id = ? ' if ref_id is not None else ''
    params: tuple[int, ...] = (ref_id,) if ref_id is not None else ()

    reference_rows = conn.execute(
        'SELECT DISTINCT r.id AS reference_id, r.name AS reference_name, '
        'r.accession AS reference_accession, r.organism AS reference_organism '
        f'FROM reference r JOIN feature g ON g.reference_id = r.id {where_sql}ORDER BY r.name',
        params,
    ).fetchall()
    feature_rows = conn.execute(
        f"SELECT r.id AS reference_id, r.name AS reference_name, {_feature_display_name_sql('g')} AS feature_name, "
        'LENGTH(g.aa_sequence) AS aa_length '
        f'FROM feature g JOIN reference r ON r.id = g.reference_id {where_sql}ORDER BY r.name, feature_name',
        params,
    ).fetchall()

    references = []
    for row in reference_rows:
        item = dict(row)
        display_id = (item.get('reference_accession') or '').strip() or (item.get('reference_name') or '').strip()
        organism = (item.get('reference_organism') or '').strip()
        references.append({
            **item,
            'reference_display_id': display_id,
            'reference_display_name': f'{display_id} ({organism})' if organism else display_id,
        })

    return {
        'references': references,
        'features': [dict(row) for row in feature_rows],
        'drug_aliases': load_drug_alias_map(conn),
        'drug_groups': load_drug_class_map(conn),
    }


def get_project_summary_for_display(conn: sqlite3.Connection) -> dict:
    """Return project-level identity and curator metadata."""
    row = conn.execute(
        'SELECT id, name, uuid, created_at, schema_version, '
        'metadata_maintainers, metadata_contact, metadata_publication_pmid, '
        'metadata_publication_doi, metadata_website, metadata_description, '
        'metadata_maintainer_update, metadata_license, metadata_tsv_checksum, example_fasta '
        'FROM project ORDER BY id LIMIT 1'
    ).fetchone()
    if row is None:
        raise ValueError('Project DB contains no project metadata.')
    return dict(row)


def get_project_example_fasta(conn: sqlite3.Connection) -> str | None:
    """Return the stored example consensus FASTA text, or ``None`` if absent."""
    row = conn.execute('SELECT example_fasta FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Project DB contains no project metadata.')
    return row['example_fasta'] or None
