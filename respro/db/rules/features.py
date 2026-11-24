"""Feature lookup, coordinate-base detection, and reference-AA validation for rule imports."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from respro.db.rules.normalize import get_value

logger = logging.getLogger(__name__)


def build_feature_lookup(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Index project features by canonical name, locus_tag, protein_id, and protein alias."""
    rows = conn.execute(
        'SELECT g.id AS feature_id, g.name AS feature_name, g.protein, g.protein_id, '
        'g.locus_tag, g.aa_sequence, r.name AS reference_name, r.accession AS reference_accession '
        'FROM feature g JOIN reference r ON r.id = g.reference_id ORDER BY g.id'
    ).fetchall()

    features_by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entries = (
            (row['feature_name'], 0),
            (row['locus_tag'], 1),
            (row['protein_id'], 2),
            (row['protein'], 3),
        )
        seen_keys: set[str] = set()
        for raw_key, alias_rank in entries:
            key = (raw_key or '').strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            features_by_name.setdefault(key, []).append({
                'feature_id': row['feature_id'],
                'feature_name': row['feature_name'],
                'aa_sequence': row['aa_sequence'],
                'reference_name': row['reference_name'],
                'reference_accession': row['reference_accession'],
                'alias_rank': alias_rank,
            })
    return features_by_name


def _narrow_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer canonical feature-name matches (alias_rank == 0) before alias matches."""
    if not candidates:
        return []
    canonical = [c for c in candidates if int(c['alias_rank']) == 0]
    return canonical or candidates


def resolve_rule_feature_id(
    candidates: list[dict[str, Any]],
    reference_identifier: str,
) -> int | None:
    """Resolve feature candidates and optional *reference_identifier* to one ``feature_id``."""
    narrowed = _narrow_candidates(candidates)
    if not narrowed:
        return None
    if len(narrowed) == 1 and not reference_identifier:
        return int(narrowed[0]['feature_id'])
    if not reference_identifier:
        return None
    matched = [
        c for c in narrowed
        if reference_identifier in {c['reference_name'], c['reference_accession']}
    ]
    return int(matched[0]['feature_id']) if len(matched) == 1 else None


def get_feature_aa_sequence(
    candidates: list[dict[str, Any]],
    reference_identifier: str,
) -> str:
    """Return the stored ``aa_sequence`` for the unambiguously matching feature candidate."""
    narrowed = _narrow_candidates(candidates)
    if reference_identifier:
        matched = [
            c for c in narrowed
            if reference_identifier in {c['reference_name'], c['reference_accession']}
        ]
        return str(matched[0]['aa_sequence'] or '') if len(matched) == 1 else ''
    if len(narrowed) == 1:
        return str(narrowed[0]['aa_sequence'] or '')
    return ''


def _iter_verifiable_rows(
    rows: list[dict],
    features_by_name: dict[str, list[dict[str, Any]]],
    allowed_reference_identifiers: set[str] | None,
):
    """Yield ``(feature_name, ref_aa, pos_raw, pos_int, ref_id, aa_seq)`` for checkable rows."""
    for row in rows:
        feature_name = get_value(row, 'feature')
        ref_aa = get_value(row, 'reference')
        position_raw = get_value(row, 'position')
        if not ref_aa or not position_raw or feature_name not in features_by_name:
            continue

        reference_identifier = get_value(row, 'reference_identifier')
        if (
            allowed_reference_identifiers is not None
            and reference_identifier
            and reference_identifier not in allowed_reference_identifiers
        ):
            continue
        aa_seq = get_feature_aa_sequence(features_by_name[feature_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError as exc:
            logger.debug(
                'Skipping non-integer rule position %r for feature %r: %s',
                position_raw, feature_name, exc,
            )
            continue
        yield feature_name, ref_aa, position_raw, pos, reference_identifier, aa_seq


def detect_coordinate_base(
    rows: list[dict],
    features_by_name: dict[str, list[dict[str, Any]]],
    allowed_reference_identifiers: set[str] | None = None,
) -> int:
    """Detect whether rules TSV positions are 0-based or 1-based by checking feature AA sequences."""
    matches_1based = 0
    matches_0based = 0
    verifiable = 0

    for _feature, ref_aa, _pos_raw, pos, _ref_id, aa_seq in _iter_verifiable_rows(
        rows, features_by_name, allowed_reference_identifiers,
    ):
        anchor_ref = ref_aa[0].upper()
        verifiable += 1
        if 1 <= pos <= len(aa_seq) and aa_seq[pos - 1].upper() == anchor_ref:
            matches_1based += 1
        if 0 <= pos < len(aa_seq) and aa_seq[pos].upper() == anchor_ref:
            matches_0based += 1

    if verifiable == 0:
        logger.warning(
            'Cannot verify coordinate base — no rules have both a reference AA '
            'and a feature with aa_sequence; assuming 1-based'
        )
        return 1
    if matches_1based > matches_0based:
        return 1
    if matches_0based > matches_1based:
        return 0
    if matches_1based == matches_0based > 0:
        logger.warning(
            'Both 0-based and 1-based positions match all %d verifiable rules; '
            'assuming 1-based (standard biochemistry convention)',
            verifiable,
        )
        return 1

    raise ValueError(
        f'Rules TSV coordinate system could not be determined: none of the '
        f'{verifiable} verifiable reference AAs match the feature sequences in either '
        'a 0-based or 1-based interpretation. '
        'Check that the reference amino acids in the rules file match the GenBank sequence.'
    )


def validate_reference_amino_acids(
    rows: list[dict],
    features_by_name: dict[str, list[dict[str, Any]]],
    coord_base: int,
    allowed_reference_identifiers: set[str] | None = None,
) -> set[tuple[str, str, str, str]]:
    """Warn on out-of-range or mismatched reference AAs and return their skip keys."""
    mismatch_keys: set[tuple[str, str, str, str]] = set()
    mismatch_details: list[str] = []
    out_of_range: list[str] = []

    for feature_name, ref_aa, position_raw, pos, reference_identifier, aa_seq in _iter_verifiable_rows(
        rows, features_by_name, allowed_reference_identifiers,
    ):
        pos_0based = pos - coord_base
        ref_block = ref_aa.upper()
        end_pos = pos_0based + len(ref_block)
        key = (feature_name, position_raw, reference_identifier, ref_aa)

        if 0 <= pos_0based and end_pos <= len(aa_seq):
            actual = aa_seq[pos_0based:end_pos].upper()
            if actual != ref_block and key not in mismatch_keys:
                mismatch_details.append(
                    f'  {reference_identifier} feature {feature_name!r} pos {pos} ({coord_base}-based): '
                    f'rule says {ref_aa!r}, feature sequence has {actual!r} — rule will be skipped'
                )
                mismatch_keys.add(key)
        elif key not in mismatch_keys:
            out_of_range.append(
                f'  {reference_identifier} feature {feature_name!r} pos {pos} ({coord_base}-based): '
                f'out of range (aa_sequence length = {len(aa_seq)}) — rule will be skipped'
            )
            mismatch_keys.add(key)

    if out_of_range:
        logger.warning(
            '%d position(s) with rules reference coordinates beyond the end of the '
            'annotated protein and will be skipped:\n%s',
            len(out_of_range), '\n'.join(out_of_range),
        )
    if mismatch_details:
        logger.warning(
            '%d position(s) with rules have reference AA mismatches and will be skipped:\n%s',
            len(mismatch_details), '\n'.join(mismatch_details),
        )
    return mismatch_keys
