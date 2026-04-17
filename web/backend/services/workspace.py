"""Workspace validation and metadata helpers for the web backend."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from respro.db.results import project_fingerprint
from respro.db.schema import init_results_db, open_project_db


def open_workspace(project_db: Path, results_db: Path, output_dir: Path) -> dict:
    """Validate workspace paths and return basic workspace metadata."""
    output_dir.mkdir(parents=True, exist_ok=True)
    project_conn = open_project_db(project_db)
    results_conn = init_results_db(results_db)
    try:
        _validate_results_fingerprint(project_conn, results_conn)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise ValueError('No project found in the project database.')

        reference_count = project_conn.execute('SELECT COUNT(*) AS c FROM reference').fetchone()['c']
        rule_count = project_conn.execute('SELECT COUNT(*) AS c FROM resistance_rule').fetchone()['c']

        return {
            'project_name': project_row['name'],
            'project_db': str(project_db.resolve()),
            'results_db': str(results_db.resolve()),
            'output_dir': str(output_dir.resolve()),
            'reference_count': int(reference_count),
            'rule_count': int(rule_count),
        }
    finally:
        project_conn.close()
        results_conn.close()


def _validate_results_fingerprint(
    project_conn: sqlite3.Connection,
    results_conn: sqlite3.Connection,
) -> None:
    """Ensure results DB belongs to the same project when it already contains runs."""
    current_fp = project_fingerprint(project_conn)
    existing_run = results_conn.execute(
        "SELECT project_fingerprint FROM run WHERE project_fingerprint != '' LIMIT 1"
    ).fetchone()
    if existing_run and existing_run['project_fingerprint'] != current_fp:
        raise ValueError(
            'Project fingerprint mismatch: workspace project DB does not match results DB runs.'
        )
