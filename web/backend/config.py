"""Shared web backend configuration loaded from ``defaults.toml`` plus env parsing helpers."""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import unquote, urlsplit


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


def _req_table(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ValueError(f'defaults.toml key {key!r} must be a table.')
    return dict(value)


def _req_str(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f'defaults.toml key {key!r} must be a string.')
    return value


def _req_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f'defaults.toml key {key!r} must be a boolean.')
    return value


def _req_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'defaults.toml key {key!r} must be an integer.')
    return value


def _req_float(mapping: Mapping[str, object], key: str) -> float:
    value = mapping.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'defaults.toml key {key!r} must be a finite float.')
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f'defaults.toml key {key!r} must be finite.')
    return parsed


def _req_int_tuple(mapping: Mapping[str, object], key: str) -> tuple[int, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f'defaults.toml key {key!r} must be an integer array.')
    result: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise ValueError(f'defaults.toml key {key!r} must contain only integers.')
        result.append(item)
    return tuple(result)


def _req_str_tuple(mapping: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f'defaults.toml key {key!r} must be a string array.')
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f'defaults.toml key {key!r} must contain only strings.')
        result.append(item)
    return tuple(result)


def _load_web_backend_config() -> WebBackendConfig:
    payload = tomllib.loads(Path(__file__).with_name('defaults.toml').read_text(encoding='utf-8'))
    raw_env = _req_table(payload, 'env')
    expected_env_keys = {f.name for f in fields(WebEnvKeys)}
    if set(raw_env) != expected_env_keys:
        raise ValueError(f'defaults.toml [env] keys mismatch: {sorted(set(raw_env) ^ expected_env_keys)}')
    env = WebEnvKeys(
        data_dir=_req_str(raw_env, 'data_dir'),
        allowed_roots=_req_str(raw_env, 'allowed_roots'),
        cors_origins=_req_str(raw_env, 'cors_origins'),
        upload_rate_limit=_req_str(raw_env, 'upload_rate_limit'),
        api_rate_limit=_req_str(raw_env, 'api_rate_limit'),
        host=_req_str(raw_env, 'host'),
        port=_req_str(raw_env, 'port'),
        redis_url=_req_str(raw_env, 'redis_url'),
        job_timeout=_req_str(raw_env, 'job_timeout'),
        job_retry_max=_req_str(raw_env, 'job_retry_max'),
        job_retry_intervals=_req_str(raw_env, 'job_retry_intervals'),
        maintained_bootstrap=_req_str(raw_env, 'maintained_bootstrap'),
        maintained_db_update_interval=_req_str(raw_env, 'maintained_db_update_interval'),
        trusted_proxies=_req_str(raw_env, 'trusted_proxies'),
        imprint=_req_str(raw_env, 'imprint'),
        contact_email=_req_str(raw_env, 'contact_email'),
        max_batch_size=_req_str(raw_env, 'max_batch_size'),
        deployment_mode=_req_str(raw_env, 'deployment_mode'),
        result_ttl=_req_str(raw_env, 'result_ttl'),
        session_ttl=_req_str(raw_env, 'session_ttl'),
    )

    raw_defaults_section = _req_table(payload, 'defaults')
    raw_defaults: dict[str, object] = {
        key: value
        for key, value in raw_defaults_section.items()
        if key not in ('profile', 'upload')
    }
    for section in ('profile', 'upload'):
        subtable = _req_table(raw_defaults_section, section)
        for key, value in subtable.items():
            raw_defaults[f'{section}_{key}'] = value

    expected_default_keys = {f.name for f in fields(WebDefaults)}
    if set(raw_defaults) != expected_default_keys:
        raise ValueError(
            f'defaults.toml [defaults] keys mismatch: {sorted(set(raw_defaults) ^ expected_default_keys)}'
        )

    defaults = WebDefaults(
        web_host=_req_str(raw_defaults, 'web_host'),
        web_port=_req_int(raw_defaults, 'web_port'),
        redis_url=_req_str(raw_defaults, 'redis_url'),
        job_timeout_seconds=_req_int(raw_defaults, 'job_timeout_seconds'),
        job_retry_max=_req_int(raw_defaults, 'job_retry_max'),
        job_retry_intervals_seconds=_req_int_tuple(raw_defaults, 'job_retry_intervals_seconds'),
        sweep_frequency_seconds=_req_int(raw_defaults, 'sweep_frequency_seconds'),
        upload_rate_limit=_req_str(raw_defaults, 'upload_rate_limit'),
        api_rate_limit=_req_str(raw_defaults, 'api_rate_limit'),
        max_batch_size=_req_int(raw_defaults, 'max_batch_size'),
        profile_max_threads=_req_int(raw_defaults, 'profile_max_threads'),
        sample_name_max_length=_req_int(raw_defaults, 'sample_name_max_length'),
        display_name_max_length=_req_int(raw_defaults, 'display_name_max_length'),
        path_list_max_length=_req_int(raw_defaults, 'path_list_max_length'),
        opaque_id_max_length=_req_int(raw_defaults, 'opaque_id_max_length'),
        min_depth_max=_req_int(raw_defaults, 'min_depth_max'),
        batch_queue_name=_req_str(raw_defaults, 'batch_queue_name'),
        cors_local_origins=_req_str_tuple(raw_defaults, 'cors_local_origins'),
        profile_queue_name=_req_str(raw_defaults, 'profile_queue_name'),
        frontend_base_path=_req_str(raw_defaults, 'frontend_base_path'),
        service_name=_req_str(raw_defaults, 'service_name'),
        maintained_bootstrap=_req_bool(raw_defaults, 'maintained_bootstrap'),
        maintained_db_update_interval_seconds=_req_int(raw_defaults, 'maintained_db_update_interval_seconds'),
        deployment_mode=_req_str(raw_defaults, 'deployment_mode'),
        result_ttl_seconds=_req_int(raw_defaults, 'result_ttl_seconds'),
        session_ttl_seconds=_req_int(raw_defaults, 'session_ttl_seconds'),
        profile_sample_name=_req_str(raw_defaults, 'profile_sample_name'),
        profile_threads=_req_int(raw_defaults, 'profile_threads'),
        profile_min_af=_req_float(raw_defaults, 'profile_min_af'),
        profile_min_depth=_req_int(raw_defaults, 'profile_min_depth'),
        upload_max_fasta_size=_req_int(raw_defaults, 'upload_max_fasta_size'),
        upload_max_vcf_size=_req_int(raw_defaults, 'upload_max_vcf_size'),
        upload_max_bam_size=_req_int(raw_defaults, 'upload_max_bam_size'),
        upload_max_json_size=_req_int(raw_defaults, 'upload_max_json_size'),
        upload_chunk_size=_req_int(raw_defaults, 'upload_chunk_size'),
        upload_max_fasta_line_length=_req_int(raw_defaults, 'upload_max_fasta_line_length'),
        upload_max_vcf_line_length=_req_int(raw_defaults, 'upload_max_vcf_line_length'),
        upload_max_vcf_data_lines=_req_int(raw_defaults, 'upload_max_vcf_data_lines'),
        upload_bgzf_header_bytes=_req_int(raw_defaults, 'upload_bgzf_header_bytes'),
    )
    return WebBackendConfig(env=env, defaults=defaults)


_SECRET_QUERY_KEYS = frozenset({'password', 'passwd', 'secret', 'token', 'auth'})
_URL_TOKEN_RE = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s"\'<>()\[\]{}]+')
_HOST_TOKEN_RE = re.compile(r'^(?:\[[^\[\]\s]+\](?::\d+)?|[A-Za-z0-9._-]+(?::\d+)?)$')
_TRAILING_PUNCT = '.,;:!?)}\'"`]'


def _is_unix_scheme(scheme: str) -> bool:
    lower = scheme.lower()
    return lower == 'unix' or lower.endswith('+unix')


def _redact_userinfo(userinfo: str) -> str:
    if ':' in userinfo:
        user, _, _ = userinfo.partition(':')
        if any(ch.isspace() or ch in '[]' for ch in user):
            return '***'
        return f'{user}:***'
    return '***'


def _split_query_fragment(text: str) -> tuple[str, str, str]:
    q_idx = text.find('?')
    h_idx = text.find('#')
    if q_idx == -1 and h_idx == -1:
        return text, '', ''
    if h_idx != -1 and (q_idx == -1 or h_idx < q_idx):
        return text[:h_idx], '', text[h_idx:]
    if h_idx == -1:
        return text[:q_idx], text[q_idx:], ''
    return text[:q_idx], text[q_idx:h_idx], text[h_idx:]


def _redact_query_part(query_part: str) -> str:
    if not query_part.startswith('?'):
        return query_part
    redacted_items: list[str] = []
    for item in query_part[1:].split('&'):
        if '=' in item:
            key, _, _val = item.partition('=')
            norm_key = (unquote(key) if '%' in key else key).strip().lower()
            if norm_key in _SECRET_QUERY_KEYS:
                redacted_items.append(f'{key}=***')
                continue
        redacted_items.append(item)
    return '?' + '&'.join(redacted_items)


def _split_authority_credentials(rest: str, *, is_unix: bool) -> tuple[str, str] | None:
    """Split *rest* into ``(userinfo, host_and_tail)`` when it carries authority credentials."""
    if '@' not in rest or (is_unix and rest.startswith('/')):
        return None
    # First check authority portion before any '/', '?', '#'.
    delim_indices = [idx for ch in ('/', '?', '#') if (idx := rest.find(ch)) != -1]
    first_delim = min(delim_indices) if delim_indices else len(rest)
    prefix = rest[:first_delim]
    suffix = rest[first_delim:]

    if '@' in prefix:
        userinfo, _, host = prefix.rpartition('@')
        # Handle passwords that themselves contain '/', '?', '#' followed by another '@host'.
        if suffix and '@' in suffix:
            right_at = rest.rfind('@')
            tail_after = rest[right_at + 1 :]
            tail_head = _split_query_fragment(tail_after)[0]
            tail_host = tail_head.partition('/')[0]
            tail_strong = tail_host.startswith('[') or ':' in tail_host or '/' in tail_head
            if (is_unix and tail_after.startswith('/')) or (
                not is_unix and _HOST_TOKEN_RE.match(tail_host) and (tail_strong or not _HOST_TOKEN_RE.match(host))
            ):
                return rest[:right_at], tail_after
        return userinfo, f'{host}{suffix}'

    # '@' only after '/', '?', '#': treat as credentials only if prefix is not a plain host[:port].
    if ':' not in prefix:
        return None
    if _HOST_TOKEN_RE.match(prefix):
        user_part, _, port_part = prefix.partition(':')
        if prefix.startswith('[') or (user_part and port_part.isdigit()):
            return None
    right_at = rest.rfind('@')
    tail_after = rest[right_at + 1 :]
    tail_head = _split_query_fragment(tail_after)[0]
    tail_host = tail_head.partition('/')[0]
    if (is_unix and tail_after.startswith('/')) or (not is_unix and _HOST_TOKEN_RE.match(tail_host)):
        return rest[:right_at], tail_after
    return None


def redact_redis_url(url: str) -> str:
    """Return *url* with userinfo passwords and secret query parameters replaced by ``***``."""
    cleaned = url.strip()
    if not cleaned:
        return ''
    if '://' not in cleaned:
        split = _split_authority_credentials(cleaned, is_unix=False)
        if split is not None:
            userinfo, tail = split
            head, query, frag = _split_query_fragment(tail)
            return f'{_redact_userinfo(userinfo)}@{head}{_redact_query_part(query)}{frag}'
        head, query, frag = _split_query_fragment(cleaned)
        return f'{head}{_redact_query_part(query)}{frag}'

    scheme, _, rest = cleaned.partition('://')
    is_unix = _is_unix_scheme(scheme)
    split = _split_authority_credentials(rest, is_unix=is_unix)
    if split is not None:
        userinfo, tail = split
        head, query, frag = _split_query_fragment(tail)
        return f'{scheme}://{_redact_userinfo(userinfo)}@{head}{_redact_query_part(query)}{frag}'
    head, query, frag = _split_query_fragment(rest)
    return f'{scheme}://{head}{_redact_query_part(query)}{frag}'


def redact_redis_text(text: str) -> str:
    """Redact credentials from any URL tokens embedded inside a log or error message."""
    if not text or '://' not in text:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        end = len(token)
        while end > 0 and token[end - 1] in _TRAILING_PUNCT:
            end -= 1
        core, trailing = token[:end], token[end:]
        if '://' not in core:
            return token
        return f'{redact_redis_url(core)}{trailing}'

    return _URL_TOKEN_RE.sub(_replace, text)


def _extract_url_secrets(url: str) -> list[str]:
    """Extract credential values from *url* so error sanitizers can catch partial leaks."""
    cleaned = url.strip()
    if not cleaned:
        return []
    secrets: list[str] = []
    rest = cleaned.partition('://')[2] if '://' in cleaned else cleaned
    is_unix = '://' in cleaned and _is_unix_scheme(cleaned.partition('://')[0])
    split = _split_authority_credentials(rest, is_unix=is_unix)
    if split is not None:
        userinfo, tail = split
        pw = userinfo.partition(':')[2] if ':' in userinfo else userinfo
        if pw:
            secrets.append(pw)
        query_source = tail
    else:
        query_source = rest
    try:
        parsed_query = urlsplit(cleaned).query
    except ValueError:
        parsed_query = _split_query_fragment(query_source)[1].lstrip('?')
    for item in parsed_query.split('&'):
        if '=' in item:
            key, _, val = item.partition('=')
            norm_key = (unquote(key) if '%' in key else key).strip().lower()
            if norm_key in _SECRET_QUERY_KEYS and val:
                secrets.append(val)
    return secrets


def sanitize_redis_client_error(raw_msg: str, redis_url: str) -> str:
    """Sanitize a Redis client/pool exception message when *redis_url* contains credentials."""
    redacted_msg = redact_redis_text(raw_msg)
    redacted_url = redact_redis_url(redis_url)
    if redacted_url == redis_url:
        return redacted_msg

    scheme = redis_url.partition('://')[0] if '://' in redis_url else ''
    secrets = _extract_url_secrets(redis_url)
    safe_target = (
        f'{scheme}://***'
        if scheme and any(sec in redacted_url for sec in secrets)
        else redacted_url
    )
    if any(sec in redacted_msg for sec in secrets):
        return f'Invalid Redis configuration for {safe_target}'
    if (redacted_msg == raw_msg and '@' in redacted_msg) or (
        'Port could not be cast to integer value' in redacted_msg
        and any(part in redacted_msg for sec in secrets for part in sec.split(':') if part)
    ):
        return f'Invalid Redis configuration for {safe_target}'
    if "netloc '" in redacted_msg and 'NFKC normalization' in redacted_msg:
        return f'Invalid Redis configuration for {safe_target}'
    return redacted_msg


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
