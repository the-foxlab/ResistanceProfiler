"""Thin client for the PubChem PUG REST API (compound CID and substance SID drug lookups)."""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

from respro.config.cli_settings import CLI_CONFIG
from respro.io.http import LookupResult, LookupStatus, fetch_json_lookup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PubChemRecord:
    """Minimal PubChem compound or substance metadata record for one drug."""

    cid: int | None  # None for substance-only (SID) records such as monoclonal antibodies
    url: str
    description: str
    structure_url: str = ''


def lookup_drug(name: str, timeout: int = CLI_CONFIG.timeouts.pubchem) -> LookupResult[PubChemRecord]:
    """Resolve *name* via PubChem compound (CID) first, falling back to substance (SID) on 404."""
    cid_result = _fetch_cid(name, timeout)
    if cid_result.status is LookupStatus.FOUND and cid_result.value is not None:
        cid = cid_result.value
        return LookupResult.found(PubChemRecord(
            cid=cid,
            url=CLI_CONFIG.urls.pubchem_compound_page.format(cid=cid),
            description=_fetch_description(cid, timeout),
            structure_url=CLI_CONFIG.urls.pubchem_structure_png.format(cid=cid),
        ))
    if cid_result.status is LookupStatus.ERROR:
        return LookupResult.error()
    return _lookup_substance(name, timeout)


def _lookup_substance(name: str, timeout: int) -> LookupResult[PubChemRecord]:
    """Resolve *name* to its lowest PubChem SID and pick a representative synonym."""
    sids_result = _fetch_substance_sids(name, timeout)
    if sids_result.status is not LookupStatus.FOUND or not sids_result.value:
        return LookupResult.not_found() if sids_result.status is LookupStatus.NOT_FOUND else LookupResult.error()

    sid = min(sids_result.value)
    synonyms_result = _fetch_substance_synonyms(sid, timeout)
    synonyms = synonyms_result.value if synonyms_result.status is LookupStatus.FOUND and synonyms_result.value else []

    lowered = name.strip().lower()
    description = next((s for s in synonyms if s.strip().lower() == lowered), '') or (
        synonyms[0] if synonyms else ''
    )
    return LookupResult.found(PubChemRecord(
        cid=None,
        url=CLI_CONFIG.urls.pubchem_substance_page.format(sid=sid),
        description=description,
        structure_url='',
    ))


def _fetch_identifier_list(url: str, key: str, label: str, timeout: int) -> LookupResult[list]:
    """Fetch a PubChem ``IdentifierList.<key>`` list payload."""
    res = fetch_json_lookup(url, timeout=timeout, label=label)
    if res.status is not LookupStatus.FOUND:
        return LookupResult(status=res.status)
    data = res.value
    if not isinstance(data, dict) or not isinstance(data.get('IdentifierList'), dict):
        logger.debug('%s malformed payload: missing IdentifierList object', label)
        return LookupResult.error()
    items = data['IdentifierList'].get(key)
    if not isinstance(items, list):
        logger.debug('%s malformed payload: %s is not a list', label, key)
        return LookupResult.error()
    return LookupResult.found(items)


def _fetch_cid(name: str, timeout: int) -> LookupResult[int]:
    """Resolve a drug name to its primary PubChem CID."""
    url = CLI_CONFIG.urls.pubchem_cid_lookup.format(name=urllib.parse.quote(name, safe=''))
    res = _fetch_identifier_list(url, 'CID', f'PubChem CID lookup for {name!r}', timeout)
    if res.status is not LookupStatus.FOUND or res.value is None:
        return LookupResult(status=res.status)
    if not res.value:
        return LookupResult.not_found()
    first = res.value[0]
    if isinstance(first, bool) or not isinstance(first, (int, str)) or not str(first).isdigit():
        return LookupResult.error()
    return LookupResult.found(int(first))


def _fetch_substance_sids(name: str, timeout: int) -> LookupResult[list[int]]:
    """Resolve a drug name to its PubChem substance IDs (SIDs)."""
    url = CLI_CONFIG.urls.pubchem_substance_sids.format(name=urllib.parse.quote(name, safe=''))
    res = _fetch_identifier_list(url, 'SID', f'PubChem SID lookup for {name!r}', timeout)
    if res.status is LookupStatus.NOT_FOUND:
        logger.debug('PubChem: no substance found for %r', name)
        return LookupResult.not_found()
    if res.status is not LookupStatus.FOUND or res.value is None:
        return LookupResult(status=res.status)
    sids = [
        int(s) for s in res.value
        if not isinstance(s, bool) and isinstance(s, (int, str)) and str(s).isdigit()
    ]
    return LookupResult.found(sids) if sids else LookupResult.not_found()


def _fetch_substance_synonyms(sid: int, timeout: int) -> LookupResult[list[str]]:
    """Fetch the synonym list of a PubChem substance record."""
    url = CLI_CONFIG.urls.pubchem_substance_record.format(sid=sid)
    res = fetch_json_lookup(url, timeout=timeout, label=f'PubChem substance record lookup for SID {sid}')
    if res.status is not LookupStatus.FOUND:
        return LookupResult(status=res.status)
    data = res.value
    if not isinstance(data, dict):
        return LookupResult.error()
    substances = data.get('PC_Substances')
    if not isinstance(substances, list) or not substances or not isinstance(substances[0], dict):
        return LookupResult.error()
    synonyms = substances[0].get('synonyms')
    if not isinstance(synonyms, list):
        return LookupResult.error()
    return LookupResult.found([s.strip() for s in synonyms if isinstance(s, str) and s.strip()])


def _fetch_description(cid: int, timeout: int) -> str:
    """Fetch a best-effort compound description (falling back to title) for *cid*."""
    desc_url = CLI_CONFIG.urls.pubchem_description.format(cid=cid)
    first_title = ''
    res = fetch_json_lookup(desc_url, timeout=timeout, label=f'PubChem description lookup for CID {cid}')
    if res.status is LookupStatus.FOUND and isinstance(res.value, dict):
        info_list = res.value.get('InformationList')
        entries = info_list.get('Information') if isinstance(info_list, dict) else None
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    return ''
                raw_title = entry.get('Title', '')
                if isinstance(raw_title, str) and raw_title.strip() and not first_title:
                    first_title = raw_title.strip()
                raw_desc = entry.get('Description', '')
                if isinstance(raw_desc, str) and raw_desc.strip():
                    return raw_desc.strip()

    if first_title:
        return first_title

    title_url = CLI_CONFIG.urls.pubchem_title.format(cid=cid)
    title_res = fetch_json_lookup(title_url, timeout=timeout, label=f'PubChem title lookup for CID {cid}')
    if title_res.status is LookupStatus.FOUND and isinstance(title_res.value, dict):
        prop_table = title_res.value.get('PropertyTable')
        props = prop_table.get('Properties') if isinstance(prop_table, dict) else None
        if isinstance(props, list):
            for entry in props:
                if not isinstance(entry, dict):
                    return ''
                raw_title = entry.get('Title', '')
                if isinstance(raw_title, str) and raw_title.strip():
                    return raw_title.strip()
    return ''
