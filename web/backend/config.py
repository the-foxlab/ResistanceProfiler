"""Shared web backend configuration loaded from ``defaults.toml`` plus env parsing helpers."""

from __future__ import annotations

import math
import os
import re
import string
import tomllib
from bisect import bisect_right
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import unquote


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
_HOST_PART_RE = re.compile(r'^(?:\[[^\[\]\s:]*:[^\[\]\s]*\](?::\d+)?|[^/?#@=:\[\]\s]+(?::\d+)?)$')
_SCHEME_FIRST_CHARS = frozenset(string.ascii_letters)
_SCHEME_CONT_CHARS = frozenset(string.ascii_letters + string.digits + '+.-')
_WHITESPACE_RE = re.compile(r'\s')
_URL_TRAILING_WRAPPER_CHARS = frozenset('\'")]}.,;!>:`*_~')
_URL_SPLIT_LEADING_DELIMS = frozenset('\'",;([{<=&|`') | frozenset(')>]}:')
_URL_SPLIT_KEY_SEPARATORS = frozenset(',;|&{"\'')
_URL_SPLIT_KEY_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-[]')
_STRUCTURAL_TRAILING_DELIMS = frozenset('"\'<>)]},;')
_STRUCTURAL_TRAILING_DELIM_RE = re.compile(r'["\'<>)\]},;]')
_HARD_STRUCTURAL_DELIMS = frozenset('"<>}')
_HARD_OR_WS_SCAN_RE = re.compile(r'[\s"\'<>}]')
_QUERY_END_SCAN_RE = re.compile(r'[#\s"\'<>}]')
_QUERY_DELIM_SCAN_RE = re.compile(r'[,;()\[\]]')
_HARD_OUTER_CANDIDATE_CHARS = frozenset('"\'<}')
_HARD_OUTER_CANDIDATE_RE = re.compile(r'["\'<}]')
_GAP_OUTER_PAREN_RE = re.compile(r'["\'<}\)\]]')
_JSON_OPEN_STOP_RE = re.compile(r'[@}\]]')
_HEAD_Q_SCAN_RE = re.compile(r'[#\s"\'<}()\[\]&=]')
_CLOSING_WRAPPER_CHARS = frozenset('"\'>})]')
_TAG_NAME_CHARS = frozenset(string.ascii_letters + string.digits + ':_-')
_HOST_SCAN_STOP_BRACKET = frozenset('[]/?#@"\'<>},;)')
_HOST_SCAN_STOP_PLAIN = frozenset('/?#@=:[]"\'<>)]},;')
_HTTP_LIKE_SCHEMES = frozenset({'http', 'https'})
_TIER_BARE = 0
_TIER_SLASH = 1
_TIER_PORT = 2


def _is_unix_scheme(scheme_lower: str) -> bool:
    return scheme_lower == 'unix' or scheme_lower.endswith('+unix')


def _redact_userinfo(userinfo: str) -> str:
    if ':' in userinfo:
        user, _, _ = userinfo.partition(':')
        if any(char.isspace() or char in '[]' for char in user):
            return '***'
        return f'{user}:***'
    return '***'


def _redact_query(query: str, *, redact_nested_schemes: bool = False) -> str:
    if not query:
        return query
    parts: list[str] = []
    for item in query.split('&'):
        if '=' in item:
            key, _, value = item.partition('=')
            norm_key = (unquote(key) if '%' in key else key).strip().lower()
            if norm_key in _SECRET_QUERY_KEYS:
                parts.append(f'{key}=***')
                continue
            if redact_nested_schemes:
                if '://' in key:
                    redacted_item = redact_redis_text(item)
                    if redacted_item != item:
                        parts.append(redacted_item)
                        continue
                elif '://' in value:
                    redacted_val = redact_redis_text(value)
                    if redacted_val != value:
                        parts.append(f'{key}={redacted_val}')
                        continue
        elif redact_nested_schemes and '://' in item:
            redacted_item = redact_redis_text(item)
            if redacted_item != item:
                parts.append(redacted_item)
                continue
        parts.append(item)
    return '&'.join(parts)


def _redact_query_part(query_part: str, *, redact_nested_schemes: bool = False) -> str:
    if not query_part.startswith('?'):
        return query_part
    return f'?{_redact_query(query_part[1:], redact_nested_schemes=redact_nested_schemes)}'


def _split_query_fragment(text: str) -> tuple[str, str, str]:
    """Split *text* into ``(head, query_with_qmark, fragment_with_hash)``."""
    q_idx = text.find('?')
    h_idx = text.find('#')
    if q_idx == -1 and h_idx == -1:
        return text, '', ''
    if h_idx != -1 and (q_idx == -1 or h_idx < q_idx):
        return text[:h_idx], '', text[h_idx:]
    if h_idx == -1:
        return text[:q_idx], text[q_idx:], ''
    return text[:q_idx], text[q_idx:h_idx], text[h_idx:]


def _first_delim_index(text: str) -> int:
    """Return the earliest index of ``/``, ``?``, or ``#`` in *text*, or ``len(text)``."""
    indices = [idx for ch in ('/', '?', '#') if (idx := text.find(ch)) != -1]
    return min(indices) if indices else len(text)


def _is_valid_redis_db_path(suffix_head: str) -> bool:
    return suffix_head in ('', '/') or (suffix_head.startswith('/') and suffix_head[1:].isdigit())


def _is_host_or_host_list(
    candidate_host: str,
    *,
    require_all_strong_for_list: bool = False,
) -> tuple[bool, bool]:
    """Return ``(is_valid, is_strong)`` for a single host or comma-separated multi-host list."""
    if ',' not in candidate_host:
        if not _HOST_PART_RE.match(candidate_host):
            return False, False
        return True, candidate_host.startswith('[') or ':' in candidate_host
    parts = candidate_host.split(',')
    if len(parts) < 2:
        return False, False
    any_strong = False
    for part in parts:
        if not part or not _HOST_PART_RE.match(part):
            return False, False
        part_strong = part.startswith('[') or ':' in part
        if require_all_strong_for_list and not part_strong:
            return False, False
        if part_strong:
            any_strong = True
    return any_strong, any_strong


def _is_genuine_authority_prefix(
    candidate_host: str,
    suffix_head: str,
    *,
    is_http_like: bool,
) -> bool:
    host_ok, has_strong_host = _is_host_or_host_list(candidate_host)
    if not host_ok:
        return False
    if '@' in suffix_head:
        return False
    if not is_http_like and not _is_valid_redis_db_path(suffix_head):
        return False
    return has_strong_host or suffix_head != ''


def _is_valid_host_and_tail(rest_after_at: str, *, is_unix: bool, require_strong: bool) -> bool:
    if is_unix:
        return rest_after_at.startswith('/')
    tail_head, _query, _fragment = _split_query_fragment(rest_after_at)
    if not tail_head or '@' in tail_head:
        return False
    host_part, slash, _path_part = tail_head.partition('/')
    host_ok, has_port = _is_host_or_host_list(host_part)
    if not host_ok:
        return False
    if not require_strong:
        return True
    return has_port or slash == '/'


def _find_authority_at_in_suffix(
    rest: str,
    start_idx: int,
    *,
    is_unix: bool,
    require_strong: bool = True,
    require_valid_redis_db: bool = False,
    query_start_pos: int = -1,
    skip_secret_query_at: bool = False,
    skip_kv_query_at: bool = False,
    skip_hash_after_kv_query: bool = False,
    end: int | None = None,
) -> int | None:
    """Locate the rightmost ``@`` in ``rest[start_idx:end]`` whose suffix is a valid host/path."""
    n = len(rest) if end is None else end
    first_q = rest.find('?', start_idx, n)
    first_h = rest.find('#', start_idx, n)
    check_query_skip = (skip_secret_query_at or skip_kv_query_at) and query_start_pos != -1
    idx = n
    if check_query_skip:
        first_hash_in_query = rest.find('#', query_start_pos + 1, n)
        if (
            first_hash_in_query != -1
            and skip_hash_after_kv_query
            and rest.find('=', query_start_pos + 1, first_hash_in_query) != -1
        ):
            idx = first_hash_in_query
            first_hash_in_query = -1
        cached_amp_pos = idx + 1
        cached_eq_item_start = -1
        cached_eq_pos = -1
        cached_eq_is_secret = False
        no_amp_and_no_eq = False
    else:
        first_hash_in_query = -1
        cached_amp_pos = -1
        cached_eq_item_start = -1
        cached_eq_pos = -1
        cached_eq_is_secret = False
        no_amp_and_no_eq = True

    if is_unix:
        while (at_idx := rest.rfind('@', start_idx, idx)) != -1:
            if (
                check_query_skip
                and at_idx > query_start_pos
                and not no_amp_and_no_eq
                and (first_hash_in_query == -1 or at_idx <= first_hash_in_query)
            ):
                if cached_amp_pos >= at_idx:
                    cached_amp_pos = rest.rfind('&', query_start_pos + 1, at_idx)
                item_start = cached_amp_pos + 1 if cached_amp_pos != -1 else query_start_pos + 1
                if cached_eq_item_start != item_start:
                    cached_eq_item_start = item_start
                    cached_eq_pos = rest.find('=', item_start, at_idx)
                    if cached_eq_pos != -1:
                        raw_k = rest[item_start:cached_eq_pos]
                        norm_k = unquote(raw_k) if '%' in raw_k else raw_k
                        cached_eq_is_secret = norm_k.strip().lower() in _SECRET_QUERY_KEYS
                    else:
                        cached_eq_is_secret = False
                        if cached_amp_pos == -1:
                            no_amp_and_no_eq = True
                if cached_eq_pos != -1 and cached_eq_pos < at_idx:
                    if (skip_secret_query_at and cached_eq_is_secret) or skip_kv_query_at:
                        idx = cached_eq_pos
                        continue
            if at_idx + 1 < n and rest[at_idx + 1] == '/':
                return at_idx
            idx = at_idx
        return None

    prev_at = idx
    next_qf = n
    while (at_idx := rest.rfind('@', start_idx, idx)) != -1:
        if at_idx + 1 < prev_at:
            if first_q != -1 and first_q < prev_at:
                q_pos = rest.find('?', at_idx + 1, prev_at)
                if q_pos != -1 and q_pos < next_qf:
                    next_qf = q_pos
            if first_h != -1 and first_h < prev_at:
                h_pos = rest.find('#', at_idx + 1, prev_at)
                if h_pos != -1 and h_pos < next_qf:
                    next_qf = h_pos
        if (
            check_query_skip
            and at_idx > query_start_pos
            and not no_amp_and_no_eq
            and (first_hash_in_query == -1 or at_idx <= first_hash_in_query)
        ):
            if cached_amp_pos >= at_idx:
                cached_amp_pos = rest.rfind('&', query_start_pos + 1, at_idx)
            item_start = cached_amp_pos + 1 if cached_amp_pos != -1 else query_start_pos + 1
            if cached_eq_item_start != item_start:
                cached_eq_item_start = item_start
                cached_eq_pos = rest.find('=', item_start, at_idx)
                if cached_eq_pos != -1:
                    raw_k = rest[item_start:cached_eq_pos]
                    norm_k = unquote(raw_k) if '%' in raw_k else raw_k
                    cached_eq_is_secret = norm_k.strip().lower() in _SECRET_QUERY_KEYS
                else:
                    cached_eq_is_secret = False
                    if cached_amp_pos == -1:
                        no_amp_and_no_eq = True
            if cached_eq_pos != -1 and cached_eq_pos < at_idx:
                if (skip_secret_query_at and cached_eq_is_secret) or skip_kv_query_at:
                    if first_q != -1 and first_q < at_idx:
                        q_in_skip = rest.find('?', cached_eq_pos, at_idx)
                        if q_in_skip != -1 and q_in_skip < next_qf:
                            next_qf = q_in_skip
                    prev_at = cached_eq_pos
                    idx = cached_eq_pos
                    continue
        if prev_at >= next_qf and next_qf > at_idx + 1:
            tail_head = rest[at_idx + 1 : next_qf]
            if '=' not in tail_head and '&' not in tail_head:
                host_part, slash, path_part = tail_head.partition('/')
                host_ok, has_strong = _is_host_or_host_list(host_part)
                if (
                    host_ok
                    and (not require_strong or has_strong or slash == '/')
                    and (not require_valid_redis_db or _is_valid_redis_db_path(f'{slash}{path_part}'))
                ):
                    return at_idx
        if prev_at < next_qf:
            q_jump = rest.rfind('?', start_idx, prev_at) if (first_q != -1 and first_q < prev_at) else -1
            h_jump = rest.rfind('#', start_idx, prev_at) if (first_h != -1 and first_h < prev_at) else -1
            qf_pos = max(q_jump, h_jump)
            if qf_pos == -1:
                return None
            next_qf = qf_pos
            prev_at = qf_pos
            idx = qf_pos
            continue
        prev_at = at_idx
        idx = at_idx
    return None


def _split_authority_at(
    rest: str,
    *,
    is_unix: bool,
    is_http_like: bool = False,
    is_sentinel_or_cluster: bool = False,
) -> tuple[str, str] | None:
    """Split *rest* into ``(userinfo, host_and_tail)`` when *rest* contains authority credentials."""
    if '@' not in rest:
        return None

    first_delim = _first_delim_index(rest)
    prefix = rest[:first_delim]
    suffix = rest[first_delim:]
    path_delim_before_qf = first_delim < len(rest) and (
        rest[first_delim] == '/' or (is_http_like and rest[first_delim] in ('?', '#'))
    )

    if '@' in prefix:
        userinfo_prefix, _, candidate_host = prefix.rpartition('@')
        if suffix and '@' in suffix:
            suffix_head, suffix_query, _f = _split_query_fragment(suffix)
            host_part_ok, host_is_strong = _is_host_or_host_list(candidate_host)
            query_start_pos = (first_delim + len(suffix_head)) if suffix_query else -1
            prefix_is_genuine = _is_genuine_authority_prefix(
                candidate_host,
                suffix_head,
                is_http_like=is_http_like,
            )
            unix_prefix_ok = (
                is_unix
                and candidate_host == ''
                and suffix_head.startswith('/')
                and '@' not in suffix_head
            )
            sentinel_prefix_ok = (
                is_sentinel_or_cluster
                and host_part_ok
                and (host_is_strong or _is_ipv4_or_localhost(candidate_host))
                and '@' not in suffix_head
                and suffix_head.startswith('/')
                and '/' not in suffix_head[1:]
            )
            if path_delim_before_qf and (prefix_is_genuine or sentinel_prefix_ok or unix_prefix_ok):
                return userinfo_prefix, f'{candidate_host}{suffix}'
            unix_has_path_prefix = is_unix and candidate_host == '' and suffix_head.startswith('/')
            has_secret_q_in_suffix = (
                query_start_pos != -1
                and _has_secret_param_in_query(rest, query_start_pos + 1, len(rest))
            )
            host_is_local_or_strong = host_is_strong or (
                host_part_ok
                and rest[first_delim] == '/'
                and _is_ipv4_or_localhost(candidate_host)
            )
            has_secret_q_for_path_at = (
                host_part_ok
                and rest[first_delim] == '/'
                and '@' in suffix_head
                and has_secret_q_in_suffix
            )
            prefix_has_host_or_path = (
                host_part_ok
                and ('@' not in suffix_head or host_is_strong or has_secret_q_for_path_at)
                and (host_is_strong or suffix_head != '')
            )
            guard_secret_query_at = (
                (
                    (host_part_ok and ('@' not in suffix_head or host_is_local_or_strong))
                    or unix_has_path_prefix
                )
                and query_start_pos != -1
            )
            guard_kv_query_at = guard_secret_query_at and (
                prefix_has_host_or_path or ':' in userinfo_prefix
            )
            at_idx = _find_authority_at_in_suffix(
                rest,
                first_delim,
                is_unix=is_unix,
                require_strong=True,
                require_valid_redis_db=(not is_unix and not is_http_like and prefix_is_genuine),
                query_start_pos=query_start_pos,
                skip_secret_query_at=guard_secret_query_at,
                skip_kv_query_at=(unix_has_path_prefix and has_secret_q_in_suffix),
                skip_hash_after_kv_query=(prefix_has_host_or_path or unix_has_path_prefix),
            )
            if at_idx is not None:
                return rest[:at_idx], rest[at_idx + 1 :]
            if (
                not is_unix
                and not prefix_is_genuine
                and not (
                    (host_is_strong or has_secret_q_for_path_at)
                    and rest[first_delim] == '/'
                    and query_start_pos != -1
                    and '@' in suffix_head
                )
            ):
                at_idx = _find_authority_at_in_suffix(
                    rest,
                    first_delim,
                    is_unix=False,
                    require_strong=False,
                    require_valid_redis_db=False,
                    query_start_pos=query_start_pos,
                    skip_secret_query_at=guard_secret_query_at,
                    skip_kv_query_at=guard_kv_query_at,
                    skip_hash_after_kv_query=prefix_has_host_or_path,
                )
                if at_idx is not None and not (
                    rest[first_delim] == '?'
                    and ':' in userinfo_prefix
                    and host_part_ok
                    and rest.find('=', first_delim + 1, at_idx) != -1
                ):
                    return rest[:at_idx], rest[at_idx + 1 :]
        return userinfo_prefix, f'{candidate_host}{suffix}'

    # '@' only appears after '/', '?', or '#'. Only treat as authority credentials if
    # prefix is a plausible userinfo start (empty user or non-port password) rather than host[:port].
    if ':' not in prefix:
        return None

    if prefix.startswith('[') and ',' not in prefix:
        suffix_head, suffix_query, _f = _split_query_fragment(suffix)
        host_part_ok = bool(_HOST_PART_RE.match(prefix))
        query_start_pos = (first_delim + len(suffix_head)) if suffix_query else -1
        guard_query_at = host_part_ok and '@' not in suffix_head and query_start_pos != -1
        prefix_is_genuine = _is_genuine_authority_prefix(
            prefix,
            suffix_head,
            is_http_like=is_http_like,
        ) or (
            is_sentinel_or_cluster
            and host_part_ok
            and '@' not in suffix_head
            and suffix_head.startswith('/')
            and '/' not in suffix_head[1:]
        )
        if path_delim_before_qf and prefix_is_genuine:
            return None
        at_idx = _find_authority_at_in_suffix(
            rest,
            first_delim,
            is_unix=is_unix,
            require_strong=True,
            require_valid_redis_db=(not is_unix and not is_http_like and prefix_is_genuine),
            query_start_pos=query_start_pos,
            skip_secret_query_at=guard_query_at,
            skip_hash_after_kv_query=host_part_ok and '@' not in suffix_head,
        )
        if at_idx is not None:
            return rest[:at_idx], rest[at_idx + 1 :]
        if prefix_is_genuine:
            return None
        if not is_unix:
            at_idx = _find_authority_at_in_suffix(
                rest,
                first_delim,
                is_unix=False,
                require_strong=False,
                query_start_pos=query_start_pos,
                skip_kv_query_at=guard_query_at,
                skip_hash_after_kv_query=host_part_ok and '@' not in suffix_head,
            )
            if at_idx is not None:
                return rest[:at_idx], rest[at_idx + 1 :]
        return None

    user_part, _, pass_part = prefix.partition(':')
    multi_host_ok, multi_host_strong = (
        _is_host_or_host_list(prefix, require_all_strong_for_list=True)
        if ',' in prefix
        else (False, False)
    )
    mixed_multi_host_ok, mixed_multi_host_strong = (
        _is_host_or_host_list(prefix)
        if ',' in prefix and not multi_host_strong
        else (multi_host_ok, multi_host_strong)
    )
    if (user_part != '' and (pass_part.isdigit() or pass_part == 'port')) or (
        ',' in prefix and (multi_host_strong or mixed_multi_host_strong)
    ):
        suffix_head, suffix_query, _f = _split_query_fragment(suffix)
        host_part_ok = mixed_multi_host_ok or bool(_HOST_PART_RE.match(prefix)) or (
            pass_part == 'port' and bool(_HOST_PART_RE.match(user_part))
        )
        query_start_pos = (first_delim + len(suffix_head)) if suffix_query else -1
        guard_query_at = host_part_ok and '@' not in suffix_head and query_start_pos != -1
        sentinel_service_path_ok = (
            is_sentinel_or_cluster
            and suffix_head.startswith('/')
            and '/' not in suffix_head[1:]
        )
        prefix_is_genuine_host_port = (
            host_part_ok
            and '@' not in suffix_head
            and (is_http_like or _is_valid_redis_db_path(suffix_head) or sentinel_service_path_ok)
        )
        prefix_is_ipv4_or_local = (
            all(_is_ipv4_or_localhost(p.partition(':')[0]) for p in prefix.split(','))
            if ',' in prefix
            else _is_ipv4_or_localhost(user_part)
        )
        if (
            prefix_is_genuine_host_port
            and path_delim_before_qf
            and (prefix_is_ipv4_or_local or is_http_like or (is_sentinel_or_cluster and multi_host_strong))
        ):
            return None
        skip_hash = prefix_is_genuine_host_port and prefix_is_ipv4_or_local
        at_idx = _find_authority_at_in_suffix(
            rest,
            first_delim,
            is_unix=is_unix,
            require_strong=True,
            require_valid_redis_db=(
                not is_unix
                and not is_http_like
                and not (is_sentinel_or_cluster and not prefix_is_ipv4_or_local)
                and prefix_is_genuine_host_port
            ),
            query_start_pos=query_start_pos,
            skip_secret_query_at=guard_query_at,
            skip_hash_after_kv_query=skip_hash,
        )
        if at_idx is not None:
            return rest[:at_idx], rest[at_idx + 1 :]
        if not is_unix and not prefix_is_genuine_host_port:
            at_idx = _find_authority_at_in_suffix(
                rest,
                first_delim,
                is_unix=False,
                require_strong=False,
                query_start_pos=query_start_pos,
                skip_kv_query_at=guard_query_at,
                skip_hash_after_kv_query=False,
            )
            if at_idx is not None:
                return rest[:at_idx], rest[at_idx + 1 :]
        if (
            not is_unix
            and not is_http_like
            and prefix_is_genuine_host_port
            and not prefix_is_ipv4_or_local
        ):
            cand_at = _find_authority_at_in_suffix(
                rest,
                first_delim,
                is_unix=False,
                require_strong=False,
                require_valid_redis_db=True,
                query_start_pos=query_start_pos,
                skip_secret_query_at=guard_query_at,
                skip_kv_query_at=guard_query_at and ',' not in prefix,
                skip_hash_after_kv_query=False,
            )
            if cand_at is not None:
                cand_host = rest[cand_at + 1 :].partition('/')[0].partition('?')[0].partition('#')[0]
                if _is_ipv4_or_localhost(cand_host) or cand_host.startswith('['):
                    return rest[:cand_at], rest[cand_at + 1 :]
        return None

    at_idx = _find_authority_at_in_suffix(
        rest,
        first_delim,
        is_unix=is_unix,
        require_strong=True,
        require_valid_redis_db=(not is_unix and not is_http_like),
    )
    if at_idx is None:
        at_idx = _find_authority_at_in_suffix(
            rest,
            first_delim,
            is_unix=is_unix,
            require_strong=False,
            require_valid_redis_db=(not is_unix and not is_http_like),
        )
    if at_idx is None and not is_unix and not is_http_like:
        at_idx = _find_authority_at_in_suffix(rest, first_delim, is_unix=False, require_strong=True)
    if at_idx is None and not is_unix and not is_http_like:
        at_idx = _find_authority_at_in_suffix(rest, first_delim, is_unix=False, require_strong=False)
    if at_idx is None:
        at_idx = rest.rfind('@')
    return rest[:at_idx], rest[at_idx + 1 :]


def _redact_redis_url_internal(
    url: str,
    *,
    in_outer_query: bool = False,
    redact_nested_schemes: bool = False,
) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ''

    def _maybe_redact_frag(frag: str) -> str:
        return redact_redis_text(frag) if redact_nested_schemes and '://' in frag else frag

    if '://' not in cleaned:
        if not cleaned.startswith('/'):
            authority_split = _split_authority_at(cleaned, is_unix=False, is_http_like=False)
            if authority_split is not None:
                userinfo, host_and_tail = authority_split
                tail_head, tail_query, tail_fragment = _split_query_fragment(host_and_tail)
                redacted_tail_q = _redact_query_part(tail_query, redact_nested_schemes=redact_nested_schemes)
                redacted_tail_frag = _maybe_redact_frag(tail_fragment)
                return f'{_redact_userinfo(userinfo)}@{tail_head}{redacted_tail_q}{redacted_tail_frag}'
        head, query_part, fragment_part = _split_query_fragment(cleaned)
        redacted_q = _redact_query_part(query_part, redact_nested_schemes=redact_nested_schemes)
        redacted_frag = _maybe_redact_frag(fragment_part)
        return f'{head}{redacted_q}{redacted_frag}'

    scheme, _, rest = cleaned.partition('://')
    scheme_lower = scheme.lower()
    is_unix = _is_unix_scheme(scheme_lower)
    is_http_like = scheme_lower in _HTTP_LIKE_SCHEMES
    is_sentinel_or_cluster = scheme_lower.endswith(('+sentinel', '+cluster'))
    is_credential_free_unix = is_unix and rest.startswith('/')

    if redact_nested_schemes and not is_credential_free_unix:
        ns = _find_next_scheme_pos(rest, 0, len(rest))
        if ns != -1:
            trim_pos = _scheme_boundary_before(rest, 0, ns)
            if (
                trim_pos < ns
                and rest[trim_pos] in '!|~^*'
                and rest.find('?', 0, trim_pos) == -1
                and rest.find('#', 0, trim_pos) == -1
            ):
                head_part = rest[:trim_pos]
                head_split = _split_authority_at(
                    head_part,
                    is_unix=is_unix,
                    is_http_like=is_http_like,
                    is_sentinel_or_cluster=is_sentinel_or_cluster,
                )
                tail_has_at = '@' in rest[trim_pos:]
                has_auth_at_after = False
                if tail_has_at and head_split is None:
                    head_host, head_slash, head_db = head_part.partition('/')
                    head_host_name, head_colon, head_port = head_host.partition(':')
                    head_is_ipv4_or_local = (
                        (
                            _is_ipv4_or_localhost(head_host_name)
                            and (not head_colon or head_port.isdigit())
                        )
                        or (head_host.startswith('[') and _is_host_or_host_list(head_host)[0])
                    )
                    head_is_genuine_ipv4_db = (
                        head_slash == '/'
                        and _is_valid_redis_db_path(f'/{head_db}')
                        and head_is_ipv4_or_local
                    )
                    n_rest = len(rest)
                    cur_ns = ns
                    prev_sep_end = trim_pos
                    while cur_ns != -1:
                        nxt_ns = _find_next_scheme_pos(rest, cur_ns + 3, n_rest)
                        seg_end = (
                            _scheme_boundary_before(rest, cur_ns + 3, nxt_ns)
                            if nxt_ns != -1
                            else n_rest
                        )
                        cur_sep_end = cur_ns + 3
                        if rest.find('@', cur_sep_end, seg_end) != -1:
                            s_start = cur_ns
                            while s_start > prev_sep_end and rest[s_start - 1] in _SCHEME_CONT_CHARS:
                                s_start -= 1
                            while s_start < cur_ns and rest[s_start] not in _SCHEME_FIRST_CHARS:
                                s_start += 1
                            seg_scheme_lower = rest[s_start:cur_ns].lower()
                            seg_is_unix = _is_unix_scheme(seg_scheme_lower)
                            seg_is_sentinel_or_cluster = seg_scheme_lower.endswith(
                                ('+sentinel', '+cluster')
                            )
                            q_after = rest.find('?', cur_sep_end, seg_end)
                            h_after = rest.find('#', cur_sep_end, seg_end)
                            qf_after = min(p for p in (q_after, h_after, seg_end) if p != -1)
                            has_path_before_q = q_after != -1 and '/' in rest[cur_sep_end:qf_after]
                            skip_sec_q = q_after != -1 and head_is_genuine_ipv4_db
                            skip_kv_q = q_after != -1 and head_is_genuine_ipv4_db
                            if (
                                seg_is_unix
                                and not head_is_genuine_ipv4_db
                                and cur_sep_end < seg_end
                                and rest[cur_sep_end] != '/'
                            ):
                                if (
                                    _find_authority_at_in_suffix(
                                        rest,
                                        cur_sep_end,
                                        is_unix=True,
                                        query_start_pos=q_after,
                                        skip_secret_query_at=skip_sec_q,
                                        skip_kv_query_at=skip_kv_q,
                                        end=seg_end,
                                    )
                                    is not None
                                ):
                                    has_auth_at_after = True
                                    break
                            seg_require_valid_db = not (
                                seg_is_sentinel_or_cluster and not head_is_ipv4_or_local
                            )
                            auth_at = _find_authority_at_in_suffix(
                                rest,
                                cur_sep_end,
                                is_unix=is_unix,
                                require_strong=True,
                                require_valid_redis_db=seg_require_valid_db,
                                query_start_pos=q_after,
                                skip_secret_query_at=skip_sec_q,
                                skip_kv_query_at=skip_kv_q,
                                end=seg_end,
                            )
                            if auth_at is not None:
                                has_auth_at_after = True
                                break
                            cand_at = _find_authority_at_in_suffix(
                                rest,
                                cur_sep_end,
                                is_unix=is_unix,
                                require_strong=False,
                                require_valid_redis_db=seg_require_valid_db,
                                query_start_pos=q_after,
                                skip_secret_query_at=(q_after != -1),
                                skip_kv_query_at=(
                                    q_after != -1
                                    and (head_is_genuine_ipv4_db or has_path_before_q)
                                ),
                                end=seg_end,
                            )
                            if cand_at is not None:
                                if q_after == -1 or cand_at < q_after:
                                    has_auth_at_after = True
                                    break
                                elif not head_is_genuine_ipv4_db:
                                    cand_host = (
                                        rest[cand_at + 1 : seg_end]
                                        .partition('/')[0]
                                        .partition('?')[0]
                                        .partition('#')[0]
                                    )
                                    if _is_ipv4_or_localhost(cand_host) or cand_host.startswith('['):
                                        has_auth_at_after = True
                                        break
                        prev_sep_end = cur_sep_end
                        cur_ns = nxt_ns
                valid_head = (
                    head_split is not None
                    and _is_valid_host_and_tail(
                        head_split[1],
                        is_unix=is_unix,
                        require_strong=(
                            tail_has_at
                            and not is_unix
                            and not _is_ipv4_or_localhost(head_split[1].partition('/')[0])
                        ),
                    )
                ) or (
                    head_split is None
                    and not has_auth_at_after
                    and _is_valid_host_and_tail(head_part, is_unix=is_unix, require_strong=False)
                )
                if valid_head:
                    redacted_head = _redact_redis_url_internal(
                        f'{scheme}://{head_part}',
                        in_outer_query=in_outer_query,
                        redact_nested_schemes=False,
                    )
                    return f'{redacted_head}{redact_redis_text(rest[trim_pos:])}'

    if in_outer_query and not is_credential_free_unix:
        amp_idx = rest.find('&')
        if amp_idx != -1 and rest.find('?', 0, amp_idx) == -1 and rest.find('#', 0, amp_idx) == -1:
            authority_split = _split_authority_at(
                rest[:amp_idx],
                is_unix=is_unix,
                is_http_like=is_http_like,
                is_sentinel_or_cluster=is_sentinel_or_cluster,
            )
            if authority_split is not None:
                userinfo, host_head = authority_split
                outer_tail = rest[amp_idx + 1 :]
                if '#' in outer_tail:
                    outer_q, _, outer_frag = outer_tail.partition('#')
                    return (
                        f'{scheme}://{_redact_userinfo(userinfo)}@{host_head}'
                        f'&{_redact_query(outer_q, redact_nested_schemes=redact_nested_schemes)}'
                        f'#{_maybe_redact_frag(outer_frag)}'
                    )
                return (
                    f'{scheme}://{_redact_userinfo(userinfo)}@{host_head}'
                    f'&{_redact_query(outer_tail, redact_nested_schemes=redact_nested_schemes)}'
                )

    if not is_credential_free_unix:
        authority_split = _split_authority_at(
            rest,
            is_unix=is_unix,
            is_http_like=is_http_like,
            is_sentinel_or_cluster=is_sentinel_or_cluster,
        )
        if authority_split is not None:
            userinfo, host_and_tail = authority_split
            tail_head, tail_query, tail_fragment = _split_query_fragment(host_and_tail)
            if in_outer_query and '&' in tail_head:
                real_tail_head, _, outer_q = tail_head.partition('&')
                return (
                    f'{scheme}://{_redact_userinfo(userinfo)}@{real_tail_head}'
                    f'&{_redact_query(f"{outer_q}{tail_query}", redact_nested_schemes=redact_nested_schemes)}'
                    f'{_maybe_redact_frag(tail_fragment)}'
                )
            return (
                f'{scheme}://{_redact_userinfo(userinfo)}@{tail_head}'
                f'{_redact_query_part(tail_query, redact_nested_schemes=redact_nested_schemes)}'
                f'{_maybe_redact_frag(tail_fragment)}'
            )

    head, query_part, fragment_part = _split_query_fragment(rest)
    if in_outer_query and '&' in head:
        real_head, _, outer_q = head.partition('&')
        return (
            f'{scheme}://{real_head}'
            f'&{_redact_query(f"{outer_q}{query_part}", redact_nested_schemes=redact_nested_schemes)}'
            f'{_maybe_redact_frag(fragment_part)}'
        )
    redacted_query_part = _redact_query_part(query_part, redact_nested_schemes=redact_nested_schemes)
    redacted_fragment_part = _maybe_redact_frag(fragment_part)
    if redacted_query_part != query_part or redacted_fragment_part != fragment_part:
        return f'{scheme}://{head}{redacted_query_part}{redacted_fragment_part}'
    return cleaned


def redact_redis_url(url: str) -> str:
    """Redact embedded credentials from a Redis URL for safe logging."""
    return _redact_redis_url_internal(url, in_outer_query=False, redact_nested_schemes=True)


def _ends_with_ipv6_host(text: str, end: int, min_idx: int = 0) -> bool:
    if end - min_idx < 4 or text[end - 1] != ']':
        return False
    idx = end - 2
    while idx >= min_idx:
        ch = text[idx]
        if ch == '[':
            return idx < end - 2 and (
                (idx > min_idx and text[idx - 1] == '@') or text.endswith('://', min_idx, idx)
            )
        if ch in ']/?#@' or ch.isspace():
            return False
        idx -= 1
    return False


def _ends_with_close_tag(text: str, start: int, end: int) -> bool:
    """Return ``True`` when ``text[start:end]`` ends with a strict ``</tag>``."""
    if end - start < 4 or text[end - 1] != '>':
        return False
    idx = end - 2
    while idx >= start and text[idx] in _TAG_NAME_CHARS:
        idx -= 1
    return idx < end - 2 and idx - 1 >= start and text[idx - 1 : idx + 1] == '</'


def _find_stripped_url_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _URL_TRAILING_WRAPPER_CHARS:
        ch = text[end - 1]
        if ch == ']' and _ends_with_ipv6_host(text, end, start):
            break
        if ch == '>' and _ends_with_close_tag(text, start, end):
            break
        end -= 1
    return end


def _find_multi_url_split_idx(token: str, prev_start: int, scheme_start: int) -> int | None:
    idx = scheme_start - 1
    while idx >= prev_start:
        if token[idx] in '\'"([{':
            idx -= 1
            if idx >= prev_start and token[idx] == '\\' and token[idx + 1] in ('"', "'"):
                idx -= 1
            continue
        break

    if idx >= prev_start and token[idx] in ('=', ':'):
        key_end = idx - 1
        while key_end >= prev_start and token[key_end] in ('"', "'"):
            key_end -= 1
        key_char_end = key_end
        while key_end >= prev_start and token[key_end] in _URL_SPLIT_KEY_CHARS:
            key_end -= 1
        if key_end < key_char_end and key_end >= prev_start:
            while key_end >= prev_start and token[key_end] in ('"', "'"):
                key_end -= 1
            if key_end >= prev_start and token[key_end] in _URL_SPLIT_KEY_SEPARATORS:
                idx = key_end

    while idx >= prev_start and (token[idx] in _URL_SPLIT_LEADING_DELIMS or token[idx] == '?'):
        if token[idx] == ']' and _ends_with_ipv6_host(token, idx + 1, prev_start):
            break
        if token[idx] == '>' and _ends_with_close_tag(token, prev_start, idx + 1):
            break
        if token[idx] in ('&', '?'):
            idx -= 1
            break
        idx -= 1

    split_idx = idx + 1
    if split_idx <= prev_start or split_idx >= scheme_start:
        return None
    return split_idx


def _is_key_eq_prefix(text: str, pos: int, end: int) -> bool:
    p = pos
    while p < end and text[p] in _URL_SPLIT_KEY_CHARS:
        p += 1
    return p > pos and p < end and text[p] == '='


def _is_outer_envelope_delim(text: str, idx: int, end: int) -> bool:
    ch = text[idx]
    if ch == '"':
        return (
            idx + 1 >= end
            or text[idx + 1] in '}]:>'
            or (text[idx + 1] == ',' and (idx + 2 >= end or text[idx + 2] in '"{['))
            or _is_key_eq_prefix(text, idx + 1, end)
        )
    if ch == "'":
        return (
            idx + 1 >= end
            or text[idx + 1] in ',}]:>'
            or _is_key_eq_prefix(text, idx + 1, end)
        )
    if ch == '}':
        return idx + 1 >= end or text[idx + 1] in ',}]":\'>'
    if ch == '<':
        return idx + 1 < end and text[idx + 1] == '/'
    if ch in (',', ';'):
        return _is_key_eq_prefix(text, idx + 1, end)
    return False


def _is_hard_structural_delim(text: str, idx: int, end: int) -> bool:
    ch = text[idx]
    if ch in _HARD_STRUCTURAL_DELIMS:
        return True
    return ch == "'" and idx + 1 < end and text[idx + 1] in ',)>]}:'


def _is_strict_close_tag(text: str, pos: int, end: int) -> bool:
    if pos < 0 or pos + 2 >= end or text[pos : pos + 2] != '</':
        return False
    p = pos + 2
    while p < end and text[p] in _TAG_NAME_CHARS:
        p += 1
    return p > pos + 2 and p < end and text[p] == '>'


def _scan_json_literal_end(text: str, pos: int, end: int) -> int | None:
    if pos >= end:
        return None
    ch = text[pos]
    for lit in ('true', 'false', 'null'):
        if text.startswith(lit, pos, end):
            lit_end = pos + len(lit)
            return lit_end if lit_end < end else None
    p = pos
    if ch == '-':
        p += 1
        if p >= end:
            return None
    if not text[p].isdigit():
        return None
    while p < end and text[p].isdigit():
        p += 1
    if p < end and text[p] == '.':
        p += 1
        if p >= end or not text[p].isdigit():
            return None
        while p < end and text[p].isdigit():
            p += 1
    if p < end and text[p] in ('e', 'E'):
        p += 1
        if p < end and text[p] in ('+', '-'):
            p += 1
        if p >= end or not text[p].isdigit():
            return None
        while p < end and text[p].isdigit():
            p += 1
    return p


def _is_strict_outer_envelope_after(
    seg: str,
    idx: int,
    end: int,
    *,
    has_authority: bool,
    start: int,
    next_scheme_pos: int,
    last_close_tag_pos: int,
    cursor: _TokenScanCursor | None = None,
) -> bool:
    ch = seg[idx]
    if ch == '<':
        return _is_strict_close_tag(seg, idx, end)

    if ch in ('"', "'"):
        if idx + 1 >= end:
            return True
        nxt = seg[idx + 1]
        if nxt in ('}', ']'):
            if idx + 2 >= end or seg[idx + 2] in ',}]':
                return True
            return seg[idx + 2] == ':' and next_scheme_pos == idx + 3
        if nxt == ',':
            if idx + 2 >= end:
                return True
            after_comma = seg[idx + 2]
            if after_comma in ('"', "'"):
                q = after_comma
                p = idx + 3
                while p < end and not seg[p].isspace() and seg[p] not in '"\'<}@':
                    p += 1
                if p < end and seg[p] == q:
                    return p + 1 < end and seg[p + 1] in ':,]}'
                if p < end and seg[p] == '@':
                    p2 = p + 1
                    while p2 < end and not seg[p2].isspace() and seg[p2] not in '"\'<}@:':
                        p2 += 1
                    return p2 < end and seg[p2] == q and p2 + 1 < end and seg[p2 + 1] in ',]}'
                return False
            if after_comma in ('{', '['):
                if not has_authority:
                    return False
                if cursor is not None:
                    p = min(cursor.find_json_open_stop(idx + 3), end)
                else:
                    m = _JSON_OPEN_STOP_RE.search(seg, idx + 3, end)
                    p = m.start() if m is not None else end
                return p < end and seg[p] in '}]' and (p + 1 >= end or seg[p + 1] in ',}]')
            lit_end = _scan_json_literal_end(seg, idx + 2, end)
            return lit_end is not None and lit_end < end and seg[lit_end] in ',}]'
        if nxt == '>':
            if not has_authority:
                return False
            if ch == '"' and idx > start and seg[idx - 1] == '\\':
                return False
            return last_close_tag_pos > idx or (next_scheme_pos != -1 and idx < next_scheme_pos < end)
        return False

    if ch == '}':
        if not has_authority:
            return False
        if idx + 1 >= end or seg[idx + 1] in '}]':
            return True
        if seg[idx + 1] == ',':
            if idx + 2 >= end or seg[idx + 2] in '"{[':
                return True
            lit_end = _scan_json_literal_end(seg, idx + 2, end)
            return lit_end is not None and lit_end < end and seg[lit_end] in ',}]'
        return False

    return False


def _scan_host_candidate(text: str, at_idx: int, end: int) -> tuple[int, bool] | None:
    """Scan a ``host[:port]`` right after ``@`` at *at_idx*, returning ``(host_end, is_strong)``."""
    p = at_idx + 1
    if p >= end:
        return None
    if text[p] == '[':
        p += 1
        has_colon = False
        while p < end and not text[p].isspace() and text[p] not in _HOST_SCAN_STOP_BRACKET:
            if text[p] == ':':
                has_colon = True
            p += 1
        if p >= end or text[p] != ']' or not has_colon:
            return None
        p += 1
        if p < end and text[p] == ':':
            d = p + 1
            while d < end and text[d].isdigit():
                d += 1
            if d == p + 1:
                return None
            p = d
        return p, True

    while p < end and not text[p].isspace() and text[p] not in _HOST_SCAN_STOP_PLAIN:
        p += 1
    if p == at_idx + 1:
        return None
    is_strong = False
    if p < end and text[p] == ':':
        d = p + 1
        while d < end and text[d].isdigit():
            d += 1
        if d == p + 1:
            return None
        p = d
        is_strong = True
    return p, is_strong


def _has_strong_host_before_structural_delim(token: str, at_idx: int, end: int) -> bool:
    scanned = _scan_host_candidate(token, at_idx, end)
    if scanned is None:
        return False
    host_end, is_strong = scanned
    if host_end >= end:
        return False
    ch = token[host_end]
    if ch in _STRUCTURAL_TRAILING_DELIMS:
        return is_strong or _is_outer_envelope_delim(token, host_end, end)
    if ch == '/':
        path_end = host_end + 1
        while (
            path_end < end
            and not token[path_end].isspace()
            and token[path_end] not in '@?#"\'<>)]},;'
        ):
            path_end += 1
        return path_end < end and token[path_end] in _STRUCTURAL_TRAILING_DELIMS
    if ch in '?#':
        q_end = host_end + 1
        while q_end < end and not token[q_end].isspace() and token[q_end] not in '@"\'<>}':
            q_end += 1
        return is_strong and q_end < end and _is_hard_structural_delim(token, q_end, end)
    return False


def _scheme_boundary_before(seg: str, min_pos: int, scheme_sep: int) -> int:
    s_start = scheme_sep
    while s_start > min_pos and seg[s_start - 1] in _SCHEME_CONT_CHARS:
        s_start -= 1
    while s_start < scheme_sep and seg[s_start] not in _SCHEME_FIRST_CHARS:
        s_start += 1
    if s_start < scheme_sep and s_start > min_pos and seg[s_start - 1] in '!|~^*':
        return s_start - 1
    return s_start


def _has_secret_param_in_query(seg: str, q_start: int, end: int) -> bool:
    q_bound = end
    search_pos = q_start
    while (match := _QUERY_END_SCAN_RE.search(seg, search_pos, end)) is not None:
        j = match.start()
        c = seg[j]
        if c == '#' or c.isspace() or _is_hard_structural_delim(seg, j, end):
            q_bound = j
            break
        search_pos = j + 1

    ns = seg.find('://', q_start, q_bound)
    item_start = q_start
    while item_start < q_bound:
        scan_limit = q_bound
        trimmed_at_scheme = False
        while ns != -1 and ns >= item_start:
            trim_pos = _scheme_boundary_before(seg, item_start, ns)
            if trim_pos < ns and seg[trim_pos] in '!|~^*':
                scan_limit = trim_pos
                trimmed_at_scheme = True
                break
            ns = seg.find('://', ns + 3, q_bound)
        next_amp = seg.find('&', item_start, scan_limit)
        item_end = next_amp if next_amp != -1 else scan_limit
        eq_idx = seg.find('=', item_start, item_end)
        if eq_idx != -1:
            raw_key = seg[item_start:eq_idx]
            norm_key = (unquote(raw_key) if '%' in raw_key else raw_key).strip().lower()
            if norm_key in _SECRET_QUERY_KEYS:
                return True
        if next_amp != -1:
            item_start = next_amp + 1
            if ns != -1 and ns < item_start:
                ns = seg.find('://', item_start, q_bound)
            continue
        if trimmed_at_scheme:
            return False
        break
    return False


def _is_ipv4_or_localhost(host: str) -> bool:
    if host.lower() == 'localhost':
        return True
    parts = host.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or len(part) > 3 or int(part) > 255:
            return False
    return True


def _is_query_hard_stop(
    seg: str,
    j: int,
    end: int,
    *,
    q_start: int,
    has_outer_dquote: bool = False,
    has_outer_angle: bool = False,
    has_outer_brace: bool = False,
    has_outer_squote: bool = False,
    in_outer_query: bool = False,
    wrappers_end_at_amp: bool = False,
) -> bool:
    ch = seg[j]
    if ch == '"':
        if j > 0 and seg[j - 1] == '\\':
            bs = 1
            k = j - 2
            while k >= 0 and seg[k] == '\\':
                bs += 1
                k -= 1
            if bs % 2 == 1:
                return False
        if j + 1 < end:
            nxt = seg[j + 1]
            if (not has_outer_dquote or in_outer_query) and wrappers_end_at_amp:
                return False
            if (j == q_start or seg[j - 1] in '=(\'[{<') and nxt not in ',});>]:' and not nxt.isspace():
                return False
        return True
    if ch == '<':
        if not _is_strict_close_tag(seg, j, end) and (j == q_start or seg[j - 1] in '="\'([{'):
            return False
        return True
    if ch == '>':
        if (not has_outer_angle or in_outer_query) and wrappers_end_at_amp:
            return False
        return True
    if ch == '}':
        if (not has_outer_brace or in_outer_query) and wrappers_end_at_amp:
            return False
        return True
    if ch == "'":
        if (not has_outer_squote or in_outer_query) and wrappers_end_at_amp:
            return False
        return j + 1 < end and seg[j + 1] in ',)>]}:'
    return False


def _gap_stays_in_outer_query(
    token: str,
    seg_start: int,
    seg_end: int,
    gap_end: int,
    was_in_outer_query: bool,
) -> bool:
    if seg_end > gap_end:
        return False
    if seg_end == gap_end:
        if not (seg_end > seg_start and token[seg_end - 1] == '='):
            return False
    else:
        idx = seg_end
        while idx < gap_end:
            if token[idx] == '\\' and idx + 1 < gap_end and token[idx + 1] in ('"', "'"):
                idx += 2
                continue
            if (
                idx == seg_end
                and seg_end > seg_start
                and token[seg_end - 1] in ('&', '?', '=')
                and token[idx] in '!|~^*'
            ):
                idx += 1
                continue
            if token[idx] not in '"\'<{([':
                return False
            idx += 1
    if was_in_outer_query:
        return token.find('#', seg_start, seg_end) == -1
    q_idx = token.find('?', seg_start, seg_end)
    return q_idx != -1 and token.find('#', q_idx + 1, seg_end) == -1


def _scan_fragment_end(
    seg: str,
    frag_start: int,
    hard_end: int,
    *,
    has_outer_paren: bool,
    has_outer_bracket: bool,
) -> int:
    search_s = frag_start
    while (ns := seg.find('://', search_s, hard_end)) != -1:
        trim_pos = _scheme_boundary_before(seg, frag_start, ns)
        if trim_pos < ns and seg[trim_pos] in '!|~^*':
            hard_end = trim_pos
            break
        search_s = ns + 3
    paren_depth = 0
    bracket_depth = 0
    j = frag_start
    while j < hard_end:
        c = seg[j]
        if c.isspace() or _is_hard_structural_delim(seg, j, hard_end):
            return j
        if c in (',', ';') and _is_key_eq_prefix(seg, j + 1, hard_end):
            return j
        if c == '(':
            paren_depth += 1
        elif c == ')':
            if paren_depth > 0:
                paren_depth -= 1
            elif has_outer_paren:
                return j
        elif c == '[':
            bracket_depth += 1
        elif c == ']':
            if bracket_depth > 0:
                bracket_depth -= 1
            elif has_outer_bracket:
                return j
        j += 1
    return hard_end


def _scan_query_and_fragment_end(
    seg: str,
    q_start: int,
    hard_end: int,
    *,
    has_outer_paren: bool,
    has_outer_bracket: bool,
    has_outer_dquote: bool = False,
    has_outer_angle: bool = False,
    has_outer_brace: bool = False,
    has_outer_squote: bool = False,
    in_outer_query: bool = False,
) -> int:
    wrapper_amp_until = -1
    non_amp_wrapper_until = -1
    cached_limit = -1

    def _wrappers_end_at_amp(pos: int, limit: int) -> bool:
        nonlocal wrapper_amp_until, non_amp_wrapper_until, cached_limit
        if cached_limit != limit:
            cached_limit = limit
            wrapper_amp_until = -1
            non_amp_wrapper_until = -1
        if pos < wrapper_amp_until:
            return True
        if pos < non_amp_wrapper_until:
            return False
        w = pos
        while w < limit and seg[w] in _CLOSING_WRAPPER_CHARS:
            w += 1
        if w > pos and w < limit and seg[w] == '&':
            wrapper_amp_until = w
            return True
        non_amp_wrapper_until = w
        return False

    search_s = q_start
    prev_checked = q_start
    cur_param_start = q_start
    cur_param_has_eq = False
    cur_param_secret = False
    while (ns := seg.find('://', search_s, hard_end)) != -1:
        trim_pos = _scheme_boundary_before(seg, q_start, ns)
        if trim_pos < ns and seg[trim_pos] in '!|~^*':
            if seg.find('#', prev_checked, trim_pos) != -1:
                break
            hit_hard_stop = False
            while prev_checked < trim_pos:
                ch = seg[prev_checked]
                w_amp_ok = (
                    _wrappers_end_at_amp(prev_checked, trim_pos)
                    if ch in _CLOSING_WRAPPER_CHARS
                    else False
                )
                if ch.isspace() or _is_query_hard_stop(
                    seg,
                    prev_checked,
                    trim_pos,
                    q_start=q_start,
                    has_outer_dquote=has_outer_dquote,
                    has_outer_angle=has_outer_angle,
                    has_outer_brace=has_outer_brace,
                    has_outer_squote=has_outer_squote,
                    in_outer_query=in_outer_query,
                    wrappers_end_at_amp=w_amp_ok,
                ):
                    hit_hard_stop = True
                    break
                if ch == '&':
                    cur_param_start = prev_checked + 1
                    cur_param_has_eq = False
                    cur_param_secret = False
                elif ch == '=' and not cur_param_has_eq:
                    cur_param_has_eq = True
                    cur_param_secret = unquote(seg[cur_param_start:prev_checked]).strip().lower() in _SECRET_QUERY_KEYS
                prev_checked += 1
            if hit_hard_stop:
                break
            if not cur_param_secret:
                hard_end = trim_pos
                break
        search_s = ns + 3

    query_end = hard_end
    q_scan_pos = q_start
    while (match := _QUERY_END_SCAN_RE.search(seg, q_scan_pos, hard_end)) is not None:
        j = match.start()
        c = seg[j]
        w_amp_ok = _wrappers_end_at_amp(j, hard_end) if c in _CLOSING_WRAPPER_CHARS else False
        if (
            c == '#'
            or c.isspace()
            or _is_query_hard_stop(
                seg,
                j,
                hard_end,
                q_start=q_start,
                has_outer_dquote=has_outer_dquote,
                has_outer_angle=has_outer_angle,
                has_outer_brace=has_outer_brace,
                has_outer_squote=has_outer_squote,
                in_outer_query=in_outer_query,
                wrappers_end_at_amp=w_amp_ok,
            )
        ):
            query_end = j
            break
        q_scan_pos = j + 1

    secret_intervals: list[tuple[int, int]] = []
    item_start = q_start
    while item_start <= query_end:
        next_amp = seg.find('&', item_start, query_end)
        item_end = next_amp if next_amp != -1 else query_end
        eq_idx = seg.find('=', item_start, item_end)
        if eq_idx != -1:
            raw_key = seg[item_start:eq_idx]
            norm_key = (unquote(raw_key) if '%' in raw_key else raw_key).strip().lower()
            if norm_key in _SECRET_QUERY_KEYS:
                secret_intervals.append((eq_idx + 1, item_end))
        if next_amp == -1:
            break
        item_start = next_amp + 1

    iv_idx = 0
    num_ivs = len(secret_intervals)
    last_secret_start = secret_intervals[-1][0] if secret_intervals else -1
    paren_depth = 0
    bracket_depth = 0
    d_scan_pos = q_start
    while (d_match := _QUERY_DELIM_SCAN_RE.search(seg, d_scan_pos, query_end)) is not None:
        j = d_match.start()
        while iv_idx < num_ivs and secret_intervals[iv_idx][1] <= j:
            iv_idx += 1
        in_secret = iv_idx < num_ivs and secret_intervals[iv_idx][0] <= j < secret_intervals[iv_idx][1]
        has_secret_after = last_secret_start > j
        c = seg[j]
        if c in (',', ';'):
            if not in_secret and not has_secret_after and _is_key_eq_prefix(seg, j + 1, query_end):
                return j
        elif c == '(':
            paren_depth += 1
        elif c == ')':
            if paren_depth > 0:
                paren_depth -= 1
            elif not (in_outer_query and _wrappers_end_at_amp(j, query_end)):
                if in_secret:
                    if has_outer_paren:
                        return j
                elif not has_secret_after:
                    return j
        elif c == '[':
            bracket_depth += 1
        elif c == ']':
            if bracket_depth > 0:
                bracket_depth -= 1
            elif not (in_outer_query and _wrappers_end_at_amp(j, query_end)):
                if in_secret:
                    if has_outer_bracket:
                        return j
                elif not has_secret_after:
                    return j
        d_scan_pos = j + 1

    if query_end < hard_end and seg[query_end] == '#':
        return _scan_fragment_end(
            seg,
            query_end + 1,
            hard_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
        )
    return query_end


def _scan_path_end(seg: str, slash_idx: int, end: int) -> int:
    path_end = slash_idx + 1
    while path_end < end and not seg[path_end].isspace() and seg[path_end] not in '@?#"\'<>)]},;':
        path_end += 1
    return path_end


def _find_hard_or_outer_delim_end(
    seg: str,
    pos: int,
    end: int,
    *,
    has_outer_paren: bool,
    has_outer_bracket: bool,
) -> int:
    if not has_outer_paren and not has_outer_bracket:
        search_pos = pos
        while (match := _HARD_OR_WS_SCAN_RE.search(seg, search_pos, end)) is not None:
            j = match.start()
            if seg[j].isspace() or _is_hard_structural_delim(seg, j, end):
                return j
            search_pos = j + 1
        return end

    paren_depth = 0
    bracket_depth = 0
    j = pos
    while j < end:
        c = seg[j]
        if c.isspace() or _is_hard_structural_delim(seg, j, end):
            return j
        if c == '(':
            paren_depth += 1
        elif c == ')':
            if paren_depth > 0:
                paren_depth -= 1
            elif has_outer_paren:
                return j
        elif c == '[':
            bracket_depth += 1
        elif c == ']':
            if bracket_depth > 0:
                bracket_depth -= 1
            elif has_outer_bracket:
                return j
        j += 1
    return end


@dataclass
class _TokenScanCursor:
    text: str
    end: int
    at_pos: int = -2
    at_checked_from: int = 0
    at_slash_pos: int = -2
    at_slash_checked_from: int = 0
    struct_delim_pos: int = -2
    struct_delim_checked_from: int = 0
    close_tag_pos: int = -2
    close_tag_min: int = 0
    json_stop_pos: int = -2
    json_stop_checked_from: int = 0
    nw_starts: list[int] = field(default_factory=list)
    nw_ends: list[int] = field(default_factory=list)

    def find_at(self, pos: int) -> int:
        if pos >= self.end:
            return -1
        if self.at_pos == -1:
            if pos >= self.at_checked_from:
                return -1
            res = self.text.find('@', pos, self.at_checked_from)
            self.at_checked_from = pos
            if res != -1:
                self.at_pos = res
            return res
        if self.at_pos != -2 and self.at_checked_from <= pos <= self.at_pos:
            return self.at_pos
        if self.at_pos != -2 and pos < self.at_checked_from:
            res = self.text.find('@', pos, self.at_checked_from)
            self.at_checked_from = pos
            if res != -1:
                self.at_pos = res
                return res
            return self.at_pos
        res = self.text.find('@', pos, self.end)
        self.at_checked_from = pos
        self.at_pos = res
        return res

    def find_at_slash(self, pos: int) -> int:
        if pos >= self.end:
            return -1
        if self.at_slash_pos == -1:
            if pos >= self.at_slash_checked_from:
                return -1
            hi = min(self.end, self.at_slash_checked_from + 1)
            res = self.text.find('@/', pos, hi)
            self.at_slash_checked_from = pos
            if res != -1:
                self.at_slash_pos = res
            return res
        if self.at_slash_pos != -2 and self.at_slash_checked_from <= pos <= self.at_slash_pos:
            return self.at_slash_pos
        if self.at_slash_pos != -2 and pos < self.at_slash_checked_from:
            hi = min(self.end, self.at_slash_checked_from + 1)
            res = self.text.find('@/', pos, hi)
            self.at_slash_checked_from = pos
            if res != -1:
                self.at_slash_pos = res
                return res
            return self.at_slash_pos
        res = self.text.find('@/', pos, self.end)
        self.at_slash_checked_from = pos
        self.at_slash_pos = res
        return res

    def has_struct_delim(self, pos: int) -> bool:
        if pos >= self.end:
            return False
        if self.struct_delim_pos == -1 and pos >= self.struct_delim_checked_from:
            return False
        if self.struct_delim_pos != -2 and self.struct_delim_checked_from <= pos <= self.struct_delim_pos:
            return True
        if self.struct_delim_pos != -2 and pos < self.struct_delim_checked_from:
            m = _STRUCTURAL_TRAILING_DELIM_RE.search(self.text, pos, self.struct_delim_checked_from)
            self.struct_delim_checked_from = pos
            if m is not None:
                self.struct_delim_pos = m.start()
                return True
            return self.struct_delim_pos != -1
        m = _STRUCTURAL_TRAILING_DELIM_RE.search(self.text, pos, self.end)
        self.struct_delim_checked_from = pos
        self.struct_delim_pos = m.start() if m is not None else -1
        return self.struct_delim_pos != -1

    def get_last_close_tag(self, min_pos: int) -> int:
        if min_pos >= self.end:
            return -1
        if self.close_tag_pos == -2:
            search_hi = self.end
            found = -1
            while (pos_ct := self.text.rfind('</', min_pos, search_hi)) != -1:
                if _is_strict_close_tag(self.text, pos_ct, self.end):
                    found = pos_ct
                    break
                search_hi = pos_ct
            self.close_tag_pos = found
            self.close_tag_min = min_pos
            return found
        if min_pos >= self.close_tag_min:
            return self.close_tag_pos if self.close_tag_pos >= min_pos else -1
        if self.close_tag_pos != -1:
            return self.close_tag_pos
        search_hi = min(self.end, self.close_tag_min + 1)
        found = -1
        while (pos_ct := self.text.rfind('</', min_pos, search_hi)) != -1:
            if _is_strict_close_tag(self.text, pos_ct, self.end):
                found = pos_ct
                break
            search_hi = pos_ct
        if found != -1:
            self.close_tag_pos = found
        self.close_tag_min = min_pos
        return found

    def find_json_open_stop(self, pos: int) -> int:
        if pos >= self.end:
            return self.end
        if self.json_stop_pos != -2 and self.json_stop_checked_from <= pos <= self.json_stop_pos:
            return self.json_stop_pos
        if self.json_stop_pos != -2 and pos < self.json_stop_checked_from:
            m = _JSON_OPEN_STOP_RE.search(self.text, pos, self.json_stop_checked_from)
            self.json_stop_checked_from = pos
            if m is not None:
                self.json_stop_pos = m.start()
            return self.json_stop_pos
        m = _JSON_OPEN_STOP_RE.search(self.text, pos, self.end)
        self.json_stop_checked_from = pos
        self.json_stop_pos = m.start() if m is not None else self.end
        return self.json_stop_pos

    def find_non_wrapper_after(self, pos: int) -> int:
        if pos >= self.end:
            return self.end
        starts = self.nw_starts
        ends = self.nw_ends
        idx = bisect_right(starts, pos)
        if idx > 0 and pos < ends[idx - 1]:
            return ends[idx - 1]
        p = pos
        next_start = starts[idx] if idx < len(starts) else self.end
        while p < next_start and self.text[p] in _CLOSING_WRAPPER_CHARS:
            p += 1
        if p == pos:
            return pos
        if p == next_start and idx < len(starts):
            final_end = ends[idx]
            if idx > 0 and pos == ends[idx - 1]:
                ends[idx - 1] = final_end
                del starts[idx]
                del ends[idx]
            else:
                starts[idx] = pos
            return final_end
        if idx > 0 and pos == ends[idx - 1]:
            ends[idx - 1] = p
            return p
        starts.insert(idx, pos)
        ends.insert(idx, p)
        return p


def _closing_wrapper_amp_after(cursor: _TokenScanCursor, pos: int, end: int) -> int | None:
    amp = cursor.find_non_wrapper_after(pos)
    if pos < amp < end and cursor.text[amp] == '&':
        return amp
    return None


def _extend_multi_host_end(
    seg: str,
    host_end: int,
    has_port: bool,
    cand_end: int,
    *,
    in_outer_query: bool = False,
) -> tuple[int, bool]:
    cur_end = host_end
    any_strong = has_port
    valid_end = host_end
    valid_strong = has_port
    while (
        cur_end < cand_end
        and seg[cur_end] == ','
        and not _is_key_eq_prefix(seg, cur_end + 1, cand_end)
    ):
        nxt_scan = _scan_host_candidate(seg, cur_end, cand_end)
        if nxt_scan is None:
            break
        nxt_end, nxt_has_port = nxt_scan
        if nxt_end < cand_end and seg[nxt_end] == '@':
            break
        if nxt_has_port:
            any_strong = True
        cur_end = nxt_end
        if any_strong and (
            nxt_has_port
            or (
                nxt_end < cand_end
                and (seg[nxt_end] in '/?#' or (in_outer_query and seg[nxt_end] == '&'))
            )
        ):
            valid_end = nxt_end
            valid_strong = True
    return valid_end, valid_strong


def _find_next_scheme_pos(seg: str, pos: int, end: int) -> int:
    search_pos = pos
    while (sep := seg.find('://', search_pos, end)) != -1:
        cand = sep
        while cand > search_pos and seg[cand - 1] in _SCHEME_CONT_CHARS:
            cand -= 1
        while cand < sep and seg[cand] not in _SCHEME_FIRST_CHARS:
            cand += 1
        if cand < sep:
            return sep
        search_pos = sep + 3
    return -1


def _split_structural_trailing_idx(
    seg: str,
    start: int,
    end: int,
    *,
    has_outer_paren: bool = False,
    has_outer_bracket: bool = False,
    has_outer_dquote: bool = False,
    has_outer_angle: bool = False,
    has_outer_brace: bool = False,
    has_outer_squote: bool = False,
    scan_cursor: _TokenScanCursor | None = None,
    in_outer_query: bool = False,
) -> int:
    scheme_sep = seg.find('://', start, end)
    if scheme_sep == -1:
        return end
    scheme_end = scheme_sep + 3
    cursor = (
        scan_cursor
        if (scan_cursor is not None and scan_cursor.text is seg and scan_cursor.end == end)
        else _TokenScanCursor(seg, end)
    )
    next_scheme_pos = _find_next_scheme_pos(seg, scheme_end, end)
    if not cursor.has_struct_delim(scheme_end) and next_scheme_pos == -1:
        return end

    scheme_lower = seg[start:scheme_sep].lower()
    is_unix = _is_unix_scheme(scheme_lower)
    is_http_like = scheme_lower in _HTTP_LIKE_SCHEMES

    if is_unix:
        if scheme_end < end and seg[scheme_end] == '/':
            unix_end = end
            if next_scheme_pos != -1:
                trim_pos = _scheme_boundary_before(seg, scheme_end, next_scheme_pos)
                if trim_pos < next_scheme_pos and seg[trim_pos] in '!|~^*':
                    unix_end = trim_pos
            unix_delim = _find_hard_or_outer_delim_end(
                seg,
                scheme_end,
                unix_end,
                has_outer_paren=has_outer_paren,
                has_outer_bracket=has_outer_bracket,
            )
            if in_outer_query and (w_amp := _closing_wrapper_amp_after(cursor, unix_delim, end)) is not None:
                return _scan_query_and_fragment_end(
                    seg,
                    w_amp + 1,
                    end,
                    has_outer_paren=has_outer_paren,
                    has_outer_bracket=has_outer_bracket,
                    has_outer_dquote=has_outer_dquote,
                    has_outer_angle=has_outer_angle,
                    has_outer_brace=has_outer_brace,
                    has_outer_squote=has_outer_squote,
                    in_outer_query=in_outer_query,
                )
            return unix_delim
        pos = scheme_end
        best_end: int | None = None
        unix_ns_pos = -1
        unix_ns_trim = -1
        while (at_idx := cursor.find_at_slash(pos)) != -1:
            if next_scheme_pos != -1 and at_idx > next_scheme_pos:
                break
            unix_bound = end
            if next_scheme_pos != -1 and at_idx < next_scheme_pos:
                if unix_ns_pos != next_scheme_pos:
                    unix_ns_pos = next_scheme_pos
                    trim_pos = _scheme_boundary_before(seg, scheme_end, next_scheme_pos)
                    unix_ns_trim = (
                        trim_pos
                        if (trim_pos < next_scheme_pos and seg[trim_pos] in '!|~^*')
                        else -1
                    )
                if unix_ns_trim > at_idx + 2:
                    unix_bound = unix_ns_trim
            next_at_slash_immediate = cursor.find_at_slash(at_idx + 2)
            scan_limit = (
                min(unix_bound, next_at_slash_immediate)
                if next_at_slash_immediate != -1
                else unix_bound
            )
            j = _find_hard_or_outer_delim_end(
                seg,
                at_idx + 2,
                scan_limit,
                has_outer_paren=has_outer_paren,
                has_outer_bracket=has_outer_bracket,
            )
            q_pos = seg.find('?', at_idx + 2, j)
            h_pos = seg.find('#', at_idx + 2, j)
            if q_pos != -1 and (h_pos == -1 or q_pos < h_pos):
                return _scan_query_and_fragment_end(
                    seg,
                    q_pos + 1,
                    end,
                    has_outer_paren=has_outer_paren,
                    has_outer_bracket=has_outer_bracket,
                    has_outer_dquote=has_outer_dquote,
                    has_outer_angle=has_outer_angle,
                    has_outer_brace=has_outer_brace,
                    has_outer_squote=has_outer_squote,
                    in_outer_query=in_outer_query,
                )
            if h_pos != -1:
                return _scan_fragment_end(
                    seg,
                    h_pos + 1,
                    end,
                    has_outer_paren=has_outer_paren,
                    has_outer_bracket=has_outer_bracket,
                )
            if j >= unix_bound:
                return unix_bound
            if next_at_slash_immediate != -1 and j == next_at_slash_immediate:
                pos = next_at_slash_immediate
                continue
            next_at_slash = cursor.find_at_slash(j + 1)
            if next_at_slash == -1:
                return j
            if next_scheme_pos != -1 and next_scheme_pos < j:
                next_scheme_pos = _find_next_scheme_pos(seg, j, end)
            if next_scheme_pos != -1 and j < next_scheme_pos < next_at_slash:
                return j
            last_close_tag_pos = (
                cursor.get_last_close_tag(scheme_end)
                if seg[j] in ('"', "'") and j + 1 < next_at_slash and seg[j + 1] == '>'
                else -1
            )
            if _is_strict_outer_envelope_after(
                seg,
                j,
                next_at_slash,
                has_authority=True,
                start=start,
                next_scheme_pos=next_scheme_pos,
                last_close_tag_pos=last_close_tag_pos,
                cursor=cursor,
            ):
                return j
            best_end = j
            pos = next_at_slash
        return best_end if best_end is not None else end

    # Non-unix: check if '@' exists anywhere in seg[scheme_end:end] without copying tail.
    if cursor.find_at(scheme_end) == -1:
        no_at_end = end
        if next_scheme_pos != -1:
            trim_pos = _scheme_boundary_before(seg, scheme_end, next_scheme_pos)
            if trim_pos < next_scheme_pos and seg[trim_pos] in '!|~^*':
                no_at_end = trim_pos
        if in_outer_query and (amp_pos := seg.find('&', scheme_end, no_at_end)) != -1:
            if seg.find('?', scheme_end, amp_pos) == -1 and seg.find('#', scheme_end, amp_pos) == -1:
                prefix_end = _find_hard_or_outer_delim_end(
                    seg,
                    scheme_end,
                    amp_pos,
                    has_outer_paren=has_outer_paren,
                    has_outer_bracket=has_outer_bracket,
                )
                if prefix_end == amp_pos or _closing_wrapper_amp_after(cursor, prefix_end, no_at_end) == amp_pos:
                    return _scan_query_and_fragment_end(
                        seg,
                        amp_pos + 1,
                        end,
                        has_outer_paren=has_outer_paren,
                        has_outer_bracket=has_outer_bracket,
                        has_outer_dquote=has_outer_dquote,
                        has_outer_angle=has_outer_angle,
                        has_outer_brace=has_outer_brace,
                        has_outer_squote=has_outer_squote,
                        in_outer_query=in_outer_query,
                    )
        head_scan = _scan_host_candidate(seg, scheme_end - 1, no_at_end)
        if head_scan is not None:
            head_end, head_is_strong_no_at = head_scan
            head_end, head_is_strong_no_at = _extend_multi_host_end(
                seg,
                head_end,
                head_is_strong_no_at,
                no_at_end,
                in_outer_query=in_outer_query,
            )
            if head_end < no_at_end:
                h_ch = seg[head_end]
                if h_ch == '/':
                    path_end = _scan_path_end(seg, head_end, no_at_end)
                    if path_end >= no_at_end:
                        return no_at_end
                    if path_end < no_at_end:
                        p_ch = seg[path_end]
                        w_amp = _closing_wrapper_amp_after(cursor, path_end, no_at_end) if in_outer_query else None
                        if p_ch == '?' or (in_outer_query and p_ch == '&') or w_amp is not None:
                            q_next = (w_amp + 1) if w_amp is not None else (path_end + 1)
                            return _scan_query_and_fragment_end(
                                seg,
                                q_next,
                                end,
                                has_outer_paren=has_outer_paren,
                                has_outer_bracket=has_outer_bracket,
                                has_outer_dquote=has_outer_dquote,
                                has_outer_angle=has_outer_angle,
                                has_outer_brace=has_outer_brace,
                                has_outer_squote=has_outer_squote,
                                in_outer_query=in_outer_query,
                            )
                        if p_ch == '#':
                            return _scan_fragment_end(
                                seg,
                                path_end + 1,
                                no_at_end,
                                has_outer_paren=has_outer_paren,
                                has_outer_bracket=has_outer_bracket,
                            )
                        if p_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
                            return path_end
                else:
                    w_amp = _closing_wrapper_amp_after(cursor, head_end, end) if in_outer_query else None
                    if h_ch == '?' or (in_outer_query and h_ch == '&') or w_amp is not None:
                        q_next = (w_amp + 1) if w_amp is not None else (head_end + 1)
                        return _scan_query_and_fragment_end(
                            seg,
                            q_next,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                            has_outer_dquote=has_outer_dquote,
                            has_outer_angle=has_outer_angle,
                            has_outer_brace=has_outer_brace,
                            has_outer_squote=has_outer_squote,
                            in_outer_query=in_outer_query,
                        )
                    if h_ch == '#':
                        return _scan_fragment_end(
                            seg,
                            head_end + 1,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                        )
                    if h_ch in (',', ';') and _is_key_eq_prefix(seg, head_end + 1, end):
                        return head_end
        return _find_hard_or_outer_delim_end(
            seg,
            scheme_end,
            no_at_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
        )

    # Pass 1, Sub-phase 4a: check if the head right after scheme:// is unambiguously credential-free.
    head_scan = _scan_host_candidate(seg, scheme_end - 1, end)
    head_q_pos: int | None = None
    head_key_stop: tuple[int, int] | None = None
    bare_head_stop: int | None = None
    bare_head_is_kv = False
    if head_scan is not None:
        head_end, head_is_strong = head_scan
        head_end, head_is_strong = _extend_multi_host_end(
            seg,
            head_end,
            head_is_strong,
            end,
            in_outer_query=in_outer_query,
        )
        if head_end < end:
            if in_outer_query and (amp_in_head := seg.find('&', scheme_end, head_end)) != -1:
                if seg.find('?', scheme_end, amp_in_head) == -1 and seg.find('#', scheme_end, amp_in_head) == -1:
                    next_at = cursor.find_at(scheme_end)
                    if next_at == -1 or next_at > amp_in_head:
                        return _scan_query_and_fragment_end(
                            seg,
                            amp_in_head + 1,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                            has_outer_dquote=has_outer_dquote,
                            has_outer_angle=has_outer_angle,
                            has_outer_brace=has_outer_brace,
                            has_outer_squote=has_outer_squote,
                            in_outer_query=in_outer_query,
                        )
            head_str = seg[scheme_end:head_end]
            h_ch = seg[head_end]
            if h_ch == '/':
                path_scan_end = end
                can_trim_head_path = False
                if next_scheme_pos != -1 and head_end < next_scheme_pos:
                    trim_pos = _scheme_boundary_before(seg, head_end, next_scheme_pos)
                    if trim_pos < next_scheme_pos and seg[trim_pos] in '!|~^*':
                        path_scan_end = trim_pos
                        can_trim_head_path = True
                if in_outer_query and not can_trim_head_path:
                    amp_pos = seg.find('&', head_end, path_scan_end)
                    if amp_pos != -1:
                        path_scan_end = amp_pos
                path_end = _scan_path_end(seg, head_end, path_scan_end)
                trimmed_head_path = can_trim_head_path and path_end == path_scan_end
                if in_outer_query and not trimmed_head_path:
                    amp_pos = seg.find('&', head_end, path_end)
                    if amp_pos != -1:
                        path_end = amp_pos
                suffix_head = seg[head_end:path_end]
                if (
                    (path_end >= end or seg[path_end] != '@')
                    and (
                        is_http_like
                        or _is_valid_redis_db_path(suffix_head)
                        or (':' not in head_str and not head_str.startswith('['))
                    )
                    and (head_is_strong or suffix_head != '')
                ):
                    if path_end >= end:
                        return end
                    if trimmed_head_path:
                        if (
                            is_http_like
                            or cursor.find_at(path_end) == -1
                            or ':' not in head_str
                            or _is_ipv4_or_localhost(head_str.partition(':')[0])
                            or head_str.startswith('[')
                        ):
                            return path_end
                    p_ch = seg[path_end]
                    w_amp = _closing_wrapper_amp_after(cursor, path_end, end) if in_outer_query else None
                    if p_ch == '?' or (in_outer_query and p_ch == '&') or w_amp is not None:
                        q_next = (w_amp + 1) if w_amp is not None else (path_end + 1)
                        return _scan_query_and_fragment_end(
                            seg,
                            q_next,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                            has_outer_dquote=has_outer_dquote,
                            has_outer_angle=has_outer_angle,
                            has_outer_brace=has_outer_brace,
                            has_outer_squote=has_outer_squote,
                            in_outer_query=in_outer_query,
                        )
                    if p_ch == '#':
                        return _scan_fragment_end(
                            seg,
                            path_end + 1,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                        )
                    if p_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
                        return path_end
                    if cursor.find_at(path_end) == -1 or (
                        next_scheme_pos != -1
                        and path_end > next_scheme_pos
                        and (
                            is_http_like
                            or ':' not in head_str
                            or _is_ipv4_or_localhost(head_str.partition(':')[0])
                            or head_str.startswith('[')
                        )
                    ):
                        return path_end
                    if (
                        is_http_like
                        or ':' not in head_str
                        or _is_ipv4_or_localhost(head_str.partition(':')[0])
                        or head_str.startswith('[')
                    ):
                        if (
                            _is_hard_structural_delim(seg, path_end, end)
                            or (has_outer_paren and p_ch == ')')
                            or (has_outer_bracket and p_ch == ']')
                        ):
                            return path_end
                    else:
                        allow_outer_stop = (
                            (has_outer_paren and p_ch == ')')
                            or (has_outer_bracket and p_ch == ']')
                            or (p_ch == '<' and _is_strict_close_tag(seg, path_end, end))
                            or (
                                (
                                    (p_ch == '"' and has_outer_dquote)
                                    or (p_ch == "'" and has_outer_squote)
                                    or (p_ch == '}' and has_outer_brace)
                                )
                                and _is_strict_outer_envelope_after(
                                    seg,
                                    path_end,
                                    end,
                                    has_authority=True,
                                    start=start,
                                    next_scheme_pos=next_scheme_pos,
                                    last_close_tag_pos=(
                                        cursor.get_last_close_tag(scheme_end)
                                        if p_ch in ('"', "'")
                                        and path_end + 1 < end
                                        and seg[path_end + 1] == '>'
                                        else -1
                                    ),
                                    cursor=cursor,
                                )
                            )
                        )
                        if allow_outer_stop:
                            return path_end
                elif (
                    (path_end >= end or seg[path_end] != '@')
                    and head_is_strong
                    and _is_host_or_host_list(head_str)[0]
                ):
                    if path_end >= end:
                        return end
                    if trimmed_head_path:
                        if (
                            is_http_like
                            or cursor.find_at(path_end) == -1
                            or ':' not in head_str
                            or _is_ipv4_or_localhost(head_str.partition(':')[0])
                            or head_str.startswith('[')
                        ):
                            return path_end
                    p_ch = seg[path_end]
                    w_amp = _closing_wrapper_amp_after(cursor, path_end, end) if in_outer_query else None
                    if p_ch == '?' or (in_outer_query and p_ch == '&') or w_amp is not None:
                        head_q_pos = w_amp if w_amp is not None else path_end
                    elif p_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
                        head_key_stop = (path_end, _TIER_SLASH)
                    elif (
                        cursor.find_at(path_end) == -1
                        or (
                            next_scheme_pos != -1
                            and path_end > next_scheme_pos
                            and (
                                is_http_like
                                or ':' not in head_str
                                or _is_ipv4_or_localhost(head_str.partition(':')[0])
                                or head_str.startswith('[')
                            )
                        )
                        or _is_strict_outer_envelope_after(
                            seg,
                            path_end,
                            end,
                            has_authority=True,
                            start=start,
                            next_scheme_pos=next_scheme_pos,
                            last_close_tag_pos=(
                                cursor.get_last_close_tag(scheme_end)
                                if p_ch in ('"', "'") and path_end + 1 < end and seg[path_end + 1] == '>'
                                else -1
                            ),
                            cursor=cursor,
                        )
                        or (has_outer_paren and p_ch == ')')
                        or (has_outer_bracket and p_ch == ']')
                    ):
                        return path_end
            else:
                w_amp = _closing_wrapper_amp_after(cursor, head_end, end) if in_outer_query else None
                if h_ch in ('?', '#') or (in_outer_query and h_ch == '&') or w_amp is not None:
                    if (':' not in head_str and not head_str.startswith('[')) or (is_http_like and head_is_strong):
                        if h_ch == '#':
                            return _scan_fragment_end(
                                seg,
                                head_end + 1,
                                end,
                                has_outer_paren=has_outer_paren,
                                has_outer_bracket=has_outer_bracket,
                            )
                        q_next = (w_amp + 1) if w_amp is not None else (head_end + 1)
                        return _scan_query_and_fragment_end(
                            seg,
                            q_next,
                            end,
                            has_outer_paren=has_outer_paren,
                            has_outer_bracket=has_outer_bracket,
                            has_outer_dquote=has_outer_dquote,
                            has_outer_angle=has_outer_angle,
                            has_outer_brace=has_outer_brace,
                            has_outer_squote=has_outer_squote,
                            in_outer_query=in_outer_query,
                        )
                    if h_ch != '#' and head_is_strong and _is_host_or_host_list(head_str)[0]:
                        head_q_pos = w_amp if w_amp is not None else head_end
                elif h_ch != '@':
                    if ':' not in head_str and (not head_str.startswith('[') or seg[head_end - 1] == ']'):
                        is_kv_stop = h_ch in (',', ';') and _is_key_eq_prefix(seg, head_end + 1, end)
                        is_bare_stop = (
                            is_kv_stop
                            or _is_strict_outer_envelope_after(
                                seg,
                                head_end,
                                end,
                                has_authority=True,
                                start=start,
                                next_scheme_pos=next_scheme_pos,
                                last_close_tag_pos=(
                                    cursor.get_last_close_tag(scheme_end)
                                    if h_ch in ('"', "'") and head_end + 1 < end and seg[head_end + 1] == '>'
                                    else -1
                                ),
                                cursor=cursor,
                            )
                            or (has_outer_paren and h_ch == ')')
                            or (has_outer_bracket and h_ch == ']')
                        )
                        if is_bare_stop:
                            if cursor.find_at(head_end) == -1:
                                return head_end
                            bare_head_stop = head_end
                            bare_head_is_kv = is_kv_stop
                    elif head_is_strong and _is_strict_outer_envelope_after(
                        seg,
                        head_end,
                        end,
                        has_authority=True,
                        start=start,
                        next_scheme_pos=next_scheme_pos,
                        last_close_tag_pos=(
                            cursor.get_last_close_tag(scheme_end)
                            if h_ch in ('"', "'") and head_end + 1 < end and seg[head_end + 1] == '>'
                            else -1
                        ),
                        cursor=cursor,
                    ):
                        return head_end

    # Pass 1, Sub-phase 4b: authority-first '@' scan.
    # Tuple fields: (at_idx, host_end, has_port, path_end, has_slash, cand_valid_db, query_pos, frag_pos, tier)
    best_auth: tuple[int, int, bool, int, bool, bool, int | None, int | None, int] | None = None
    best_auth_tier = -1
    key_stop: tuple[int, int] | None = head_key_stop
    last_clean_end: int | None = None
    gap_pos = scheme_end
    hard_end = end
    pos = scheme_end

    q_cursor = head_q_pos + 1 if head_q_pos is not None else -1
    q_item_start = q_cursor
    q_item_has_eq = False
    q_item_is_secret = False
    q_ended = False
    q_ended_by_hash = False
    q_any_secret = False
    q_paren_depth = 0
    q_bracket_depth = 0
    cached_head_q_secret: bool | None = None
    eq_cursor = -1
    failed_json_open_until = -1
    q_failed_json_open_until = -1
    head_q_is_safe_host = head_q_pos is not None and (
        is_http_like
        or ':' not in seg[scheme_end:head_q_pos]
        or _is_ipv4_or_localhost(seg[scheme_end:head_q_pos].partition(':')[0])
        or seg[scheme_end:head_q_pos].startswith('[')
    )
    bare_kv_scan_pos = bare_head_stop if (bare_head_stop is not None and bare_head_is_kv) else -1
    bare_kv_has_eq = False
    bare_kv_colon_after_eq = False

    def _head_query_has_secret() -> bool:
        nonlocal cached_head_q_secret
        if cached_head_q_secret is not None:
            return cached_head_q_secret
        assert head_q_pos is not None
        cached_head_q_secret = _has_secret_param_in_query(seg, head_q_pos + 1, end)
        return cached_head_q_secret

    def _make_monotonic_query_secret_checker() -> Callable[[int], bool]:
        last_q_pos = -2
        last_res = False
        qb_from = -1
        qb_pos = end
        qns_from = -1
        qns_bound = -1
        qns_ns = -1
        qns_trim = -1
        qa_from = -1
        qa_bound = -1
        qa_pos = -1
        qe_from = -1
        qe_end = -1
        qe_pos = -1
        qe_min_secret_start = 0
        qt_bound = -1
        qt_scanned_pos = -1
        qt_next_secret = -1

        def _check(q_pos_arg: int) -> bool:
            nonlocal last_q_pos, last_res
            nonlocal qb_from, qb_pos
            nonlocal qns_from, qns_bound, qns_ns, qns_trim
            nonlocal qa_from, qa_bound, qa_pos
            nonlocal qe_from, qe_end, qe_pos, qe_min_secret_start
            nonlocal qt_bound, qt_scanned_pos, qt_next_secret
            if q_pos_arg == last_q_pos:
                return last_res
            q_start = q_pos_arg + 1
            if q_start >= end:
                last_q_pos = q_pos_arg
                last_res = False
                return False

            if qb_from != -1 and qb_from <= q_start <= qb_pos:
                q_bound = qb_pos
            else:
                q_bound = end
                search_pos = q_start
                while (match := _QUERY_END_SCAN_RE.search(seg, search_pos, end)) is not None:
                    j = match.start()
                    c = seg[j]
                    if c == '#' or c.isspace() or _is_hard_structural_delim(seg, j, end):
                        q_bound = j
                        break
                    search_pos = j + 1
                qb_from = q_start
                qb_pos = q_bound

            if q_start >= q_bound:
                last_q_pos = q_pos_arg
                last_res = False
                return False

            if (
                qns_bound == q_bound
                and qns_from != -1
                and qns_from <= q_start
                and (qns_trim == -1 or q_start <= qns_trim)
            ):
                scan_limit = qns_trim if qns_trim != -1 else q_bound
            else:
                scan_limit = q_bound
                found_trim = -1
                found_ns = -1
                ns_cand = seg.find('://', q_start, q_bound)
                while ns_cand != -1:
                    trim_cand = _scheme_boundary_before(seg, q_start, ns_cand)
                    if trim_cand < ns_cand and seg[trim_cand] in '!|~^*':
                        scan_limit = trim_cand
                        found_trim = trim_cand
                        found_ns = ns_cand
                        break
                    ns_cand = seg.find('://', ns_cand + 3, q_bound)
                qns_from = q_start
                qns_bound = q_bound
                qns_ns = found_ns
                qns_trim = found_trim

            if q_start >= scan_limit:
                last_q_pos = q_pos_arg
                last_res = False
                return False

            if (
                qa_bound == scan_limit
                and qa_from != -1
                and qa_from <= q_start
                and (qa_pos == -1 or q_start <= qa_pos)
            ):
                next_amp = qa_pos
            else:
                next_amp = seg.find('&', q_start, scan_limit)
                qa_from = q_start
                qa_bound = scan_limit
                qa_pos = next_amp

            item_end = next_amp if next_amp != -1 else scan_limit
            if (
                qe_end == item_end
                and qe_from != -1
                and qe_from <= q_start
                and (qe_pos == -1 or q_start <= qe_pos)
            ):
                eq_idx = qe_pos
            else:
                eq_idx = seg.find('=', q_start, item_end)
                qe_from = q_start
                qe_end = item_end
                qe_pos = eq_idx
                if eq_idx != -1:
                    p = eq_idx
                    while p > scheme_end:
                        if seg[p - 1].isspace():
                            p -= 1
                        elif p - 3 >= scheme_end and seg[p - 3] == '%' and unquote(seg[p - 3 : p]).isspace():
                            p -= 3
                        elif p - 6 >= scheme_end and seg[p - 6] == '%' and seg[p - 3] == '%' and unquote(seg[p - 6 : p]).isspace():
                            p -= 6
                        else:
                            break
                    non_ws = 0
                    while p > scheme_end and non_ws <= 24:
                        ch_p = seg[p - 1]
                        if ch_p.isspace():
                            p -= 1
                        elif p - 3 >= scheme_end and seg[p - 3] == '%' and unquote(seg[p - 3 : p]).isspace():
                            p -= 3
                        elif p - 6 >= scheme_end and seg[p - 6] == '%' and seg[p - 3] == '%' and unquote(seg[p - 6 : p]).isspace():
                            p -= 6
                        elif ch_p in '_%' or ch_p.isalnum():
                            non_ws += 1
                            p -= 1
                        else:
                            break
                    qe_min_secret_start = p
                else:
                    qe_min_secret_start = item_end

            if eq_idx != -1 and q_start >= qe_min_secret_start:
                raw_key = seg[q_start:eq_idx]
                norm_key = (unquote(raw_key) if '%' in raw_key else raw_key).strip().lower()
                if norm_key in _SECRET_QUERY_KEYS:
                    last_q_pos = q_pos_arg
                    last_res = True
                    return True

            if next_amp == -1:
                last_q_pos = q_pos_arg
                last_res = False
                return False

            tail_start = next_amp + 1
            if qt_bound != scan_limit:
                qt_bound = scan_limit
                qt_scanned_pos = scan_limit
                qt_next_secret = scan_limit

            if tail_start >= qt_scanned_pos and qt_next_secret == scan_limit:
                last_q_pos = q_pos_arg
                last_res = False
                return False
            if tail_start >= qt_scanned_pos and tail_start <= qt_next_secret < scan_limit:
                last_q_pos = q_pos_arg
                last_res = True
                return True

            item_start = tail_start
            found_secret_at = scan_limit
            while item_start < scan_limit:
                t_amp = seg.find('&', item_start, scan_limit)
                t_end = t_amp if t_amp != -1 else scan_limit
                t_eq = seg.find('=', item_start, t_end)
                if t_eq != -1:
                    raw_key = seg[item_start:t_eq]
                    norm_key = (unquote(raw_key) if '%' in raw_key else raw_key).strip().lower()
                    if norm_key in _SECRET_QUERY_KEYS:
                        found_secret_at = item_start
                        break
                if t_amp == -1:
                    break
                item_start = t_amp + 1
            qt_scanned_pos = tail_start
            qt_next_secret = found_secret_at
            res = found_secret_at < scan_limit
            last_q_pos = q_pos_arg
            last_res = res
            return res

        return _check

    _ext_query_has_secret = _make_monotonic_query_secret_checker()
    _prev_query_has_secret = _make_monotonic_query_secret_checker()

    gap_re = _GAP_OUTER_PAREN_RE if (has_outer_paren or has_outer_bracket) else _HARD_OUTER_CANDIDATE_RE
    next_gap_cand = -2
    cached_ns_pos = -1
    cached_ns_trim = -1
    cached_ns_trim_amp = -1
    next_raw_sep_pos = -2
    cached_raw_sep_pos = -1
    cached_raw_sep_trim = -1
    cached_raw_sep_next_at = -1
    next_amp_pos = -2
    cached_path_at_cand_end = -1
    cached_path_at_start = -1
    cached_path_at_end = -1
    cached_path_at_has_secret = False

    while (at_idx := cursor.find_at(pos)) != -1:
        if (
            (bare_head_stop is not None or (best_auth is None and key_stop is not None))
            and next_scheme_pos != -1
            and at_idx > next_scheme_pos
        ):
            cand = next_scheme_pos
            while cand > scheme_end and seg[cand - 1] in _SCHEME_CONT_CHARS:
                cand -= 1
            while cand < next_scheme_pos and seg[cand] not in _SCHEME_FIRST_CHARS:
                cand += 1
            if cand < next_scheme_pos:
                if bare_head_stop is not None:
                    return bare_head_stop
                assert key_stop is not None
                return key_stop[0]
            next_scheme_pos = _find_next_scheme_pos(seg, at_idx + 1, end)

        if head_q_pos is not None and best_auth is None and not q_ended:
            q_scheme_trim = -1
            if next_scheme_pos != -1 and q_cursor <= next_scheme_pos <= at_idx:
                cand_trim = _scheme_boundary_before(seg, head_q_pos + 1, next_scheme_pos)
                if cand_trim < next_scheme_pos and seg[cand_trim] in '!|~^*':
                    q_scheme_trim = cand_trim
            while q_cursor < at_idx:
                scan_limit = (
                    q_scheme_trim
                    if (q_scheme_trim != -1 and q_cursor <= q_scheme_trim < at_idx)
                    else at_idx
                )
                q_match = _HEAD_Q_SCAN_RE.search(seg, q_cursor, scan_limit)
                if q_match is None:
                    if scan_limit == q_scheme_trim:
                        q_cursor = q_scheme_trim
                        if not q_item_is_secret and head_q_is_safe_host:
                            q_ended = True
                            hard_end = q_cursor
                            break
                        q_cursor += 1
                        continue
                    q_cursor = at_idx
                    break
                q_cursor = q_match.start()
                qc = seg[q_cursor]
                if qc == '#':
                    if head_q_is_safe_host and next_scheme_pos != -1 and q_cursor < next_scheme_pos < at_idx:
                        ns = next_scheme_pos
                        while ns != -1 and ns < at_idx:
                            trim_pos = _scheme_boundary_before(seg, q_cursor + 1, ns)
                            if trim_pos < ns and seg[trim_pos] in '!|~^*':
                                q_ended = True
                                hard_end = trim_pos
                                break
                            ns = _find_next_scheme_pos(seg, ns + 3, at_idx)
                        if q_ended:
                            break
                    q_ended = True
                    q_ended_by_hash = True
                    break
                if qc.isspace():
                    q_ended = True
                    hard_end = q_cursor
                    break
                if qc in _HARD_OUTER_CANDIDATE_CHARS and _is_hard_structural_delim(seg, q_cursor, end):
                    is_json_open = (
                        qc in ('"', "'")
                        and q_cursor + 2 < end
                        and seg[q_cursor + 1] == ','
                        and seg[q_cursor + 2] in ('{', '[')
                    )
                    if is_json_open and q_cursor < q_failed_json_open_until:
                        q_cursor += 1
                        continue
                    if (
                        qc == '}'
                        and has_outer_brace
                        and q_cursor + 1 < end
                        and seg[q_cursor + 1] == '&'
                        and not in_outer_query
                    ):
                        q_ended = True
                        hard_end = q_cursor
                        break
                    if _is_strict_outer_envelope_after(
                        seg,
                        q_cursor,
                        end,
                        has_authority=True,
                        start=start,
                        next_scheme_pos=next_scheme_pos,
                        last_close_tag_pos=(
                            cursor.get_last_close_tag(scheme_end)
                            if qc in ('"', "'") and q_cursor + 1 < end and seg[q_cursor + 1] == '>'
                            else -1
                        ),
                        cursor=cursor,
                    ):
                        q_ended = True
                        hard_end = q_cursor
                        break
                    if is_json_open:
                        q_failed_json_open_until = cursor.find_json_open_stop(q_cursor + 3)
                elif qc == '(':
                    q_paren_depth += 1
                elif qc == ')':
                    if q_paren_depth > 0:
                        q_paren_depth -= 1
                    elif has_outer_paren and not (
                        in_outer_query and _closing_wrapper_amp_after(cursor, q_cursor, end) is not None
                    ):
                        q_ended = True
                        hard_end = q_cursor
                        break
                elif qc == '[':
                    q_bracket_depth += 1
                elif qc == ']':
                    if q_bracket_depth > 0:
                        q_bracket_depth -= 1
                    elif has_outer_bracket and not (
                        in_outer_query and _closing_wrapper_amp_after(cursor, q_cursor, end) is not None
                    ):
                        q_ended = True
                        hard_end = q_cursor
                        break
                elif qc == '&':
                    q_item_has_eq = False
                    q_item_is_secret = False
                    q_item_start = q_cursor + 1
                elif qc == '=' and not q_item_has_eq:
                    q_item_has_eq = True
                    raw_q_key = seg[q_item_start:q_cursor]
                    norm_q_key = unquote(raw_q_key) if '%' in raw_q_key else raw_q_key
                    q_item_is_secret = norm_q_key.strip().lower() in _SECRET_QUERY_KEYS
                    if q_item_is_secret:
                        q_any_secret = True
                q_cursor += 1
            if q_ended and not q_ended_by_hash:
                break

        if (
            head_q_pos is not None
            and best_auth is None
            and q_ended_by_hash
            and next_scheme_pos != -1
            and next_scheme_pos < at_idx
        ):
            ns = next_scheme_pos
            broke_frag_scheme = False
            while ns != -1 and ns < at_idx:
                trim_pos = _scheme_boundary_before(seg, q_cursor + 1, ns)
                if trim_pos < ns and seg[trim_pos] in '!|~^*':
                    hard_end = trim_pos
                    q_ended_by_hash = False
                    broke_frag_scheme = True
                    break
                ns = _find_next_scheme_pos(seg, ns + 3, at_idx)
            if broke_frag_scheme:
                break

        if next_scheme_pos != -1 and best_auth is None and next_scheme_pos < at_idx:
            next_scheme_pos = _find_next_scheme_pos(seg, at_idx + 1, end)
        elif next_scheme_pos != -1 and next_scheme_pos < pos:
            next_scheme_pos = _find_next_scheme_pos(seg, pos, end)
        if best_auth is not None and next_scheme_pos != -1 and at_idx > next_scheme_pos:
            if key_stop is not None:
                return key_stop[0]
            hard_end = _scheme_boundary_before(seg, best_auth[3], next_scheme_pos)
            break

        if best_auth is not None:
            gap_start = max(gap_pos, best_auth[3])
            broke_at_outer = False
            g = gap_start
            while g < at_idx:
                if next_gap_cand < g:
                    m_gap = gap_re.search(seg, g, end)
                    next_gap_cand = m_gap.start() if m_gap is not None else end
                if next_gap_cand >= at_idx:
                    break
                g = next_gap_cand
                gc = seg[g]
                if gc in _HARD_OUTER_CANDIDATE_CHARS and _is_hard_structural_delim(seg, g, at_idx):
                    is_json_open = (
                        gc in ('"', "'")
                        and g + 2 < at_idx
                        and seg[g + 1] == ','
                        and seg[g + 2] in ('{', '[')
                    )
                    if is_json_open and g < failed_json_open_until:
                        g += 1
                        continue
                    if _is_strict_outer_envelope_after(
                        seg,
                        g,
                        at_idx,
                        has_authority=True,
                        start=start,
                        next_scheme_pos=next_scheme_pos,
                        last_close_tag_pos=(
                            cursor.get_last_close_tag(scheme_end)
                            if gc in ('"', "'") and g + 1 < at_idx and seg[g + 1] == '>'
                            else -1
                        ),
                        cursor=cursor,
                    ):
                        hard_end = g
                        broke_at_outer = True
                        break
                    if is_json_open:
                        failed_json_open_until = min(cursor.find_json_open_stop(g + 3), at_idx)
                elif (has_outer_paren and gc == ')') or (has_outer_bracket and gc == ']'):
                    if best_auth[6] is None and best_auth[7] is None:
                        hard_end = g
                        broke_at_outer = True
                        break
                g += 1
            gap_pos = at_idx
            if broke_at_outer:
                if key_stop is not None and key_stop[0] <= hard_end:
                    return key_stop[0]
                break

        cand_end = end
        pre_trimmed_at_scheme = False
        if next_scheme_pos != -1 and at_idx < next_scheme_pos:
            if cached_ns_pos != next_scheme_pos:
                cached_ns_pos = next_scheme_pos
                s_start = next_scheme_pos
                while s_start > scheme_end and seg[s_start - 1] in _SCHEME_CONT_CHARS:
                    s_start -= 1
                while s_start < next_scheme_pos and seg[s_start] not in _SCHEME_FIRST_CHARS:
                    s_start += 1
                if s_start < next_scheme_pos and s_start > scheme_end and seg[s_start - 1] in '!|~^*':
                    cached_ns_trim = s_start - 1
                    cached_ns_trim_amp = s_start - 1
                elif s_start < next_scheme_pos and s_start > scheme_end and seg[s_start - 1] == '&':
                    cached_ns_trim = -1
                    cached_ns_trim_amp = s_start - 1
                else:
                    cached_ns_trim = -1
                    cached_ns_trim_amp = -1
            if cached_ns_trim > at_idx + 1:
                cand_end = cached_ns_trim
                pre_trimmed_at_scheme = True
        scanned = _scan_host_candidate(seg, at_idx, cand_end)
        if scanned is None:
            if next_raw_sep_pos == -2 or (next_raw_sep_pos != -1 and next_raw_sep_pos <= at_idx):
                next_raw_sep_pos = seg.find('://', at_idx + 1, end)
            raw_sep = next_raw_sep_pos if (next_raw_sep_pos != -1 and next_raw_sep_pos < cand_end) else -1
            if raw_sep != -1:
                if cached_raw_sep_pos != raw_sep:
                    cached_raw_sep_pos = raw_sep
                    rs = raw_sep
                    while rs > scheme_end and seg[rs - 1] in _SCHEME_CONT_CHARS:
                        rs -= 1
                    cached_raw_sep_trim = (
                        rs - 1
                        if (rs > scheme_end and rs < raw_sep and seg[rs - 1] in '!|~^*')
                        else -1
                    )
                    cached_raw_sep_next_at = cursor.find_at(raw_sep + 3) if cached_raw_sep_trim != -1 else -1
                if cached_raw_sep_trim > at_idx + 1:
                    if cached_raw_sep_next_at == -1 or (
                        next_scheme_pos != -1 and cached_raw_sep_next_at > next_scheme_pos
                    ):
                        cand_end = cached_raw_sep_trim
                        pre_trimmed_at_scheme = True
                        scanned = _scan_host_candidate(seg, at_idx, cand_end)
            if scanned is None:
                pos = at_idx + 1
                continue
        host_end, has_port = scanned
        host_end, has_port = _extend_multi_host_end(
            seg,
            host_end,
            has_port,
            cand_end,
            in_outer_query=in_outer_query,
        )
        has_slash = host_end < cand_end and seg[host_end] == '/'
        orig_path_end = host_end
        path_extended_at = False
        if has_slash:
            path_end = _scan_path_end(seg, host_end, cand_end)
            orig_path_end = path_end
            if path_end < cand_end and seg[path_end] == '@':
                if (
                    cand_end == cached_path_at_cand_end
                    and cached_path_at_start <= path_end < cached_path_at_end
                ):
                    ext_end = cached_path_at_end
                    ext_has_secret = cached_path_at_has_secret
                else:
                    ext_end = _scan_path_end(seg, path_end, cand_end)
                    while ext_end < cand_end and seg[ext_end] == '@':
                        ext_end = _scan_path_end(seg, ext_end, cand_end)
                    ext_has_secret = (
                        ext_end < cand_end
                        and seg[ext_end] == '?'
                        and _ext_query_has_secret(ext_end)
                    )
                    cached_path_at_cand_end = cand_end
                    cached_path_at_start = path_end
                    cached_path_at_end = ext_end
                    cached_path_at_has_secret = ext_has_secret
                if ext_has_secret:
                    path_end = ext_end
                    path_extended_at = True
        else:
            path_end = host_end
        amp_trimmed = False
        if in_outer_query:
            if next_amp_pos == -2 or (next_amp_pos != -1 and next_amp_pos <= at_idx):
                next_amp_pos = seg.find('&', at_idx + 1, end)
            if next_amp_pos != -1 and next_amp_pos < path_end:
                amp_pos = next_amp_pos
                if has_slash and amp_pos <= host_end:
                    has_slash = False
                    host_end = amp_pos
                elif not has_slash:
                    host_end = amp_pos
                path_end = amp_pos
                amp_trimmed = True
        trimmed_at_scheme = pre_trimmed_at_scheme and not amp_trimmed and path_end == cand_end
        if not trimmed_at_scheme and next_scheme_pos != -1 and at_idx < next_scheme_pos <= path_end:
            trim_pos = cached_ns_trim_amp
            if trim_pos > at_idx + 1:
                if has_slash and trim_pos <= host_end:
                    has_slash = False
                    host_end = trim_pos
                elif not has_slash:
                    host_end = trim_pos
                path_end = trim_pos
                trimmed_at_scheme = True
        if not trimmed_at_scheme and path_end < end and seg[path_end] == '@':
            pos = path_end
            continue

        if is_http_like or not has_slash or path_end <= host_end + 1:
            cand_valid_db = True
        elif path_extended_at and path_end > orig_path_end:
            cand_valid_db = False
        else:
            cand_valid_db = seg[host_end + 1 : path_end].isdigit()
        cur_tier = _TIER_PORT if has_port else (_TIER_SLASH if (has_slash and cand_valid_db) else _TIER_BARE)
        term_ch = seg[path_end] if path_end < end else ''
        w_amp_b = _closing_wrapper_amp_after(cursor, path_end, end) if in_outer_query else None
        if term_ch == '?':
            query_pos: int | None = path_end
        elif in_outer_query and term_ch == '&':
            query_pos = path_end
        elif w_amp_b is not None:
            query_pos = w_amp_b
        else:
            query_pos = None
        frag_pos = path_end if term_ch == '#' else None

        if head_q_pos is not None and best_auth is None:
            if q_ended_by_hash:
                if q_any_secret or cur_tier != _TIER_PORT or not cand_valid_db:
                    pos = at_idx + 1
                    continue
                clean_end = (
                    path_end >= end
                    or query_pos is not None
                    or frag_pos is not None
                    or term_ch.isspace()
                    or _is_hard_structural_delim(seg, path_end, end)
                    or (has_outer_paren and term_ch == ')')
                    or (has_outer_bracket and term_ch == ']')
                    or trimmed_at_scheme
                    or (term_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end))
                )
                if not clean_end:
                    pos = at_idx + 1
                    continue
            elif q_item_is_secret:
                pos = at_idx + 1
                continue
            elif q_item_has_eq:
                if cur_tier != _TIER_PORT or not cand_valid_db:
                    pos = at_idx + 1
                    continue
                clean_end = (
                    path_end >= end
                    or query_pos is not None
                    or frag_pos is not None
                    or term_ch.isspace()
                    or _is_hard_structural_delim(seg, path_end, end)
                    or (has_outer_paren and term_ch == ')')
                    or (has_outer_bracket and term_ch == ']')
                    or trimmed_at_scheme
                )
                if not clean_end:
                    if term_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
                        if _head_query_has_secret():
                            pos = at_idx + 1
                            continue
                    else:
                        pos = at_idx + 1
                        continue

        if bare_head_stop is not None:
            accept_bare = cur_tier == _TIER_PORT
            if not accept_bare and bare_head_is_kv and bare_kv_scan_pos != -1:
                while bare_kv_scan_pos < at_idx:
                    bch = seg[bare_kv_scan_pos]
                    if bch in (',', ';') and _is_key_eq_prefix(seg, bare_kv_scan_pos + 1, at_idx):
                        bare_kv_has_eq = False
                        bare_kv_colon_after_eq = False
                    elif bch == '=' and not bare_kv_has_eq:
                        bare_kv_has_eq = True
                        bare_kv_colon_after_eq = False
                    elif bch == ':' and bare_kv_has_eq:
                        bare_kv_colon_after_eq = True
                    bare_kv_scan_pos += 1
                if bare_kv_colon_after_eq:
                    accept_bare = True
                elif cand_valid_db and _is_ipv4_or_localhost(seg[at_idx + 1 : host_end]):
                    accept_bare = True
            if not accept_bare:
                pos = path_end + 1
                continue
            bare_head_stop = None

        if key_stop is not None:
            if not (cur_tier == _TIER_PORT and cur_tier > key_stop[1]):
                pos = path_end + 1
                continue
            key_stop = None

        if best_auth is not None and best_auth[6] is not None:
            prev_valid_db = best_auth[5]
            prev_has_slash = best_auth[4]
            prev_has_port = best_auth[2]
            prev_q_pos = best_auth[6]
            if not cand_valid_db:
                pos = path_end + 1
                continue
            if (
                prev_valid_db
                or (
                    _prev_query_has_secret(prev_q_pos)
                    and (
                        prev_has_port
                        or (prev_has_slash and _is_ipv4_or_localhost(seg[best_auth[0] + 1 : best_auth[1]]))
                    )
                )
            ) and (prev_has_slash or prev_has_port):
                pos = path_end + 1
                continue
            if not has_port:
                if eq_cursor != -2 and eq_cursor <= prev_q_pos:
                    eq_cursor = seg.find('=', prev_q_pos + 1, end)
                    if eq_cursor == -1:
                        eq_cursor = -2
                if eq_cursor != -2 and eq_cursor < at_idx:
                    pos = path_end + 1
                    continue
            else:
                clean_end = (
                    term_ch == ''
                    or term_ch.isspace()
                    or _is_hard_structural_delim(seg, path_end, end)
                )
                if not clean_end:
                    if term_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
                        if _prev_query_has_secret(prev_q_pos):
                            pos = path_end + 1
                            continue
                    elif not trimmed_at_scheme:
                        pos = path_end + 1
                        continue
        elif best_auth is not None and best_auth[7] is not None:
            prev_valid_db = best_auth[5]
            prev_has_slash = best_auth[4]
            prev_has_port = best_auth[2]
            if not cand_valid_db or (
                prev_valid_db and (prev_has_slash or (prev_has_port and not has_port))
            ):
                pos = path_end + 1
                continue

        best_auth = (at_idx, host_end, has_port, path_end, has_slash, cand_valid_db, query_pos, frag_pos, cur_tier)
        last_clean_end = None
        if cur_tier > best_auth_tier:
            best_auth_tier = cur_tier

        if trimmed_at_scheme:
            last_clean_end = path_end
            hard_end = path_end
            break

        if path_end >= end:
            break
        if term_ch.isspace():
            last_clean_end = path_end
            break
        if term_ch in (',', ';') and _is_key_eq_prefix(seg, path_end + 1, end):
            # Inherent local ambiguity on same-tier _TIER_PORT ',key=':
            # 'url=redis://u:p@fake:8080,email=a@b:8080/1' and
            # 'redis://user:p@fake:8080,k=pw_secret@127.0.0.1:6379/0' share identical
            # local syntax, so ',key=' terminates the URL in free-form text
            # (redact_redis_url() still redacts the full URL directly).
            if cur_tier == _TIER_PORT:
                return path_end
            if key_stop is None or cur_tier > key_stop[1]:
                key_stop = (path_end, cur_tier)
            pos = path_end + 1
            continue
        if query_pos is None and _is_hard_structural_delim(seg, path_end, end):
            next_at_after = cursor.find_at(path_end + 1)
            if next_at_after == -1 or (next_scheme_pos != -1 and next_scheme_pos < next_at_after):
                return path_end
            last_clean_end = path_end
            pos = path_end + 1
            continue
        if query_pos is None and ((has_outer_paren and term_ch == ')') or (has_outer_bracket and term_ch == ']')):
            if frag_pos is None:
                return path_end
        if query_pos is not None or frag_pos is not None:
            pos = (query_pos + 1) if query_pos is not None else (path_end + 1)
            continue
        pos = path_end + 1

    if bare_head_stop is not None:
        return bare_head_stop

    if key_stop is not None:
        return key_stop[0]

    if head_q_pos is not None and best_auth is None:
        return _scan_query_and_fragment_end(
            seg,
            head_q_pos + 1,
            hard_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
            has_outer_dquote=has_outer_dquote,
            has_outer_angle=has_outer_angle,
            has_outer_brace=has_outer_brace,
            has_outer_squote=has_outer_squote,
            in_outer_query=in_outer_query,
        )

    if best_auth is None:
        return _find_hard_or_outer_delim_end(
            seg,
            scheme_end,
            end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
        )

    if best_auth[6] is not None:
        return _scan_query_and_fragment_end(
            seg,
            best_auth[6] + 1,
            hard_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
            has_outer_dquote=has_outer_dquote,
            has_outer_angle=has_outer_angle,
            has_outer_brace=has_outer_brace,
            has_outer_squote=has_outer_squote,
            in_outer_query=in_outer_query,
        )
    if best_auth[7] is not None:
        return _scan_fragment_end(
            seg,
            best_auth[7] + 1,
            hard_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
        )
    if last_clean_end is not None and last_clean_end <= hard_end:
        return last_clean_end
    if best_auth[3] < hard_end:
        term_ch = seg[best_auth[3]]
        if (
            term_ch.isspace()
            or _is_hard_structural_delim(seg, best_auth[3], hard_end)
            or (has_outer_paren and term_ch == ')')
            or (has_outer_bracket and term_ch == ']')
        ):
            return best_auth[3]
    return min(best_auth[3], hard_end) if best_auth[3] <= hard_end else hard_end


def _find_next_url_token(text: str, pos: int, n: int) -> tuple[int, int, int] | None:
    search_pos = pos
    while (sep := text.find('://', search_pos, n)) != -1:
        scheme_end = sep + 3
        if scheme_end >= n or text[scheme_end].isspace():
            search_pos = scheme_end
            continue
        cand = sep
        while cand > pos and text[cand - 1] in _SCHEME_CONT_CHARS:
            cand -= 1
        while cand < sep and text[cand] not in _SCHEME_FIRST_CHARS:
            cand += 1
        if cand < sep:
            ws_match = _WHITESPACE_RE.search(text, scheme_end, n)
            tok_end = ws_match.start() if ws_match is not None else n
            return cand, scheme_end, tok_end
        search_pos = scheme_end
    return None


def _collect_scheme_spans(token: str, first_scheme_end: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = [(0, first_scheme_end)]
    search_pos = first_scheme_end
    while (sep := token.find('://', search_pos, end)) != -1:
        scheme_end = sep + 3
        cand = sep
        while cand > search_pos and token[cand - 1] in _SCHEME_CONT_CHARS:
            cand -= 1
        while cand < sep and token[cand] not in _SCHEME_FIRST_CHARS:
            cand += 1
        if cand < sep:
            spans.append((cand, scheme_end))
        search_pos = scheme_end
    return spans


def _redact_url_token_with_possible_multi_urls(
    token: str,
    first_scheme_end: int | None = None,
    *,
    has_outer_paren: bool = False,
    has_outer_bracket: bool = False,
    has_outer_dquote: bool = False,
    has_outer_angle: bool = False,
    has_outer_brace: bool = False,
    has_outer_squote: bool = False,
    in_outer_query: bool = False,
) -> str:
    n = len(token)
    if first_scheme_end is None:
        first_sep = token.find('://')
        if first_sep == -1:
            return token
        first_scheme_end = first_sep + 3

    scheme_spans = _collect_scheme_spans(token, first_scheme_end, n)
    if len(scheme_spans) <= 1:
        stripped_end = _find_stripped_url_end(token, 0, n)
        struct_end = _split_structural_trailing_idx(
            token,
            0,
            stripped_end,
            has_outer_paren=has_outer_paren,
            has_outer_bracket=has_outer_bracket,
            has_outer_dquote=has_outer_dquote,
            has_outer_angle=has_outer_angle,
            has_outer_brace=has_outer_brace,
            has_outer_squote=has_outer_squote,
            in_outer_query=in_outer_query,
        )
        redacted_core = _redact_redis_url_internal(token[:struct_end], in_outer_query=in_outer_query)
        return f'{redacted_core}{token[struct_end:]}'

    split_candidates: list[tuple[int, int]] = []
    for k in range(1, len(scheme_spans)):
        split_idx = _find_multi_url_split_idx(token, scheme_spans[k - 1][0], scheme_spans[k][0])
        if split_idx is not None:
            split_candidates.append((k, split_idx))

    base_stripped_end = _find_stripped_url_end(token, 0, n)
    has_outer_at_after: list[bool] | None = None
    if split_candidates and token.find('@', 0, base_stripped_end) != -1:
        has_outer_at_after = [False] * len(split_candidates)
        first_head_slice = token[first_scheme_end : split_candidates[0][1]]
        first_head_host = first_head_slice.partition('/')[0].partition('?')[0].partition('#')[0]
        head_is_ipv4_or_local = (
            _is_ipv4_or_localhost(first_head_host.partition(':')[0])
            or first_head_host.startswith('[')
        )
        suffix_outer_at = False
        for j in range(len(split_candidates) - 1, -1, -1):
            if not suffix_outer_at:
                k_j, _ = split_candidates[j]
                next_k = split_candidates[j + 1][0] if j + 1 < len(split_candidates) else len(scheme_spans)
                bound_j = split_candidates[j + 1][1] if j + 1 < len(split_candidates) else base_stripped_end
                for m in range(next_k - 1, k_j - 1, -1):
                    sub_start_m, sub_sep_end_m = scheme_spans[m]
                    if m + 1 < next_k:
                        nxt_start = scheme_spans[m + 1][0]
                        sub_bound_m = (
                            nxt_start - 1
                            if nxt_start > sub_sep_end_m and token[nxt_start - 1] in '!|~^*'
                            else nxt_start
                        )
                    else:
                        sub_bound_m = bound_j
                    first_at_m = token.find('@', sub_sep_end_m, sub_bound_m)
                    if first_at_m == -1:
                        continue
                    next_scheme_lower = token[sub_start_m : sub_sep_end_m - 3].lower()
                    next_is_unix = _is_unix_scheme(next_scheme_lower)
                    next_is_sentinel_or_cluster = next_scheme_lower.endswith(
                        ('+sentinel', '+cluster')
                    )
                    next_is_http = next_scheme_lower in _HTTP_LIKE_SCHEMES
                    q_pos_m = token.find('?', sub_sep_end_m, sub_bound_m)
                    h_pos_m = token.find('#', sub_sep_end_m, sub_bound_m)
                    qf_pos_m = min(p for p in (q_pos_m, h_pos_m, sub_bound_m) if p != -1)
                    has_path_before_q = '/' in token[sub_sep_end_m:qf_pos_m]
                    skip_sec_q_m = q_pos_m != -1 and head_is_ipv4_or_local
                    if next_is_unix:
                        if sub_sep_end_m < sub_bound_m and token[sub_sep_end_m] == '/':
                            suffix_outer_at = (
                                _find_authority_at_in_suffix(
                                    token,
                                    max(sub_sep_end_m, first_at_m),
                                    is_unix=False,
                                    require_strong=True,
                                    require_valid_redis_db=True,
                                    query_start_pos=q_pos_m,
                                    skip_secret_query_at=skip_sec_q_m,
                                    skip_kv_query_at=(q_pos_m != -1 and head_is_ipv4_or_local),
                                    skip_hash_after_kv_query=False,
                                    end=sub_bound_m,
                                )
                                is not None
                            )
                        elif (
                            not head_is_ipv4_or_local
                            and _find_authority_at_in_suffix(
                                token,
                                sub_sep_end_m,
                                is_unix=True,
                                query_start_pos=q_pos_m,
                                skip_secret_query_at=skip_sec_q_m,
                                skip_kv_query_at=(q_pos_m != -1 and head_is_ipv4_or_local),
                                end=sub_bound_m,
                            )
                            is not None
                        ):
                            suffix_outer_at = True
                        else:
                            search_start_m = first_at_m if not head_is_ipv4_or_local else first_at_m + 1
                            second_at_m = (
                                first_at_m
                                if not head_is_ipv4_or_local
                                else token.find('@', first_at_m + 1, sub_bound_m)
                            )
                            suffix_outer_at = (
                                second_at_m != -1
                                and not (
                                    second_at_m + 1 < sub_bound_m
                                    and token[second_at_m + 1] == '/'
                                )
                                and _find_authority_at_in_suffix(
                                    token,
                                    search_start_m,
                                    is_unix=False,
                                    require_strong=True,
                                    require_valid_redis_db=True,
                                    query_start_pos=q_pos_m,
                                    skip_secret_query_at=skip_sec_q_m,
                                    skip_kv_query_at=(q_pos_m != -1 and head_is_ipv4_or_local),
                                    end=sub_bound_m,
                                )
                                is not None
                            )
                    elif next_is_http:
                        if first_at_m < qf_pos_m:
                            second_at_m = token.find('@', first_at_m + 1, qf_pos_m)
                            pre_at = token[sub_sep_end_m:first_at_m]
                            if (
                                second_at_m != -1
                                or ':' not in pre_at
                                or '/' in pre_at
                                or '|' in pre_at
                                or _find_authority_at_in_suffix(
                                    token,
                                    first_at_m,
                                    is_unix=False,
                                    require_strong=True,
                                    require_valid_redis_db=True,
                                    end=sub_bound_m,
                                )
                                == first_at_m
                            ):
                                suffix_outer_at = True
                            elif qf_pos_m < sub_bound_m and token.find('@', qf_pos_m, sub_bound_m) != -1:
                                suffix_outer_at = (
                                    _find_authority_at_in_suffix(
                                        token,
                                        qf_pos_m,
                                        is_unix=False,
                                        require_strong=True,
                                        require_valid_redis_db=True,
                                        query_start_pos=q_pos_m,
                                        skip_secret_query_at=skip_sec_q_m,
                                        skip_kv_query_at=(
                                            q_pos_m != -1 and has_path_before_q and head_is_ipv4_or_local
                                        ),
                                        skip_hash_after_kv_query=False,
                                        end=sub_bound_m,
                                    )
                                    is not None
                                )
                        else:
                            auth_at_m = _find_authority_at_in_suffix(
                                token,
                                qf_pos_m,
                                is_unix=False,
                                require_strong=True,
                                require_valid_redis_db=True,
                                query_start_pos=q_pos_m,
                                skip_secret_query_at=skip_sec_q_m,
                                skip_kv_query_at=(
                                    q_pos_m != -1 and has_path_before_q and head_is_ipv4_or_local
                                ),
                                skip_hash_after_kv_query=False,
                                end=sub_bound_m,
                            )
                            if auth_at_m is not None:
                                suffix_outer_at = True
                            elif not has_path_before_q and not head_is_ipv4_or_local:
                                cand_at_m = _find_authority_at_in_suffix(
                                    token,
                                    qf_pos_m,
                                    is_unix=False,
                                    require_strong=False,
                                    require_valid_redis_db=True,
                                    query_start_pos=q_pos_m,
                                    skip_secret_query_at=(q_pos_m != -1),
                                    skip_kv_query_at=False,
                                    skip_hash_after_kv_query=False,
                                    end=sub_bound_m,
                                )
                                if cand_at_m is not None:
                                    cand_h = (
                                        token[cand_at_m + 1 : sub_bound_m]
                                        .partition('/')[0]
                                        .partition('?')[0]
                                        .partition('#')[0]
                                    )
                                    if _is_ipv4_or_localhost(cand_h) or cand_h.startswith('['):
                                        suffix_outer_at = True
                    else:
                        require_valid_db_m = not (
                            next_is_sentinel_or_cluster and not head_is_ipv4_or_local
                        )
                        if first_at_m < qf_pos_m:
                            second_at_m = token.find('@', first_at_m + 1, qf_pos_m)
                            pre_at = token[sub_sep_end_m:first_at_m]
                            if (
                                second_at_m != -1
                                or '/' in pre_at
                                or '|' in pre_at
                                or (
                                    (not head_is_ipv4_or_local or _HOST_PART_RE.match(pre_at) is not None)
                                    and (
                                        _find_authority_at_in_suffix(
                                            token,
                                            first_at_m,
                                            is_unix=False,
                                            require_strong=True,
                                            require_valid_redis_db=require_valid_db_m,
                                            end=sub_bound_m,
                                        )
                                        == first_at_m
                                        or (
                                            _find_authority_at_in_suffix(
                                                token,
                                                first_at_m,
                                                is_unix=False,
                                                require_strong=False,
                                                require_valid_redis_db=require_valid_db_m,
                                                end=sub_bound_m,
                                            )
                                            == first_at_m
                                            and (
                                                _is_ipv4_or_localhost(
                                                    token[first_at_m + 1 : qf_pos_m].partition('/')[0]
                                                )
                                                or token[first_at_m + 1 : qf_pos_m].startswith('[')
                                            )
                                        )
                                    )
                                )
                            ):
                                suffix_outer_at = True
                            elif qf_pos_m < sub_bound_m and token.find('@', qf_pos_m, sub_bound_m) != -1:
                                suffix_outer_at = (
                                    _find_authority_at_in_suffix(
                                        token,
                                        qf_pos_m,
                                        is_unix=False,
                                        require_strong=True,
                                        require_valid_redis_db=require_valid_db_m,
                                        query_start_pos=q_pos_m,
                                        skip_secret_query_at=skip_sec_q_m,
                                        skip_kv_query_at=(
                                            q_pos_m != -1 and has_path_before_q and head_is_ipv4_or_local
                                        ),
                                        skip_hash_after_kv_query=False,
                                        end=sub_bound_m,
                                    )
                                    is not None
                                )
                        else:
                            auth_at_m = _find_authority_at_in_suffix(
                                token,
                                qf_pos_m,
                                is_unix=False,
                                require_strong=True,
                                require_valid_redis_db=require_valid_db_m,
                                query_start_pos=q_pos_m,
                                skip_secret_query_at=skip_sec_q_m,
                                skip_kv_query_at=(
                                    q_pos_m != -1 and has_path_before_q and head_is_ipv4_or_local
                                ),
                                skip_hash_after_kv_query=False,
                                end=sub_bound_m,
                            )
                            if auth_at_m is not None:
                                suffix_outer_at = True
                            elif not has_path_before_q and not head_is_ipv4_or_local:
                                cand_at_m = _find_authority_at_in_suffix(
                                    token,
                                    qf_pos_m,
                                    is_unix=False,
                                    require_strong=False,
                                    require_valid_redis_db=require_valid_db_m,
                                    query_start_pos=q_pos_m,
                                    skip_secret_query_at=(q_pos_m != -1),
                                    skip_kv_query_at=False,
                                    skip_hash_after_kv_query=False,
                                    end=sub_bound_m,
                                )
                                if cand_at_m is not None:
                                    cand_h = (
                                        token[cand_at_m + 1 : sub_bound_m]
                                        .partition('/')[0]
                                        .partition('?')[0]
                                        .partition('#')[0]
                                    )
                                    if _is_ipv4_or_localhost(cand_h) or cand_h.startswith('['):
                                        suffix_outer_at = True
                    if suffix_outer_at:
                        break
            has_outer_at_after[j] = suffix_outer_at

    out: list[str] = []
    stack: list[tuple[int, int, int, int, int, int, bool, bool, bool, bool, bool, bool, bool] | str] = [
        (
            0,
            n,
            0,
            len(scheme_spans),
            0,
            len(split_candidates),
            has_outer_paren,
            has_outer_bracket,
            has_outer_dquote,
            has_outer_angle,
            has_outer_brace,
            has_outer_squote,
            in_outer_query,
        )
    ]

    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue

        (
            seg_start,
            seg_end,
            span_lo,
            span_hi,
            cand_lo,
            cand_hi,
            chunk_paren,
            chunk_bracket,
            chunk_dquote,
            chunk_angle,
            chunk_brace,
            chunk_squote,
            chunk_in_outer_query,
        ) = item
        stripped_end = _find_stripped_url_end(token, seg_start, seg_end)
        trailing_str = token[stripped_end:seg_end]

        if cand_lo >= cand_hi:
            cur_start = seg_start
            cur_idx = span_lo
            cur_paren = chunk_paren
            cur_bracket = chunk_bracket
            cur_dquote = chunk_dquote
            cur_angle = chunk_angle
            cur_brace = chunk_brace
            cur_squote = chunk_squote
            cur_in_outer_query = chunk_in_outer_query
            unsplit_cursor = _TokenScanCursor(token, stripped_end)
            while cur_idx < span_hi and cur_start < stripped_end:
                struct_end = _split_structural_trailing_idx(
                    token,
                    cur_start,
                    stripped_end,
                    has_outer_paren=cur_paren,
                    has_outer_bracket=cur_bracket,
                    has_outer_dquote=cur_dquote,
                    has_outer_angle=cur_angle,
                    has_outer_brace=cur_brace,
                    has_outer_squote=cur_squote,
                    scan_cursor=unsplit_cursor,
                    in_outer_query=cur_in_outer_query,
                )
                out.append(
                    _redact_redis_url_internal(
                        token[cur_start:struct_end],
                        in_outer_query=cur_in_outer_query,
                    )
                )
                if struct_end >= stripped_end:
                    break
                while cur_idx < span_hi and scheme_spans[cur_idx][0] < struct_end:
                    cur_idx += 1
                if cur_idx >= span_hi:
                    out.append(token[struct_end:stripped_end])
                    break
                next_start = scheme_spans[cur_idx][0]
                if next_start > struct_end:
                    out.append(token[struct_end:next_start])
                cur_in_outer_query = _gap_stays_in_outer_query(
                    token,
                    cur_start,
                    struct_end,
                    next_start,
                    cur_in_outer_query,
                )
                cur_paren = next_start > 0 and token[next_start - 1] == '('
                cur_bracket = next_start > 0 and token[next_start - 1] == '['
                cur_dquote = next_start > 0 and token[next_start - 1] == '"'
                cur_angle = next_start > 0 and token[next_start - 1] == '<'
                cur_brace = next_start > 0 and token[next_start - 1] == '{'
                cur_squote = next_start > 0 and token[next_start - 1] == "'"
                cur_start = next_start
            if trailing_str:
                out.append(trailing_str)
            continue

        first_sep_end = scheme_spans[span_lo][1]
        scheme_lower = token[seg_start : first_sep_end - 3].lower()
        is_unix = _is_unix_scheme(scheme_lower)
        is_http_like = scheme_lower in _HTTP_LIKE_SCHEMES
        is_credential_free_unix = is_unix and first_sep_end < stripped_end and token[first_sep_end] == '/'
        scan_pos = first_sep_end
        last_at = -1
        last_at_before_delim = -1
        prev_at_before_qf = -1
        last_qf_pos = -1
        has_unix_socket_at = False
        first_delim = -1
        first_colon = -1
        colon_non_digit = False
        in_brackets = False
        invalid_host_at = -1
        unclosed_bracket_at = -1
        unclosed_bracket_until = -1
        invalid_auth_prefix_at = -1
        bare_qf_auth_prefix_at = -1
        invalid_qf_auth_at = -1
        bare_qf_auth_at = -1
        has_strong_char_since_at = False
        has_outer_delim = False
        outer_split_exhausted = False
        active_q_pos = seg_start if chunk_in_outer_query else -1
        in_hash_q = False
        cur_q_item_start = seg_start if chunk_in_outer_query else -1
        cur_q_has_eq = False
        cur_q_is_secret = False
        first_qf_not_host = False
        did_split = False
        cached_head_ipv4_db: bool | None = None

        for c_idx in range(cand_lo, cand_hi):
            k, split_idx = split_candidates[c_idx]
            if split_idx >= stripped_end:
                break

            while scan_pos < split_idx:
                ch = token[scan_pos]
                if (
                    not outer_split_exhausted
                    and ch in _HARD_OUTER_CANDIDATE_CHARS
                    and _is_hard_structural_delim(token, scan_pos, stripped_end)
                    and (ch != '<' or _is_strict_close_tag(token, scan_pos, stripped_end))
                ):
                    has_outer_delim = True
                if ch == '@':
                    if (
                        first_qf_not_host
                        and not is_unix
                        and not chunk_in_outer_query
                        and last_at_before_delim < first_sep_end
                    ):
                        active_q_pos = -1
                        in_hash_q = False
                        cur_q_item_start = -1
                        cur_q_has_eq = False
                        cur_q_is_secret = False
                        first_delim = -1
                        first_qf_not_host = False
                    last_at = scan_pos
                    invalid_host_at = -1
                    unclosed_bracket_at = -1
                    unclosed_bracket_until = -1
                    has_strong_char_since_at = False
                    if first_delim == -1:
                        last_at_before_delim = scan_pos
                        invalid_auth_prefix_at = -1
                        bare_qf_auth_prefix_at = -1
                    if is_unix and scan_pos + 1 < stripped_end and token[scan_pos + 1] == '/':
                        has_unix_socket_at = True
                elif ch in '?#':
                    if ch == '?':
                        if active_q_pos == -1 and not in_hash_q:
                            active_q_pos = scan_pos
                            cur_q_item_start = scan_pos + 1
                            cur_q_has_eq = False
                            cur_q_is_secret = False
                    else:
                        in_hash_q = True
                        active_q_pos = -1
                        cur_q_item_start = -1
                        cur_q_has_eq = False
                        cur_q_is_secret = False
                    if first_delim == -1:
                        first_delim = scan_pos
                        if not is_unix and not chunk_in_outer_query and last_at_before_delim < first_sep_end:
                            first_qf_not_host = not _is_host_or_host_list(token[first_sep_end:scan_pos])[0]
                    if (
                        last_at >= first_sep_end
                        and last_at != prev_at_before_qf
                        and (prev_at_before_qf == -1 or has_strong_char_since_at)
                    ):
                        prev_at_before_qf = last_at
                        last_qf_pos = scan_pos
                        invalid_qf_auth_at = -1
                        bare_qf_auth_at = -1
                else:
                    if active_q_pos != -1 and not in_hash_q:
                        if ch == '&':
                            cur_q_item_start = scan_pos + 1
                            cur_q_has_eq = False
                            cur_q_is_secret = False
                        elif ch == '=' and not cur_q_has_eq:
                            cur_q_has_eq = True
                            raw_q_key = token[cur_q_item_start:scan_pos]
                            norm_q_key = (unquote(raw_q_key) if '%' in raw_q_key else raw_q_key).strip().lower()
                            cur_q_is_secret = norm_q_key in _SECRET_QUERY_KEYS
                    if ch in ':/[':
                        has_strong_char_since_at = True
                    if first_delim == -1:
                        if ch == '/':
                            first_delim = scan_pos
                        elif ch == '[' and not in_brackets and (scan_pos == first_sep_end or scan_pos == last_at + 1):
                            in_brackets = True
                        elif ch == ']' and in_brackets:
                            in_brackets = False
                        elif not in_brackets:
                            if first_colon == -1 and ch == ':':
                                first_colon = scan_pos
                            elif first_colon != -1 and last_at < first_sep_end and not ch.isdigit():
                                colon_non_digit = True
                scan_pos += 1

            is_query_param_split = (active_q_pos != -1 and token[split_idx] in ('=', '&')) or (
                token[split_idx] == '?' and not in_hash_q
            )
            skip_query_split = False
            if is_query_param_split:
                is_secret_q_param = False
                if token[split_idx] == '=':
                    if cur_q_has_eq:
                        is_secret_q_param = cur_q_is_secret
                    elif cur_q_item_start != -1:
                        raw_q_key = token[cur_q_item_start:split_idx]
                        norm_q_key = (unquote(raw_q_key) if '%' in raw_q_key else raw_q_key).strip().lower()
                        is_secret_q_param = norm_q_key in _SECRET_QUERY_KEYS
                elif token[split_idx] == '&':
                    eq_p = token.find('=', split_idx + 1, scheme_spans[k][0])
                    if eq_p != -1 and '&' not in token[split_idx + 1 : eq_p]:
                        raw_q_key = token[split_idx + 1 : eq_p]
                        norm_q_key = (unquote(raw_q_key) if '%' in raw_q_key else raw_q_key).strip().lower()
                        is_secret_q_param = norm_q_key in _SECRET_QUERY_KEYS
                elif active_q_pos != -1 and cur_q_is_secret:
                    is_secret_q_param = True
                if is_secret_q_param:
                    skip_query_split = True
                else:
                    q_next_start = scheme_spans[k][0]
                    val_bound = split_candidates[c_idx + 1][1] if c_idx + 1 < cand_hi else stripped_end
                    amp_p = token.find('&', q_next_start, val_bound)
                    hash_p = token.find('#', q_next_start, val_bound)
                    val_end = min(p for p in (amp_p, hash_p, val_bound) if p != -1)
                    has_at_or_q = (
                        token.find('@', q_next_start, val_end) != -1
                        or token.find('?', q_next_start, val_end) != -1
                    )
                    has_next_scheme = k + 1 < span_hi and scheme_spans[k + 1][0] < val_end
                    if not has_at_or_q and not has_next_scheme:
                        skip_query_split = True

            core_end = _find_stripped_url_end(token, seg_start, split_idx)
            is_outer_split = core_end < split_idx or _is_outer_envelope_delim(token, split_idx, stripped_end)
            safe_to_split = False

            if not skip_query_split:
                if is_credential_free_unix:
                    safe_to_split = True
                elif last_at < first_sep_end or last_at >= core_end:
                    outer_at = has_outer_at_after[c_idx] if has_outer_at_after is not None else False
                    if not in_brackets:
                        if first_delim != -1 and first_delim < core_end:
                            if first_colon == -1 or first_colon >= core_end:
                                safe_to_split = True
                            elif not colon_non_digit:
                                if not outer_at or is_http_like:
                                    safe_to_split = True
                                elif first_colon < first_delim:
                                    if cached_head_ipv4_db is None:
                                        cached_head_ipv4_db = _is_ipv4_or_localhost(
                                            token[first_sep_end:first_colon]
                                        ) and _is_valid_redis_db_path(token[first_delim:core_end])
                                    if cached_head_ipv4_db:
                                        safe_to_split = True
                        elif first_colon == -1 or first_colon >= core_end:
                            if token[split_idx] != ':' or (
                                core_end > first_sep_end + 2
                                and token[first_sep_end] == '['
                                and token[core_end - 1] == ']'
                            ):
                                safe_to_split = True
                        elif (
                            first_colon > first_sep_end
                            and not colon_non_digit
                            and core_end > first_colon + 1
                            and not outer_at
                        ):
                            safe_to_split = True
                elif is_unix:
                    safe_to_split = has_unix_socket_at
                else:
                    if invalid_host_at != last_at and (
                        unclosed_bracket_at != last_at
                        or token.find(']', unclosed_bracket_until, core_end) != -1
                    ):
                        tail = token[last_at + 1 : core_end]
                        if _is_valid_host_and_tail(tail, is_unix=False, require_strong=False) or (
                            _has_strong_host_before_structural_delim(token, last_at, core_end)
                        ):
                            safe_to_split = True
                        elif not tail.startswith('[') or ']' in tail or any(c in tail for c in '/?#'):
                            invalid_host_at = last_at
                        else:
                            unclosed_bracket_at = last_at
                            unclosed_bracket_until = core_end
                    if not safe_to_split and first_delim != -1 and first_delim < core_end:
                        if last_at_before_delim >= first_sep_end:
                            if bare_qf_auth_prefix_at == last_at_before_delim:
                                if is_outer_split:
                                    safe_to_split = True
                            elif invalid_auth_prefix_at != last_at_before_delim:
                                cand_host = token[last_at_before_delim + 1 : first_delim]
                                if token[first_delim] in '?#':
                                    suffix_head = ''
                                else:
                                    q_idx = token.find('?', first_delim, core_end)
                                    h_idx = token.find('#', first_delim, core_end)
                                    if q_idx == -1 and h_idx == -1:
                                        suffix_head = token[first_delim:core_end]
                                    elif q_idx == -1:
                                        suffix_head = token[first_delim:h_idx]
                                    elif h_idx == -1:
                                        suffix_head = token[first_delim:q_idx]
                                    else:
                                        suffix_head = token[first_delim : min(q_idx, h_idx)]
                                if _is_genuine_authority_prefix(cand_host, suffix_head, is_http_like=is_http_like):
                                    safe_to_split = True
                                elif token[first_delim] in '?#' and _HOST_PART_RE.match(cand_host):
                                    bare_qf_auth_prefix_at = last_at_before_delim
                                    if is_outer_split:
                                        safe_to_split = True
                                elif suffix_head or token[first_delim] in '?#':
                                    invalid_auth_prefix_at = last_at_before_delim
                        elif (
                            not in_brackets
                            and (first_colon == -1 or first_colon > first_delim or not colon_non_digit)
                        ):
                            safe_to_split = True
                    if (
                        not safe_to_split
                        and prev_at_before_qf >= first_sep_end
                        and prev_at_before_qf < last_qf_pos < core_end
                    ):
                        if bare_qf_auth_at == prev_at_before_qf:
                            if is_outer_split:
                                safe_to_split = True
                        elif invalid_qf_auth_at != prev_at_before_qf:
                            cand_qf_host = token[prev_at_before_qf + 1 : last_qf_pos]
                            if '@' not in cand_qf_host:
                                cand_h, slash_c, path_c = cand_qf_host.partition('/')
                                if _HOST_PART_RE.match(cand_h) and (
                                    is_http_like or _is_valid_redis_db_path(f'{slash_c}{path_c}')
                                ):
                                    if cand_h.startswith('[') or ':' in cand_h or slash_c == '/':
                                        safe_to_split = True
                                    else:
                                        bare_qf_auth_at = prev_at_before_qf
                                        if is_outer_split:
                                            safe_to_split = True
                                else:
                                    invalid_qf_auth_at = prev_at_before_qf
                            else:
                                invalid_qf_auth_at = prev_at_before_qf

            if safe_to_split:
                next_start = scheme_spans[k][0]
                next_paren = next_start > 0 and token[next_start - 1] == '('
                next_bracket = next_start > 0 and token[next_start - 1] == '['
                next_dquote = next_start > 0 and token[next_start - 1] == '"'
                next_angle = next_start > 0 and token[next_start - 1] == '<'
                next_brace = next_start > 0 and token[next_start - 1] == '{'
                next_squote = next_start > 0 and token[next_start - 1] == "'"
                next_in_outer_query = is_query_param_split or (
                    active_q_pos != -1
                    and not in_hash_q
                    and _gap_stays_in_outer_query(token, seg_start, core_end, next_start, chunk_in_outer_query)
                )
                if trailing_str:
                    stack.append(trailing_str)
                stack.append(
                    (
                        next_start,
                        stripped_end,
                        k,
                        span_hi,
                        c_idx + 1,
                        cand_hi,
                        next_paren,
                        next_bracket,
                        next_dquote,
                        next_angle,
                        next_brace,
                        next_squote,
                        next_in_outer_query,
                    )
                )
                stack.append(token[core_end:next_start])
                stack.append(
                    (
                        seg_start,
                        core_end,
                        span_lo,
                        k,
                        c_idx,
                        c_idx,
                        chunk_paren,
                        chunk_bracket,
                        chunk_dquote,
                        chunk_angle,
                        chunk_brace,
                        chunk_squote,
                        chunk_in_outer_query,
                    )
                )
                did_split = True
                break

            if has_outer_delim and not outer_split_exhausted:
                bound_end = scheme_spans[k][0]
                bound_cursor = _TokenScanCursor(token, bound_end)
                struct_end = _split_structural_trailing_idx(
                    token,
                    seg_start,
                    bound_end,
                    has_outer_paren=chunk_paren,
                    has_outer_bracket=chunk_bracket,
                    has_outer_dquote=chunk_dquote,
                    has_outer_angle=chunk_angle,
                    has_outer_brace=chunk_brace,
                    has_outer_squote=chunk_squote,
                    scan_cursor=bound_cursor,
                    in_outer_query=chunk_in_outer_query,
                )
                if struct_end < bound_end:
                    cur_c_start = seg_start
                    last_c_start = seg_start
                    cur_span = span_lo
                    cur_c_paren = chunk_paren
                    cur_c_bracket = chunk_bracket
                    cur_c_dquote = chunk_dquote
                    cur_c_angle = chunk_angle
                    cur_c_brace = chunk_brace
                    cur_c_squote = chunk_squote
                    cur_c_in_outer_query = chunk_in_outer_query
                    sub_end = struct_end
                    while cur_span < k and cur_c_start < bound_end:
                        if cur_span > span_lo:
                            sub_end = _split_structural_trailing_idx(
                                token,
                                cur_c_start,
                                bound_end,
                                has_outer_paren=cur_c_paren,
                                has_outer_bracket=cur_c_bracket,
                                has_outer_dquote=cur_c_dquote,
                                has_outer_angle=cur_c_angle,
                                has_outer_brace=cur_c_brace,
                                has_outer_squote=cur_c_squote,
                                scan_cursor=bound_cursor,
                                in_outer_query=cur_c_in_outer_query,
                            )
                        last_c_start = cur_c_start
                        out.append(
                            _redact_redis_url_internal(
                                token[cur_c_start:sub_end],
                                in_outer_query=cur_c_in_outer_query,
                            )
                        )
                        if sub_end >= bound_end:
                            cur_c_start = bound_end
                            break
                        while cur_span < k and scheme_spans[cur_span][0] < sub_end:
                            cur_span += 1
                        if cur_span >= k:
                            break
                        next_c_start = scheme_spans[cur_span][0]
                        if next_c_start > sub_end:
                            out.append(token[sub_end:next_c_start])
                        cur_c_in_outer_query = _gap_stays_in_outer_query(
                            token,
                            cur_c_start,
                            sub_end,
                            next_c_start,
                            cur_c_in_outer_query,
                        )
                        cur_c_paren = next_c_start > 0 and token[next_c_start - 1] == '('
                        cur_c_bracket = next_c_start > 0 and token[next_c_start - 1] == '['
                        cur_c_dquote = next_c_start > 0 and token[next_c_start - 1] == '"'
                        cur_c_angle = next_c_start > 0 and token[next_c_start - 1] == '<'
                        cur_c_brace = next_c_start > 0 and token[next_c_start - 1] == '{'
                        cur_c_squote = next_c_start > 0 and token[next_c_start - 1] == "'"
                        cur_c_start = next_c_start
                    if cur_span >= k:
                        next_span = k
                        while next_span < span_hi and scheme_spans[next_span][0] < sub_end:
                            next_span += 1
                        if next_span >= span_hi:
                            out.append(token[sub_end:stripped_end])
                            if trailing_str:
                                out.append(trailing_str)
                        else:
                            next_start = scheme_spans[next_span][0]
                            if next_start > sub_end:
                                out.append(token[sub_end:next_start])
                            next_cand = c_idx
                            while next_cand < cand_hi and split_candidates[next_cand][0] <= next_span:
                                next_cand += 1
                            next_in_q = _gap_stays_in_outer_query(
                                token,
                                last_c_start,
                                sub_end,
                                next_start,
                                cur_c_in_outer_query,
                            )
                            next_paren = next_start > 0 and token[next_start - 1] == '('
                            next_bracket = next_start > 0 and token[next_start - 1] == '['
                            next_dquote = next_start > 0 and token[next_start - 1] == '"'
                            next_angle = next_start > 0 and token[next_start - 1] == '<'
                            next_brace = next_start > 0 and token[next_start - 1] == '{'
                            next_squote = next_start > 0 and token[next_start - 1] == "'"
                            if trailing_str:
                                stack.append(trailing_str)
                            stack.append(
                                (
                                    next_start,
                                    stripped_end,
                                    next_span,
                                    span_hi,
                                    next_cand,
                                    cand_hi,
                                    next_paren,
                                    next_bracket,
                                    next_dquote,
                                    next_angle,
                                    next_brace,
                                    next_squote,
                                    next_in_q,
                                )
                            )
                    else:
                        next_cand = c_idx + 1
                        while next_cand < cand_hi and split_candidates[next_cand][0] <= k:
                            next_cand += 1
                        next_in_q = _gap_stays_in_outer_query(
                            token,
                            last_c_start,
                            sub_end,
                            bound_end,
                            cur_c_in_outer_query,
                        )
                        next_paren = bound_end > 0 and token[bound_end - 1] == '('
                        next_bracket = bound_end > 0 and token[bound_end - 1] == '['
                        next_dquote = bound_end > 0 and token[bound_end - 1] == '"'
                        next_angle = bound_end > 0 and token[bound_end - 1] == '<'
                        next_brace = bound_end > 0 and token[bound_end - 1] == '{'
                        next_squote = bound_end > 0 and token[bound_end - 1] == "'"
                        if trailing_str:
                            stack.append(trailing_str)
                        stack.append(
                            (
                                bound_end,
                                stripped_end,
                                k,
                                span_hi,
                                next_cand,
                                cand_hi,
                                next_paren,
                                next_bracket,
                                next_dquote,
                                next_angle,
                                next_brace,
                                next_squote,
                                next_in_q,
                            )
                        )
                    did_split = True
                    break
                has_outer_delim = False
                outer_split_exhausted = True

        if not did_split:
            if trailing_str:
                stack.append(trailing_str)
            stack.append(
                (
                    seg_start,
                    stripped_end,
                    span_lo,
                    span_hi,
                    cand_hi,
                    cand_hi,
                    chunk_paren,
                    chunk_bracket,
                    chunk_dquote,
                    chunk_angle,
                    chunk_brace,
                    chunk_squote,
                    chunk_in_outer_query,
                )
            )

    return ''.join(out)


def redact_redis_text(text: str) -> str:
    """Redact credentials from any scheme-prefixed URL tokens inside free-form text."""
    if '://' not in text:
        return text
    n = len(text)
    out: list[str] = []
    cur = 0
    while (found := _find_next_url_token(text, cur, n)) is not None:
        tok_start, scheme_end, tok_end = found
        if tok_start > cur:
            out.append(text[cur:tok_start])
        has_outer_paren = tok_start > 0 and text[tok_start - 1] == '('
        has_outer_bracket = tok_start > 0 and text[tok_start - 1] == '['
        has_outer_dquote = tok_start > 0 and text[tok_start - 1] == '"'
        has_outer_angle = tok_start > 0 and text[tok_start - 1] == '<'
        has_outer_brace = tok_start > 0 and text[tok_start - 1] == '{'
        has_outer_squote = tok_start > 0 and text[tok_start - 1] == "'"
        in_outer_query = False
        eq_check = tok_start
        while eq_check > cur:
            if text[eq_check - 1] in '"\'<{([':
                eq_check -= 1
                if eq_check > cur and text[eq_check - 1] == '\\' and text[eq_check] in ('"', "'"):
                    eq_check -= 1
                continue
            break
        if eq_check > cur and text[eq_check - 1] == '=':
            p = eq_check - 1
            while p > cur and not text[p - 1].isspace():
                p -= 1
            key_start = eq_check - 1
            while key_start > p and text[key_start - 1] in _URL_SPLIT_KEY_CHARS:
                key_start -= 1
            if key_start < eq_check - 1 and key_start > p and text[key_start - 1] in '?&':
                q_idx = text.rfind('?', p, key_start)
                if q_idx != -1 and text.find('#', q_idx + 1, key_start) == -1:
                    in_outer_query = True
        out.append(
            _redact_url_token_with_possible_multi_urls(
                text[tok_start:tok_end],
                scheme_end - tok_start,
                has_outer_paren=has_outer_paren,
                has_outer_bracket=has_outer_bracket,
                has_outer_dquote=has_outer_dquote,
                has_outer_angle=has_outer_angle,
                has_outer_brace=has_outer_brace,
                has_outer_squote=has_outer_squote,
                in_outer_query=in_outer_query,
            )
        )
        cur = tok_end
    if cur == 0:
        return text
    if cur < n:
        out.append(text[cur:])
    return ''.join(out)


_REDIS_USERINFO_SECRET_SPLIT_RE = re.compile(r'[,;)?#/!|:@]')
_REDIS_PORT_CAST_PREFIX = 'Port could not be cast to integer value as '
_PORT_LEAK_DELIMS = frozenset('?#/!|,;)')
_HEX_DIGITS = frozenset(string.hexdigits)


def _contains_any_pattern(haystack: str, patterns: tuple[str, ...] | list[str]) -> bool:
    """Return ``True`` when any non-empty pattern in *patterns* occurs in *haystack* in O(N + M) time."""
    if not haystack or not patterns:
        return False
    if len(patterns) <= 32:
        return any(pat in haystack for pat in patterns if pat)
    children: list[dict[str, int]] = [{}]
    out: list[bool] = [False]
    haystack_len = len(haystack)
    for pat in patterns:
        if not pat or len(pat) > haystack_len:
            continue
        node = 0
        for ch in pat:
            node_children = children[node]
            nxt = node_children.get(ch)
            if nxt is None:
                nxt = len(children)
                node_children[ch] = nxt
                children.append({})
                out.append(False)
            node = nxt
        out[node] = True
    if len(children) == 1:
        return False
    fail: list[int] = [0] * len(children)
    queue: list[int] = list(children[0].values())
    q_idx = 0
    while q_idx < len(queue):
        cur = queue[q_idx]
        q_idx += 1
        cur_fail = fail[cur]
        for ch, nxt in children[cur].items():
            f = cur_fail
            while f and ch not in children[f]:
                f = fail[f]
            f_next = children[f].get(ch, 0)
            fail[nxt] = f_next
            if out[f_next]:
                out[nxt] = True
            queue.append(nxt)
    root_get = children[0].get
    node = 0
    for ch in haystack:
        if node == 0:
            node = root_get(ch, 0)
            if node and out[node]:
                return True
            continue
        nxt = children[node].get(ch)
        while nxt is None and node:
            node = fail[node]
            nxt = children[node].get(ch)
        node = nxt if nxt is not None else 0
        if out[node]:
            return True
    return False


def _extract_port_cast_values(raw_msg: str) -> tuple[str, ...]:
    """Extract all quoted port values from ``urlparse`` port-cast error substrings in *raw_msg*."""
    values: list[str] = []
    seen: set[str] = set()
    n = len(raw_msg)
    prefix_len = len(_REDIS_PORT_CAST_PREFIX)
    pos = 0
    while pos < n:
        idx = raw_msg.find(_REDIS_PORT_CAST_PREFIX, pos)
        if idx == -1:
            break
        q_pos = idx + prefix_len
        if q_pos >= n or raw_msg[q_pos] not in ("'", '"'):
            pos = q_pos
            continue
        quote = raw_msg[q_pos]
        j = q_pos + 1
        has_escape = False
        while j < n:
            ch = raw_msg[j]
            if ch == '\\' and j + 1 < n:
                has_escape = True
                j += 2
                continue
            if ch == quote:
                break
            j += 1
        if j >= n:
            break
        if not has_escape:
            val = raw_msg[q_pos + 1 : j]
        else:
            chars: list[str] = []
            k = q_pos + 1
            while k < j:
                ch = raw_msg[k]
                if ch == '\\' and k + 1 < j:
                    esc = raw_msg[k + 1]
                    if esc in ('\\', "'", '"'):
                        chars.append(esc)
                        k += 2
                        continue
                    if esc == 't':
                        chars.append('\t')
                        k += 2
                        continue
                    if esc == 'n':
                        chars.append('\n')
                        k += 2
                        continue
                    if esc == 'r':
                        chars.append('\r')
                        k += 2
                        continue
                    if (
                        esc == 'x'
                        and k + 3 < j
                        and raw_msg[k + 2] in _HEX_DIGITS
                        and raw_msg[k + 3] in _HEX_DIGITS
                    ):
                        chars.append(chr(int(raw_msg[k + 2 : k + 4], 16)))
                        k += 4
                        continue
                chars.append(ch)
                k += 1
            val = ''.join(chars)
        if val and val not in seen:
            seen.add(val)
            values.append(val)
        pos = j + 1
    return tuple(values)


def _contains_colon_port_before_delim(secret_norm: str, port_val: str) -> bool:
    """Return ``True`` when ``:<port_val>`` appears in *secret_norm* at end or before a delimiter."""
    needle = f':{port_val}'
    m = len(needle)
    n = len(secret_norm)
    pos = secret_norm.find(needle)
    if pos == -1:
        return False
    end = pos + m
    if end == n or secret_norm[end] in _PORT_LEAK_DELIMS:
        return True
    lps = [0] * m
    k = 0
    for idx in range(1, m):
        while k > 0 and needle[k] != needle[idx]:
            k = lps[k - 1]
        if needle[k] == needle[idx]:
            k += 1
            lps[idx] = k
    j = 0
    i = pos + 1
    while i < n:
        if needle[j] == secret_norm[i]:
            i += 1
            j += 1
            if j == m:
                if i == n or secret_norm[i] in _PORT_LEAK_DELIMS:
                    return True
                j = lps[j - 1]
        elif j > 0:
            j = lps[j - 1]
        else:
            i += 1
    return False


def _contains_any_port_leak_aho(secret_norm: str, port_vals: tuple[str, ...]) -> bool:
    """Check prefix and ``:<port_val>`` delimiter-bounded matches in O(|secret_norm| + sum(|port_vals|))."""
    children: list[dict[str, int]] = [{}]
    out: list[bool] = [False]
    n = len(secret_norm)
    for port_val in port_vals:
        if not port_val or len(port_val) > n:
            continue
        node = 0
        for ch in f':{port_val}':
            node_children = children[node]
            nxt = node_children.get(ch)
            if nxt is None:
                nxt = len(children)
                node_children[ch] = nxt
                children.append({})
                out.append(False)
            node = nxt
        out[node] = True
    if len(children) == 1:
        return False
    colon_node = children[0].get(':')
    if colon_node is not None:
        node = colon_node
        for idx, ch in enumerate(secret_norm):
            nxt = children[node].get(ch)
            if nxt is None:
                break
            node = nxt
            if out[node]:
                end = idx + 1
                if end == n or secret_norm[end] in _PORT_LEAK_DELIMS:
                    return True
    fail: list[int] = [0] * len(children)
    queue: list[int] = list(children[0].values())
    q_idx = 0
    while q_idx < len(queue):
        cur = queue[q_idx]
        q_idx += 1
        cur_fail = fail[cur]
        for ch, nxt in children[cur].items():
            f = cur_fail
            while f and ch not in children[f]:
                f = fail[f]
            f_next = children[f].get(ch, 0)
            fail[nxt] = f_next
            if out[f_next]:
                out[nxt] = True
            queue.append(nxt)
    root_get = children[0].get
    node = 0
    for idx, ch in enumerate(secret_norm):
        if node == 0:
            node = root_get(ch, 0)
            if node and out[node]:
                end = idx + 1
                if end == n or secret_norm[end] in _PORT_LEAK_DELIMS:
                    return True
            continue
        nxt = children[node].get(ch)
        while nxt is None and node:
            node = fail[node]
            nxt = children[node].get(ch)
        node = nxt if nxt is not None else 0
        if out[node]:
            end = idx + 1
            if end == n or secret_norm[end] in _PORT_LEAK_DELIMS:
                return True
    return False


def extract_redis_port_leak(raw_msg: str, uri: str) -> bool:
    """Return ``True`` when *raw_msg* contains a schemeless ``urlparse`` port error leaking *uri*'s secret."""
    if '://' not in uri:
        return False
    port_vals = _extract_port_cast_values(raw_msg)
    if not port_vals:
        return False
    scheme, _, rest = uri.partition('://')
    scheme_lower = scheme.lower()
    is_unix = _is_unix_scheme(scheme_lower)
    is_http_like = scheme_lower in _HTTP_LIKE_SCHEMES
    is_sentinel_or_cluster = scheme_lower.endswith(('+sentinel', '+cluster'))
    if is_unix and rest.startswith('/'):
        return False
    authority_split = _split_authority_at(
        rest,
        is_unix=is_unix,
        is_http_like=is_http_like,
        is_sentinel_or_cluster=is_sentinel_or_cluster,
    )
    if authority_split is None:
        return False
    userinfo, _host_and_tail = authority_split
    secret = userinfo.partition(':')[2] if ':' in userinfo else userinfo
    if not secret:
        return False
    secret_norm = secret.replace('\t', '').replace('\r', '').replace('\n', '')
    if not secret_norm:
        return False
    if len(port_vals) <= 4:
        for port_val in port_vals:
            if (
                secret_norm == port_val
                or (
                    len(secret_norm) > len(port_val)
                    and secret_norm.startswith(port_val)
                    and secret_norm[len(port_val)] in _PORT_LEAK_DELIMS
                )
                or _contains_colon_port_before_delim(secret_norm, port_val)
            ):
                return True
        return False
    return _contains_any_port_leak_aho(secret_norm, port_vals)


def _has_secret_sub_leak(redacted_msg: str, secret: str) -> bool:
    """Check whether *secret* or its delimiter-split fragments appear in *redacted_msg* in O(N + M) time."""
    if not secret or not redacted_msg:
        return False
    secret_norm = (
        secret.replace('\t', '').replace('\r', '').replace('\n', '')
        if ('\t' in secret or '\r' in secret or '\n' in secret)
        else secret
    )
    if len(secret) >= 4 and secret in redacted_msg:
        return True
    if secret_norm != secret and len(secret_norm) >= 4 and secret_norm in redacted_msg:
        return True
    seen: set[str] = set()
    subs: list[str] = []
    for candidate_secret in (secret, secret_norm) if secret_norm != secret else (secret,):
        for sub in _REDIS_USERINFO_SECRET_SPLIT_RE.split(candidate_secret):
            if len(sub) >= 4 and sub not in seen:
                seen.add(sub)
                subs.append(sub)
    if not subs:
        return False
    return _contains_any_pattern(redacted_msg, subs)


def _has_short_userinfo_host_leak(redacted_msg: str, secret: str) -> bool:
    """Detect <4-char host tokens parsed from ``@<host>`` inside *secret* in client connection errors."""
    if '@' not in secret or not redacted_msg:
        return False
    secret_norm = (
        secret.replace('\t', '').replace('\r', '').replace('\n', '')
        if ('\t' in secret or '\r' in secret or '\n' in secret)
        else secret
    )
    host_cands: set[str] = set()
    for candidate_secret in (secret, secret_norm) if secret_norm != secret else (secret,):
        for tail_part in candidate_secret.split('@')[1:]:
            host_cand = tail_part.split(':', 1)[0].split('?', 1)[0].split('#', 1)[0].split('/', 1)[0]
            if host_cand:
                host_cands.add(host_cand)
    if not host_cands:
        return False
    if len(host_cands) <= 16:
        return any(
            f' {host_cand}:' in redacted_msg
            or f' {host_cand}.' in redacted_msg
            or f"'{host_cand}'" in redacted_msg
            for host_cand in host_cands
        )
    simple_cands: set[str] = set()
    special_patterns: list[str] = []
    max_simple_len = 0
    for host_cand in host_cands:
        if ' ' in host_cand or '.' in host_cand or "'" in host_cand or ':' in host_cand:
            special_patterns.append(f' {host_cand}:')
            special_patterns.append(f' {host_cand}.')
            special_patterns.append(f"'{host_cand}'")
        else:
            simple_cands.add(host_cand)
            if len(host_cand) > max_simple_len:
                max_simple_len = len(host_cand)
    if simple_cands:
        msg_len = len(redacted_msg)
        pos = redacted_msg.find(' ')
        while pos != -1:
            start = pos + 1
            limit = min(msg_len, start + max_simple_len + 1)
            j = start
            while j < limit:
                ch = redacted_msg[j]
                if ch == ':' or ch == '.':
                    if j > start and redacted_msg[start:j] in simple_cands:
                        return True
                    break
                if ch == ' ':
                    break
                j += 1
            if j < msg_len and redacted_msg[j] == ' ':
                pos = j
            else:
                pos = redacted_msg.find(' ', start)
        q_pos = redacted_msg.find("'")
        while q_pos != -1:
            end_q = redacted_msg.find("'", q_pos + 1)
            if end_q == -1:
                break
            if end_q > q_pos + 1 and end_q - q_pos - 1 <= max_simple_len:
                if redacted_msg[q_pos + 1 : end_q] in simple_cands:
                    return True
            q_pos = end_q
    if special_patterns:
        return _contains_any_pattern(redacted_msg, special_patterns)
    return False


def _redacted_url_leaks_secret(redacted_url: str, secret: str) -> bool:
    """Return ``True`` if *redacted_url* still leaks *secret* or its fragments."""
    if not secret:
        return False
    if _has_secret_sub_leak(redacted_url, secret) or _has_short_userinfo_host_leak(redacted_url, secret):
        return True
    if len(secret) < 4 and (
        f':{secret}@' in redacted_url
        or f'//{secret}@' in redacted_url
        or f':{secret}|' in redacted_url
        or f':{secret}!' in redacted_url
    ):
        return True
    return False


def sanitize_redis_client_error(raw_msg: str, redis_url: str) -> str:
    """Sanitize a Redis client/pool exception message when *redis_url* contains credentials."""
    redacted_msg = redact_redis_text(raw_msg)
    redacted_url = redact_redis_url(redis_url)
    if redacted_url != redis_url:
        port_leaked = extract_redis_port_leak(redacted_msg, redis_url)
        unredacted_at = redacted_msg == raw_msg and '@' in redacted_msg
        scheme = ''
        secret = ''
        authority_split = None
        if '://' in redis_url:
            scheme, _, rest = redis_url.partition('://')
            scheme_lower = scheme.lower()
            is_unix = _is_unix_scheme(scheme_lower)
            is_http_like = scheme_lower in _HTTP_LIKE_SCHEMES
            is_sentinel_or_cluster = scheme_lower.endswith(('+sentinel', '+cluster'))
            if not (is_unix and rest.startswith('/')):
                authority_split = _split_authority_at(
                    rest,
                    is_unix=is_unix,
                    is_http_like=is_http_like,
                    is_sentinel_or_cluster=is_sentinel_or_cluster,
                )
                if authority_split is not None:
                    userinfo, _host_and_tail = authority_split
                    secret = userinfo.partition(':')[2] if ':' in userinfo else userinfo
        safe_target = (
            f'{scheme}://***'
            if scheme and _redacted_url_leaks_secret(redacted_url, secret)
            else redacted_url
        )
        if port_leaked or unredacted_at:
            return f'Invalid Redis configuration for {safe_target}'
        if authority_split is not None:
            if (
                "netloc '" in redacted_msg
                and "' contains invalid characters under NFKC normalization" in redacted_msg
            ):
                return f'Invalid Redis configuration for {safe_target}'
            if secret and (
                _has_secret_sub_leak(redacted_msg, secret)
                or _has_short_userinfo_host_leak(redacted_msg, secret)
            ):
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
