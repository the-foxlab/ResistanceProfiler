"""Project database metadata loading, validation, and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from respro.core.algorithms import validate_interpretation_algorithms
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
_ALLOWED_PROJECT_COLUMNS = frozenset(_ALLOWED_METADATA_KEYS.values()) | {'metadata_publication_doi'}

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
    """Load and validate a project metadata JSON file into ``(column_payload, algorithms)``."""
    require_file(metadata_path, 'Metadata JSON file')
    try:
        payload = json.loads(metadata_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid metadata JSON: {exc}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Invalid metadata JSON: top-level value must be an object.')

    raw_algorithms: object = None
    has_algorithms = False
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
        if canonical == 'interpretation_algorithms':
            raw_algorithms = raw_value
            has_algorithms = True
            continue
        value = _normalize_metadata_value(canonical, raw_value)
        if value:
            normalized[canonical] = value

    algorithms = validate_interpretation_algorithms(raw_algorithms) if has_algorithms and raw_algorithms is not None else []

    if invalid_keys:
        allowed = ', '.join(sorted(_ALLOWED_METADATA_KEYS))
        raise ValueError(f"Invalid metadata key(s): {', '.join(invalid_keys)}. Allowed keys: {allowed}")

    publication_pmid = normalized.get('publication_pmid', '').strip()
    if publication_pmid:
        meta = fetch_pubmed_metadata(publication_pmid)
        if meta and meta.get('doi'):
            normalized['publication_doi'] = str(meta['doi']).strip()

    return _to_project_column_payload(normalized), algorithms


def store_project_metadata(
    conn: sqlite3.Connection,
    project_id: int,
    metadata: dict[str, str],
) -> None:
    """Persist validated metadata columns onto the ``project`` row."""
    if not metadata:
        return
    unknown = sorted(set(metadata) - _ALLOWED_PROJECT_COLUMNS)
    if unknown:
        raise ValueError(f'Unsupported project metadata column(s): {", ".join(unknown)}')

    assignments = ', '.join(f'{col} = ?' for col in metadata)
    values: list[str | int] = [*metadata.values(), project_id]
    conn.execute(
        f"UPDATE project SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        tuple(values),
    )


def _canonical_metadata_key(raw_key: str) -> str | None:
    return _CANONICAL_KEY_ALIASES.get(raw_key.strip().lower().replace('-', '_'))


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
        if pmid and not pmid.isdigit():
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
        elif key in _ALLOWED_METADATA_KEYS:
            payload[_ALLOWED_METADATA_KEYS[key]] = value
    return payload
