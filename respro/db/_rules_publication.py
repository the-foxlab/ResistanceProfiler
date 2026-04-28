"""
Publication handling for resistance rule imports — parsing, resolution, and DB linking.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.config.settings import CLI_CONFIG
from respro.io.publications import (
    fetch_publication_metadata,
    fetch_pubmed_id_for_doi,
    fetch_pubmed_metadata,
    normalize_doi_token,
)

logger = logging.getLogger(__name__)


def _normalize_publication_token(token: str) -> tuple[str, str, str]:
    """
    Normalise a single publication token to (doi, pubmed_id, raw_input).

    Accepted forms:
    - ``https://doi.org/10.xxx`` or ``http://doi.org/10.xxx``
    - ``doi.org/10.xxx``
    - ``doi:10.xxx``
    - ``PMID:12345678`` (case-insensitive) — pubmed_id only; doi resolved at insert time
    - anything else → kept as raw_input only

    :param token: single publication string
    :return: (doi, pubmed_id, raw_input) tuple
    """
    t = token.strip()
    if not t:
        return '', '', ''

    lower = t.lower()

    if lower.startswith('pmid:'):
        return '', t[5:].strip(), t

    for prefix in CLI_CONFIG.parsing.doi_prefixes:
        if lower.startswith(prefix):
            return t[len(prefix):].strip(), '', t

    if lower.startswith('doi.org/'):
        return t[8:].strip(), '', t

    if lower.startswith('doi:'):
        return t[4:].strip(), '', t

    return '', '', t


def _parse_publication_entries(raw: str) -> list[tuple[str, str, str]]:
    """
    Split a comma-separated publication string into normalised (doi, pubmed_id, raw_input) tuples.

    :param raw: raw publication string from TSV cell
    :return: list of (doi, pubmed_id, raw_input) tuples; empty entries are dropped
    """
    entries = []
    for token in raw.split(','):
        doi, pubmed_id, raw_input = _normalize_publication_token(token.strip())
        if doi or pubmed_id or raw_input:
            entries.append((doi, pubmed_id, raw_input))
    return entries


def _record_publication_lookup_failure(
    publication_lookup_failures: list[str] | None,
    message: str,
) -> None:
    """Append one publication lookup failure message when collection is enabled."""
    if publication_lookup_failures is None:
        return
    publication_lookup_failures.append(message)


def _report_publication_lookup_failures(publication_lookup_failures: list[str]) -> None:
    """Emit one consolidated warning block for failed publication metadata lookups."""
    unique_failures = sorted(set(publication_lookup_failures))
    if not unique_failures:
        return
    logger.warning(
        '%d publication metadata lookup(s) failed:\n%s',
        len(unique_failures),
        '\n'.join(f'  - {message}' for message in unique_failures),
    )


def _get_or_create_publication(
    conn: sqlite3.Connection,
    doi: str,
    pubmed_id: str,
    raw_input: str,
    additional_info: bool,
    pub_cache: dict[str, int],
    publication_lookup_failures: list[str] | None = None,
) -> int:
    """
    Return the id of an existing publication row, creating one if needed.

    Dedup key: ``doi`` when non-empty (including DOIs resolved from a PMID);
    otherwise ``raw_input``.  Both the resolved key and the original
    ``raw_input`` token are stored in the cache so that repeated references
    to the same PMID skip the network lookup on every call after the first.

    When ``additional_info`` is True:
    - A PMID is looked up via NCBI E-utilities, which returns both the title
      and the DOI (when available) in a single call.
    - If no PMID is present but a DOI is, the title is fetched from CrossRef.
    Both lookups are best-effort and non-fatal; a missing title is acceptable.

    :param conn: SQLite database connection
    :param doi: bare DOI string (may be empty)
    :param pubmed_id: PubMed ID digits string (may be empty)
    :param raw_input: original curator token (preserved as fallback)
    :param additional_info: whether to attempt HTTP metadata resolution
    :param pub_cache: in-process cache mapping dedup key → publication id
    :return: publication row id
    """
    # Fast path: raw_input is always known before any network call; if we have
    # already processed this exact token (e.g. the same PMID appears on many
    # rules), return immediately without hitting the network again.
    if raw_input in pub_cache:
        return pub_cache[raw_input]

    prefetched_title = ''
    doi = normalize_doi_token(doi)
    pmid_to_doi_resolved = False
    doi_to_pmid_resolved = False
    doi_lookup_missing_pmid = False
    if additional_info and pubmed_id:
        meta = fetch_pubmed_metadata(pubmed_id)
        if meta:
            if meta['doi'] and not doi:
                doi = normalize_doi_token(meta['doi'])
                if doi:
                    pmid_to_doi_resolved = True
            prefetched_title = meta['title']
        else:
            _record_publication_lookup_failure(
                publication_lookup_failures,
                f'PMID:{pubmed_id} → metadata lookup failed',
            )

    if additional_info and doi and not pubmed_id:
        resolved_pubmed_id = fetch_pubmed_id_for_doi(doi)
        if resolved_pubmed_id:
            pubmed_id = resolved_pubmed_id
            doi_to_pmid_resolved = True

            meta = fetch_pubmed_metadata(pubmed_id)
            if meta:
                prefetched_title = prefetched_title or meta.get('title', '')
                if meta['doi'] and not doi:
                    normalized_doi = normalize_doi_token(meta['doi'])
                    if normalized_doi:
                        doi = normalized_doi
                        pmid_to_doi_resolved = True
        else:
            doi_lookup_missing_pmid = True

    cache_key = doi if doi else raw_input

    if cache_key in pub_cache:
        # The resolved DOI is already cached (e.g. reached via a different raw form).
        # Also register raw_input so future calls hit this fast path.
        pub_cache[raw_input] = pub_cache[cache_key]
        return pub_cache[cache_key]

    conn.row_factory = sqlite3.Row
    if doi:
        row = conn.execute(
            'SELECT id FROM publication WHERE doi = ? LIMIT 1', (doi,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM publication WHERE doi = '' AND raw_input = ? LIMIT 1", (raw_input,)
        ).fetchone()

    if row is not None:
        pub_id = int(row['id'])
        pub_cache[cache_key] = pub_id
        pub_cache[raw_input] = pub_id
        return pub_id

    # Prefer CrossRef title whenever a DOI is known; fall back to PMID title.
    title = prefetched_title
    crossref_title_fetched = False
    if additional_info and doi:
        meta = fetch_publication_metadata(doi)
        if meta:
            crossref_title = meta.get('title', '')
            if crossref_title:
                title = crossref_title
                crossref_title_fetched = True

    if crossref_title_fetched:
        if doi_to_pmid_resolved:
            logger.info(
                'Resolved DOI %s → PMID:%s and title successfully fetched via CrossRef',
                doi,
                pubmed_id,
            )
        elif doi_lookup_missing_pmid:
            logger.info(
                'Resolved DOI %s → No PMID found and title successfully fetched via CrossRef',
                doi,
            )
        if pmid_to_doi_resolved:
            logger.info(
                'Resolved PMID %s → DOI %s and title successfully fetched via CrossRef',
                pubmed_id,
                doi,
            )
    elif doi_lookup_missing_pmid:
        _record_publication_lookup_failure(
            publication_lookup_failures,
            f'DOI {doi} → identifier lookup failed',
        )

    cur = conn.execute(
        'INSERT INTO publication (doi, title, pubmed_id, raw_input) VALUES (?, ?, ?, ?)',
        (doi, title, pubmed_id, raw_input),
    )
    pub_id = int(cur.lastrowid)  # type: ignore[arg-type]
    pub_cache[cache_key] = pub_id
    pub_cache[raw_input] = pub_id
    return pub_id


def _link_rule_publications(
    conn: sqlite3.Connection,
    rule_id: int,
    raw_publication: str,
    additional_info: bool,
    pub_cache: dict[str, int],
    publication_lookup_failures: list[str] | None = None,
) -> None:
    """Parse, resolve, and link all publications in a raw TSV cell to a single rule."""
    for doi, pubmed_id, raw_input in _parse_publication_entries(raw_publication):
        pub_id = _get_or_create_publication(
            conn,
            doi,
            pubmed_id,
            raw_input,
            additional_info,
            pub_cache,
            publication_lookup_failures,
        )
        conn.execute(
            'INSERT OR IGNORE INTO rule_publication (rule_id, publication_id) VALUES (?, ?)',
            (rule_id, pub_id),
        )


def _link_formula_rule_publications(
    conn: sqlite3.Connection,
    formula_rule_id: int,
    raw_publication: str,
    additional_info: bool,
    pub_cache: dict[str, int],
    publication_lookup_failures: list[str] | None = None,
) -> None:
    """Parse, resolve, and link all publications in a raw TSV cell to one formula rule."""
    for doi, pubmed_id, raw_input in _parse_publication_entries(raw_publication):
        pub_id = _get_or_create_publication(
            conn,
            doi,
            pubmed_id,
            raw_input,
            additional_info,
            pub_cache,
            publication_lookup_failures,
        )
        conn.execute(
            'INSERT OR IGNORE INTO resistance_formula_rule_publication '
            '(formula_rule_id, publication_id) VALUES (?, ?)',
            (formula_rule_id, pub_id),
        )
