"""
Atomic and formula resistance rule loading — row iteration, validation, and DB insertion.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path

from respro.db._rules_alleles import (
    _RE_ANCHORLESS_DEL,
    _expand_anchor_changed_indel_rules,
    _is_noop_mutation,
    _is_supported_mutation_token,
    _normalize_rule_alleles_for_storage,
    _resolve_anchorless_deletion,
)
from respro.db._rules_formula import (
    _FORMULA_OPERATORS,
    _parse_formula_expression,
    _tokenize_formula_expression,
)
from respro.db._rules_genes import (
    _detect_coordinate_base,
    _get_gene_aa_sequence,
    _resolve_rule_gene_id,
    _validate_reference_amino_acids,
)
from respro.db._rules_normalize import (
    _append_contradictory_comment,
    _get_value,
    _normalize_fold_ic50_from_row,
    _normalize_ic50_from_row,
    _normalize_phenotypes_from_row,
    _normalize_score_from_row,
)
from respro.db._rules_persist import (
    _build_gene_lookup,
    _external_rule_id_exists,
    _formula_rule_exists,
    _load_rule_ids_by_external_id,
    _rule_exists,
)
from respro.db._rules_publication import (
    _link_formula_rule_publications,
    _link_rule_publications,
    _report_publication_lookup_failures,
)
from respro.db.drugs import _get_or_create_drug_id
from respro.db.models import _INTERNAL_FORMULA_COMPONENT_DRUG_NAME

logger = logging.getLogger(__name__)


def _load_resistance_rules(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    require_external_ids: bool = False,
    additional_info: bool = False,
    publication_lookup_failures: list[str] | None = None,
) -> tuple[int, set[str], set[str], dict[str, str]]:
    """
    Load resistance rules from TSV file; return count of inserted rules and grouped IDs.

    All rows are imported as atomic single rules into ``resistance_rule``.
    Grouping metadata from ``group_id``/``member_id`` is captured for formula
    validation only; no implicit combination rules are created during this step.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param rules_tsv: path to resistance rules TSV file
    :return: (inserted atomic-rule count, set of group_id values found in rules TSV,
             set of declared external_ids in rules TSV,
             dict of external_id -> skip reason for ids that were skipped)
    """
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}
    count = 0
    skipped_duplicates = 0

    conn.row_factory = sqlite3.Row
    genes_by_name = _build_gene_lookup(conn)

    errors: list[str] = []
    skipped_ref: list[str] = []
    skipped_gene: list[str] = []
    skipped_gene_pairs: list[tuple[str, str]] = []
    skipped_invalid_aa: list[str] = []
    skipped_duplicates_detail: list[str] = []
    skipped_identical_member_id_rows: list[str] = []
    # Maps external_id → skip reason, for formula rule skip messages.
    skipped_external_ids: dict[str, str] = {}
    seen_external_id_signatures: dict[str, tuple[int, tuple[int, str, int, str, str]]] = {}

    with open(rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        all_rows = _expand_anchor_changed_indel_rules(list(reader))

    header_columns = {col.strip() for col in (reader.fieldnames or []) if col}
    present_ic50 = sorted(header_columns & {'ic50', 'ic_50'})
    present_fold = sorted(header_columns & {'fold_ic50', 'fold_ic_50'})
    if len(present_ic50) > 1:
        raise ValueError(
            'Rules validation failed:\n'
            '- only one IC50 column is allowed; found '
            + ', '.join(repr(col) for col in present_ic50)
        )
    if len(present_fold) > 1:
        raise ValueError(
            'Rules validation failed:\n'
            '- only one fold-IC50 column is allowed; found '
            + ', '.join(repr(col) for col in present_fold)
        )

    required_field_errors: list[str] = []
    external_ids: list[str] = []
    declared_external_ids: set[str] = set()
    grouped_ids: set[str] = set()
    for row_number, row in enumerate(all_rows, start=2):
        if not _get_value(row, 'reference_identifier'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference_identifier'
            )
        if not _get_value(row, 'reference'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference'
            )
        group_ids = [
            value.strip()
            for value in _get_value(row, 'group_id', 'rule_group').split(',')
            if value.strip()
        ]
        grouped_ids.update(group_ids)

        external_id = _get_value(row, 'member_id', 'rule_id')
        if external_id:
            if external_id.upper() in _FORMULA_OPERATORS:
                required_field_errors.append(
                    f'row {row_number}: member_id {external_id!r} uses a reserved boolean keyword'
                )
            external_ids.append(external_id)
            declared_external_ids.add(external_id)
        elif group_ids:
            required_field_errors.append(
                f'row {row_number}: missing required field member_id'
            )

    existing_external_ids = sorted(
        external_id for external_id in set(external_ids) if _external_rule_id_exists(conn, external_id)
    )
    if existing_external_ids:
        required_field_errors.append(
            'atomic rule ids already exist in project: '
            + ', '.join(repr(external_id) for external_id in existing_external_ids)
        )

    if required_field_errors:
        formatted = '\n'.join(f'- {message}' for message in required_field_errors)
        raise ValueError(f'Rules validation failed:\n{formatted}')

    # Detect coordinate base once globally and use it consistently for all rows.
    coord_base = _detect_coordinate_base(all_rows, genes_by_name)
    logger.info('Detected %d-based amino acid positions in rules TSV', coord_base)
    mismatch_keys = _validate_reference_amino_acids(all_rows, genes_by_name, coord_base)

    for row_number, row in enumerate(all_rows, start=2):
        gene_name = _get_value(row, 'gene')
        reference_identifier = _get_value(row, 'reference_identifier')
        if not gene_name or gene_name not in genes_by_name:
            gene_label = gene_name or '<empty>'
            reference_label = reference_identifier or '<empty>'
            skipped_gene.append(
                f'row {row_number}: gene {gene_label!r}, reference_identifier {reference_label!r}'
            )
            skipped_gene_pairs.append((gene_label, reference_label))
            continue

        gene_id = _resolve_rule_gene_id(genes_by_name[gene_name], reference_identifier)
        if gene_id is None:
            # Missing reference context can make same gene name ambiguous across records.
            candidate_refs = sorted(
                {
                    candidate['reference_accession'] or candidate['reference_name']
                    for candidate in genes_by_name[gene_name]
                }
            )
            if reference_identifier:
                skipped_ref.append(
                    f'gene {gene_name!r}: reference_identifier {reference_identifier!r} '
                    f'not found (available: {candidate_refs})'
                )
            else:
                errors.append(
                    f'Rules gene {gene_name!r} is ambiguous across references {candidate_refs}; '
                    'add reference_identifier to the rules row'
                )
            continue

        drug_name = _get_value(row, 'antiviral')
        external_id = _get_value(row, 'member_id', 'rule_id')
        group_ids = [
            value.strip()
            for value in _get_value(row, 'group_id', 'rule_group').split(',')
            if value.strip()
        ]

        if not drug_name:
            if require_external_ids and external_id:
                drug_name = _INTERNAL_FORMULA_COMPONENT_DRUG_NAME
            else:
                errors.append(f'Rule for gene {gene_name!r} has no antiviral value')
                continue

        position_raw = _get_value(row, 'position')
        mutation_raw = _get_value(row, 'mutation')
        if not position_raw or not mutation_raw:
            errors.append(f'Rule for gene {gene_name!r} is missing position or mutation')
            continue

        try:
            position_0based = int(position_raw) - coord_base
        except ValueError:
            errors.append(
                f'Rule for gene {gene_name!r} has invalid position {position_raw!r}'
            )
            continue

        reference_aa = _get_value(row, 'reference')
        if (gene_name, position_raw, reference_identifier, reference_aa) in mismatch_keys:
            if external_id:
                skipped_external_ids[external_id] = (
                    f'reference AA mismatch at {reference_identifier} {gene_name} pos {position_raw}'
                )
            continue

        # Resolve anchor-less deletion tokens (e.g. 'Q35del', 'DD676del') emitted by
        m_del = _RE_ANCHORLESS_DEL.match(mutation_raw)
        if m_del:
            deleted_block = m_del.group(1).upper()
            aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
            if not aa_seq:
                errors.append(
                    f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                    f'gene has no aa_sequence, cannot resolve deletion anchor for {mutation_raw!r}'
                )
                continue
            resolved = _resolve_anchorless_deletion(deleted_block, position_0based, aa_seq)
            if resolved is None:
                errors.append(
                    f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                    f'cannot resolve anchor for deletion {mutation_raw!r} — '
                    'check that the deleted block matches the gene sequence and '
                    'that the deletion does not start at position 1'
                )
                continue
            position_0based, reference_aa, mutation_raw = resolved

        ic50_value = _normalize_ic50_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        fold_ic50_value = _normalize_fold_ic50_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        score_value = _normalize_score_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        phenotype_value, clinical_phenotype_value = _normalize_phenotypes_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        normalized = _normalize_rule_alleles_for_storage(
            reference_aa=reference_aa,
            mutation_raw=mutation_raw,
            position_0based=position_0based,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
            errors=errors,
        )
        if normalized is None:
            continue
        position_0based, reference_aa, mutation = normalized

        if _is_noop_mutation(reference_aa, mutation):
            errors.append(
                f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                f'mutation {mutation_raw!r} does not change reference {reference_aa!r}'
            )
            continue

        if not _is_supported_mutation_token(mutation):
            skipped_invalid_aa.append(
                f'gene {gene_name!r} pos {position_raw!r}: unsupported amino-acid token '
                f'{mutation_raw!r} (normalized {mutation!r})'
            )
            continue

        # Reuse/create drug IDs through a tiny cache to avoid repeated lookups.
        drug_id = _get_or_create_drug_id(conn, project_id, drug_name, drug_cache)
        comment_value = _append_contradictory_comment(
            _get_value(row, 'comment'),
            phenotype=phenotype_value,
            clinical_phenotype=clinical_phenotype_value,
        )

        if external_id:
            signature = (gene_id, reference_identifier, position_0based, reference_aa, mutation)
            seen = seen_external_id_signatures.get(external_id)
            if seen is not None:
                first_row, first_signature = seen
                if signature == first_signature:
                    skipped_identical_member_id_rows.append(
                        f'row {row_number}: member_id {external_id!r} duplicates identical atomic '
                        f'definition from row {first_row}'
                    )
                    skipped_external_ids[external_id] = 'duplicate of an earlier identical row'
                    continue
                errors.append(
                    f'duplicate atomic rule ids: {external_id!r} '
                    f'(conflicting definitions in rows {first_row} and {row_number})'
                )
                continue
            seen_external_id_signatures[external_id] = (row_number, signature)

        # Formula component rows (no antiviral, linked via external_id) may share the same
        # mutation across multiple formula groups. Each has a unique external_id, so skip
        # mutation-level deduplication; the unique index on external_id prevents true duplicates.
        if not group_ids and _rule_exists(
            conn,
            gene_id=gene_id,
            drug_id=drug_id,
            reference_identifier=reference_identifier,
            position=position_0based,
            reference=reference_aa,
            mutation=mutation,
        ):
            skipped_duplicates += 1
            skipped_duplicates_detail.append(
                f'{reference_identifier} gene {gene_name!r} pos {position_raw} '
                f'{reference_aa!r}>{mutation!r} ({drug_name})'
            )
            if external_id:
                skipped_external_ids[external_id] = 'duplicate of an existing rule'
            continue

        conn.execute(
            'INSERT INTO resistance_rule '
            '('
            'gene_id, drug_id, external_id, reference_identifier, position, reference, mutation, '
            'phenotype, clinical_phenotype, ic50, fold_ic50, score, source, comment'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                gene_id,
                drug_id,
                external_id,
                reference_identifier,
                position_0based,
                reference_aa,
                mutation,
                phenotype_value,
                clinical_phenotype_value,
                ic50_value,
                fold_ic50_value,
                score_value,
                _get_value(row, 'source'),
                comment_value,
            ),
        )
        rule_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        raw_publication = _get_value(row, 'publication')
        if raw_publication:
            _link_rule_publications(
                conn,
                rule_id,
                raw_publication,
                additional_info,
                pub_cache,
                publication_lookup_failures,
            )
        count += 1

    if skipped_gene:
        unique_rows = sorted(set(skipped_gene))
        unique_pairs = sorted(set(skipped_gene_pairs))
        logger.warning(
            '%d rule(s) skipped — gene(s) not found in GenBank annotations: %s\n%s',
            len(skipped_gene),
            ', '.join(
                f'{gene!r} @ reference_identifier {reference!r}'
                for gene, reference in unique_pairs
            ),
            '\n'.join(f'  - {detail}' for detail in unique_rows),
        )

    if skipped_ref:
        unique_skipped = sorted(set(skipped_ref))
        logger.warning(
            '%d rule(s) skipped — reference_identifier not in this project:\n%s',
            len(unique_skipped),
            '\n'.join(f'  - {msg}' for msg in unique_skipped),
        )

    if skipped_invalid_aa:
        unique_invalid = sorted(set(skipped_invalid_aa))
        logger.warning(
            '%d rule(s) skipped — unsupported amino-acid tokens:\n%s',
            len(unique_invalid),
            '\n'.join(f'  - {msg}' for msg in unique_invalid),
        )

    if skipped_duplicates_detail:
        logger.warning(
            '%d duplicate rule(s) skipped — existing rows were kept:\n%s',
            len(skipped_duplicates_detail),
            '\n'.join(f'  {rule}' for rule in sorted(skipped_duplicates_detail)),
        )

    if skipped_identical_member_id_rows:
        logger.warning(
            '%d row(s) skipped — duplicate member_id with identical atomic definition '
            '(first occurrence kept):\n%s',
            len(skipped_identical_member_id_rows),
            '\n'.join(f'  - {msg}' for msg in sorted(skipped_identical_member_id_rows)),
        )

    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    if grouped_ids and not require_external_ids:
        logger.warning(
            'Detected grouped atomic rules (%d group_id values), but no formula TSV was provided; '
            'combinatorial rules are ignored while atomic rules are still imported',
            len(grouped_ids),
        )

    logger.info('Loaded %d single resistance rule(s)', count)
    return count, grouped_ids, declared_external_ids, skipped_external_ids


def _load_formula_rules(
    conn: sqlite3.Connection,
    project_id: int,
    formula_rules_tsv: Path,
    expected_group_ids: set[str] | None = None,
    declared_atomic_ids: set[str] | None = None,
    skipped_atomic_ids: dict[str, str] | None = None,
    additional_info: bool = False,
    publication_lookup_failures: list[str] | None = None,
) -> int:
    """Load formula rules from a second TSV and return the inserted formula-rule count."""
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}
    errors: list[str] = []

    with open(formula_rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        rows = list(reader)

    formula_ids: list[str] = []
    formula_id_to_row: dict[str, int] = {}  # Track row number for each formula_id
    normalized_by_drug: dict[tuple[str, str], str] = {}
    prepared_rows: list[tuple[dict[str, str], str, list[str]]] = []
    skipped_formula_validation: list[str] = []  # Track rows skipped due to duplicates/conflicts
    for row_number, row in enumerate(rows, start=2):
        formula_id = _get_value(row, 'group_id', 'formula_id')
        drug_name = _get_value(row, 'antiviral')
        expression = _get_value(row, 'expression', 'formula')
        context = f'Formula rule row {row_number}'

        if not formula_id:
            errors.append(f'{context}: missing required field group_id')
            continue
        if not drug_name:
            errors.append(f'{context}: missing required field antiviral')
            continue
        if not expression:
            errors.append(f'{context}: missing required field expression')
            continue
        if formula_id.upper() in _FORMULA_OPERATORS:
            errors.append(f'{context}: formula_id {formula_id!r} uses a reserved boolean keyword')
            continue

        try:
            normalized_expression, referenced_ids = _parse_formula_expression(expression)
        except ValueError as exc:
            errors.append(f'{context}: invalid expression {expression!r} ({exc})')
            continue

        duplicate_refs = sorted(
            {ref_id for ref_id in referenced_ids if referenced_ids.count(ref_id) > 1}
        )
        if duplicate_refs:
            skipped_formula_validation.append(
                f'{context}: duplicate atomic rule ids in expression '
                + ', '.join(repr(ref_id) for ref_id in duplicate_refs)
            )
            continue

        key = (drug_name.lower(), normalized_expression)
        if key in normalized_by_drug:
            skipped_formula_validation.append(
                f'{context}: duplicate formula rule for drug {drug_name!r}; '
                f'matches {normalized_by_drug[key]!r} after normalization'
            )
            continue

        # Check for duplicate formula_id but only report the second and later occurrences
        if formula_id in formula_id_to_row:
            skipped_formula_validation.append(
                f'{context}: duplicate formula rule id {formula_id!r} (first occurrence at row {formula_id_to_row[formula_id]})'
            )
            continue

        normalized_by_drug[key] = formula_id
        formula_ids.append(formula_id)
        formula_id_to_row[formula_id] = row_number
        prepared_rows.append((row, normalized_expression, referenced_ids))

    # Warn about duplicate/conflict validation issues
    if skipped_formula_validation:
        logger.warning(
            '%d formula rule(s) skipped due to duplicates or conflicts:\n%s',
            len(skipped_formula_validation),
            '\n'.join(f'  - {msg}' for msg in skipped_formula_validation),
        )

    if expected_group_ids:
        provided_group_ids = set(formula_ids)
        missing_group_ids = sorted(expected_group_ids - provided_group_ids)
        if missing_group_ids:
            missing_list = ', '.join(repr(group_id) for group_id in missing_group_ids)
            logger.warning(
                'missing formula rule(s) for group id(s): %s',
                missing_list
            )

        unknown_group_ids = sorted(provided_group_ids - expected_group_ids)
        if unknown_group_ids:
            unknown_list = ', '.join(
                f'{group_id!r} (row {formula_id_to_row.get(group_id, "?")})'
                for group_id in unknown_group_ids
            )
            logger.warning(
                'formula rule(s) reference unknown atomic rule id(s) from grouped rules: %s',
                unknown_list
            )

    referenced_atomic_ids = {
        ref_id
        for _, _, referenced_ids in prepared_rows
        for ref_id in referenced_ids
    }
    rule_ids_by_external_id = _load_rule_ids_by_external_id(conn, referenced_atomic_ids)

    inserted = 0
    skipped_formula_rules: list[str] = []
    for row, normalized_expression, referenced_ids in prepared_rows:
        formula_id = _get_value(row, 'group_id', 'formula_id')
        drug_name = _get_value(row, 'antiviral')
        missing_members = sorted(ref_id for ref_id in referenced_ids if ref_id not in rule_ids_by_external_id)
        if missing_members:
            # Member rules were skipped during atomic import or are unknown; skip this formula rule.
            reasons = []
            for m in missing_members:
                if skipped_atomic_ids and m in skipped_atomic_ids:
                    reasons.append(f'{m!r} ({skipped_atomic_ids[m]})')
                elif declared_atomic_ids and m not in declared_atomic_ids:
                    reasons.append(f'{m!r} (unknown atomic rule id)')
                else:
                    reasons.append(repr(m))
            skipped_formula_rules.append(
                f'{formula_id!r}: member(s) not imported: ' + ', '.join(reasons)
            )
            continue

        phenotype_value, clinical_phenotype_value = _normalize_phenotypes_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        ic50_value = _normalize_ic50_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        fold_ic50_value = _normalize_fold_ic50_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        score_value = _normalize_score_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        comment_value = _append_contradictory_comment(
            _get_value(row, 'comment'),
            phenotype=phenotype_value,
            clinical_phenotype=clinical_phenotype_value,
        )

        drug_id = _get_or_create_drug_id(conn, project_id, drug_name, drug_cache)
        formula_id_exists, normalized_exists = _formula_rule_exists(
            conn,
            formula_id=formula_id,
            drug_id=drug_id,
            normalized_expression=normalized_expression,
        )
        if formula_id_exists:
            errors.append(f'Formula rule {formula_id!r}: formula_id already exists in project')
            continue
        if normalized_exists:
            errors.append(
                f'Formula rule {formula_id!r}: duplicate normalized expression for drug {drug_name!r}'
            )
            continue

        cur = conn.execute(
            'INSERT INTO resistance_formula_rule '
            '('
            'drug_id, formula_id, label, normalized_expression, phenotype, '
            'clinical_phenotype, ic50, fold_ic50, score, source, comment'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                drug_id,
                formula_id,
                _get_value(row, 'label'),
                normalized_expression,
                phenotype_value,
                clinical_phenotype_value,
                ic50_value,
                fold_ic50_value,
                score_value,
                _get_value(row, 'source'),
                comment_value,
            ),
        )
        formula_rule_id = int(cur.lastrowid)

        for ref_id in sorted(referenced_ids):
            conn.execute(
                'INSERT INTO resistance_formula_rule_member (formula_rule_id, rule_id) VALUES (?, ?)',
                (formula_rule_id, rule_ids_by_external_id[ref_id]),
            )

        raw_publication = _get_value(row, 'publication')
        if raw_publication:
            _link_formula_rule_publications(
                conn,
                formula_rule_id,
                raw_publication,
                additional_info,
                pub_cache,
                publication_lookup_failures,
            )
        inserted += 1

    if skipped_formula_rules:
        logger.warning(
            '%d formula rule(s) skipped — one or more member rules were not imported:\n%s',
            len(skipped_formula_rules),
            '\n'.join(f'  - {msg}' for msg in skipped_formula_rules),
        )

    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    logger.info('Loaded %d formula resistance rule(s)', inserted)
    return inserted
