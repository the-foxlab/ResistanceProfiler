"""Thin clients for NCBI E-utilities (PubMed / PMC idconv) and CrossRef publication metadata."""

from __future__ import annotations

import logging
import re
import urllib.parse

from respro.config.cli_settings import CLI_CONFIG
from respro.io.http import LookupStatus, fetch_json_lookup

logger = logging.getLogger(__name__)

_RATE_LIMIT_CODES = frozenset({429})
_RATE_LIMIT_BACKOFF = (0.5, 1.0, 2.0)
_RE_DOI_LIKE = re.compile(r'^10\.\S+/\S+$', re.IGNORECASE)

_TITLE_TAG_RE = re.compile(r'(\s*)<([A-Za-z][A-Za-z0-9]*)>(.*?)</\2>(\s*)', re.DOTALL)
_RE_TITLE_LEFTOVER_TAG = re.compile(r'</?[A-Za-z][^>]*>')
_RE_TITLE_WHITESPACE = re.compile(r'\s+')


def _replace_title_tag(match: re.Match[str]) -> str:
    """Strip one inline JATS/NLM markup tag while rejoining split word fragments."""
    leading, content_raw, trailing = match.group(1), match.group(3), match.group(4)
    content = content_raw.strip()
    if not content:
        return f'{leading} '
    if ' ' in content:
        return f'{leading} {content} '
    rest = match.string[match.end():]
    if not trailing:
        if rest[:1].isalnum() or rest[:1] in '([':
            return f'{leading}{content}'
    elif rest[:1].islower() and ('\n' in trailing or len(trailing) > 1 or len(content) == 1):
        # Indented line break inside a tagged word fragment, e.g. '<scp>S</scp>\n  outhern'.
        return f'{leading}{content}'
    return f'{leading}{content} '


def _clean_publication_title(raw_title: object) -> str:
    """Normalize raw JATS/HTML-marked titles to plain single-spaced text."""
    if not isinstance(raw_title, str):
        return ''
    title = raw_title
    for _ in range(10):
        cleaned = _TITLE_TAG_RE.sub(_replace_title_tag, title)
        if cleaned == title:
            break
        title = cleaned
    title = _RE_TITLE_LEFTOVER_TAG.sub('', title)
    return _RE_TITLE_WHITESPACE.sub(' ', title).strip()


def fetch_pubmed_metadata(pmid: str, timeout: int = CLI_CONFIG.timeouts.pubmed) -> dict | None:
    """Fetch title, DOI, first author, year, and journal for *pmid* via NCBI esummary."""
    url = CLI_CONFIG.urls.ncbi_pubmed_esummary.format(pmid=urllib.parse.quote(pmid))
    res = fetch_json_lookup(
        url,
        timeout=timeout,
        label=f'NCBI PMID lookup for {pmid!r}',
        not_found_codes=frozenset(),
        retry_codes=_RATE_LIMIT_CODES,
        backoff_seconds=_RATE_LIMIT_BACKOFF,
    )
    if res.status is not LookupStatus.FOUND or not isinstance(res.value, dict):
        return None

    result = res.value.get('result')
    result_data = result.get(pmid) if isinstance(result, dict) else None
    if not isinstance(result_data, dict):
        return None
    article_ids = result_data.get('articleids')
    if not isinstance(article_ids, list):
        return None

    doi = ''
    for entry in article_ids:
        if not isinstance(entry, dict):
            return None
        if entry.get('idtype') == 'doi':
            raw_doi = entry.get('value', '')
            doi = raw_doi.strip() if isinstance(raw_doi, str) else ''
            break

    raw_journal = result_data.get('fulljournalname', '')
    return {
        'title': _clean_publication_title(result_data.get('title', '')),
        'doi': doi,
        'first_author': _parse_pubmed_first_author(result_data.get('authors')),
        'year': _parse_pubmed_year(result_data.get('pubdate', '')),
        'journal': raw_journal.strip() if isinstance(raw_journal, str) else '',
    }


def fetch_pubmed_id_for_doi(doi: str, timeout: int = CLI_CONFIG.timeouts.pubmed) -> str | None:
    """Resolve a DOI to its PubMed ID via NCBI PMC idconv, or ``None`` if unavailable."""
    normalized = normalize_doi_token(doi)
    if not normalized or not _RE_DOI_LIKE.match(normalized):
        return None

    url = CLI_CONFIG.urls.ncbi_pmc_idconv.format(identifier=urllib.parse.quote(normalized, safe=''))
    res = fetch_json_lookup(
        url,
        timeout=timeout,
        label=f'NCBI DOI->PMID lookup for {normalized!r}',
        not_found_codes=frozenset({400, 404}),
        retry_codes=_RATE_LIMIT_CODES,
        backoff_seconds=_RATE_LIMIT_BACKOFF,
    )
    if res.status is not LookupStatus.FOUND or not isinstance(res.value, dict):
        return None
    records = res.value.get('records')
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None
    raw_pmid = records[0].get('pmid')
    if isinstance(raw_pmid, bool) or not isinstance(raw_pmid, (str, int)):
        return None
    pmid = str(raw_pmid).strip()
    return pmid or None


def fetch_publication_metadata(doi: str, timeout: int = CLI_CONFIG.timeouts.crossref) -> dict | None:
    """Fetch title, first author, year, and journal for *doi* via the CrossRef works API."""
    normalized = normalize_doi_token(doi)
    if not normalized:
        return None

    url = CLI_CONFIG.urls.crossref_works.format(doi=urllib.parse.quote(normalized, safe=''))
    res = fetch_json_lookup(
        url,
        timeout=timeout,
        label=f'CrossRef lookup for DOI {normalized!r}',
        not_found_codes=frozenset({404}),
        retry_codes=_RATE_LIMIT_CODES,
        backoff_seconds=_RATE_LIMIT_BACKOFF,
    )
    if res.status is not LookupStatus.FOUND or not isinstance(res.value, dict):
        return None
    message = res.value.get('message')
    if not isinstance(message, dict):
        return None
    titles = message.get('title')
    if not isinstance(titles, list):
        return None

    title = _clean_publication_title(titles[0] if titles else '')
    if not title:
        logger.debug('CrossRef: no title found for DOI %r', normalized)
        return None

    container = message.get('container-title')
    journal = container[0].strip() if isinstance(container, list) and container and isinstance(container[0], str) else ''
    return {
        'title': title,
        'first_author': _parse_crossref_first_author(message.get('author')),
        'year': _parse_crossref_year(message),
        'journal': journal,
    }


def _parse_pubmed_first_author(authors: object) -> str:
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return ''
    name = authors[0].get('name', '')
    return name.strip() if isinstance(name, str) else ''


def _parse_pubmed_year(pubdate: object) -> str:
    if not isinstance(pubdate, str):
        return ''
    match = re.search(r'(\d{4})', pubdate)
    return match.group(1) if match else ''


def _parse_crossref_first_author(authors: object) -> str:
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return ''
    family = authors[0].get('family', '')
    return family.strip() if isinstance(family, str) else ''


def _parse_crossref_year(message: dict) -> str:
    for key in ('published', 'issued'):
        node = message.get(key)
        date_parts = node.get('date-parts') if isinstance(node, dict) else None
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = date_parts[0][0]
            if isinstance(year, int):
                return str(year)
            if isinstance(year, str) and year.strip():
                return year.strip()
    return ''


def normalize_doi_token(token: str) -> str:
    """Strip URL/scheme prefixes and trailing punctuation from a DOI token."""
    value = token.strip().rstrip('.,;:')
    if not value:
        return ''
    lower = value.lower()
    for prefix in (*CLI_CONFIG.parsing.doi_prefixes, 'https://dx.doi.org/', 'http://dx.doi.org/'):
        if lower.startswith(prefix.lower()):
            value = value[len(prefix):].strip()
            lower = value.lower()
            break
    for prefix in ('doi.org/', 'dx.doi.org/', 'doi:'):
        if lower.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value.strip().rstrip('.,;:')
