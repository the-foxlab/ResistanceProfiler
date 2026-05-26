"""Interpretation algorithm validation, storage, and loading."""

from __future__ import annotations

import json
import sqlite3

_KNOWN_ALGORITHM_NAMES = {'ic50_thresholds', 'drug_groups', 'drug_interpretation'}


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

        if name in seen_names:
            raise ValueError(
                f'Duplicate algorithm name {name!r}: each algorithm may appear at most once.'
            )
        seen_names.add(name)

        if name == 'ic50_thresholds':
            _validate_ic50_thresholds(item)
        elif name == 'drug_groups':
            _validate_drug_groups(item)
        elif name == 'drug_interpretation':
            _validate_drug_interpretation(item)

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
    if method not in ('by_phenotype', 'by_score'):
        raise ValueError(
            f'drug_interpretation: "method" must be "by_phenotype" or "by_score", '
            f'got {method!r}.'
        )

    thresholds = config.get('thresholds')
    if not isinstance(thresholds, dict):
        raise ValueError('drug_interpretation: "thresholds" must be a dict.')

    if 'resistant' not in thresholds:
        raise ValueError('drug_interpretation: "thresholds" must include the "resistant" key.')

    for key, val in thresholds.items():
        if not isinstance(val, int) or val <= 0:
            raise ValueError(
                f'drug_interpretation: thresholds[{key!r}] must be a positive integer, '
                f'got {val!r}.'
            )
