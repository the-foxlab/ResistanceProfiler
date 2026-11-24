"""VCF record grouping and validation against the supplied reference and feature alignment."""

from __future__ import annotations

from dataclasses import dataclass

from Bio.Seq import Seq

from respro.core.vcf_to_reference.reference_alignment import FeatureAlignment, SuppliedPosition
from respro.db.models import VariantCall

_AF_MASS_EPS = 1e-9

_RecordKey = tuple[str, int, str, int]


@dataclass(frozen=True, slots=True)
class ValidatedCall:
    """One validated VCF ALT call ready to be mapped into internal-reference coordinates."""

    call: VariantCall
    positions: list[SuppliedPosition]  # REF span in coding orientation
    coding_alt: str  # ALT sequence in coding orientation
    record_id: int = -1
    alt_id: int = -1


@dataclass(frozen=True, slots=True)
class SkippedCall:
    """One rejected VCF call (or unphased overlapping set) with its user-facing warning."""

    kind: str  # 'ref_mismatch' | 'unmapped' | 'overlapping_deletion' | 'af_mass' | 'phase'
    pos: int | None  # 1-based VCF position when position-specific
    message: str


def validate_vcf_calls(
    alignment: FeatureAlignment,
    variants: list[VariantCall],
    supplied_upper: str,
) -> tuple[list[ValidatedCall], list[SkippedCall]]:
    """Group VCF calls by record, run validation gates, and return ``(valid_calls, skipped)``."""
    groups: dict[_RecordKey, list[VariantCall]] = {}
    order: list[_RecordKey] = []
    for var in variants:
        key = (var.chrom, var.pos, var.ref, var.record_index)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(var)

    skipped: list[SkippedCall] = []
    positions_by_key: dict[_RecordKey, list[SuppliedPosition] | None] = {}
    for key in order:
        positions, rejection = _validate_record_group(alignment, groups[key], supplied_upper)
        positions_by_key[key] = positions
        if rejection is not None:
            skipped.append(rejection)

    verified = [key for key in order if positions_by_key.get(key) is not None]
    conflicting = _find_overlapping_records(verified)
    if conflicting:
        span_text = ', '.join(
            f'{key[0]}:{key[1] + 1}{key[2]}>' + '/'.join(c.alt for c in groups[key])
            for key in verified if key in conflicting
        )
        skipped.append(SkippedCall(
            kind='phase',
            pos=None,
            message='Overlapping VCF records cannot be phased together — variants skipped: ' + span_text,
        ))

    record_id_by_key = _assign_record_ids(order)
    valid_calls: list[ValidatedCall] = []
    for key in order:
        key_positions = positions_by_key.get(key)
        if key_positions is None or key in conflicting:
            continue
        record_id = record_id_by_key[key]
        used_alt_ids = {c.alt_index for c in groups[key] if c.alt_index >= 0}
        next_alt_id = max(used_alt_ids, default=-1) + 1
        coding_positions = key_positions if alignment.match.strand == '+' else list(reversed(key_positions))
        for call in groups[key]:
            if call.alt_index >= 0:
                alt_id = call.alt_index
            else:
                alt_id = next_alt_id
                next_alt_id += 1
            coding_alt = (
                call.alt if alignment.match.strand == '+' else str(Seq(call.alt).reverse_complement())
            )
            valid_calls.append(ValidatedCall(
                call=call,
                positions=coding_positions,
                coding_alt=coding_alt,
                record_id=record_id,
                alt_id=alt_id,
            ))
    return valid_calls, skipped


def _validate_record_group(
    alignment: FeatureAlignment,
    group: list[VariantCall],
    supplied_upper: str,
) -> tuple[list[SuppliedPosition] | None, SkippedCall | None]:
    """Check one VCF record against the supplied sequence, alignment bounds, and AF limits."""
    var = group[0]
    ref_len = len(var.ref)

    # Gate 1: VCF REF must match the supplied reference sequence at POS.
    supplied = supplied_upper[var.pos:var.pos + ref_len]
    if supplied != var.ref.upper():
        return None, SkippedCall(
            kind='ref_mismatch',
            pos=var.pos + 1,
            message=(
                f'pos {var.pos + 1}: VCF REF {var.ref!r} does not match the supplied '
                f'reference {supplied!r} — variant skipped'
            ),
        )

    # Gate 2: every REF position must lie inside the aligned feature region.
    positions: list[SuppliedPosition] = []
    for offset in range(ref_len):
        pos = alignment.position_map.get(var.pos + offset)
        if pos is None or not _inserted_base_matches(alignment, pos, supplied_upper):
            return None, SkippedCall(
                kind='unmapped',
                pos=var.pos + 1,
                message=(
                    f'pos {var.pos + 1}: VCF {var.ref}>{var.alt} is outside the aligned '
                    'region — variant skipped'
                ),
            )
        positions.append(pos)

    # Gate 3: VCF deletions or multi-base REF spans cannot overlap/touch a reference deletion.
    col_lo = min(p.column for p in positions)
    col_hi = max(p.column for p in positions)
    is_deletion = any(len(c.alt) < ref_len for c in group)
    check_lo = max(0, col_lo - 1) if is_deletion else col_lo
    check_hi = min(len(alignment.columns) - 1, col_hi + 1) if is_deletion else col_hi
    if (is_deletion or ref_len > 1) and any(
        alignment.columns[c].supplied == '-' for c in range(check_lo, check_hi + 1)
    ):
        label = 'VCF deletion' if is_deletion else 'VCF'
        return None, SkippedCall(
            kind='overlapping_deletion',
            pos=var.pos + 1,
            message=(
                f'pos {var.pos + 1}: {label} {var.ref}>{var.alt} overlaps a '
                'reference deletion — variant skipped'
            ),
        )

    # Gate 4: mutually exclusive ALTs of one VCF record cannot exceed total frequency mass 1.
    total_af = sum(call.allele_freq for call in group)
    if total_af > 1.0 + _AF_MASS_EPS:
        return None, SkippedCall(
            kind='af_mass',
            pos=var.pos + 1,
            message=(
                f'pos {var.pos + 1}: allele frequencies sum to {total_af:.3f} (> 1) — '
                'variants skipped'
            ),
        )

    return positions, None


def _inserted_base_matches(
    alignment: FeatureAlignment,
    pos: SuppliedPosition,
    supplied_upper: str,
) -> bool:
    """Confirm an inserted position's stored base matches the supplied reference."""
    if pos.kind == 'aligned':
        return True
    inserted = alignment.columns[pos.column].inserted
    if pos.insert_offset < 0 or pos.insert_offset >= len(inserted):
        return False
    expected = (
        supplied_upper[pos.query_pos]
        if alignment.match.strand == '+'
        else str(Seq(supplied_upper[pos.query_pos]).complement())
    )
    return inserted[pos.insert_offset] == expected


def _find_overlapping_records(verified: list[_RecordKey]) -> set[_RecordKey]:
    """Return verified VCF record keys whose supplied-reference REF spans overlap."""
    conflicting: set[_RecordKey] = set()
    for i, key_a in enumerate(verified):
        a_start, a_end = key_a[1], key_a[1] + len(key_a[2]) - 1
        for key_b in verified[i + 1:]:
            b_start, b_end = key_b[1], key_b[1] + len(key_b[2]) - 1
            if a_start <= b_end and b_start <= a_end:
                conflicting.add(key_a)
                conflicting.add(key_b)
    return conflicting


def _assign_record_ids(order: list[_RecordKey]) -> dict[_RecordKey, int]:
    """Keep parser record indices and assign unique synthetic IDs where absent."""
    used_ids = {key[3] for key in order if key[3] >= 0}
    next_id = max(used_ids, default=-1) + 1
    assigned: dict[_RecordKey, int] = {}
    for key in order:
        if key[3] >= 0:
            assigned[key] = key[3]
        else:
            assigned[key] = next_id
            next_id += 1
    return assigned
