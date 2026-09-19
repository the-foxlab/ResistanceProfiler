"""
Resistance rule matching — load rules from the project database and match against annotated variants.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from respro.config.cli_settings import CLI_CONFIG
from respro.db._rules_formula import _tokenize_formula_expression
from respro.db._rules_publication import _report_publication_lookup_failures
from respro.db.models import (
    AnnotatedVariant,
    FormulaRuleHit,
    FormulaRuleRuntime,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
)
from respro.db.rules_import import (
    load_formula_rules as _db_load_formula_rules,
)
from respro.db.rules_import import (
    load_resistance_rules as _db_load_resistance_rules,
)

logger = logging.getLogger(__name__)

# Import-time default for the Fréchet-bound acceptance tolerance on the formula
# path. Callers thread the per-invocation value ([matching] frechet_epsilon)
# through match_formula_rules(eps=...); this alias is the fallback default and
# is kept for backward-compat with annotation.py's re-export and tests.
_FRECHET_EPS = CLI_CONFIG.codon.frechet_epsilon


def _frechet_and_bound(
    q_values: list[float],
    min_fraction: float,
    eps: float = _FRECHET_EPS,
) -> tuple[float, float, float, bool]:
    """
    Compute the sharp Fréchet intersection bounds for an AND clause of members.

    Uses the identical formula as the same-codon policy in
    ``respro.core.combined_snp._compute_codon_frechet_states``:

    - ``lower = max(0, sum(q) - (k-1))`` — minimum guaranteed co-occurrence.
    - ``upper = min(q)`` — maximum possible co-occurrence.
    - ``forced_fraction = lower / upper`` (0 when ``upper == 0``) — the minimum
      fraction of the rarest member's carriers that must carry all other members.

    A clause is ``accepted`` when ``lower > eps`` and
    ``forced_fraction >= min_fraction - eps``.

    :param q_values: member frequencies (matched effect lower bounds), length >= 1
    :param min_fraction: forced-fraction acceptance threshold (policy knob,
        ``[matching] min_cooccurrence_combination_fraction``)
    :param eps: numerical tolerance shared with the codon path
    :return: ``(lower, upper, forced_fraction, accepted)``
    :raises ValueError: when ``q_values`` is empty
    """
    if not q_values:
        raise ValueError('AND clause requires at least one member frequency')
    k = len(q_values)
    lower = max(0.0, sum(q_values) - (k - 1))
    upper = min(q_values)
    # Clamp: lower <= upper always holds in exact arithmetic, but float
    # summation (e.g. 0.6 + 0.6) can push lower above upper by 1 ULP.
    lower = min(lower, upper)
    forced_fraction = lower / upper if upper > 0 else 0.0
    accepted = lower > eps and forced_fraction >= min_fraction - eps
    return lower, upper, forced_fraction, accepted


def import_rules_with_summary(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    *,
    formula_rules_tsv: Path | None = None,
    additional_info: bool,
) -> dict[str, int]:
    """
    Import rules TSV and return inserted row counts.

    :param conn: open project DB connection
    :param project_id: current project id
    :param rules_tsv: rules TSV path
    :param additional_info: enable external metadata lookups during import
    :return: summary counts for inserted rows
    """
    before_formula_rules = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0])
    before_formula_members = int(
        conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0]
    )
    publication_lookup_failures: list[str] = []

    single_rules, declared_external_ids, skipped_external_ids = _db_load_resistance_rules(
        conn,
        project_id,
        rules_tsv,
        require_external_ids=formula_rules_tsv is not None,
        additional_info=additional_info,
        publication_lookup_failures=publication_lookup_failures,
    )
    if formula_rules_tsv is not None:
        _db_load_formula_rules(
            conn,
            project_id,
            formula_rules_tsv,
            declared_atomic_ids=declared_external_ids,
            skipped_atomic_ids=skipped_external_ids,
            additional_info=additional_info,
            publication_lookup_failures=publication_lookup_failures,
        )

    _report_publication_lookup_failures(publication_lookup_failures)

    after_formula_rules = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0])
    after_formula_members = int(
        conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0]
    )

    return {
        'single_rules': int(single_rules),
        'formula_rules': max(0, after_formula_rules - before_formula_rules),
        'formula_rule_members': max(0, after_formula_members - before_formula_members),
    }


def validate_rules_tsv(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    *,
    formula_rules_tsv: Path | None = None,
) -> dict[str, int]:
    """
    Validate a rules TSV by running the real import pipeline in a rolled-back savepoint.

    :param conn: open project DB connection
    :param project_id: current project id
    :param rules_tsv: rules TSV path
    :return: summary counts produced by the validation pass
    """
    conn.execute('SAVEPOINT rules_validate')
    try:
        summary = import_rules_with_summary(
            conn,
            project_id,
            rules_tsv,
            formula_rules_tsv=formula_rules_tsv,
            additional_info=False,
        )
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT rules_validate')
        conn.execute('RELEASE SAVEPOINT rules_validate')
    return summary


def match_rules(
    annotations: list[AnnotatedVariant],
    rules: list[ResistanceRule],
) -> list[AnnotatedVariant]:
    """
    Match annotated variants against resistance rules.

    Mutates the ``rule_matches`` attribute of each AnnotatedVariant in place
    and returns the same list.

    :param annotations: list of annotated variants
    :param rules: list of resistance rules to match against
    :return: the same annotations list with rule_matches populated
    """
    # Build a lookup by feature and codon position.
    rule_index: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        key = (rule.feature_name, rule.position)
        rule_index.setdefault(key, []).append(rule)

    hit_count = 0
    anchor_warning_cache: set[str] = set()
    # A combined-codon state is emitted on every member-SNP annotation so each
    # SNP can display its possible effects. When two members emit the same
    # amino-acid effect, a direct rule against that effect must still fire once.
    # Track rule/effect identities per combined codon independently of the
    # annotation which carried the effect.
    matched_combined_effects: set[tuple[str, str, int, str, int]] = set()
    for ann in annotations:
        key = (ann.feature_name, ann.codon_pos)
        candidates = rule_index.get(key, [])
        if not candidates:
            continue

        # Build the list of (alt_aa, amino_acid_frequency) pairs to match
        # against. Resistance rules key on amino-acid changes, so the relevant
        # frequency is the amino-acid frequency (population share of the exact
        # codon), NOT the nucleotide frequency (marginal allele frequency of the
        # SNP). The single-exchange candidate is gated by its
        # single_exchange_aa_freq — the amino-acid frequency of the single-exchange
        # codon. A combined member whose single-exchange amino acid is
        # Fréchet-impossible (lower=0) is guaranteed absent as a single exchange
        # and must not match a single-AA rule — only its combined states can
        # fire. Each Fréchet-accepted combined state adds its own alt_aa gated
        # by its ``lower`` (also an amino-acid frequency).
        effect_candidates: list[tuple[str, float]] = []
        if ann.alt_aa and ann.consequence != 'synonymous' and ann.single_exchange_aa_freq > 0.0:
            effect_candidates.append((ann.alt_aa, ann.single_exchange_aa_freq))
        for state in ann.combined_states:
            if state.accepted and state.lower > 0.0 and state.alt_aa and state.alt_aa != '?':
                effect_candidates.append((state.alt_aa, state.lower))

        if not effect_candidates:
            continue

        for rule in candidates:
            anchor_warning = _indel_anchor_mismatch_warning(
                rule_reference=rule.reference,
                rule_mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
                feature_name=ann.feature_name,
                codon_pos=ann.codon_pos,
            )
            if anchor_warning and anchor_warning not in anchor_warning_cache:
                logger.warning(anchor_warning)
                anchor_warning_cache.add(anchor_warning)

            # Match against the single-exchange effect first, then combined states.
            # A combined-state hit takes precedence for the display frequency: the
            # combinatorial effect's Fréchet lower bound is what the report shows.
            matched_lower: float | None = None
            matched_alt: str | None = None
            for eff_alt, eff_lower in effect_candidates:
                # Combined states use missense consequence for matching; the single
                # keeps its own consequence (e.g. frameshift, insertion).
                eff_consequence = ann.consequence if eff_alt == ann.alt_aa else 'missense'
                if _matches_rule_alleles(
                    reference=rule.reference,
                    mutation=rule.mutation,
                    ann_ref=ann.ref_aa,
                    ann_alt=eff_alt,
                    ann_consequence=eff_consequence,
                ):
                    matched_lower = eff_lower
                    matched_alt = eff_alt
                    if eff_alt != ann.alt_aa:
                        break  # combined-state hit found; stop searching

            if matched_lower is not None:
                # Group by the feature's codon index, not `pos // 3` — raw
                # genomic/reference position is not frame-aligned, so two SNPs
                # of the same codon can floor-divide into different buckets
                # (e.g. positions 4193 and 4195 of the same codon: 1397 vs 1398).
                combined_effect_key = (
                    ann.variant.chrom,
                    ann.feature_name,
                    ann.codon_pos,
                    matched_alt if matched_alt is not None else ann.alt_aa,
                    rule.id,
                )
                if ann.is_combined_codon_event and combined_effect_key in matched_combined_effects:
                    continue
                ann.rule_matches.append(rule)
                ann.rule_effect_aa_freq[rule.id] = matched_lower
                ann.rule_effect_alt[rule.id] = matched_alt if matched_alt is not None else ann.alt_aa
                if ann.is_combined_codon_event:
                    matched_combined_effects.add(combined_effect_key)
                hit_count += 1

        # Suppress INS_any when a specific insertion rule fires for the same position+drug.
        _suppress_ins_any_when_specific_fires(ann.rule_matches)

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


def match_formula_rules(
    annotations: list[AnnotatedVariant],
    formula_rules: list[FormulaRuleRuntime],
    min_fraction: float,
    eps: float = _FRECHET_EPS,
) -> list[FormulaRuleHit]:
    """
    Evaluate formula rules over Fréchet-gated matched atomic member_ids.

    A member contributes when its matched effect lower bound
    (``rule_effect_aa_freq``) exceeds eps — the same amino-acid-frequency basis
    single rules use, not the nucleotide ``allele_freq``. An AND clause of
    members fires when the joint Fréchet bound (``_frechet_and_bound``) is
    accepted at ``min_fraction``; OR/XOR/NOT follow the operator semantics in
    ``_evaluate_formula_expression``. Each hit records the firing clause's
    guaranteed lower bound (``frechet_lower``), its ``forced_fraction``, and the
    number of contributing members.

    :param eps: numerical tolerance for Fréchet-bound acceptance; callers load
        it from the per-invocation config (``[matching] frechet_epsilon``)
        rather than relying on the import-time module default.
    """
    if not formula_rules:
        return []

    best_ann_by_member: dict[str, AnnotatedVariant] = {}
    best_rule_by_member: dict[str, ResistanceRule] = {}
    member_lower: dict[str, float] = {}
    for ann in annotations:
        for rule in ann.rule_matches:
            if not rule.external_id:
                continue
            effect_lower = ann.rule_effect_aa_freq.get(rule.id, 0.0)
            if effect_lower <= eps:
                continue
            existing = best_ann_by_member.get(rule.external_id)
            if existing is None:
                best_ann_by_member[rule.external_id] = ann
                best_rule_by_member[rule.external_id] = rule
                member_lower[rule.external_id] = effect_lower
                continue
            existing_lower = member_lower[rule.external_id]
            if effect_lower > existing_lower:
                best_ann_by_member[rule.external_id] = ann
                best_rule_by_member[rule.external_id] = rule
                member_lower[rule.external_id] = effect_lower
                continue
            if effect_lower == existing_lower:
                ann_key = (ann.feature_name, ann.codon_pos, ann.alt_aa)
                existing_key = (existing.feature_name, existing.codon_pos, existing.alt_aa)
                if ann_key < existing_key:
                    best_ann_by_member[rule.external_id] = ann
                    best_rule_by_member[rule.external_id] = rule
                    member_lower[rule.external_id] = effect_lower

    hits: list[FormulaRuleHit] = []
    for formula in formula_rules:
        is_true, contributing_ids, frechet_lower, forced_fraction = (
            _evaluate_formula_expression(
                formula.normalized_expression, member_lower, min_fraction, eps,
            )
        )
        if not is_true:
            continue

        matched_ids = sorted(contributing_ids)
        if len(matched_ids) == 1 and _is_pure_or_expression(formula.normalized_expression):
            # A pure-OR formula (no AND/XOR/NOT) that fires through exactly one
            # member is logically equivalent to that member's own atomic rule
            # — but only when that atomic rule actually fired independently.
            # A member is not required to have its own single rule for this
            # drug at all (e.g. a formula-only member with no matching
            # antiviral row): fired_rule is None in that case, or its own
            # rule_matches never gained an entry, so no suppression happens.
            fired_rule = best_rule_by_member.get(matched_ids[0])
            if (
                fired_rule is not None
                and not fired_rule.is_internal_formula_component
                and fired_rule.drug_id == formula.drug_id
            ):
                # The atomic rule already reports this exact hit via
                # match_rules; firing the formula too would double-count it.
                continue

        members = []
        for idx, member_id in enumerate(sorted(formula.member_rules), start=1):
            member_rule = formula.member_rules[member_id]
            members.append(
                ResistanceRuleSetMember(
                    id=idx,
                    rule_set_id=formula.id,
                    feature_name=member_rule.feature_name,
                    feature_id=member_rule.feature_id,
                    reference_identifier=member_rule.reference_identifier,
                    position=member_rule.position,
                    reference=member_rule.reference,
                    mutation=member_rule.mutation,
                    external_id=member_id,
                )
            )

        rule_set = ResistanceRuleSet(
            id=formula.id,
            drug_name=formula.drug_name,
            drug_id=formula.drug_id,
            phenotype=formula.phenotype,
            clinical_phenotype=formula.clinical_phenotype,
            ic50=formula.ic50,
            fold_ic50=formula.fold_ic50,
            score=formula.score,
            source=formula.source,
            group_name=formula.label or formula.formula_id,
            pubchem_url=formula.pubchem_url,
            description=formula.description,
            comment=formula.comment,
            logic_expression=formula.normalized_expression,
            publications=formula.publications,
            members=members,
        )

        matched_variants = [best_ann_by_member[mid] for mid in matched_ids if mid in best_ann_by_member]
        hits.append(
            FormulaRuleHit(
                rule_set=rule_set,
                matched_variants=matched_variants,
                matched_member_ids=matched_ids,
                frechet_lower=frechet_lower,
                forced_fraction=forced_fraction,
                member_count=len(matched_ids),
            )
        )

    logger.info('Matched %d formula rule hit(s) across %d annotation(s)', len(hits), len(annotations))
    return hits


def _is_pure_or_expression(expression: str) -> bool:
    """Return True when a normalized formula expression is a plain OR of atoms.

    Used to scope the single-member-duplicate guardrail (see
    ``match_formula_rules``) to formulas whose only operator is OR — AND/XOR/NOT
    change the claim's meaning (e.g. co-occurrence or absence), so a single
    contributing member there is not equivalent to that member's atomic rule.
    """
    tokens = _tokenize_formula_expression(expression)
    return not any(token.upper() in {'AND', 'XOR', 'NOT'} for token in tokens)


def _evaluate_formula_expression(
    expression: str,
    member_lower: dict[str, float],
    min_fraction: float,
    eps: float,
) -> tuple[bool, set[str], float, float]:
    """Evaluate one normalized expression under Fréchet gating.

    Nodes return ``(accepted, contributors, lower, forced_fraction)``:

    - AND: Fréchet joint bound over positive members (``_frechet_and_bound``).
      NOT sub-branches invert presence only and contribute no frequency.
    - OR: the accepted child with the highest ``lower`` (lexical tie-break).
    - XOR: parity fold — fires when an odd number of operands are accepted;
      frequency = the winning operand's ``lower`` when exactly one, else the
      Fréchet AND-bound over all accepted operands' lowers.
    - NOT: pure boolean inversion, never carries a frequency.
    """
    tokens = _tokenize_formula_expression(expression)
    index = 0

    def _score(contributors: set[str]) -> tuple[float, tuple[str, ...]]:
        if not contributors:
            return (0.0, tuple())
        max_lower = max(member_lower.get(member_id, 0.0) for member_id in contributors)
        lexical = tuple(sorted(contributors))
        return (max_lower, lexical)

    def parse_primary() -> tuple[bool, set[str], float, float]:
        nonlocal index
        if index >= len(tokens):
            raise ValueError('unexpected end of expression')
        token = tokens[index]
        if token == '(':
            index += 1
            value, contributors, lower, ff = parse_or_expression()
            if index >= len(tokens) or tokens[index] != ')':
                raise ValueError('unbalanced parentheses')
            index += 1
            return value, contributors, lower, ff
        if token.upper() in {'AND', 'OR', 'NOT', 'XOR', ')'}:
            raise ValueError(f'unexpected token {token!r}')
        index += 1
        lower = member_lower.get(token, 0.0)
        is_true = lower > eps
        return is_true, ({token} if is_true else set()), lower, 1.0

    def parse_not_expression() -> tuple[bool, set[str], float, float]:
        nonlocal index
        if index < len(tokens) and tokens[index].upper() == 'NOT':
            index += 1
            value, _contributors, _lower, _ff = parse_not_expression()
            # NOT contributes no positive evidence ids or frequency by design.
            return (not value), set(), 0.0, 0.0
        return parse_primary()

    def parse_and_expression() -> tuple[bool, set[str], float, float]:
        nonlocal index
        value, contributors, lower, ff = parse_not_expression()
        while index < len(tokens) and tokens[index].upper() == 'AND':
            index += 1
            right_value, right_contributors, right_lower, right_ff = parse_not_expression()
            if value and right_value:
                # Joint Fréchet bound over the accumulated positive members.
                positive_contributors = contributors | right_contributors
                if not positive_contributors:
                    # A true conjunction of only NOT operands has no positive
                    # evidence and therefore reports a zero frequency.
                    value, contributors, lower, ff = True, set(), 0.0, 0.0
                    continue
                q_values = [member_lower.get(mid, 0.0) for mid in positive_contributors]
                new_lower, _upper, new_ff, accepted = _frechet_and_bound(q_values, min_fraction, eps)
                value = accepted
                contributors = positive_contributors if accepted else set()
                lower, ff = new_lower, new_ff
            else:
                value = False
                contributors = set()
                lower, ff = 0.0, 0.0
        return value, contributors, lower, ff

    def parse_xor_expression() -> tuple[bool, set[str], float, float]:
        nonlocal index
        # XOR folds with parity: fires when an ODD number of operands are
        # accepted. The accepted operands are accumulated across the chain so
        # a 3-present chain (even, then odd again) still reports the full
        # present set, not just the last operand.
        value, contributors, lower, ff = parse_and_expression()
        accepted_nodes: list[tuple[set[str], float, float]] = (
            [(contributors, lower, ff)] if value else []
        )
        while index < len(tokens) and tokens[index].upper() == 'XOR':
            index += 1
            right_value, right_contributors, right_lower, right_ff = parse_and_expression()
            if right_value:
                accepted_nodes.append((right_contributors, right_lower, right_ff))
        n = len(accepted_nodes)
        if n % 2 == 0:
            return False, set(), 0.0, 0.0
        if n == 1:
            only_contribs, only_lower, only_ff = accepted_nodes[0]
            return True, only_contribs, only_lower, only_ff
        # Odd with >= 3 accepted: fire on the Fréchet AND-bound over the union
        # of accepted members (conservative guaranteed co-occurrence).
        union: set[str] = set()
        for c, _l, _f in accepted_nodes:
            union |= c
        if not union:
            # An odd chain of true NOT operands has no positive evidence and
            # therefore reports a zero frequency.
            return True, set(), 0.0, 0.0
        q_values = [member_lower.get(mid, 0.0) for mid in union]
        new_lower, _upper, new_ff, accepted = _frechet_and_bound(q_values, min_fraction, eps)
        if not accepted:
            return False, set(), 0.0, 0.0
        return True, union, new_lower, new_ff

    def parse_or_expression() -> tuple[bool, set[str], float, float]:
        nonlocal index
        value, contributors, lower, ff = parse_xor_expression()
        while index < len(tokens) and tokens[index].upper() == 'OR':
            index += 1
            right_value, right_contributors, right_lower, right_ff = parse_xor_expression()
            if value and right_value:
                # Deterministic branch preference by lower and lexical order.
                left_score = _score(contributors)
                right_score = _score(right_contributors)
                if left_score[0] > right_score[0]:
                    pass
                elif right_score[0] > left_score[0]:
                    contributors, lower, ff = right_contributors, right_lower, right_ff
                else:
                    if right_score[1] < left_score[1]:
                        contributors, lower, ff = right_contributors, right_lower, right_ff
                value = True
            elif right_value:
                value, contributors, lower, ff = True, right_contributors, right_lower, right_ff
            # else: keep left side as-is
        return value, contributors, lower, ff

    result, contributors, lower, ff = parse_or_expression()
    if index != len(tokens):
        raise ValueError('unexpected trailing tokens')
    return result, contributors, lower, ff


def _suppress_ins_any_when_specific_fires(rule_matches: list[ResistanceRule]) -> None:
    """Remove INS_any matches when a specific insertion rule fires at the same position+drug."""
    specific_keys: set[tuple[int, int]] = set()
    for rule in rule_matches:
        if rule.mutation != 'INS_any' and len(rule.mutation) > len(rule.reference):
            specific_keys.add((rule.position, rule.drug_id))
    if specific_keys:
        rule_matches[:] = [
            r for r in rule_matches
            if not (r.mutation == 'INS_any' and (r.position, r.drug_id) in specific_keys)
        ]


def _matches_rule_alleles(
    *,
    reference: str,
    mutation: str,
    ann_ref: str,
    ann_alt: str,
    ann_consequence: str = '',
) -> bool:
    """Compare one rule allele pair with one annotation allele pair."""
    if ann_consequence == 'frameshift':
        return _is_frameshift_token(mutation) and _is_frameshift_token(ann_alt)

    # Wildcard insertion rule: match any in-frame insertion.
    if mutation == 'INS_any':
        return ann_consequence == 'insertion'

    # In-frame insertion-like rules are matched by inserted payload only.
    if ann_consequence == 'insertion' and len(mutation) > len(reference):
        return _insertion_payload(reference, mutation) == _insertion_payload(ann_ref, ann_alt)

    # In-frame deletion-like rules are matched by deleted payload only.
    if ann_consequence == 'deletion' and len(reference) > len(mutation):
        return _deletion_payload(reference, mutation) == _deletion_payload(ann_ref, ann_alt)

    # SNP-like rules: compare resulting state (alt AA).
    if len(reference) == 1 and len(mutation) == 1:
        return ann_alt == mutation

    # Insertion-like rules: match by resulting AA state.
    if len(mutation) > len(reference):
        return ann_alt == mutation

    # Deletion-like rules: match by deleted reference block.
    if len(reference) > len(mutation):
        return ann_ref == reference

    # Fallback for rare same-length non-SNP AA rewrites.
    return ann_ref == reference and ann_alt == mutation


def _insertion_payload(reference: str, mutation: str) -> str:
    """Return insertion payload from an anchor+payload allele pair."""
    payload_len = len(mutation) - len(reference)
    if payload_len <= 0:
        return ''
    if mutation.startswith(reference):
        return mutation[len(reference):]
    return mutation[-payload_len:]


def _deletion_payload(reference: str, mutation: str) -> str:
    """Return deleted payload from an anchor+payload allele pair."""
    payload_len = len(reference) - len(mutation)
    if payload_len <= 0:
        return ''
    if reference.startswith(mutation):
        return reference[len(mutation):]
    return reference[-payload_len:]


def _is_frameshift_token(token: str) -> bool:
    """Return True for canonical or anchored frameshift tokens (``fsX`` / ``KfsX``)."""
    token_upper = token.upper()
    return token_upper == 'FSX' or (token_upper.endswith('FSX') and len(token_upper) == 4)


def _indel_anchor_mismatch_warning(
    *,
    rule_reference: str,
    rule_mutation: str,
    ann_ref: str,
    ann_alt: str,
    ann_consequence: str,
    feature_name: str,
    codon_pos: int,
) -> str | None:
    """Return a warning text when a matched-position indel has a different anchor AA."""
    if ann_consequence == 'insertion' and len(rule_mutation) > len(rule_reference):
        if ann_ref != rule_reference:
            return (
                f'Indel anchor mismatch at {feature_name}:{codon_pos + 1} (insertion): '
                f'rule anchor {rule_reference!r} vs observed anchor {ann_ref!r}. '
                'Matching by inserted payload only.'
            )
        return None

    if ann_consequence == 'deletion' and len(rule_reference) > len(rule_mutation):
        if ann_alt != rule_mutation:
            return (
                f'Indel anchor mismatch at {feature_name}:{codon_pos + 1} (deletion): '
                f'rule anchor {rule_mutation!r} vs observed anchor {ann_alt!r}. '
                'Matching by deleted payload only.'
            )
        return None

    return None
