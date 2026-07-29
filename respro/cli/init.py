"""
Project initialisation commands and orchestration — respro init, respro add.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from respro.core.rules import import_rules_with_summary, validate_rules_tsv
from respro.db.algorithms import (
    apply_drug_alias_mappings,
    apply_ic50_threshold_classification,
    load_interpretation_algorithms,
    store_interpretation_algorithms,
)
from respro.db.drugs import _consolidate_drug_names_to_lowercase, _get_drugs_from_pubchem
from respro.db.features import _load_genbank_records
from respro.db.project_metadata import load_metadata_json, store_project_metadata
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema, open_project_db
from respro.io.genbank import ParsedGenBankReference, parse_genbank_sources
from respro.utils.cli_errors import cli_error
from respro.utils.files import require_file, resolve_output_file
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
    formula_rules_tsv: Path | None = None,
    metadata_json: Path | None = None,
    overwrite: bool = False,
    additional_info: bool = True,
) -> Path:
    """
    Create and populate a new project database.

    :param db_path: where to write the SQLite file
    :param name: project name
    :param genbank_paths: one or more GenBank file paths
    :param rules_tsv: tab-separated resistance rules file
    :param metadata_json: optional metadata JSON file with curated database metadata
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
    if formula_rules_tsv is not None:
        require_file(formula_rules_tsv, 'Formula rules TSV')
    metadata_payload, algorithms = load_metadata_json(metadata_json) if metadata_json else ({}, [])

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
        import_rules_with_summary(
            conn,
            project_id,
            rules_tsv,
            formula_rules_tsv=formula_rules_tsv,
            additional_info=additional_info,
        )
        store_project_metadata(conn, project_id, metadata_payload)
        if algorithms:
            algorithms = _sanitize_effect_as_resistant_algorithms(conn, project_id, algorithms)
            if algorithms:
                store_interpretation_algorithms(conn, project_id, algorithms)
        alias_config = next((a for a in algorithms if a['name'] == 'drug_alias'), None)
        if alias_config:
            apply_drug_alias_mappings(conn, project_id, alias_config)
        ic50_config = next((a for a in algorithms if a['name'] == 'ic50_thresholds'), None)
        if ic50_config:
            apply_ic50_threshold_classification(conn, project_id, ic50_config)
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
    formula_rules_tsv: Path | None = None,
    genbank_paths: list[Path] | None = None,
    additional_info: bool = True,
    validate_only: bool = False,
) -> Path:
    """
    Add curated rules and optional GenBank annotations to an existing project.

    :param db_path: existing project database path
    :param rules_tsv: tab-separated rules file to add
    :param genbank_paths: optional GenBank files with additional references/features
    :param additional_info: if True, query PubChem for new drugs and resolve publication metadata
    :param validate_only: if True, run full rules validation/import path and roll back all DB changes
    :return: path to the updated database
    """
    require_file(db_path, 'Project database')
    require_file(rules_tsv, 'Rules TSV')
    if formula_rules_tsv is not None:
        require_file(formula_rules_tsv, 'Formula rules TSV')

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
        if validate_only:
            validate_rules_tsv(conn, project_id, rules_tsv, formula_rules_tsv=formula_rules_tsv)
            conn.rollback()
            logger.info('Rules validation passed: %s', rules_tsv)
            return db_path

        import_rules_with_summary(
            conn,
            project_id,
            rules_tsv,
            formula_rules_tsv=formula_rules_tsv,
            additional_info=additional_info,
        )
        stored_algorithms = load_interpretation_algorithms(conn, project_id)
        sanitized_algorithms = _sanitize_effect_as_resistant_algorithms(
            conn,
            project_id,
            stored_algorithms,
        )
        if sanitized_algorithms != stored_algorithms:
            store_interpretation_algorithms(conn, project_id, sanitized_algorithms)
        stored_algorithms = sanitized_algorithms
        alias_config = next((a for a in stored_algorithms if a['name'] == 'drug_alias'), None)
        if alias_config:
            apply_drug_alias_mappings(conn, project_id, alias_config)
        ic50_config = next((a for a in stored_algorithms if a['name'] == 'ic50_thresholds'), None)
        if ic50_config:
            apply_ic50_threshold_classification(conn, project_id, ic50_config)
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


def _sanitize_effect_as_resistant_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> list[dict]:
    """
    Remove effect_as_resistant rules that reference drugs absent from the project.

    :param conn: project DB connection
    :param project_id: project id
    :param algorithms: interpretation algorithm configs
    :return: sanitized algorithm list with unknown-drug effect rules removed
    """
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
                rule['drug'],
                rule['feature'],
                rule['reference'],
            )

        if kept_rules:
            sanitized_config = dict(config)
            sanitized_config['rules'] = kept_rules
            sanitized.append(sanitized_config)
            continue

        logger.warning(
            'effect_as_resistant: removed algorithm because no rules reference known project drugs'
        )

    return sanitized


def _get_existing_project_id(conn: sqlite3.Connection) -> int:
    """Return the existing project id from an initialized database."""
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT id FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Existing database has no project row')
    return int(row['id'])


def _ensure_project_has_reference_annotations(conn: sqlite3.Connection) -> None:
    """Fail early when an existing DB lacks the stored references/features needed for rule loading."""
    reference_count = conn.execute('SELECT COUNT(*) FROM reference').fetchone()[0]
    feature_count = conn.execute('SELECT COUNT(*) FROM feature').fetchone()[0]
    if reference_count == 0 or feature_count == 0:
        raise ValueError(
            'Existing database has no stored references/features. '
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
    formula_rules: Annotated[
        Path | None, typer.Option('--formula-rules', '-f', help='Optional formula rules TSV.')
    ] = None,
    genbank_paths: Annotated[
        list[Path] | None, typer.Option(
            '--genbank', '-g', exists=True,
            help='GenBank file(s). Repeat for multiple files.',
        )
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
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
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
        list[Path] | None, typer.Option(
            '--genbank', '-g', exists=True,
            help='Optional GenBank file(s) with additional references/features.',
        )
    ] = None,
    additional_info: Annotated[
        bool, typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        )
    ] = True,
    validate: Annotated[
        bool, typer.Option('--validate', '-v', help='Validate rules and exit without writing DB changes.')
    ] = False,
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
                formula_rules_tsv=formula_rules,
                additional_info=additional_info,
                validate_only=validate,
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        cli_error(str(exc))

    if validate:
        console.print(f'[green]✓[/green] Rules validation passed: [cyan]{db_path}[/cyan]')
        return

    console.print(f'[green]✓[/green] Project updated: [cyan]{db_path}[/cyan]')


def register(app: typer.Typer) -> None:
    """Register init and add commands on the given Typer app."""
    app.command('init')(_init_command)
    app.command('add')(_init_add_command)
