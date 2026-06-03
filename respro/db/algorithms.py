"""Interpretation algorithm validation, storage, and loading."""

from __future__ import annotations

import json
import sqlite3

from respro.core.annotation import HIGH_IMPACT_CONSEQUENCES
from respro.db._rules_normalize import _append_contradictory_comment, _parse_ic50_value

_ALLOWED_EFFECTS: frozenset[str] = HIGH_IMPACT_CONSEQUENCES

_KNOWN_ALGORITHM_NAMES = {
    'ic50_thresholds',
    'drug_groups',
    'drug_interpretation',
    'drug_alias',
    'effect_as_resistant',
}


def validate_interpretation_algorithms(algorithms: object) -> list[dict]:
    """
    Validate and return a list of interpretation algorithm configs.

    :param algorithms: raw value from the ``interpretation_algorithms`` metadata key
    :return: validated list of algorithm config dicts
    :raises ValueError: with a descriptive message on any invalid input
    """
    if not isinstance(algorithms, list):
        raise ValueError(
            'interpretation_algorithms must be a list, '
            f'got {type(algorithms).__name__}.'
        )

    seen_names: set[str] = set()
    seen_drug_interp_methods: set[str] = set()
    for i, item in enumerate(algorithms):
        if not isinstance(item, dict):
            raise ValueError(
                f'interpretation_algorithms[{i}] must be a dict, '
                f'got {type(item).__name__}.'
            )

        name = item.get('name')
        if name not in _KNOWN_ALGORITHM_NAMES:
            known = ', '.join(sorted(_KNOWN_ALGORITHM_NAMES))
            raise ValueError(
                f'Unknown algorithm name {name!r} at index {i}. '
                f'Known names: {known}.'
            )

        if name == 'drug_interpretation':
            # Multiple drug_interpretation entries are allowed if their methods differ.
            _validate_drug_interpretation(item)
            method = item.get('method', '')
            if method in seen_drug_interp_methods:
                raise ValueError(
                    f'Duplicate drug_interpretation method {method!r}: '
                    f'each method may appear at most once.'
                )
            seen_drug_interp_methods.add(method)
        else:
            if name in seen_names:
                raise ValueError(
                    f'Duplicate algorithm name {name!r}: each algorithm may appear at most once.'
                )
            seen_names.add(name)

            if name == 'ic50_thresholds':
                _validate_ic50_thresholds(item)
            elif name == 'drug_groups':
                _validate_drug_groups(item)
            elif name == 'drug_alias':
                _validate_drug_alias(item)
            elif name == 'effect_as_resistant':
                _validate_effect_as_resistant(item)

    return algorithms


def store_interpretation_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> None:
    """
    Persist validated interpretation algorithms to the project database.

    Replaces any existing algorithms for the given project.

    :param conn: project DB connection
    :param project_id: project id
    :param algorithms: list of validated algorithm config dicts
    """
    conn.execute(
        'DELETE FROM interpretation_algorithm WHERE project_id = ?',
        (project_id,),
    )
    for config in algorithms:
        conn.execute(
            'INSERT INTO interpretation_algorithm (project_id, algorithm_name, config_json) '
            'VALUES (?, ?, ?)',
            (project_id, config['name'], json.dumps(config)),
        )


def load_interpretation_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
) -> list[dict]:
    """
    Load interpretation algorithms from the project database.

    :param conn: project DB connection
    :param project_id: project id
    :return: list of algorithm config dicts; empty list if none are configured
    """
    rows = conn.execute(
        'SELECT config_json FROM interpretation_algorithm WHERE project_id = ? ORDER BY id',
        (project_id,),
    ).fetchall()
    return [json.loads(row['config_json']) for row in rows]


def apply_ic50_threshold_classification(
    conn: sqlite3.Connection,
    project_id: int,
    config: dict,
) -> int:
    """
    Classify rule phenotypes based on IC50 or fold-IC50 thresholds.

    For each resistance rule whose drug has a configured threshold and whose IC50 or
    fold-IC50 value is non-empty, updates the phenotype to ``resistant``,
    ``intermediate``, or ``sensitive`` according to the configured breakpoints.

    :param conn: project DB connection
    :param project_id: project id
    :param config: validated ic50_thresholds algorithm config dict
    :return: number of rules updated
    """
    use_column = config['use']
    thresholds = config['thresholds']
    q = (
        f'SELECT r.id, d.name AS drug_name, r.{use_column} AS value, r.phenotype, r.comment '
        'FROM resistance_rule r '
        'JOIN drug d ON d.id = r.drug_id '
        'JOIN feature f ON f.id = r.feature_id '
        'JOIN reference ref ON ref.id = f.reference_id '
        f"WHERE ref.project_id = ? AND r.{use_column} IS NOT NULL AND r.{use_column} != ''"
    )
    rows = conn.execute(q, (project_id,)).fetchall()

    updated = 0
    for row in rows:
        drug_name = row['drug_name']
        if drug_name not in thresholds:
            continue
        parsed = _parse_ic50_value(row['value'])
        if parsed is None:
            continue
        new_phenotype = _classify_ic50(parsed, thresholds[drug_name])
        existing_phenotype = (row['phenotype'] or '').strip().lower()
        # If an existing non-trivial phenotype conflicts with the IC50-derived call,
        # flag as contradictory and append the standard comment rather than silently
        # overwriting the stored association.
        if existing_phenotype and existing_phenotype not in ('unknown', 'contradictory') and existing_phenotype != new_phenotype:
            new_phenotype = 'contradictory'
        updated_comment = _append_contradictory_comment(
            row['comment'] or '',
            phenotype=new_phenotype,
            clinical_phenotype='',
        )
        conn.execute(
            'UPDATE resistance_rule SET phenotype = ?, comment = ? WHERE id = ?',
            (new_phenotype, updated_comment, int(row['id'])),
        )
        updated += 1
    return updated


def apply_drug_alias_mappings(
    conn: sqlite3.Connection,
    project_id: int,
    config: dict,
) -> int:
    """
    Apply canonical drug-name alias mappings to ``drug.alias`` for one project.

    For each canonical name in ``config['groups']``, updates matching
    ``drug.name`` rows in the current project. Missing drugs are skipped.

    :param conn: project DB connection
    :param project_id: project id
    :param config: validated drug_alias algorithm config dict
    :return: number of drug rows updated
    """
    groups = config['groups']
    updated = 0
    for canonical_name, alias in groups.items():
        cur = conn.execute(
            'UPDATE drug SET alias = ? WHERE project_id = ? AND LOWER(name) = ?',
            (alias.strip(), project_id, canonical_name.strip().lower()),
        )
        updated += int(cur.rowcount or 0)
    return updated


def _classify_ic50(value: float, drug_thresholds: dict) -> str:
    """Return the canonical phenotype for a numeric IC50 value against breakpoints."""
    if value >= drug_thresholds['resistant']:
        return 'resistant'
    if value >= drug_thresholds['intermediate']:
        return 'intermediate'
    return 'sensitive'


def _validate_ic50_thresholds(config: dict) -> None:
    use = config.get('use')
    if use not in ('ic50', 'fold_ic50'):
        raise ValueError(
            f'ic50_thresholds: "use" must be "ic50" or "fold_ic50", got {use!r}.'
        )

    thresholds = config.get('thresholds')
    if not isinstance(thresholds, dict) or not thresholds:
        raise ValueError('ic50_thresholds: "thresholds" must be a non-empty dict.')

    for drug, limits in thresholds.items():
        if not isinstance(limits, dict):
            raise ValueError(
                f'ic50_thresholds: thresholds[{drug!r}] must be a dict, '
                f'got {type(limits).__name__}.'
            )
        for key in ('intermediate', 'resistant'):
            val = limits.get(key)
            if val is None:
                raise ValueError(
                    f'ic50_thresholds: thresholds[{drug!r}] is missing required key {key!r}.'
                )
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(
                    f'ic50_thresholds: thresholds[{drug!r}][{key!r}] must be a positive number, '
                    f'got {val!r}.'
                )
        if limits['resistant'] <= limits['intermediate']:
            raise ValueError(
                f'ic50_thresholds: thresholds[{drug!r}] "resistant" ({limits["resistant"]}) '
                f'must be strictly greater than "intermediate" ({limits["intermediate"]}).'
            )


def _validate_drug_groups(config: dict) -> None:
    groups = config.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('drug_groups: "groups" must be a non-empty dict.')

    seen_drugs: dict[str, str] = {}
    for group_name, members in groups.items():
        if not isinstance(members, list) or not members:
            raise ValueError(
                f'drug_groups: groups[{group_name!r}] must be a non-empty list of strings.'
            )
        for drug in members:
            if not isinstance(drug, str):
                raise ValueError(
                    f'drug_groups: groups[{group_name!r}] must contain strings only, '
                    f'got {type(drug).__name__}.'
                )
            if drug in seen_drugs:
                raise ValueError(
                    f'drug_groups: drug {drug!r} appears in both '
                    f'{seen_drugs[drug]!r} and {group_name!r}.'
                )
            seen_drugs[drug] = group_name


def _validate_drug_interpretation(config: dict) -> None:
    method = config.get('method')
    if method not in ('by_phenotype', 'by_score', 'by_ic50', 'by_fold_ic50'):
        raise ValueError(
            'drug_interpretation: "method" must be "by_phenotype", "by_score", '
            f'"by_ic50", or "by_fold_ic50", '
            f'got {method!r}.'
        )

    thresholds = config.get('thresholds')
    if not isinstance(thresholds, dict):
        raise ValueError('drug_interpretation: "thresholds" must be a dict.')

    if 'resistant' not in thresholds:
        raise ValueError('drug_interpretation: "thresholds" must include the "resistant" key.')

    numeric_methods = {'by_ic50', 'by_fold_ic50'}
    if method in numeric_methods:
        for key, val in thresholds.items():
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(
                    f'drug_interpretation: thresholds[{key!r}] must be a positive number, '
                    f'got {val!r}.'
                )
        intermediate = thresholds.get('intermediate')
        resistant = thresholds.get('resistant')
        if intermediate is not None and resistant <= intermediate:
            raise ValueError(
                'drug_interpretation: "resistant" threshold must be strictly greater than '
                '"intermediate" for numeric methods.'
            )
        return

    for key, val in thresholds.items():
        if not isinstance(val, int) or val <= 0:
            raise ValueError(
                f'drug_interpretation: thresholds[{key!r}] must be a positive integer, '
                f'got {val!r}.'
            )


def _validate_drug_alias(config: dict) -> None:
    groups = config.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('drug_alias: "groups" must be a non-empty dict.')

    seen_aliases: set[str] = set()
    for canonical_name, alias in groups.items():
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ValueError(
                'drug_alias: each canonical drug name key must be a non-empty string.'
            )
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError(
                f'drug_alias: alias for {canonical_name!r} must be a non-empty string.'
            )
        normalized_alias = alias.strip()
        if normalized_alias in seen_aliases:
            raise ValueError(
                f'drug_alias: alias value {normalized_alias!r} is duplicated across canonical names.'
            )
        seen_aliases.add(normalized_alias)


def _validate_effect_as_resistant(config: dict) -> None:
    rules = config.get('rules')
    if not isinstance(rules, list) or not rules:
        raise ValueError('effect_as_resistant: "rules" must be a non-empty list.')

    seen_keys: set[tuple[str, str, str]] = set()
    required_keys = ('feature', 'effect', 'reference', 'drug')
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(
                f'effect_as_resistant: rules[{i}] must be a dict, '
                f'got {type(rule).__name__}.'
            )

        for key in required_keys:
            if key not in rule:
                raise ValueError(
                    f'effect_as_resistant: rules[{i}] is missing required key {key!r}.'
                )

        # Validate and strip feature, reference, drug
        for key in ('feature', 'reference', 'drug'):
            val = rule.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f'effect_as_resistant: rules[{i}][{key!r}] must be a non-empty string.'
                )
            rule[key] = val.strip()

        # Validate effect list
        effect = rule.get('effect')
        if not isinstance(effect, list) or not effect:
            raise ValueError(
                f'effect_as_resistant: rules[{i}][\'effect\'] must be a non-empty list of strings.'
            )
        stripped_effects: list[str] = []
        for j, eff in enumerate(effect):
            if not isinstance(eff, str) or not eff.strip():
                raise ValueError(
                    f"effect_as_resistant: rules[{i}]['effect'][{j}] must be a non-empty string."
                )
            stripped = eff.strip()
            if stripped not in _ALLOWED_EFFECTS:
                allowed = ', '.join(sorted(_ALLOWED_EFFECTS))
                raise ValueError(
                    f"effect_as_resistant: rules[{i}]['effect'][{j}] has invalid value "
                    f'{stripped!r}. Allowed values: {allowed}.'
                )
            stripped_effects.append(stripped)
        rule['effect'] = stripped_effects

        triplet = (rule['feature'], rule['reference'], rule['drug'])
        if triplet in seen_keys:
            raise ValueError(
                'effect_as_resistant: duplicate rule tuple '
                f'(feature={triplet[0]!r}, reference={triplet[1]!r}, drug={triplet[2]!r}).'
            )
        seen_keys.add(triplet)


_METHOD_LABEL: dict[str, str] = {
    'by_phenotype': 'Phenotype',
    'by_score': 'Score',
    'by_ic50': 'IC50',
    'by_fold_ic50': 'Fold IC50',
}

_ASSESSMENT_RANK: dict[str, int] = {
    'resistant': 0,
    'contradictory': 1,
    'intermediate': 2,
    'sensitive': 3,
}


def compute_drug_assessment(
    drug_data: dict,
    configs: list[dict],
) -> tuple[str, list[dict]]:
    """
    Compute per-method assessments and a final merged assessment for one drug.

    :param drug_data: dict with keys ``resistant_count``, ``intermediate_count``,
        ``sensitive_count``, ``contradictory_count``, ``score_total``,
        ``ic50_values``, ``fold_ic50_values``, ``hit_count``
    :param configs: list of validated ``drug_interpretation`` config dicts
    :return: ``(final_assessment, method_assessments)`` where
        ``final_assessment`` is the strongest-wins result (empty string if no
        method produces an assessment) and ``method_assessments`` is a list of
        ``{'method': ..., 'label': ..., 'assessment': ...}`` dicts (only for
        methods that produced a non-empty assessment)
    """
    method_assessments: list[dict] = []

    for config in configs:
        method = config.get('method', '')
        thresholds = config.get('thresholds', {})
        resistant_threshold = thresholds.get('resistant', 1)
        intermediate_threshold = thresholds.get('intermediate')

        assessment = _compute_single_method(
            method, drug_data, resistant_threshold, intermediate_threshold,
        )
        if assessment:
            method_assessments.append({
                'method': method,
                'label': _METHOD_LABEL.get(method, method),
                'assessment': assessment,
            })

    if not method_assessments:
        return '', []

    best = min(method_assessments, key=lambda m: _ASSESSMENT_RANK.get(m['assessment'], 99))
    return best['assessment'], method_assessments


def _compute_single_method(
    method: str,
    drug_data: dict,
    resistant_threshold,
    intermediate_threshold,
) -> str:
    """Compute assessment for a single method. Returns empty string if no data."""
    if method == 'by_phenotype':
        return _assess_by_phenotype(drug_data, resistant_threshold, intermediate_threshold)
    if method == 'by_score':
        return _assess_by_score(drug_data, resistant_threshold, intermediate_threshold)
    if method == 'by_ic50':
        return _assess_by_ic50(drug_data, resistant_threshold, intermediate_threshold)
    if method == 'by_fold_ic50':
        return _assess_by_fold_ic50(drug_data, resistant_threshold, intermediate_threshold)
    return ''


def _assess_by_phenotype(drug_data: dict, resistant_threshold, intermediate_threshold) -> str:
    if drug_data['resistant_count'] >= resistant_threshold:
        return 'resistant'
    if intermediate_threshold is not None and drug_data['intermediate_count'] >= intermediate_threshold:
        return 'intermediate'
    if drug_data['contradictory_count'] > 0:
        return 'contradictory'
    if drug_data['hit_count'] > 0:
        return 'sensitive'
    return ''


def _assess_by_score(drug_data: dict, resistant_threshold, intermediate_threshold) -> str:
    total = drug_data['score_total']
    if total >= resistant_threshold:
        return 'resistant'
    if intermediate_threshold is not None and total >= intermediate_threshold:
        return 'intermediate'
    if drug_data['hit_count'] > 0:
        return 'sensitive'
    return ''


def _assess_by_ic50(drug_data: dict, resistant_threshold, intermediate_threshold) -> str:
    ic50_values = drug_data['ic50_values']
    if not ic50_values:
        return ''
    if any(value >= resistant_threshold for value in ic50_values):
        return 'resistant'
    if intermediate_threshold is not None and any(value >= intermediate_threshold for value in ic50_values):
        return 'intermediate'
    return 'sensitive'


def _assess_by_fold_ic50(drug_data: dict, resistant_threshold, intermediate_threshold) -> str:
    fold_ic50_values = drug_data['fold_ic50_values']
    if not fold_ic50_values:
        return ''
    if any(value >= resistant_threshold for value in fold_ic50_values):
        return 'resistant'
    if intermediate_threshold is not None and any(value >= intermediate_threshold for value in fold_ic50_values):
        return 'intermediate'
    return 'sensitive'
