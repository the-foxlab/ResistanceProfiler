"""
Client for the respro companion database repository (https://github.com/jonas-fuchs/respro-db).

Fetches database listings, metadata, and downloads rules/GenBank files for use with respro init.
All public functions raise RuntimeError on any network or HTTP failure.
"""

from __future__ import annotations

import csv
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from respro.config.settings import CLI_CONFIG

logger = logging.getLogger(__name__)

_GENBANK_TIMEOUT = 30


def list_maintained_databases() -> list[str]:
    """
    Return the names of all available databases in the companion repository.

    :return: sorted list of database folder names (e.g. ['hsv_daehne_jaki'])
    """
    url = CLI_CONFIG.urls.github_respro_db_api
    data = _fetch_json(url, context='database listing')
    return sorted(entry['name'] for entry in data if entry.get('type') == 'dir')


def fetch_database_metadata(db_name: str) -> dict:
    """
    Fetch the metadata.json for a named database.

    :param db_name: folder name of the database (e.g. 'hsv_daehne_jaki')
    :return: parsed metadata dict
    """
    url = f'{CLI_CONFIG.urls.github_respro_db_raw}/{db_name}/output/metadata.json'
    return _fetch_json(url, context=f'metadata for {db_name!r}')


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

    accessions = _parse_reference_identifiers(rules_path)
    genbank_paths = _fetch_genbank_records(accessions, dest_dir)

    return {
        'rules': rules_path,
        'metadata': metadata_path,
        'formula_rules': formula_rules_path,
        'genbank': genbank_paths,
    }


def list_output_files(db_name: str) -> list[dict]:
    """
    Return the file listing for a database's output/ folder.

    :param db_name: folder name of the database
    :return: list of dicts with at least ``name`` and ``download_url`` keys
    """
    url = f'{CLI_CONFIG.urls.github_respro_db_api}/{db_name}/output'
    return _fetch_json(url, context=f'output listing for {db_name!r}')


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

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

    :param accessions: list of nucleotide accession strings
    :param dest_dir: directory where .gb files are written
    :return: list of paths to written GenBank files
    """
    paths: list[Path] = []
    for accession in accessions:
        url = CLI_CONFIG.urls.ncbi_nuccore_efetch.format(accession=urllib.request.quote(accession))
        dest = dest_dir / f'{accession}.gb'
        try:
            with urllib.request.urlopen(url, timeout=_GENBANK_TIMEOUT) as resp:
                content = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f'Failed to fetch GenBank record for {accession!r}: HTTP {exc.code}'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'Network error fetching GenBank record for {accession!r}: {exc}'
            ) from exc

        if b'LOCUS' not in content:
            raise RuntimeError(
                f'NCBI returned unexpected content for accession {accession!r} — '
                'not a valid GenBank record'
            )
        dest.write_bytes(content)
        logger.debug('Downloaded GenBank record %s → %s', accession, dest)
        paths.append(dest)

    return paths
