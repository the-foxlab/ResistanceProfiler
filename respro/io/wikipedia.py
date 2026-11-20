"""Thin client for the Wikipedia REST summary API (fallback drug descriptions)."""

from __future__ import annotations

import logging
import urllib.parse

from respro.config.cli_settings import CLI_CONFIG
from respro.io.http import LookupResult, LookupStatus, fetch_json_lookup

logger = logging.getLogger(__name__)


def lookup_summary(name: str, timeout: int | None = None) -> LookupResult[str]:
    """Look up a one-paragraph Wikipedia summary for *name*; never raises."""
    if timeout is None:
        timeout = CLI_CONFIG.timeouts.pubchem

    url = CLI_CONFIG.urls.wikipedia_summary.format(name=urllib.parse.quote(name))
    result = fetch_json_lookup(url, timeout=timeout, label=f'Wikipedia summary lookup for {name!r}')
    if result.status is LookupStatus.NOT_FOUND:
        logger.debug('Wikipedia: no page found for %r', name)
        return LookupResult.not_found()
    if result.status is not LookupStatus.FOUND:
        return LookupResult(status=result.status)

    data = result.value
    if not isinstance(data, dict):
        logger.debug('Wikipedia summary malformed payload for %r: root is %s', name, type(data).__name__)
        return LookupResult.error()

    extract = data.get('extract')
    if not isinstance(extract, str) or not extract.strip():
        logger.debug('Wikipedia summary has no extract text for %r', name)
        return LookupResult.not_found()
    return LookupResult.found(extract.strip())
