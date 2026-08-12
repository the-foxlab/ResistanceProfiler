"""Read-only profiling query helpers shared by CLI orchestration."""

from __future__ import annotations

import sqlite3


def load_existing_run_project_fingerprint(results_conn: sqlite3.Connection) -> str | None:
    """
    Return one non-empty project fingerprint from the results database.

    :param results_conn: open results DB connection
    :return: project fingerprint, or None when no runs exist
    """
    row = results_conn.execute(
        "SELECT project_fingerprint FROM run WHERE project_fingerprint != '' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return str(row['project_fingerprint'])


def load_reference_name(project_conn: sqlite3.Connection, reference_id: int) -> str | None:
    """
    Return the reference name for a reference id.

    :param project_conn: open project DB connection
    :param reference_id: internal reference id
    :return: reference name, or None when id does not exist
    """
    row = project_conn.execute(
        'SELECT name FROM reference WHERE id = ?',
        (reference_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row['name'])


def load_reference_metadata(project_conn: sqlite3.Connection, reference_id: int) -> tuple[str, int]:
    """
    Return organism and nucleotide length for a reference id.

    :param project_conn: open project DB connection
    :param reference_id: internal reference id
    :return: tuple of (organism, reference_length_nt)
    """
    row = project_conn.execute(
        'SELECT organism, length FROM reference WHERE id = ?',
        (reference_id,),
    ).fetchone()
    if row is None:
        return '', 0
    organism = row['organism'] or ''
    reference_length_nt = int(row['length'] or 0)
    return organism, reference_length_nt
