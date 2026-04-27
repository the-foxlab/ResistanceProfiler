"""
`respro maintained.db` command — browse and download databases from the companion repository.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from respro.cli.init import init_project
from respro.io.maintained_db import (
    download_database_files,
    fetch_database_metadata,
    list_maintained_databases,
)
from respro.utils.files import resolve_output_file
from respro.utils.logging import err_console


def _list_command() -> None:
    """List all databases available in the respro companion repository."""
    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Fetching database listing…[/dim]'):
            db_names = list_maintained_databases()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if not db_names:
        console.print('[yellow]No databases found in companion repository.[/yellow]')
        raise typer.Exit()

    for db_name in db_names:
        try:
            with err_console.status(f'[dim]Fetching metadata for {db_name}…[/dim]'):
                meta = fetch_database_metadata(db_name)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        lines = Text()
        lines.append('database_name: ', style='bold cyan')
        lines.append(f'{db_name}\n', style='bold white')

        _meta_fields = [
            ('description', 'Description'),
            ('maintainers', 'Maintainers'),
            ('contact', 'Contact'),
            ('website', 'Website'),
            ('license', 'License'),
            ('maintainer_update', 'Last updated'),
            ('publication_pmid', 'Publication PMID'),
        ]
        for key, label in _meta_fields:
            value = meta.get(key)
            if not value:
                continue
            if isinstance(value, list):
                value = ', '.join(str(v) for v in value)
            lines.append(f'{label}: ', style='dim')
            if key == 'publication_pmid':
                lines.append(f'{value}')
            else:
                lines.append(f'{value}\n')
        console.print(Panel(lines, expand=False))


def _download_command(
    database_name: Annotated[
        str,
        typer.Option(
            '--download',
            '-d',
            help='Name of the database to download (from --list).',
            metavar='NAME',
        ),
    ],
    db_path: Path,
) -> None:
    """Download a maintained database and initialise a project database from it."""
    console = Console(highlight=False)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        try:
            with err_console.status(f'[dim]Downloading {database_name}…[/dim]'):
                files = download_database_files(database_name, tmp_dir)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        genbank_paths: list[Path] = files['genbank']  # type: ignore[assignment]
        if not genbank_paths:
            raise click.ClickException(
                f'No GenBank records could be fetched for database {database_name!r}. '
                'Check that the rules.tsv contains valid reference_identifier values.'
            )

        db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with err_console.status('[dim]Initialising project database…[/dim]'):
                init_project(
                    db_path=db_path,
                    name=database_name,
                    genbank_paths=genbank_paths,
                    rules_tsv=files['rules'],  # type: ignore[arg-type]
                    formula_rules_tsv=files['formula_rules'],  # type: ignore[arg-type]
                    metadata_json=files['metadata'],  # type: ignore[arg-type]
                    overwrite=True,
                    additional_info=True,
                )
        except (FileNotFoundError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc

    console.print(f'[green]✓[/green] Database initialised: [cyan]{db_path}[/cyan]')


def _maintained_db_command(
    list_mode: Annotated[
        bool,
        typer.Option(
            '--list',
            help='List available maintained databases and their metadata.',
        ),
    ] = False,
    download: Annotated[
        str | None,
        typer.Option(
            '--download',
            help='Database name to download (use value from --list output).',
            metavar='NAME',
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            '--output',
            '-o',
            help='Output SQLite database path (directory or file). Defaults to <database_name>.db.',
        ),
    ] = None,
) -> None:
    """
    List maintained project databases or download one and initialize a local project DB.

    These databases are stored in a companion repository and are monthly checked for maintainer updates and formatted to be respro compatible.
    """
    if list_mode and download:
        raise click.ClickException('Use either --list or --download, not both.')
    if not list_mode and not download:
        raise click.ClickException('Provide either --list or --download NAME.')

    if list_mode:
        _list_command()
        return

    db_output = (
        resolve_output_file(output, f'{download}.db')
        if output is not None
        else Path(f'{download}.db')
    )
    _download_command(database_name=str(download), db_path=db_output)


def register(app: typer.Typer) -> None:
    """Register maintained.db command on the given Typer app."""
    app.command('databases')(_maintained_db_command)
