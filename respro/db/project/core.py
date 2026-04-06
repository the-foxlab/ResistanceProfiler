"""
Project orchestration — create and extend project databases.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

from respro.db.project.drugs import _consolidate_drug_names_to_lowercase, _get_drugs_from_pubchem
from respro.db.project.genes import _load_genbank_records
from respro.db.project.rules import _load_resistance_rules
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema, open_project_db
from respro.io.genbank import ParsedGenBankReference, parse_genbank_sources
from respro.utils.files import require_file

logger = logging.getLogger(__name__)


def init_project(
    *,
    db_path: Path,
    name: str,
    genbank_paths: list[Path],
    rules_tsv: Path,
    overwrite: bool = False,
    drug_info: bool = True,
) -> Path:
    """
    Create and populate a new project database.

    :param db_path: where to write the SQLite file
    :param name: project name
    :param genbank_paths: one or more GenBank file paths; each file may contain
        one or more records and CDS features are imported as genes with
        GenBank-derived identifiers
    :param rules_tsv: tab-separated file with columns: gene, position, mutation,
        antiviral, reference_identifier, reference; optional columns:
        phenotype and/or clinical_phenotype, ic50, publication, source
    :param overwrite: if True, delete an existing database at db_path before
        creating a fresh one; if False (default), raise FileExistsError
    :param drug_info: if True (default), query PubChem to attach CID, URL, and a
        short description to each drug; failures are non-fatal and the project
        is still created without drug information
    :return: path to the created database
    """
    if not genbank_paths:
        raise ValueError('At least one GenBank file must be provided')

    # Validate all declared input files up front so init fails early and clearly.
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
        # Load curated references/genes first, then validate and import rules.
        project_id = _insert_project(conn, name)
        _load_genbank_records(conn, project_id, genbank_records)
        _load_resistance_rules(conn, project_id, rules_tsv)
        if drug_info:
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
    drug_info: bool = True,
) -> Path:
    """
    Add curated rules and optional GenBank annotations to an existing project.

    :param db_path: existing project database path
    :param rules_tsv: tab-separated rules file to add
    :param genbank_paths: optional GenBank files with additional references/genes;
        if omitted, the existing DB annotations are used for rule validation
    :param drug_info: if True, query PubChem for newly seen drugs
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
        _load_resistance_rules(conn, project_id, rules_tsv)
        if drug_info:
            _get_drugs_from_pubchem(conn, project_id)
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

