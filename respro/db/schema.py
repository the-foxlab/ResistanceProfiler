"""
SQLite schema creation and validation for ResistanceProfiler databases.
"""

import re
import sqlite3
import uuid
from pathlib import Path

from respro.utils.files import require_file

PROJECT_SCHEMA_VERSION = 1
RESULTS_SCHEMA_VERSION = 1

PROJECT_SCHEMA_SQL = """\
-- Project metadata
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

-- features / ORFs / CDS
CREATE TABLE IF NOT EXISTS feature (
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
    feature_type TEXT   NOT NULL DEFAULT 'CDS',
    parent_feature_name TEXT NOT NULL DEFAULT '',
    UNIQUE(reference_id, name)
);

CREATE TABLE IF NOT EXISTS feature_segment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id     INTEGER NOT NULL REFERENCES feature(id),
    segment_index INTEGER NOT NULL,
    start       INTEGER NOT NULL,  -- 0-based inclusive
    end         INTEGER NOT NULL,  -- 0-based exclusive
    UNIQUE(feature_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_feature_segment_feature ON feature_segment(feature_id);

-- Drugs
CREATE TABLE IF NOT EXISTS drug (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id),
    name        TEXT    NOT NULL,
    alias       TEXT    DEFAULT '',
    pubchem_cid TEXT    DEFAULT '',  -- PubChem compound ID (integer stored as text)
    pubchem_url TEXT    DEFAULT '',  -- canonical PubChem compound URL
    description TEXT    DEFAULT '',  -- short human-readable description from PubChem
    structure_url TEXT    DEFAULT '',  -- 2D structure image URL from PubChem
    UNIQUE(project_id, name)
);

-- Resistance rules
CREATE TABLE IF NOT EXISTS resistance_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id     INTEGER NOT NULL REFERENCES feature(id),
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    external_id TEXT    NOT NULL DEFAULT '',
    reference_identifier TEXT DEFAULT '',
    position    INTEGER NOT NULL,  -- 0-based AA position within feature
    reference   TEXT    DEFAULT '',
    mutation    TEXT    NOT NULL,
    phenotype   TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype TEXT NOT NULL DEFAULT 'unknown',
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
    phenotype   TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype TEXT NOT NULL DEFAULT 'unknown',
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

-- Publications (deduped; doi is the natural key when available)
CREATE TABLE IF NOT EXISTS publication (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doi         TEXT    NOT NULL DEFAULT '',
    title       TEXT    NOT NULL DEFAULT '',
    pubmed_id   TEXT    NOT NULL DEFAULT '',
    raw_input   TEXT    NOT NULL DEFAULT ''
);

-- Link tables between rules/rule-sets and publications
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
    UNIQUE(query_ref_id, feature_id)
);

-- Interpretation algorithms (non-mutually-exclusive; stored as JSON config blobs)
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
CREATE INDEX IF NOT EXISTS idx_vr_feature ON variant_result(feature_name, codon_pos);

CREATE TABLE IF NOT EXISTS coverage_gap (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    feature_name   TEXT    NOT NULL,
    codon_start INTEGER NOT NULL,
    codon_end   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cg_run ON coverage_gap(run_id);

CREATE TABLE IF NOT EXISTS formula_rule_hit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    hit_json    TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_frh_run ON formula_rule_hit(run_id);

CREATE TABLE IF NOT EXISTS sample_classification (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES run(id),
    drug                TEXT    DEFAULT '',
    phenotype           TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype  TEXT    NOT NULL DEFAULT 'unknown',
    ic50                TEXT    DEFAULT '',
    fold_ic50           TEXT    DEFAULT '',
    note                TEXT    DEFAULT '',
    source              TEXT    DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sc_run ON sample_classification(run_id);
"""


_REQUIRED_RESULTS_COLUMNS = {
    'results_meta': {'key', 'value'},
    'run': {'id', 'project_name', 'project_db_path', 'reference_name', 'vcf_path'},
    'variant_result': {'id', 'run_id', 'chrom', 'pos', 'ref', 'alt'},
}

_RESULTS_OPTIONAL_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS coverage_gap (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    feature_name   TEXT    NOT NULL,
    codon_start INTEGER NOT NULL,
    codon_end   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cg_run ON coverage_gap(run_id);

CREATE TABLE IF NOT EXISTS formula_rule_hit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    hit_json    TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_frh_run ON formula_rule_hit(run_id);

CREATE TABLE IF NOT EXISTS sample_classification (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES run(id),
    drug                TEXT    DEFAULT '',
    phenotype           TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype  TEXT    NOT NULL DEFAULT 'unknown',
    ic50                TEXT    DEFAULT '',
    fold_ic50           TEXT    DEFAULT '',
    note                TEXT    DEFAULT '',
    source              TEXT    DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sc_run ON sample_classification(run_id);
"""

_OPTIONAL_RESULTS_COLUMN_DEFS = {
    'run': {
        'project_fingerprint': "TEXT DEFAULT ''",
        'project_updated_at': "TEXT DEFAULT ''",
        'sample_name': "TEXT DEFAULT ''",
        'total_variants': 'INTEGER NOT NULL DEFAULT 0',
        'variants_in_cds': 'INTEGER NOT NULL DEFAULT 0',
        'resistance_hits': 'INTEGER NOT NULL DEFAULT 0',
        'formula_hits': 'INTEGER NOT NULL DEFAULT 0',
        'status': "TEXT NOT NULL DEFAULT 'complete'",
        'created_at': "TEXT DEFAULT ''",
    },
    'variant_result': {
        'allele_freq': 'REAL',
        'depth': 'INTEGER',
        'feature_name': "TEXT DEFAULT ''",
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
    'feature': {'id', 'reference_id', 'name', 'start', 'end', 'strand'},
    'feature_segment': {'id', 'feature_id', 'segment_index', 'start', 'end'},
    'drug': {'id', 'project_id', 'name'},
    'resistance_rule': {'id', 'feature_id', 'drug_id', 'position', 'mutation'},
    'query_reference': {'id', 'name', 'sequence', 'length', 'checksum'},
    'query_feature_mapping': {
        'id', 'query_ref_id', 'feature_id', 'identity', 'cds_coverage',
        'query_start', 'query_end', 'strand', 'cigar',
    },
    'publication': {'id', 'doi', 'title', 'pubmed_id', 'raw_input'},
    'rule_publication': {'id', 'rule_id', 'publication_id'},
}

_OPTIONAL_PROJECT_COLUMN_DEFS = {
    'project': {
        'uuid': "TEXT NOT NULL DEFAULT ''",
        # Use static default for ALTER TABLE compatibility (no datetime() in ADD COLUMN).
        'updated_at': "TEXT NOT NULL DEFAULT ''",
        'metadata_maintainers': "TEXT NOT NULL DEFAULT ''",
        'metadata_contact': "TEXT NOT NULL DEFAULT ''",
        'metadata_publication_pmid': "TEXT NOT NULL DEFAULT ''",
        'metadata_publication_doi': "TEXT NOT NULL DEFAULT ''",
        'metadata_website': "TEXT NOT NULL DEFAULT ''",
        'metadata_description': "TEXT NOT NULL DEFAULT ''",
        'metadata_maintainer_update': "TEXT NOT NULL DEFAULT ''",
        'metadata_license': "TEXT NOT NULL DEFAULT ''",
        'metadata_tsv_checksum': "TEXT NOT NULL DEFAULT ''",
    },
    'reference': {
        'accession': "TEXT DEFAULT ''",
        'organism': "TEXT DEFAULT ''",
        'taxonomy': "TEXT DEFAULT ''",
    },
    'feature': {
        'protein': "TEXT DEFAULT ''",
        'protein_id': "TEXT DEFAULT ''",
        'ncbi_protein_url': "TEXT DEFAULT ''",
        'locus_tag': "TEXT DEFAULT ''",
        'note': "TEXT DEFAULT ''",
        'codon_start': 'INTEGER NOT NULL DEFAULT 0',
        'nt_sequence': "TEXT NOT NULL DEFAULT ''",
        'aa_sequence': "TEXT NOT NULL DEFAULT ''",
        'feature_type': "TEXT NOT NULL DEFAULT 'CDS'",
        'parent_feature_name': "TEXT NOT NULL DEFAULT ''",
    },
    'drug': {
        'alias': "TEXT DEFAULT ''",
        'pubchem_cid': "TEXT DEFAULT ''",
        'pubchem_url': "TEXT DEFAULT ''",
        'description': "TEXT DEFAULT ''",
        'structure_url': "TEXT DEFAULT ''",
    },
    'resistance_rule': {
        'external_id': "TEXT NOT NULL DEFAULT ''",
        'reference_identifier': "TEXT DEFAULT ''",
        'reference': "TEXT DEFAULT ''",
        'phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'clinical_phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'ic50': "TEXT DEFAULT ''",
        'fold_ic50': "TEXT DEFAULT ''",
        'score': "TEXT DEFAULT ''",
        'publication': "TEXT DEFAULT ''",
        'source': "TEXT DEFAULT ''",
        'comment': "TEXT DEFAULT ''",
    },
    'resistance_rule_set': {
        'phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'clinical_phenotype': "TEXT NOT NULL DEFAULT 'unknown'",
        'ic50': "TEXT DEFAULT ''",
        'fold_ic50': "TEXT DEFAULT ''",
        'publication': "TEXT DEFAULT ''",
        'source': "TEXT DEFAULT ''",
        'group_name': "TEXT DEFAULT ''",
        'comment': "TEXT DEFAULT ''",
    },
    'resistance_rule_set_member': {
        'reference_identifier': "TEXT DEFAULT ''",
        'reference': "TEXT DEFAULT ''",
    },
    'query_reference': {
        'created_at': "TEXT DEFAULT ''",
    },
    'query_feature_mapping': {
        'query_coverage': 'REAL NOT NULL DEFAULT 0',
        'cds_start': 'INTEGER NOT NULL DEFAULT 0',
    },
}

_SQL_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _quote_sql_identifier(identifier: str, *, kind: str) -> str:
    """Validate and quote an SQL identifier for safe interpolation."""
    if not _SQL_IDENTIFIER_RE.match(identifier):
        raise ValueError(f'Invalid SQL {kind} identifier: {identifier!r}')
    return f'"{identifier}"'


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
    conn.executescript(_RESULTS_OPTIONAL_TABLES_SQL)
    if _add_missing_optional_columns(conn, _OPTIONAL_RESULTS_COLUMN_DEFS):
        conn.commit()
    return conn


def open_results_db(db_path: Path) -> sqlite3.Connection:
    """
    Open and validate an existing results database.

    :param db_path: path to existing results database
    :return: open SQLite connection
    """
    require_file(db_path, 'Results database')
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    _validate_results_schema_overlap(conn, db_path)
    conn.executescript(_RESULTS_OPTIONAL_TABLES_SQL)
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
        table_identifier = _quote_sql_identifier(table_name, kind='table')
        if table_name not in existing_tables:
            missing_tables.append(table_name)
            continue

        available_columns = {
            row['name']
            for row in conn.execute(f'PRAGMA table_info({table_identifier})').fetchall()
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
        table_identifier = _quote_sql_identifier(table_name, kind='table')
        if table_name not in existing_tables:
            continue

        available_columns = {
            row['name']
            for row in conn.execute(f'PRAGMA table_info({table_identifier})').fetchall()
        }
        for column_name, column_def in column_defs.items():
            column_identifier = _quote_sql_identifier(column_name, kind='column')
            if column_name in available_columns:
                continue
            conn.execute(
                f'ALTER TABLE {table_identifier} ADD COLUMN {column_identifier} {column_def}'
            )
            changed = True

    return changed


def _ensure_project_indexes(conn: sqlite3.Connection) -> None:
    """Create project indexes that depend on optional columns when prerequisites exist."""
    existing_tables = {
        row['name']
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if 'resistance_rule' not in existing_tables:
        return

    available_columns = {
        row['name']
        for row in conn.execute('PRAGMA table_info(resistance_rule)').fetchall()
    }
    if 'external_id' not in available_columns:
        return

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_external_id_unique "
        "ON resistance_rule(external_id) WHERE external_id != ''"
    )


def open_project_db(db_path: Path) -> sqlite3.Connection:
    """
    Open an existing project database and validate the schema version.

    :param db_path: path to project database
    :return: SQLite connection object
    """
    require_file(db_path, 'Project database')
    conn = sqlite3.connect(str(db_path))
    _configure_connection(conn)
    _ensure_optional_tables(conn)
    _validate_project_schema_overlap(conn, db_path)
    changed = False
    if _add_missing_optional_columns(conn, _OPTIONAL_PROJECT_COLUMN_DEFS):
        changed = True
    if _backfill_feature_segments(conn):
        changed = True
    if changed:
        _ensure_project_indexes(conn)
        conn.commit()
    else:
        _ensure_project_indexes(conn)
    _ensure_project_uuid(conn)
    return conn


def _ensure_optional_tables(conn: sqlite3.Connection) -> None:
    """
    Create publication join tables if they are absent from an existing project database.

    Safe to call on new databases (uses CREATE TABLE IF NOT EXISTS).
    """
    conn.executescript("""\
CREATE TABLE IF NOT EXISTS publication (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doi        TEXT    NOT NULL DEFAULT '',
    title      TEXT    NOT NULL DEFAULT '',
    pubmed_id  TEXT    NOT NULL DEFAULT '',
    raw_input  TEXT    NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS rule_publication (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id        INTEGER NOT NULL REFERENCES resistance_rule(id),
    publication_id INTEGER NOT NULL REFERENCES publication(id),
    UNIQUE(rule_id, publication_id)
);
CREATE INDEX IF NOT EXISTS idx_rule_pub ON rule_publication(rule_id);
CREATE TABLE IF NOT EXISTS resistance_formula_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id     INTEGER NOT NULL REFERENCES drug(id),
    formula_id  TEXT    NOT NULL,
    label       TEXT    DEFAULT '',
    normalized_expression TEXT NOT NULL,
    phenotype   TEXT    NOT NULL DEFAULT 'unknown',
    clinical_phenotype TEXT NOT NULL DEFAULT 'unknown',
    ic50        TEXT    DEFAULT '',
    fold_ic50   TEXT    DEFAULT '',
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
CREATE TABLE IF NOT EXISTS resistance_formula_rule_publication (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_rule_id INTEGER NOT NULL REFERENCES resistance_formula_rule(id),
    publication_id  INTEGER NOT NULL REFERENCES publication(id),
    UNIQUE(formula_rule_id, publication_id)
);
CREATE INDEX IF NOT EXISTS idx_resistance_formula_rule_pub ON resistance_formula_rule_publication(formula_rule_id);
CREATE TABLE IF NOT EXISTS interpretation_algorithm (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES project(id),
    algorithm_name TEXT    NOT NULL,
    config_json    TEXT    NOT NULL DEFAULT '{}'
);
""")
    conn.commit()
    _drop_interpretation_algorithm_unique_constraint(conn)


def _drop_interpretation_algorithm_unique_constraint(conn: sqlite3.Connection) -> None:
    """
    Migrate the interpretation_algorithm table to remove the UNIQUE(project_id, algorithm_name) constraint.

    This allows multiple rows with algorithm_name='drug_interpretation' (different methods).
    SQLite does not support dropping constraints directly, so we recreate the table.
    """
    # Check if the unique constraint still exists
    try:
        table_info = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='interpretation_algorithm'"
        ).fetchone()
    except sqlite3.Error:
        return

    if not table_info or 'UNIQUE(project_id, algorithm_name)' not in (table_info['sql'] or ''):
        return

    # Recreate without the unique constraint
    conn.executescript("""\
CREATE TABLE IF NOT EXISTS interpretation_algorithm_v2 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES project(id),
    algorithm_name TEXT    NOT NULL,
    config_json    TEXT    NOT NULL DEFAULT '{}'
);
INSERT INTO interpretation_algorithm_v2 (id, project_id, algorithm_name, config_json)
    SELECT id, project_id, algorithm_name, config_json FROM interpretation_algorithm;
DROP TABLE interpretation_algorithm;
ALTER TABLE interpretation_algorithm_v2 RENAME TO interpretation_algorithm;
""")
    conn.commit()


def _backfill_feature_segments(conn: sqlite3.Connection) -> bool:
    """Ensure every existing feature has at least one feature_segment row."""
    rows = conn.execute(
        'SELECT g.id, g.start, g.end FROM feature g '
        'LEFT JOIN feature_segment gs ON gs.feature_id = g.id '
        'GROUP BY g.id HAVING COUNT(gs.id) = 0'
    ).fetchall()
    if not rows:
        return False

    conn.executemany(
        'INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (?, 0, ?, ?)',
        [(int(row['id']), int(row['start']), int(row['end'])) for row in rows],
    )
    return True


def _ensure_project_uuid(conn: sqlite3.Connection) -> None:
    """Assign a UUID to any project row that does not yet have one (one-time migration)."""
    rows = conn.execute("SELECT id FROM project WHERE uuid = '' OR uuid IS NULL").fetchall()
    for row in rows:
        conn.execute('UPDATE project SET uuid = ? WHERE id = ?', (str(uuid.uuid4()), row['id']))
    if rows:
        conn.commit()

