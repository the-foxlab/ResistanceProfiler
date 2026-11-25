"""Project database discovery, schema validation, UUID indexing, and resolution."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from respro.db.schema import PROJECT_SCHEMA_SQL, _configure_connection, _validate_schema

logger = logging.getLogger(__name__)


def _validate_project_row(conn: sqlite3.Connection) -> None:
    """Ensure the database contains a ``project`` row with a non-empty ``uuid``."""
    row = conn.execute('SELECT uuid FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Project database contains no project metadata.')
    if not str(row['uuid'] or '').strip():
        raise ValueError('Project database has an empty project UUID.')


def open_project_db_readonly(project_db: Path) -> sqlite3.Connection:
    """Open an existing project database in SQLite read-only mode and validate its schema and metadata."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project database not found: {project_db.name}')
    resolved = project_db.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f'Project database not found: {project_db.name}')
    conn = sqlite3.connect(f'{resolved.as_uri()}?mode=ro', uri=True)
    try:
        _configure_connection(conn)
        _validate_schema(conn, resolved, schema_sql=PROJECT_SCHEMA_SQL, label='Project database')
        _validate_project_row(conn)
    except Exception:
        conn.close()
        raise
    return conn


def validate_project_db_filename(database_id: str) -> str:
    """Validate that *database_id* is a single safe filename without path separators or traversal."""
    if (
        not database_id
        or not database_id.strip()
        or '/' in database_id
        or '\\' in database_id
        or '\x00' in database_id
        or database_id in {'.', '..'}
    ):
        raise ValueError(f'Unknown database_id {database_id!r}.')
    return database_id


def iter_valid_project_dbs(project_databases_dir: Path) -> Iterator[tuple[Path, sqlite3.Connection]]:
    """Yield ``(db_path, conn)`` once per valid ``*.db`` file, skipping corrupt or legacy DBs."""
    if not project_databases_dir.is_dir():
        return
    for db_path in sorted(path for path in project_databases_dir.glob('*.db') if path.is_file()):
        try:
            conn = open_project_db_readonly(db_path)
        except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
            logger.warning('Skipping invalid project database %s: %s', db_path.name, exc)
            continue
        with contextlib.closing(conn):
            yield db_path, conn


def list_project_db_paths(project_databases_dir: Path) -> list[Path]:
    """Return validated project DB files sorted by filename, skipping invalid DBs."""
    return [db_path for db_path, _ in iter_valid_project_dbs(project_databases_dir)]


def build_project_db_uuid_index(project_databases_dir: Path) -> dict[str, Path]:
    """Return a ``uuid -> db_path`` mapping for all validated project databases (one open per DB)."""
    uuid_index: dict[str, Path] = {}
    for db_path, conn in iter_valid_project_dbs(project_databases_dir):
        try:
            row = conn.execute('SELECT uuid FROM project ORDER BY id LIMIT 1').fetchone()
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Skipping unreadable project database %s: %s', db_path.name, exc)
            continue
        if row is None:
            logger.warning('Skipping project database missing project row: %s', db_path.name)
            continue
        project_uuid = str(row['uuid'] or '').strip()
        if not project_uuid:
            logger.warning('Skipping project database missing UUID in project table: %s', db_path.name)
            continue
        if project_uuid in uuid_index:
            logger.warning(
                'Duplicate project database UUID detected: uuid=%s keeping=%s ignoring=%s',
                project_uuid,
                uuid_index[project_uuid].name,
                db_path.name,
            )
            continue
        uuid_index[project_uuid] = db_path
    return uuid_index


def open_resolved_project_db_readonly(
    project_databases_dir: Path,
    database_id: str | None,
) -> tuple[Path, sqlite3.Connection]:
    """Resolve *database_id* (or first valid DB) and return ``(path, open_ro_conn)``."""
    if not database_id:
        if project_databases_dir.is_dir():
            for db_path in sorted(path for path in project_databases_dir.glob('*.db') if path.is_file()):
                try:
                    return db_path, open_project_db_readonly(db_path)
                except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
                    logger.warning('Skipping invalid project database %s: %s', db_path.name, exc)
                    continue
        raise FileNotFoundError(
            'No project database available. Add a .db file or enable maintained bootstrap.'
        )

    validate_project_db_filename(database_id)
    candidate = (
        next((p for p in project_databases_dir.glob('*.db') if p.is_file() and p.name == database_id), None)
        if project_databases_dir.is_dir()
        else None
    )
    if candidate is None:
        raise ValueError(f'Unknown database_id {database_id!r}.')

    try:
        return candidate, open_project_db_readonly(candidate)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        logger.warning('Requested project database %s is invalid: %s', database_id, exc)
        raise ValueError(f'Project database {database_id!r} is invalid or corrupt.') from exc


def resolve_project_db_path(project_databases_dir: Path, database_id: str | None) -> Path:
    """Resolve *database_id* (or the first available DB when omitted) to a validated path."""
    db_path, conn = open_resolved_project_db_readonly(project_databases_dir, database_id)
    conn.close()
    return db_path


def resolve_regenerate_project_db_path(
    project_databases_dir: Path,
    project_db_uuid_index: dict[str, Path],
    *,
    project_fingerprint: str,
    fallback_database_id: str | None,
) -> Path:
    """Resolve project DB for regenerate requests via ``project_fingerprint`` or fallback ID."""
    fingerprint = project_fingerprint.strip()
    if fingerprint:
        project_db = project_db_uuid_index.get(fingerprint)
        if project_db is None:
            raise ValueError(f'No project database found for JSON project_fingerprint {fingerprint!r}.')
        return project_db
    return resolve_project_db_path(project_databases_dir, fallback_database_id)
