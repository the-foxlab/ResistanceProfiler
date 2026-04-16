"""
`respro explore` command — browse resistance rules or stored profiling runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import click
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from respro.db.results import list_runs
from respro.db.rules_queries import list_references_for_display, list_rules_for_display
from respro.db.schema import open_project_db, open_results_db


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

    Use either --rules (project DB) or --results (results DB), not both.
    """
    if not rules and not results:
        raise click.UsageError('Specify either --rules or --results.')
    if rules and results:
        raise click.UsageError('Use either --rules or --results, not both.')

    if rules:
        _explore_rules(rules, reference)
    else:
        _explore_runs(results)  # type: ignore[arg-type]


def _explore_rules(project_db: Path, reference: str | None) -> None:
    """List resistance rules in a project database."""
    console = Console(highlight=False)
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

        # Only render columns that have at least one non-empty value.
        def _has_values(key: str) -> bool:
            return any(row.get(key) for row in rows)

        table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
        table.add_column('Reference')
        table.add_column('Gene')
        table.add_column('Pos', justify='right')
        table.add_column('Ref')
        table.add_column('Mut')
        table.add_column('Drug')
        table.add_column('Phenotype')

        show_ic50 = _has_values('ic50')
        show_fold = _has_values('fold_ic50')
        show_clinical = _has_values('clinical_phenotype')
        show_source = _has_values('source')

        if show_ic50:
            table.add_column('IC50')
        if show_fold:
            table.add_column('Fold-IC50')
        if show_clinical:
            table.add_column('Clinical phenotype')
        if show_source:
            table.add_column('Source')

        for row in rows:
            cells = [
                row.get('reference_name', ''),
                row['gene'],
                str(row['position'] + 1),
                row['reference'] or '',
                row['mutation'],
                row['drug'],
                row['phenotype'],
            ]
            if show_ic50:
                cells.append(row['ic50'] or '')
            if show_fold:
                cells.append(row['fold_ic50'] or '')
            if show_clinical:
                cells.append(row['clinical_phenotype'] or '')
            if show_source:
                cells.append(row['source'] or '')
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


def register(app: typer.Typer) -> None:
    """Register the explore command on the given Typer app."""
    app.command()(explore)
