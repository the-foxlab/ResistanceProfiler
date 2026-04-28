"""Startup configuration and validation for the web backend."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from respro.db.schema import open_project_db
from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV
from web.backend.services.maintained_bootstrap import bootstrap_missing_maintained_databases

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupConfig:
    """Validated startup configuration shared by API routes and workers."""

    project_databases_dir: Path
    uploads_dir: Path
    results_dir: Path
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    api_token: str


def load_startup_config() -> StartupConfig:
    """
    Load, validate, and return backend startup configuration.

    All paths default to ``RESPRO_WEB_DATA_DIR`` (default: ``data/`` next to the
    repository root, or ``/data`` when that exists — typical for Docker mounts).
    Individual paths can still be overridden via their own environment variables.
    """
    repo_data = Path('/data') if Path('/data').is_dir() else Path(__file__).resolve().parents[2] / 'data'
    data_dir = Path(os.getenv(WEB_ENV.data_dir, str(repo_data))).expanduser().resolve()

    project_databases_dir = (data_dir / 'project_databases').resolve()
    uploads_dir = (data_dir / 'uploads').resolve()
    results_dir = (data_dir / 'results').resolve()
    api_token = os.getenv(WEB_ENV.api_token, '').strip()
    maintained_bootstrap = _resolve_bool(
        os.getenv(
            WEB_ENV.maintained_bootstrap,
            str(WEB_BACKEND_CONFIG.defaults.maintained_bootstrap),
        ),
        setting_name=WEB_ENV.maintained_bootstrap,
    )

    allowed_roots_env = os.getenv(WEB_ENV.allowed_roots, '')
    allowed_roots = _parse_allowed_roots(
        default_roots=(project_databases_dir, uploads_dir, results_dir),
        env_value=allowed_roots_env,
    )

    _validate_data_dir(data_dir=data_dir)
    _initialize_workspace_dirs(
        project_databases_dir=project_databases_dir,
        uploads_dir=uploads_dir,
        results_dir=results_dir,
    )
    _migrate_legacy_project_db(data_dir=data_dir, project_databases_dir=project_databases_dir)
    if maintained_bootstrap:
        logger.info('Maintained database bootstrap enabled — downloading missing databases')
        bootstrap_missing_maintained_databases(project_databases_dir)
    _validate_at_least_one_project_db(project_databases_dir)
    _validate_startup_policy(api_token)

    return StartupConfig(
        project_databases_dir=project_databases_dir,
        uploads_dir=uploads_dir,
        results_dir=results_dir,
        data_dir=data_dir,
        allowed_roots=allowed_roots,
        api_token=api_token,
    )


def list_project_db_paths(project_databases_dir: Path) -> list[Path]:
    """Return validated project DB files sorted by file name."""
    db_paths = sorted(path for path in project_databases_dir.glob('*.db') if path.is_file())
    validated_paths: list[Path] = []
    for db_path in db_paths:
        _validate_project_db(db_path)
        validated_paths.append(db_path)
    return validated_paths


def resolve_project_db_path(project_databases_dir: Path, database_id: str | None) -> Path:
    """Resolve a database id to one validated project DB path."""
    db_paths = list_project_db_paths(project_databases_dir)
    if not db_paths:
        raise FileNotFoundError(
            f'No project database found in {project_databases_dir}. '
            'Add a .db file or enable maintained bootstrap.'
        )

    if not database_id:
        return db_paths[0]

    matches = [path for path in db_paths if path.name == database_id]
    if not matches:
        raise ValueError(f'Unknown database_id {database_id!r}.')
    return matches[0]


def is_path_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return whether a resolved path is contained in one of the allowed roots."""
    resolved_path = path.expanduser().resolve()
    for root in allowed_roots:
        if resolved_path == root or root in resolved_path.parents:
            return True
    return False


def _migrate_legacy_project_db(*, data_dir: Path, project_databases_dir: Path) -> None:
    """Move a legacy data/project.db into project_databases/ on first startup."""
    legacy = data_dir / 'project.db'
    if not legacy.is_file():
        return
    destination = project_databases_dir / legacy.name
    if destination.exists():
        return
    shutil.move(str(legacy), destination)


def _parse_allowed_roots(default_roots: tuple[Path, ...], env_value: str) -> tuple[Path, ...]:
    """Return allowed filesystem roots: env override if set, otherwise defaults."""
    parsed = [item.strip() for item in env_value.split(',') if item.strip()]
    if not parsed:
        return default_roots
    return tuple(Path(value).expanduser().resolve() for value in parsed)


def _validate_data_dir(data_dir: Path) -> None:
    """Ensure data directory exists and is writable."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise ValueError(f'Data directory path is not a directory: {data_dir}')
    with tempfile.NamedTemporaryFile(prefix='respro-web-', dir=data_dir, delete=True):
        pass


def _validate_at_least_one_project_db(project_databases_dir: Path) -> None:
    """Ensure at least one valid project database exists in project_databases_dir."""
    if list_project_db_paths(project_databases_dir):
        return
    raise FileNotFoundError(
        f'No project database found in {project_databases_dir}. '
        'Add a .db file or enable maintained bootstrap.'
    )


def _initialize_workspace_dirs(
    *,
    project_databases_dir: Path,
    uploads_dir: Path,
    results_dir: Path,
) -> None:
    """Create deterministic mounted-drive workspace directories."""
    for directory in (project_databases_dir, uploads_dir, results_dir):
        directory.mkdir(parents=True, exist_ok=True)


def _resolve_bool(raw_value: str, *, setting_name: str) -> bool:
    """Parse lenient boolean env values and fail on invalid tokens."""
    normalized = raw_value.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off', ''}:
        return False
    raise ValueError(f'Invalid boolean for {setting_name}: {raw_value!r}')


def _validate_startup_policy(api_token: str) -> None:
    """Enforce deployment safety rules based on host binding and token configuration."""
    host = os.getenv(WEB_ENV.host, '').strip()
    if host and host not in ('127.0.0.1', 'localhost') and not api_token:
        raise RuntimeError(
            'Public bind address detected but RESPRO_WEB_API_TOKEN is not set. '
            'Set a strong API token before deploying publicly.'
        )
    cors_origins = os.getenv(WEB_ENV.cors_origins, '').strip()
    if api_token and not cors_origins:
        raise RuntimeError(
            'RESPRO_WEB_API_TOKEN is set but RESPRO_WEB_CORS_ORIGINS is not configured. '
            'Set explicit allowed origins for token-authenticated deployments.'
        )


def _validate_project_db(project_db: Path) -> None:
    """Ensure project.db exists and can be opened as a valid SQLite database."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project DB not found: {project_db}')
    connection = open_project_db(project_db)
    connection.close()