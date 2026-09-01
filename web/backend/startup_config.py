"""Startup configuration and validation for the web backend."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from respro.db.schema import open_project_db
from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV
from web.backend.services.maintained_bootstrap import (
    bootstrap_missing_maintained_databases,
    check_and_update_maintained_databases,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImprintConfig:
    """Validated imprint configuration.

    ``kind='path'`` — self-hosted HTML read once at startup into ``html``.
    ``kind='url'`` — direct link to an already-hosted imprint page in ``url``.
    """

    kind: Literal['path', 'url']
    html: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ContactEmailConfig:
    """Validated contact e-mail configuration.

    Holds a single bare RFC-822 address (no display-name form) surfaced as a
    ``mailto:`` link in the app footer and on the About tab.
    """

    email: str


@dataclass(frozen=True)
class StartupConfig:
    """Validated startup configuration shared by API routes and workers."""

    project_databases_dir: Path
    uploads_dir: Path
    results_dir: Path
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    imprint: ImprintConfig | None = None
    contact_email: ContactEmailConfig | None = None
    project_db_uuid_index: dict[str, Path] = field(default_factory=dict)
    deployment_mode: str = 'local'


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
    maintained_bootstrap = _resolve_maintained_bootstrap_enabled()
    imprint = _resolve_imprint()
    contact_email = _resolve_contact_email()
    deployment_mode = _resolve_deployment_mode()

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
        try:
            check_and_update_maintained_databases(project_databases_dir)
        except Exception:  # noqa: BLE001 — update failures must never block startup
            logger.exception('Maintained database update check failed — continuing with existing databases')
    _validate_at_least_one_project_db(project_databases_dir)
    project_db_uuid_index = build_project_db_uuid_index(project_databases_dir)
    _validate_startup_policy(deployment_mode)

    return StartupConfig(
        project_databases_dir=project_databases_dir,
        uploads_dir=uploads_dir,
        results_dir=results_dir,
        data_dir=data_dir,
        allowed_roots=allowed_roots,
        imprint=imprint,
        contact_email=contact_email,
        project_db_uuid_index=project_db_uuid_index,
        deployment_mode=deployment_mode,
    )


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


def resolve_regenerate_project_db_path(
    project_databases_dir: Path,
    project_db_uuid_index: dict[str, Path],
    *,
    project_fingerprint: str,
    fallback_database_id: str | None,
) -> Path:
    """Resolve project DB for regenerate requests using JSON fingerprint when present.

    The ``project_db_uuid_index`` is the mutable, refreshable UUID->path cache held on
    ``app.state`` (seeded from the frozen :class:`StartupConfig` at startup and rebuilt
    by the weekly maintained-DB update thread). Passing it in keeps the frozen config
    authoritative for request handling while allowing the index to be refreshed in
    place after a weekly DB swap.

    :param project_databases_dir: directory containing project ``.db`` files
    :param project_db_uuid_index: mutable UUID->path cache (refreshable at runtime)
    :param project_fingerprint: project UUID from the submitted results JSON
    :param fallback_database_id: explicit database id from the request body
    :return: resolved project database path
    """
    normalized_fingerprint = project_fingerprint.strip()
    if normalized_fingerprint:
        project_db = project_db_uuid_index.get(normalized_fingerprint)
        if project_db is None:
            raise ValueError(
                f'No project database found for JSON project_fingerprint {normalized_fingerprint!r}.'
            )
        return project_db

    return resolve_project_db_path(project_databases_dir, fallback_database_id)


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


def _resolve_imprint() -> ImprintConfig | None:
    """Resolve optional imprint config from the ``RESPRO_WEB_IMPRINT`` env var.

    Detection rule — the trimmed value is either:

    * an absolute ``http://`` / ``https://`` URL → ``ImprintConfig(kind='url', url=...)``
      (direct link to an already-hosted imprint; the footer links straight there and
      ``/legal`` 302-redirects to it);
    * any other non-empty value → treated as a local file path →
      ``ImprintConfig(kind='path', html=...)`` (self-hosted HTML read at startup);
    * unset / empty → ``None`` (feature disabled).

    Non-``http(s)`` URL schemes (``ftp://``, ``file://``, …) fail fast at startup.
    A path that points at a missing or unreadable file fails fast at startup.
    """
    raw = os.getenv(WEB_ENV.imprint, '').strip()
    if not raw:
        return None

    if _looks_like_url(raw):
        scheme = urlparse(raw).scheme.lower()
        if scheme not in ('http', 'https'):
            raise ValueError(
                f'RESPRO_WEB_IMPRINT URL must use http or https scheme, got {scheme!r}: {raw}'
            )
        return ImprintConfig(kind='url', url=raw)

    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f'Imprint file configured via RESPRO_WEB_IMPRINT not found or unreadable: {path}'
        )
    return ImprintConfig(kind='path', html=path.read_text(encoding='utf-8'))


def _looks_like_url(value: str) -> bool:
    """True when ``value`` carries a ``scheme://`` prefix (e.g. ``http://``, ``ftp://``).

    Any scheme is detected so that non-``http(s)`` URLs are rejected with a clear scheme
    error by :func:`_resolve_imprint` rather than being misread as a local file path.
    """
    return '://' in value and bool(urlparse(value).scheme)


def _resolve_contact_email() -> ContactEmailConfig | None:
    """Resolve optional contact e-mail config from the ``RESPRO_WEB_CONTACT_EMAIL`` env var.

    The trimmed value is either:

    * empty / unset → ``None`` (feature disabled);
    * a single bare RFC-822 address → ``ContactEmailConfig(email=...)``;
    * anything else (no ``@``, missing local/domain part, a ``"Name <addr>"`` display-name
      form, embedded whitespace, …) → ``ValueError`` mentioning
      ``RESPRO_WEB_CONTACT_EMAIL`` so an invalid value fails fast at startup rather than
      silently rendering a broken ``mailto:`` link.

    :func:`email.utils.parseaddr` is used to split display-name from address; a valid
    contact address has an empty realname and a non-empty ``addr`` that itself contains
    ``@`` and a domain.
    """
    raw = os.getenv(WEB_ENV.contact_email, '').strip()
    if not raw:
        return None

    realname, addr = parseaddr(raw)
    if realname or not addr or '@' not in addr or not _has_domain(addr):
        raise ValueError(
            f'RESPRO_WEB_CONTACT_EMAIL must be a single valid e-mail address, got {raw!r}'
        )
    return ContactEmailConfig(email=addr)


def _has_domain(addr: str) -> bool:
    """True when the local-part@domain ``addr`` has a non-empty domain with a dot."""
    local, _, domain = addr.rpartition('@')
    return bool(local) and '.' in domain and ' ' not in addr


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


def list_project_db_paths(project_databases_dir: Path) -> list[Path]:
    """Return validated project DB files sorted by file name."""
    db_paths = sorted(path for path in project_databases_dir.glob('*.db') if path.is_file())
    validated_paths: list[Path] = []
    for db_path in db_paths:
        _validate_project_db(db_path)
        validated_paths.append(db_path)
    return validated_paths


def build_project_db_uuid_index(project_databases_dir: Path) -> dict[str, Path]:
    """Return UUID->project DB path mapping for all validated project databases."""
    uuid_index: dict[str, Path] = {}
    for db_path in list_project_db_paths(project_databases_dir):
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


def refresh_project_db_uuid_index(project_databases_dir: Path) -> dict[str, Path]:
    """Recompute the UUID->project DB path index after a maintained-DB refresh.

    Called by the weekly update thread to rebuild the mutable cache on
    ``app.state.project_db_uuid_index`` because refreshed databases receive a new UUID.

    :param project_databases_dir: directory containing project ``.db`` files
    :return: freshly computed UUID->path mapping
    """
    return build_project_db_uuid_index(project_databases_dir)


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


def _resolve_maintained_bootstrap_enabled() -> bool:
    """Resolve the maintained-bootstrap flag from env with the config default.

    Shared by :func:`load_startup_config` and the lifespan startup of the weekly update
    thread so both resolve the same source of truth.
    """
    return _resolve_bool(
        os.getenv(
            WEB_ENV.maintained_bootstrap,
            str(WEB_BACKEND_CONFIG.defaults.maintained_bootstrap),
        ),
        setting_name=WEB_ENV.maintained_bootstrap,
    )


def _validate_startup_policy(deployment_mode: str) -> None:
    """Enforce deployment safety rules based on the explicit deployment mode.

    Two modes:

    * ``local`` — default zero-config behaviour. The service binds to loopback
      (the reference compose file does so). No proxy trust, no CORS requirement.
      API docs remain enabled for developer convenience.
    * ``online`` — the service sits behind a reverse proxy (nginx/Caddy/SSO) that
      terminates TLS and, optionally, authenticates browsers. Requires
      ``RESPRO_WEB_TRUSTED_PROXIES`` so forwarded client IPs are trusted only from
      known proxies. API docs are disabled. Browser authentication (Basic Auth /
      SSO / IP allowlist) is configured at the proxy, not here; the session cookie
      provides per-user data isolation within a shared deployment.
    """
    if deployment_mode == 'local':
        return

    if deployment_mode == 'online':
        trusted_proxies = os.getenv(WEB_ENV.trusted_proxies, '').strip()
        if not trusted_proxies:
            raise RuntimeError(
                "RESPRO_WEB_DEPLOYMENT_MODE='online' requires RESPRO_WEB_TRUSTED_PROXIES "
                'to be set so forwarded client IPs are trusted only from known proxies.'
            )
        return

    raise RuntimeError(
        f"Unknown RESPRO_WEB_DEPLOYMENT_MODE={deployment_mode!r}. "
        "Valid modes are 'local', 'online'."
    )


def _resolve_deployment_mode() -> str:
    """Resolve the deployment trust mode from env with the config default."""
    default = WEB_BACKEND_CONFIG.defaults.deployment_mode
    raw_value = os.getenv(WEB_ENV.deployment_mode, default).strip()
    if not raw_value:
        return default
    return raw_value


def _validate_project_db(project_db: Path) -> None:
    """Ensure project.db exists and can be opened as a valid SQLite database."""
    if not project_db.is_file():
        raise FileNotFoundError(f'Project DB not found: {project_db}')
    connection = open_project_db(project_db)
    connection.close()


def _read_project_uuid(project_db: Path) -> str:
    """Read and validate the stable project UUID from one project database."""
    connection = open_project_db(project_db)
    try:
        row = connection.execute('SELECT uuid FROM project LIMIT 1').fetchone()
    finally:
        connection.close()

    if row is None:
        raise ValueError(f'Project database missing project row: {project_db}')
    project_uuid = str(row['uuid'] or '').strip()
    if not project_uuid:
        raise ValueError(f'Project database missing UUID in project table: {project_db}')
    return project_uuid
