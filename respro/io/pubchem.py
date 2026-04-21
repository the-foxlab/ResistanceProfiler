"""
Thin client for the PubChem PUG REST API.

Resolves a drug name to its PubChem CID and a short human-readable description.
Only stdlib modules are required and no API key is needed.

All public functions return None / empty on any failure so that callers can
treat PubChem data lookup as strictly best-effort.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from respro.config.settings import CLI_CONFIG

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class PubChemRecord:
    """
    Minimal PubChem compound record for a single drug.
    """

    cid: int
    url: str
    description: str
    structure_url: str = ''  # 2D structure image URL from PubChem


def lookup_drug(name: str, timeout: int = CLI_CONFIG.timeouts.pubchem) -> PubChemRecord | None:
    """
    Look up a drug by name on PubChem and return a minimal record.

    Returns None when the name cannot be resolved or any network or parsing
    failure occurs, so callers do not need to handle exceptions.

    :param name: drug name as it appears in the rules TSV
    :param timeout: HTTP request timeout in seconds (default 5)
    :return: PubChemRecord if found, otherwise None
    """
    cid = _fetch_cid(name, timeout)
    if cid is None:
        return None

    description = _fetch_description(cid, timeout)
    return PubChemRecord(
        cid=cid,
        url=CLI_CONFIG.urls.pubchem_compound_page.format(cid=cid),
        description=description,
        structure_url=CLI_CONFIG.urls.pubchem_structure_png.format(cid=cid),
    )


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _fetch_cid(name: str, timeout: int) -> int | None:
    """
    Resolve a drug name to its primary PubChem CID.

    :param name: drug name
    :param timeout: HTTP request timeout in seconds
    :return: integer CID if found, otherwise None
    """
    encoded = urllib.parse.quote(name)
    url = CLI_CONFIG.urls.pubchem_cid_lookup.format(name=encoded)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            cids = data.get('IdentifierList', {}).get('CID', [])
            return int(cids[0]) if cids else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('PubChem: no compound found for %r', name)
        else:
            logger.debug('PubChem CID lookup failed for %r: HTTP %s', name, exc.code)
        return None
    except OSError as exc:
        # Covers socket timeouts, connection refused, no network, etc.
        logger.debug('PubChem CID lookup failed for %r (network): %s', name, exc)
        return None
    except Exception as exc:
        logger.debug('PubChem CID lookup failed for %r: %s', name, exc)
        return None


def _fetch_description(cid: int, timeout: int) -> str:
    """
    Fetch a human-readable description for a PubChem compound.

    Prefers the Description endpoint, but falls back to a title if no
    description text is available.

    :param cid: PubChem compound ID
    :param timeout: HTTP request timeout in seconds
    :return: description string, empty string if unavailable
    """
    description_url = CLI_CONFIG.urls.pubchem_description.format(cid=cid)
    first_title = ''
    try:
        with urllib.request.urlopen(description_url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            for entry in data.get('InformationList', {}).get('Information', []):
                title = entry.get('Title', '').strip()
                if title and not first_title:
                    first_title = title
                desc = entry.get('Description', '').strip()
                if desc:
                    return desc
    except OSError as exc:
        logger.debug('PubChem description lookup failed for CID %s (network): %s', cid, exc)
    except Exception as exc:
        logger.debug('PubChem description lookup failed for CID %s: %s', cid, exc)

    if first_title:
        return first_title

    title_url = CLI_CONFIG.urls.pubchem_title.format(cid=cid)
    try:
        with urllib.request.urlopen(title_url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            for entry in data.get('PropertyTable', {}).get('Properties', []):
                title = entry.get('Title', '').strip()
                if title:
                    return title
    except OSError as exc:
        logger.debug('PubChem title lookup failed for CID %s (network): %s', cid, exc)
    except Exception as exc:
        logger.debug('PubChem title lookup failed for CID %s: %s', cid, exc)

    return ''

