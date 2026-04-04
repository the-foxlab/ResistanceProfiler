"""
SQLite schema creation and validation for ResistanceProfiler databases.
"""

import sqlite3
from pathlib import Path

PROJECT_SCHEMA_VERSION = 16
RESULTS_SCHEMA_VERSION = 1

PROJECT_SCHEMA_SQL = """\
-- Project metadata
CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    schema_version INTEGER NOT NULL DEFAULT 1
);

-- Reference sequences
CREATE TABLE IF NOT EXISTS reference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id),
    name        TEXT    NOT NULL,
    accession   TEXT    DEFAULT '',
    organism    TEXT    DEFAULT '',
    taxonomy    TEXT    DEFAULT '',
    length      INTEGER NOT NULL,
    UNIQUE(project_id, name)
);

-- Genes / ORFs / CDS
CREATE TABLE IF NOT EXISTS gene (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id INTEGER NOT NULL REFERENCES reference(id),
    name        TEXT    NOT NULL,
    protein     TEXT    DEFAULT '',
    protein_id  TEXT    DEFAULT '',  -- GenBank protein_id qualifier
    ncbi_protein_url TEXT DEFAULT '',  -- verified NCBI protein URL when reachable
    locus_tag   TEXT    DEFAULT '',  -- GenBank locus_tag qualifier
    note        TEXT    DEFAULT '',  -- GenBank note qualifier (short free text)
    start       INTEGER NOT NULL,  -- 0-based inclusive
    end         INTEGER NOT NULL,  -- 0-based exclusive
    strand      TEXT    NOT NULL DEFAULT '+',
    codon_start INTEGER NOT NULL DEFAULT 0,  -- 0-based offset (GenBank codon_start qualifier minus 1)
    nt_sequence TEXT    NOT NULL DEFAULT '',  -- CDS nucleotide slice in coding orientation
    aa_sequence TEXT    NOT NULL DEFAULT '',  -- pre-translated protein sequence
    UNIQUE(reference_id, name)
);

-- Drugs
CREATE TABLE IF NOT EXISTS drug (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id),
    name        TEXT    NOT NULL,
    pubchem_cid TEXT    DEFAULT '',  -- PubChem compound ID (integer stored as text)
    pubchem_url TEXT    DEFAULT '',  -- canonical PubChem compound URL
    description TEXT    DEFAULT '',  -- short human-readable description from PubChem
    structure_url TEXT    DEFAULT '',  -- 2D structure image URL from PubChem
    UNIQUE(project_id, name)
);

-- Resistance rules
CREATE TABLE IF NOT EXISTS resistance_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    gene_id     INTEGER NOT NULL REFERENCES gene(id),
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    reference_identifier TEXT DEFAULT '',
    position    INTEGER NOT NULL,  -- 0-based AA position within gene
    reference   TEXT    DEFAULT '',
    mutation    TEXT    NOT NULL,
    phenotype   TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype TEXT NOT NULL DEFAULT 'unknown',
    ic50        TEXT    DEFAULT '',
    publication TEXT    DEFAULT '',
    source      TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rule_gene_pos ON resistance_rule(gene_id, position);

-- Combined / co-occurring resistance rules (prepared for future use)
CREATE TABLE IF NOT EXISTS resistance_rule_set (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    phenotype   TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype TEXT NOT NULL DEFAULT 'unknown',
    ic50        TEXT    DEFAULT '',
    publication TEXT    DEFAULT '',
    source      TEXT    DEFAULT '',
    group_name  TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resistance_rule_set_member (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_set_id INTEGER NOT NULL REFERENCES resistance_rule_set(id),
    gene_id     INTEGER NOT NULL REFERENCES gene(id),
    reference_identifier TEXT DEFAULT '',
    position    INTEGER NOT NULL,  -- 0-based AA position within gene
    reference   TEXT    DEFAULT '',
    mutation    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rule_set_drug ON resistance_rule_set(drug_id);
CREATE INDEX IF NOT EXISTS idx_rule_set_member_set ON resistance_rule_set_member(rule_set_id);
CREATE INDEX IF NOT EXISTS idx_rule_set_member_gene_pos ON resistance_rule_set_member(gene_id, position);

-- Cached user-provided query references and their CDS mappings
CREATE TABLE IF NOT EXISTS query_reference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    sequence    TEXT    NOT NULL,
    length      INTEGER NOT NULL,
    checksum    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(checksum)
);

CREATE TABLE IF NOT EXISTS query_gene_mapping (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query_ref_id    INTEGER NOT NULL REFERENCES query_reference(id),
    gene_id         INTEGER NOT NULL REFERENCES gene(id),
    identity        REAL    NOT NULL,
    coverage        REAL    NOT NULL,
    query_start     INTEGER NOT NULL,
    query_end       INTEGER NOT NULL,
    strand          TEXT    NOT NULL DEFAULT '+',
    cigar           TEXT    NOT NULL,
    UNIQUE(query_ref_id, gene_id)
);
"""


RESULTS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS results_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name    TEXT    NOT NULL,
    project_db_path TEXT    NOT NULL,
    reference_name  TEXT    NOT NULL,
    sample_name     TEXT    DEFAULT '',
    vcf_path        TEXT    NOT NULL,
    total_variants  INTEGER NOT NULL DEFAULT 0,
    variants_in_cds INTEGER NOT NULL DEFAULT 0,
    resistance_hits INTEGER NOT NULL DEFAULT 0,
    combo_hits      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'complete',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS variant_result (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    chrom       TEXT    NOT NULL,
    pos         INTEGER NOT NULL,
    ref         TEXT    NOT NULL,
    alt         TEXT    NOT NULL,
    allele_freq REAL,
    depth       INTEGER,
    gene_name   TEXT    DEFAULT '',
    codon_pos   INTEGER,
    ref_codon   TEXT    DEFAULT '',
    alt_codon   TEXT    DEFAULT '',
    ref_aa      TEXT    DEFAULT '',
    alt_aa      TEXT    DEFAULT '',
    consequence TEXT    DEFAULT '',
    af_bin      TEXT    DEFAULT '',
    rule_match  INTEGER DEFAULT 0,
    drug_hits   TEXT    DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_vr_run ON variant_result(run_id);
CREATE INDEX IF NOT EXISTS idx_vr_gene ON variant_result(gene_name, codon_pos);
"""

_REQUIRED_RESULTS_COLUMNS = {
    'results_meta': {'key', 'value'},
    'run': {'id', 'project_name', 'project_db_path', 'reference_name', 'vcf_path'},
    'variant_result': {'id', 'run_id', 'chrom', 'pos', 'ref', 'alt'},
}

_OPTIONAL_RESULTS_COLUMN_DEFS = {
    'run': {
        'sample_name': "TEXT DEFAULT ''",
        'total_variants': 'INTEGER NOT NULL DEFAULT 0',
        'variants_in_cds': 'INTEGER NOT NULL DEFAULT 0',
        'resistance_hits': 'INTEGER NOT NULL DEFAULT 0',
        'combo_hits': 'INTEGER NOT NULL DEFAULT 0',
        'status': "TEXT NOT NULL DEFAULT 'complete'",
        'created_at': "TEXT DEFAULT ''",
    },
    'variant_result': {
        'allele_freq': 'REAL',
        'depth': 'INTEGER',
        'gene_name': "TEXT DEFAULT ''",
        'codon_pos': 'INTEGER',
        'ref_codon': "TEXT DEFAULT ''",
        'alt_codon': "TEXT DEFAULT ''",
        'ref_aa': "TEXT DEFAULT ''",
        'alt_aa': "TEXT DEFAULT ''",
        'consequence': "TEXT DEFAULT ''",
        'af_bin': "TEXT DEFAULT ''",
        'rule_match': 'INTEGER DEFAULT 0',
        'drug_hits': "TEXT DEFAULT '[]'",
    },
}

_REQUIRED_PROJECT_COLUMNS = {
    'project': {'id', 'name', 'created_at', 'schema_version'},
    'reference': {'id', 'project_id', 'name', 'length'},
    'gene': {'id', 'reference_id', 'name', 'start', 'end', 'strand'},
    'drug': {'id', 'project_id', 'name'},
    'resistance_rule': {'id', 'gene_id', 'drug_id', 'position', 'mutation'},
    'resistance_rule_set': {'id', 'drug_id'},
    'resistance_rule_set_member': {'id', 'rule_set_id', 'gene_id', 'position', 'mutation'},
    'query_reference': {'id', 'name', 'sequence', 'length', 'checksum'},
    'query_gene_mapping': {
        'id', 'query_ref_id', 'gene_id', 'identity', 'coverage',
        'query_start', 'query_end', 'strand', 'cigar',
    },
}

_OPTIONAL_PROJECT_COLUMN_DEFS = {
    'reference': {
        'accession': "TEXT DEFAULT ''",
        'organism': "TEXT DEFAULT ''",
        'taxonomy': "TEXT DEFAULT ''",
    },
    'gene': {
        'protein': "TEXT DEFAULT ''",
        'protein_id': "TEXT DEFAULT ''",
        'ncbi_protein_url': "TEXT DEFAULT ''",
        'locus_tag': "TEXT DEFAULT ''",
        'note': "TEXT DEFAULT ''",
        'codon_start': 'INTEGER NOT NULL DEFAULT 0',
        'nt_sequence': "TEXT NOT NULL DEFAULT ''",
        'aa_sequence': "TEXT NOT NULL DEFAULT ''",
    },
    'drug': {
        'pubchem_cid': "TEXT DEFAULT ''",
        'pubchem_url': "TEXT DEFAULT ''",
        'description': "TEXT DEFAULT ''",
        'structure_url': "TEXT DEFAULT ''",
    },
    'resistance_rule': {
        'reference_identifier': "TEXT DEFAULT ''",
        'reference': "TEXT DEFAULT ''",
        'phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'clinical_phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'ic50': "TEXT DEFAULT ''",
        'publication': "TEXT DEFAULT ''",
        'source': "TEXT DEFAULT ''",
    },
    'resistance_rule_set': {
        'phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'clinical_phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'ic50': "TEXT DEFAULT ''",
        'publication': "TEXT DEFAULT ''",
        'source': "TEXT DEFAULT ''",
        'group_name': "TEXT DEFAULT ''",
    },
    'resistance_rule_set_member': {
        'reference_identifier': "TEXT DEFAULT ''",
        'reference': "TEXT DEFAULT ''",
    },
    'query_reference': {
        'created_at': "TEXT DEFAULT ''",
    },
}


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA foreign_keys=ON')
    conn.row_factory = sqlite3.Row


def create_schema(db_path: Path) -> sqlite3.Connection:
    """Create a new project database with the full schema.

    Returns the open connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA journal_mode=WAL')
    _configure_connection(conn)
    conn.executescript(PROJECT_SCHEMA_SQL)
    conn.commit()
    return conn


def init_results_db(db_path: Path) -> sqlite3.Connection:
    """
    Create or validate a results database.

    If ``db_path`` does not exist, a fresh results schema is created.
    If it already exists, schema overlap with the expected internal structure is
    validated and no schema mutation is attempted.

    :param db_path: path to the results database
    :return: open SQLite connection
    """
    if db_path.exists() and not db_path.is_file():
        raise ValueError(f'Results database path is not a file: {db_path}')

    existed = db_path.is_file()
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)

    if not existed:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(RESULTS_SCHEMA_SQL)
        conn.execute(
            'INSERT OR REPLACE INTO results_meta (key, value) VALUES (?, ?)',
            ('results_schema_version', str(RESULTS_SCHEMA_VERSION)),
        )
        conn.commit()
        return conn

    _validate_results_schema_overlap(conn, db_path)
    if _add_missing_optional_columns(conn, _OPTIONAL_RESULTS_COLUMN_DEFS):
        conn.commit()
    return conn


def open_results_db(db_path: Path) -> sqlite3.Connection:
    """
    Open and validate an existing results database.

    :param db_path: path to existing results database
    :return: open SQLite connection
    """
    if not db_path.is_file():
        raise FileNotFoundError(f'Results database not found: {db_path}')

    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    _validate_results_schema_overlap(conn, db_path)
    if _add_missing_optional_columns(conn, _OPTIONAL_RESULTS_COLUMN_DEFS):
        conn.commit()
    return conn


def _validate_results_schema_overlap(conn: sqlite3.Connection, db_path: Path) -> None:
    """Validate that an existing results DB contains required tables/columns."""
    _validate_required_schema_overlap(
        conn,
        db_path,
        required_columns=_REQUIRED_RESULTS_COLUMNS,
        label='Results database',
    )


def _validate_project_schema_overlap(conn: sqlite3.Connection, db_path: Path) -> None:
    """Validate that an existing project DB contains required tables/columns."""
    _validate_required_schema_overlap(
        conn,
        db_path,
        required_columns=_REQUIRED_PROJECT_COLUMNS,
        label='Project database',
    )


def _validate_required_schema_overlap(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    required_columns: dict[str, set[str]],
    label: str,
) -> None:
    """Validate table/column presence for required schema contract."""
    existing_tables = {
        row['name']
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table_name, expected_columns in required_columns.items():
        if table_name not in existing_tables:
            missing_tables.append(table_name)
            continue

        available_columns = {
            row['name']
            for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()
        }
        missing = sorted(expected_columns - available_columns)
        if missing:
            missing_columns.append(f'{table_name}: {", ".join(missing)}')

    if not missing_tables and not missing_columns:
        return

    errors: list[str] = []
    if missing_tables:
        errors.append('missing tables: ' + ', '.join(sorted(missing_tables)))
    if missing_columns:
        errors.append('missing columns: ' + '; '.join(missing_columns))

    details = '\n- '.join(errors)
    raise ValueError(f'{label} schema mismatch for {db_path}:\n- {details}')


def _add_missing_optional_columns(
    conn: sqlite3.Connection,
    optional_column_defs: dict[str, dict[str, str]],
) -> bool:
    """Add missing optional columns with schema defaults and return whether DB changed."""
    changed = False
    existing_tables = {
        row['name']
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }

    for table_name, column_defs in optional_column_defs.items():
        if table_name not in existing_tables:
            continue

        available_columns = {
            row['name']
            for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()
        }
        for column_name, column_def in column_defs.items():
            if column_name in available_columns:
                continue
            conn.execute(
                f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}'
            )
            changed = True

    return changed


def open_project_db(db_path: Path) -> sqlite3.Connection:
    """
    Open an existing project database and validate the schema version.

    :param db_path: path to project database
    :return: SQLite connection object
    """
    if not db_path.is_file():
        raise FileNotFoundError(f'Project database not found: {db_path}')
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    _validate_project_schema_overlap(conn, db_path)
    if _add_missing_optional_columns(conn, _OPTIONAL_PROJECT_COLUMN_DEFS):
        conn.commit()
    return conn
