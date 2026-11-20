"""TSV cell normalization for resistance rule imports — phenotypes, IC50, score, and comments."""

from __future__ import annotations

import re

from respro.core.phenotype_ranks import normalize_phenotype_label

CONTRADICTORY_COMMENT = 'Publications have contradictory phenotype associations.'


def get_value(row: dict[str, str], *keys: str) -> str:
    """Return the first non-empty, stripped cell value matching *keys* in *row*."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            stripped = value.strip()
            if stripped:
                return stripped
    return ''


def append_contradictory_comment(
    comment: str,
    *,
    phenotype: str,
    clinical_phenotype: str,
) -> str:
    """Append a standard explanatory note when a rule carries a contradictory phenotype."""
    if phenotype != 'contradictory' and clinical_phenotype != 'contradictory':
        return comment

    normalized = comment.strip()
    if CONTRADICTORY_COMMENT.lower() in normalized.lower():
        return normalized
    if not normalized:
        return CONTRADICTORY_COMMENT
    if normalized.endswith(('.', '!', '?')):
        return f'{normalized} {CONTRADICTORY_COMMENT}'
    return f'{normalized}. {CONTRADICTORY_COMMENT}'


def parse_ic50_value(raw: str) -> float | None:
    """Extract the leading floating-point value from a raw IC50 cell, or ``None``."""
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


def parse_single_ic50(raw: str, *, errors: list[str], context: str) -> str:
    """Parse one IC50 cell into canonical numeric text or append an error and return ``''``."""
    value = raw.strip()
    if not value or value.lower() == 'none':
        return ''
    parsed = parse_ic50_value(value)
    if parsed is None:
        errors.append(f'{context}: invalid ic50 value {value!r}')
        return ''
    return f'{parsed:g}'


def normalize_ic50_from_row(row: dict[str, str], *, errors: list[str], context: str) -> str:
    """Normalize the ``ic50`` / ``ic_50`` column for one rule row."""
    return parse_single_ic50(get_value(row, 'ic50', 'ic_50'), errors=errors, context=context)


def normalize_fold_ic50_from_row(row: dict[str, str], *, errors: list[str], context: str) -> str:
    """Normalize the ``fold_ic50`` / ``fold_ic_50`` column for one rule row."""
    return parse_single_ic50(get_value(row, 'fold_ic50', 'fold_ic_50'), errors=errors, context=context)


def normalize_score_from_row(row: dict[str, str], *, errors: list[str], context: str) -> str:
    """Normalize the numeric ``score`` column for one rule row."""
    value = get_value(row, 'score')
    if not value or value.lower() == 'none':
        return ''
    try:
        return f'{float(value):g}'
    except ValueError:
        errors.append(f'{context}: invalid score value {value!r}')
        return ''


def normalize_phenotypes_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> tuple[str, str]:
    """Normalize ``phenotype`` and ``clinical_phenotype`` cells against the rank vocabulary."""
    def _resolve(column: str) -> str:
        raw = get_value(row, column)
        if not raw:
            return ''
        try:
            return normalize_phenotype_label(raw)
        except ValueError as exc:
            errors.append(f'{context}: invalid {column} value {raw!r}: {exc}')
            return ''

    return _resolve('phenotype'), _resolve('clinical_phenotype')
