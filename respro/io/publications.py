"""
Thin clients for NCBI E-utilities and CrossRef APIs.

Resolves PubMed IDs to DOIs and fetches publication titles.
All public functions return None on any failure — callers treat this as best-effort.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def resolve_pubmed_to_doi(pmid: str, timeout: int = 5) -> str | None:
    """
    Resolve a PubMed ID to a DOI via the NCBI E-utilities esummary endpoint.

    :param pmid: numeric PubMed ID string (digits only)
    :param timeout: HTTP request timeout in seconds
    :return: bare DOI string (e.g. ``10.1234/xyz``) or None if unresolvable
    """
    url = (
        f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
        f'?db=pubmed&id={urllib.parse.quote(pmid)}&retmode=json'
    )
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
        article_ids = (
            data.get('result', {}).get(pmid, {}).get('articleids', [])
        )
        for entry in article_ids:
            if entry.get('idtype') == 'doi':
                doi = entry.get('value', '').strip()
                if doi:
                    return doi
        logger.debug('NCBI: no DOI found for PMID %s', pmid)
        return None
    except urllib.error.HTTPError as exc:
        logger.debug('NCBI PMID lookup failed for %r: HTTP %s', pmid, exc.code)
        return None
    except OSError as exc:
        logger.debug('NCBI PMID lookup failed for %r (network): %s', pmid, exc)
        return None
    except Exception as exc:
        logger.debug('NCBI PMID lookup failed for %r: %s', pmid, exc)
        return None


def fetch_publication_metadata(doi: str, timeout: int = 5) -> dict | None:
    """
    Fetch a publication title from the CrossRef API.

    :param doi: bare DOI string (e.g. ``10.1234/xyz``)
    :param timeout: HTTP request timeout in seconds
    :return: dict with key ``'title'`` or None if unavailable
    """
    encoded = urllib.parse.quote(doi, safe='')
    url = f'https://api.crossref.org/works/{encoded}'
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

