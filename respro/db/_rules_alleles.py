"""
Mutation and allele normalization for resistance rule imports.
"""

from __future__ import annotations

import logging
import re

from respro.core.annotation import normalize_mutation
from respro.db._rules_normalize import _get_value

logger = logging.getLogger(__name__)

# Detects anchor-less deletion tokens emitted by companion database converters.
# Form: one or more AA letters + position digits + "del" (case-insensitive), e.g. "Q35del", "DD676del".
_RE_ANCHORLESS_DEL = re.compile(r'^([A-Za-z]+)\d+del$', re.IGNORECASE)
_RE_REWRITE_TOKEN = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$')
_AUTO_SPLIT_GROUP_PREFIX = '__auto_anchor_split_row_'


def _is_noop_mutation(reference_aa: str, mutation: str) -> bool:
    """Return True when a rule encodes no amino-acid change."""
    return reference_aa.upper() == mutation.upper()


def _is_supported_mutation_token(mutation: str) -> bool:
    """Return True when a normalized mutation token uses supported AA letters."""
    aa_letters = frozenset('ACDEFGHIKLMNPQRSTVWY')

    token = mutation.upper()
    if token == 'ANY':
        return False
    if token in {'FSX', '*'} or (token.endswith('FSX') and len(token) == 4):
        return True
    if re.fullmatch(r'[A-Z]+', token):
        return set(token) <= aa_letters
    return False


def _normalize_rule_alleles_for_storage(
    *,
    reference_aa: str,
    mutation_raw: str,
    position_0based: int,
    context: str,
    errors: list[str],
) -> tuple[int, str, str] | None:
    """
    Normalize one rule row to canonical DB storage columns.

    :param reference_aa: value from TSV reference column
    :param mutation_raw: value from TSV mutation column
    :param position_0based: row position converted to 0-based
    :param context: human-readable context for validation errors
    :param errors: shared error collector
    :return: (position_0based, reference, mutation) or None on validation failure
    """
    reference = reference_aa.strip().upper()
    mutation_input = mutation_raw.strip()
    if not reference or not mutation_input:
        return None

    token_upper = mutation_input.upper()
    is_direct_aa_token = (
        re.fullmatch(r'[A-Za-z*]+', mutation_input) is not None
        and not token_upper.startswith('INS')
        and token_upper != 'DEL'
    )

    if is_direct_aa_token:
        if token_upper in {'*', 'STOP'}:
            mutation = '*'
        elif token_upper.startswith('FS') or token_upper.startswith('FRAMESHIFT'):
            mutation = f'{reference}fsX'
        elif token_upper.endswith('FSX') and len(token_upper) == 4:
            mutation = f'{reference}fsX'
        else:
            mutation = token_upper
    else:
        mutation = normalize_mutation(
            mutation_input,
            reference=reference,
            position_1based=position_0based + 1,
        )
        if mutation is None:
            errors.append(f'{context}: unrecognised mutation {mutation_raw!r}')
            return None

    if mutation == 'fsX':
        mutation = f'{reference}fsX'

    rewrite_match = _RE_REWRITE_TOKEN.fullmatch(mutation.upper())
    if rewrite_match is None:
        return position_0based, reference, mutation

    left, pos_text, right = rewrite_match.groups()

    # SNP-style rewrites keep mutation-only storage (alt AA token).
    if len(left) == 1 and len(right) == 1:
        return position_0based, reference, right

    anchor_pos_0based = int(pos_text) - 1
    if anchor_pos_0based != position_0based:
        errors.append(
            f'{context}: position {position_0based + 1} conflicts with mutation token '
            f'{mutation!r} (anchor position is {anchor_pos_0based + 1})'
        )
        return None

    return anchor_pos_0based, left, right


def _expand_anchor_changed_indel_rules(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Expand mixed anchor-change indel rows into explicit two-member grouped rows.

    Rows that cannot be split safely are returned unchanged and continue through
    normal validation.
    """
    expanded_rows: list[dict[str, str]] = []
    split_count = 0

    for row_number, row in enumerate(rows, start=2):
        mutation_raw = _get_value(row, 'mutation')
        position_raw = _get_value(row, 'position')
        reference_raw = _get_value(row, 'reference').upper()

        split = _split_anchor_changed_indel_token(mutation_raw)
        if split is None:
            expanded_rows.append(row)
            continue

        rewrite_match = _RE_REWRITE_TOKEN.fullmatch(mutation_raw.upper())
        if rewrite_match is None:
            expanded_rows.append(row)
            continue
        left, token_pos_text, _ = rewrite_match.groups()

        try:
            token_pos = int(token_pos_text)
            row_pos = int(position_raw)
        except ValueError:
            expanded_rows.append(row)
            continue

        if token_pos != row_pos:
            expanded_rows.append(row)
            continue

        if reference_raw and reference_raw not in {left, left[0]}:
            expanded_rows.append(row)
            continue

        sub_ref, sub_mut, indel_ref, indel_mut = split
        group_name = _get_value(row, 'group_id', 'rule_group') or (
            f'{_AUTO_SPLIT_GROUP_PREFIX}{row_number}'
        )

        substitution_row = dict(row)
        substitution_row['reference'] = sub_ref
        substitution_row['mutation'] = sub_mut
        substitution_row['group_id'] = group_name
        member_id = _get_value(row, 'member_id', 'rule_id')
        if member_id:
            substitution_row['member_id'] = f'{member_id}__sub'
        else:
            substitution_row['member_id'] = f'{group_name}__sub'

        indel_row = dict(row)
        indel_row['reference'] = indel_ref
        indel_row['mutation'] = indel_mut
        indel_row['group_id'] = group_name
        if member_id:
            indel_row['member_id'] = f'{member_id}__indel'
        else:
            indel_row['member_id'] = f'{group_name}__indel'

        expanded_rows.extend([substitution_row, indel_row])
        split_count += 1

    if split_count:
        logger.info(
            'Expanded %d mixed anchor-change indel rule(s) into explicit combo members',
            split_count,
        )

    return expanded_rows


def _split_anchor_changed_indel_token(
    mutation_raw: str,
) -> tuple[str, str, str, str] | None:
    """
    Split mixed anchor-change indel rewrite tokens into two events.

    Examples:
    - ``GY50A`` -> ``G50A`` + ``GY50G``
    - ``G50AW`` -> ``G50A`` + ``G50GW``

    Returns tuple ``(sub_ref, sub_mut, indel_ref, indel_mut)`` using canonical
    storage alleles (reference/mutation columns), or ``None`` when the token is
    not a mixed anchor-change indel.
    """
    lowered = mutation_raw.lower()
    if (
        lowered.endswith('del')
        or 'ins' in lowered
        or 'frameshift' in lowered
        or 'stop' in lowered
    ):
        return None

    match = _RE_REWRITE_TOKEN.fullmatch(mutation_raw.upper())
    if match is None:
        return None

    left, _, right = match.groups()
    if (
        right.startswith('DEL')
        or right.startswith('INS')
        or right.startswith('FS')
        or right.startswith('STOP')
    ):
        return None

    if len(left) == len(right):
        return None
    if left[0] == right[0]:
        return None

    left_tail = left[1:]
    right_tail = right[1:]

    # Simple insertion/deletion around a changed anchor: after removing the
    # first AA, one side must be a strict prefix extension of the other.
    if not (
        (right_tail.startswith(left_tail) and len(right_tail) > len(left_tail))
        or (left_tail.startswith(right_tail) and len(left_tail) > len(right_tail))
    ):
        return None

    sub_ref = left[0]
    sub_mut = right[0]
    indel_ref = left
    indel_mut = left[0] + right_tail
    return sub_ref, sub_mut, indel_ref, indel_mut


def _resolve_anchorless_deletion(
    deleted_block: str,
    position_0based: int,
    aa_seq: str,
) -> tuple[int, str, str] | None:
    """
    Resolve an anchor-less deletion token to canonical form.

    The anchor residue is the AA immediately preceding the deleted block.  It is
    fetched from the feature sequence and used to build the canonical deletion token
    ``ANCHOR + DELETED_BLOCK + ANCHOR_POS_1BASED + ANCHOR``.

    :param deleted_block: uppercase deleted AA block (e.g. ``'Q'`` or ``'DD'``)
    :param position_0based: 0-based start index of the deletion in the feature sequence
    :param aa_seq: amino-acid sequence of the feature
    :return: ``(anchor_position_0based, anchor_aa, canonical_mutation)`` or ``None``
             when the anchor cannot be resolved (position 0, or block mismatch)
    """
    anchor_idx = position_0based - 1
    if anchor_idx < 0:
        return None  # no preceding residue

    end_idx = position_0based + len(deleted_block)
    if end_idx > len(aa_seq):
        return None  # deletion extends beyond sequence

    actual_block = aa_seq[position_0based:end_idx].upper()
    if actual_block != deleted_block.upper():
        return None  # feature sequence does not match claimed deleted block

    anchor_aa = aa_seq[anchor_idx].upper()
    anchor_pos_1based = anchor_idx + 1
    canonical = f'{anchor_aa}{deleted_block.upper()}{anchor_pos_1based}{anchor_aa}'
    return anchor_idx, anchor_aa, canonical
