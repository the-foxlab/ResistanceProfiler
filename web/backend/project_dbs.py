"""Project database discovery, schema validation, UUID indexing, and resolution."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from respro.db.schema import open_project_db

logger = logging.getLogger(__name__)


def _validate_project_db(project_db: Path) -> None:
    """Ensure *project_db* exists and opens as a valid project SQLite database."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project DB not found: {project_db}')
    with contextlib.closing(open_project_db(project_db)):
        pass


def _read_project_uuid(project_db: Path) -> str:
    """Validate *project_db* and return its non-empty project UUID."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project DB not found: {project_db}')
    with contextlib.closing(open_project_db(project_db)) as conn:
        row = conn.execute('SELECT uuid FROM project LIMIT 1').fetchone()
    if row is None:
        raise ValueError(f'Project database missing project row: {project_db}')
    project_uuid = str(row['uuid'] or '').strip()
    if not project_uuid:
        raise ValueError(f'Project database missing UUID in project table: {project_db}')
    return project_uuid


def list_project_db_paths(project_databases_dir: Path) -> list[Path]:
    """Return validated project DB files sorted by filename."""
    db_paths = sorted(path for path in project_databases_dir.glob('*.db') if path.is_file())
    for db_path in db_paths:
        _validate_project_db(db_path)
    return db_paths


def build_project_db_uuid_index(project_databases_dir: Path) -> dict[str, Path]:
    """Return a ``uuid -> db_path`` mapping for all validated project databases (one open per DB)."""
    uuid_index: dict[str, Path] = {}
    for db_path in sorted(path for path in project_databases_dir.glob('*.db') if path.is_file()):
        project_uuid = _read_project_uuid(db_path)
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


def resolve_project_db_path(project_databases_dir: Path, database_id: str | None) -> Path:
    """Resolve *database_id* (or the first available DB when omitted) to a validated path."""
    db_paths = list_project_db_paths(project_databases_dir)
    if not db_paths:
        raise FileNotFoundError(
            'No project database available. Add a .db file or enable maintained bootstrap.'
        )
    if not database_id:
        return db_paths[0]
    for path in db_paths:
        if path.name == database_id:
            return path
    raise ValueError(f'Unknown database_id {database_id!r}.')


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
