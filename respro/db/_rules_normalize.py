"""
TSV row normalization for resistance rule imports — phenotypes, IC50, score, and comments.
"""

from __future__ import annotations

import re

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


def _normalize_phenotype_token(raw: str) -> str | None:
    """Map supported phenotype inputs to canonical internal values."""
    value = raw.strip().lower()
    if not value or value == 'none':
        return 'unknown'

    mapping = {
        'resistant': 'resistant',
        'resistance': 'resistant',
        'res': 'resistant',
        'r': 'resistant',
        'true': 'resistant',
        '1': 'resistant',
        'intermediate': 'intermediate',
        'interm': 'intermediate',
        'i': 'intermediate',
        'sensitive': 'sensitive',
        'susceptible': 'sensitive',
        'sensi': 'sensitive',
        'sens': 'sensitive',
        's': 'sensitive',
        'false': 'sensitive',
        '0': 'sensitive',
        'unknown': 'unknown',
        'na': 'unknown',
        'n/a': 'unknown',
        'nd': 'unknown',
        'contradictory': 'contradictory',
        'contra': 'contradictory',
        'conflict': 'contradictory',
        'conflicting': 'contradictory',
    }
    return mapping.get(value)


def _normalize_phenotypes_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> tuple[str, str]:
    """Normalize phenotype and clinical_phenotype to canonical values independently."""
    phenotype_raw = _get_value(row, 'phenotype')
    clinical_raw = _get_value(row, 'clinical_phenotype')

    phenotype_normalized = _normalize_phenotype_token(phenotype_raw) if phenotype_raw else 'unknown'
    if phenotype_raw and phenotype_normalized is None:
        errors.append(f'{context}: invalid phenotype value {phenotype_raw!r}')
        phenotype_normalized = 'unknown'

    clinical_normalized = _normalize_phenotype_token(clinical_raw) if clinical_raw else 'unknown'
    if clinical_raw and clinical_normalized is None:
        errors.append(f'{context}: invalid clinical_phenotype value {clinical_raw!r}')
        clinical_normalized = 'unknown'

    return phenotype_normalized, clinical_normalized
