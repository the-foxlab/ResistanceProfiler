"""Rank-based phenotype severity vocabulary.

A single built-in mapping from classification labels to integer severity ranks.
Ranks 1–5 denote increasing severity; sentinels ``0`` (unknown / not analysed)
and ``-1`` (contradictory / conflicting) sit outside the severity ladder. Labels
are stored and compared lowercased + whitespace-stripped; the canonical fallback
label for each rank is the first label in its row of the vocabulary table.

This module is the single source of truth shared by database import, the
interpretation algorithms, and report rendering. Every consumer infers a rank
from a stored label via :func:`label_to_rank` rather than carrying rank as a
separate stored value.
"""

from __future__ import annotations

RANK_UNKNOWN = 0
RANK_CONTRADICTORY = -1

# Canonical fallback label for each rank (first label in each vocabulary row).
# Lowercased to match the storage convention.
_FALLBACK_LABELS: dict[int, str] = {
    1: 'susceptible',
    2: 'potential low-level resistance',
    3: 'low-level resistance',
    4: 'intermediate',
    5: 'resistant',
    RANK_UNKNOWN: 'unknown',
    RANK_CONTRADICTORY: 'contradictory',
}

# Rank → display colour. 1 green, 2 pleasant yellow, 3 slight orange, 4 orange,
# 5 red, 0 grey, -1 dark.
RANK_COLOURS: dict[int, str] = {
    1: '#27ae60',
    2: '#f1c40f',
    3: '#e67e22',
    4: '#e67e22',
    5: '#e74c3c',
    RANK_UNKNOWN: '#bdc3c7',
    RANK_CONTRADICTORY: '#334142',
}

# Label → rank. All keys lowercased. Multiple labels may map to the same rank.
_LABEL_TO_RANK: dict[str, int] = {
    # Rank 1 — susceptible
    'susceptible': 1,
    'sensitive': 1,
    'normal inhibition': 1,
    'ni': 1,
    'normal': 1,
    # Rank 2 — potential low-level resistance
    'potential low-level resistance': 2,
    'possibly resistant': 2,
    'suspected reduced': 2,
    # Rank 3 — low-level resistance
    'low-level resistance': 3,
    'reduced susceptibility': 3,
    'limited susceptibility': 3,
    # Rank 4 — intermediate
    'intermediate': 4,
    'intermediate resistance': 4,
    'reduced inhibition': 4,
    'ri': 4,
    # Rank 5 — resistant
    'resistant': 5,
    'high-level resistance': 5,
    'highly reduced inhibition': 5,
    'hri': 5,
    # Sentinel — unknown / not analysed
    'unknown': RANK_UNKNOWN,
    'not analysed': RANK_UNKNOWN,
    'none': RANK_UNKNOWN,
    '': RANK_UNKNOWN,
    # Sentinel — contradictory / conflicting
    'contradictory': RANK_CONTRADICTORY,
    'conflicting': RANK_CONTRADICTORY,
}


class _VocabularyView:
    """Read-only view over the rank vocabulary for external inspection/tests."""

    label_to_rank: dict[str, int] = _LABEL_TO_RANK
    fallback_labels: dict[int, str] = _FALLBACK_LABELS
    colours: dict[int, str] = RANK_COLOURS


RANK_VOCABULARY = _VocabularyView()


def _canonicalize(raw: str) -> str:
    """Lowercase and strip whitespace from a label."""
    return raw.strip().lower()


def label_to_rank(label: str) -> int | None:
    """Return the severity rank for *label*, or ``None`` if unrecognized.

    The lookup is case-insensitive and whitespace-tolerant. The empty string
    maps to :data:`RANK_UNKNOWN`.
    """
    key = _canonicalize(label)
    return _LABEL_TO_RANK.get(key)


def rank_to_label(rank: int) -> str:
    """Return the canonical fallback label for *rank*.

    :raises ValueError: if *rank* is not a known rank or sentinel.
    """
    if rank not in _FALLBACK_LABELS:
        raise ValueError(f'Unknown phenotype rank: {rank!r}.')
    return _FALLBACK_LABELS[rank]


def rank_to_colour(rank: int) -> str:
    """Return the display colour for *rank*.

    :raises ValueError: if *rank* is not a known rank or sentinel.
    """
    if rank not in RANK_COLOURS:
        raise ValueError(f'Unknown phenotype rank: {rank!r}.')
    return RANK_COLOURS[rank]


def allowed_ranks_for_labels(labels: set[str]) -> set[int]:
    """Return the set of ranks inferred from *labels*; unknown labels are skipped."""
    ranks: set[int] = set()
    for label in labels:
        rank = label_to_rank(label)
        if rank is not None:
            ranks.add(rank)
    return ranks
