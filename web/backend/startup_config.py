"""Startup configuration, workspace validation, and deployment policy for the web backend."""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, env_str, parse_csv_tokens
from web.backend.project_dbs import (
    build_project_db_uuid_index,
    list_project_db_paths,
    resolve_project_db_path,
)
from web.backend.services.maintained_bootstrap import (
    bootstrap_missing_maintained_databases,
    check_and_update_maintained_databases,
)

logger = logging.getLogger(__name__)

__all__ = [
    'ContactEmailConfig',
    'ImprintConfig',
    'StartupConfig',
    'build_project_db_uuid_index',
    'is_path_within_allowed_roots',
    'list_project_db_paths',
    'load_startup_config',
    'resolve_maintained_bootstrap_enabled',
    'resolve_project_db_path',
]


@dataclass(frozen=True)
class ImprintConfig:
    """Validated imprint configuration (``'path'`` self-hosted HTML or ``'url'`` external link)."""

    kind: Literal['path', 'url']
    html: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ContactEmailConfig:
    """Validated bare RFC-822 contact e-mail surfaced as a ``mailto:`` link."""

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
    """Load, validate, and return backend startup configuration rooted at ``RESPRO_WEB_DATA_DIR``."""
    repo_data = Path('/data') if Path('/data').is_dir() else Path(__file__).resolve().parents[2] / 'data'
    data_dir = Path(os.getenv(WEB_ENV.data_dir, str(repo_data))).expanduser().resolve()

    project_databases_dir = (data_dir / 'project_databases').resolve()
    uploads_dir = (data_dir / 'uploads').resolve()
    results_dir = (data_dir / 'results').resolve()
    maintained_bootstrap = resolve_maintained_bootstrap_enabled()
    imprint = _resolve_imprint()
    contact_email = _resolve_contact_email()
    deployment_mode = env_str(WEB_ENV.deployment_mode, WEB_BACKEND_CONFIG.defaults.deployment_mode)
    _validate_startup_policy(deployment_mode)

    parsed_roots = parse_csv_tokens(os.getenv(WEB_ENV.allowed_roots, ''))
    allowed_roots = (
        tuple(Path(value).expanduser().resolve() for value in parsed_roots)
        if parsed_roots
        else (project_databases_dir, uploads_dir, results_dir)
    )

    _validate_data_dir(data_dir)
    for directory in (project_databases_dir, uploads_dir, results_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_project_db(data_dir=data_dir, project_databases_dir=project_databases_dir)

    bootstrap_error: Exception | None = None
    if maintained_bootstrap:
        logger.info('Maintained database bootstrap enabled — downloading missing databases')
        try:
            bootstrap_missing_maintained_databases(project_databases_dir)
        except Exception as exc:  # noqa: BLE001 — fail-soft if valid local DBs exist
            bootstrap_error = exc
            logger.warning('Maintained database bootstrap failed (%s) — checking local databases', exc)
        else:
            try:
                check_and_update_maintained_databases(project_databases_dir)
            except Exception:  # noqa: BLE001 — update failures must never block startup
                logger.exception('Maintained database update check failed — continuing with existing databases')

    project_db_uuid_index = build_project_db_uuid_index(project_databases_dir)
    if not project_db_uuid_index:
        if bootstrap_error is not None:
            raise FileNotFoundError(
                f'No project database found in {project_databases_dir} and maintained bootstrap failed: {bootstrap_error}'
            ) from bootstrap_error
        raise FileNotFoundError(
            f'No project database found in {project_databases_dir}. '
            'Add a .db file or enable maintained bootstrap.'
        )
    if bootstrap_error is not None:
        logger.warning(
            'Maintained database bootstrap failed; continuing startup with %d existing local project database(s).',
            len(project_db_uuid_index),
        )

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


def is_path_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Return whether *path* resolves to or inside one of *allowed_roots*."""
    resolved_path = path.expanduser().resolve()
    for root in allowed_roots:
        resolved_root = root.expanduser().resolve()
        if resolved_path == resolved_root or resolved_root in resolved_path.parents:
            return True
    return False


def _migrate_legacy_project_db(*, data_dir: Path, project_databases_dir: Path) -> None:
    """Move a legacy ``data/project.db`` into ``project_databases/`` on first startup."""
    legacy = data_dir / 'project.db'
    destination = project_databases_dir / legacy.name
    if legacy.is_file() and not destination.exists():
        shutil.move(str(legacy), destination)


def _resolve_imprint() -> ImprintConfig | None:
    """Resolve optional ``RESPRO_WEB_IMPRINT`` as an ``http(s)`` URL or local HTML file."""
    raw = os.getenv(WEB_ENV.imprint, '').strip()
    if not raw:
        return None
    if '://' in raw or raw.lower().startswith(('javascript:', 'data:', 'vbscript:')):
        if any(ord(ch) < 32 or ch == '\x7f' or ch.isspace() for ch in raw):
            raise ValueError('RESPRO_WEB_IMPRINT URL must not contain whitespace or control characters.')
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        if scheme not in ('http', 'https'):
            raise ValueError(f'RESPRO_WEB_IMPRINT URL must use http or https scheme, got {scheme!r}: {raw}')
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('RESPRO_WEB_IMPRINT URL must not contain embedded credentials.')
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(f'RESPRO_WEB_IMPRINT URL contains an invalid port: {raw}') from exc
        if not parsed.netloc or not parsed.hostname:
            raise ValueError(f'RESPRO_WEB_IMPRINT URL must include a host: {raw}')
        return ImprintConfig(kind='url', url=raw)

    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Imprint file configured via RESPRO_WEB_IMPRINT not found or unreadable: {path}')
    return ImprintConfig(kind='path', html=path.read_text(encoding='utf-8'))


def _resolve_contact_email() -> ContactEmailConfig | None:
    """Resolve optional ``RESPRO_WEB_CONTACT_EMAIL`` as a single bare RFC-822 e-mail address."""
    raw = os.getenv(WEB_ENV.contact_email, '').strip()
    if not raw:
        return None
    realname, addr = parseaddr(raw)
    local, _, domain = addr.rpartition('@')
    if realname or not addr or '@' not in addr or not local or '.' not in domain or ' ' in addr:
        raise ValueError(f'RESPRO_WEB_CONTACT_EMAIL must be a single valid e-mail address, got {raw!r}')
    return ContactEmailConfig(email=addr)


def _validate_data_dir(data_dir: Path) -> None:
    """Ensure *data_dir* exists and is writable."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise ValueError(f'Data directory path is not a directory: {data_dir}')
    with tempfile.NamedTemporaryFile(prefix='respro-web-', dir=data_dir, delete=True):
        pass


def resolve_maintained_bootstrap_enabled() -> bool:
    """Parse ``RESPRO_WEB_MAINTAINED_BOOTSTRAP`` leniently, failing fast on invalid tokens."""
    raw = os.getenv(WEB_ENV.maintained_bootstrap, str(WEB_BACKEND_CONFIG.defaults.maintained_bootstrap))
    normalized = raw.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off', ''}:
        return False
    raise ValueError(f'Invalid boolean for {WEB_ENV.maintained_bootstrap}: {raw!r}')


def _validate_trusted_proxies_env() -> None:
    """Validate ``RESPRO_WEB_TRUSTED_PROXIES`` as non-empty IP/CIDR tokens (no wildcards)."""
    tokens = parse_csv_tokens(os.getenv(WEB_ENV.trusted_proxies, ''))
    if not tokens:
        raise RuntimeError(
            "RESPRO_WEB_DEPLOYMENT_MODE='online' requires RESPRO_WEB_TRUSTED_PROXIES "
            'to be set to one or more IP addresses or CIDR ranges.'
        )
    for token in tokens:
        if token == '*':
            raise RuntimeError(
                "RESPRO_WEB_DEPLOYMENT_MODE='online' does not allow wildcard '*' in "
                'RESPRO_WEB_TRUSTED_PROXIES; specify explicit proxy IPs or CIDRs.'
            )
        try:
            ipaddress.ip_address(token)
            continue
        except ValueError:
            pass
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise RuntimeError(
                f'Invalid entry {token!r} in RESPRO_WEB_TRUSTED_PROXIES: '
                'expected an IP address or CIDR range.'
            ) from exc
        if network.prefixlen == 0:
            raise RuntimeError(
                f'RESPRO_WEB_TRUSTED_PROXIES entry {token!r} trusts all addresses '
                "in 'online' mode; specify explicit proxy IPs or CIDRs."
            )


def _validate_online_cors_env() -> None:
    """Reject wildcard CORS in ``online`` mode and warn on localhost fallback."""
    origins = parse_csv_tokens(os.getenv(WEB_ENV.cors_origins, ''))
    if not origins:
        logger.warning(
            "RESPRO_WEB_DEPLOYMENT_MODE='online' with %s unset — falling back to localhost CORS defaults %s",
            WEB_ENV.cors_origins,
            WEB_BACKEND_CONFIG.defaults.cors_local_origins,
        )
        return
    if any('*' in origin for origin in origins):
        raise RuntimeError(
            "RESPRO_WEB_DEPLOYMENT_MODE='online' does not allow wildcard '*' in "
            f'{WEB_ENV.cors_origins}; specify explicit frontend origin(s).'
        )


def _validate_startup_policy(deployment_mode: str) -> None:
    """Enforce deployment safety rules for ``local`` and ``online`` modes."""
    if deployment_mode == 'local':
        return
    if deployment_mode == 'online':
        _validate_trusted_proxies_env()
        _validate_online_cors_env()
        return
    raise RuntimeError(
        f"Unknown RESPRO_WEB_DEPLOYMENT_MODE={deployment_mode!r}. Valid modes are 'local', 'online'."
    )
