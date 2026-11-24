"""Client for the companion maintained-database repository and NCBI GenBank efetch."""

from __future__ import annotations

import csv
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG
from respro.io.http import parse_retry_after

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_CODES = frozenset({429, 503})
_ACCESSION_RE = re.compile(r'^[A-Za-z0-9_]+(\.\d+)?$')
_VERSION_SPEC_RE = re.compile(r'^\s*>=\s*(\d+(?:\.\d+)*)\s*$')


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


def _fetch_manifest() -> dict:
    manifest_url = f"{CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')}/manifest.json"
    payload = _fetch_json(manifest_url, context='maintained database manifest')
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
    if not isinstance(entry.get('formula_rules_path'), str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].formula_rules_path must be a string')
    if not isinstance(entry.get('example_fasta_path', ''), str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].example_fasta_path must be a string')
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
    for entry in _fetch_manifest()['databases']:
        if entry['source_name'] == db_name:
            return dict(entry)
    raise RuntimeError(f'Unknown maintained database {db_name!r}')


def _resolve_manifest_url(path: str) -> str:
    cleaned = path.lstrip('/')
    if not cleaned:
        raise RuntimeError('Malformed manifest: output path must not be empty')
    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    if cleaned.startswith('databases/') and raw_base.endswith('/databases'):
        raw_base = raw_base[:-len('/databases')]
    return urllib.parse.urljoin(f'{raw_base}/', cleaned)


def _fetch_json(url: str, *, context: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Failed to fetch {context}: HTTP {exc.code} — {url}') from exc
    except OSError as exc:
        raise RuntimeError(f'Network error while fetching {context}: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid JSON in {context}: {exc}') from exc


def _download_file(url: str, dest: Path, label: str) -> Path:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            dest.write_bytes(resp.read())
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

    for index, accession in enumerate(accessions):
        if index > 0:
            time.sleep(request_interval)
        _validate_accession(accession)
        url = CLI_CONFIG.urls.ncbi_nuccore_efetch.format(accession=urllib.parse.quote(accession))
        dest = dest_dir / f'{accession}.gb'

        content: bytes | None = None
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    content = resp.read()
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

        if content is None:
            raise RuntimeError(
                f'Network error fetching GenBank record for {accession!r} '
                f'after {max_retries} attempts: {last_exc}'
            ) from last_exc
        if b'LOCUS' not in content:
            raise RuntimeError(
                f'NCBI returned unexpected content for accession {accession!r} — '
                'not a valid GenBank record'
            )
        dest.write_bytes(content)
        logger.debug('Downloaded GenBank record %s → %s', accession, dest)
        paths.append(dest)
    return paths
