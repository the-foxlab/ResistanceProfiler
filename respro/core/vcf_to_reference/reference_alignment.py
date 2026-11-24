"""Supplied-reference to internal-CDS alignment and reference-mismatch extraction."""

from __future__ import annotations

import logging
from bisect import bisect_left
from dataclasses import dataclass

from Bio.Seq import Seq

from respro.core.coordinates import (
    aligned_cds_length,
    build_query_to_cds_map,
    build_ref_positions,
    full_intron_spans,
    gapped_strings_from_cigar,
    strip_introns_from_region,
)
from respro.core.fasta_to_vcf import variants_from_columns
from respro.db.models import FeatureMatch, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

VCF_DERIVED = 'vcf_derived'
REFERENCE_DERIVED = 'reference_derived'
_UNAMBIGUOUS_BASES = frozenset('ACGT')


@dataclass(slots=True)
class AlignedColumn:
    """One internal-CDS column paired with its aligned supplied-reference base and any insertion."""

    internal: str
    supplied: str
    inserted: str = ''

    def copy(self) -> AlignedColumn:
        return AlignedColumn(self.internal, self.supplied, self.inserted)

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.internal, self.supplied, self.inserted)


@dataclass(frozen=True, slots=True)
class SuppliedPosition:
    """Where one forward-strand supplied-reference base sits inside a feature alignment."""

    kind: str  # 'aligned' (1:1 column) or 'inserted' (inserted before a column)
    column: int  # index into FeatureAlignment.columns (full CDS coordinates)
    insert_offset: int  # offset in owning column's inserted string (-1 for 'aligned')
    query_pos: int  # 0-based forward-strand position in the supplied reference


@dataclass(frozen=True, slots=True)
class ReferenceMismatch:
    """One difference between the supplied reference and the internal CDS (assumed AF 1.0)."""

    variant: VariantCall
    columns: frozenset[int]  # occupied coding-frame column indices
    is_insertion: bool


@dataclass
class FeatureAlignment:
    """Precomputed supplied-vs-internal alignment state for one feature match."""

    match: FeatureMatch
    feature: FeatureRecord
    q2c: dict[int, int]
    columns: list[AlignedColumn]
    position_map: dict[int, SuppliedPosition]
    ref_coding_seq: str
    has_ref_mismatches: bool

    @property
    def coding(self) -> list[AlignedColumn]:
        """Columns after applying the feature's ``codon_start`` reading-frame offset."""
        return self.columns[self.feature.codon_start:]

    @property
    def covered_cds_end(self) -> int:
        """Exclusive internal CDS coordinate marking the end of the aligned span."""
        return self.match.cds_start + aligned_cds_length(self.match.cigar)


def align_supplied_to_feature(
    match: FeatureMatch,
    supplied_sequence: str,
) -> FeatureAlignment | None:
    """Build the supplied-vs-internal alignment for one feature match, or ``None`` if CDS is absent."""
    feature = match.feature
    if not feature.nt_sequence:
        logger.warning('Feature %r has no stored CDS sequence — skipping', feature.name)
        return None

    supplied_len = len(supplied_sequence)
    q2c = build_query_to_cds_map(
        match.cigar, match.query_start, match.query_end,
        match.strand, supplied_len, match.cds_start, match.intron_intervals,
    )

    region = supplied_sequence[match.query_start:match.query_end].upper()
    if match.strand == '-':
        region = str(Seq(region).reverse_complement())
    region = strip_introns_from_region(region, match.intron_intervals)

    aligned_internal, aligned_supplied = gapped_strings_from_cigar(
        feature.nt_sequence.upper(), region, match.cigar, match.cds_start,
    )
    columns = [
        AlignedColumn(internal, supplied, inserted)
        for internal, supplied, inserted in build_ref_positions(aligned_internal, aligned_supplied)
    ]
    position_map = _build_position_map(match, supplied_len, q2c)
    ref_coding_seq = ''.join(col.internal for col in columns[feature.codon_start:])
    has_ref_mismatches = any(
        col.supplied != col.internal or bool(col.inserted)
        for col in columns
    )
    return FeatureAlignment(
        match=match,
        feature=feature,
        q2c=q2c,
        columns=columns,
        position_map=position_map,
        ref_coding_seq=ref_coding_seq,
        has_ref_mismatches=has_ref_mismatches,
    )


def _build_position_map(
    match: FeatureMatch,
    supplied_len: int,
    q2c: dict[int, int],
) -> dict[int, SuppliedPosition]:
    """Map every aligned forward supplied-reference position to its aligned or inserted slot."""
    intron_spans: list[tuple[int, int]] = []
    rc_start = supplied_len - match.query_end
    for full_start, full_end in full_intron_spans(match.intron_intervals):
        if match.strand == '+':
            intron_spans.append((match.query_start + full_start, match.query_start + full_end))
        else:
            intron_spans.append((supplied_len - (rc_start + full_end), supplied_len - (rc_start + full_start)))

    def in_intron(pos: int) -> bool:
        return any(lo <= pos < hi for lo, hi in intron_spans)

    position_map: dict[int, SuppliedPosition] = {
        pos: SuppliedPosition('aligned', cds, -1, pos) for pos, cds in q2c.items()
    }
    mapped = sorted(q2c)

    pos = match.query_start
    while pos < match.query_end:
        if pos in q2c or in_intron(pos):
            pos += 1
            continue
        run_start = pos
        while pos < match.query_end and pos not in q2c and not in_intron(pos):
            pos += 1
        run_end = pos

        # Inserted supplied bases attach to the internal column they precede in coding order.
        if match.strand == '+':
            idx = bisect_left(mapped, run_end)
            if idx == len(mapped):
                continue
            column = q2c[mapped[idx]]
            for run_pos in range(run_start, run_end):
                position_map[run_pos] = SuppliedPosition('inserted', column, run_pos - run_start, run_pos)
        else:
            idx = bisect_left(mapped, run_start) - 1
            if idx < 0:
                continue
            column = q2c[mapped[idx]]
            for run_pos in range(run_start, run_end):
                position_map[run_pos] = SuppliedPosition('inserted', column, run_end - 1 - run_pos, run_pos)
    return position_map


def find_reference_mismatches(
    alignment: FeatureAlignment,
    warnings: list[str],
) -> list[ReferenceMismatch]:
    """Extract supplied-vs-internal reference differences (AF 1.0), excluding ambiguous N stretches."""
    variants, _gaps = variants_from_columns(
        [col.as_tuple() for col in alignment.columns],
        alignment.feature,
        covered_cds_start=alignment.match.cds_start,
        covered_cds_end=alignment.covered_cds_end,
    )
    mismatches: list[ReferenceMismatch] = []
    for var in variants:
        var.source = REFERENCE_DERIVED
        var.query_ref_codon = ''
        var.query_codon_feature_id = 0
        attach_internal_codon(alignment, var)
        mismatches.append(ReferenceMismatch(
            variant=var,
            columns=frozenset(variant_coding_columns(alignment, var)),
            is_insertion=len(var.alt) > len(var.ref),
        ))

    # Ambiguous stretches in the supplied reference cannot support assumed reference-derived calls.
    ambig_runs = _ambiguous_stretches(alignment.coding)
    if ambig_runs:
        run_columns = [set(range(lo, hi + 1)) for lo, hi in ambig_runs]
        mismatches = [
            mismatch for mismatch in mismatches
            if not any(mismatch.columns & cols for cols in run_columns)
        ]
        for lo, hi in ambig_runs:
            warnings.append(
                f'Feature {alignment.feature.name}: supplied reference contains an ambiguous N stretch '
                f'at positions {lo + 1}-{hi + 1} — no variants emitted for this stretch'
            )
    return mismatches


def _ambiguous_stretches(coding: list[AlignedColumn]) -> list[tuple[int, int]]:
    """Return maximal coding-index runs carrying non-ACGT bases in supplied or inserted slots."""
    runs: list[tuple[int, int]] = []
    for idx, col in enumerate(coding):
        sup = col.supplied.upper()
        has_ambig = (sup != '-' and sup not in _UNAMBIGUOUS_BASES) or any(
            ch not in _UNAMBIGUOUS_BASES for ch in col.inserted.upper()
        )
        if not has_ambig:
            continue
        if runs and idx == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], idx)
        else:
            runs.append((idx, idx))
    return runs


def attach_internal_codon(alignment: FeatureAlignment, var: VariantCall) -> None:
    """Attach the internal-reference codon context when all 3 codon bases align without deletion."""
    feature = alignment.feature
    cds_pos = feature.genomic_to_cds_position(var.pos)
    if cds_pos is None:
        return
    coding_idx = cds_pos - feature.codon_start
    # Minus-strand VCF anchors sit coding-downstream of indels, so shift left by REF length.
    if len(var.ref) != len(var.alt) and feature.strand == '-':
        coding_idx -= len(var.ref)
    coding = alignment.coding
    if coding_idx < 0 or coding_idx >= len(coding):
        return
    codon_start = (coding_idx // 3) * 3
    if codon_start + 3 > len(coding):
        return
    if any(coding[codon_start + offset].supplied == '-' for offset in range(3)):
        return
    # Use the internal reference codon because surviving reference mismatches emit as separate variants.
    codon = ''.join(coding[codon_start + offset].internal for offset in range(3))
    if '-' in codon:
        return
    var.query_ref_codon = codon
    var.query_codon_feature_id = feature.id


def variant_coding_columns(alignment: FeatureAlignment, var: VariantCall) -> set[int]:
    """Return the coding-column indices occupied by one internal-coordinate variant."""
    feature = alignment.feature
    cds_pos = feature.genomic_to_cds_position(var.pos)
    if cds_pos is None:
        return set()
    coding_idx = cds_pos - feature.codon_start
    if len(var.ref) == 1 and len(var.alt) == 1:
        return {coding_idx}
    payload_len = abs(len(var.ref) - len(var.alt))
    if len(var.alt) > len(var.ref):
        return {coding_idx + 1} if feature.strand == '+' else {coding_idx}
    if feature.strand == '+':
        return {coding_idx + 1 + offset for offset in range(payload_len)}
    return {coding_idx - 1 - offset for offset in range(payload_len)}
