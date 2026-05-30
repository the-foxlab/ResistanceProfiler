"""Read-only database accessors used by HTML report generation."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import statistics

logger = logging.getLogger(__name__)


# Matches an optional qualifier (>, <, >=, <=, ~) followed by a leading number.
_RE_LEADING_NUM = re.compile(r'^[><=~≥≤≈\s]*(-?\d+(?:\.\d+)?)')


def load_numeric_metric_thresholds(
    project_conn: sqlite3.Connection | None,
) -> dict[str, tuple[float, float] | None]:
    """
    Compute mean and standard deviation for numeric metric fields across DB rules.

    Values are collected from both single rules and formula rules. A field returns
    ``None`` when fewer than two parseable numeric values are available.

    :param project_conn: open project DB connection
    :return: mapping of metric name to (mean, std) tuple or None
    """
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
        for field in ('ic50', 'fold_ic50', 'score'):
            parsed = _parse_numeric_value(row[field] or '')
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


def load_drug_class_map(
    project_conn: sqlite3.Connection | None,
) -> dict[str, str]:
    """
    Build a lowercase-drug-name to class-name mapping from ``drug_groups`` config.

    :param project_conn: open project DB connection
    :return: map of normalized drug names to class/group names
    """
    if project_conn is None:
        return {}
    try:
        row = project_conn.execute(
            "SELECT config_json FROM interpretation_algorithm "
            "WHERE algorithm_name = 'drug_groups' LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug_groups algorithm from DB: %s', exc)
        return {}
    if row is None:
        return {}
    config = json.loads(row['config_json'])
    drug_map: dict[str, str] = {}
    for group_name, members in config.get('groups', {}).items():
        for drug in members:
            drug_map[drug.strip().lower()] = group_name
    return drug_map


def load_drug_alias_map(
    project_conn: sqlite3.Connection | None,
) -> dict[str, str]:
    """
    Build a lowercase canonical-drug-name to alias mapping from the drug table.

    :param project_conn: open project DB connection
    :return: map of normalized canonical names to aliases
    """
    if project_conn is None:
        return {}
    try:
        rows = project_conn.execute('SELECT name, alias FROM drug').fetchall()
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
    """
    Return whether the project DB has at least one interpretation algorithm.

    :param project_conn: optional project DB connection
    :return: True when any interpretation algorithm exists
    """
    if project_conn is None:
        return False
    try:
        row = project_conn.execute('SELECT 1 FROM interpretation_algorithm LIMIT 1').fetchone()
        return row is not None
    except Exception:
        return False


def load_drug_cards(
    project_conn: sqlite3.Connection | None,
    detected_drug_names: set[str] | None = None,
) -> list[dict]:
    """Load DB-backed drug metadata for drugs detected in the current run."""
    if project_conn is None or not detected_drug_names:
        return []

    try:
        rows = project_conn.execute(
            'SELECT name, pubchem_url, description, structure_url '
            'FROM drug ORDER BY name'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug cards from project DB: %s', exc)
        return []

    cards: list[dict] = []
    for row in rows:
        name = (row['name'] or '').strip()
        if not name:
            continue
        if name.lower() not in detected_drug_names:
            continue
        cards.append({
            'name': name,
            'pubchem_url': row['pubchem_url'] or '',
            'description': row['description'] or '',
            'structure_url': row['structure_url'] or '',
        })

    cards.sort(key=lambda card: (card.get('name') or '').lower())
    return cards


def load_feature_cards(
    project_conn: sqlite3.Connection | None,
    reference_name: str,
    detected_feature_names: set[str] | None = None,
) -> list[dict]:
    """
    Load feature metadata for detected features in the active reference.

    :param project_conn: optional project DB connection
    :param reference_name: active reference name from profiling result
    :param detected_feature_names: features observed in this profiling run
    :return: list of feature cards
    """
    if project_conn is None or not detected_feature_names:
        return []

    try:
        rows = project_conn.execute(
            'SELECT g.name, g.protein, g.protein_id, g.ncbi_protein_url, g.locus_tag, g.note, '
            'g.nt_sequence, g.aa_sequence FROM feature g '
            'JOIN reference r ON r.id = g.reference_id '
            'WHERE r.name = ? ORDER BY g.start',
            (reference_name,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load feature cards from project DB for %r: %s', reference_name, exc)
        return []

    cards: list[dict] = []
    for row in rows:
        name = (row['name'] or '').strip()
        if not name or name not in detected_feature_names:
            continue
        cards.append({
            'name': name,
            'protein': row['protein'] or '',
            'protein_id': row['protein_id'] or '',
            'ncbi_protein_url': row['ncbi_protein_url'] or '',
            'locus_tag': row['locus_tag'] or '',
            'note': row['note'] or '',
            'nt_sequence': row['nt_sequence'] or '',
            'aa_sequence': row['aa_sequence'] or '',
        })

    cards.sort(key=lambda card: (card.get('name') or '').lower())
    return cards


def _parse_numeric_value(value_str: str) -> float | None:
    """Extract a leading numeric value from qualified metric strings."""
    match = _RE_LEADING_NUM.match(value_str.strip())
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None
