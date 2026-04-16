"""
Project initialisation commands and orchestration — respro init, respro add.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console

from respro.db.drugs import _consolidate_drug_names_to_lowercase, _get_drugs_from_pubchem
from respro.db.genes import _load_genbank_records
from respro.db.rules_import import _load_resistance_rules
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema, open_project_db
from respro.io.genbank import ParsedGenBankReference, parse_genbank_sources
from respro.utils.files import require_file
from respro.utils.logging import err_console

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────

def init_project(
    *,
    db_path: Path,
    name: str,
    genbank_paths: list[Path],
    rules_tsv: Path,
    overwrite: bool = False,
    additional_info: bool = True,
) -> Path:
    """
    Create and populate a new project database.

    :param db_path: where to write the SQLite file
    :param name: project name
    :param genbank_paths: one or more GenBank file paths
    :param rules_tsv: tab-separated resistance rules file
    :param overwrite: if True, delete an existing database at db_path before creating a fresh one
    :param additional_info: if True (default), query PubChem for drug metadata and resolve
        publication DOIs/titles via NCBI and CrossRef; failures are non-fatal
    :return: path to the created database
    """
    if not genbank_paths:
        raise ValueError('At least one GenBank file must be provided')

    for genbank_path in genbank_paths:
        require_file(genbank_path, 'GenBank file')
    require_file(rules_tsv, 'Rules TSV')

    genbank_records = parse_genbank_sources(genbank_paths)

    if db_path.exists():
        if not overwrite:
            raise FileExistsError(f'Database already exists: {db_path}')
        db_path.unlink()
        logger.info('Removed existing database: %s', db_path)

    conn = create_schema(db_path)
    try:
        project_id = _insert_project(conn, name)
        _load_genbank_records(conn, project_id, genbank_records)
        _load_resistance_rules(conn, project_id, rules_tsv, additional_info=additional_info)
        if additional_info:
            _get_drugs_from_pubchem(conn, project_id)
        conn.commit()
        logger.info('Project initialized: %s (%s)', name, db_path)
    except Exception:
        db_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    return db_path


def add_to_project(
    *,
    db_path: Path,
    rules_tsv: Path,
    genbank_paths: list[Path] | None = None,
    additional_info: bool = True,
) -> Path:
    """
    Add curated rules and optional GenBank annotations to an existing project.

    :param db_path: existing project database path
    :param rules_tsv: tab-separated rules file to add
    :param genbank_paths: optional GenBank files with additional references/genes
    :param additional_info: if True, query PubChem for new drugs and resolve publication metadata
    :return: path to the updated database
    """
    require_file(db_path, 'Project database')
    require_file(rules_tsv, 'Rules TSV')

    records: list[ParsedGenBankReference] = []
    for genbank_path in genbank_paths or []:
        require_file(genbank_path, 'GenBank file')
    if genbank_paths:
        records = parse_genbank_sources(genbank_paths)

    conn = open_project_db(db_path)
    try:
        project_id = _get_existing_project_id(conn)
        _ensure_project_has_reference_annotations(conn)
        _consolidate_drug_names_to_lowercase(conn, project_id)
        if records:
            _load_genbank_records(conn, project_id, records)
        _load_resistance_rules(conn, project_id, rules_tsv, additional_info=additional_info)
        if additional_info:
            _get_drugs_from_pubchem(conn, project_id)
        conn.execute(
            "UPDATE project SET updated_at = datetime('now') WHERE id = ?",
            (project_id,),
        )
        conn.commit()
        logger.info('Project updated: %s', db_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return db_path


def _insert_project(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        (name, PROJECT_SCHEMA_VERSION, str(uuid.uuid4())),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _get_existing_project_id(conn: sqlite3.Connection) -> int:
    """Return the existing project id from an initialized database."""
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT id FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Existing database has no project row')
    return int(row['id'])


def _ensure_project_has_reference_annotations(conn: sqlite3.Connection) -> None:
    """Fail early when an existing DB lacks the stored references/genes needed for rule loading."""
    reference_count = conn.execute('SELECT COUNT(*) FROM reference').fetchone()[0]
    gene_count = conn.execute('SELECT COUNT(*) FROM gene').fetchone()[0]
    if reference_count == 0 or gene_count == 0:
        raise ValueError(
            'Existing database has no stored references/genes. '
            'Provide --genbank or rebuild the project with respro init.'
        )


# ──────────────────────────────────────────────────────────────────────
# CLI commands
# ──────────────────────────────────────────────────────────────────────

def _init_command(
    name: Annotated[
        str, typer.Option('--name', '-n', help='Project name.')
    ],
    rules: Annotated[
        Path, typer.Option('--rules', '-r', help='Resistance rules TSV.')
    ],
    genbank_paths: Annotated[
        list[Path] | None, typer.Option(
            '--genbank', '-g', exists=True,
            help='GenBank file(s). Repeat for multiple files.',
        )
    ] = None,
    output: Annotated[
        Path, typer.Option('--output', '-o', help='Output SQLite database path.')
    ] = Path('project.db'),
    overwrite: Annotated[
        bool, typer.Option('--overwrite', help='Overwrite existing database.')
    ] = False,
    additional_info: Annotated[
        bool, typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        )
    ] = True,
) -> None:
    """
    Initialise a project database from one or more GenBank reference records and resistance rules provided in TSV.
    """
    if not genbank_paths:
        raise click.UsageError('At least one --genbank file is required.')

    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Initialising project database…[/dim]'):
            db_path = init_project(
                db_path=output,
                name=name,
                genbank_paths=list(genbank_paths),
                rules_tsv=rules,
                overwrite=overwrite,
                additional_info=additional_info,
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f'[green]✓[/green] Project initialised: [cyan]{db_path}[/cyan]')


def _init_add_command(
    project: Annotated[
        Path, typer.Option('--project', '-p', exists=True, help='Existing project SQLite database.')
    ],
    rules: Annotated[
        Path, typer.Option('--rules', '-r', exists=True, help='Resistance rules TSV to add.')
    ],
    genbank_paths: Annotated[
        list[Path] | None, typer.Option(
            '--genbank', '-g', exists=True,
            help='Optional GenBank file(s) with additional references/genes.',
        )
    ] = None,
    additional_info: Annotated[
        bool, typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        )
    ] = True,
) -> None:
    """
    Add curated rules and optional GenBank annotations to an existing project database.
    """
    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Updating project database…[/dim]'):
            db_path = add_to_project(
                db_path=project,
                genbank_paths=list(genbank_paths or []),
                rules_tsv=rules,
                additional_info=additional_info,
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f'[green]✓[/green] Project updated: [cyan]{db_path}[/cyan]')


def register(app: typer.Typer) -> None:
    """Register init and add commands on the given Typer app."""
    app.command('init')(_init_command)
    app.command('add')(_init_add_command)
