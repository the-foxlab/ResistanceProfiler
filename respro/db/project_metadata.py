"""Project database metadata loading, validation, and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from respro.db.algorithms import validate_interpretation_algorithms
from respro.io.publications import fetch_pubmed_metadata
from respro.utils.files import require_file

_ALLOWED_METADATA_KEYS = {
    'maintainers': 'metadata_maintainers',
    'contact': 'metadata_contact',
    'publication_pmid': 'metadata_publication_pmid',
    'website': 'metadata_website',
    'description': 'metadata_description',
    'maintainer_update': 'metadata_maintainer_update',
    'license': 'metadata_license',
    'tsv_checksum': 'metadata_tsv_checksum',
}

_CANONICAL_KEY_ALIASES = {
    'maintainers': 'maintainers',
    'maintainer': 'maintainers',
    'contact': 'contact',
    'publication': 'publication_pmid',
    'publication_pmid': 'publication_pmid',
    'pmid': 'publication_pmid',
    'website': 'website',
    'description': 'description',
    'maintainer_update': 'maintainer_update',
    'maintainer update': 'maintainer_update',
    'license': 'license',
    'tsv_checksum': 'tsv_checksum',
    'tsv checksum': 'tsv_checksum',
    'interpretation_algorithms': 'interpretation_algorithms',
}


def load_metadata_json(metadata_path: Path) -> tuple[dict[str, str], list[dict]]:
    """
    Load and validate a metadata JSON file for project creation.

    :param metadata_path: path to metadata JSON
    :return: tuple of (normalized metadata dict mapped to project column values,
             validated list of interpretation algorithm configs)
    """
    require_file(metadata_path, 'Metadata JSON file')

    try:
        payload = json.loads(metadata_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid metadata JSON: {exc}') from exc

    if not isinstance(payload, dict):
        raise ValueError('Invalid metadata JSON: top-level value must be an object.')

    raw_algorithms = payload.pop('interpretation_algorithms', None)
    algorithms: list[dict] = []
    if raw_algorithms is not None:
        algorithms = validate_interpretation_algorithms(raw_algorithms)

    normalized: dict[str, str] = {}
    invalid_keys: list[str] = []

    for raw_key, raw_value in payload.items():
        if not isinstance(raw_key, str):
            invalid_keys.append(str(raw_key))
            continue

        canonical = _canonical_metadata_key(raw_key)
        if canonical is None:
            invalid_keys.append(raw_key)
            continue

        normalized_value = _normalize_metadata_value(canonical, raw_value)
        if normalized_value:
            normalized[canonical] = normalized_value

    if invalid_keys:
        allowed = ', '.join(sorted(_ALLOWED_METADATA_KEYS.keys()))
        unknown = ', '.join(invalid_keys)
        raise ValueError(f'Invalid metadata key(s): {unknown}. Allowed keys: {allowed}')

    publication_pmid = normalized.get('publication_pmid', '').strip()
    if publication_pmid:
        metadata = fetch_pubmed_metadata(publication_pmid)
        if metadata and metadata.get('doi'):
            normalized['publication_doi'] = str(metadata['doi']).strip()

    return _to_project_column_payload(normalized), algorithms


def store_project_metadata(
    conn: sqlite3.Connection,
    project_id: int,
    metadata: dict[str, str],
) -> None:
    """
    Persist validated metadata values on the project row.

    :param conn: project DB connection
    :param project_id: project id to update
    :param metadata: normalized project-column metadata values
    """
    if not metadata:
        return

    assignments = ', '.join(f'{column} = ?' for column in metadata)
    values = [metadata[column] for column in metadata]
    values.append(project_id)
    conn.execute(
        f'UPDATE project SET {assignments}, updated_at = datetime(\'now\') WHERE id = ?',
        tuple(values),
    )


def _canonical_metadata_key(raw_key: str) -> str | None:
    lowered = raw_key.strip().lower().replace('-', '_')
    return _CANONICAL_KEY_ALIASES.get(lowered)


def _normalize_metadata_value(key: str, value: object) -> str:
    if value is None:
        return ''

    if key == 'maintainers':
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return '; '.join(item.strip() for item in value if item.strip())
        raise ValueError('Invalid metadata value for maintainers: expected string or array of strings.')

    if key == 'publication_pmid':
        pmid = str(value).strip()
        if not pmid:
            return ''
        if not pmid.isdigit():
            raise ValueError('Invalid metadata value for publication_pmid: expected PubMed ID digits.')
        return pmid

    if isinstance(value, str):
        return value.strip()

    raise ValueError(f'Invalid metadata value for {key}: expected string.')


def _to_project_column_payload(metadata: dict[str, str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in metadata.items():
        if key == 'publication_doi':
            payload['metadata_publication_doi'] = value
            continue
        project_column = _ALLOWED_METADATA_KEYS.get(key)
        if project_column is not None:
            payload[project_column] = value
    return payload
