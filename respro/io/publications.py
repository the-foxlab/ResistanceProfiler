"""
Thin clients for NCBI E-utilities and CrossRef APIs.

Fetches publication metadata (title, DOI) from PubMed IDs and DOIs.
All public functions return None on any failure — callers treat this as best-effort.

PubMed entries are resolved via a single NCBI E-utilities esummary call that
returns both the title and the DOI (when one exists), so no separate DOI-resolution
step is needed.  Old publications that have no DOI are handled correctly: the
PubMed ID is stored as the primary identifier and a PubMed link is generated at
display time.

DOI-only entries fall back to CrossRef for the title.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
from time import sleep
from urllib.request import urlopen

from respro.config.cli_settings import CLI_CONFIG

logger = logging.getLogger(__name__)

_PUBMED_RATE_LIMIT_RETRIES = 3
_PUBMED_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_DOI_RATE_LIMIT_RETRIES = 3
_DOI_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_RE_DOI_LIKE = r'^10\.\S+/\S+$'


def fetch_pubmed_metadata(pmid: str, timeout: int = CLI_CONFIG.timeouts.pubmed) -> dict | None:
    """
    Fetch title and DOI (when available) for a PubMed article via NCBI E-utilities.

    A single esummary call returns both fields, so no separate DOI-resolution
    step is needed.  Old publications that have no DOI return an empty ``'doi'``
    key; callers store the PMID as the primary identifier in that case.

    :param pmid: numeric PubMed ID string (digits only)
    :param timeout: HTTP request timeout in seconds
    :return: dict with ``'title'`` (str) and ``'doi'`` (str, may be empty), or
             ``None`` on any network / parsing failure
    """
    url = CLI_CONFIG.urls.ncbi_pubmed_esummary.format(pmid=urllib.parse.quote(pmid))
    for attempt in range(_PUBMED_RATE_LIMIT_RETRIES + 1):
        try:
            with urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('NCBI PMID lookup malformed payload for %r: root is %s', pmid, type(data).__name__)
                return None
            result = data.get('result')
            if not isinstance(result, dict):
                logger.debug('NCBI PMID lookup malformed payload for %r: missing result object', pmid)
                return None
            result_data = result.get(pmid)
            if not isinstance(result_data, dict):
                logger.debug('NCBI PMID lookup malformed payload for %r: missing PMID result object', pmid)
                return None
            raw_title = result_data.get('title', '')
            title = raw_title.strip() if isinstance(raw_title, str) else ''
            doi = ''
            article_ids = result_data.get('articleids')
            if not isinstance(article_ids, list):
                logger.debug('NCBI PMID lookup malformed payload for %r: articleids is not a list', pmid)
                return None
            for entry in article_ids:
                if not isinstance(entry, dict):
                    logger.debug('NCBI PMID lookup malformed payload for %r: articleids entry is not an object', pmid)
                    return None
                if entry.get('idtype') == 'doi':
                    raw_doi = entry.get('value', '')
                    doi = raw_doi.strip() if isinstance(raw_doi, str) else ''
                    break
            return {'title': title, 'doi': doi}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _PUBMED_RATE_LIMIT_RETRIES:
                delay = _parse_retry_after(exc)
                if delay is None:
                    delay = _PUBMED_BACKOFF_SECONDS[min(attempt, len(_PUBMED_BACKOFF_SECONDS) - 1)]
                logger.debug(
                    'NCBI PMID lookup rate-limited for %r: HTTP 429, retrying in %.2fs',
                    pmid,
                    delay,
                )
                sleep(delay)
                continue
            logger.warning('NCBI PMID lookup failed for %r: HTTP %s', pmid, exc.code)
            return None
        except OSError as exc:
            logger.warning('NCBI PMID lookup failed for %r (network): %s', pmid, exc)
            return None
        except Exception as exc:
            logger.warning('NCBI PMID lookup failed for %r: %s', pmid, exc)
            return None

    logger.warning('NCBI PMID lookup failed for %r: all %d retries exhausted', pmid, _PUBMED_RATE_LIMIT_RETRIES + 1)
    return None


def fetch_pubmed_id_for_doi(doi: str, timeout: int = CLI_CONFIG.timeouts.pubmed) -> str | None:
    """
    Resolve a DOI to a PubMed ID via the NCBI PMC idconv API.

    :param doi: bare DOI string (e.g. ``10.1234/xyz``)
    :param timeout: HTTP request timeout in seconds
    :return: PubMed ID string or ``None`` if unavailable
    """
    normalized_doi = normalize_doi_token(doi)
    if not normalized_doi:
        return None
    if not re.match(_RE_DOI_LIKE, normalized_doi, flags=re.IGNORECASE):
        return None

    encoded = urllib.parse.quote(normalized_doi, safe='')
    url = CLI_CONFIG.urls.ncbi_pmc_idconv.format(identifier=encoded)
    for attempt in range(_DOI_RATE_LIMIT_RETRIES + 1):
        try:
            with urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug(
                    'NCBI DOI->PMID lookup malformed payload for %r: root is %s',
                    normalized_doi,
                    type(data).__name__,
                )
                return None
            records = data.get('records')
            if not isinstance(records, list):
                logger.debug(
                    'NCBI DOI->PMID lookup malformed payload for %r: records is not a list',
                    normalized_doi,
                )
                return None
            if not records:
                return None

            first_record = records[0]
            if not isinstance(first_record, dict):
                logger.debug(
                    'NCBI DOI->PMID lookup malformed payload for %r: first record is not an object',
                    normalized_doi,
                )
                return None
            pmid = str(first_record.get('pmid', '')).strip()
            if pmid:
                return pmid

            return None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _DOI_RATE_LIMIT_RETRIES:
                delay = _parse_retry_after(exc)
                if delay is None:
                    delay = _DOI_BACKOFF_SECONDS[min(attempt, len(_DOI_BACKOFF_SECONDS) - 1)]
                logger.debug(
                    'NCBI DOI->PMID lookup rate-limited for %r: HTTP 429, retrying in %.2fs',
                    normalized_doi,
                    delay,
                )
                sleep(delay)
                continue
            if exc.code in (400, 404):
                logger.debug('NCBI DOI->PMID lookup returned HTTP %s for %r', exc.code, normalized_doi)
                return None
            logger.warning('NCBI DOI->PMID lookup failed for %r: HTTP %s', normalized_doi, exc.code)
            return None
        except OSError as exc:
            logger.warning('NCBI DOI->PMID lookup failed for %r (network): %s', normalized_doi, exc)
            return None
        except Exception as exc:
            logger.warning('NCBI DOI->PMID lookup failed for %r: %s', normalized_doi, exc)
            return None

    logger.warning('NCBI DOI->PMID lookup failed for %r: all %d retries exhausted', normalized_doi, _DOI_RATE_LIMIT_RETRIES + 1)
    return None


def fetch_publication_metadata(doi: str, timeout: int = CLI_CONFIG.timeouts.crossref) -> dict | None:
    """
    Fetch a publication title from the CrossRef API.

    Used as a fallback for DOI-only entries that have no PubMed ID.

    :param doi: bare DOI string (e.g. ``10.1234/xyz``)
    :param timeout: HTTP request timeout in seconds
    :return: dict with key ``'title'`` or None if unavailable
    """
    normalized_doi = normalize_doi_token(doi)
    if not normalized_doi:
        return None

    encoded = urllib.parse.quote(normalized_doi, safe='')
    url = CLI_CONFIG.urls.crossref_works.format(doi=encoded)
    for attempt in range(_DOI_RATE_LIMIT_RETRIES + 1):
        try:
            with urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('CrossRef lookup malformed payload for DOI %r: root is %s', normalized_doi, type(data).__name__)
                return None
            message = data.get('message')
            if not isinstance(message, dict):
                logger.debug('CrossRef lookup malformed payload for DOI %r: missing message object', normalized_doi)
                return None
            titles = message.get('title')
            if not isinstance(titles, list):
                logger.debug('CrossRef lookup malformed payload for DOI %r: title is not a list', normalized_doi)
                return None
            first_title = titles[0] if titles else ''
            title = first_title.strip() if isinstance(first_title, str) else ''
            if title:
                return {'title': title}
            logger.debug('CrossRef: no title found for DOI %r', normalized_doi)
            return None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _DOI_RATE_LIMIT_RETRIES:
                delay = _parse_retry_after(exc)
                if delay is None:
                    delay = _DOI_BACKOFF_SECONDS[min(attempt, len(_DOI_BACKOFF_SECONDS) - 1)]
                logger.debug(
                    'CrossRef DOI lookup rate-limited for %r: HTTP 429, retrying in %.2fs',
                    normalized_doi,
                    delay,
                )
                sleep(delay)
                continue
            if exc.code == 404:
                logger.debug('CrossRef: no record found for DOI %r', normalized_doi)
            else:
                logger.warning('CrossRef lookup failed for DOI %r: HTTP %s', normalized_doi, exc.code)
            return None
        except OSError as exc:
            logger.warning('CrossRef lookup failed for DOI %r (network): %s', normalized_doi, exc)
            return None
        except Exception as exc:
            logger.warning('CrossRef lookup failed for DOI %r: %s', normalized_doi, exc)
            return None

    logger.warning('CrossRef lookup failed for DOI %r: all %d retries exhausted', normalized_doi, _DOI_RATE_LIMIT_RETRIES + 1)
    return None


def normalize_doi_token(token: str) -> str:
    """Normalize DOI-like input to a bare DOI token when possible."""
    value = token.strip().rstrip('.,;:')
    if not value:
        return ''

    lower = value.lower()
    for prefix in CLI_CONFIG.parsing.doi_prefixes:
        if lower.startswith(prefix):
            value = value[len(prefix):].strip()
            lower = value.lower()
            break

    if lower.startswith('doi.org/'):
        value = value[8:].strip()
        lower = value.lower()
    elif lower.startswith('dx.doi.org/'):
        value = value[11:].strip()
        lower = value.lower()
    elif lower.startswith('doi:'):
        value = value[4:].strip()

    return value.strip().rstrip('.,;:')


def _parse_retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Return Retry-After seconds from an HTTP error header when present and valid."""
    if exc.headers is None:
        return None
    retry_after = exc.headers.get('Retry-After', '').strip()
    if not retry_after:
        return None
    try:
        value = float(retry_after)
    except ValueError:
        return None
    return max(value, 0.0)

