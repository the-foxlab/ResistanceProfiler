"""Read-only browse helpers for rules and runs."""

from __future__ import annotations

from pathlib import Path

from respro.db.rules_queries import (
    get_project_summary_for_display,
    list_formula_rules_for_display,
    list_plot_metadata_for_display,
    list_references_for_display,
    list_rules_for_display,
)
from respro.db.schema import open_project_db
from web.backend.startup_config import list_project_db_paths, resolve_project_db_path


def list_databases(project_databases_dir: Path) -> dict:
    """Return metadata for all project databases available in project_databases_dir."""
    items: list[dict] = []
    for project_db in list_project_db_paths(project_databases_dir):
        project_conn = open_project_db(project_db)
        try:
            try:
                project_row = get_project_summary_for_display(project_conn)
            except (ValueError, Exception):
                continue

            metadata = {
                'maintainers': project_row.get('metadata_maintainers', ''),
                'contact': project_row.get('metadata_contact', ''),
                'publication_pmid': project_row.get('metadata_publication_pmid', ''),
                'publication_doi': project_row.get('metadata_publication_doi', ''),
                'website': project_row.get('metadata_website', ''),
                'description': project_row.get('metadata_description', ''),
                'maintainer_update': project_row.get('metadata_maintainer_update', ''),
                'license': project_row.get('metadata_license', ''),
                'tsv_checksum': project_row.get('metadata_tsv_checksum', ''),
            }

            organisms = [
                row['organism']
                for row in project_conn.execute(
                    "SELECT DISTINCT organism FROM reference WHERE organism IS NOT NULL AND organism != '' ORDER BY organism"
                ).fetchall()
            ]
            mutation_count_row = project_conn.execute('SELECT COUNT(*) AS count FROM resistance_rule').fetchone()
            mutation_count = int(mutation_count_row['count']) if mutation_count_row else 0

            items.append(
                {
                    'id': project_db.name,
                    'display_name': project_row['name'],
                    'uuid': project_row['uuid'],
                    'created_at': project_row['created_at'],
                    'schema_version': project_row['schema_version'],
                    'supported_organisms': organisms,
                    'mutation_count': mutation_count,
                    'metadata': metadata,
                }
            )
        finally:
            project_conn.close()

    return {'items': items, 'count': len(items)}


def list_rules(
    project_databases_dir: Path,
    database_id: str | None,
    reference_filter: str | None = None,
) -> dict:
    """Return rule rows with optional reference-name filtering."""
    project_db = resolve_project_db_path(project_databases_dir, database_id)
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
        columns = list(rows[0].keys()) if rows else []
        formula_rows = list_formula_rules_for_display(project_conn, ref_id=ref_id)
        formula_columns = list(formula_rows[0].keys()) if formula_rows else []
        plot_meta = list_plot_metadata_for_display(project_conn, ref_id=ref_id)
        return {
            'items': rows,
            'count': len(rows),
            'single_count': len(rows),
            'columns': columns,
            'formula_items': formula_rows,
            'formula_count': len(formula_rows),
            'formula_columns': formula_columns,
            'plot_meta': plot_meta,
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

