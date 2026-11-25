"""Read-only browse helpers for project databases and resistance rules."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path

from respro.db.algorithms import load_interpretation_algorithms
from respro.db.models import is_internal_formula_component_drug_name
from respro.db.rules_queries import (
    get_project_summary_for_display,
    list_formula_rules_for_display,
    list_plot_metadata_for_display,
    list_references_for_display,
    list_rules_for_display,
)
from web.backend.project_dbs import iter_valid_project_dbs, open_resolved_project_db_readonly

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
    for project_db, conn in iter_valid_project_dbs(project_databases_dir):
        try:
            project_row = get_project_summary_for_display(conn)
            organisms = [
                row['organism']
                for row in conn.execute(
                    "SELECT DISTINCT organism FROM reference "
                    "WHERE organism IS NOT NULL AND organism != '' ORDER BY organism"
                ).fetchall()
            ]
            drug_counts = conn.execute(
                'SELECT d.name AS drug_name, COUNT(*) AS count '
                'FROM resistance_rule rr '
                'JOIN drug d ON d.id = rr.drug_id '
                'GROUP BY d.id, d.name'
            ).fetchall()
            mutation_count = sum(
                int(row['count'])
                for row in drug_counts
                if not is_internal_formula_component_drug_name(row['drug_name'] or '')
            )
            algorithms = _extract_display_algorithms(load_interpretation_algorithms(conn, project_row['id']))
        except (ValueError, sqlite3.Error) as exc:
            logger.warning('Skipping invalid project database %s: %s', project_db.name, exc)
            continue
        except Exception as exc:
            logger.exception('Unexpected error while reading project database %s: %s', project_db.name, exc)
            continue

        items.append({
            'id': project_db.name,
            'display_name': project_row['name'],
            'uuid': project_row['uuid'],
            'created_at': project_row['created_at'],
            'schema_version': project_row['schema_version'],
            'supported_organisms': organisms,
            'mutation_count': mutation_count,
            'has_example': bool(project_row.get('example_fasta')),
            'metadata': {k: project_row.get(f'metadata_{k}', '') for k in _METADATA_FIELDS},
            'algorithms': algorithms,
        })

    if not items and project_databases_dir.is_dir() and any(p.is_file() for p in project_databases_dir.glob('*.db')):
        raise ValueError('No valid project database available.')

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
    db_path, conn = open_resolved_project_db_readonly(project_databases_dir, database_id)
    with contextlib.closing(conn):
        try:
            ref_id = _resolve_reference_id(conn, _normalize_reference_filter(reference_filter))
            rows = list_rules_for_display(conn, ref_id=ref_id)
            formula_rows = list_formula_rules_for_display(conn, ref_id=ref_id)
            plot_meta = list_plot_metadata_for_display(conn, ref_id=ref_id)
        except sqlite3.Error as exc:
            logger.warning('Failed to read rules from project database %s: %s', db_path.name, exc)
            if database_id:
                raise ValueError(f'Project database {database_id!r} is invalid or corrupt.') from exc
            raise ValueError('No valid project database available.') from exc
        return {
            'items': rows,
            'count': len(rows),
            'single_count': len(rows),
            'columns': list(rows[0].keys()) if rows else [],
            'formula_items': formula_rows,
            'formula_count': len(formula_rows),
            'formula_columns': list(formula_rows[0].keys()) if formula_rows else [],
            'plot_meta': plot_meta,
        }


def _resolve_reference_id(conn: sqlite3.Connection, reference_filter: str | None) -> int | None:
    if not reference_filter:
        return None
    refs = list_references_for_display(conn)
    needle = reference_filter.lower()
    exact_matches = [r for r in refs if r['name'].lower() == needle]
    if len(exact_matches) == 1:
        return int(exact_matches[0]['id'])
    if len(exact_matches) > 1:
        names = ', '.join(r['name'] for r in exact_matches)
        raise ValueError(f'Ambiguous reference filter {reference_filter!r}: {names}')

    matches = [r for r in refs if needle in r['name'].lower()]
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
