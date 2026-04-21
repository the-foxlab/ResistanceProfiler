"""
`respro explore` command — browse resistance rules or stored profiling runs.
"""

from __future__ import annotations  # noqa: I001

from pathlib import Path
from typing import Annotated

import click
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from respro.db.results import list_runs
from respro.db.rules_queries import (
    get_project_summary_for_display,
    list_references_for_display,
    list_rules_for_display,
)
from respro.db.schema import open_project_db, open_results_db


RULE_COLUMN_LABELS = {
    'reference_name': 'Reference',
    'gene': 'Gene',
    'position': 'Pos',
    'reference': 'Reference AA',
    'mutation': 'Mutation',
    'drug': 'Drug',
    'phenotype': 'Phenotype',
    'clinical_phenotype': 'Clinical phenotype',
    'ic50': 'IC50',
    'fold_ic50': 'Fold IC50',
    'publication': 'DOI',
    'source': 'Source',
    'comment': 'Comment',
}

PROJECT_INFO_LABELS = {
    'name': 'Name',
    'uuid': 'UUID',
    'created_at': 'Created at',
    'schema_version': 'Schema version',
    'metadata_maintainers': 'Maintainers',
    'metadata_contact': 'Contact',
    'metadata_publication_pmid': 'Publication PMID',
    'metadata_publication_doi': 'Publication DOI',
    'metadata_website': 'Website',
    'metadata_description': 'Description',
    'metadata_maintainer_update': 'Maintainer update',
    'metadata_license': 'License',
    'metadata_tsv_checksum': 'TSV checksum',
}


def explore(
    rules: Annotated[
        Path | None,
        typer.Option(
            '--rules',
            '-r',
            exists=True,
            help='Project database to explore resistance rules.',
        ),
    ] = None,
    results: Annotated[
        Path | None,
        typer.Option(
            '--results',
            '-s',
            exists=True,
            help='Results database to explore stored profiling runs.',
        ),
    ] = None,
    info: Annotated[
        Path | None,
        typer.Option(
            '--info',
            '-i',
            exists=True,
            help='Project database to inspect project and curated metadata.',
        ),
    ] = None,
    reference: Annotated[
        str | None,
        typer.Option(
            '--reference',
            help='(With --rules) Filter by reference name (partial match, case-insensitive).',
        ),
    ] = None,
) -> None:
    """
    Browse resistance rules in a project database or stored profiling runs in a results database.

    Use exactly one of --rules, --results, or --info.
    """
    selected_modes = [rules is not None, results is not None, info is not None]
    if sum(selected_modes) != 1:
        raise click.UsageError('Specify exactly one of --rules, --results, or --info.')

    if rules:
        _explore_rules(rules, reference)
    elif results:
        _explore_runs(results)  # type: ignore[arg-type]
    else:
        _explore_info(info)  # type: ignore[arg-type]


def _explore_rules(project_db: Path, reference: str | None) -> None:
    """List resistance rules in a project database."""
    console = Console(highlight=False, width=220)
    project_conn = None
    try:
        project_conn = open_project_db(project_db)

        ref_id: int | None = None
        if reference is not None:
            refs = list_references_for_display(project_conn)
            matches = [r for r in refs if reference.lower() in r['name'].lower()]
            if not matches:
                raise click.ClickException(
                    f'No reference matching {reference!r} found in the project database.\n'
                    'Available references: ' + ', '.join(r['name'] for r in refs)
                )
            if len(matches) > 1:
                names = ', '.join(r['name'] for r in matches)
                raise click.ClickException(
                    f'Ambiguous reference filter {reference!r} — matched: {names}.\n'
                    'Please be more specific.'
                )
            ref_id = int(matches[0]['id'])

        rows = list_rules_for_display(project_conn, ref_id=ref_id)
        if not rows:
            console.print('No resistance rules found.')
            return

        columns = list(rows[0].keys())
        table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
        for column_key in columns:
            label = RULE_COLUMN_LABELS.get(column_key, column_key)
            justify = 'right' if column_key == 'position' else 'left'
            table.add_column(label, justify=justify)

        for row in rows:
            cells = []
            for column_key in columns:
                value = row.get(column_key)
                cells.append('' if value is None else str(value))
            table.add_row(*cells)

        console.print(table)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()


def _explore_runs(results_db: Path) -> None:
    """List all stored profiling runs in a results database."""
    console = Console(highlight=False)
    results_conn = None
    try:
        results_conn = open_results_db(results_db)
        runs = list_runs(results_conn)
        if not runs:
            console.print('No stored results found.')
            return

        table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
        table.add_column('ID', justify='right', style='dim', no_wrap=True)
        table.add_column('Sample')
        table.add_column('Reference')
        table.add_column('Input')
        table.add_column('Hits', justify='right')
        table.add_column('Created', style='dim')
        table.add_column('Status')

        for run in runs:
            stale = ''
            run_project_ts = run.get('project_updated_at') or ''
            run_created_ts = run.get('created_at') or ''
            if run_project_ts and run_created_ts and run_project_ts > run_created_ts:
                stale = '[yellow]stale[/yellow]'
            table.add_row(
                str(run['id']),
                run['sample_name'] or '',
                run['reference_name'],
                Path(run['vcf_path']).name,
                str(run['resistance_hits']),
                run['created_at'],
                stale,
            )
        console.print(table)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()


def _explore_info(project_db: Path) -> None:
    """Show non-empty project metadata fields from a project database."""
    console = Console(highlight=False)
    project_conn = None
    try:
        project_conn = open_project_db(project_db)
        info = get_project_summary_for_display(project_conn)

        ordered_keys = [
            'name',
            'uuid',
            'created_at',
            'schema_version',
            'metadata_maintainers',
            'metadata_contact',
            'metadata_publication_pmid',
            'metadata_publication_doi',
            'metadata_website',
            'metadata_description',
            'metadata_maintainer_update',
            'metadata_license',
            'metadata_tsv_checksum',
        ]

        rows_to_show: list[tuple[str, str]] = []
        for key in ordered_keys:
            value = info.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text == '':
                continue
            rows_to_show.append((PROJECT_INFO_LABELS[key], text))

        if not rows_to_show:
            console.print('No project metadata found.')
            return

        table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
        table.add_column('Field', style='bold')
        table.add_column('Value')
        for label, value in rows_to_show:
            table.add_row(label, value)
        console.print(table)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()


def register(app: typer.Typer) -> None:
    """Register the explore command on the given Typer app."""
    app.command()(explore)
