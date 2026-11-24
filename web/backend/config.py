"""Shared web backend configuration loaded from ``defaults.toml`` plus env parsing helpers."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class WebEnvKeys:
    """Environment variable names read by the web backend."""

    data_dir: str
    allowed_roots: str
    cors_origins: str
    upload_rate_limit: str
    api_rate_limit: str
    host: str
    port: str
    redis_url: str
    job_timeout: str
    job_retry_max: str
    job_retry_intervals: str
    maintained_bootstrap: str
    maintained_db_update_interval: str
    trusted_proxies: str
    imprint: str
    contact_email: str
    max_batch_size: str
    deployment_mode: str
    result_ttl: str
    session_ttl: str


@dataclass(frozen=True)
class WebDefaults:
    """Runtime defaults loaded from ``defaults.toml``."""

    web_host: str
    web_port: int
    redis_url: str
    job_timeout_seconds: int
    job_retry_max: int
    job_retry_intervals_seconds: tuple[int, ...]
    sweep_frequency_seconds: int
    upload_rate_limit: str
    api_rate_limit: str
    max_batch_size: int
    profile_max_threads: int
    sample_name_max_length: int
    display_name_max_length: int
    path_list_max_length: int
    opaque_id_max_length: int
    min_depth_max: int
    batch_queue_name: str
    cors_local_origins: tuple[str, ...]
    profile_queue_name: str
    frontend_base_path: str
    service_name: str
    maintained_bootstrap: bool
    maintained_db_update_interval_seconds: int
    deployment_mode: str
    result_ttl_seconds: int
    session_ttl_seconds: int
    profile_sample_name: str
    profile_threads: int
    profile_min_af: float
    profile_min_depth: int
    upload_max_fasta_size: int
    upload_max_vcf_size: int
    upload_max_bam_size: int
    upload_max_json_size: int
    upload_chunk_size: int
    upload_max_fasta_line_length: int
    upload_max_vcf_line_length: int
    upload_max_vcf_data_lines: int
    upload_bgzf_header_bytes: int


@dataclass(frozen=True)
class WebBackendConfig:
    """Complete backend configuration: env keys plus default values."""

    env: WebEnvKeys
    defaults: WebDefaults


def _coerce_value(value: object, hint: object) -> object:
    if get_origin(hint) is tuple:
        elem_type = get_args(hint)[0]
        return tuple(elem_type(item) for item in value)  # type: ignore[union-attr]
    return hint(value)  # type: ignore[operator]


def _load_web_backend_config() -> WebBackendConfig:
    payload = tomllib.loads(Path(__file__).with_name('defaults.toml').read_text(encoding='utf-8'))
    env = WebEnvKeys(**{f.name: str(payload['env'][f.name]) for f in fields(WebEnvKeys)})

    raw_defaults = dict(payload['defaults'])
    for section in ('profile', 'upload'):
        for key, value in raw_defaults.pop(section).items():
            raw_defaults[f'{section}_{key}'] = value

    hints = get_type_hints(WebDefaults)
    defaults = WebDefaults(**{f.name: _coerce_value(raw_defaults[f.name], hints[f.name]) for f in fields(WebDefaults)})
    return WebBackendConfig(env=env, defaults=defaults)


def env_str(key: str, default: str) -> str:
    """Return a stripped env string value, falling back to *default* when unset or blank."""
    return os.getenv(key, default).strip() or default


def parse_csv_tokens(raw: str) -> list[str]:
    """Split a comma-separated string into stripped non-empty tokens."""
    return [item.strip() for item in raw.split(',') if item.strip()]


def parse_int_env(
    key: str,
    default: int,
    *,
    min_value: int = 0,
    allow_empty: bool = True,
    error_cls: type[Exception] = ValueError,
) -> int:
    """Parse an integer env setting with lower-bound validation."""
    raw = os.getenv(key, str(default)).strip()
    if not raw and allow_empty:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise error_cls(f'{key} must be an integer value.') from exc
    if min_value == 1 and parsed <= 0:
        raise error_cls(f'{key} must be > 0.')
    if parsed < min_value:
        raise error_cls(f'{key} must be >= {min_value}.')
    return parsed


WEB_BACKEND_CONFIG = _load_web_backend_config()
WEB_ENV = WEB_BACKEND_CONFIG.env
