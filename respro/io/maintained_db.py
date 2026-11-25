"""Client for the companion maintained-database repository and NCBI GenBank efetch."""

from __future__ import annotations

import csv
import http.client
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG
from respro.io.http import parse_retry_after

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_CODES = frozenset({429, 503})
_ACCESSION_RE = re.compile(r'^[A-Za-z0-9_]+(\.\d+)?$')
_VERSION_SPEC_RE = re.compile(r'^\s*>=\s*(\d+(?:\.\d+)*)\s*$')
_MAINTAINED_DB_NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]*\Z')
_MAX_DB_NAME_LEN = 128
_STREAM_CHUNK_BYTES = 65536
_MAX_MANIFEST_BYTES = 5 * 1024 * 1024
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_MAX_GENBANK_BYTES = 50 * 1024 * 1024
_MAX_REDIRECTS = 5
_DEFAULT_PORTS = {'http': 80, 'https': 443}
_FORBIDDEN_ENCODED_TOKENS = ('%2e', '%2f', '%5c', '%00')
_DEFAULT_URLOPEN = urllib.request.urlopen


def validate_maintained_db_name(name: str) -> str:
    """Validate *name* as a single safe filename component for maintained databases."""
    if not isinstance(name, str) or not (1 <= len(name) <= _MAX_DB_NAME_LEN):
        raise ValueError(
            f'Invalid maintained database name {name!r}: must be a 1-{_MAX_DB_NAME_LEN} character string.'
        )
    if (
        name in {'.', '..'}
        or any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in name)
        or not _MAINTAINED_DB_NAME_RE.match(name)
    ):
        raise ValueError(
            f'Invalid maintained database name {name!r}: must be a single safe filename component '
            'without path separators, traversal sequences, whitespace, or control characters.'
        )
    return name


def _validate_accession(accession: str) -> None:
    """Reject accessions with path separators or traversal characters before building filenames."""
    if not accession or not _ACCESSION_RE.match(accession):
        raise ValueError(
            f'Invalid GenBank accession {accession!r}: accessions must be alphanumeric '
            '(underscores allowed) with an optional .version suffix, and must not '
            'contain path separators or traversal sequences.'
        )


def list_maintained_databases() -> list[str]:
    """Return sorted database names listed in the companion repository manifest."""
    return sorted(entry['source_name'] for entry in _fetch_manifest()['databases'])


def list_maintained_databases_with_checksums() -> list[tuple[str, str]]:
    """Return sorted ``(source_name, tsv_checksum)`` pairs for all maintained databases."""
    entries = [
        (entry['source_name'], str(entry.get('metadata', {}).get('tsv_checksum', '') or ''))
        for entry in _fetch_manifest()['databases']
    ]
    return sorted(entries, key=lambda item: item[0])


def fetch_database_metadata(db_name: str) -> dict:
    """Return the parsed ``metadata.json`` payload for *db_name* from the manifest."""
    return dict(_find_manifest_database_entry(db_name)['metadata'])


def list_output_files(db_name: str) -> list[dict]:
    """Return ``[{'name', 'download_url'}]`` for *db_name*'s output files."""
    entry = _find_manifest_database_entry(db_name)
    files = [
        {'name': 'rules.tsv', 'download_url': _resolve_manifest_url(entry['rules_path'])},
        {'name': 'metadata.json', 'download_url': _resolve_manifest_url(entry['metadata_path'])},
    ]
    for filename, key in (('formula-rules.tsv', 'formula_rules_path'), ('example.fasta', 'example_fasta_path')):
        path = entry.get(key, '')
        if path:
            files.append({'name': filename, 'download_url': _resolve_manifest_url(path)})
    return files


def download_database_files(db_name: str, dest_dir: Path) -> dict[str, object]:
    """Download curated TSV/metadata/example files and fetch referenced GenBank records."""
    file_map = {entry['name']: entry['download_url'] for entry in list_output_files(db_name)}
    for required in ('rules.tsv', 'metadata.json'):
        if required not in file_map:
            raise RuntimeError(f'Database {db_name!r} is missing required {required} in output/')

    rules_path = _download_file(file_map['rules.tsv'], dest_dir / 'rules.tsv', f'{db_name}/rules.tsv')
    metadata_path = _download_file(file_map['metadata.json'], dest_dir / 'metadata.json', f'{db_name}/metadata.json')

    formula_rules_path = (
        _download_file(file_map['formula-rules.tsv'], dest_dir / 'formula-rules.tsv', f'{db_name}/formula-rules.tsv')
        if 'formula-rules.tsv' in file_map else None
    )
    example_path = (
        _download_file(file_map['example.fasta'], dest_dir / 'example.fasta', f'{db_name}/example.fasta')
        if 'example.fasta' in file_map else None
    )

    accessions = _parse_reference_identifiers(rules_path)
    genbank_paths = _fetch_genbank_records(accessions, dest_dir)
    return {
        'rules': rules_path,
        'metadata': metadata_path,
        'formula_rules': formula_rules_path,
        'example': example_path,
        'genbank': genbank_paths,
    }


def _has_control_or_whitespace(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in value)


def _validate_manifest_relative_path(raw_path: object, *, field: str, allow_empty: bool = False) -> str:
    """Validate that a manifest path is a safe relative POSIX path with no traversal or URL parts."""
    if not isinstance(raw_path, str):
        raise RuntimeError(f'Malformed manifest: {field} must be a string')
    if raw_path == '':
        if allow_empty:
            return ''
        raise RuntimeError(f'Malformed manifest: {field} must be a non-empty relative path')
    if _has_control_or_whitespace(raw_path) or '\\' in raw_path or raw_path.startswith('/'):
        raise RuntimeError(f'Malformed manifest: {field} must be a relative POSIX path ({raw_path!r})')

    lowered = raw_path.lower()
    if any(tok in lowered for tok in _FORBIDDEN_ENCODED_TOKENS):
        raise RuntimeError(f'Malformed manifest: {field} contains forbidden encoded path characters ({raw_path!r})')

    parsed = urllib.parse.urlsplit(raw_path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError(f'Malformed manifest: {field} must be a relative path, not a URL ({raw_path!r})')

    decoded = urllib.parse.unquote(raw_path)
    if _has_control_or_whitespace(decoded) or '\\' in decoded or decoded.startswith('/'):
        raise RuntimeError(f'Malformed manifest: {field} contains unsafe decoded characters ({raw_path!r})')

    for candidate in (raw_path, decoded):
        segments = candidate.split('/')
        if any(seg in {'', '.', '..'} for seg in segments):
            raise RuntimeError(f'Malformed manifest: {field} contains invalid path segment ({raw_path!r})')
    return raw_path


def _parse_origin_and_prefix(base_url: str, *, label: str, strip_accession_suffix: bool = False) -> tuple[tuple[str, str, int], tuple[str, ...]]:
    """Return ``((scheme, host, port), prefix_segments)`` for a configured base URL."""
    if _has_control_or_whitespace(base_url):
        raise RuntimeError(f'Unsafe {label}: whitespace or control characters are not allowed')
    parsed = urllib.parse.urlsplit(base_url)
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'}:
        raise RuntimeError(f'Unsafe {label}: scheme must be http or https, got {parsed.scheme!r}')
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f'Unsafe {label}: embedded credentials are not allowed')
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f'Unsafe {label}: invalid port') from exc
    host = (parsed.hostname or '').lower()
    if not host:
        raise RuntimeError(f'Unsafe {label}: missing hostname')
    effective_port = port if port is not None else _DEFAULT_PORTS[scheme]

    raw_segments = [seg for seg in parsed.path.split('/') if seg]
    if strip_accession_suffix:
        prefix: list[str] = []
        for seg in raw_segments:
            if '{accession}' in seg or 'ACCESSION_PLACEHOLDER' in seg:
                break
            prefix.append(seg)
        raw_segments = prefix
    return (scheme, host, effective_port), tuple(raw_segments)


def _validate_target_url(
    url: str,
    *,
    allowed_origin: tuple[str, str, int],
    allowed_prefix: tuple[str, ...],
    label: str,
) -> str:
    """Validate that *url* matches *allowed_origin* and starts with *allowed_prefix* segments."""
    if not isinstance(url, str) or not url or _has_control_or_whitespace(url):
        raise RuntimeError(f'Blocked untrusted {label} URL: {url!r}')
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'}:
        raise RuntimeError(f'Blocked untrusted {label} scheme {parsed.scheme!r}: {url}')
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f'Blocked {label} URL with embedded credentials: {url}')
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f'Blocked {label} URL with invalid port: {url}') from exc
    host = (parsed.hostname or '').lower()
    if not host:
        raise RuntimeError(f'Blocked {label} URL missing hostname: {url}')
    effective_port = port if port is not None else _DEFAULT_PORTS[scheme]
    if (scheme, host, effective_port) != allowed_origin:
        raise RuntimeError(f'Blocked cross-origin {label} URL: {url}')

    lowered_path = parsed.path.lower()
    if any(tok in lowered_path for tok in _FORBIDDEN_ENCODED_TOKENS) or '\\' in parsed.path:
        raise RuntimeError(f'Blocked unsafe path in {label} URL: {url}')
    decoded_path = urllib.parse.unquote(parsed.path)
    if _has_control_or_whitespace(decoded_path) or '\\' in decoded_path:
        raise RuntimeError(f'Blocked unsafe decoded path in {label} URL: {url}')
    for candidate in (parsed.path, decoded_path):
        if any(seg in {'.', '..'} for seg in candidate.split('/')):
            raise RuntimeError(f'Blocked traversal segment in {label} URL: {url}')

    target_segments = tuple(seg for seg in parsed.path.split('/') if seg)
    if target_segments[:len(allowed_prefix)] != allowed_prefix:
        raise RuntimeError(f'Blocked out-of-prefix {label} URL: {url}')
    return url


class _OriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler enforcing same-origin, path-prefix confinement, and hop limit."""

    max_redirections = _MAX_REDIRECTS

    def __init__(
        self,
        *,
        allowed_origin: tuple[str, str, int],
        allowed_prefix: tuple[str, ...],
        max_redirects: int = _MAX_REDIRECTS,
        label: str,
    ) -> None:
        self._allowed_origin = allowed_origin
        self._allowed_prefix = allowed_prefix
        self._max_redirects = max_redirects
        self._redirect_count = 0
        self._label = label

    def _validate_redirect(self, req: urllib.request.Request, fp: Any, newurl: str) -> None:
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            fp.close()
            raise RuntimeError(
                f'Exceeded maximum redirects ({self._max_redirects}) for {self._label}: {req.full_url}'
            )
        try:
            _validate_target_url(
                newurl,
                allowed_origin=self._allowed_origin,
                allowed_prefix=self._allowed_prefix,
                label=f'{self._label} redirect',
            )
        except RuntimeError:
            fp.close()
            raise

    def http_error_302(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
    ) -> Any:
        location = headers.get('location') or headers.get('uri')
        if location is None:
            return None
        resolved = urllib.parse.urljoin(req.full_url, location)
        self._validate_redirect(req, fp, resolved)
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _open_url(
    url: str,
    *,
    timeout: float,
    allowed_origin: tuple[str, str, int],
    allowed_prefix: tuple[str, ...],
    label: str,
) -> Any:
    """Validate *url* and open via monkeypatched ``urlopen`` (in tests) or confined opener."""
    _validate_target_url(
        url,
        allowed_origin=allowed_origin,
        allowed_prefix=allowed_prefix,
        label=label,
    )
    if urllib.request.urlopen is not _DEFAULT_URLOPEN:
        resp = urllib.request.urlopen(url, timeout=timeout)
    else:
        handler = _OriginRedirectHandler(
            allowed_origin=allowed_origin,
            allowed_prefix=allowed_prefix,
            max_redirects=_MAX_REDIRECTS,
            label=label,
        )
        opener = urllib.request.build_opener(handler)
        resp = opener.open(url, timeout=timeout)

    final_url = resp.geturl() if hasattr(resp, 'geturl') else None
    if isinstance(final_url, str) and final_url:
        try:
            _validate_target_url(
                final_url,
                allowed_origin=allowed_origin,
                allowed_prefix=allowed_prefix,
                label=f'{label} final',
            )
        except RuntimeError:
            resp.close()
            raise
    return resp


def _check_content_length(resp: Any, max_bytes: int, label: str) -> None:
    headers = getattr(resp, 'headers', None)
    if headers is None or not hasattr(headers, 'get'):
        return
    raw_len = headers.get('Content-Length')
    if isinstance(raw_len, str) and raw_len.strip().isdigit():
        if int(raw_len.strip()) > max_bytes:
            raise RuntimeError(f'{label} exceeds maximum allowed size ({max_bytes} bytes)')


def _read_bounded_bytes(resp: Any, max_bytes: int, label: str) -> bytes:
    _check_content_length(resp, max_bytes, label)
    buf = bytearray()
    while True:
        chunk = resp.read(_STREAM_CHUNK_BYTES)
        if not chunk:
            break
        if len(buf) + len(chunk) > max_bytes:
            raise RuntimeError(f'{label} exceeds maximum allowed size ({max_bytes} bytes)')
        buf.extend(chunk)
    return bytes(buf)


def _stream_to_file(
    resp: Any,
    dest: Path,
    *,
    max_bytes: int,
    label: str,
    require_locus: bool = False,
    accession: str = '',
) -> Path:
    _check_content_length(resp, max_bytes, label)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    found_locus = False
    tail = b''
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest.parent,
            prefix=f'.{dest.name}.',
            suffix='.part',
            delete=False,
        ) as tmp_fh:
            tmp_path = Path(tmp_fh.name)
            while True:
                chunk = resp.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f'{label} exceeds maximum allowed size ({max_bytes} bytes)')
                if require_locus and not found_locus:
                    window = tail + chunk
                    if b'LOCUS' in window:
                        found_locus = True
                    else:
                        tail = window[-4:]
                tmp_fh.write(chunk)
        if require_locus and not found_locus:
            raise RuntimeError(
                f'NCBI returned unexpected content for accession {accession!r} — '
                'not a valid GenBank record'
            )
        os.replace(tmp_path, dest)
        tmp_path = None
        return dest
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _fetch_manifest() -> dict:
    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    allowed_origin, allowed_prefix = _parse_origin_and_prefix(raw_base, label='manifest base URL')
    manifest_url = f'{raw_base}/manifest.json'
    payload = _fetch_json(
        manifest_url,
        context='maintained database manifest',
        allowed_origin=allowed_origin,
        allowed_prefix=allowed_prefix,
    )
    if not isinstance(payload, dict):
        raise RuntimeError('Malformed manifest: top-level JSON value must be an object')
    databases = payload.get('databases')
    if not isinstance(databases, list):
        raise RuntimeError('Malformed manifest: databases must be a list')
    for idx, entry in enumerate(databases):
        _validate_manifest_entry(entry, idx)
    _check_respro_version_compatibility(payload)
    return payload


def _validate_manifest_entry(entry: object, idx: int) -> None:
    if not isinstance(entry, dict):
        raise RuntimeError(f'Malformed manifest: databases[{idx}] must be an object')
    for field in ('source_name', 'metadata_path', 'rules_path'):
        val = entry.get(field)
        if not isinstance(val, str) or not val.strip():
            raise RuntimeError(f'Malformed manifest: databases[{idx}].{field} must be a non-empty string')
    try:
        validate_maintained_db_name(entry['source_name'])
    except ValueError as exc:
        raise RuntimeError(f'Malformed manifest: databases[{idx}].source_name is invalid: {exc}') from exc

    _validate_manifest_relative_path(entry['metadata_path'], field=f'databases[{idx}].metadata_path')
    _validate_manifest_relative_path(entry['rules_path'], field=f'databases[{idx}].rules_path')

    formula_rules_path = entry.get('formula_rules_path')
    if not isinstance(formula_rules_path, str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].formula_rules_path must be a string')
    _validate_manifest_relative_path(
        formula_rules_path,
        field=f'databases[{idx}].formula_rules_path',
        allow_empty=True,
    )

    example_fasta_path = entry.get('example_fasta_path', '')
    if not isinstance(example_fasta_path, str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].example_fasta_path must be a string')
    _validate_manifest_relative_path(
        example_fasta_path,
        field=f'databases[{idx}].example_fasta_path',
        allow_empty=True,
    )

    if not isinstance(entry.get('metadata'), dict):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].metadata must be an object')


def _check_respro_version_compatibility(manifest: dict) -> None:
    """Fail fast when the installed respro version is older than the manifest's minimum."""
    raw_spec = manifest.get('respro_version')
    if raw_spec is None:
        return
    if not isinstance(raw_spec, str) or not raw_spec.strip():
        raise RuntimeError('Malformed manifest: respro_version must be a non-empty string')

    required = _parse_required_version(raw_spec)
    if __version__ == '0.0.0' or _version_tuple(__version__) < required:
        raise RuntimeError(
            f'The installed respro version ({__version__}) is incompatible with the '
            f'maintained databases, which require respro {raw_spec}. '
            'Please upgrade respro (e.g. ``pip install --upgrade respro``) and try again.'
        )


def _parse_required_version(spec: str) -> tuple[int, ...]:
    match = _VERSION_SPEC_RE.match(spec)
    if not match:
        raise RuntimeError(f'Malformed manifest: respro_version must be ">=X.Y.Z", got {spec!r}')
    return tuple(int(part) for part in match.group(1).split('.'))


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split('.'):
        match = re.match(r'\d+', part)
        parts.append(int(match.group()) if match is not None else 0)
    return tuple(parts)


def _find_manifest_database_entry(db_name: str) -> dict:
    validate_maintained_db_name(db_name)
    for entry in _fetch_manifest()['databases']:
        if entry['source_name'] == db_name:
            return dict(entry)
    raise RuntimeError(f'Unknown maintained database {db_name!r}')


def _resolve_manifest_url(path: str) -> str:
    cleaned = _validate_manifest_relative_path(path, field='output path')
    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    allowed_origin, allowed_prefix = _parse_origin_and_prefix(raw_base, label='manifest base URL')
    if cleaned.startswith('databases/') and raw_base.endswith('/databases'):
        raw_base = raw_base[:-len('/databases')]
    resolved = urllib.parse.urljoin(f'{raw_base}/', cleaned)
    return _validate_target_url(
        resolved,
        allowed_origin=allowed_origin,
        allowed_prefix=allowed_prefix,
        label='manifest asset',
    )


def _fetch_json(
    url: str,
    *,
    context: str,
    allowed_origin: tuple[str, str, int],
    allowed_prefix: tuple[str, ...],
) -> object:
    try:
        with _open_url(
            url,
            timeout=15,
            allowed_origin=allowed_origin,
            allowed_prefix=allowed_prefix,
            label=context,
        ) as resp:
            raw = _read_bounded_bytes(resp, _MAX_MANIFEST_BYTES, context)
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Failed to fetch {context}: HTTP {exc.code} — {url}') from exc
    except OSError as exc:
        raise RuntimeError(f'Network error while fetching {context}: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid JSON in {context}: {exc}') from exc


def _download_file(url: str, dest: Path, label: str) -> Path:
    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    allowed_origin, allowed_prefix = _parse_origin_and_prefix(raw_base, label='manifest base URL')
    try:
        with _open_url(
            url,
            timeout=30,
            allowed_origin=allowed_origin,
            allowed_prefix=allowed_prefix,
            label=label,
        ) as resp:
            _stream_to_file(resp, dest, max_bytes=_MAX_DOWNLOAD_BYTES, label=label)
        logger.debug('Downloaded %s → %s', label, dest)
        return dest
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Failed to download {label}: HTTP {exc.code}') from exc
    except OSError as exc:
        raise RuntimeError(f'Network error downloading {label}: {exc}') from exc


def _parse_reference_identifiers(rules_path: Path) -> list[str]:
    """Return sorted unique non-empty ``reference_identifier`` accessions from *rules_path*."""
    accessions: set[str] = set()
    with rules_path.open(newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        if reader.fieldnames is None or 'reference_identifier' not in reader.fieldnames:
            return []
        for row in reader:
            value = (row.get('reference_identifier') or '').strip()
            if value:
                accessions.add(value)
    return sorted(accessions)


def _fetch_genbank_records(accessions: list[str], dest_dir: Path) -> list[Path]:
    """Fetch GenBank files from NCBI with pacing, retry/backoff on 429/503, and transport retries."""
    paths: list[Path] = []
    timeout = CLI_CONFIG.timeouts.genbank_timeout
    max_retries = CLI_CONFIG.timeouts.genbank_max_retries
    backoff_base = CLI_CONFIG.timeouts.genbank_backoff_base
    request_interval = CLI_CONFIG.timeouts.genbank_request_interval
    allowed_origin, allowed_prefix = _parse_origin_and_prefix(
        CLI_CONFIG.urls.ncbi_nuccore_efetch,
        label='GenBank efetch URL',
        strip_accession_suffix=True,
    )

    for index, accession in enumerate(accessions):
        if index > 0:
            time.sleep(request_interval)
        _validate_accession(accession)
        url = CLI_CONFIG.urls.ncbi_nuccore_efetch.format(accession=urllib.parse.quote(accession))
        dest = dest_dir / f'{accession}.gb'

        fetched = False
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                with _open_url(
                    url,
                    timeout=timeout,
                    allowed_origin=allowed_origin,
                    allowed_prefix=allowed_prefix,
                    label=f'GenBank {accession}',
                ) as resp:
                    _stream_to_file(
                        resp,
                        dest,
                        max_bytes=_MAX_GENBANK_BYTES,
                        label=f'GenBank {accession}',
                        require_locus=True,
                        accession=accession,
                    )
                fetched = True
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRYABLE_HTTP_CODES:
                    raise RuntimeError(
                        f'Failed to fetch GenBank record for {accession!r}: HTTP {exc.code}'
                    ) from exc
                last_exc = exc
                logger.warning(
                    'Rate-limited/availability error fetching GenBank record %r (attempt %d/%d): HTTP %s',
                    accession, attempt, max_retries, exc.code,
                )
                if attempt < max_retries:
                    delay = parse_retry_after(exc, default=backoff_base * (2 ** (attempt - 1)))
                    assert delay is not None
                    time.sleep(delay)
            except (OSError, http.client.HTTPException) as exc:
                last_exc = exc
                logger.warning(
                    'Transient error fetching GenBank record %r (attempt %d/%d): %s',
                    accession, attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    time.sleep(backoff_base * (2 ** (attempt - 1)))

        if not fetched:
            raise RuntimeError(
                f'Network error fetching GenBank record for {accession!r} '
                f'after {max_retries} attempts: {last_exc}'
            ) from last_exc
        logger.debug('Downloaded GenBank record %s → %s', accession, dest)
        paths.append(dest)
    return paths
