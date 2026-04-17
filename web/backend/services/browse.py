"""Read-only browse helpers for rules and runs."""

from __future__ import annotations

from pathlib import Path

from respro.db.rules_queries import list_references_for_display, list_rules_for_display
from respro.db.schema import open_project_db


def list_databases(project_db: Path) -> dict:
    """Return available database metadata for the web UI catalog dropdown."""
    project_conn = open_project_db(project_db)
    try:
        project_row = project_conn.execute(
            'SELECT id, name, created_at, schema_version FROM project ORDER BY id LIMIT 1'
        ).fetchone()
        if project_row is None:
            raise ValueError('Project DB contains no project metadata.')

        organisms = [
            row['organism']
            for row in project_conn.execute(
                "SELECT DISTINCT organism FROM reference WHERE organism IS NOT NULL AND organism != '' ORDER BY organism"
            ).fetchall()
        ]
        mutation_count_row = project_conn.execute('SELECT COUNT(*) AS count FROM resistance_rule').fetchone()
        mutation_count = int(mutation_count_row['count']) if mutation_count_row else 0

        item = {
            'id': str(project_row['id']),
            'display_name': project_row['name'],
            'created_at': project_row['created_at'],
            'schema_version': project_row['schema_version'],
            'supported_organisms': organisms,
            'mutation_count': mutation_count,
        }
        return {'items': [item], 'count': 1}
    finally:
        project_conn.close()


def list_rules(project_db: Path, reference_filter: str | None = None) -> dict:
    """Return rule rows with optional reference-name filtering."""
    project_conn = open_project_db(project_db)
    try:
        normalized_reference_filter = _normalize_reference_filter(reference_filter)
        ref_id: int | None = None
        if normalized_reference_filter:
            refs = list_references_for_display(project_conn)
            matches = [
                r for r in refs
                if normalized_reference_filter.lower() in r['name'].lower()
            ]
            if not matches:
                raise ValueError(f'No reference matching {normalized_reference_filter!r} found.')
            if len(matches) > 1:
                names = ', '.join(r['name'] for r in matches)
                raise ValueError(
                    f'Ambiguous reference filter {normalized_reference_filter!r}: {names}'
                )
            ref_id = int(matches[0]['id'])

        rows = list_rules_for_display(project_conn, ref_id=ref_id)
        return {
            'items': rows,
            'count': len(rows),
        }
    finally:
        project_conn.close()


def _normalize_reference_filter(reference_filter: str | None) -> str | None:
    """Treat browser-sent placeholder strings as missing filters."""
    if reference_filter is None:
        return None

    cleaned = reference_filter.strip()
    if cleaned.lower() in {'', 'undefined', 'null'}:
        return None
    return cleaned

