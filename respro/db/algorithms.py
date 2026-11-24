"""SQLite persistence for project interpretation algorithms and drug-alias updates."""

from __future__ import annotations

import json
import sqlite3


def store_interpretation_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> None:
    """Replace all stored interpretation algorithm configs for *project_id*."""
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
    """Load all interpretation algorithm config dicts for *project_id* in insertion order."""
    rows = conn.execute(
        'SELECT config_json FROM interpretation_algorithm WHERE project_id = ? ORDER BY id',
        (project_id,),
    ).fetchall()
    return [json.loads(row['config_json']) for row in rows]


def apply_drug_alias_mappings(
    conn: sqlite3.Connection,
    project_id: int,
    config: dict,
) -> int:
    """Apply canonical drug-name alias mappings from a ``drug_alias`` config to ``drug.alias``."""
    updated = 0
    for canonical_name, alias in config['groups'].items():
        cur = conn.execute(
            'UPDATE drug SET alias = ? WHERE project_id = ? AND LOWER(name) = ?',
            (alias.strip(), project_id, canonical_name.strip().lower()),
        )
        updated += int(cur.rowcount or 0)
    return updated
