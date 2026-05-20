"""
Feature lookup and coordinate detection for resistance rule imports.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.db._rules_normalize import _get_value

logger = logging.getLogger(__name__)


def _resolve_rule_feature_id(candidates: list[sqlite3.Row], reference_identifier: str) -> int | None:
    """Resolve a rule row to a unique feature_id using optional reference information."""
    narrowed = _narrow_feature_lookup_candidates(candidates)
    if not narrowed:
        return None
    if len(narrowed) == 1 and not reference_identifier:
        return narrowed[0]['feature_id']
    if not reference_identifier:
        return None

    matched = [
        c for c in narrowed
        if reference_identifier in {c['reference_name'], c['reference_accession']}
    ]
    if len(matched) == 1:
        return matched[0]['feature_id']
    return None


def _detect_coordinate_base(
    rows: list[dict],
    features_by_name: dict[str, list[sqlite3.Row]],
    allowed_reference_identifiers: set[str] | None = None,
) -> int:
    """
    Detect whether the rules TSV uses 0-based or 1-based amino acid positions.

    Compares the ``reference`` column against the pre-translated ``aa_sequence``
    stored for each feature. Returns 1 if all verifiable positions match the
    1-based interpretation, 0 if they match the 0-based interpretation.

    :param rows: all parsed rows from the rules TSV
    :param features_by_name: feature lookup built from the project DB
    :param allowed_reference_identifiers: optional set of reference identifiers
        present in supplied GenBank files; rows targeting other references are
        ignored for detection
    :return: 0 or 1 indicating the detected coordinate base
    :raises ValueError: if positions match neither system consistently
    """
    matches_1based = 0
    matches_0based = 0
    verifiable = 0

    for row in rows:
        # Only rows with feature + position + reference AA can contribute to detection.
        feature_name = _get_value(row, 'feature')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or feature_name not in features_by_name:
            continue
        anchor_ref = ref_aa[0].upper()

        reference_identifier = _get_value(row, 'reference_identifier')
        if (
            allowed_reference_identifiers is not None
            and reference_identifier
            and reference_identifier not in allowed_reference_identifiers
        ):
            continue
        aa_seq = _get_feature_aa_sequence(features_by_name[feature_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError as exc:
            logger.debug(
                'Skipping non-integer rule position %r for feature %r during coordinate-base detection: %s',
                position_raw,
                feature_name,
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
            'and a feature with aa_sequence; assuming 1-based'
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
        f'{verifiable} verifiable reference AAs match the feature sequences in either '
        'a 0-based or 1-based interpretation. '
        'Check that the reference amino acids in the rules file match the GenBank sequence.'
    )


def _validate_reference_amino_acids(
    rows: list[dict],
    features_by_name: dict[str, list[sqlite3.Row]],
    coord_base: int,
    allowed_reference_identifiers: set[str] | None = None,
) -> set[tuple[str, str, str, str]]:
    """
    Check reference AAs in the rules TSV against the stored feature aa_sequence.

    Out-of-range positions and reference AA mismatches are both logged as
    warnings and collected for skipping — callers must filter out returned keys
    before inserting rules.

    :param rows: all parsed rows from the rules TSV
    :param features_by_name: feature lookup with aa_sequence from the project DB
    :param coord_base: detected coordinate base (0 or 1)
    :param allowed_reference_identifiers: optional set of reference identifiers
        present in supplied GenBank files; rows targeting other references are
        ignored for mismatch/out-of-range reporting
    :return: set of ``(feature_name, position_raw, reference_identifier, ref_aa)``
             tuples for rows whose reference AA does not match the feature sequence
    """
    mismatch_keys: set[tuple[str, str, str, str]] = set()
    mismatch_details: list[str] = []
    out_of_range: list[str] = []

    for row in rows:
        feature_name = _get_value(row, 'feature')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or feature_name not in features_by_name:
            continue

        reference_identifier = _get_value(row, 'reference_identifier')
        if (
            allowed_reference_identifiers is not None
            and reference_identifier
            and reference_identifier not in allowed_reference_identifiers
        ):
            continue
        aa_seq = _get_feature_aa_sequence(features_by_name[feature_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError as exc:
            logger.debug(
                'Skipping non-integer rule position %r for feature %r during reference-AA validation: %s',
                position_raw,
                feature_name,
                exc,
            )
            continue

        pos_0based = pos - coord_base
        ref_block = ref_aa.upper()
        end_pos = pos_0based + len(ref_block)
        if 0 <= pos_0based and end_pos <= len(aa_seq):
            actual = aa_seq[pos_0based:end_pos].upper()
            if actual != ref_block:
                mismatch_keys.add((feature_name, position_raw, reference_identifier, ref_aa))
                mismatch_details.append(
                    f'  {reference_identifier} feature {feature_name!r} pos {pos} ({coord_base}-based): '
                    f'rule says {ref_aa!r}, feature sequence has {actual!r} — rule will be skipped'
                )
        else:
            out_of_range.append(
                f'  {reference_identifier} feature {feature_name!r} pos {pos} ({coord_base}-based): '
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


def _get_feature_aa_sequence(
    candidates: list[sqlite3.Row],
    reference_identifier: str,
) -> str:
    """
    Return the aa_sequence for the best-matching feature candidate.

    If a reference_identifier is given, match it; otherwise use the only
    candidate if unambiguous.

    :param candidates: list of feature rows from the DB
    :param reference_identifier: optional reference identifier from the rules row
    :return: amino acid sequence string or empty string if ambiguous/unavailable
    """
    narrowed = _narrow_feature_lookup_candidates(candidates)
    if reference_identifier:
        for c in narrowed:
            if reference_identifier in {c['reference_name'], c['reference_accession']}:
                return c['aa_sequence'] or ''
        return ''
    if len(narrowed) == 1:
        return narrowed[0]['aa_sequence'] or ''
    return ''


def _narrow_feature_lookup_candidates(candidates: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Prefer canonical feature-name matches before alias matches when both are present."""
    if not candidates:
        return []

    canonical = [candidate for candidate in candidates if int(candidate['alias_rank']) == 0]
    if canonical:
        return canonical
    return candidates
