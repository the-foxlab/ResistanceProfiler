"""Resistance rule matching — evaluate atomic rules and Fréchet-gated boolean formulas."""

from __future__ import annotations

import logging

from respro.core.formula import parse_formula_ast, tokenize_formula_expression
from respro.core.frechet import DEFAULT_FRECHET_EPS, frechet_and_bound
from respro.db.models import (
    AnnotatedVariant,
    FormulaRuleHit,
    ResistanceRule,
    ResistanceRuleSet,
)

logger = logging.getLogger(__name__)


def _collect_effect_candidates(ann: AnnotatedVariant) -> list[tuple[str, float]]:
    """Return ``(alt_aa, aa_freq)`` effects eligible for rule matching on ``ann``."""
    candidates: list[tuple[str, float]] = []
    # Single-exchange effect is gated on amino-acid frequency (not nucleotide AF),
    # so a Fréchet-impossible single state (0.0) cannot fire a single-AA rule.
    if ann.alt_aa and ann.consequence != 'synonymous' and ann.single_exchange_aa_freq > 0.0:
        candidates.append((ann.alt_aa, ann.single_exchange_aa_freq))
    for state in ann.combined_states:
        if state.accepted and state.lower > 0.0 and state.alt_aa and state.alt_aa != '?':
            candidates.append((state.alt_aa, state.lower))
    return candidates


def _find_best_effect_match(
    ann: AnnotatedVariant,
    rule: ResistanceRule,
    effect_candidates: list[tuple[str, float]],
) -> tuple[str, float] | None:
    """Return the winning ``(matched_alt, aa_freq)`` for ``rule``, preferring combined states."""
    matched: tuple[str, float] | None = None
    for eff_alt, eff_lower in effect_candidates:
        eff_consequence = ann.consequence if eff_alt == ann.alt_aa else 'missense'
        if not _matches_rule_alleles(
            reference=rule.reference,
            mutation=rule.mutation,
            ann_ref=ann.ref_aa,
            ann_alt=eff_alt,
            ann_consequence=eff_consequence,
        ):
            continue
        matched = (eff_alt, eff_lower)
        if eff_alt != ann.alt_aa:
            break  # Combined-state hit takes precedence over single-exchange frequency.
    return matched


def match_rules(
    annotations: list[AnnotatedVariant],
    rules: list[ResistanceRule],
) -> list[AnnotatedVariant]:
    """Match annotated variants against atomic resistance rules, populating ``rule_matches`` in place."""
    rule_index: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        rule_index.setdefault((rule.feature_name, rule.position), []).append(rule)

    hit_count = 0
    warned_anchors: set[str] = set()
    # Deduplicate combined-codon hits across sibling member-SNP rows that share one AA effect.
    matched_combined_effects: set[tuple[str, str, int, str, int]] = set()

    for ann in annotations:
        candidates = rule_index.get((ann.feature_name, ann.codon_pos))
        if not candidates:
            continue
        effect_candidates = _collect_effect_candidates(ann)
        if not effect_candidates:
            continue

        for rule in candidates:
            warning = _indel_anchor_mismatch_warning(
                rule_reference=rule.reference,
                rule_mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
                feature_name=ann.feature_name,
                codon_pos=ann.codon_pos,
            )
            if warning and warning not in warned_anchors:
                logger.warning(warning)
                warned_anchors.add(warning)

            matched = _find_best_effect_match(ann, rule, effect_candidates)
            if matched is None:
                continue

            matched_alt, matched_lower = matched
            combined_key = (ann.variant.chrom, ann.feature_name, ann.codon_pos, matched_alt, rule.id)
            if ann.is_combined_codon_event and combined_key in matched_combined_effects:
                continue

            ann.rule_matches.append(rule)
            ann.rule_effect_aa_freq[rule.id] = matched_lower
            ann.rule_effect_alt[rule.id] = matched_alt
            if ann.is_combined_codon_event:
                matched_combined_effects.add(combined_key)
            hit_count += 1

        _suppress_ins_any_when_specific_fires(ann.rule_matches)

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


def _index_best_member_hits(
    annotations: list[AnnotatedVariant],
    eps: float,
) -> tuple[dict[str, AnnotatedVariant], dict[str, ResistanceRule], dict[str, float]]:
    """Select the highest-frequency matched annotation/rule per atomic ``external_id``."""
    best_ann: dict[str, AnnotatedVariant] = {}
    best_rule: dict[str, ResistanceRule] = {}
    member_lower: dict[str, float] = {}

    for ann in annotations:
        for rule in ann.rule_matches:
            if not rule.external_id:
                continue
            effect_lower = ann.rule_effect_aa_freq.get(rule.id, 0.0)
            if effect_lower <= eps:
                continue
            existing = best_ann.get(rule.external_id)
            if existing is not None:
                prev_lower = member_lower[rule.external_id]
                if effect_lower < prev_lower:
                    continue
                if effect_lower == prev_lower and (
                    (ann.feature_name, ann.codon_pos, ann.alt_aa)
                    >= (existing.feature_name, existing.codon_pos, existing.alt_aa)
                ):
                    continue
            best_ann[rule.external_id] = ann
            best_rule[rule.external_id] = rule
            member_lower[rule.external_id] = effect_lower

    return best_ann, best_rule, member_lower


def match_formula_rules(
    annotations: list[AnnotatedVariant],
    formula_rules: list[ResistanceRuleSet],
    min_fraction: float,
    eps: float = DEFAULT_FRECHET_EPS,
) -> list[FormulaRuleHit]:
    """Evaluate Fréchet-gated boolean formula rules against matched atomic member IDs."""
    if not formula_rules:
        return []

    best_ann_by_member, best_rule_by_member, member_lower = _index_best_member_hits(annotations, eps)

    hits: list[FormulaRuleHit] = []
    for formula in formula_rules:
        is_true, contributing_ids, frechet_lower, forced_fraction = _evaluate_formula_expression(
            formula.logic_expression, member_lower, min_fraction, eps,
        )
        if not is_true:
            continue

        matched_ids = sorted(contributing_ids)
        if len(matched_ids) == 1 and _is_pure_or_expression(formula.logic_expression):
            # Suppress a pure-OR single-member hit when that atomic rule already fired for this drug.
            fired_rule = best_rule_by_member.get(matched_ids[0])
            if (
                fired_rule is not None
                and not fired_rule.is_internal_formula_component
                and fired_rule.drug_id == formula.drug_id
            ):
                continue

        matched_variants = [best_ann_by_member[mid] for mid in matched_ids if mid in best_ann_by_member]
        hits.append(
            FormulaRuleHit(
                rule_set=formula,
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
    """Return True when ``expression`` uses no AND, XOR, or NOT operators."""
    return not any(tok in {'AND', 'XOR', 'NOT'} for tok in tokenize_formula_expression(expression))


def _evaluate_formula_expression(
    expression: str,
    member_lower: dict[str, float],
    min_fraction: float,
    eps: float,
) -> tuple[bool, set[str], float, float]:
    """Evaluate a formula expression AST under Fréchet gating.

    Returns ``(accepted, contributors, lower, forced_fraction)``.
    """
    ast_node, _ = parse_formula_ast(expression)

    def _branch_rank(contributors: set[str]) -> tuple[float, tuple[str, ...]]:
        if not contributors:
            return (0.0, ())
        max_lower = max(member_lower.get(mid, 0.0) for mid in contributors)
        return (max_lower, tuple(sorted(contributors)))

    def _eval(node: tuple) -> tuple[bool, set[str], float, float]:
        kind = node[0]
        if kind == 'ATOM':
            token = node[1]
            lower = member_lower.get(token, 0.0)
            is_true = lower > eps
            return is_true, ({token} if is_true else set()), lower, 1.0

        if kind == 'NOT':
            val, _, _, _ = _eval(node[1])
            return (not val), set(), 0.0, 0.0

        results = [_eval(child) for child in node[1]]

        if kind == 'AND':
            if not all(val for val, _, _, _ in results):
                return False, set(), 0.0, 0.0
            contributors = set().union(*(c for _, c, _, _ in results))
            if not contributors:
                return True, set(), 0.0, 0.0
            q_values = [member_lower.get(mid, 0.0) for mid in contributors]
            lower, _upper, ff, accepted = frechet_and_bound(q_values, min_fraction, eps)
            return accepted, (contributors if accepted else set()), lower, ff

        accepted_nodes = [(c, lo, ff) for val, c, lo, ff in results if val]

        if kind == 'XOR':
            if len(accepted_nodes) % 2 == 0:
                return False, set(), 0.0, 0.0
            if len(accepted_nodes) == 1:
                return True, *accepted_nodes[0]
            union = set().union(*(c for c, _, _ in accepted_nodes))
            if not union:
                return True, set(), 0.0, 0.0
            q_values = [member_lower.get(mid, 0.0) for mid in union]
            lower, _upper, ff, accepted = frechet_and_bound(q_values, min_fraction, eps)
            return (True, union, lower, ff) if accepted else (False, set(), 0.0, 0.0)

        # OR: deterministic winner by highest contributor lower bound, then lexical tie-break.
        if not accepted_nodes:
            return False, set(), 0.0, 0.0
        best_c, best_lo, best_ff = accepted_nodes[0]
        best_rank = _branch_rank(best_c)
        for c, lo, ff in accepted_nodes[1:]:
            rank = _branch_rank(c)
            if rank[0] > best_rank[0] or (rank[0] == best_rank[0] and rank[1] < best_rank[1]):
                best_c, best_lo, best_ff, best_rank = c, lo, ff, rank
        return True, best_c, best_lo, best_ff

    return _eval(ast_node)


def _suppress_ins_any_when_specific_fires(rule_matches: list[ResistanceRule]) -> None:
    """Drop ``INS_any`` wildcard hits when a specific insertion rule fired for the same position+drug."""
    specific_keys = {
        (rule.position, rule.drug_id)
        for rule in rule_matches
        if rule.mutation != 'INS_any' and len(rule.mutation) > len(rule.reference)
    }
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
    """Return True when one annotation allele pair satisfies a rule allele pair."""
    if ann_consequence == 'frameshift':
        return _is_frameshift_token(mutation) and _is_frameshift_token(ann_alt)
    if mutation == 'INS_any':
        return ann_consequence == 'insertion'
    if ann_consequence == 'insertion' and len(mutation) > len(reference):
        return _indel_payload(reference, mutation) == _indel_payload(ann_ref, ann_alt)
    if ann_consequence == 'deletion' and len(reference) > len(mutation):
        return _indel_payload(mutation, reference) == _indel_payload(ann_alt, ann_ref)
    if len(reference) == 1 and len(mutation) == 1:
        return ann_alt == mutation
    if len(mutation) > len(reference):
        return ann_alt == mutation
    if len(reference) > len(mutation):
        return ann_ref == reference
    return ann_ref == reference and ann_alt == mutation


def _indel_payload(short_allele: str, long_allele: str) -> str:
    """Return the inserted or deleted AA payload between an anchor allele and its longer counterpart."""
    payload_len = len(long_allele) - len(short_allele)
    if payload_len <= 0:
        return ''
    if long_allele.startswith(short_allele):
        return long_allele[len(short_allele):]
    return long_allele[-payload_len:]


def _is_frameshift_token(token: str) -> bool:
    """Return True for canonical or anchored frameshift tokens (``fsX`` / ``KfsX``)."""
    upper = token.upper()
    return upper == 'FSX' or (upper.endswith('FSX') and len(upper) == 4)


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
    """Return a warning message when a matched-position indel has a different anchor AA."""
    if ann_consequence == 'insertion' and len(rule_mutation) > len(rule_reference) and ann_ref != rule_reference:
        return (
            f'Indel anchor mismatch at {feature_name}:{codon_pos + 1} (insertion): '
            f'rule anchor {rule_reference!r} vs observed anchor {ann_ref!r}. '
            'Matching by inserted payload only.'
        )
    if ann_consequence == 'deletion' and len(rule_reference) > len(rule_mutation) and ann_alt != rule_mutation:
        return (
            f'Indel anchor mismatch at {feature_name}:{codon_pos + 1} (deletion): '
            f'rule anchor {rule_mutation!r} vs observed anchor {ann_alt!r}. '
            'Matching by deleted payload only.'
        )
    return None
