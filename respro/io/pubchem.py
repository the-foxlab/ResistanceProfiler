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

from respro.config.cli_settings import CLI_CONFIG

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
            if not isinstance(data, dict):
                logger.debug('PubChem CID lookup malformed payload for %r: root is %s', name, type(data).__name__)
                return None
            identifier_list = data.get('IdentifierList')
            if not isinstance(identifier_list, dict):
                logger.debug('PubChem CID lookup malformed payload for %r: missing IdentifierList object', name)
                return None
            cids = identifier_list.get('CID')
            if not isinstance(cids, list):
                logger.debug('PubChem CID lookup malformed payload for %r: CID is not a list', name)
                return None
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
            if not isinstance(data, dict):
                logger.debug(
                    'PubChem description lookup malformed payload for CID %s: root is %s',
                    cid,
                    type(data).__name__,
                )
                return ''
            information_list = data.get('InformationList')
            if not isinstance(information_list, dict):
                logger.debug(
                    'PubChem description lookup malformed payload for CID %s: missing InformationList object',
                    cid,
                )
                return ''
            information_entries = information_list.get('Information')
            if not isinstance(information_entries, list):
                logger.debug(
                    'PubChem description lookup malformed payload for CID %s: Information is not a list',
                    cid,
                )
                return ''
            for entry in information_entries:
                if not isinstance(entry, dict):
                    logger.debug(
                        'PubChem description lookup malformed payload for CID %s: entry is not an object',
                        cid,
                    )
                    return ''
                raw_title = entry.get('Title', '')
                title = raw_title.strip() if isinstance(raw_title, str) else ''
                if title and not first_title:
                    first_title = title
                raw_description = entry.get('Description', '')
                description = raw_description.strip() if isinstance(raw_description, str) else ''
                if description:
                    return description
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
            if not isinstance(data, dict):
                logger.debug('PubChem title lookup malformed payload for CID %s: root is %s', cid, type(data).__name__)
                return ''
            property_table = data.get('PropertyTable')
            if not isinstance(property_table, dict):
                logger.debug('PubChem title lookup malformed payload for CID %s: missing PropertyTable object', cid)
                return ''
            properties = property_table.get('Properties')
            if not isinstance(properties, list):
                logger.debug('PubChem title lookup malformed payload for CID %s: Properties is not a list', cid)
                return ''
            for entry in properties:
                if not isinstance(entry, dict):
                    logger.debug('PubChem title lookup malformed payload for CID %s: entry is not an object', cid)
                    return ''
                raw_title = entry.get('Title', '')
                title = raw_title.strip() if isinstance(raw_title, str) else ''
                if title:
                    return title
    except OSError as exc:
        logger.debug('PubChem title lookup failed for CID %s (network): %s', cid, exc)
    except Exception as exc:
        logger.debug('PubChem title lookup failed for CID %s: %s', cid, exc)

    return ''

