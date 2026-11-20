"""Shared HTTP JSON fetch helpers, rate-limit retry policy, and tri-state lookup outcomes."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


class LookupStatus(Enum):
    """Outcome of an external lookup (definitive miss vs transient failure)."""

    FOUND = 'found'
    NOT_FOUND = 'not_found'
    ERROR = 'error'


@dataclass(frozen=True)
class LookupResult(Generic[T]):
    """Tri-state result distinguishing FOUND, definitive NOT_FOUND, and transient ERROR."""

    status: LookupStatus
    value: T | None = None

    @classmethod
    def found(cls, value: T) -> LookupResult[T]:
        return cls(status=LookupStatus.FOUND, value=value)

    @classmethod
    def not_found(cls) -> LookupResult[T]:
        return cls(status=LookupStatus.NOT_FOUND)

    @classmethod
    def error(cls) -> LookupResult[T]:
        return cls(status=LookupStatus.ERROR)


def parse_retry_after(exc: urllib.error.HTTPError, default: float | None = None) -> float | None:
    """Return numeric ``Retry-After`` seconds from an HTTP error header, or *default*."""
    headers = getattr(exc, 'headers', None)
    if headers is not None:
        raw = (headers.get('Retry-After') or '').strip()
        if raw:
            try:
                return max(float(raw), 0.0)
            except ValueError:
                pass
    return default


def fetch_json_lookup(
    url: str,
    *,
    timeout: float,
    label: str,
    not_found_codes: frozenset[int] = frozenset({404}),
    retry_codes: frozenset[int] = frozenset(),
    backoff_seconds: tuple[float, ...] = (),
) -> LookupResult[object]:
    """Fetch JSON from *url* with optional HTTP status retries and return a ``LookupResult``."""
    max_retries = len(backoff_seconds) if retry_codes else 0
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return LookupResult.found(json.loads(resp.read()))
        except urllib.error.HTTPError as exc:
            if exc.code in retry_codes and attempt < max_retries:
                delay = parse_retry_after(exc, default=backoff_seconds[attempt])
                assert delay is not None
                logger.debug('%s rate-limited: HTTP %s, retrying in %.2fs', label, exc.code, delay)
                time.sleep(delay)
                continue
            if exc.code in not_found_codes:
                logger.debug('%s returned HTTP %s (not found)', label, exc.code)
                return LookupResult.not_found()
            logger.warning('%s failed: HTTP %s', label, exc.code)
            return LookupResult.error()
        except OSError as exc:
            logger.warning('%s failed (network): %s', label, exc)
            return LookupResult.error()
        except Exception as exc:
            logger.warning('%s failed: %s', label, exc)
            return LookupResult.error()

    logger.warning('%s failed: all %d retries exhausted', label, max_retries + 1)
    return LookupResult.error()
