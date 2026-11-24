"""Publication parsing, optional online metadata lookup, and DB linking for rule imports."""

from __future__ import annotations

import logging
import sqlite3

from respro.config.cli_settings import CLI_CONFIG
from respro.io.publications import (
    fetch_publication_metadata,
    fetch_pubmed_id_for_doi,
    fetch_pubmed_metadata,
    normalize_doi_token,
)

logger = logging.getLogger(__name__)

_LINK_TARGETS: dict[str, tuple[str, str]] = {
    'rule': ('rule_publication', 'rule_id'),
    'formula': ('resistance_formula_rule_publication', 'formula_rule_id'),
}


def normalize_publication_token(token: str) -> tuple[str, str, str]:
    """Normalize a single publication token to ``(doi, pubmed_id, raw_input)``."""
    cleaned = token.strip()
    if not cleaned:
        return '', '', ''

    lower = cleaned.lower()
    if lower.startswith('pmid:'):
        return '', cleaned[5:].strip(), cleaned
    for prefix in CLI_CONFIG.parsing.doi_prefixes:
        if lower.startswith(prefix):
            return cleaned[len(prefix):].strip(), '', cleaned
    if lower.startswith('doi.org/'):
        return cleaned[8:].strip(), '', cleaned
    if lower.startswith('doi:'):
        return cleaned[4:].strip(), '', cleaned
    return '', '', cleaned


def parse_publication_entries(raw: str) -> list[tuple[str, str, str]]:
    """Split a comma-separated publication cell into normalized ``(doi, pubmed_id, raw_input)`` tuples."""
    entries: list[tuple[str, str, str]] = []
    for token in raw.split(','):
        doi, pubmed_id, raw_input = normalize_publication_token(token)
        if doi or pubmed_id or raw_input:
            entries.append((doi, pubmed_id, raw_input))
    return entries


def report_publication_lookup_failures(failures: list[str]) -> None:
    """Emit one consolidated warning listing unique publication lookup failures."""
    unique = sorted(set(failures))
    if not unique:
        return
    logger.warning(
        '%d publication metadata lookup(s) failed:\n%s',
        len(unique),
        '\n'.join(f'  - {message}' for message in unique),
    )


def _get_or_create_publication(
    conn: sqlite3.Connection,
    doi: str,
    pubmed_id: str,
    raw_input: str,
    additional_info: bool,
    pub_cache: dict[str, int],
    lookup_failures: list[str] | None = None,
) -> int:
    """Return an existing publication id or create one (enriching via PubMed/CrossRef if requested)."""
    if raw_input in pub_cache:
        return pub_cache[raw_input]

    meta_fields = {'title': '', 'first_author': '', 'year': '', 'journal': ''}
    doi = normalize_doi_token(doi)
    pmid_to_doi = False
    doi_to_pmid = False
    doi_missing_pmid = False

    def _fill_missing(meta: dict[str, str]) -> None:
        for field in meta_fields:
            if not meta_fields[field] and meta.get(field):
                meta_fields[field] = meta[field]

    if additional_info and pubmed_id:
        meta = fetch_pubmed_metadata(pubmed_id)
        if meta:
            if meta.get('doi') and not doi:
                doi = normalize_doi_token(meta['doi'])
                pmid_to_doi = bool(doi)
            _fill_missing(meta)
        elif lookup_failures is not None:
            lookup_failures.append(f'PMID:{pubmed_id} → metadata lookup failed')

    if additional_info and doi and not pubmed_id:
        resolved_pmid = fetch_pubmed_id_for_doi(doi)
        if resolved_pmid:
            pubmed_id = resolved_pmid
            doi_to_pmid = True
            meta = fetch_pubmed_metadata(pubmed_id)
            if meta:
                _fill_missing(meta)
        else:
            doi_missing_pmid = True

    cache_key = doi or raw_input
    if cache_key in pub_cache:
        pub_cache[raw_input] = pub_cache[cache_key]
        return pub_cache[cache_key]

    conn.row_factory = sqlite3.Row
    if doi:
        row = conn.execute('SELECT id FROM publication WHERE doi = ? LIMIT 1', (doi,)).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM publication WHERE doi = '' AND raw_input = ? LIMIT 1",
            (raw_input,),
        ).fetchone()
    if row is not None:
        pub_id = int(row['id'])
        pub_cache[cache_key] = pub_id
        pub_cache[raw_input] = pub_id
        return pub_id

    # CrossRef supplies cleaner bibliographic metadata than PubMed when a DOI is available.
    crossref_title_fetched = False
    if additional_info and doi:
        meta = fetch_publication_metadata(doi)
        if meta:
            if meta.get('title'):
                meta_fields['title'] = meta['title']
                crossref_title_fetched = True
            for field in ('first_author', 'year', 'journal'):
                if meta.get(field):
                    meta_fields[field] = meta[field]

    if crossref_title_fetched:
        if doi_to_pmid:
            logger.info('Resolved DOI %s → PMID:%s and title successfully fetched via CrossRef', doi, pubmed_id)
        elif doi_missing_pmid:
            logger.info('Resolved DOI %s → No PMID found and title successfully fetched via CrossRef', doi)
        if pmid_to_doi:
            logger.info('Resolved PMID %s → DOI %s and title successfully fetched via CrossRef', pubmed_id, doi)
    elif doi_missing_pmid and lookup_failures is not None:
        lookup_failures.append(f'DOI {doi} → identifier lookup failed')

    cur = conn.execute(
        'INSERT INTO publication (doi, title, pubmed_id, raw_input, first_author, year, journal) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            doi,
            meta_fields['title'],
            pubmed_id,
            raw_input,
            meta_fields['first_author'],
            meta_fields['year'],
            meta_fields['journal'],
        ),
    )
    assert cur.lastrowid is not None
    pub_id = int(cur.lastrowid)
    pub_cache[cache_key] = pub_id
    pub_cache[raw_input] = pub_id
    return pub_id


def link_publications(
    conn: sqlite3.Connection,
    target_kind: str,
    row_id: int,
    raw_publication: str,
    additional_info: bool,
    pub_cache: dict[str, int],
    lookup_failures: list[str] | None = None,
) -> None:
    """Parse, resolve, and link every publication in *raw_publication* to an atomic or formula rule."""
    table, fk_column = _LINK_TARGETS[target_kind]
    for doi, pubmed_id, raw_input in parse_publication_entries(raw_publication):
        pub_id = _get_or_create_publication(
            conn, doi, pubmed_id, raw_input, additional_info, pub_cache, lookup_failures,
        )
        conn.execute(
            f'INSERT OR IGNORE INTO {table} ({fk_column}, publication_id) VALUES (?, ?)',
            (row_id, pub_id),
        )
