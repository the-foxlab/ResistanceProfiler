"""Amino-acid allele normalization and anchorless deletion resolution for rule imports."""

from __future__ import annotations

import re

from respro.core.codons import normalize_mutation

# Anchor-less deletion tokens (e.g. 'Q35del', 'DD676del') resolved against feature aa_sequence.
RE_ANCHORLESS_DEL = re.compile(r'^([A-Za-z]+)\d+del$', re.IGNORECASE)
RE_REWRITE_TOKEN = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$')
_RE_EMBEDDED_COORD = re.compile(r'^([A-Za-z*]+)(\d+)(.+)$')

_STANDARD_AA_LETTERS = frozenset('ACDEFGHIKLMNPQRSTVWY')


def is_noop_mutation(reference_aa: str, mutation: str) -> bool:
    """Return True when reference and mutation encode no amino-acid change."""
    return reference_aa.upper() == mutation.upper()


def is_supported_mutation_token(mutation: str) -> bool:
    """Return True when a normalized mutation token uses supported amino-acid symbols."""
    token = mutation.upper()
    if token == 'ANY':
        return False
    if mutation == 'INS_any':
        return True
    if token in {'FSX', '*'} or (token.endswith('FSX') and len(token) == 4):
        return True
    if re.fullmatch(r'[A-Z]+', token):
        return set(token) <= _STANDARD_AA_LETTERS
    return False


def normalize_rule_alleles_for_storage(
    *,
    reference_aa: str,
    mutation_raw: str,
    position_0based: int,
    context: str,
    errors: list[str],
) -> tuple[int, str, str] | None:
    """Normalize one TSV rule row to canonical ``(position_0based, reference, mutation)`` columns."""
    reference = reference_aa.strip().upper()
    mutation_input = mutation_raw.strip()
    if not reference or not mutation_input:
        return None

    token_upper = mutation_input.upper()
    is_direct_aa = (
        re.fullmatch(r'[A-Za-z*]+', mutation_input) is not None
        and not token_upper.startswith('INS')
        and token_upper != 'DEL'
    )

    if is_direct_aa:
        if token_upper in {'*', 'STOP'}:
            mutation = '*'
        elif token_upper.startswith(('FS', 'FRAMESHIFT')) or (
            token_upper.endswith('FSX') and len(token_upper) == 4
        ):
            mutation = f'{reference}fsX'
        else:
            mutation = token_upper
    else:
        embedded = _RE_EMBEDDED_COORD.fullmatch(mutation_input)
        if embedded is not None:
            emb_left, emb_pos_text, _ = embedded.groups()
            emb_pos_0based = int(emb_pos_text) - 1
            if emb_pos_0based != position_0based:
                errors.append(
                    f'{context}: position {position_0based + 1} conflicts with mutation token '
                    f'{mutation_raw!r} (embedded position is {emb_pos_0based + 1})'
                )
                return None
            emb_left_u = emb_left.upper()
            if not (emb_left_u.startswith(reference) or reference.startswith(emb_left_u)):
                errors.append(
                    f'{context}: reference AA {reference!r} conflicts with mutation token '
                    f'{mutation_raw!r} (embedded reference is {emb_left_u!r})'
                )
                return None

        normalized = normalize_mutation(
            mutation_input,
            reference=reference,
            position_1based=position_0based + 1,
        )
        if normalized is None:
            errors.append(f'{context}: unrecognised mutation {mutation_raw!r}')
            return None
        mutation = normalized

    if mutation == 'fsX':
        mutation = f'{reference}fsX'

    rewrite_match = RE_REWRITE_TOKEN.fullmatch(mutation.upper())
    if rewrite_match is None:
        return position_0based, reference, mutation

    left, pos_text, right = rewrite_match.groups()
    anchor_pos_0based = int(pos_text) - 1
    if anchor_pos_0based != position_0based:
        errors.append(
            f'{context}: position {position_0based + 1} conflicts with mutation token '
            f'{mutation!r} (anchor position is {anchor_pos_0based + 1})'
        )
        return None
    return anchor_pos_0based, left, right


def resolve_anchorless_deletion(
    deleted_block: str,
    position_0based: int,
    aa_seq: str,
) -> tuple[int, str, str] | None:
    """Resolve an anchorless deletion against *aa_seq* to ``(anchor_pos, anchor_aa, token)``."""
    anchor_idx = position_0based - 1
    end_idx = position_0based + len(deleted_block)
    if anchor_idx < 0 or end_idx > len(aa_seq):
        return None
    if aa_seq[position_0based:end_idx].upper() != deleted_block.upper():
        return None
    anchor_aa = aa_seq[anchor_idx].upper()
    canonical = f'{anchor_aa}{deleted_block.upper()}{anchor_idx + 1}{anchor_aa}'
    return anchor_idx, anchor_aa, canonical
