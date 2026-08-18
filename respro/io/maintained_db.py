"""
Client for the respro companion database repository (https://github.com/the-foxlab/respro-databases).

Fetches database listings, metadata, and downloads rules/GenBank files for use with respro init.
All public functions raise RuntimeError on any network or HTTP failure.
"""

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

from respro.config.cli_settings import CLI_CONFIG

logger = logging.getLogger(__name__)

_GENBANK_TIMEOUT = 30
# NCBI eutils is rate-limited and intermittently resets connections or returns
# truncated chunked responses. Retry transient failures a few times with
# exponential backoff before giving up. HTTPError is NOT retried — a 4xx/5xx
# status is a permanent response, not a transport hiccup.
_GENBANK_MAX_RETRIES = 3
_GENBANK_BACKOFF_BASE = 1.0  # seconds; doubled each retry (1, 2, 4)

# NCBI nucleotide accession: letters/digits/underscore, optional ``.version`` suffix.
# Rejects path separators, ``..``, whitespace, and other characters that could escape
# ``dest_dir`` when interpolated into ``f'{accession}.gb'`` (SEC-006).
_ACCESSION_RE = re.compile(r'^[A-Za-z0-9_]+(\.\d+)?$')


def _validate_accession(accession: str) -> None:
    """Reject accessions that could escape ``dest_dir`` via path traversal (SEC-006).

    NCBI nucleotide accessions are alphanumeric (with underscores) optionally followed
    by a ``.<version>`` suffix. Anything containing path separators, ``..``, or other
    filesystem-significant characters is rejected before it is used to build a path.
    """
    if not accession or not _ACCESSION_RE.match(accession):
        raise ValueError(
            f'Invalid GenBank accession {accession!r}: accessions must be alphanumeric '
            '(underscores allowed) with an optional .version suffix, and must not '
            'contain path separators or traversal sequences.'
        )


def list_maintained_databases() -> list[str]:
    """
    Return the names of all available databases in the companion repository.

    :return: sorted list of database folder names (e.g. ['hsv_daehne_jaki'])
    """
    manifest = _fetch_manifest()
    return sorted(entry['source_name'] for entry in manifest['databases'])


def list_maintained_databases_with_checksums() -> list[tuple[str, str]]:
    """
    Return ``(source_name, tsv_checksum)`` for every database in the manifest.

    :return: sorted list of (database name, tsv_checksum) tuples; checksum may be
        empty string if the manifest entry lacks one
    """
    manifest = _fetch_manifest()
    entries = [
        (entry['source_name'], str(entry.get('metadata', {}).get('tsv_checksum', '') or ''))
        for entry in manifest['databases']
    ]
    return sorted(entries, key=lambda item: item[0])


def fetch_database_metadata(db_name: str) -> dict:
    """
    Fetch the metadata.json for a named database.

    :param db_name: folder name of the database (e.g. 'hsv_daehne_jaki')
    :return: parsed metadata dict
    """
    entry = _find_manifest_database_entry(db_name)
    return dict(entry['metadata'])


def download_database_files(db_name: str, dest_dir: Path) -> dict[str, object]:
    """
    Download all files needed to initialise a project database.

    Downloads ``rules.tsv``, ``metadata.json``, and optionally ``formula-rules.tsv``
    from the companion repository.  Then parses the unique non-empty
    ``reference_identifier`` values from ``rules.tsv`` and fetches each as a
    GenBank record from NCBI.

    :param db_name: folder name of the database (e.g. 'hsv_daehne_jaki')
    :param dest_dir: directory where all files are written
    :return: dict with keys ``'rules'``, ``'metadata'``, ``'formula_rules'`` (Path or None),
             and ``'genbank'`` (list[Path])
    """
    files_listing = list_output_files(db_name)
    file_map = {entry['name']: entry['download_url'] for entry in files_listing}

    if 'rules.tsv' not in file_map:
        raise RuntimeError(f'Database {db_name!r} is missing required rules.tsv in output/')
    if 'metadata.json' not in file_map:
        raise RuntimeError(f'Database {db_name!r} is missing required metadata.json in output/')

    rules_path = _download_file(file_map['rules.tsv'], dest_dir / 'rules.tsv', f'{db_name}/rules.tsv')
    metadata_path = _download_file(
        file_map['metadata.json'], dest_dir / 'metadata.json', f'{db_name}/metadata.json'
    )

    formula_rules_path: Path | None = None
    if 'formula-rules.tsv' in file_map:
        formula_rules_path = _download_file(
            file_map['formula-rules.tsv'],
            dest_dir / 'formula-rules.tsv',
            f'{db_name}/formula-rules.tsv',
        )

    example_path: Path | None = None
    if 'example.fasta' in file_map:
        example_path = _download_file(
            file_map['example.fasta'],
            dest_dir / 'example.fasta',
            f'{db_name}/example.fasta',
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


def list_output_files(db_name: str) -> list[dict]:
    """
    Return the file listing for a database's output/ folder.

    :param db_name: folder name of the database
    :return: list of dicts with at least ``name`` and ``download_url`` keys
    """
    entry = _find_manifest_database_entry(db_name)
    output_files = [
        {
            'name': 'rules.tsv',
            'download_url': _resolve_manifest_path_to_download_url(entry['rules_path']),
        },
        {
            'name': 'metadata.json',
            'download_url': _resolve_manifest_path_to_download_url(entry['metadata_path']),
        },
    ]

    formula_rules_path = entry['formula_rules_path']
    if formula_rules_path:
        output_files.append(
            {
                'name': 'formula-rules.tsv',
                'download_url': _resolve_manifest_path_to_download_url(formula_rules_path),
            }
        )

    example_fasta_path = entry.get('example_fasta_path', '')
    if example_fasta_path:
        output_files.append(
            {
                'name': 'example.fasta',
                'download_url': _resolve_manifest_path_to_download_url(example_fasta_path),
            }
        )

    return output_files


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _fetch_manifest() -> dict:
    """Fetch and validate the global maintained database manifest."""
    manifest_url = f"{CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')}/manifest.json"
    payload = _fetch_json(manifest_url, context='maintained database manifest')

    if not isinstance(payload, dict):
        raise RuntimeError('Malformed manifest: top-level JSON value must be an object')

    manifest_version = payload.get('manifest_version')
    if not isinstance(manifest_version, int):
        raise RuntimeError('Malformed manifest: manifest_version must be an integer')

    databases = payload.get('databases')
    if not isinstance(databases, list):
        raise RuntimeError('Malformed manifest: databases must be a list')

    for idx, entry in enumerate(databases):
        _validate_manifest_entry(entry, idx)

    return payload


def _validate_manifest_entry(entry: object, idx: int) -> None:
    """Validate one database entry in the manifest."""
    if not isinstance(entry, dict):
        raise RuntimeError(f'Malformed manifest: databases[{idx}] must be an object')

    source_name = entry.get('source_name')
    if not isinstance(source_name, str) or not source_name.strip():
        raise RuntimeError(f'Malformed manifest: databases[{idx}].source_name must be a non-empty string')

    metadata_path = entry.get('metadata_path')
    if not isinstance(metadata_path, str) or not metadata_path.strip():
        raise RuntimeError(
            f'Malformed manifest: databases[{idx}].metadata_path must be a non-empty string'
        )

    rules_path = entry.get('rules_path')
    if not isinstance(rules_path, str) or not rules_path.strip():
        raise RuntimeError(f'Malformed manifest: databases[{idx}].rules_path must be a non-empty string')

    formula_rules_path = entry.get('formula_rules_path')
    if not isinstance(formula_rules_path, str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].formula_rules_path must be a string')

    example_fasta_path = entry.get('example_fasta_path', '')
    if not isinstance(example_fasta_path, str):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].example_fasta_path must be a string')

    metadata = entry.get('metadata')
    if not isinstance(metadata, dict):
        raise RuntimeError(f'Malformed manifest: databases[{idx}].metadata must be an object')


def _find_manifest_database_entry(db_name: str) -> dict:
    """Return the manifest database entry for a source name."""
    manifest = _fetch_manifest()
    for entry in manifest['databases']:
        if entry['source_name'] == db_name:
            return dict(entry)
    raise RuntimeError(f'Unknown maintained database {db_name!r}')


def _resolve_manifest_path_to_download_url(path: str) -> str:
    """Resolve a manifest path to a raw GitHub download URL."""
    cleaned_path = path.lstrip('/')
    if not cleaned_path:
        raise RuntimeError('Malformed manifest: output path must not be empty')

    if cleaned_path.startswith('databases/'):
        base_url = _derive_raw_repo_root_url()
        return urllib.parse.urljoin(f"{base_url.rstrip('/')}/", cleaned_path)

    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    return urllib.parse.urljoin(f'{raw_base}/', cleaned_path)


def _derive_raw_repo_root_url() -> str:
    """Derive raw repo root from configured raw databases URL."""
    raw_base = CLI_CONFIG.urls.github_respro_db_raw.rstrip('/')
    if raw_base.endswith('/databases'):
        return raw_base[: -len('/databases')]
    return raw_base


def _fetch_json(url: str, *, context: str) -> object:
    """Fetch URL and parse response as JSON; raise RuntimeError on failure."""
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
    """Download a URL to dest; raise RuntimeError on failure."""
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
    """
    Parse unique non-empty reference_identifier values from a rules TSV.

    :param rules_path: path to the rules TSV file
    :return: sorted list of unique accession strings
    """
    accessions: set[str] = set()
    with rules_path.open(newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        if reader.fieldnames is None or 'reference_identifier' not in reader.fieldnames:
            return []
        for row in reader:
            value = row.get('reference_identifier', '').strip()
            if value:
                accessions.add(value)
    return sorted(accessions)


def _fetch_genbank_records(accessions: list[str], dest_dir: Path) -> list[Path]:
    """
    Fetch GenBank records for a list of NCBI accession IDs.

    Transient transport failures (``ConnectionResetError``, ``IncompleteRead``,
    and other ``OSError`` / ``http.client.HTTPException`` subclasses) are
    retried up to ``_GENBANK_MAX_RETRIES`` times with exponential backoff.
    A permanent ``HTTPError`` (4xx/5xx) is raised immediately without
    retrying. NCBI eutils is rate-limited, so a short inter-request delay is
    also applied between successful accessions.

    :param accessions: list of nucleotide accession strings
    :param dest_dir: directory where .gb files are written
    :return: list of paths to written GenBank files
    :raises RuntimeError: on permanent HTTP failure or after exhausting retries
    :raises ValueError: on an invalid accession (via ``_validate_accession``)
    """
    paths: list[Path] = []
    for accession in accessions:
        _validate_accession(accession)
        url = CLI_CONFIG.urls.ncbi_nuccore_efetch.format(accession=urllib.parse.quote(accession))
        dest = dest_dir / f'{accession}.gb'

        content: bytes | None = None
        last_exc: Exception | None = None
        for attempt in range(1, _GENBANK_MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(url, timeout=_GENBANK_TIMEOUT) as resp:
                    content = resp.read()
                break  # success
            except urllib.error.HTTPError as exc:
                # Permanent response — do not retry.
                raise RuntimeError(
                    f'Failed to fetch GenBank record for {accession!r}: HTTP {exc.code}'
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                # Transient transport failure — retry with backoff.
                last_exc = exc
                logger.warning(
                    'Transient error fetching GenBank record %r (attempt %d/%d): %s',
                    accession, attempt, _GENBANK_MAX_RETRIES, exc,
                )
                if attempt < _GENBANK_MAX_RETRIES:
                    time.sleep(_GENBANK_BACKOFF_BASE * (2 ** (attempt - 1)))

        if content is None:
            raise RuntimeError(
                f'Network error fetching GenBank record for {accession!r} '
                f'after {_GENBANK_MAX_RETRIES} attempts: {last_exc}'
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
