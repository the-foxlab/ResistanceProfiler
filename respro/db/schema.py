"""SQLite schema definitions, creation, and read-only validation for project and results DBs."""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from respro.utils.files import require_file

PROJECT_SCHEMA_VERSION = 4
RESULTS_SCHEMA_VERSION = 1

PROJECT_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    uuid        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata_maintainers TEXT NOT NULL DEFAULT '',
    metadata_contact TEXT NOT NULL DEFAULT '',
    metadata_publication_pmid TEXT NOT NULL DEFAULT '',
    metadata_publication_doi TEXT NOT NULL DEFAULT '',
    metadata_website TEXT NOT NULL DEFAULT '',
    metadata_description TEXT NOT NULL DEFAULT '',
    metadata_maintainer_update TEXT NOT NULL DEFAULT '',
    metadata_license TEXT NOT NULL DEFAULT '',
    metadata_tsv_checksum TEXT NOT NULL DEFAULT '',
    example_fasta TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 4
);

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

CREATE TABLE IF NOT EXISTS feature (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id INTEGER NOT NULL REFERENCES reference(id),
    name        TEXT    NOT NULL,
    protein     TEXT    DEFAULT '',
    protein_id  TEXT    DEFAULT '',
    ncbi_protein_url TEXT DEFAULT '',
    locus_tag   TEXT    DEFAULT '',
    note        TEXT    DEFAULT '',
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL,
    strand      TEXT    NOT NULL DEFAULT '+',
    codon_start INTEGER NOT NULL DEFAULT 0,
    nt_sequence TEXT    NOT NULL DEFAULT '',
    aa_sequence TEXT    NOT NULL DEFAULT '',
    feature_type TEXT   NOT NULL DEFAULT 'CDS',
    parent_feature_name TEXT NOT NULL DEFAULT '',
    has_rules   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(reference_id, name)
);

CREATE TABLE IF NOT EXISTS feature_segment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id     INTEGER NOT NULL REFERENCES feature(id),
    segment_index INTEGER NOT NULL,
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL,
    UNIQUE(feature_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_feature_segment_feature ON feature_segment(feature_id);

CREATE TABLE IF NOT EXISTS drug (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id),
    name        TEXT    NOT NULL,
    alias       TEXT    DEFAULT '',
    pubchem_cid TEXT    DEFAULT '',
    pubchem_url TEXT    DEFAULT '',
    description TEXT    DEFAULT '',
    structure_url TEXT    DEFAULT '',
    pubchem_lookup_failed INTEGER DEFAULT 0,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS resistance_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id     INTEGER NOT NULL REFERENCES feature(id),
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    external_id TEXT    NOT NULL DEFAULT '',
    reference_identifier TEXT DEFAULT '',
    position    INTEGER NOT NULL,
    reference   TEXT    DEFAULT '',
    mutation    TEXT    NOT NULL,
    phenotype   TEXT    NOT NULL DEFAULT '',
    clinical_phenotype TEXT NOT NULL DEFAULT '',
    ic50        TEXT    DEFAULT '',
    fold_ic50   TEXT    DEFAULT '',
    score       TEXT    DEFAULT '',
    publication TEXT    DEFAULT '',
    source      TEXT    DEFAULT '',
    comment     TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rule_feature_pos ON resistance_rule(feature_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_external_id_unique
ON resistance_rule(external_id)
WHERE external_id != '';

CREATE TABLE IF NOT EXISTS resistance_formula_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    formula_id  TEXT    NOT NULL,
    label       TEXT    DEFAULT '',
    normalized_expression TEXT NOT NULL,
    phenotype   TEXT    NOT NULL DEFAULT '',
    clinical_phenotype TEXT NOT NULL DEFAULT '',
    ic50        TEXT    DEFAULT '',
    fold_ic50   TEXT    DEFAULT '',
    score       TEXT    DEFAULT '',
    source      TEXT    DEFAULT '',
    comment     TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resistance_formula_rule_member (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_rule_id INTEGER NOT NULL REFERENCES resistance_formula_rule(id),
    rule_id         INTEGER NOT NULL REFERENCES resistance_rule(id),
    UNIQUE(formula_rule_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_formula_rule_drug ON resistance_formula_rule(drug_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_formula_rule_id_unique
ON resistance_formula_rule(formula_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_formula_rule_expression_unique
ON resistance_formula_rule(drug_id, normalized_expression);
CREATE INDEX IF NOT EXISTS idx_formula_rule_member_formula
ON resistance_formula_rule_member(formula_rule_id);

CREATE TABLE IF NOT EXISTS publication (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doi         TEXT    NOT NULL DEFAULT '',
    title       TEXT    NOT NULL DEFAULT '',
    pubmed_id   TEXT    NOT NULL DEFAULT '',
    raw_input   TEXT    NOT NULL DEFAULT '',
    first_author TEXT   NOT NULL DEFAULT '',
    year        TEXT    NOT NULL DEFAULT '',
    journal     TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rule_publication (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id        INTEGER NOT NULL REFERENCES resistance_rule(id),
    publication_id INTEGER NOT NULL REFERENCES publication(id),
    UNIQUE(rule_id, publication_id)
);
CREATE INDEX IF NOT EXISTS idx_rule_pub ON rule_publication(rule_id);

CREATE TABLE IF NOT EXISTS resistance_formula_rule_publication (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_rule_id INTEGER NOT NULL REFERENCES resistance_formula_rule(id),
    publication_id  INTEGER NOT NULL REFERENCES publication(id),
    UNIQUE(formula_rule_id, publication_id)
);
CREATE INDEX IF NOT EXISTS idx_resistance_formula_rule_pub ON resistance_formula_rule_publication(formula_rule_id);

-- Privacy-minimal query cache: persists only sequence checksum + CIGAR mappings (no FASTA header or sequence).
CREATE TABLE IF NOT EXISTS query_reference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    checksum    TEXT    NOT NULL,
    UNIQUE(checksum)
);

CREATE TABLE IF NOT EXISTS query_feature_mapping (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query_ref_id    INTEGER NOT NULL REFERENCES query_reference(id),
    feature_id         INTEGER NOT NULL REFERENCES feature(id),
    identity        REAL    NOT NULL,
    cds_coverage    REAL    NOT NULL DEFAULT 0,
    query_coverage  REAL    NOT NULL DEFAULT 0,
    cds_start       INTEGER NOT NULL DEFAULT 0,
    query_start     INTEGER NOT NULL,
    query_end       INTEGER NOT NULL,
    strand          TEXT    NOT NULL DEFAULT '+',
    cigar           TEXT    NOT NULL,
    intron_intervals TEXT   NOT NULL DEFAULT '[]',
    UNIQUE(query_ref_id, feature_id)
);

CREATE TABLE IF NOT EXISTS interpretation_algorithm (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES project(id),
    algorithm_name TEXT    NOT NULL,
    config_json    TEXT    NOT NULL DEFAULT '{}'
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
    project_fingerprint TEXT DEFAULT '',
    project_updated_at  TEXT DEFAULT '',
    reference_name  TEXT    NOT NULL,
    sample_name     TEXT    DEFAULT '',
    vcf_path        TEXT    NOT NULL,
    total_variants  INTEGER NOT NULL DEFAULT 0,
    variants_in_cds INTEGER NOT NULL DEFAULT 0,
    resistance_hits INTEGER NOT NULL DEFAULT 0,
    formula_hits    INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'complete',
    is_fasta_mode   INTEGER NOT NULL DEFAULT 0,
    coverage_assessed INTEGER NOT NULL DEFAULT 0,
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
    feature_name   TEXT    DEFAULT '',
    reference_name TEXT    DEFAULT '',
    codon_pos   INTEGER,
    ref_codon   TEXT    DEFAULT '',
    alt_codon   TEXT    DEFAULT '',
    ref_aa      TEXT    DEFAULT '',
    alt_aa      TEXT    DEFAULT '',
    consequence TEXT    DEFAULT '',
    af_bin      TEXT    DEFAULT '',
    rule_match  INTEGER DEFAULT 0,
    drug_hits   TEXT    DEFAULT '[]',
    is_combined_codon_event INTEGER DEFAULT 0,
    combined_member_count   INTEGER DEFAULT 1,
    combined_states         TEXT    DEFAULT '[]',
    single_exchange_aa_freq REAL    DEFAULT 0.0,
    rule_effect_aa_freq     TEXT    DEFAULT '{}',
    rule_effect_alt         TEXT    DEFAULT '{}',
    freq_method             TEXT    DEFAULT 'observed',
    variant_source          TEXT    DEFAULT '',
    haplotype_links         TEXT    DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_vr_run ON variant_result(run_id);
CREATE INDEX IF NOT EXISTS idx_vr_feature ON variant_result(feature_name, codon_pos);

CREATE TABLE IF NOT EXISTS coverage_gap (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    feature_name   TEXT    NOT NULL,
    reference_name TEXT    DEFAULT '',
    chrom          TEXT    DEFAULT '',
    codon_start INTEGER NOT NULL,
    codon_end   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cg_run ON coverage_gap(run_id);

CREATE TABLE IF NOT EXISTS formula_rule_hit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    reference_name TEXT DEFAULT '',
    hit_json    TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_frh_run ON formula_rule_hit(run_id);

CREATE TABLE IF NOT EXISTS sample_classification (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES run(id),
    drug                TEXT    DEFAULT '',
    phenotype           TEXT    NOT NULL DEFAULT '',
    clinical_phenotype  TEXT    NOT NULL DEFAULT '',
    ic50                TEXT    DEFAULT '',
    fold_ic50           TEXT    DEFAULT '',
    note                TEXT    DEFAULT '',
    source              TEXT    DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sc_run ON sample_classification(run_id);

CREATE TABLE IF NOT EXISTS profiled_feature (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES run(id),
    reference_name  TEXT    NOT NULL DEFAULT '',
    feature_name    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pf_run ON profiled_feature(run_id);
"""

_SQL_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _quote_sql_identifier(identifier: str, *, kind: str) -> str:
    """Validate and quote an SQL identifier for safe PRAGMA interpolation."""
    if not _SQL_IDENTIFIER_RE.match(identifier):
        raise ValueError(f'Invalid SQL {kind} identifier: {identifier!r}')
    return f'"{identifier}"'


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA foreign_keys=ON')
    conn.row_factory = sqlite3.Row


@lru_cache(maxsize=2)
def _expected_schema_columns(schema_sql: str) -> dict[str, frozenset[str]]:
    """Derive the canonical ``{table: columns}`` contract directly from DDL."""
    with sqlite3.connect(':memory:') as mem:
        mem.executescript(schema_sql)
        tables = [
            row[0]
            for row in mem.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        return {
            table: frozenset(row[1] for row in mem.execute(f'PRAGMA table_info("{table}")').fetchall())
            for table in tables
        }


def _validate_schema(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    schema_sql: str,
    label: str,
) -> None:
    """Verify an existing SQLite database contains every expected table and column."""
    expected = _expected_schema_columns(schema_sql)
    existing_tables = {
        row['name']
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table_name, expected_columns in expected.items():
        if table_name not in existing_tables:
            missing_tables.append(table_name)
            continue
        table_id = _quote_sql_identifier(table_name, kind='table')
        available = {row['name'] for row in conn.execute(f'PRAGMA table_info({table_id})').fetchall()}
        missing = sorted(expected_columns - available)
        if missing:
            missing_columns.append(f'{table_name}: {", ".join(missing)}')

    if not missing_tables and not missing_columns:
        return

    errors: list[str] = []
    if missing_tables:
        errors.append('missing tables: ' + ', '.join(sorted(missing_tables)))
    if missing_columns:
        errors.append('missing columns: ' + '; '.join(missing_columns))
    raise ValueError(f'{label} schema mismatch for {db_path}:\n- ' + '\n- '.join(errors))


def create_schema(db_path: Path) -> sqlite3.Connection:
    """Create a new project database with the canonical schema and return its connection."""
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA journal_mode=WAL')
    _configure_connection(conn)
    conn.executescript(PROJECT_SCHEMA_SQL)
    conn.commit()
    return conn


def open_project_db(db_path: Path) -> sqlite3.Connection:
    """Open an existing project database and validate its schema without mutating it."""
    require_file(db_path, 'Project database')
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    try:
        _validate_schema(conn, db_path, schema_sql=PROJECT_SCHEMA_SQL, label='Project database')
    except Exception:
        conn.close()
        raise
    return conn


def init_results_db(db_path: Path) -> sqlite3.Connection:
    """Create a fresh results database, or open and validate an existing one."""
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

    try:
        _validate_schema(conn, db_path, schema_sql=RESULTS_SCHEMA_SQL, label='Results database')
    except Exception:
        conn.close()
        raise
    return conn


def open_results_db(db_path: Path) -> sqlite3.Connection:
    """Open an existing results database and validate its schema without mutating it."""
    require_file(db_path, 'Results database')
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    try:
        _validate_schema(conn, db_path, schema_sql=RESULTS_SCHEMA_SQL, label='Results database')
    except Exception:
        conn.close()
        raise
    return conn
