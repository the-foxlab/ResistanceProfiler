"""
Shared tri-state lookup outcome for the external data clients.

Clients distinguish a *definitive* negative (the source answered and has no
record — typically HTTP 404) from a *transient* failure (network or parsing
problems). Callers may cache definitive negatives permanently but must retry
transient failures on the next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar('T')


class LookupStatus(Enum):
    """Outcome of an external lookup."""

    FOUND = 'found'
    NOT_FOUND = 'not_found'
    ERROR = 'error'


@dataclass(frozen=True)
class LookupResult(Generic[T]):
    """
    Tri-state result of one external lookup.

    ``value`` carries the payload when ``status`` is ``FOUND`` and is None
    otherwise.
    """

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
