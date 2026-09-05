"""
TSV row normalization for resistance rule imports — phenotypes, IC50, score, and comments.
"""

from __future__ import annotations

import re

from respro.db.phenotype_ranks import _LABEL_TO_RANK, RANK_UNKNOWN, rank_to_label

_CONTRADICTORY_COMMENT = 'Publications have contradictory phenotype associations.'


def _append_contradictory_comment(
    comment: str,
    *,
    phenotype: str,
    clinical_phenotype: str,
) -> str:
    """Append a standard explanatory comment when a row is labeled contradictory."""
    if phenotype != 'contradictory' and clinical_phenotype != 'contradictory':
        return comment

    normalized_comment = comment.strip()
    if _CONTRADICTORY_COMMENT.lower() in normalized_comment.lower():
        return normalized_comment
    if not normalized_comment:
        return _CONTRADICTORY_COMMENT
    if normalized_comment.endswith(('.', '!', '?')):
        return f'{normalized_comment} {_CONTRADICTORY_COMMENT}'
    return f'{normalized_comment}. {_CONTRADICTORY_COMMENT}'


def _get_value(row: dict[str, str], *keys: str) -> str:
    """Return the first non-empty value for *keys* from a TSV row."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            value = value.strip()
            if value:
                return value
    return ''


def _parse_ic50_value(raw: str) -> float | None:
    """Parse a numeric IC50 fold-change from a raw TSV cell value."""
    value = raw.strip()
    if not value or value.lower() == 'none':
        return None

    match = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', value)
    if match is None:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_single_ic50(raw: str, *, errors: list[str], context: str) -> str:
    """Parse one IC50 cell value and return a canonical numeric string or empty string."""
    value = raw.strip()
    if not value or value.lower() == 'none':
        return ''
    parsed = _parse_ic50_value(value)
    if parsed is None:
        errors.append(f'{context}: invalid ic50 value {value!r}')
        return ''
    return f'{parsed:g}'


def _normalize_ic50_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical IC50 text or empty string; reads ic50/ic_50 columns only."""
    return _parse_single_ic50(_get_value(row, 'ic50', 'ic_50'), errors=errors, context=context)


def _normalize_fold_ic50_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical fold-IC50 text or empty string; reads fold_ic50/fold_ic_50 columns only."""
    return _parse_single_ic50(_get_value(row, 'fold_ic50', 'fold_ic_50'), errors=errors, context=context)


def _normalize_score_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical score text or empty string; reads the score column."""
    raw = _get_value(row, 'score')
    if not raw:
        return ''
    value = raw.strip()
    if not value or value.lower() == 'none':
        return ''
    try:
        return f'{float(value):g}'
    except ValueError:
        errors.append(f'{context}: invalid score value {value!r}')
        return ''


def normalize_phenotype_label(raw: str) -> str:
    """Strict phenotype label normalizer for the rank-based system.

    Lowercases and whitespace-strips *raw*, then resolves it against the rank
    vocabulary. Empty input returns ``''`` (rank 0 / unknown). A bare integer
    rank string (``'1'``–``'5'``) resolves to the canonical fallback label for
    that rank. Any non-empty value not present in the vocabulary raises
    :class:`ValueError` — there are no fuzzy synonyms.

    :param raw: raw phenotype cell value
    :return: the lowercased + whitespace-stripped label to store, or ``''``
    :raises ValueError: if *raw* is non-empty but not a known label or rank
    """

    key = raw.strip().lower()
    if not key:
        return ''
    if key in _LABEL_TO_RANK:
        # Unknown-sentinel synonyms ('none', 'not analysed') collapse to the
        # canonical 'unknown' label; 'unknown' itself passes through.
        if _LABEL_TO_RANK[key] == RANK_UNKNOWN and key != 'unknown':
            return 'unknown'
        return key
    # A bare severity rank (1–5) resolves to its canonical fallback label.
    # Sentinels (0, -1) are not accepted as bare input.
    if key.isdigit() and 1 <= (rank := int(key)) <= 5:
        return rank_to_label(rank)
    raise ValueError(
        f'Unknown phenotype label {raw!r}. '
        f'Allowed labels: {sorted(_LABEL_TO_RANK)} '
        f'or a bare rank 1–5.'
    )


def _normalize_phenotypes_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
    missing_phenotype_default: str = '',
    missing_clinical_default: str = '',
) -> tuple[str, str]:
    """Normalize phenotype and clinical_phenotype to strict rank-vocabulary labels.

    Each non-empty value is lowercased + whitespace-stripped and resolved against
    the rank vocabulary via :func:`normalize_phenotype_label`. Unknown non-empty
    labels raise :class:`ValueError` (collected into *errors* so the caller can
    report all row errors at once). Empty cells store ``''`` (rank 0 / unknown).
    The ``missing_*_default`` arguments are accepted for backward call-site
    compatibility but are no longer meaningful under the strict system — empty
    is the only default.
    """
    phenotype_raw = _get_value(row, 'phenotype')
    clinical_raw = _get_value(row, 'clinical_phenotype')

    def _resolve(raw: str, column: str) -> str:
        if not raw:
            return ''
        try:
            return normalize_phenotype_label(raw)
        except ValueError as exc:
            errors.append(f'{context}: invalid {column} value {raw!r}: {exc}')
            raise

    return _resolve(phenotype_raw, 'phenotype'), _resolve(clinical_raw, 'clinical_phenotype')
