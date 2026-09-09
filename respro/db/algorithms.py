"""Interpretation algorithm validation, storage, and loading."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from respro.core.annotation import HIGH_IMPACT_CONSEQUENCES
from respro.db._rules_normalize import normalize_phenotype_label
from respro.db.phenotype_ranks import (
    RANK_CONTRADICTORY,
    RANK_UNKNOWN,
    label_to_rank,
    rank_to_label,
)

logger = logging.getLogger(__name__)

_ALLOWED_EFFECTS: frozenset[str] = HIGH_IMPACT_CONSEQUENCES

# Accession identifier (base + optional version), mirroring the matcher in
# respro/report/html.py so reference matching is consistent across DB and report.
_ACCESSION_IDENTIFIER_RE = re.compile(
    r'^(?P<base>(?:[A-Z]{1,6}_[A-Z0-9]*\d[A-Z0-9]*|[A-Z]{1,6}\d[A-Z0-9]*))(?:\.(?P<version>\d+))?$'
)

_KNOWN_ALGORITHM_NAMES = {
    'drug_groups',
    'drug_interpretation',
    'drug_alias',
    'effect_as_resistant',
}


def _normalize_threshold_label_key(key: object) -> str:
    """Normalize a threshold dict key to a canonical phenotype label.

    Accepts a verbatim label (lowercased + whitespace-stripped), a bare integer
    rank (1–5), or a bare-rank string (``'1'``–``'5'``); returns the canonical
    label for that rank. Unknown value raise :class:`ValueError` pointing at the
    vocabulary.

    :param key: raw threshold key (label string or rank int/str)
    :return: canonical lowercased phenotype label
    :raises ValueError: if *key* is not a known label or bare rank
    """

    if isinstance(key, int) and not isinstance(key, bool):
        # Bare integer rank: convert to its string form and let the normalizer
        # resolve it to the canonical fallback label.
        key = str(key)
    if not isinstance(key, str):
        raise ValueError(
            f'Unknown phenotype label {key!r}. '
            'Allowed: a phenotype label or a bare rank 1–5.'
        )
    return normalize_phenotype_label(key)


def _normalize_thresholds_dict_keys(thresholds: dict, *, prefix: str = 'thresholds') -> dict:
    """Return a new thresholds dict with all keys normalized to canonical labels.

    Raises :class:`ValueError` if any key is an unknown label/shorthand, or if
    two raw keys normalize to the same canonical label (e.g. bare rank ``'1'``
    and the label ``'susceptible'``). A silent last-write-wins collision would
    discard one threshold with no warning, so the collision is rejected
    explicitly.

    :param thresholds: raw thresholds dict (label/rank → value)
    :param prefix: descriptive prefix for error messages
    :return: new dict with canonical label keys
    """
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
    """
    Validate and return a list of interpretation algorithm configs.

    :param algorithms: raw value from the ``interpretation_algorithms`` metadata key
    :return: validated list of algorithm config dicts
    :raises ValueError: with a descriptive message on any invalid input
    """
    if not isinstance(algorithms, list):
        raise ValueError(
            'interpretation_algorithms must be a list, '
            f'got {type(algorithms).__name__}.'
        )

    seen_names: set[str] = set()
    seen_drug_interp_methods: set[str] = set()
    for i, item in enumerate(algorithms):
        if not isinstance(item, dict):
            raise ValueError(
                f'interpretation_algorithms[{i}] must be a dict, '
                f'got {type(item).__name__}.'
            )

        name = item.get('name')
        if name not in _KNOWN_ALGORITHM_NAMES:
            known = ', '.join(sorted(_KNOWN_ALGORITHM_NAMES))
            raise ValueError(
                f'Unknown algorithm name {name!r} at index {i}. '
                f'Known names: {known}.'
            )

        if name == 'drug_interpretation':
            # Multiple drug_interpretation entries are allowed if their methods differ.
            _validate_drug_interpretation(item)
            method = item.get('method', '')
            if method in seen_drug_interp_methods:
                raise ValueError(
                    f'Duplicate drug_interpretation method {method!r}: '
                    f'each method may appear at most once.'
                )
            seen_drug_interp_methods.add(method)
        else:
            if name in seen_names:
                raise ValueError(
                    f'Duplicate algorithm name {name!r}: each algorithm may appear at most once.'
                )
            seen_names.add(name)

            if name == 'drug_groups':
                _validate_drug_groups(item)
            elif name == 'drug_alias':
                _validate_drug_alias(item)
            elif name == 'effect_as_resistant':
                _validate_effect_as_resistant(item)

    return algorithms


def _collect_algorithm_labels(algorithms: list[dict]) -> set[str]:
    """Return the set of phenotype labels declared by the algorithm configs.

    ``drug_interpretation`` declares labels as threshold dict keys.
    ``effect_as_resistant`` implicitly declares ``'resistant'``. Other algorithm
    kinds declare no phenotype labels.

    :param algorithms: validated algorithm config list
    :return: set of declared phenotype labels (lowercased)
    """
    labels: set[str] = set()
    for config in algorithms:
        name = config.get('name')
        if name == 'drug_interpretation':
            labels.update(config.get('thresholds', {}).keys())
        elif name == 'effect_as_resistant':
            labels.add('resistant')
    return labels


def warn_algorithm_labels_not_in_db(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> None:
    """Emit a non-fatal warning for each algorithm label absent from the DB.

    For each phenotype label declared by *algorithms* (see
    :func:`_collect_algorithm_labels`), check whether that exact label string is
    among the labels stored in the project's ``resistance_rule`` and
    ``resistance_formula_rule`` tables (both already lowercased + whitespace-
    stripped at import time). When a declared label is not present in the DB, a
    WARNING is logged. The label still resolves to a rank via the vocabulary, so
    processing continues. Labels not in the vocabulary at all already hard-fail
    during :func:`validate_interpretation_algorithms`.

    :param conn: open project DB connection
    :param project_id: project id
    :param algorithms: validated algorithm config list
    """
    declared = _collect_algorithm_labels(algorithms)
    if not declared:
        return
    stored: set[str] = set()
    for table in ('resistance_rule', 'resistance_formula_rule'):
        rows = conn.execute(
            f'SELECT DISTINCT t.phenotype AS phenotype FROM {table} t '
            'JOIN drug d ON d.id = t.drug_id '
            'WHERE d.project_id = ? AND TRIM(COALESCE(t.phenotype, "")) <> ""',
            (project_id,),
        ).fetchall()
        stored.update(r['phenotype'].strip() for r in rows)
    # Only warn when the database actually stores phenotype labels. When no
    # labels are stored at all the comparison is meaningless and the warning
    # is just noise (e.g. projects whose rules carry no phenotype column).
    if not stored:
        return
    for label in sorted(declared):
        if label not in stored:
            logger.warning(
                'Algorithm config declares phenotype label %r, which is not among '
                'the labels stored in the database (%s). The label still resolves '
                'to a rank and processing continues; verify the vocabulary is '
                'consistent between the algorithm config and the rules sheet.',
                label, sorted(stored),
            )


def store_interpretation_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
    algorithms: list[dict],
) -> None:
    """
    Persist validated interpretation algorithms to the project database.

    Replaces any existing algorithms for the given project.

    :param conn: project DB connection
    :param project_id: project id
    :param algorithms: list of validated algorithm config dicts
    """
    conn.execute(
        'DELETE FROM interpretation_algorithm WHERE project_id = ?',
        (project_id,),
    )
    for config in algorithms:
        conn.execute(
            'INSERT INTO interpretation_algorithm (project_id, algorithm_name, config_json) '
            'VALUES (?, ?, ?)',
            (project_id, config['name'], json.dumps(config)),
        )


def load_interpretation_algorithms(
    conn: sqlite3.Connection,
    project_id: int,
) -> list[dict]:
    """
    Load interpretation algorithms from the project database.

    :param conn: project DB connection
    :param project_id: project id
    :return: list of algorithm config dicts; empty list if none are configured
    """
    rows = conn.execute(
        'SELECT config_json FROM interpretation_algorithm WHERE project_id = ? ORDER BY id',
        (project_id,),
    ).fetchall()
    return [json.loads(row['config_json']) for row in rows]


def apply_drug_alias_mappings(
    conn: sqlite3.Connection,
    project_id: int,
    config: dict,
) -> int:
    """
    Apply canonical drug-name alias mappings to ``drug.alias`` for one project.

    For each canonical name in ``config['groups']``, updates matching
    ``drug.name`` rows in the current project. Missing drugs are skipped.

    :param conn: project DB connection
    :param project_id: project id
    :param config: validated drug_alias algorithm config dict
    :return: number of drug rows updated
    """
    groups = config['groups']
    updated = 0
    for canonical_name, alias in groups.items():
        cur = conn.execute(
            'UPDATE drug SET alias = ? WHERE project_id = ? AND LOWER(name) = ?',
            (alias.strip(), project_id, canonical_name.strip().lower()),
        )
        updated += int(cur.rowcount or 0)
    return updated


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
            f'"by_ic50", or "by_fold_ic50", '
            f'got {method!r}.'
        )

    if method == 'by_phenotype':
        # by_phenotype is hardcoded: the highest-rank phenotype label among the
        # drug's hits wins. No thresholds are configurable — labels come from the
        # DB rules, not the config.
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

    # Normalize label/rank keys to canonical labels (rejects shorthand and duplicate-key collisions).
    config['thresholds'] = _normalize_thresholds_dict_keys(
        thresholds, prefix='drug_interpretation: "thresholds"',
    )
    thresholds = config['thresholds']

    _require_severity_label(thresholds, prefix='drug_interpretation: "thresholds"')

    numeric_methods = {'by_ic50', 'by_fold_ic50'}
    is_numeric = method in numeric_methods
    if is_numeric:
        _require_rank1_label(thresholds, prefix='drug_interpretation: "thresholds"')
    _validate_threshold_values(
        thresholds, is_numeric=is_numeric, prefix='drug_interpretation: thresholds',
    )

    _validate_drug_thresholds_overrides(
        config, is_numeric=is_numeric, prefix='drug_interpretation: drug_thresholds',
    )


def _require_severity_label(thresholds: dict, *, prefix: str) -> None:
    """Require at least one severity label (rank > 0) in a thresholds dict.

    Any phenotype label that resolves to a positive rank (1–5) counts as a
    severity label; sentinels (``unknown``/``contradictory``, rank 0/-1) do
    not. A thresholds dict with no severity label is rejected — there would be
    no breakpoint to evaluate.

    :param thresholds: thresholds dict with canonical label keys
    :param prefix: descriptive prefix for error messages
    :raises ValueError: if no key resolves to a positive rank
    """
    if not any((rank := label_to_rank(k)) is not None and rank > 0 for k in thresholds):
        raise ValueError(
            f'{prefix} must include at least one severity label '
            '(a phenotype label with rank 1–5, e.g. "resistant" or '
            '"low-level resistance").'
        )


def _require_rank1_label(thresholds: dict, *, prefix: str) -> None:
    """Require at least one rank-1 label in a numeric-method thresholds dict.

    Numeric methods (``by_ic50``/``by_fold_ic50``) return the configured rank-1
    label when the value falls below all higher-rank breakpoints. The rank
    vocabulary allows multiple rank-1 labels (``susceptible``, ``sensitive``,
    ``normal inhibition``, …), so the config must declare which one to use.

    :param thresholds: thresholds dict with canonical label keys
    :param prefix: descriptive prefix for error messages
    :raises ValueError: if no key resolves to rank 1
    """
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
    """Validate a thresholds dict's values for one algorithm scope.

    Thresholds must be non-decreasing with severity rank; this is enforced by
    :func:`_require_monotonic_thresholds` (rank-generic, covers all tier pairs).

    :param thresholds: thresholds dict with canonical label keys
    :param is_numeric: True for by_ic50/by_fold_ic50 (non-negative numbers for
        rank-1 labels, positive numbers for higher ranks); False for by_score
        (positive integers)
    :param prefix: descriptive prefix for error messages
    """
    if is_numeric:
        for key, val in thresholds.items():
            rank = label_to_rank(key)
            # Rank-1 labels are the lower bound; 0.0 is allowed. Higher ranks
            # and unranked keys must be strictly positive.
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(
                    f'{prefix}[{key!r}] must be a positive number, got {val!r}.'
                )
            if rank != 1 and val <= 0:
                raise ValueError(
                    f'{prefix}[{key!r}] must be a positive number, got {val!r}.'
                )
        _require_monotonic_thresholds(thresholds, prefix=prefix)
        return

    # by_score: positive integers for rank > 1; rank-1 labels are the lower
    # bound fallback ceiling and accept 0 (consistent with numeric methods).
    for key, val in thresholds.items():
        rank = label_to_rank(key)
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(
                f'{prefix}[{key!r}] must be a positive integer, got {val!r}.'
            )
        if rank != 1 and val <= 0:
            raise ValueError(
                f'{prefix}[{key!r}] must be a positive integer, got {val!r}.'
            )


def _require_monotonic_thresholds(thresholds: dict, *, prefix: str) -> None:
    """Require numeric thresholds to be non-decreasing with severity rank.

    The strongest-matched-breakpoint selection in :func:`_assess_numeric`
    iterates breakpoints highest-rank-first and returns the first whose
    threshold the value meets. This is only well-defined when a higher rank
    never has a *lower* threshold than a lower rank — otherwise a value could
    meet a lower-rank breakpoint while a higher-rank (stronger) breakpoint with
    a smaller threshold is also met but visited later. Equal thresholds across
    ranks are permitted (a breakpoint shared by two tiers).

    Only labels resolving to a positive severity rank (1–5) participate;
    sentinels and unrecognized labels are ignored here (they are rejected
    elsewhere).

    :param thresholds: thresholds dict with canonical label keys
    :param prefix: descriptive prefix for error messages
    :raises ValueError: if any higher rank has a strictly lower threshold than
        a lower rank
    """
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
    """Validate the optional ``drug_thresholds`` override list on a config.

    Each entry is ``{reference?, drug, thresholds}`` where the override's
    ``thresholds`` must include at least one severity label (rank 1–5); the
    thresholds must be non-decreasing with severity rank.

    :param config: algorithm config dict
    :param is_numeric: True when threshold values must be positive numbers
        (by_ic50/by_fold_ic50); False for positive integers (by_phenotype/by_score)
    :param prefix: descriptive prefix for error messages
    """
    drug_thresholds = config.get('drug_thresholds')
    if drug_thresholds is None:
        return
    if not isinstance(drug_thresholds, list):
        raise ValueError(f'{prefix}: "drug_thresholds" must be a list.')

    seen_keys: set[tuple[str | None, str]] = set()
    for i, entry in enumerate(drug_thresholds):
        if not isinstance(entry, dict):
            raise ValueError(
                f'{prefix}[{i}] must be a dict, got {type(entry).__name__}.'
            )

        reference = entry.get('reference')
        if reference is not None:
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError(
                    f'{prefix}[{i}][\'reference\'] must be a non-empty string.'
                )
            reference = reference.strip()
            entry['reference'] = reference

        drug = entry.get('drug')
        if not isinstance(drug, str) or not drug.strip():
            raise ValueError(
                f'{prefix}[{i}][\'drug\'] must be a non-empty string.'
            )
        drug = drug.strip()
        entry['drug'] = drug

        override_thresholds = entry.get('thresholds')
        if not isinstance(override_thresholds, dict):
            raise ValueError(
                f'{prefix}[{i}][\'thresholds\'] must be a dict.'
            )
        # Normalize label/rank keys to canonical labels (rejects shorthand and duplicate-key collisions).
        entry['thresholds'] = _normalize_thresholds_dict_keys(
            override_thresholds, prefix=f"{prefix}[{i}][\'thresholds\']",
        )
        override_thresholds = entry['thresholds']
        _require_severity_label(
            override_thresholds, prefix=f"{prefix}[{i}][\'thresholds\']",
        )

        _validate_threshold_values(
            override_thresholds, is_numeric=is_numeric,
            prefix=f'{prefix}[{i}][\'thresholds\']',
        )

        key = (_normalize_reference_for_dedup(reference), drug.lower())
        if key in seen_keys:
            raise ValueError(
                f'{prefix}: duplicate override for '
                f'(reference={reference!r}, drug={drug!r}).'
            )
        seen_keys.add(key)


def _validate_drug_alias(config: dict) -> None:
    groups = config.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('drug_alias: "groups" must be a non-empty dict.')

    seen_aliases: set[str] = set()
    for canonical_name, alias in groups.items():
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ValueError(
                'drug_alias: each canonical drug name key must be a non-empty string.'
            )
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError(
                f'drug_alias: alias for {canonical_name!r} must be a non-empty string.'
            )
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
    required_keys = ('feature', 'effect', 'reference', 'drug')
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(
                f'effect_as_resistant: rules[{i}] must be a dict, '
                f'got {type(rule).__name__}.'
            )

        for key in required_keys:
            if key not in rule:
                raise ValueError(
                    f'effect_as_resistant: rules[{i}] is missing required key {key!r}.'
                )

        # Validate and strip feature, reference, drug
        for key in ('feature', 'reference', 'drug'):
            val = rule.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f'effect_as_resistant: rules[{i}][{key!r}] must be a non-empty string.'
                )
            rule[key] = val.strip()

        # Validate effect list
        effect = rule.get('effect')
        if not isinstance(effect, list) or not effect:
            raise ValueError(
                f'effect_as_resistant: rules[{i}][\'effect\'] must be a non-empty list of strings.'
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


_METHOD_LABEL: dict[str, str] = {
    'by_phenotype': 'Phenotype',
    'by_score': 'Score',
    'by_ic50': 'IC50',
    'by_fold_ic50': 'Fold IC50',
}


# Severity strength for strongest-wins resolution (higher = more severe).
# Severity ranks 1–5 map to themselves. Contradictory sits *between* rank 1
# (susceptible) and rank 2 (potential low-level resistance): it wins over
# susceptible and unknown, but any higher-tier severity (ranks 2–5) wins over
# contradictory. Unknown and unrecognized labels are weakest.
_STRENGTH_CONTRADICTORY = 1.5
_STRENGTH_WEAKEST = 0


def _assessment_strength(label: str) -> float:
    """Return a severity strength for *label* (higher = more severe).

    Used by :func:`compute_drug_assessment` to pick the strongest result across
    methods via ``max()``. Contradictory (rank -1) wins only over susceptible
    (rank 1) and unknown (rank 0); any higher-tier severity rank (2–5) wins
    over contradictory. Unknown (rank 0) and unrecognized labels are weakest.

    :param label: assessment label (a vocabulary entry or empty string)
    :return: severity strength; 0 (weakest) … 5 (resistant, strongest)
    """
    rank = label_to_rank(label)
    if rank is None or rank == RANK_UNKNOWN:
        return _STRENGTH_WEAKEST
    if rank == RANK_CONTRADICTORY:
        return _STRENGTH_CONTRADICTORY
    return rank


def _references_match(configured_reference: str, observed_reference: str) -> bool:
    """Return whether two references match exactly or by accession base plus version.

    Mirrors ``respro.report._row_helpers.references_match_with_accession_version`` so
    that drug_thresholds overrides resolve consistently between DB classification
    and report rendering.
    """
    if configured_reference == observed_reference:
        return True
    configured_match = _ACCESSION_IDENTIFIER_RE.fullmatch(configured_reference)
    observed_match = _ACCESSION_IDENTIFIER_RE.fullmatch(observed_reference)
    if configured_match is None or observed_match is None:
        return False
    return configured_match.group('base') == observed_match.group('base')


def _normalize_reference_for_dedup(reference: str | None) -> str | None:
    """Normalize a reference string for duplicate-override detection.

    Two overrides that differ only by accession version (e.g. ``NC_001345`` and
    ``NC_001345.1``) resolve to the same accession base and would otherwise be
    treated as distinct keys, allowing conflicting overrides through and making
    resolution order-dependent. This collapses them to their accession base so
    the duplicate check catches the conflict. Non-accession strings and ``None``
    are returned unchanged.
    """
    if reference is None:
        return None
    m = _ACCESSION_IDENTIFIER_RE.fullmatch(reference)
    if m is None:
        return reference
    return m.group('base')


def resolve_thresholds_dict(
    config: dict,
    reference_name: str | None,
    drug_name: str,
) -> dict:
    """Resolve the full multi-tier thresholds dict for one drug.

    Returns the complete thresholds dict (all severity-label keys), so multi-tier
    configs (e.g. ``{resistant, low-level resistance}``) are preserved rather
    than collapsed to a ``(resistant, intermediate)`` pair.

    :param config: validated algorithm config dict (``drug_interpretation``)
    :param reference_name: observed reference name, or ``None`` when unavailable
    :param drug_name: drug name to resolve
    :return: resolved thresholds dict mapping severity labels to threshold values
    """
    drug_thresholds = config.get('drug_thresholds') or []
    drug_lower = drug_name.strip().lower()

    drug_only: dict | None = None
    reference_specific: dict | None = None
    for entry in drug_thresholds:
        if entry.get('drug', '').strip().lower() != drug_lower:
            continue
        ref = entry.get('reference')
        if ref is None:
            drug_only = entry.get('thresholds')
        elif reference_name is not None and _references_match(ref, reference_name):
            reference_specific = entry.get('thresholds')

    if reference_specific is not None:
        return dict(reference_specific)
    if drug_only is not None:
        return dict(drug_only)

    global_thresholds = config.get('thresholds', {})
    # drug_interpretation uses flat label-keyed thresholds.
    if isinstance(global_thresholds.get('resistant'), (int, float)):
        return dict(global_thresholds)
    drug_entry = global_thresholds.get(drug_name) or global_thresholds.get(drug_name.strip())
    if drug_entry is not None:
        return dict(drug_entry)
    # Last-resort fallback: drug_interpretation's flat thresholds when drug absent.
    return dict(global_thresholds)


def compute_drug_assessment(
    drug_data: dict,
    configs: list[dict],
    reference_name: str | None = None,
    drug_name: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Compute per-method assessments and a final merged assessment for one drug.

    :param drug_data: dict with keys ``rank_counts`` (dict[int, int]),
        ``score_total``, ``ic50_values``, ``fold_ic50_values``, ``hit_count``
    :param configs: list of validated ``drug_interpretation`` config dicts
    :param reference_name: observed reference name for the drug; when provided together
        with ``drug_name``, per-``(reference, drug)`` overrides take precedence over
        the config's global ``thresholds``
    :param drug_name: drug name to resolve overrides for; when ``None`` only the
        global ``thresholds`` are used (backward-compatible behaviour)
    :return: ``(final_assessment, method_assessments)`` where
        ``final_assessment`` is the strongest-wins result and
        ``method_assessments`` is a list of
        ``{'method': ..., 'label': ..., 'assessment': ...}`` dicts (one per
        configured method; methods with no evidence default to \"susceptible\")
    """
    method_assessments: list[dict] = []

    for config in configs:
        method = config.get('method', '')
        if method == 'by_phenotype':
            # by_phenotype is hardcoded; no thresholds to resolve.
            resolved_thresholds = {}
        elif drug_name is not None:
            resolved_thresholds = resolve_thresholds_dict(
                config, reference_name, drug_name,
            )
        else:
            resolved_thresholds = config.get('thresholds', {})
            resolved_thresholds = {
                k: v for k, v in resolved_thresholds.items()
            }

        assessment = _compute_single_method(
            method, drug_data, resolved_thresholds,
        )
        # Default to the canonical rank-1 label ("susceptible") when the method
        # has no evidence of resistance. Both 'susceptible' and 'sensitive' are
        # rank 1 in the vocabulary; 'susceptible' is the canonical fallback.
        if not assessment:
            assessment = 'susceptible'
        method_assessments.append({
            'method': method,
            'label': _METHOD_LABEL.get(method, method),
            'assessment': assessment,
        })

    best = max(method_assessments, key=lambda m: _assessment_strength(m['assessment']))
    return best['assessment'], method_assessments


def _compute_single_method(
    method: str,
    drug_data: dict,
    thresholds: dict,
) -> str:
    """Compute assessment for a single method. Returns empty string if no data.

    *thresholds* maps phenotype labels to numeric thresholds (counts for
    by_phenotype, values for by_score/by_ic50/by_fold_ic50). Labels are
    resolved to ranks via the vocabulary so multi-tier configs work.
    """
    if method == 'by_phenotype':
        return _assess_by_phenotype(drug_data, thresholds)
    if method == 'by_score':
        return _assess_by_score(drug_data, thresholds)
    if method == 'by_ic50':
        return _assess_by_ic50(drug_data, thresholds)
    if method == 'by_fold_ic50':
        return _assess_by_fold_ic50(drug_data, thresholds)
    return ''


def _assess_by_phenotype(drug_data: dict, thresholds: dict) -> str:
    """Assess by phenotype labels: the highest-rank hit wins.

    Hardcoded logic (no configurable thresholds): iterate ``rank_counts``
    highest-rank first and return the canonical label of the first rank >= 2
    with count >= 1. Contradictory (any count > 0) wins over susceptible (rank
    1) but loses to any higher-tier severity hit (ranks 2-5). Hits with no
    severity/contradictory label yield ``'susceptible'``. No hits yield ``''``
    (the caller defaults to ``'susceptible'``).

    *thresholds* is accepted for signature parity with the other assess helpers
    but is ignored.
    """
    rank_counts: dict[int, int] = drug_data.get('rank_counts', {})

    # Severity ranks 2–5, highest first; return the first with any hits.
    # Rank 1 (susceptible) is deliberately skipped here so that contradictory
    # can win over it — contradictory sits between rank 1 and rank 2.
    for rank in sorted((r for r in rank_counts if r >= 2), reverse=True):
        if rank_counts[rank] >= 1:
            return rank_to_label(rank)

    if rank_counts.get(RANK_CONTRADICTORY, 0) > 0:
        return 'contradictory'
    if drug_data['hit_count'] > 0:
        return 'susceptible'
    return ''


def _severity_breakpoints(thresholds: dict) -> list[tuple[int, float, str]]:
    """Return severity breakpoints as ``(rank, threshold, label)`` sorted weakest-first.

    Only labels that resolve to a positive severity rank (1–5) with a non-None
    threshold are included. Sorting by ``(rank, threshold)`` — not by label
    string — guarantees that iterating in reverse visits the strongest matched
    breakpoint first, so multi-tier and same-rank configs resolve correctly.
    """
    items: list[tuple[int, float, str]] = []
    for label, threshold in thresholds.items():
        if threshold is None:
            continue
        rank = label_to_rank(label)
        if rank is None or rank <= 0:
            continue
        items.append((rank, threshold, label))
    items.sort(key=lambda item: (item[0], item[1]))
    return items


def _assess_by_score(drug_data: dict, thresholds: dict) -> str:
    """Assess by total score against label-keyed score thresholds.

    The strongest matched breakpoint is the highest-rank label whose threshold
    the total score meets; ties within a rank go to the higher threshold. The
    rank-1 label is the lower-bound fallback ceiling and is **skipped** during
    matching (consistent with ``_assess_numeric``) — its threshold value has no
    effect; only its label is returned when no rank > 1 breakpoint is met. When
    no rank-1 label is configured, the caller defaults to ``'susceptible'``.
    """
    total = drug_data['score_total']
    for rank, threshold, label in reversed(_severity_breakpoints(thresholds)):
        if rank == 1:
            continue
        if total >= threshold:
            return label
    rank1_labels = [label for label in thresholds if label_to_rank(label) == 1]
    if rank1_labels:
        return str(rank1_labels[0])
    if drug_data['hit_count'] > 0:
        return 'susceptible'
    return ''


def _assess_by_ic50(drug_data: dict, thresholds: dict) -> str:
    ic50_values = drug_data['ic50_values']
    if not ic50_values:
        return ''
    return _assess_numeric(ic50_values, thresholds)


def _assess_by_fold_ic50(drug_data: dict, thresholds: dict) -> str:
    fold_ic50_values = drug_data['fold_ic50_values']
    if not fold_ic50_values:
        return ''
    return _assess_numeric(fold_ic50_values, thresholds)


def _assess_numeric(values: list[float], thresholds: dict) -> str:
    """Assess numeric values against label-keyed breakpoints; return strongest matched label.

    Iterates severity breakpoints strongest-first (by ``(rank, threshold)``,
    not label string) and returns the first rank > 1 label whose threshold any
    value meets. When no severity-rank > 1 breakpoint is met, the configured
    rank-1 label is returned (e.g. ``susceptible``). If no rank-1 label is
    configured (unvalidated legacy config), fall back to the canonical rank-1
    label ``'susceptible'``.
    """
    for rank, threshold, label in reversed(_severity_breakpoints(thresholds)):
        if rank == 1:
            continue
        if any(value >= threshold for value in values):
            return label
    rank1_labels = [label for label in thresholds if label_to_rank(label) == 1]
    if rank1_labels:
        return str(rank1_labels[0])
    return 'susceptible'
