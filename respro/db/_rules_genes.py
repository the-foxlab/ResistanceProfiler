"""
Gene lookup and coordinate detection for resistance rule imports.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.db._rules_normalize import _get_value

logger = logging.getLogger(__name__)


def _resolve_rule_gene_id(candidates: list[sqlite3.Row], reference_identifier: str) -> int | None:
    """Resolve a rule row to a unique gene_id using optional reference information."""
    narrowed = _narrow_gene_lookup_candidates(candidates)
    if not narrowed:
        return None
    if len(narrowed) == 1 and not reference_identifier:
        return narrowed[0]['gene_id']
    if not reference_identifier:
        return None

    matched = [
        c for c in narrowed
        if reference_identifier in {c['reference_name'], c['reference_accession']}
    ]
    if len(matched) == 1:
        return matched[0]['gene_id']
    return None


def _detect_coordinate_base(
    rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
) -> int:
    """
    Detect whether the rules TSV uses 0-based or 1-based amino acid positions.

    Compares the ``reference`` column against the pre-translated ``aa_sequence``
    stored for each gene. Returns 1 if all verifiable positions match the
    1-based interpretation, 0 if they match the 0-based interpretation.

    :param rows: all parsed rows from the rules TSV
    :param genes_by_name: gene lookup built from the project DB
    :return: 0 or 1 indicating the detected coordinate base
    :raises ValueError: if positions match neither system consistently
    """
    matches_1based = 0
    matches_0based = 0
    verifiable = 0

    for row in rows:
        # Only rows with gene + position + reference AA can contribute to detection.
        gene_name = _get_value(row, 'gene')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or gene_name not in genes_by_name:
            continue
        anchor_ref = ref_aa[0].upper()

        reference_identifier = _get_value(row, 'reference_identifier')
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError as exc:
            logger.debug(
                'Skipping non-integer rule position %r for gene %r during coordinate-base detection: %s',
                position_raw,
                gene_name,
                exc,
            )
            continue

        verifiable += 1
        if 1 <= pos <= len(aa_seq) and aa_seq[pos - 1].upper() == anchor_ref:
            matches_1based += 1
        if 0 <= pos < len(aa_seq) and aa_seq[pos].upper() == anchor_ref:
            matches_0based += 1

    if verifiable == 0:
        # Keep initialization usable when source rules do not carry verifiable ref AAs.
        logger.warning(
            'Cannot verify coordinate base — no rules have both a reference AA '
            'and a gene with aa_sequence; assuming 1-based'
        )
        return 1

    if matches_1based > matches_0based:
        return 1
    if matches_0based > matches_1based:
        return 0

    # Equal non-zero: both systems match the same set of positions (e.g. all
    # ref AAs happen to be identical at both offsets). Default to 1-based.
    if matches_1based == matches_0based > 0:
        logger.warning(
            'Both 0-based and 1-based positions match all %d verifiable rules; '
            'assuming 1-based (standard biochemistry convention)',
            verifiable,
        )
        return 1

    # Nothing matched in either system
    raise ValueError(
        f'Rules TSV coordinate system could not be determined: none of the '
        f'{verifiable} verifiable reference AAs match the gene sequences in either '
        'a 0-based or 1-based interpretation. '
        'Check that the reference amino acids in the rules file match the GenBank sequence.'
    )


def _validate_reference_amino_acids(
    rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
    coord_base: int,
) -> set[tuple[str, str, str, str]]:
    """
    Check reference AAs in the rules TSV against the stored gene aa_sequence.

    Out-of-range positions and reference AA mismatches are both logged as
    warnings and collected for skipping — callers must filter out returned keys
    before inserting rules.

    :param rows: all parsed rows from the rules TSV
    :param genes_by_name: gene lookup with aa_sequence from the project DB
    :param coord_base: detected coordinate base (0 or 1)
    :return: set of ``(gene_name, position_raw, reference_identifier, ref_aa)``
             tuples for rows whose reference AA does not match the gene sequence
    """
    mismatch_keys: set[tuple[str, str, str, str]] = set()
    mismatch_details: list[str] = []
    out_of_range: list[str] = []

    for row in rows:
        gene_name = _get_value(row, 'gene')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or gene_name not in genes_by_name:
            continue

        reference_identifier = _get_value(row, 'reference_identifier')
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError as exc:
            logger.debug(
                'Skipping non-integer rule position %r for gene %r during reference-AA validation: %s',
                position_raw,
                gene_name,
                exc,
            )
            continue

        pos_0based = pos - coord_base
        ref_block = ref_aa.upper()
        end_pos = pos_0based + len(ref_block)
        if 0 <= pos_0based and end_pos <= len(aa_seq):
            actual = aa_seq[pos_0based:end_pos].upper()
            if actual != ref_block:
                mismatch_keys.add((gene_name, position_raw, reference_identifier, ref_aa))
                mismatch_details.append(
                    f'  {reference_identifier} gene {gene_name!r} pos {pos} ({coord_base}-based): '
                    f'rule says {ref_aa!r}, gene sequence has {actual!r} — rule will be skipped'
                )
        else:
            out_of_range.append(
                f'  {reference_identifier} gene {gene_name!r} pos {pos} ({coord_base}-based): '
                f'out of range (aa_sequence length = {len(aa_seq)}) — rule will be skipped'
            )

    if out_of_range:
        logger.warning(
            '%d rule(s) reference positions beyond the end of the annotated protein '
            'and will be skipped:\n%s',
            len(out_of_range),
            '\n'.join(out_of_range),
        )

    if mismatch_details:
        logger.warning(
            '%d rule(s) have reference AA mismatches and will be skipped:\n%s',
            len(mismatch_details),
            '\n'.join(mismatch_details),
        )

    return mismatch_keys


def _get_gene_aa_sequence(
    candidates: list[sqlite3.Row],
    reference_identifier: str,
) -> str:
    """
    Return the aa_sequence for the best-matching gene candidate.

    If a reference_identifier is given, match it; otherwise use the only
    candidate if unambiguous.

    :param candidates: list of gene rows from the DB
    :param reference_identifier: optional reference identifier from the rules row
    :return: amino acid sequence string or empty string if ambiguous/unavailable
    """
    narrowed = _narrow_gene_lookup_candidates(candidates)
    if reference_identifier:
        for c in narrowed:
            if reference_identifier in {c['reference_name'], c['reference_accession']}:
                return c['aa_sequence'] or ''
    if len(narrowed) == 1:
        return narrowed[0]['aa_sequence'] or ''
    return ''


def _narrow_gene_lookup_candidates(candidates: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Prefer canonical gene-name matches before alias matches when both are present."""
    if not candidates:
        return []

    canonical = [candidate for candidate in candidates if int(candidate['alias_rank']) == 0]
    if canonical:
        return canonical
    return candidates
