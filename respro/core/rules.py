"""
Resistance rule matching — load rules from the project database and match against annotated variants.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from respro.config.cli_settings import CLI_CONFIG
from respro.db._rules_formula import _tokenize_formula_expression
from respro.db._rules_publication import _report_publication_lookup_failures
from respro.db.models import (
    AnnotatedVariant,
    FormulaRuleHit,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    is_internal_formula_component_drug_name,
)
from respro.db.rules_import import (
    _load_formula_rules,
    _load_resistance_rules,
)

logger = logging.getLogger(__name__)


@dataclass
class FormulaRuleRuntime:
    """Runtime representation of one formula rule and its referenced atomic members."""

    id: int
    formula_id: str
    label: str
    normalized_expression: str
    drug_name: str
    drug_id: int
    phenotype: str
    clinical_phenotype: str
    ic50: str
    fold_ic50: str
    score: str
    source: str
    comment: str
    pubchem_url: str = ''
    description: str = ''
    publications: list[Publication] = field(default_factory=list)
    member_rules: dict[str, ResistanceRule] = field(default_factory=dict)


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

    single_rules, grouped_ids, declared_external_ids, skipped_external_ids = _load_resistance_rules(
        conn,
        project_id,
        rules_tsv,
        require_external_ids=formula_rules_tsv is not None,
        additional_info=additional_info,
        publication_lookup_failures=publication_lookup_failures,
    )
    if formula_rules_tsv is not None:
        _load_formula_rules(
            conn,
            project_id,
            formula_rules_tsv,
            expected_group_ids=grouped_ids,
            declared_atomic_ids=declared_external_ids,
            skipped_atomic_ids=skipped_external_ids,
            additional_info=additional_info,
            publication_lookup_failures=publication_lookup_failures,
        )
    elif grouped_ids:
        logger.warning(
            'Detected grouped atomic rules in rules TSV, but --formula-rules was not provided; '
            'the database was created and atomic rules were imported, while combinatorial rules were ignored'
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


def load_rules(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRule]:
    """
    Load all resistance rules for genes belonging to a reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of ResistanceRule objects
    """
    rows = conn.execute(
        """
        SELECT
            rr.id, g.name AS gene_name, rr.gene_id,
            d.name AS drug_name, rr.drug_id,
            d.pubchem_url, d.description,
            rr.external_id,
            rr.reference_identifier,
            rr.position, rr.reference, rr.mutation,
            rr.phenotype, rr.clinical_phenotype, rr.ic50, rr.fold_ic50, rr.score, rr.source, rr.comment
        FROM resistance_rule rr
        JOIN gene g ON g.id = rr.gene_id
        JOIN drug d ON d.id = rr.drug_id
        WHERE g.reference_id = ?
        ORDER BY g.name, rr.position
        """,
        (reference_id,),
    ).fetchall()

    rules = [_rule_from_row(r) for r in rows]

    if rules:
        _attach_publications_to_rules(conn, rules)

    logger.info('Loaded %d resistance rule(s)', len(rules))
    return rules


def load_formula_rules(conn: sqlite3.Connection, reference_id: int) -> list[FormulaRuleRuntime]:
    """
    Load formula rules for one reference and include only same-reference member rules.

    Formulas that reference at least one atomic rule outside the active reference are
    skipped with a warning.
    """
    formula_rows = conn.execute(
        """
        SELECT DISTINCT
            fr.id,
            fr.formula_id,
            fr.label,
            fr.normalized_expression,
            fr.phenotype,
            fr.clinical_phenotype,
            fr.ic50,
            fr.fold_ic50,
            fr.score,
            fr.source,
            fr.comment,
            d.id AS drug_id,
            d.name AS drug_name,
            d.pubchem_url,
            d.description
        FROM resistance_formula_rule fr
        JOIN drug d ON d.id = fr.drug_id
        JOIN resistance_formula_rule_member frm ON frm.formula_rule_id = fr.id
        JOIN resistance_rule rr ON rr.id = frm.rule_id
        JOIN gene g ON g.id = rr.gene_id
        WHERE g.reference_id = ?
        ORDER BY fr.id
        """,
        (reference_id,),
    ).fetchall()

    if not formula_rows:
        return []

    formulas: dict[int, FormulaRuleRuntime] = {}
    for row in formula_rows:
        formulas[int(row['id'])] = FormulaRuleRuntime(
            id=int(row['id']),
            formula_id=row['formula_id'] or '',
            label=row['label'] or '',
            normalized_expression=row['normalized_expression'] or '',
            drug_name=row['drug_name'] or '',
            drug_id=int(row['drug_id']),
            phenotype=row['phenotype'] or 'unknown',
            clinical_phenotype=row['clinical_phenotype'] or 'unknown',
            ic50=row['ic50'] or '',
            fold_ic50=row['fold_ic50'] or '',
            score=row['score'] or '',
            source=row['source'] or '',
            comment=row['comment'] or '',
            pubchem_url=row['pubchem_url'] or '',
            description=row['description'] or '',
        )

    placeholders = ','.join('?' * len(formulas))
    member_rows = conn.execute(
        f"""
        SELECT
            frm.formula_rule_id,
            rr.id,
            rr.external_id,
            rr.reference_identifier,
            rr.position,
            rr.reference,
            rr.mutation,
            rr.phenotype,
            rr.clinical_phenotype,
            rr.ic50,
            rr.fold_ic50,
            rr.score,
            rr.source,
            rr.comment,
            rr.drug_id,
            d.name AS drug_name,
            d.pubchem_url,
            d.description,
            g.name AS gene_name,
            g.id AS gene_id,
            g.reference_id AS member_reference_id
        FROM resistance_formula_rule_member frm
        JOIN resistance_rule rr ON rr.id = frm.rule_id
        JOIN drug d ON d.id = rr.drug_id
        JOIN gene g ON g.id = rr.gene_id
        WHERE frm.formula_rule_id IN ({placeholders})
        ORDER BY frm.formula_rule_id, rr.external_id, rr.id
        """,
        list(formulas.keys()),
    ).fetchall()

    formulas_with_cross_reference_members: set[int] = set()
    for row in member_rows:
        formula_rule_id = int(row['formula_rule_id'])
        if int(row['member_reference_id']) != reference_id:
            formulas_with_cross_reference_members.add(formula_rule_id)
            continue
        external_id = row['external_id'] or ''
        if external_id == '':
            continue
        formulas[formula_rule_id].member_rules[external_id] = ResistanceRule(
            id=int(row['id']),
            gene_name=row['gene_name'] or '',
            gene_id=int(row['gene_id']),
            drug_name=row['drug_name'] or '',
            drug_id=int(row['drug_id']),
            external_id=external_id,
            reference_identifier=row['reference_identifier'] or '',
            position=int(row['position']),
            reference=row['reference'] or '',
            mutation=row['mutation'] or '',
            phenotype=row['phenotype'] or 'unknown',
            clinical_phenotype=row['clinical_phenotype'] or 'unknown',
            ic50=row['ic50'] or '',
            fold_ic50=row['fold_ic50'] or '',
            score=row['score'] or '',
            source=row['source'] or '',
            comment=row['comment'] or '',
            pubchem_url=row['pubchem_url'] or '',
            description=row['description'] or '',
              is_internal_formula_component=is_internal_formula_component_drug_name(row['drug_name'] or ''),
        )

    if formulas_with_cross_reference_members:
        skipped = sorted(formulas_with_cross_reference_members)
        logger.warning(
            '%d formula rule(s) skipped — cross-reference members are not allowed: %s',
            len(skipped),
            ', '.join(formulas[fid].formula_id for fid in skipped),
        )
        for formula_id in skipped:
            formulas.pop(formula_id, None)

    if formulas:
        _attach_publications_to_formula_rules(conn, list(formulas.values()))

    logger.info('Loaded %d formula rule(s)', len(formulas))
    return list(formulas.values())


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
    # Build a lookup by gene and codon position.
    rule_index: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        key = (rule.gene_name, rule.position)
        rule_index.setdefault(key, []).append(rule)

    hit_count = 0
    anchor_warning_cache: set[str] = set()
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue

        key = (ann.gene_name, ann.codon_pos)
        candidates = rule_index.get(key, [])
        for rule in candidates:
            anchor_warning = _indel_anchor_mismatch_warning(
                rule_reference=rule.reference,
                rule_mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
                gene_name=ann.gene_name,
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

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


def match_rule_sets(
    annotations: list[AnnotatedVariant],
    rule_sets: list[ResistanceRuleSet],
    snp_combine_af_threshold: float = 0.75,
) -> list[FormulaRuleHit]:
    """
    Match annotated variants against combination resistance rule sets.

    A rule set fires only when every member mutation is present in the annotated
    variant list.

    :param annotations: list of annotated variants
    :param rule_sets: list of ResistanceRuleSet objects with populated members
    :param snp_combine_af_threshold: strict AF threshold used for combo-member support;
        only annotations with AF > threshold can satisfy a combo member
    :return: list of FormulaRuleHit for every rule set that fired
    """
    if not rule_sets:
        return []

    # Build lookup from (gene_name, codon_pos) -> list[AnnotatedVariant].
    # Synonymous and low-AF variants cannot satisfy a resistance rule member.
    present: dict[tuple[str, int], list[AnnotatedVariant]] = {}
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue
        if ann.variant.allele_freq <= snp_combine_af_threshold:
            continue
        present.setdefault((ann.gene_name, ann.codon_pos), []).append(ann)

    hits: list[FormulaRuleHit] = []
    anchor_warning_cache: set[str] = set()
    for rule_set in rule_sets:
        contributing: list[AnnotatedVariant] = []
        all_matched = True
        for member in rule_set.members:
            ann = _pick_matching_member_annotation(
                present.get((member.gene_name, member.position), []),
                member.reference,
                member.mutation,
                member.gene_name,
                member.position,
                anchor_warning_cache,
            )
            if ann is None:
                all_matched = False
                break
            contributing.append(ann)

        if all_matched and contributing:
            hits.append(FormulaRuleHit(rule_set=rule_set, matched_variants=contributing))

    logger.info(
        'Matched %d combination rule set hit(s) across %d annotation(s)',
        len(hits),
        len(annotations),
    )
    return hits


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

    threshold = (
        float(member_af_threshold)
        if member_af_threshold is not None
        else float(CLI_CONFIG.matching.combination_member_af_threshold)
    )

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
                ann_key = (ann.gene_name, ann.codon_pos, ann.alt_aa)
                existing_key = (existing.gene_name, existing.codon_pos, existing.alt_aa)
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
                    gene_name=member_rule.gene_name,
                    gene_id=member_rule.gene_id,
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


def _publication_from_row(row: sqlite3.Row) -> Publication:
    """Build one Publication object from a SQLite row."""
    return Publication(
        id=int(row['id']),
        doi=row['doi'] or '',
        title=row['title'] or '',
        pubmed_id=row['pubmed_id'] or '',
        raw_input=row['raw_input'] or '',
    )


def _fetch_publications_by_owner(
    conn: sqlite3.Connection,
    owner_ids: list[int],
    *,
    link_table: str,
    owner_column: str,
) -> dict[int, list[Publication]]:
    """Fetch publications grouped by owner id (rule or rule_set)."""
    if not owner_ids:
        return {}

    placeholders = ','.join('?' * len(owner_ids))
    rows = conn.execute(
        f'SELECT lp.{owner_column} AS owner_id, p.id, p.doi, p.title, p.pubmed_id, p.raw_input '
        f'FROM {link_table} lp '
        f'JOIN publication p ON p.id = lp.publication_id '
        f'WHERE lp.{owner_column} IN ({placeholders})',
        owner_ids,
    ).fetchall()

    grouped: dict[int, list[Publication]] = {}
    for row in rows:
        grouped.setdefault(int(row['owner_id']), []).append(_publication_from_row(row))
    return grouped


def _rule_from_row(row: sqlite3.Row) -> ResistanceRule:
    """Build one ResistanceRule object from a SQLite row."""
    return ResistanceRule(
        id=row['id'],
        gene_name=row['gene_name'],
        gene_id=row['gene_id'],
        drug_name=row['drug_name'],
        drug_id=row['drug_id'],
        external_id=row['external_id'] or '',
        reference_identifier=row['reference_identifier'] or '',
        position=row['position'],
        reference=row['reference'] or '',
        mutation=row['mutation'],
        phenotype=row['phenotype'],
        clinical_phenotype=row['clinical_phenotype'] or 'unknown',
        ic50=row['ic50'] or '',
        fold_ic50=row['fold_ic50'] or '',
        source=row['source'] or '',
        comment=row['comment'] or '',
        pubchem_url=row['pubchem_url'] or '',
        description=row['description'] or '',
        is_internal_formula_component=is_internal_formula_component_drug_name(row['drug_name'] or ''),
    )


def _attach_publications_to_rules(
    conn: sqlite3.Connection,
    rules: list[ResistanceRule],
) -> None:
    """
    Batch-load publications for a list of rules and assign them in place.

    :param conn: SQLite database connection
    :param rules: list of ResistanceRule objects to enrich
    """
    rule_ids = [r.id for r in rules]
    pubs_by_rule = _fetch_publications_by_owner(
        conn,
        rule_ids,
        link_table='rule_publication',
        owner_column='rule_id',
    )
    for rule in rules:
        rule.publications = pubs_by_rule.get(rule.id, [])


def _attach_publications_to_formula_rules(
    conn: sqlite3.Connection,
    formula_rules: list[FormulaRuleRuntime],
) -> None:
    """Batch-load publications for formula rules and assign them in place."""
    formula_ids = [fr.id for fr in formula_rules]
    pubs_by_formula = _fetch_publications_by_owner(
        conn,
        formula_ids,
        link_table='resistance_formula_rule_publication',
        owner_column='formula_rule_id',
    )
    for formula in formula_rules:
        formula.publications = pubs_by_formula.get(formula.id, [])


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
    gene_name: str,
    codon_pos: int,
) -> str | None:
    """Return a warning text when a matched-position indel has a different anchor AA."""
    if ann_consequence == 'insertion' and len(rule_mutation) > len(rule_reference):
        if ann_ref != rule_reference:
            return (
                f'Indel anchor mismatch at {gene_name}:{codon_pos + 1} (insertion): '
                f'rule anchor {rule_reference!r} vs observed anchor {ann_ref!r}. '
                'Matching by inserted payload only.'
            )
        return None

    if ann_consequence == 'deletion' and len(rule_reference) > len(rule_mutation):
        if ann_alt != rule_mutation:
            return (
                f'Indel anchor mismatch at {gene_name}:{codon_pos + 1} (deletion): '
                f'rule anchor {rule_mutation!r} vs observed anchor {ann_alt!r}. '
                'Matching by deleted payload only.'
            )
        return None

    return None


def _pick_matching_member_annotation(
    candidates: list[AnnotatedVariant],
    reference: str,
    mutation: str,
    gene_name: str,
    codon_pos: int,
    anchor_warning_cache: set[str],
) -> AnnotatedVariant | None:
    """Return one candidate annotation satisfying one combo-rule member."""
    if not candidates:
        return None

    for ann in candidates:
        anchor_warning = _indel_anchor_mismatch_warning(
            rule_reference=reference,
            rule_mutation=mutation,
            ann_ref=ann.ref_aa,
            ann_alt=ann.alt_aa,
            ann_consequence=ann.consequence,
            gene_name=gene_name,
            codon_pos=codon_pos,
        )
        if anchor_warning and anchor_warning not in anchor_warning_cache:
            logger.warning(anchor_warning)
            anchor_warning_cache.add(anchor_warning)

        if _matches_rule_alleles(
            reference=reference,
            mutation=mutation,
            ann_ref=ann.ref_aa,
            ann_alt=ann.alt_aa,
            ann_consequence=ann.consequence,
        ):
            return ann

    return None


