"""
`respro manage` command group — browse databases and manage stored run results.
"""

from __future__ import annotations  # noqa: I001

from pathlib import Path
from typing import Annotated

import click
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from respro.cli.sync import sync_results_database
from respro.db.results import delete_run, list_runs
from respro.db.rules_queries import (
    get_project_summary_for_display,
    list_formula_rules_for_display,
    list_references_for_display,
    list_rules_for_display,
)
from respro.db.schema import open_project_db, open_results_db


RULE_COLUMN_LABELS = {
    'reference_name': 'Reference',
    'feature': 'Feature',
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

FORMULA_RULE_COLUMN_LABELS = {
    'reference_name': 'Reference',
    'drug': 'Drug',
    'formula_id': 'Formula ID',
    'label': 'Label',
    'normalized_expression': 'Expression',
    'phenotype': 'Phenotype',
    'clinical_phenotype': 'Clinical phenotype',
    'ic50': 'IC50',
    'fold_ic50': 'Fold IC50',
    'score': 'Score',
    'publication': 'DOI',
    'source': 'Source',
    'comment': 'Comment',
    'member_count': 'Members',
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


manage_app = typer.Typer(
    help='Manage project and results databases.',
    no_args_is_help=True,
)


@manage_app.command('database')
def manage_database(
    db_path: Annotated[
        Path,
        typer.Argument(exists=True, help='Project database path.'),
    ],
    rules: Annotated[
        bool,
        typer.Option('--rules', help='Show resistance rules from the project database.'),
    ] = False,
    info: Annotated[
        bool,
        typer.Option('--info', help='Show project metadata from the project database.'),
    ] = False,
    reference: Annotated[
        str | None,
        typer.Option('--reference', help='Optional reference filter (partial, case-insensitive).'),
    ] = None,
    list_single: Annotated[
        bool,
        typer.Option('--list-single', help='List single (atomic) resistance rules.'),
    ] = False,
    list_combi: Annotated[
        bool,
        typer.Option('--list-combi', help='List combination/formula resistance rules.'),
    ] = False,
) -> None:
    """Manage a project database via --rules or --info mode."""
    rules_mode = bool(rules or list_single or list_combi)
    if info == rules_mode:
        raise click.UsageError('Specify either --info or --rules/--list-single/--list-combi.')

    if rules_mode:
        if not list_single and not list_combi:
            list_single = True
            list_combi = True
        _explore_rules(db_path, reference, list_single=list_single, list_combi=list_combi)
        return

    _explore_info(db_path)


@manage_app.command('results')
def manage_results(
    results_db: Annotated[
        Path,
        typer.Argument(exists=True, help='Results database path.'),
    ],
    list_mode: Annotated[
        bool,
        typer.Option('--list', help='List stored profiling runs.'),
    ] = False,
    delete_run_id: Annotated[
        str | None,
        typer.Option('--delete', help='Delete one run by id.'),
    ] = None,
    sync_project_db: Annotated[
        Path | None,
        typer.Option('--sync', exists=True, help='Sync all stored runs against this project DB.'),
    ] = None,
    force: Annotated[
        bool,
        typer.Option('--force', '-f', help='Skip delete confirmation prompt.'),
    ] = False,
) -> None:
    """Manage a results database via --list, --delete, or --sync."""
    selected_modes = (
        int(list_mode)
        + int(delete_run_id is not None)
        + int(sync_project_db is not None)
    )
    if selected_modes != 1:
        raise click.UsageError('Specify exactly one of --list, --delete, or --sync.')

    if list_mode:
        _explore_runs(results_db)
        return

    if sync_project_db is not None:
        sync_results_database(results_db_path=results_db, project_db_path=sync_project_db)
        return

    results_conn = None
    try:
        results_conn = open_results_db(results_db)
        run_row = results_conn.execute(
            'SELECT id, sample_name FROM run WHERE id = ? OR CAST(id AS TEXT) = ?',
            (delete_run_id, delete_run_id),
        ).fetchone()
        label = (
            delete_run_id
            if run_row is None
            else f"{run_row['id']} ({run_row['sample_name'] or 'sample: n/a'})"
        )

        if not force:
            confirmed = typer.confirm(f'Delete run {label}?', default=False)
            if not confirmed:
                raise click.ClickException('Deletion cancelled.')

        deleted = delete_run(results_conn, delete_run_id or '')
        sample_name = str(deleted['sample_name']) or 'n/a'
        typer.echo(f"Deleted run {deleted['id']} (sample: {sample_name})")
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()


def _explore_rules(
    project_db: Path,
    reference: str | None,
    *,
    list_single: bool,
    list_combi: bool,
) -> None:
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

        single_rows = list_rules_for_display(project_conn, ref_id=ref_id) if list_single else []
        combi_rows = list_formula_rules_for_display(project_conn, ref_id=ref_id) if list_combi else []

        if not single_rows and not combi_rows:
            console.print('No resistance rules found.')
            return

        if single_rows:
            console.print('[bold]Single rules[/bold]')
            _render_rule_table(console, single_rows, RULE_COLUMN_LABELS)

        if combi_rows:
            if single_rows:
                console.print('')
            console.print('[bold]Combination rules[/bold]')
            _render_rule_table(console, combi_rows, FORMULA_RULE_COLUMN_LABELS)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()

def _render_rule_table(
    console: Console,
    rows: list[dict],
    column_labels: dict[str, str],
) -> None:
    """Render a tabular list of rule rows."""
    columns = list(rows[0].keys())
    table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
    for column_key in columns:
        label = column_labels.get(column_key, column_key)
        justify = 'right' if column_key in {'position', 'member_count'} else 'left'
        table.add_column(label, justify=justify)

    for row in rows:
        cells = []
        for column_key in columns:
            value = row.get(column_key)
            cells.append('' if value is None else str(value))
        table.add_row(*cells)

    console.print(table)


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
    """Register the manage command group on the given Typer app."""
    app.add_typer(manage_app, name='manage')
