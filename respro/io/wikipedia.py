"""
Thin client for the Wikipedia REST summary API.

Provides a one-paragraph description for drug names that have no PubChem
record at all (neither compound nor substance) — typical for monoclonal
antibodies. Only stdlib modules are required and no API key is needed.

Wikipedia text is licensed CC BY-SA; attribution is documented in the project
docs rather than embedded in the stored text.

Public lookups return a tri-state :class:`LookupResult`: ``FOUND`` carries
the summary text, ``NOT_FOUND`` means the source definitively has no summary
(HTTP 404 or no extract text), and ``ERROR`` means a transient network or
parsing failure. Callers may cache ``NOT_FOUND`` permanently but should retry
``ERROR`` on a later run.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from respro.config.cli_settings import CLI_CONFIG
from respro.io.lookup import LookupResult

logger = logging.getLogger(__name__)


def lookup_summary(name: str, timeout: int | None = None) -> LookupResult[str]:
    """
    Look up a one-paragraph summary for a page title on Wikipedia.

    Calls the unauthenticated Wikipedia REST summary endpoint and returns the
    ``extract`` field. Never raises.

    :param name: page title (typically a drug name as it appears in the rules TSV)
    :param timeout: HTTP request timeout in seconds (defaults to the PubChem
        timeout from the bundled configuration)
    :return: LookupResult carrying the summary extract string when found
    """
    if timeout is None:
        timeout = CLI_CONFIG.timeouts.pubchem

    encoded = urllib.parse.quote(name)
    url = CLI_CONFIG.urls.wikipedia_summary.format(name=encoded)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            if not isinstance(data, dict):
                logger.debug('Wikipedia summary malformed payload for %r: root is %s', name, type(data).__name__)
                return LookupResult.error()
            extract = data.get('extract')
            if not isinstance(extract, str) or not extract.strip():
                logger.debug('Wikipedia summary has no extract text for %r', name)
                return LookupResult.not_found()
            return LookupResult.found(extract.strip())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug('Wikipedia: no page found for %r', name)
            return LookupResult.not_found()
        logger.warning('Wikipedia summary lookup failed for %r: HTTP %s', name, exc.code)
        return LookupResult.error()
    except OSError as exc:
        # Covers socket timeouts, connection refused, no network, etc.
        logger.warning('Wikipedia summary lookup failed for %r (network): %s', name, exc)
        return LookupResult.error()
    except Exception as exc:
        logger.warning('Wikipedia summary lookup failed for %r: %s', name, exc)
        return LookupResult.error()
