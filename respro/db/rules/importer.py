"""Atomic and formula resistance-rule TSV validation and SQLite import."""

from __future__ import annotations

import csv
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from respro.core.formula import FORMULA_OPERATORS, parse_formula_expression
from respro.db.drugs import get_or_create_drug_id
from respro.db.models import INTERNAL_FORMULA_COMPONENT_DRUG_NAME
from respro.db.rules.alleles import (
    RE_ANCHORLESS_DEL,
    is_noop_mutation,
    is_supported_mutation_token,
    normalize_rule_alleles_for_storage,
    resolve_anchorless_deletion,
)
from respro.db.rules.features import (
    build_feature_lookup,
    detect_coordinate_base,
    get_feature_aa_sequence,
    resolve_rule_feature_id,
    validate_reference_amino_acids,
)
from respro.db.rules.normalize import (
    append_contradictory_comment,
    get_value,
    normalize_fold_ic50_from_row,
    normalize_ic50_from_row,
    normalize_phenotypes_from_row,
    normalize_score_from_row,
)
from respro.db.rules.publications import link_publications

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PreparedAtomicRule:
    row_number: int
    feature_name: str
    feature_id: int
    drug_name: str
    external_id: str
    reference_identifier: str
    position_raw: str
    position_0based: int
    reference_aa: str
    mutation: str
    phenotype: str
    clinical_phenotype: str
    ic50: str
    fold_ic50: str
    score: str
    source: str
    comment: str
    raw_publication: str


@dataclass(frozen=True, slots=True)
class _PreparedFormulaRule:
    formula_id: str
    drug_name: str
    label: str
    normalized_expression: str
    referenced_ids: list[str]
    phenotype: str
    clinical_phenotype: str
    ic50: str
    fold_ic50: str
    score: str
    source: str
    comment: str
    raw_publication: str


@dataclass
class _AtomicLoadState:
    errors: list[str] = field(default_factory=list)
    skipped_missing_ref_ids: set[str] = field(default_factory=set)
    skipped_missing_ref_count: int = 0
    skipped_ref: list[str] = field(default_factory=list)
    skipped_feature: list[str] = field(default_factory=list)
    skipped_feature_pairs: list[tuple[str, str]] = field(default_factory=list)
    skipped_invalid_aa: list[str] = field(default_factory=list)
    stripped_member_id_rows: list[str] = field(default_factory=list)
    skipped_external_ids: dict[str, str] = field(default_factory=dict)


def _rule_exists(
    conn: sqlite3.Connection,
    *,
    feature_id: int,
    drug_id: int,
    reference_identifier: str,
    position: int,
    reference: str,
    mutation: str,
) -> bool:
    row = conn.execute(
        'SELECT id FROM resistance_rule '
        'WHERE feature_id = ? AND drug_id = ? AND reference_identifier = ? '
        'AND position = ? AND reference = ? AND mutation = ? LIMIT 1',
        (feature_id, drug_id, reference_identifier, position, reference, mutation),
    ).fetchone()
    return row is not None


def _external_rule_id_exists(conn: sqlite3.Connection, external_id: str) -> bool:
    return conn.execute(
        'SELECT 1 FROM resistance_rule WHERE external_id = ? LIMIT 1',
        (external_id,),
    ).fetchone() is not None


def _load_rule_ids_by_external_id(conn: sqlite3.Connection, external_ids: set[str]) -> dict[str, int]:
    if not external_ids:
        return {}
    placeholders = ','.join('?' * len(external_ids))
    rows = conn.execute(
        f'SELECT id, external_id FROM resistance_rule WHERE external_id IN ({placeholders})',
        sorted(external_ids),
    ).fetchall()
    return {row['external_id']: int(row['id']) for row in rows}


def _load_known_reference_identifiers(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute('SELECT name, accession FROM reference').fetchall()
    identifiers: set[str] = set()
    for row in rows:
        if row['name']:
            identifiers.add(row['name'])
        if row['accession']:
            identifiers.add(row['accession'])
    return identifiers


def _formula_rule_exists_readonly(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    formula_id: str,
    drug_name: str,
    normalized_expression: str,
) -> tuple[bool, bool]:
    """Check DB formula_id and drug+expression collisions without mutating the drug table."""
    id_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule WHERE formula_id = ? LIMIT 1',
        (formula_id,),
    ).fetchone() is not None
    expr_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule fr '
        'JOIN drug d ON d.id = fr.drug_id '
        'WHERE d.project_id = ? AND LOWER(d.name) = LOWER(?) AND fr.normalized_expression = ? '
        'LIMIT 1',
        (project_id, drug_name.strip(), normalized_expression),
    ).fetchone() is not None
    return id_exists, expr_exists


def _mark_feature_has_rules(conn: sqlite3.Connection, feature_id: int) -> None:
    conn.execute('UPDATE feature SET has_rules = 1 WHERE id = ?', (feature_id,))


def load_resistance_rules(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    require_external_ids: bool = False,
    additional_info: bool = False,
    publication_lookup_failures: list[str] | None = None,
) -> tuple[int, set[str], dict[str, str]]:
    """Validate and import atomic resistance rules from *rules_tsv* into SQLite."""
    conn.row_factory = sqlite3.Row
    features_by_name = build_feature_lookup(conn)
    known_ref_ids = _load_known_reference_identifiers(conn)

    with open(rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        all_rows = list(reader)

    declared_external_ids = _validate_rules_header_and_collect_ids(
        conn, all_rows, reader.fieldnames,
    )

    state = _AtomicLoadState()
    prepared_rows = _prepare_atomic_rule_rows(
        all_rows, features_by_name, known_ref_ids, require_external_ids, state,
    )

    # Validate completely before inserting anything so invalid TSVs never partially mutate SQLite.
    if state.errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(state.errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    count, skipped_duplicates_detail = _insert_prepared_atomic_rules(
        conn, project_id, prepared_rows, additional_info, publication_lookup_failures,
    )
    _log_atomic_skip_warnings(state, skipped_duplicates_detail)
    logger.info('Loaded %d single resistance rule(s)', count)
    return count, declared_external_ids, state.skipped_external_ids


def _validate_ic50_alias_headers(fieldnames: Sequence[str] | None) -> None:
    """Reject TSVs declaring both ``ic50``/``ic_50`` or ``fold_ic50``/``fold_ic_50``."""
    header_columns = {col.strip() for col in (fieldnames or []) if col}
    for label, candidates in (
        ('IC50', {'ic50', 'ic_50'}),
        ('fold-IC50', {'fold_ic50', 'fold_ic_50'}),
    ):
        present = sorted(header_columns & candidates)
        if len(present) > 1:
            raise ValueError(
                f'Rules validation failed:\n- only one {label} column is allowed; found '
                + ', '.join(repr(col) for col in present)
            )


def _validate_rules_header_and_collect_ids(
    conn: sqlite3.Connection,
    all_rows: list[dict[str, str]],
    fieldnames: Sequence[str] | None,
) -> set[str]:
    """Validate TSV header columns and required per-row identifiers; return declared external IDs."""
    _validate_ic50_alias_headers(fieldnames)

    errors: list[str] = []
    declared_external_ids: set[str] = set()
    for row_number, row in enumerate(all_rows, start=2):
        if not get_value(row, 'reference_identifier'):
            errors.append(f'row {row_number}: missing required field reference_identifier')
        if not get_value(row, 'reference'):
            errors.append(f'row {row_number}: missing required field reference')

        external_id = get_value(row, 'member_id', 'rule_id')
        if external_id:
            if external_id.upper() in FORMULA_OPERATORS:
                errors.append(
                    f'row {row_number}: member_id {external_id!r} uses a reserved boolean keyword'
                )
            declared_external_ids.add(external_id)

    existing = sorted(eid for eid in declared_external_ids if _external_rule_id_exists(conn, eid))
    if existing:
        errors.append(
            'atomic rule ids already exist in project: ' + ', '.join(repr(eid) for eid in existing)
        )

    if errors:
        raise ValueError('Rules validation failed:\n' + '\n'.join(f'- {msg}' for msg in errors))
    return declared_external_ids


def _prepare_atomic_rule_rows(
    all_rows: list[dict[str, str]],
    features_by_name: dict[str, list[dict[str, Any]]],
    known_ref_ids: set[str],
    require_external_ids: bool,
    state: _AtomicLoadState,
) -> list[_PreparedAtomicRule]:
    """Normalize and validate all atomic rule rows without modifying SQLite."""
    coord_base = detect_coordinate_base(
        all_rows, features_by_name, allowed_reference_identifiers=known_ref_ids,
    )
    logger.info('Detected %d-based amino acid positions in rules TSV', coord_base)
    mismatch_keys = validate_reference_amino_acids(
        all_rows, features_by_name, coord_base, allowed_reference_identifiers=known_ref_ids,
    )

    prepared: list[_PreparedAtomicRule] = []
    seen_external_ids: dict[str, tuple[int, tuple[int, str, int, str, str]]] = {}

    for row_number, row in enumerate(all_rows, start=2):
        feature_name = get_value(row, 'feature')
        reference_identifier = get_value(row, 'reference_identifier')
        if reference_identifier and reference_identifier not in known_ref_ids:
            state.skipped_missing_ref_ids.add(reference_identifier)
            state.skipped_missing_ref_count += 1
            continue
        if not feature_name or feature_name not in features_by_name:
            feature_label = feature_name or '<empty>'
            ref_label = reference_identifier or '<empty>'
            state.skipped_feature.append(
                f'row {row_number}: feature {feature_label!r}, reference_identifier {ref_label!r}'
            )
            state.skipped_feature_pairs.append((feature_label, ref_label))
            continue

        feature_id = resolve_rule_feature_id(features_by_name[feature_name], reference_identifier)
        if feature_id is None:
            candidate_refs = sorted({
                c['reference_accession'] or c['reference_name']
                for c in features_by_name[feature_name]
            })
            if reference_identifier:
                state.skipped_ref.append(
                    f'feature {feature_name!r}: reference_identifier {reference_identifier!r} '
                    f'not found (available: {candidate_refs})'
                )
            else:
                state.errors.append(
                    f'Rules feature {feature_name!r} is ambiguous across references {candidate_refs}; '
                    'add reference_identifier to the rules row'
                )
            continue

        drug_name = get_value(row, 'antiviral')
        external_id = get_value(row, 'member_id', 'rule_id')
        if not drug_name:
            if require_external_ids and external_id:
                drug_name = INTERNAL_FORMULA_COMPONENT_DRUG_NAME
            else:
                state.errors.append(f'Rule for feature {feature_name!r} has no antiviral value')
                continue

        position_raw = get_value(row, 'position')
        mutation_raw = get_value(row, 'mutation')
        if not position_raw or not mutation_raw:
            state.errors.append(f'Rule for feature {feature_name!r} is missing position or mutation')
            continue

        try:
            position_0based = int(position_raw) - coord_base
        except ValueError:
            state.errors.append(
                f'Rule for feature {feature_name!r} has invalid position {position_raw!r}'
            )
            continue

        reference_aa = get_value(row, 'reference')
        if (feature_name, position_raw, reference_identifier, reference_aa) in mismatch_keys:
            if external_id:
                state.skipped_external_ids[external_id] = (
                    f'reference AA mismatch at {reference_identifier} {feature_name} pos {position_raw}'
                )
            continue

        context = f'Rule for feature {feature_name!r} pos {position_raw!r}'
        del_match = RE_ANCHORLESS_DEL.match(mutation_raw)
        if del_match:
            aa_seq = get_feature_aa_sequence(features_by_name[feature_name], reference_identifier)
            if not aa_seq:
                state.errors.append(
                    f'{context}: feature has no aa_sequence, cannot resolve deletion anchor '
                    f'for {mutation_raw!r}'
                )
                continue
            resolved = resolve_anchorless_deletion(del_match.group(1).upper(), position_0based, aa_seq)
            if resolved is None:
                state.errors.append(
                    f'{context}: cannot resolve anchor for deletion {mutation_raw!r} — '
                    'check that the deleted block matches the feature sequence and '
                    'that the deletion does not start at position 1'
                )
                continue
            position_0based, reference_aa, mutation_raw = resolved

        ic50_value = normalize_ic50_from_row(row, errors=state.errors, context=context)
        fold_ic50_value = normalize_fold_ic50_from_row(row, errors=state.errors, context=context)
        score_value = normalize_score_from_row(row, errors=state.errors, context=context)
        phenotype_value, clinical_value = normalize_phenotypes_from_row(
            row, errors=state.errors, context=context,
        )
        normalized = normalize_rule_alleles_for_storage(
            reference_aa=reference_aa,
            mutation_raw=mutation_raw,
            position_0based=position_0based,
            context=context,
            errors=state.errors,
        )
        if normalized is None:
            continue
        position_0based, reference_aa, mutation = normalized

        if is_noop_mutation(reference_aa, mutation):
            state.errors.append(
                f'{context}: mutation {mutation_raw!r} does not change reference {reference_aa!r}'
            )
            continue
        if not is_supported_mutation_token(mutation):
            state.skipped_invalid_aa.append(
                f'feature {feature_name!r} pos {position_raw!r}: unsupported amino-acid token '
                f'{mutation_raw!r} (normalized {mutation!r})'
            )
            continue

        if external_id:
            signature = (feature_id, reference_identifier, position_0based, reference_aa, mutation)
            seen = seen_external_ids.get(external_id)
            if seen is not None:
                first_row, first_sig = seen
                if signature == first_sig:
                    state.stripped_member_id_rows.append(
                        f'row {row_number}: member_id {external_id!r} stripped — '
                        f'duplicates identical atomic definition from row {first_row}'
                    )
                    external_id = ''
                else:
                    state.errors.append(
                        f'duplicate member_id: {external_id!r} '
                        f'(conflicting definitions in rows {first_row} and {row_number})'
                    )
                    continue
            else:
                seen_external_ids[external_id] = (row_number, signature)

        comment_value = append_contradictory_comment(
            get_value(row, 'comment'),
            phenotype=phenotype_value,
            clinical_phenotype=clinical_value,
        )
        prepared.append(_PreparedAtomicRule(
            row_number=row_number,
            feature_name=feature_name,
            feature_id=feature_id,
            drug_name=drug_name,
            external_id=external_id,
            reference_identifier=reference_identifier,
            position_raw=position_raw,
            position_0based=position_0based,
            reference_aa=reference_aa,
            mutation=mutation,
            phenotype=phenotype_value,
            clinical_phenotype=clinical_value,
            ic50=ic50_value,
            fold_ic50=fold_ic50_value,
            score=score_value,
            source=get_value(row, 'source'),
            comment=comment_value,
            raw_publication=get_value(row, 'publication'),
        ))
    return prepared


def _insert_prepared_atomic_rules(
    conn: sqlite3.Connection,
    project_id: int,
    prepared_rows: list[_PreparedAtomicRule],
    additional_info: bool,
    publication_lookup_failures: list[str] | None,
) -> tuple[int, list[str]]:
    """Insert validated atomic rules and link publications."""
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}
    count = 0
    skipped_duplicates: list[str] = []

    for rule in prepared_rows:
        drug_id = get_or_create_drug_id(conn, project_id, rule.drug_name, drug_cache)
        if not rule.external_id and _rule_exists(
            conn,
            feature_id=rule.feature_id,
            drug_id=drug_id,
            reference_identifier=rule.reference_identifier,
            position=rule.position_0based,
            reference=rule.reference_aa,
            mutation=rule.mutation,
        ):
            skipped_duplicates.append(
                f'{rule.reference_identifier} feature {rule.feature_name!r} '
                f'pos {rule.position_raw} {rule.reference_aa!r}>{rule.mutation!r} '
                f'({rule.drug_name})'
            )
            continue

        cur = conn.execute(
            'INSERT INTO resistance_rule '
            '(feature_id, drug_id, external_id, reference_identifier, position, reference, '
            'mutation, phenotype, clinical_phenotype, ic50, fold_ic50, score, source, comment) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                rule.feature_id, drug_id, rule.external_id, rule.reference_identifier,
                rule.position_0based, rule.reference_aa, rule.mutation, rule.phenotype,
                rule.clinical_phenotype, rule.ic50, rule.fold_ic50, rule.score,
                rule.source, rule.comment,
            ),
        )
        assert cur.lastrowid is not None
        if rule.raw_publication:
            link_publications(
                conn, 'rule', int(cur.lastrowid), rule.raw_publication,
                additional_info, pub_cache, publication_lookup_failures,
            )
        _mark_feature_has_rules(conn, rule.feature_id)
        count += 1
    return count, skipped_duplicates


def _log_atomic_skip_warnings(
    state: _AtomicLoadState,
    skipped_duplicates: list[str],
) -> None:
    """Emit structured warnings summarizing skipped atomic rows."""
    if state.skipped_missing_ref_ids:
        logger.warning(
            '%d rule(s) skipped — reference(s) not provided (no GenBank supplied): %s',
            state.skipped_missing_ref_count,
            ', '.join(repr(r) for r in sorted(state.skipped_missing_ref_ids)),
        )
    if state.skipped_feature:
        unique_rows = sorted(set(state.skipped_feature))
        unique_pairs = sorted(set(state.skipped_feature_pairs))
        logger.warning(
            '%d rule(s) skipped — feature(s) not found in GenBank annotations: %s\n%s',
            len(state.skipped_feature),
            ', '.join(f'{feat!r} @ reference_identifier {ref!r}' for feat, ref in unique_pairs),
            '\n'.join(f'  - {detail}' for detail in unique_rows),
        )
    if state.skipped_ref:
        unique_skipped = sorted(set(state.skipped_ref))
        logger.warning(
            '%d rule(s) skipped — reference_identifier not in this project:\n%s',
            len(unique_skipped), '\n'.join(f'  - {msg}' for msg in unique_skipped),
        )
    if state.skipped_invalid_aa:
        unique_invalid = sorted(set(state.skipped_invalid_aa))
        logger.warning(
            '%d rule(s) skipped — unsupported amino-acid tokens:\n%s',
            len(unique_invalid), '\n'.join(f'  - {msg}' for msg in unique_invalid),
        )
    if skipped_duplicates:
        logger.warning(
            '%d duplicate rule(s) skipped — existing rows were kept:\n%s',
            len(skipped_duplicates), '\n'.join(f'  {item}' for item in sorted(skipped_duplicates)),
        )
    if state.stripped_member_id_rows:
        logger.warning(
            '%d row(s) had member_id stripped — duplicate member_id with identical atomic '
            'definition (first occurrence kept the member_id):\n%s',
            len(state.stripped_member_id_rows),
            '\n'.join(f'  - {msg}' for msg in sorted(state.stripped_member_id_rows)),
        )


def load_formula_rules(
    conn: sqlite3.Connection,
    project_id: int,
    formula_rules_tsv: Path,
    declared_atomic_ids: set[str] | None = None,
    skipped_atomic_ids: dict[str, str] | None = None,
    additional_info: bool = False,
    publication_lookup_failures: list[str] | None = None,
) -> int:
    """Validate and import boolean formula resistance rules from *formula_rules_tsv*."""
    conn.row_factory = sqlite3.Row
    with open(formula_rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        _validate_ic50_alias_headers(reader.fieldnames)
        rows = list(reader)

    errors: list[str] = []
    parsed_rows, skipped_validation = _parse_formula_rows(rows, errors)
    if skipped_validation:
        logger.warning(
            '%d formula rule(s) skipped due to duplicates or conflicts:\n%s',
            len(skipped_validation), '\n'.join(f'  - {msg}' for msg in skipped_validation),
        )

    referenced_atomic_ids = {ref_id for _, _, ref_ids in parsed_rows for ref_id in ref_ids}
    rule_ids_by_external_id = _load_rule_ids_by_external_id(conn, referenced_atomic_ids)

    prepared, skipped_missing_members = _prepare_formula_rows(
        conn, project_id, parsed_rows, rule_ids_by_external_id,
        declared_atomic_ids, skipped_atomic_ids, errors,
    )

    if skipped_missing_members:
        logger.warning(
            '%d formula rule(s) skipped — one or more member rules were not imported:\n%s',
            len(skipped_missing_members),
            '\n'.join(f'  - {msg}' for msg in skipped_missing_members),
        )

    # Validate completely before inserting any drug, formula, member, or publication row.
    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    inserted = _insert_prepared_formula_rules(
        conn, project_id, prepared, rule_ids_by_external_id,
        additional_info, publication_lookup_failures,
    )
    logger.info('Loaded %d formula resistance rule(s)', inserted)
    return inserted


def _parse_formula_rows(
    rows: list[dict[str, str]],
    errors: list[str],
) -> tuple[list[tuple[dict[str, str], str, list[str]]], list[str]]:
    """Validate required fields, parse expressions, and deduplicate intra-file formula rows."""
    formula_id_to_row: dict[str, int] = {}
    normalized_by_drug: dict[tuple[str, str], str] = {}
    parsed: list[tuple[dict[str, str], str, list[str]]] = []
    skipped: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        formula_id = get_value(row, 'group_id', 'formula_id')
        drug_name = get_value(row, 'antiviral')
        expression = get_value(row, 'expression', 'formula')
        context = f'Formula rule row {row_number}'

        if not formula_id:
            errors.append(f'{context}: missing required field formula_id')
            continue
        if not drug_name:
            errors.append(f'{context}: missing required field antiviral')
            continue
        if not expression:
            errors.append(f'{context}: missing required field expression')
            continue
        if formula_id.upper() in FORMULA_OPERATORS:
            errors.append(f'{context}: formula_id {formula_id!r} uses a reserved boolean keyword')
            continue

        try:
            normalized_expr, referenced_ids = parse_formula_expression(expression)
        except ValueError as exc:
            errors.append(f'{context}: invalid expression {expression!r} ({exc})')
            continue

        duplicate_refs = sorted({r for r in referenced_ids if referenced_ids.count(r) > 1})
        if duplicate_refs:
            skipped.append(
                f'{context}: duplicate atomic rule ids in expression '
                + ', '.join(repr(r) for r in duplicate_refs)
            )
            continue

        drug_key = (drug_name.lower(), normalized_expr)
        if drug_key in normalized_by_drug:
            skipped.append(
                f'{context}: duplicate formula rule for drug {drug_name!r}; '
                f'matches {normalized_by_drug[drug_key]!r} after normalization'
            )
            continue
        if formula_id in formula_id_to_row:
            skipped.append(
                f'{context}: duplicate formula rule id {formula_id!r} '
                f'(first occurrence at row {formula_id_to_row[formula_id]})'
            )
            continue

        normalized_by_drug[drug_key] = formula_id
        formula_id_to_row[formula_id] = row_number
        parsed.append((row, normalized_expr, referenced_ids))

    return parsed, skipped


def _prepare_formula_rows(
    conn: sqlite3.Connection,
    project_id: int,
    parsed_rows: list[tuple[dict[str, str], str, list[str]]],
    rule_ids_by_external_id: dict[str, int],
    declared_atomic_ids: set[str] | None,
    skipped_atomic_ids: dict[str, str] | None,
    errors: list[str],
) -> tuple[list[_PreparedFormulaRule], list[str]]:
    """Validate member presence, normalize metrics, and check DB collisions read-only."""
    prepared: list[_PreparedFormulaRule] = []
    skipped_missing: list[str] = []

    for row, normalized_expr, referenced_ids in parsed_rows:
        formula_id = get_value(row, 'group_id', 'formula_id')
        drug_name = get_value(row, 'antiviral')
        missing = sorted(r for r in referenced_ids if r not in rule_ids_by_external_id)
        if missing:
            reasons = []
            for member in missing:
                if skipped_atomic_ids and member in skipped_atomic_ids:
                    reasons.append(f'{member!r} ({skipped_atomic_ids[member]})')
                elif declared_atomic_ids and member not in declared_atomic_ids:
                    reasons.append(f'{member!r} (unknown atomic rule id)')
                else:
                    reasons.append(repr(member))
            skipped_missing.append(f'{formula_id!r}: member(s) not imported: ' + ', '.join(reasons))
            continue

        context = f'Formula rule {formula_id!r}'
        phenotype, clinical = normalize_phenotypes_from_row(row, errors=errors, context=context)
        ic50 = normalize_ic50_from_row(row, errors=errors, context=context)
        fold_ic50 = normalize_fold_ic50_from_row(row, errors=errors, context=context)
        score = normalize_score_from_row(row, errors=errors, context=context)
        comment = append_contradictory_comment(
            get_value(row, 'comment'), phenotype=phenotype, clinical_phenotype=clinical,
        )

        id_exists, expr_exists = _formula_rule_exists_readonly(
            conn,
            project_id=project_id,
            formula_id=formula_id,
            drug_name=drug_name,
            normalized_expression=normalized_expr,
        )
        if id_exists:
            errors.append(f'Formula rule {formula_id!r}: formula_id already exists in project')
            continue
        if expr_exists:
            errors.append(
                f'Formula rule {formula_id!r}: duplicate normalized expression for drug {drug_name!r}'
            )
            continue

        prepared.append(_PreparedFormulaRule(
            formula_id=formula_id,
            drug_name=drug_name,
            label=get_value(row, 'label'),
            normalized_expression=normalized_expr,
            referenced_ids=referenced_ids,
            phenotype=phenotype,
            clinical_phenotype=clinical,
            ic50=ic50,
            fold_ic50=fold_ic50,
            score=score,
            source=get_value(row, 'source'),
            comment=comment,
            raw_publication=get_value(row, 'publication'),
        ))
    return prepared, skipped_missing


def _insert_prepared_formula_rules(
    conn: sqlite3.Connection,
    project_id: int,
    prepared_rows: list[_PreparedFormulaRule],
    rule_ids_by_external_id: dict[str, int],
    additional_info: bool,
    publication_lookup_failures: list[str] | None,
) -> int:
    """Insert validated formula rules, member links, feature flags, and publications."""
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}

    for rule in prepared_rows:
        drug_id = get_or_create_drug_id(conn, project_id, rule.drug_name, drug_cache)
        cur = conn.execute(
            'INSERT INTO resistance_formula_rule '
            '(drug_id, formula_id, label, normalized_expression, phenotype, '
            'clinical_phenotype, ic50, fold_ic50, score, source, comment) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                drug_id, rule.formula_id, rule.label, rule.normalized_expression,
                rule.phenotype, rule.clinical_phenotype, rule.ic50, rule.fold_ic50,
                rule.score, rule.source, rule.comment,
            ),
        )
        assert cur.lastrowid is not None
        formula_rule_id = int(cur.lastrowid)

        for ref_id in sorted(rule.referenced_ids):
            member_rule_id = rule_ids_by_external_id[ref_id]
            conn.execute(
                'INSERT INTO resistance_formula_rule_member (formula_rule_id, rule_id) VALUES (?, ?)',
                (formula_rule_id, member_rule_id),
            )
            member_row = conn.execute(
                'SELECT feature_id FROM resistance_rule WHERE id = ?',
                (member_rule_id,),
            ).fetchone()
            if member_row is not None:
                _mark_feature_has_rules(conn, int(member_row['feature_id']))

        if rule.raw_publication:
            link_publications(
                conn, 'formula', formula_rule_id, rule.raw_publication,
                additional_info, pub_cache, publication_lookup_failures,
            )

    return len(prepared_rows)
