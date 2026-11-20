"""Interpretation algorithm validation, threshold resolution, and drug-level scoring."""

from __future__ import annotations

import re

from respro.core.codons import HIGH_IMPACT_CONSEQUENCES
from respro.core.phenotype_ranks import (
    RANK_CONTRADICTORY,
    RANK_UNKNOWN,
    label_to_rank,
    most_frequent_label,
    normalize_phenotype_label,
    rank_to_label,
)

_ALLOWED_EFFECTS: frozenset[str] = HIGH_IMPACT_CONSEQUENCES

_ACCESSION_IDENTIFIER_RE = re.compile(
    r'^(?P<base>(?:[A-Z]{1,6}_[A-Z0-9]*\d[A-Z0-9]*|[A-Z]{1,6}\d[A-Z0-9]*))(?:\.(?P<version>\d+))?$'
)

_KNOWN_ALGORITHM_NAMES = frozenset({
    'drug_groups',
    'drug_interpretation',
    'drug_alias',
    'effect_as_resistant',
})

METHOD_LABEL: dict[str, str] = {
    'by_phenotype': 'Phenotype',
    'by_score': 'Score',
    'by_ic50': 'IC50',
    'by_fold_ic50': 'Fold IC50',
}

# Contradictory wins over susceptible (1) and unknown (0) but loses to higher severity ranks (2-5).
_STRENGTH_CONTRADICTORY = 1.5
_STRENGTH_WEAKEST = 0.0


def assessment_strength(label: str) -> float:
    """Return numeric severity strength for strongest-wins cross-method comparison."""
    rank = label_to_rank(label)
    if rank is None or rank == RANK_UNKNOWN:
        return _STRENGTH_WEAKEST
    if rank == RANK_CONTRADICTORY:
        return _STRENGTH_CONTRADICTORY
    return float(rank)


def references_match(configured_reference: str, observed_reference: str) -> bool:
    """Return whether two reference identifiers match exactly or share an accession base."""
    if configured_reference == observed_reference:
        return True
    configured_match = _ACCESSION_IDENTIFIER_RE.fullmatch(configured_reference)
    observed_match = _ACCESSION_IDENTIFIER_RE.fullmatch(observed_reference)
    if configured_match is None or observed_match is None:
        return False
    return configured_match.group('base') == observed_match.group('base')


def _normalize_reference_for_dedup(reference: str | None) -> str | None:
    """Collapse versioned accessions to their base so duplicate overrides cannot sneak past validation."""
    if reference is None:
        return None
    match = _ACCESSION_IDENTIFIER_RE.fullmatch(reference)
    return reference if match is None else match.group('base')


def _normalize_threshold_label_key(key: object) -> str:
    """Normalize a threshold dict key (verbatim label or bare rank 1-5) to canonical form."""
    if isinstance(key, int) and not isinstance(key, bool):
        key = str(key)
    if not isinstance(key, str):
        raise ValueError(
            f'Unknown phenotype label {key!r}. '
            'Allowed: a phenotype label or a bare rank 1–5.'
        )
    return normalize_phenotype_label(key)


def _normalize_thresholds_dict_keys(thresholds: dict, *, prefix: str = 'thresholds') -> dict:
    """Return a copy of *thresholds* with canonical label keys, rejecting collisions."""
    normalized: dict = {}
    for raw_key, value in thresholds.items():
        label = _normalize_threshold_label_key(raw_key)
        if label in normalized:
            raise ValueError(
                f'{prefix}: duplicate threshold keys normalize to the same label '
                f'{label!r} (from {raw_key!r}); provide each label only once.'
            )
        normalized[label] = value
    return normalized


def validate_interpretation_algorithms(algorithms: object) -> list[dict]:
    """Validate and normalize a list of ``interpretation_algorithms`` metadata configs."""
    if not isinstance(algorithms, list):
        raise ValueError(
            f'interpretation_algorithms must be a list, got {type(algorithms).__name__}.'
        )

    seen_names: set[str] = set()
    seen_methods: set[str] = set()
    validators = {
        'drug_groups': _validate_drug_groups,
        'drug_alias': _validate_drug_alias,
        'effect_as_resistant': _validate_effect_as_resistant,
    }

    for i, item in enumerate(algorithms):
        if not isinstance(item, dict):
            raise ValueError(
                f'interpretation_algorithms[{i}] must be a dict, got {type(item).__name__}.'
            )
        name = item.get('name')
        if name not in _KNOWN_ALGORITHM_NAMES:
            known = ', '.join(sorted(_KNOWN_ALGORITHM_NAMES))
            raise ValueError(f'Unknown algorithm name {name!r} at index {i}. Known names: {known}.')

        if name == 'drug_interpretation':
            _validate_drug_interpretation(item)
            method = item.get('method', '')
            if method in seen_methods:
                raise ValueError(
                    f'Duplicate drug_interpretation method {method!r}: '
                    'each method may appear at most once.'
                )
            seen_methods.add(method)
        else:
            if name in seen_names:
                raise ValueError(
                    f'Duplicate algorithm name {name!r}: each algorithm may appear at most once.'
                )
            seen_names.add(name)
            validators[name](item)

    return algorithms


def _validate_drug_groups(config: dict) -> None:
    groups = config.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('drug_groups: "groups" must be a non-empty dict.')

    seen_drugs: dict[str, str] = {}
    for group_name, members in groups.items():
        if not isinstance(members, list) or not members:
            raise ValueError(
                f'drug_groups: groups[{group_name!r}] must be a non-empty list of strings.'
            )
        for drug in members:
            if not isinstance(drug, str):
                raise ValueError(
                    f'drug_groups: groups[{group_name!r}] must contain strings only, '
                    f'got {type(drug).__name__}.'
                )
            if drug in seen_drugs:
                raise ValueError(
                    f'drug_groups: drug {drug!r} appears in both '
                    f'{seen_drugs[drug]!r} and {group_name!r}.'
                )
            seen_drugs[drug] = group_name


def _validate_drug_interpretation(config: dict) -> None:
    method = config.get('method')
    if method not in ('by_phenotype', 'by_score', 'by_ic50', 'by_fold_ic50'):
        raise ValueError(
            'drug_interpretation: "method" must be "by_phenotype", "by_score", '
            f'"by_ic50", or "by_fold_ic50", got {method!r}.'
        )

    if method == 'by_phenotype':
        if 'thresholds' in config:
            raise ValueError(
                'drug_interpretation: "by_phenotype" does not accept a "thresholds" '
                'key; the highest-rank phenotype hit wins automatically.'
            )
        if config.get('drug_thresholds') is not None:
            raise ValueError(
                'drug_interpretation: "by_phenotype" does not accept '
                '"drug_thresholds" overrides; there are no thresholds to override.'
            )
        return

    thresholds = config.get('thresholds')
    if not isinstance(thresholds, dict):
        raise ValueError('drug_interpretation: "thresholds" must be a dict.')

    thresholds = _normalize_thresholds_dict_keys(
        thresholds, prefix='drug_interpretation: "thresholds"',
    )
    config['thresholds'] = thresholds
    _require_severity_label(thresholds, prefix='drug_interpretation: "thresholds"')

    is_numeric = method in {'by_ic50', 'by_fold_ic50'}
    if is_numeric:
        _require_rank1_label(thresholds, prefix='drug_interpretation: "thresholds"')
    _validate_threshold_values(
        thresholds, is_numeric=is_numeric, prefix='drug_interpretation: thresholds',
    )
    _validate_drug_thresholds_overrides(
        config, is_numeric=is_numeric, prefix='drug_interpretation: drug_thresholds',
    )


def _require_severity_label(thresholds: dict, *, prefix: str) -> None:
    if not any((rank := label_to_rank(k)) is not None and rank > 0 for k in thresholds):
        raise ValueError(
            f'{prefix} must include at least one severity label '
            '(a phenotype label with rank 1–5, e.g. "resistant" or '
            '"low-level resistance").'
        )


def _require_rank1_label(thresholds: dict, *, prefix: str) -> None:
    if not any(label_to_rank(k) == 1 for k in thresholds):
        raise ValueError(
            f'{prefix} must include at least one rank-1 label '
            '(a phenotype label with rank 1, e.g. "susceptible" or '
            '"sensitive") for numeric methods; it is returned when the value '
            'falls below all higher-rank breakpoints.'
        )


def _validate_threshold_values(
    thresholds: dict, *, is_numeric: bool, prefix: str,
) -> None:
    type_label = 'positive number' if is_numeric else 'positive integer'
    for key, val in thresholds.items():
        rank = label_to_rank(key)
        valid_type = (
            isinstance(val, (int, float)) if is_numeric else isinstance(val, int)
        ) and not isinstance(val, bool)
        if not valid_type or (rank != 1 and val <= 0):
            raise ValueError(f'{prefix}[{key!r}] must be a {type_label}, got {val!r}.')
    if is_numeric:
        _require_monotonic_thresholds(thresholds, prefix=prefix)


def _require_monotonic_thresholds(thresholds: dict, *, prefix: str) -> None:
    """Require numeric thresholds to be non-decreasing across ascending severity ranks."""
    ranked = sorted(
        (rank, threshold)
        for label, threshold in thresholds.items()
        if threshold is not None and (rank := label_to_rank(label)) is not None and rank > 0
    )
    for i in range(1, len(ranked)):
        prev_rank, prev_threshold = ranked[i - 1]
        cur_rank, cur_threshold = ranked[i]
        if cur_rank > prev_rank and cur_threshold < prev_threshold:
            raise ValueError(
                f'{prefix}: numeric thresholds must be non-decreasing with rank, '
                f'but rank {cur_rank} ({cur_threshold!r}) is lower than '
                f'rank {prev_rank} ({prev_threshold!r}).'
            )


def _validate_drug_thresholds_overrides(
    config: dict, *, is_numeric: bool, prefix: str,
) -> None:
    drug_thresholds = config.get('drug_thresholds')
    if drug_thresholds is None:
        return
    if not isinstance(drug_thresholds, list):
        raise ValueError(f'{prefix}: "drug_thresholds" must be a list.')

    seen_keys: set[tuple[str | None, str]] = set()
    for i, entry in enumerate(drug_thresholds):
        if not isinstance(entry, dict):
            raise ValueError(f'{prefix}[{i}] must be a dict, got {type(entry).__name__}.')

        reference = entry.get('reference')
        if reference is not None:
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError(f"{prefix}[{i}]['reference'] must be a non-empty string.")
            reference = reference.strip()
            entry['reference'] = reference

        drug = entry.get('drug')
        if not isinstance(drug, str) or not drug.strip():
            raise ValueError(f"{prefix}[{i}]['drug'] must be a non-empty string.")
        drug = drug.strip()
        entry['drug'] = drug

        override_thresholds = entry.get('thresholds')
        if not isinstance(override_thresholds, dict):
            raise ValueError(f"{prefix}[{i}]['thresholds'] must be a dict.")
        entry_prefix = f"{prefix}[{i}]['thresholds']"
        override_thresholds = _normalize_thresholds_dict_keys(
            override_thresholds, prefix=entry_prefix,
        )
        entry['thresholds'] = override_thresholds
        _require_severity_label(override_thresholds, prefix=entry_prefix)
        _validate_threshold_values(override_thresholds, is_numeric=is_numeric, prefix=entry_prefix)

        key = (_normalize_reference_for_dedup(reference), drug.lower())
        if key in seen_keys:
            raise ValueError(
                f'{prefix}: duplicate override for (reference={reference!r}, drug={drug!r}).'
            )
        seen_keys.add(key)


def _validate_drug_alias(config: dict) -> None:
    groups = config.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('drug_alias: "groups" must be a non-empty dict.')

    seen_aliases: set[str] = set()
    for canonical_name, alias in groups.items():
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ValueError('drug_alias: each canonical drug name key must be a non-empty string.')
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError(f'drug_alias: alias for {canonical_name!r} must be a non-empty string.')
        normalized_alias = alias.strip()
        if normalized_alias in seen_aliases:
            raise ValueError(
                f'drug_alias: alias value {normalized_alias!r} is duplicated across canonical names.'
            )
        seen_aliases.add(normalized_alias)


def _validate_effect_as_resistant(config: dict) -> None:
    rules = config.get('rules')
    if not isinstance(rules, list) or not rules:
        raise ValueError('effect_as_resistant: "rules" must be a non-empty list.')

    seen_keys: set[tuple[str, str, str]] = set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(
                f'effect_as_resistant: rules[{i}] must be a dict, got {type(rule).__name__}.'
            )
        for key in ('feature', 'effect', 'reference', 'drug'):
            if key not in rule:
                raise ValueError(
                    f'effect_as_resistant: rules[{i}] is missing required key {key!r}.'
                )
        for key in ('feature', 'reference', 'drug'):
            val = rule.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f'effect_as_resistant: rules[{i}][{key!r}] must be a non-empty string.'
                )
            rule[key] = val.strip()

        effect = rule.get('effect')
        if not isinstance(effect, list) or not effect:
            raise ValueError(
                f"effect_as_resistant: rules[{i}]['effect'] must be a non-empty list of strings."
            )
        stripped_effects: list[str] = []
        for j, eff in enumerate(effect):
            if not isinstance(eff, str) or not eff.strip():
                raise ValueError(
                    f"effect_as_resistant: rules[{i}]['effect'][{j}] must be a non-empty string."
                )
            stripped = eff.strip()
            if stripped not in _ALLOWED_EFFECTS:
                allowed = ', '.join(sorted(_ALLOWED_EFFECTS))
                raise ValueError(
                    f"effect_as_resistant: rules[{i}]['effect'][{j}] has invalid value "
                    f'{stripped!r}. Allowed values: {allowed}.'
                )
            stripped_effects.append(stripped)
        rule['effect'] = stripped_effects

        triplet = (rule['feature'], rule['reference'], rule['drug'])
        if triplet in seen_keys:
            raise ValueError(
                'effect_as_resistant: duplicate rule tuple '
                f'(feature={triplet[0]!r}, reference={triplet[1]!r}, drug={triplet[2]!r}).'
            )
        seen_keys.add(triplet)


def resolve_thresholds_with_source(
    config: dict,
    reference_name: str | None,
    drug_name: str,
) -> tuple[dict, str]:
    """Resolve thresholds for one ``(reference, drug)`` and return ``(thresholds, source_label)``."""
    drug_lower = drug_name.strip().lower()
    drug_only: dict | None = None
    reference_specific: dict | None = None

    for entry in (config.get('drug_thresholds') or []):
        if entry.get('drug', '').strip().lower() != drug_lower:
            continue
        ref = entry.get('reference')
        if ref is None:
            drug_only = entry.get('thresholds')
        elif reference_name is not None and references_match(ref, reference_name):
            reference_specific = entry.get('thresholds')

    if reference_specific is not None:
        return dict(reference_specific), 'override (reference, drug)'
    if drug_only is not None:
        return dict(drug_only), 'override (drug)'

    global_thresholds = config.get('thresholds', {})
    if isinstance(global_thresholds.get('resistant'), (int, float)):
        return dict(global_thresholds), 'global default'
    drug_entry = global_thresholds.get(drug_name) or global_thresholds.get(drug_name.strip())
    if isinstance(drug_entry, dict):
        return dict(drug_entry), 'global default'
    return dict(global_thresholds), 'global default'


def resolve_thresholds_dict(
    config: dict,
    reference_name: str | None,
    drug_name: str,
) -> dict:
    """Resolve the active multi-tier thresholds dict for one drug and optional reference."""
    thresholds, _ = resolve_thresholds_with_source(config, reference_name, drug_name)
    return thresholds


def compute_drug_assessment(
    drug_data: dict,
    configs: list[dict],
    reference_name: str | None = None,
    drug_name: str | None = None,
) -> tuple[str, list[dict]]:
    """Compute per-method drug assessments and return ``(strongest_label, method_assessments)``."""
    method_assessments: list[dict] = []
    for config in configs:
        method = config.get('method', '')
        if method == 'by_phenotype':
            resolved_thresholds: dict = {}
        elif drug_name is not None:
            resolved_thresholds = resolve_thresholds_dict(config, reference_name, drug_name)
        else:
            resolved_thresholds = dict(config.get('thresholds', {}))

        assessment = _compute_single_method(method, drug_data, resolved_thresholds)
        if not assessment:
            assessment = (
                _thresholds_rank1_label(resolved_thresholds)
                or drug_data.get('tier1_label')
                or rank_to_label(1)
            )
        method_assessments.append({
            'method': method,
            'label': METHOD_LABEL.get(method, method),
            'assessment': assessment,
        })

    best = max(method_assessments, key=lambda m: assessment_strength(m['assessment']))
    return best['assessment'], method_assessments


def _compute_single_method(method: str, drug_data: dict, thresholds: dict) -> str:
    if method == 'by_phenotype':
        return _assess_by_phenotype(drug_data)
    if method == 'by_score':
        return _assess_by_score(drug_data, thresholds)
    if method == 'by_ic50':
        values = drug_data['ic50_values']
        return _assess_numeric(values, thresholds, drug_data.get('tier1_label')) if values else ''
    if method == 'by_fold_ic50':
        values = drug_data['fold_ic50_values']
        return _assess_numeric(values, thresholds, drug_data.get('tier1_label')) if values else ''
    return ''


def _assess_by_phenotype(drug_data: dict) -> str:
    """Return the highest-rank phenotype label among hits (contradictory sits between rank 1 and 2)."""
    rank_counts: dict[int, int] = drug_data.get('rank_counts', {})
    rank_label_counts: dict[int, dict[str, int]] = drug_data.get('rank_label_counts', {})

    def _label_for(rank: int) -> str:
        labels = rank_label_counts.get(rank)
        return (most_frequent_label(labels) or rank_to_label(rank)) if labels else rank_to_label(rank)

    for rank in sorted((r for r in rank_counts if r >= 2), reverse=True):
        if rank_counts[rank] >= 1:
            return _label_for(rank)
    if rank_counts.get(RANK_CONTRADICTORY, 0) > 0:
        return _label_for(RANK_CONTRADICTORY)
    if rank_counts.get(1, 0) > 0:
        return _label_for(1)
    if drug_data['hit_count'] > 0:
        return drug_data.get('tier1_label') or rank_to_label(1)
    return ''


def _thresholds_rank1_label(thresholds: dict) -> str | None:
    """Return the first rank-1 label configured in *thresholds*, or ``None``."""
    for label in thresholds:
        if label_to_rank(label) == 1:
            return str(label)
    return None


def _severity_breakpoints(thresholds: dict) -> list[tuple[int, float, str]]:
    """Return ``(rank, threshold, label)`` breakpoints sorted weakest-first."""
    items = [
        (rank, threshold, label)
        for label, threshold in thresholds.items()
        if threshold is not None and (rank := label_to_rank(label)) is not None and rank > 0
    ]
    items.sort(key=lambda item: (item[0], item[1]))
    return items


def _assess_by_score(drug_data: dict, thresholds: dict) -> str:
    total = drug_data['score_total']
    for rank, threshold, label in reversed(_severity_breakpoints(thresholds)):
        if rank > 1 and total >= threshold:
            return label
    rank1_label = _thresholds_rank1_label(thresholds)
    if rank1_label:
        return rank1_label
    if drug_data['hit_count'] > 0:
        return drug_data.get('tier1_label') or rank_to_label(1)
    return ''


def _assess_numeric(
    values: list[float],
    thresholds: dict,
    tier1_label: str | None = None,
) -> str:
    """Return the strongest rank > 1 breakpoint met by any value, else the rank-1 fallback label."""
    for rank, threshold, label in reversed(_severity_breakpoints(thresholds)):
        if rank > 1 and any(value >= threshold for value in values):
            return label
    return _thresholds_rank1_label(thresholds) or tier1_label or rank_to_label(1)
