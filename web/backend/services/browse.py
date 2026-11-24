"""Read-only browse helpers for project databases and resistance rules."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from respro.db.algorithms import load_interpretation_algorithms
from respro.db.rules_queries import (
    get_project_summary_for_display,
    list_formula_rules_for_display,
    list_plot_metadata_for_display,
    list_references_for_display,
    list_rules_for_display,
)
from respro.db.schema import open_project_db
from web.backend.startup_config import list_project_db_paths, resolve_project_db_path

logger = logging.getLogger(__name__)

_METADATA_FIELDS = (
    'maintainers',
    'contact',
    'publication_pmid',
    'publication_doi',
    'website',
    'description',
    'maintainer_update',
    'license',
    'tsv_checksum',
)


def list_databases(project_databases_dir: Path) -> dict:
    """Return metadata for all valid project databases in *project_databases_dir*."""
    items: list[dict] = []
    for project_db in list_project_db_paths(project_databases_dir):
        with contextlib.closing(open_project_db(project_db)) as conn:
            try:
                project_row = get_project_summary_for_display(conn)
            except ValueError as exc:
                logger.warning('Skipping invalid project database %s: %s', project_db, exc)
                continue
            except Exception as exc:
                logger.exception('Unexpected error while reading project database %s: %s', project_db, exc)
                continue

            organisms = [
                row['organism']
                for row in conn.execute(
                    "SELECT DISTINCT organism FROM reference "
                    "WHERE organism IS NOT NULL AND organism != '' ORDER BY organism"
                ).fetchall()
            ]
            count_row = conn.execute('SELECT COUNT(*) AS count FROM resistance_rule').fetchone()
            algorithms = _extract_display_algorithms(load_interpretation_algorithms(conn, project_row['id']))
            items.append({
                'id': project_db.name,
                'display_name': project_row['name'],
                'uuid': project_row['uuid'],
                'created_at': project_row['created_at'],
                'schema_version': project_row['schema_version'],
                'supported_organisms': organisms,
                'mutation_count': int(count_row['count']) if count_row else 0,
                'has_example': bool(project_row.get('example_fasta')),
                'metadata': {k: project_row.get(f'metadata_{k}', '') for k in _METADATA_FIELDS},
                'algorithms': algorithms,
            })

    return {'items': items, 'count': len(items)}


def _extract_display_algorithms(algorithms: list[dict]) -> dict:
    """Return only ``effect_as_resistant`` and ``drug_interpretation`` configs for display."""
    result: dict = {}
    drug_interp_list: list[dict] = []
    for config in algorithms:
        name = config.get('name')
        if name == 'effect_as_resistant':
            result['effect_as_resistant'] = {'rules': config.get('rules', [])}
        elif name == 'drug_interpretation':
            entry: dict = {'method': config.get('method', ''), 'thresholds': config.get('thresholds', {})}
            if config.get('drug_thresholds'):
                entry['drug_thresholds'] = config['drug_thresholds']
            drug_interp_list.append(entry)
    if drug_interp_list:
        result['drug_interpretation'] = drug_interp_list
    return result


def list_rules(
    project_databases_dir: Path,
    database_id: str | None,
    reference_filter: str | None = None,
) -> dict:
    """Return single/formula rule rows and plot metadata with optional reference filtering."""
    project_db = resolve_project_db_path(project_databases_dir, database_id)
    with contextlib.closing(open_project_db(project_db)) as conn:
        ref_id = _resolve_reference_id(conn, _normalize_reference_filter(reference_filter))
        rows = list_rules_for_display(conn, ref_id=ref_id)
        formula_rows = list_formula_rules_for_display(conn, ref_id=ref_id)
        return {
            'items': rows,
            'count': len(rows),
            'single_count': len(rows),
            'columns': list(rows[0].keys()) if rows else [],
            'formula_items': formula_rows,
            'formula_count': len(formula_rows),
            'formula_columns': list(formula_rows[0].keys()) if formula_rows else [],
            'plot_meta': list_plot_metadata_for_display(conn, ref_id=ref_id),
        }


def _resolve_reference_id(conn, reference_filter: str | None) -> int | None:
    if not reference_filter:
        return None
    matches = [r for r in list_references_for_display(conn) if reference_filter.lower() in r['name'].lower()]
    if not matches:
        raise ValueError(f'No reference matching {reference_filter!r} found.')
    if len(matches) > 1:
        names = ', '.join(r['name'] for r in matches)
        raise ValueError(f'Ambiguous reference filter {reference_filter!r}: {names}')
    return int(matches[0]['id'])


def _normalize_reference_filter(reference_filter: str | None) -> str | None:
    """Treat browser-sent placeholder strings as missing filters."""
    if reference_filter is None:
        return None
    cleaned = reference_filter.strip()
    return None if cleaned.lower() in {'', 'undefined', 'null'} else cleaned
