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
import urllib.error
import urllib.parse
import urllib.request

from respro.config.settings import CLI_CONFIG

logger = logging.getLogger(__name__)


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
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
        result_data = data.get('result', {}).get(pmid, {})
        title = result_data.get('title', '').strip()
        doi = ''
        for entry in result_data.get('articleids', []):
            if entry.get('idtype') == 'doi':
                doi = entry.get('value', '').strip()
                break
        return {'title': title, 'doi': doi}
    except urllib.error.HTTPError as exc:
        logger.debug('NCBI PMID lookup failed for %r: HTTP %s', pmid, exc.code)
        return None
    except OSError as exc:
        logger.debug('NCBI PMID lookup failed for %r (network): %s', pmid, exc)
        return None
    except Exception as exc:
        logger.debug('NCBI PMID lookup failed for %r: %s', pmid, exc)
        return None


def fetch_publication_metadata(doi: str, timeout: int = CLI_CONFIG.timeouts.crossref) -> dict | None:
    """
    Fetch a publication title from the CrossRef API.

    Used as a fallback for DOI-only entries that have no PubMed ID.

    :param doi: bare DOI string (e.g. ``10.1234/xyz``)
    :param timeout: HTTP request timeout in seconds
    :return: dict with key ``'title'`` or None if unavailable
    """
    encoded = urllib.parse.quote(doi, safe='')
    url = CLI_CONFIG.urls.crossref_works.format(doi=encoded)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
        titles = data.get('message', {}).get('title', [])
        title = titles[0].strip() if titles else ''
        if title:
            return {'title': title}
        logger.debug('CrossRef: no title found for DOI %r', doi)
        return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('CrossRef: no record found for DOI %r', doi)
        else:
            logger.debug('CrossRef lookup failed for DOI %r: HTTP %s', doi, exc.code)
        return None
    except OSError as exc:
        logger.debug('CrossRef lookup failed for DOI %r (network): %s', doi, exc)
        return None
    except Exception as exc:
        logger.debug('CrossRef lookup failed for DOI %r: %s', doi, exc)
        return None

