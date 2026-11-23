"""Read-only project DB accessors shared by report generation and rule display."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import statistics
from collections.abc import Sequence

from respro.db.models import is_internal_formula_component_drug_name

logger = logging.getLogger(__name__)

_RE_LEADING_NUM = re.compile(r'^[><=~≥≤≈\s]*(-?\d+(?:\.\d+)?)')

_KNOWN_PHENOTYPE_CLAUSE = (
    "(TRIM(COALESCE(phenotype, '')) <> '' AND LOWER(TRIM(phenotype)) <> 'unknown') "
    "OR (TRIM(COALESCE(clinical_phenotype, '')) <> '' "
    "AND LOWER(TRIM(clinical_phenotype)) <> 'unknown')"
)


def parse_numeric_value(value_str: str) -> float | None:
    """Extract a leading float from a potentially qualified metric string (e.g. '>0.5 µM')."""
    match = _RE_LEADING_NUM.match(value_str.strip())
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def load_numeric_metric_thresholds(
    project_conn: sqlite3.Connection | None,
) -> dict[str, tuple[float, float] | None]:
    """Compute ``(mean, stdev)`` across single and formula rules for ``ic50``, ``fold_ic50``, ``score``."""
    if project_conn is None:
        return {}
    try:
        rows = project_conn.execute(
            'SELECT ic50, fold_ic50, score FROM resistance_rule '
            'UNION ALL '
            'SELECT ic50, fold_ic50, score FROM resistance_formula_rule'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load numeric metric stats from DB: %s', exc)
        return {}

    buckets: dict[str, list[float]] = {'ic50': [], 'fold_ic50': [], 'score': []}
    for row in rows:
        for field in buckets:
            parsed = parse_numeric_value(row[field] or '')
            if parsed is not None:
                buckets[field].append(parsed)

    result: dict[str, tuple[float, float] | None] = {}
    for field, values in buckets.items():
        if len(values) < 2:
            result[field] = None
        else:
            mean = statistics.mean(values)
            std = statistics.stdev(values)
            result[field] = (mean, std) if std > 0 else None
    return result


def load_algorithm_config(
    project_conn: sqlite3.Connection | None,
    algorithm_name: str,
) -> dict | None:
    """Load one interpretation algorithm config dict by name, or ``None`` if absent."""
    if project_conn is None:
        return None
    try:
        row = project_conn.execute(
            'SELECT config_json FROM interpretation_algorithm '
            'WHERE algorithm_name = ? LIMIT 1',
            (algorithm_name,),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to load %s algorithm from DB: %s', algorithm_name, exc)
        return None
    if row is None:
        return None
    try:
        config = json.loads(row['config_json'])
    except (TypeError, json.JSONDecodeError) as exc:
        logger.debug('Failed to parse %s algorithm config JSON: %s', algorithm_name, exc)
        return None
    return config if isinstance(config, dict) else None


def load_drug_interpretation_configs(project_conn: sqlite3.Connection | None) -> list[dict]:
    """Load all ``drug_interpretation`` algorithm configs in insertion order."""
    if project_conn is None:
        return []
    try:
        rows = project_conn.execute(
            "SELECT config_json FROM interpretation_algorithm "
            "WHERE algorithm_name = 'drug_interpretation' ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug_interpretation algorithm: %s', exc)
        return []
    configs: list[dict] = []
    for row in rows:
        try:
            cfg = json.loads(row['config_json'])
        except (TypeError, json.JSONDecodeError) as exc:
            logger.debug('Skipping malformed drug_interpretation config: %s', exc)
            continue
        if isinstance(cfg, dict):
            configs.append(cfg)
    return configs


def has_any_phenotype_association(project_conn: sqlite3.Connection | None) -> bool:
    """Return True when any resistance rule records a known phenotype or clinical phenotype."""
    if project_conn is None:
        return False
    try:
        row = project_conn.execute(
            f'SELECT (EXISTS(SELECT 1 FROM resistance_rule WHERE {_KNOWN_PHENOTYPE_CLAUSE}) '
            f'OR EXISTS(SELECT 1 FROM resistance_formula_rule WHERE {_KNOWN_PHENOTYPE_CLAUSE})) AS has_rows'
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to check phenotype association rows in DB: %s', exc)
        return False
    return bool(row['has_rows']) if row is not None else False


def load_in_scope_drug_names(
    project_conn: sqlite3.Connection | None,
    profiled_feature_ids: set[int],
) -> list[str]:
    """Return distinct non-internal drug names that have rules on *profiled_feature_ids*."""
    if project_conn is None or not profiled_feature_ids:
        return []
    placeholders = ','.join('?' * len(profiled_feature_ids))
    params = tuple(profiled_feature_ids)
    try:
        table_names = {
            row[0]
            for row in project_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if {'resistance_formula_rule', 'resistance_formula_rule_member'}.issubset(table_names):
            rows = project_conn.execute(
                f'SELECT name FROM ('
                f'  SELECT d.name AS name FROM drug d '
                f'  JOIN resistance_rule r ON r.drug_id = d.id '
                f'  WHERE r.feature_id IN ({placeholders}) '
                f'  UNION '
                f'  SELECT d.name AS name FROM drug d '
                f'  JOIN resistance_formula_rule fr ON fr.drug_id = d.id '
                f'  JOIN resistance_formula_rule_member m ON m.formula_rule_id = fr.id '
                f'  JOIN resistance_rule r ON r.id = m.rule_id '
                f'  WHERE r.feature_id IN ({placeholders})'
                f') ORDER BY name',
                params + params,
            ).fetchall()
        else:
            rows = project_conn.execute(
                f'SELECT DISTINCT d.name FROM drug d '
                f'JOIN resistance_rule r ON r.drug_id = d.id '
                f'WHERE r.feature_id IN ({placeholders}) '
                f'ORDER BY d.name',
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load in-scope drugs from DB: %s', exc)
        return []
    return [
        name
        for row in rows
        if (name := (row[0] or '').strip()) and not is_internal_formula_component_drug_name(name)
    ]


def load_tier1_phenotype_rows(
    project_conn: sqlite3.Connection | None,
    reference_ids: set[int],
) -> list[tuple[str, str, str, int]]:
    """Return ``(drug_name, phenotype, clinical_phenotype, count)`` rows for *reference_ids*.

    Returns an empty list when *project_conn* is a partial DB lacking the core rule
    tables, while letting genuine SQLite errors propagate.
    """
    if project_conn is None or not reference_ids:
        return []
    table_names = {
        row[0]
        for row in project_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if not {'drug', 'resistance_rule', 'feature'}.issubset(table_names):
        logger.debug('Project DB lacks core rule schema; no tier-1 labels resolved.')
        return []
    placeholders = ','.join('?' * len(reference_ids))
    params = tuple(reference_ids)
    if {'resistance_formula_rule', 'resistance_formula_rule_member'}.issubset(table_names):
        rows = project_conn.execute(
            f'SELECT name, phenotype, clinical_phenotype, COUNT(*) AS n FROM ('
            f'  SELECT d.name, r.phenotype, r.clinical_phenotype '
            f'  FROM drug d '
            f'  JOIN resistance_rule r ON r.drug_id = d.id '
            f'  JOIN feature f ON r.feature_id = f.id '
            f'  WHERE f.reference_id IN ({placeholders}) '
            f'  UNION ALL '
            f'  SELECT name, phenotype, clinical_phenotype FROM ('
            f'    SELECT DISTINCT fr.id, d.name, fr.phenotype, fr.clinical_phenotype '
            f'    FROM drug d '
            f'    JOIN resistance_formula_rule fr ON fr.drug_id = d.id '
            f'    JOIN resistance_formula_rule_member m ON m.formula_rule_id = fr.id '
            f'    JOIN resistance_rule r ON r.id = m.rule_id '
            f'    JOIN feature f ON r.feature_id = f.id '
            f'    WHERE f.reference_id IN ({placeholders})'
            f'  )'
            f') GROUP BY name, phenotype, clinical_phenotype '
            f'ORDER BY name, phenotype, clinical_phenotype',
            params + params,
        ).fetchall()
    else:
        rows = project_conn.execute(
            f'SELECT d.name, r.phenotype, r.clinical_phenotype, COUNT(*) AS n '
            f'FROM drug d '
            f'JOIN resistance_rule r ON r.drug_id = d.id '
            f'JOIN feature f ON r.feature_id = f.id '
            f'WHERE f.reference_id IN ({placeholders}) '
            f'GROUP BY d.name, r.phenotype, r.clinical_phenotype '
            f'ORDER BY d.name, r.phenotype, r.clinical_phenotype',
            params,
        ).fetchall()
    return [(row[0], row[1], row[2], int(row[3])) for row in rows]


def load_drug_class_map(project_conn: sqlite3.Connection | None) -> dict[str, str]:
    """Return a lowercase-drug-name -> class-name mapping from ``drug_groups`` config."""
    config = load_algorithm_config(project_conn, 'drug_groups')
    if config is None:
        return {}
    groups = config.get('groups', {})
    drug_map: dict[str, str] = {}
    for group_name in sorted(groups, key=str.lower):
        for member in sorted(groups.get(group_name, []), key=str.lower):
            key = member.strip().lower()
            if key:
                drug_map[key] = group_name
    return drug_map


def load_drug_alias_map(project_conn: sqlite3.Connection | None) -> dict[str, str]:
    """Return a lowercase canonical-drug-name -> short alias mapping from the ``drug`` table."""
    if project_conn is None:
        return {}
    try:
        rows = project_conn.execute(
            "SELECT name, alias FROM drug WHERE alias IS NOT NULL AND alias != '' "
            'ORDER BY LOWER(name), alias'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug aliases from DB: %s', exc)
        return {}

    alias_map: dict[str, str] = {}
    for row in rows:
        canonical = (row['name'] or '').strip().lower()
        short = (row['alias'] or '').strip()
        if canonical and short:
            alias_map[canonical] = short
    return alias_map


def has_interpretation_algorithm(project_conn: sqlite3.Connection | None) -> bool:
    """Return True when the project DB configures at least one interpretation algorithm."""
    if project_conn is None:
        return False
    try:
        return project_conn.execute('SELECT 1 FROM interpretation_algorithm LIMIT 1').fetchone() is not None
    except sqlite3.Error as exc:
        logger.debug('Failed to check interpretation_algorithm table: %s', exc)
        return False


def load_drug_cards(
    project_conn: sqlite3.Connection | None,
    detected_drug_names: set[str] | None = None,
) -> list[dict]:
    """Load DB-backed drug metadata cards for drugs present in *detected_drug_names*."""
    if project_conn is None or not detected_drug_names:
        return []
    try:
        rows = project_conn.execute(
            'SELECT name, pubchem_url, description, structure_url FROM drug ORDER BY name'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug cards from project DB: %s', exc)
        return []

    normalized_names = {n.strip().lower() for n in detected_drug_names if n}
    cards = [
        {
            'name': name,
            'pubchem_url': row['pubchem_url'] or '',
            'description': row['description'] or '',
            'structure_url': row['structure_url'] or '',
        }
        for row in rows
        if (name := (row['name'] or '').strip()) and name.lower() in normalized_names
    ]
    cards.sort(key=lambda card: card['name'].lower())
    return cards


def load_feature_cards(
    project_conn: sqlite3.Connection | None,
    reference_name: str | Sequence[str],
    detected_feature_names: set[str] | None = None,
) -> list[dict]:
    """Load feature metadata cards for *detected_feature_names* on one or more references."""
    if project_conn is None or not detected_feature_names:
        return []
    ref_names = [reference_name] if isinstance(reference_name, str) else [r for r in reference_name if r]
    if not ref_names:
        return []
    placeholders = ','.join('?' * len(ref_names))
    try:
        rows = project_conn.execute(
            f'SELECT r.name AS reference_name, g.name, g.protein, g.protein_id, '
            f'g.ncbi_protein_url, g.locus_tag, g.note, g.nt_sequence, g.aa_sequence '
            f'FROM feature g '
            f'JOIN reference r ON r.id = g.reference_id '
            f'WHERE r.name IN ({placeholders}) ORDER BY g.start',
            tuple(ref_names),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load feature cards for %r: %s', ref_names, exc)
        return []

    cards = [
        {
            'reference_name': row['reference_name'] or '',
            'name': name,
            'protein': row['protein'] or '',
            'protein_id': row['protein_id'] or '',
            'ncbi_protein_url': row['ncbi_protein_url'] or '',
            'locus_tag': row['locus_tag'] or '',
            'note': row['note'] or '',
            'nt_sequence': row['nt_sequence'] or '',
            'aa_sequence': row['aa_sequence'] or '',
        }
        for row in rows
        if (name := (row['name'] or '').strip()) and name in detected_feature_names
    ]
    cards.sort(key=lambda card: (card['name'].lower(), card['reference_name'].lower()))
    return cards
