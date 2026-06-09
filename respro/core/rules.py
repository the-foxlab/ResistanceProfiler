"""
Resistance rule matching — load rules from the project database and match against annotated variants.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

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
    for ann in annotations:
        if not ann.feature_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue

        key = (ann.feature_name, ann.codon_pos)
        candidates = rule_index.get(key, [])
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

            if _matches_rule_alleles(
                reference=rule.reference,
                mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
            ):
                ann.rule_matches.append(rule)
                hit_count += 1

        # Suppress INS_any when a specific insertion rule fires for the same position+drug.
        _suppress_ins_any_when_specific_fires(ann.rule_matches)

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


def match_formula_rules(
    annotations: list[AnnotatedVariant],
    formula_rules: list[FormulaRuleRuntime],
    member_af_threshold: float | None = None,
) -> list[FormulaRuleHit]:
    """
    Evaluate formula rules over AF-gated matched atomic member_ids.

    The AF gate uses strict '>' semantics to keep compatibility with prior combo behavior.
    """
    if not formula_rules:
        return []

    threshold = float(member_af_threshold) if member_af_threshold is not None else 0.75

    best_ann_by_member: dict[str, AnnotatedVariant] = {}
    for ann in annotations:
        if ann.variant.allele_freq <= threshold:
            continue
        for rule in ann.rule_matches:
            if not rule.external_id:
                continue
            existing = best_ann_by_member.get(rule.external_id)
            if existing is None:
                best_ann_by_member[rule.external_id] = ann
                continue
            if ann.variant.allele_freq > existing.variant.allele_freq:
                best_ann_by_member[rule.external_id] = ann
                continue
            if ann.variant.allele_freq == existing.variant.allele_freq:
                ann_key = (ann.feature_name, ann.codon_pos, ann.alt_aa)
                existing_key = (existing.feature_name, existing.codon_pos, existing.alt_aa)
                if ann_key < existing_key:
                    best_ann_by_member[rule.external_id] = ann

    member_truth = {member_id: True for member_id in best_ann_by_member}
    member_af_map = {
        member_id: ann.variant.allele_freq for member_id, ann in best_ann_by_member.items()
    }

    hits: list[FormulaRuleHit] = []
    for formula in formula_rules:
        is_true, contributing_ids = _evaluate_formula_expression(
            formula.normalized_expression,
            member_truth,
            member_af_map,
        )
        if not is_true:
            continue

        matched_ids = sorted(contributing_ids)
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
            )
        )

    logger.info('Matched %d formula rule hit(s) across %d annotation(s)', len(hits), len(annotations))
    return hits


def _evaluate_formula_expression(
    expression: str,
    member_truth: dict[str, bool],
    member_af_map: dict[str, float],
) -> tuple[bool, set[str]]:
    """Evaluate one normalized expression and return (truth, contributing member_ids)."""
    tokens = _tokenize_formula_expression(expression)
    index = 0

    def _score(contributors: set[str]) -> tuple[float, tuple[str, ...]]:
        if not contributors:
            return (0.0, tuple())
        max_af = max(member_af_map.get(member_id, 0.0) for member_id in contributors)
        lexical = tuple(sorted(contributors))
        return (max_af, lexical)

    def parse_primary() -> tuple[bool, set[str]]:
        nonlocal index
        if index >= len(tokens):
            raise ValueError('unexpected end of expression')
        token = tokens[index]
        if token == '(':
            index += 1
            value, contributors = parse_or_expression()
            if index >= len(tokens) or tokens[index] != ')':
                raise ValueError('unbalanced parentheses')
            index += 1
            return value, contributors
        if token.upper() in {'AND', 'OR', 'NOT', 'XOR', ')'}:
            raise ValueError(f'unexpected token {token!r}')
        index += 1
        is_true = bool(member_truth.get(token, False))
        return is_true, ({token} if is_true else set())

    def parse_not_expression() -> tuple[bool, set[str]]:
        nonlocal index
        if index < len(tokens) and tokens[index].upper() == 'NOT':
            index += 1
            value, _contributors = parse_not_expression()
            # NOT contributes no positive evidence ids by design.
            return (not value), set()
        return parse_primary()

    def parse_and_expression() -> tuple[bool, set[str]]:
        nonlocal index
        value, contributors = parse_not_expression()
        while index < len(tokens) and tokens[index].upper() == 'AND':
            index += 1
            right_value, right_contributors = parse_not_expression()
            value = value and right_value
            contributors = contributors | right_contributors if value else set()
        return value, contributors

    def parse_xor_expression() -> tuple[bool, set[str]]:
        nonlocal index
        value, contributors = parse_and_expression()
        while index < len(tokens) and tokens[index].upper() == 'XOR':
            index += 1
            right_value, right_contributors = parse_and_expression()
            xor_true = (value and not right_value) or (right_value and not value)
            if xor_true:
                contributors = contributors if value else right_contributors
            else:
                contributors = set()
            value = xor_true
        return value, contributors

    def parse_or_expression() -> tuple[bool, set[str]]:
        nonlocal index
        value, contributors = parse_xor_expression()
        while index < len(tokens) and tokens[index].upper() == 'OR':
            index += 1
            right_value, right_contributors = parse_xor_expression()
            if value and right_value:
                # Deterministic branch preference by AF and lexical member_id order.
                left_score = _score(contributors)
                right_score = _score(right_contributors)
                if left_score[0] > right_score[0]:
                    contributors = contributors
                elif right_score[0] > left_score[0]:
                    contributors = right_contributors
                else:
                    contributors = contributors if left_score[1] <= right_score[1] else right_contributors
                value = True
            elif right_value:
                value = True
                contributors = right_contributors
            # else: keep left side as-is
        return value, contributors

    result, contributors = parse_or_expression()
    if index != len(tokens):
        raise ValueError('unexpected trailing tokens')
    return result, contributors


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
