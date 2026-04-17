"""Startup configuration and validation for the web backend."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from respro.db.schema import init_results_db, open_project_db


@dataclass(frozen=True)
class StartupConfig:
    """Validated startup configuration shared by API routes and workers."""

    project_db: Path
    results_db: Path
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    api_token: str

    @property
    def output_dir(self) -> Path:
        """Alias kept for backwards compatibility with routes that use output_dir."""
        return self.data_dir


def load_startup_config() -> StartupConfig:
    """
    Load, validate, and return backend startup configuration.

    All paths default to ``RESPRO_WEB_DATA_DIR`` (default: ``data/`` next to the
    repository root, or ``/data`` when that exists — typical for Docker mounts).
    Individual paths can still be overridden via their own environment variables.
    """
    repo_data = Path('/data') if Path('/data').is_dir() else Path(__file__).resolve().parents[2] / 'data'
    data_dir = Path(os.getenv('RESPRO_WEB_DATA_DIR', str(repo_data))).expanduser().resolve()

    project_db = Path(os.getenv('RESPRO_WEB_PROJECT_DB', str(data_dir / 'project.db'))).expanduser().resolve()
    results_db = Path(os.getenv('RESPRO_WEB_RESULTS_DB', str(data_dir / 'results.db'))).expanduser().resolve()
    api_token = os.getenv('RESPRO_WEB_API_TOKEN', '').strip()

    allowed_roots_env = os.getenv('RESPRO_WEB_ALLOWED_ROOTS', '')
    allowed_roots = _parse_allowed_roots(data_dir, allowed_roots_env)

    _validate_data_dir(data_dir)
    _validate_project_db(project_db)
    _initialize_results_db(results_db)

    return StartupConfig(
        project_db=project_db,
        results_db=results_db,
        data_dir=data_dir,
        allowed_roots=allowed_roots,
        api_token=api_token,
    )


def is_path_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return whether a resolved path is contained in one of the allowed roots."""
    resolved_path = path.expanduser().resolve()
    for root in allowed_roots:
        if resolved_path == root or root in resolved_path.parents:
            return True
    return False


def _initialize_results_db(results_db: Path) -> None:
    """Create (if absent) and validate results.db at startup."""
    results_db.parent.mkdir(parents=True, exist_ok=True)
    connection = init_results_db(results_db)
    connection.close()


def _parse_allowed_roots(data_dir: Path, env_value: str) -> tuple[Path, ...]:
    """Return allowed filesystem roots: env override if set, otherwise only data_dir."""
    parsed = [item.strip() for item in env_value.split(',') if item.strip()]
    if not parsed:
        return (data_dir,)
    return tuple(Path(value).expanduser().resolve() for value in parsed)


def _validate_data_dir(data_dir: Path) -> None:
    """Ensure data directory exists and is writable."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise ValueError(f'Data directory path is not a directory: {data_dir}')
    with tempfile.NamedTemporaryFile(prefix='respro-web-', dir=data_dir, delete=True):
        pass


def _validate_project_db(project_db: Path) -> None:
    """Ensure project.db exists and has a readable project row."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project DB not found: {project_db}')
    connection = open_project_db(project_db)
    try:
        row = connection.execute('SELECT name FROM project LIMIT 1').fetchone()
        if row is None:
            raise ValueError(f'Project DB contains no project metadata: {project_db}')
    finally:
        connection.close()