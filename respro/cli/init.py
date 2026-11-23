"""Project initialization and incremental update commands — ``respro init`` and ``respro add``."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from respro.db.algorithms import (
    apply_drug_alias_mappings,
    load_interpretation_algorithms,
    store_interpretation_algorithms,
)
from respro.db.cache import clear_cached_mappings
from respro.db.drugs import consolidate_drug_names_to_lowercase, enrich_drugs_from_pubchem
from respro.db.features import load_genbank_records
from respro.db.project_metadata import load_metadata_json, store_project_metadata
from respro.db.rules.main import import_rules_with_summary, validate_rules_tsv
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema, open_project_db
from respro.io.genbank import ParsedGenBankReference, parse_genbank_sources
from respro.io.reference import read_fasta
from respro.utils.cli_errors import cli_error
from respro.utils.files import require_file, resolve_output_file
from respro.utils.logging import err_console

logger = logging.getLogger(__name__)


def _read_example_fasta_text(example_fasta: Path) -> str:
    """Read and validate a single-record example consensus FASTA, returning its original text."""
    require_file(example_fasta, 'Example FASTA')
    text = example_fasta.read_text()
    try:
        records = read_fasta(example_fasta)
    except Exception as exc:
        raise ValueError(f'Example FASTA could not be parsed: {exc}') from exc
    if len(records) != 1:
        raise ValueError(
            f'Example FASTA must contain a single record; got {len(records)}. '
            'The example is profiled as a single consensus sequence.'
        )
    return text


def _finalize_algorithms_and_enrichment(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
    *,
    force_store: bool,
    additional_info: bool,
    example_text: str,
    clear_example: bool = False,
) -> None:
    """Sanitize algorithms, apply drug aliases, optionally enrich drugs, and update example FASTA."""
    sanitized = _sanitize_effect_as_resistant_algorithms(conn, project_id, algorithms)
    if (force_store and sanitized) or (not force_store and sanitized != algorithms):
        store_interpretation_algorithms(conn, project_id, sanitized)

    alias_config = next((a for a in sanitized if a['name'] == 'drug_alias'), None)
    if alias_config:
        apply_drug_alias_mappings(conn, project_id, alias_config)
    if additional_info:
        enrich_drugs_from_pubchem(conn, project_id)
    if example_text:
        conn.execute('UPDATE project SET example_fasta = ? WHERE id = ?', (example_text, project_id))
    elif clear_example:
        conn.execute('UPDATE project SET example_fasta = ? WHERE id = ?', ('', project_id))


def init_project(
    *,
    db_path: Path,
    name: str,
    genbank_paths: list[Path],
    rules_tsv: Path,
    formula_rules_tsv: Path | None = None,
    metadata_json: Path | None = None,
    overwrite: bool = False,
    additional_info: bool = True,
    example_fasta: Path | None = None,
) -> Path:
    """Create and populate a new SQLite project database from GenBank records and rule TSVs."""
    if not genbank_paths:
        raise ValueError('At least one GenBank file must be provided')
    for genbank_path in genbank_paths:
        require_file(genbank_path, 'GenBank file')
    require_file(rules_tsv, 'Rules TSV')
    if formula_rules_tsv is not None:
        require_file(formula_rules_tsv, 'Formula rules TSV')

    example_text = _read_example_fasta_text(example_fasta) if example_fasta else ''
    metadata_payload, algorithms = load_metadata_json(metadata_json) if metadata_json else ({}, [])
    genbank_records = parse_genbank_sources(genbank_paths)

    if db_path.exists() and not overwrite:
        raise FileExistsError(f'Database already exists: {db_path}')

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=f'.{db_path.name}.', suffix='.tmp', dir=db_path.parent)
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    conn = create_schema(tmp_path)
    try:
        project_id = _insert_project(conn, name)
        load_genbank_records(conn, project_id, genbank_records)
        import_rules_with_summary(
            conn, project_id, rules_tsv,
            formula_rules_tsv=formula_rules_tsv, additional_info=additional_info,
        )
        store_project_metadata(conn, project_id, metadata_payload)
        _finalize_algorithms_and_enrichment(
            conn, project_id, algorithms,
            force_store=True, additional_info=additional_info, example_text=example_text,
        )
        conn.commit()
    except Exception:
        conn.close()
        for suffix in ('', '-wal', '-shm'):
            Path(f'{tmp_path}{suffix}').unlink(missing_ok=True)
        raise
    else:
        conn.close()

    for suffix in ('-wal', '-shm'):
        Path(f'{db_path}{suffix}').unlink(missing_ok=True)
    os.replace(tmp_path, db_path)
    logger.info('Project initialized: %s (%s)', name, db_path)
    return db_path


def add_to_project(
    *,
    db_path: Path,
    rules_tsv: Path,
    formula_rules_tsv: Path | None = None,
    genbank_paths: list[Path] | None = None,
    additional_info: bool = True,
    validate_only: bool = False,
    example_fasta: Path | None = None,
    clear_example: bool = False,
) -> Path:
    """Add curated rules and optional GenBank annotations to an existing project database."""
    require_file(db_path, 'Project database')
    require_file(rules_tsv, 'Rules TSV')
    if formula_rules_tsv is not None:
        require_file(formula_rules_tsv, 'Formula rules TSV')
    if example_fasta is not None and clear_example:
        raise ValueError('Provide either --example or --no-example, not both.')
    example_text = _read_example_fasta_text(example_fasta) if example_fasta else ''

    records: list[ParsedGenBankReference] = []
    for genbank_path in genbank_paths or []:
        require_file(genbank_path, 'GenBank file')
    if genbank_paths:
        records = parse_genbank_sources(genbank_paths)

    conn = open_project_db(db_path)
    try:
        project_id = _get_existing_project_id(conn)
        consolidate_drug_names_to_lowercase(conn, project_id)
        # Load supplied GenBank files before verifying reference presence so --genbank can recover empty DBs.
        if records:
            load_genbank_records(conn, project_id, records)
        _ensure_project_has_reference_annotations(conn)

        if validate_only:
            validate_rules_tsv(conn, project_id, rules_tsv, formula_rules_tsv=formula_rules_tsv)
            conn.rollback()
            logger.info('Rules validation passed: %s', rules_tsv)
            return db_path

        import_rules_with_summary(
            conn, project_id, rules_tsv,
            formula_rules_tsv=formula_rules_tsv, additional_info=additional_info,
        )
        stored_algorithms = load_interpretation_algorithms(conn, project_id)
        _finalize_algorithms_and_enrichment(
            conn, project_id, stored_algorithms,
            force_store=False, additional_info=additional_info,
            example_text=example_text, clear_example=clear_example,
        )
        clear_cached_mappings(conn)
        conn.execute("UPDATE project SET updated_at = datetime('now') WHERE id = ?", (project_id,))
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
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _sanitize_effect_as_resistant_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> list[dict]:
    """Drop ``effect_as_resistant`` rules whose drug is absent from the project."""
    project_drugs = {
        row[0]
        for row in conn.execute('SELECT name FROM drug WHERE project_id = ?', (project_id,)).fetchall()
    }
    sanitized: list[dict] = []
    for config in algorithms:
        if config.get('name') != 'effect_as_resistant':
            sanitized.append(config)
            continue

        kept_rules: list[dict] = []
        for rule in config['rules']:
            if rule['drug'] in project_drugs:
                kept_rules.append(rule)
                continue
            logger.warning(
                'effect_as_resistant: ignoring rule for unknown drug %r (feature=%r, reference=%r)',
                rule['drug'], rule['feature'], rule['reference'],
            )
        if kept_rules:
            sanitized.append({**config, 'rules': kept_rules})
        else:
            logger.warning(
                'effect_as_resistant: removed algorithm because no rules reference known project drugs'
            )
    return sanitized


def _get_existing_project_id(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT id FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Existing database has no project row')
    return int(row['id'])


def _ensure_project_has_reference_annotations(conn: sqlite3.Connection) -> None:
    ref_count = conn.execute('SELECT COUNT(*) FROM reference').fetchone()[0]
    feat_count = conn.execute('SELECT COUNT(*) FROM feature').fetchone()[0]
    if ref_count == 0 or feat_count == 0:
        raise ValueError(
            'Existing database has no stored references/features. '
            'Provide --genbank or rebuild the project with respro init.'
        )


def _init_command(
    name: Annotated[str, typer.Option('--name', '-n', help='Project name.')],
    rules: Annotated[Path, typer.Option('--rules', '-r', help='Resistance rules TSV.')],
    formula_rules: Annotated[
        Path | None, typer.Option('--formula-rules', '-f', help='Optional formula rules TSV.')
    ] = None,
    genbank_paths: Annotated[
        list[Path] | None,
        typer.Option('--genbank', '-g', exists=True, help='GenBank file(s). Repeat for multiple files.'),
    ] = None,
    output: Annotated[
        Path, typer.Option('--output', '-o', help='Output SQLite database path.')
    ] = Path('project.db'),
    metadata: Annotated[
        Path | None, typer.Option('--metadata', '-m', exists=True, help='Optional metadata JSON file.')
    ] = None,
    overwrite: Annotated[
        bool, typer.Option('--overwrite', '-w', help='Overwrite existing database.')
    ] = False,
    additional_info: Annotated[
        bool,
        typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        ),
    ] = True,
    example: Annotated[
        Path | None,
        typer.Option(
            '--example', '-ex', exists=True,
            help='Optional single-record consensus FASTA stored with the database as an example.',
        ),
    ] = None,
) -> None:
    """Initialise a project database from one or more GenBank records and resistance rules TSV."""
    if not genbank_paths:
        cli_error('At least one --genbank file is required.')

    output_path = resolve_output_file(output, 'project.db')
    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Initialising project database…[/dim]'):
            db_path = init_project(
                db_path=output_path,
                name=name,
                genbank_paths=list(genbank_paths),
                rules_tsv=rules,
                formula_rules_tsv=formula_rules,
                metadata_json=metadata,
                overwrite=overwrite,
                additional_info=additional_info,
                example_fasta=example,
            )
    except (FileExistsError, FileNotFoundError, ValueError, sqlite3.Error, OSError) as exc:
        cli_error(str(exc))
    console.print(f'[green]✓[/green] Project initialised: [cyan]{db_path}[/cyan]')


def _init_add_command(
    project: Annotated[
        Path, typer.Option('--project', '-p', exists=True, help='Existing project SQLite database.')
    ],
    rules: Annotated[
        Path, typer.Option('--rules', '-r', exists=True, help='Resistance rules TSV to add.')
    ],
    formula_rules: Annotated[
        Path | None, typer.Option('--formula-rules', '-f', exists=True, help='Optional formula rules TSV.')
    ] = None,
    genbank_paths: Annotated[
        list[Path] | None,
        typer.Option('--genbank', '-g', exists=True, help='Optional GenBank file(s) with additional references/features.'),
    ] = None,
    additional_info: Annotated[
        bool,
        typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        ),
    ] = True,
    validate: Annotated[
        bool, typer.Option('--validate', '-v', help='Validate rules and exit without writing DB changes.')
    ] = False,
    example: Annotated[
        Path | None,
        typer.Option(
            '--example', '-ex', exists=True,
            help='Replace the stored example consensus FASTA with this single-record file.',
        ),
    ] = None,
    clear_example: Annotated[
        bool, typer.Option('--no-example', help='Clear any stored example consensus FASTA.')
    ] = False,
) -> None:
    """Add curated rules and optional GenBank annotations to an existing project database."""
    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Updating project database…[/dim]'):
            db_path = add_to_project(
                db_path=project,
                genbank_paths=list(genbank_paths or []),
                rules_tsv=rules,
                formula_rules_tsv=formula_rules,
                additional_info=additional_info,
                validate_only=validate,
                example_fasta=example,
                clear_example=clear_example,
            )
    except (FileExistsError, FileNotFoundError, ValueError, sqlite3.Error, OSError) as exc:
        cli_error(str(exc))

    if validate:
        console.print(f'[green]✓[/green] Rules validation passed: [cyan]{db_path}[/cyan]')
        return
    console.print(f'[green]✓[/green] Project updated: [cyan]{db_path}[/cyan]')


def register(app: typer.Typer) -> None:
    """Register ``init`` and ``add`` commands on *app*."""
    app.command('init')(_init_command)
    app.command('add')(_init_add_command)
