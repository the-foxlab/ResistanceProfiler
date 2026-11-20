"""Rank-based phenotype severity vocabulary and strict label normalizer."""

from __future__ import annotations

RANK_UNKNOWN = 0
RANK_CONTRADICTORY = -1

_FALLBACK_LABELS: dict[int, str] = {
    1: 'susceptible',
    2: 'potential low-level resistance',
    3: 'low-level resistance',
    4: 'intermediate',
    5: 'resistant',
    RANK_UNKNOWN: 'unknown',
    RANK_CONTRADICTORY: 'contradictory',
}

# Monotonic severity colour ladder: 1 green -> 5 red; 0 grey, -1 slate.
RANK_COLOURS: dict[int, str] = {
    1: '#27ae60',
    2: '#f1c40f',
    3: '#f39c12',
    4: '#e67e22',
    5: '#e74c3c',
    RANK_UNKNOWN: '#bdc3c7',
    RANK_CONTRADICTORY: '#64748b',
}

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
    """Read-only view over the phenotype rank vocabulary."""

    label_to_rank: dict[str, int] = _LABEL_TO_RANK
    fallback_labels: dict[int, str] = _FALLBACK_LABELS
    colours: dict[int, str] = RANK_COLOURS


RANK_VOCABULARY = _VocabularyView()


def label_to_rank(label: str) -> int | None:
    """Return the integer severity rank for *label*, or ``None`` if unrecognized."""
    return _LABEL_TO_RANK.get(label.strip().lower())


def rank_to_label(rank: int) -> str:
    """Return the canonical fallback label for *rank*."""
    if rank not in _FALLBACK_LABELS:
        raise ValueError(f'Unknown phenotype rank: {rank!r}.')
    return _FALLBACK_LABELS[rank]


def rank_to_colour(rank: int) -> str:
    """Return the display hex colour for *rank*."""
    if rank not in RANK_COLOURS:
        raise ValueError(f'Unknown phenotype rank: {rank!r}.')
    return RANK_COLOURS[rank]


def most_frequent_label(counts: dict[str, int]) -> str | None:
    """Return the most frequent label from *counts* (alphabetical tie-break), or ``None`` if empty."""
    if not counts:
        return None
    return min(counts, key=lambda label: (-counts[label], label))


def allowed_ranks_for_labels(labels: set[str]) -> set[int]:
    """Return the set of severity ranks represented in *labels*, ignoring unknown strings."""
    return {rank for label in labels if (rank := label_to_rank(label)) is not None}


def normalize_phenotype_label(raw: str) -> str:
    """Strictly normalize a raw phenotype label or bare severity rank ('1'-'5') to storage form."""
    key = raw.strip().lower()
    if not key:
        return ''
    if key in _LABEL_TO_RANK:
        if _LABEL_TO_RANK[key] == RANK_UNKNOWN and key != 'unknown':
            return 'unknown'
        return key
    if key.isdigit() and 1 <= (rank := int(key)) <= 5:
        return rank_to_label(rank)
    raise ValueError(
        f'Unknown phenotype label {raw!r}. '
        f'Allowed labels: {sorted(_LABEL_TO_RANK)} '
        f'or a bare rank 1–5.'
    )
