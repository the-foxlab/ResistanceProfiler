"""
Thin client for the PubChem PUG REST API.

Resolves a drug name to its PubChem CID and a short human-readable description.
Only stdlib modules are required and no API key is needed.

Public lookups return a tri-state :class:`LookupResult`: ``FOUND`` carries the
record, ``NOT_FOUND`` means the source definitively has no record (HTTP 404 or
an empty identifier list), and ``ERROR`` means a transient network or parsing
failure. Callers may cache ``NOT_FOUND`` permanently but should retry
``ERROR`` on a later run. Description/title fetches remain strictly
best-effort and never change the lookup status.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from respro.config.cli_settings import CLI_CONFIG
from respro.io.lookup import LookupResult, LookupStatus

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class PubChemRecord:
    """
    Minimal PubChem compound record for a single drug.

    ``cid`` is None when the drug could only be resolved as a PubChem
    *substance* (SID) — typical for monoclonal antibodies, which have no
    compound record. Substance hits carry a substance page URL and synonym
    text but no structure image.
    """

    cid: int | None
    url: str
    description: str
    structure_url: str = ''  # 2D structure image URL from PubChem


def lookup_drug(name: str, timeout: int = CLI_CONFIG.timeouts.pubchem) -> LookupResult[PubChemRecord]:
    """
    Look up a drug by name on PubChem.

    Tries the compound (CID) path first. Monoclonal antibodies and similar
    biologics have no compound record (the CID lookup returns HTTP 404), so
    on a CID miss the substance (SID) path is tried: the lowest SID for the
    name is used as the canonical record and its synonyms provide the
    description text (substance records have no description endpoint and no
    structure images).

    Returns ``NOT_FOUND`` only when both paths definitively have no record
    (compound 404 AND substance 404). Any transient network or parsing
    failure along the way yields ``ERROR`` so callers can retry later.

    :param name: drug name as it appears in the rules TSV
    :param timeout: HTTP request timeout in seconds (default 5)
    :return: LookupResult carrying a PubChemRecord when found
    """
    cid_result = _fetch_cid(name, timeout)
    if cid_result.status is not LookupStatus.NOT_FOUND:
        if cid_result.status is LookupStatus.FOUND and cid_result.value is not None:
            cid = cid_result.value
            description = _fetch_description(cid, timeout)
            return LookupResult.found(PubChemRecord(
                cid=cid,
                url=CLI_CONFIG.urls.pubchem_compound_page.format(cid=cid),
                description=description,
                structure_url=CLI_CONFIG.urls.pubchem_structure_png.format(cid=cid),
            ))
        return LookupResult.error()

    return _lookup_substance(name, timeout)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _lookup_substance(name: str, timeout: int) -> LookupResult[PubChemRecord]:
    """
    Resolve a drug to its canonical PubChem substance (SID) record.

    Used as the fallback when the compound (CID) path definitively has no
    record. The lowest SID is picked as canonical so the choice is
    deterministic across runs. The description is the first synonym that
    matches the query name case-insensitively, or the first synonym when none
    matches exactly. A SID is definitive data once resolved, so a synonym
    fetch that fails only costs the description text — the record is still
    returned (with an empty description) rather than an error.

    :param name: drug name
    :param timeout: HTTP request timeout in seconds
    :return: LookupResult carrying a PubChemRecord with cid=None, or
        NOT_FOUND when no substance exists, or ERROR on a transient failure
    """
    sids_result = _fetch_substance_sids(name, timeout)
    if sids_result.status is not LookupStatus.FOUND or not sids_result.value:
        return LookupResult.not_found() if sids_result.status is LookupStatus.NOT_FOUND else LookupResult.error()

    sid = min(sids_result.value)
    synonyms_result = _fetch_substance_synonyms(sid, timeout)
    synonyms = synonyms_result.value if synonyms_result.status is LookupStatus.FOUND else []

    description = ''
    if synonyms:
        lowered = name.strip().lower()
        for synonym in synonyms:
            if synonym.strip().lower() == lowered:
                description = synonym
                break
        if not description:
            description = synonyms[0]

    return LookupResult.found(PubChemRecord(
        cid=None,
        url=CLI_CONFIG.urls.pubchem_substance_page.format(sid=sid),
        description=description,
        structure_url='',
    ))


def _fetch_substance_sids(name: str, timeout: int) -> LookupResult[list[int]]:
    """
    Resolve a drug name to its PubChem substance IDs (SIDs).

    :param name: drug name
    :param timeout: HTTP request timeout in seconds
    :return: LookupResult carrying the list of integer SIDs; NOT_FOUND when
        the substance endpoint answers 404 or returns no SIDs, ERROR on any
        transient network or parsing failure
    """
    encoded = urllib.parse.quote(name)
    url = CLI_CONFIG.urls.pubchem_substance_sids.format(name=encoded)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('PubChem SID lookup malformed payload for %r: root is %s', name, type(data).__name__)
                return LookupResult.error()
            identifier_list = data.get('IdentifierList')
            if not isinstance(identifier_list, dict):
                logger.debug('PubChem SID lookup malformed payload for %r: missing IdentifierList object', name)
                return LookupResult.error()
            sids = identifier_list.get('SID')
            if not isinstance(sids, list):
                logger.debug('PubChem SID lookup malformed payload for %r: SID is not a list', name)
                return LookupResult.error()
            return LookupResult.found([int(sid) for sid in sids if isinstance(sid, (int, str)) and str(sid).isdigit()])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('PubChem: no substance found for %r', name)
            return LookupResult.not_found()
        logger.warning('PubChem SID lookup failed for %r: HTTP %s', name, exc.code)
        return LookupResult.error()
    except OSError as exc:
        logger.warning('PubChem SID lookup failed for %r (network): %s', name, exc)
        return LookupResult.error()
    except Exception as exc:
        logger.warning('PubChem SID lookup failed for %r: %s', name, exc)
        return LookupResult.error()

def _fetch_substance_synonyms(sid: int, timeout: int) -> LookupResult[list[str]]:
    """
    Fetch the synonym list of a PubChem substance record.

    The substance description endpoint is discontinued (HTTP 501) and the
    substance property/Title endpoints are invalid operations (HTTP 400), so
    synonyms are the only human-readable text available for a substance.

    :param sid: PubChem substance ID
    :param timeout: HTTP request timeout in seconds
    :return: LookupResult carrying the list of synonym strings; NOT_FOUND on
        HTTP 404, ERROR on any transient network or parsing failure
    """
    url = CLI_CONFIG.urls.pubchem_substance_record.format(sid=sid)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('PubChem substance record malformed payload for SID %s: root is %s', sid, type(data).__name__)
                return LookupResult.error()
            substances = data.get('PC_Substances')
            if not isinstance(substances, list) or not substances:
                logger.debug('PubChem substance record malformed payload for SID %s: missing PC_Substances list', sid)
                return LookupResult.error()
            substance = substances[0]
            if not isinstance(substance, dict):
                logger.debug('PubChem substance record malformed payload for SID %s: entry is not an object', sid)
                return LookupResult.error()
            synonyms = substance.get('synonyms')
            if not isinstance(synonyms, list):
                logger.debug('PubChem substance record malformed payload for SID %s: synonyms is not a list', sid)
                return LookupResult.error()
            return LookupResult.found(
                [synonym.strip() for synonym in synonyms if isinstance(synonym, str) and synonym.strip()]
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('PubChem: no substance record found for SID %s', sid)
            return LookupResult.not_found()
        logger.warning('PubChem substance record lookup failed for SID %s: HTTP %s', sid, exc.code)
        return LookupResult.error()
    except OSError as exc:
        logger.warning('PubChem substance record lookup failed for SID %s (network): %s', sid, exc)
        return LookupResult.error()
    except Exception as exc:
        logger.warning('PubChem substance record lookup failed for SID %s: %s', sid, exc)
        return LookupResult.error()

def _fetch_cid(name: str, timeout: int) -> LookupResult[int]:
    """
    Resolve a drug name to its primary PubChem CID.

    :param name: drug name
    :param timeout: HTTP request timeout in seconds
    :return: LookupResult carrying the integer CID; NOT_FOUND when the
        compound endpoint answers 404 or returns no CIDs, ERROR on any
        transient network or parsing failure
    """
    encoded = urllib.parse.quote(name)
    url = CLI_CONFIG.urls.pubchem_cid_lookup.format(name=encoded)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('PubChem CID lookup malformed payload for %r: root is %s', name, type(data).__name__)
                return LookupResult.error()
            identifier_list = data.get('IdentifierList')
            if not isinstance(identifier_list, dict):
                logger.debug('PubChem CID lookup malformed payload for %r: missing IdentifierList object', name)
                return LookupResult.error()
            cids = identifier_list.get('CID')
            if not isinstance(cids, list):
                logger.debug('PubChem CID lookup malformed payload for %r: CID is not a list', name)
                return LookupResult.error()
            return LookupResult.found(int(cids[0])) if cids else LookupResult.not_found()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('PubChem: no compound found for %r', name)
            return LookupResult.not_found()
        logger.warning('PubChem CID lookup failed for %r: HTTP %s', name, exc.code)
        return LookupResult.error()
    except OSError as exc:
        # Covers socket timeouts, connection refused, no network, etc.
        logger.warning('PubChem CID lookup failed for %r (network): %s', name, exc)
        return LookupResult.error()
    except Exception as exc:
        logger.warning('PubChem CID lookup failed for %r: %s', name, exc)
        return LookupResult.error()


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
        logger.warning('PubChem description lookup failed for CID %s (network): %s', cid, exc)
    except Exception as exc:
        logger.warning('PubChem description lookup failed for CID %s: %s', cid, exc)

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
        logger.warning('PubChem title lookup failed for CID %s (network): %s', cid, exc)
    except Exception as exc:
        logger.warning('PubChem title lookup failed for CID %s: %s', cid, exc)

    return ''

