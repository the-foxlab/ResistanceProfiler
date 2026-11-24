"""Map VCF variant calls from a supplied reference into internal project-reference coordinates."""

from __future__ import annotations

import logging

from respro.core.query import QueryRecord, pick_best_reference_id, select_matches_for_reference
from respro.core.vcf_to_reference.apply import (
    AppliedOverlap,
    dedupe_across_features,
    emit_surviving_ref_mismatches,
    merge_identical_variants,
    partition_overlapping_calls,
    remap_direct_call,
    resolve_overlapping_call,
)
from respro.core.vcf_to_reference.reference_alignment import (
    REFERENCE_DERIVED,
    VCF_DERIVED,
    align_supplied_to_feature,
    find_reference_mismatches,
)
from respro.core.vcf_to_reference.validate import SkippedCall, validate_vcf_calls
from respro.db.models import FeatureMatch, VariantCall

logger = logging.getLogger(__name__)

__all__ = [
    'REFERENCE_DERIVED',
    'VCF_DERIVED',
    'map_vcf_to_matches',
    'map_vcf_to_references',
]


def map_vcf_to_references(
    variants: list[VariantCall],
    query_records: list[QueryRecord],
) -> tuple[list[VariantCall], list[str], list[str]]:
    """Map VCF calls across all CHROMs into internal project-reference coordinates.

    Returns ``(mapped_variants, warnings, dropped_chroms)``. Raises ``ValueError``
    when variants are present but no CHROM maps to a resolved reference record.
    """
    records_by_name = {rec.query_name: rec for rec in query_records}

    grouped: dict[str, list[VariantCall]] = {}
    for var in variants:
        grouped.setdefault(var.chrom, []).append(var)

    dropped_chroms = sorted(chrom for chrom in grouped if chrom not in records_by_name)
    mapped_all: list[VariantCall] = []
    warnings_all: list[str] = []

    # Iterate every matched reference record (not only CHROMs with passing VCF calls)
    # so contigs with zero calls still emit their supplied-vs-internal reference differences.
    for record in query_records:
        if not record.feature_matches:
            continue
        group = grouped.get(record.query_name, [])
        ref_id = pick_best_reference_id(record.feature_matches)
        narrowed_matches = select_matches_for_reference(record.feature_matches, ref_id)
        mapped, warnings = map_vcf_to_matches(
            group, narrowed_matches, record.query_sequence, output_chrom=record.query_name,
        )
        mapped_all.extend(mapped)
        warnings_all.extend(warnings)

    for chrom in dropped_chroms:
        logger.warning(
            'VCF CHROM %r has no matching reference record — dropping %d variant(s)',
            chrom, len(grouped.get(chrom, [])),
        )
    if variants and not any(chrom in records_by_name for chrom in grouped):
        raise ValueError(
            'No VCF CHROM matches an internal project reference. '
            f'VCF CHROMs={sorted(grouped)} '
            f'resolved FASTA records={sorted(records_by_name)}. '
            'Ensure the reference FASTA records align to features in the project database.'
        )

    logger.info(
        'Compared %d variant(s) across %d CHROM(s); %d emitted, %d CHROM(s) dropped',
        len(variants), len(grouped), len(mapped_all), len(dropped_chroms),
    )
    return mapped_all, warnings_all, dropped_chroms


def map_vcf_to_matches(
    variants: list[VariantCall],
    matches: list[FeatureMatch],
    supplied_sequence: str,
    output_chrom: str | None = None,
) -> tuple[list[VariantCall], list[str]]:
    """Map VCF calls and supplied-vs-internal reference mismatches across one CHROM's features."""
    supplied_upper = supplied_sequence.upper()
    warnings: list[str] = []
    pairs: list[tuple[VariantCall, FeatureMatch]] = []
    all_skipped: list[SkippedCall] = []

    for match in matches:
        emitted, skipped = _map_variants_for_feature(match, variants, supplied_upper, warnings)
        all_skipped.extend(skipped)
        pairs.extend((var, match) for var in emitted)

    if output_chrom is not None:
        for var, _match in pairs:
            var.chrom = output_chrom

    mismatch_positions = sorted({s.pos for s in all_skipped if s.kind == 'ref_mismatch' and s.pos})
    if len(mismatch_positions) >= 2:
        warnings.append(
            f'{len(mismatch_positions)} VCF positions do not match the supplied reference '
            '— the VCF was probably called against a different reference; these variants '
            'were skipped'
        )

    deduped, dedupe_warnings = dedupe_across_features(pairs)
    warnings.extend(dedupe_warnings)
    return deduped, warnings


def _map_variants_for_feature(
    match: FeatureMatch,
    variants: list[VariantCall],
    supplied_upper: str,
    warnings: list[str],
) -> tuple[list[VariantCall], list[SkippedCall]]:
    """Express sample variants for one aligned feature in internal-reference coordinates."""
    # 1. Align the supplied reference sequence to this internal feature CDS.
    alignment = align_supplied_to_feature(match, supplied_upper)
    if alignment is None:
        return [], []

    # 2. Validate VCF calls (REF matches supplied seq, inside aligned span, AF <= 1, no overlaps).
    valid_calls, skipped = validate_vcf_calls(alignment, variants, supplied_upper)
    warnings.extend(s.message for s in skipped)

    # 3. Fast path: supplied reference matches internal CDS everywhere — remap VCF calls directly.
    if not alignment.has_ref_mismatches:
        direct_variants = [var for call in valid_calls for var in remap_direct_call(alignment, call, warnings)]
        return merge_identical_variants(direct_variants), skipped

    # 4. References differ: extract all supplied-vs-internal reference mismatches (AF 1.0).
    ref_mismatches = find_reference_mismatches(alignment, warnings)
    if not valid_calls:
        return merge_identical_variants([m.variant for m in ref_mismatches]), skipped

    # 5. Partition VCF calls ONCE into calls outside vs overlapping any reference mismatch.
    direct_calls, overlapping_calls = partition_overlapping_calls(alignment, valid_calls)

    emitted: list[VariantCall] = []

    # 5a. Calls in identical stretches remap directly without touching any reference mismatch.
    for call in direct_calls:
        emitted.extend(remap_direct_call(alignment, call, warnings))

    # 5b. Calls overlapping a reference mismatch resolve surgically in a local window;
    #     untouched reference mismatches stay at AF 1.0 and partially covered ones keep residual AF.
    applied_overlaps: list[AppliedOverlap] = []
    for call in overlapping_calls:
        events, applied = resolve_overlapping_call(alignment, call, warnings)
        emitted.extend(events)
        applied_overlaps.append(applied)

    emitted.extend(emit_surviving_ref_mismatches(ref_mismatches, applied_overlaps))
    return merge_identical_variants(emitted), skipped
