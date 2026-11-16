"""
VCF reference reconciliation — sample states relative to the internal reference.

Also hosts the shared VCF coordinate machinery (the intron-aware query-to-CDS
coordinate map and the cross-match dedupe helper) used by the BAM coverage
projection (:mod:`respro.core.vcf_coverage`) and the BAM codon co-occurrence
context. The former event-level remap pipeline (``respro.core.vcf_remap``) was
superseded by this module and removed.

VCF variants are called against the supplied reference FASTA, but resistance
rules are defined against the internal project reference. This module emits the
complete reconciled variant set in internal-reference coordinates:

1. **Baseline emission** — differences between the supplied reference and the
   internal reference, derived from the existing feature alignments (the same
   alignment-diff machinery as FASTA mode, with the supplied reference playing
   the role of the query). Baseline variants carry an assumed allele frequency
   of 1.0 and the ``reference_derived`` source label.
2. **Call overlay** — each in-cutoff VCF call defines a per-allele sample state
   (the supplied reference with the call's REF span replaced by its ALT). The
   state is re-emitted against the internal reference through the same
   alignment-diff walk, carrying the call's allele frequency and the
   ``vcf_derived`` source label. This resolves baseline/call interactions at
   sequence level: a call that reverses a baseline indel cancels it, and a call
   indel plus a baseline indel merge into the net state.
3. **Residual states** — the residual frequency mass (1 - sum of call AFs) at
   an affected position belongs to the plain supplied reference, so the
   baseline variant at that position is re-emitted at the residual frequency
   with the ``vcf_derived`` label (its frequency derives from caller AFs).

The reconciliation is tiered for performance: the supplied-internal alignment
is computed once per feature match; calls are located by coordinate lookup and
only their local window is re-walked. When the supplied and internal references
are identical the baseline is empty and the output equals the previous
event-level remap behaviour.

Unverified inputs (overlapping separate records, REF blocks that do not match
the supplied reference, spans crossing alignment gaps, inconsistent allele
frequencies) are skipped with a warning instead of producing a false result.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from Bio.Seq import Seq

from respro.core.alignment import cigar_to_coordinate_map
from respro.core.fasta_to_vcf import (
    _build_ref_positions,
    _gapped_strings_from_cigar,
    _get_query_codon,
    _make_fasta_deletion_from_alignment,
    _make_fasta_insertion_from_alignment,
    _make_variant_from_coding_nt,
    _nnn_and_uncovered_codon_indices,
    _strip_introns_from_region,
    _variants_from_alignment,
)
from respro.core.query import QueryRecord, pick_best_reference_id, select_matches_for_reference
from respro.db.models import FeatureMatch, FeatureRecord, IntronInterval, VariantCall

logger = logging.getLogger(__name__)

# Tolerance for frequency-mass consistency checks (sum of allele frequencies
# must not exceed 1 for one position's mutually exclusive allele set).
_AF_MASS_EPS = 1e-9

# Residual frequency mass below this is treated as fully called (no residual
# state emitted).
_RESIDUAL_EPS = 1e-9

VCF_DERIVED = 'vcf_derived'
REFERENCE_DERIVED = 'reference_derived'


def reconcile_variants(
    variants: list[VariantCall],
    query_records: list[QueryRecord],
) -> tuple[list[VariantCall], list[str], list[str]]:
    """
    Reconcile VCF calls with supplied-internal reference differences, per CHROM.

    Groups variants by CHROM, pairs each group with its matching query record,
    and reconciles independently via :func:`reconcile_variants_for_matches`.
    A CHROM whose ``query_name`` is absent from ``query_records`` is recorded in
    ``dropped_chroms`` (its variants cannot be projected). If no CHROM has a
    usable mapping, :class:`ValueError` is raised.

    :param variants: parsed VCF variants (0-based on the supplied reference),
        already filtered by the allele-frequency/depth cutoffs
    :param query_records: one per aligning FASTA record, each carrying its feature matches
    :return: ``(reconciled, warnings, dropped_chroms)``
    :raises ValueError: if no variant's CHROM has a usable query record mapping
    """
    records_by_name = {rec.query_name: rec for rec in query_records}

    grouped: dict[str, list[VariantCall]] = {}
    for var in variants:
        grouped.setdefault(var.chrom, []).append(var)

    dropped_chroms: list[str] = []
    reconciled_all: list[VariantCall] = []
    warnings_all: list[str] = []

    any_chrom_matched = False
    for chrom, group in grouped.items():
        record = records_by_name.get(chrom)
        if record is None:
            logger.warning(
                'VCF CHROM %r has no usable internal feature mapping; dropping %d variant(s)',
                chrom, len(group),
            )
            dropped_chroms.append(chrom)
            continue
        any_chrom_matched = True
        # Narrow to the single best internal reference (same policy as the
        # previous event-level routing): a query may align to features on
        # multiple internal references; without narrowing, overlapping feature
        # copies would produce cross-reference contamination.
        ref_id = pick_best_reference_id(record.feature_matches)
        narrowed_matches = select_matches_for_reference(record.feature_matches, ref_id)
        reconciled, warnings = reconcile_variants_for_matches(
            group, narrowed_matches, record.query_sequence, output_chrom=chrom,
        )
        reconciled_all.extend(reconciled)
        warnings_all.extend(warnings)

    if variants and not any_chrom_matched:
        raise ValueError(
            'No VCF CHROM could be reconciled to an internal reference. '
            f'VCF CHROMs={sorted(grouped)} '
            f'resolved FASTA records={sorted(records_by_name)}. '
            'Ensure the reference FASTA records align to features in the project database.'
        )

    logger.info(
        'Reconciled %d variant(s) across %d CHROM(s); %d emitted, %d CHROM(s) dropped',
        len(variants), len(grouped), len(reconciled_all), len(dropped_chroms),
    )
    return reconciled_all, warnings_all, dropped_chroms


def reconcile_variants_for_matches(
    variants: list[VariantCall],
    matches: list[FeatureMatch],
    query_sequence: str,
    output_chrom: str | None = None,
) -> tuple[list[VariantCall], list[str]]:
    """
    Reconcile VCF calls against the internal reference for one query record.

    Every overlapping feature match projects the calls and its own baseline
    independently; duplicates with identical ``(chrom, pos, ref, alt)`` keys are
    collapsed afterwards, keeping the best-scoring match's copy.

    :param variants: in-cutoff VCF calls (0-based on the supplied reference)
    :param matches: feature matches from the supplied-internal alignment
    :param query_sequence: supplied reference sequence (forward strand)
    :param output_chrom: when set, emitted variants carry this CHROM (the VCF
        CHROM) instead of the producing feature's name, so cross-match dedupe
        keys on the VCF chrom exactly like the former event-level routing
    :return: (reconciled variants in internal coordinates, warnings)
    """
    query_upper = query_sequence.upper()
    warnings: list[str] = []
    pairs: list[tuple[VariantCall, FeatureMatch]] = []

    for match in matches:
        ctx = _build_match_alignment(match, query_sequence)
        if ctx is None:
            continue
        baseline = _emit_baseline(ctx, warnings)
        plans, rejected = _plan_calls(ctx, variants, query_upper, warnings)
        warnings.extend(rejected)
        emissions: list[VariantCall] = []
        windows: list[tuple[int, int, float]] = []
        for plan in plans:
            events, window = _emit_call_state(ctx, plan, warnings)
            emissions.extend(events)
            windows.append((window[0], window[1], plan.call.allele_freq))
        for var, occupied in baseline:
            residual, source = _baseline_residual(var, occupied, windows)
            if residual <= _RESIDUAL_EPS:
                continue
            emissions.append(replace(
                var, allele_freq=residual, source=source, depth=0,
            ))
        pairs.extend((var, match) for var in _aggregate_emissions(emissions))

    if output_chrom is not None:
        for var, _match in pairs:
            var.chrom = output_chrom

    deduped, dedupe_warnings = _dedupe_remapped_variants(pairs)
    warnings.extend(dedupe_warnings)
    return deduped, warnings


@dataclass(frozen=True)
class _Slot:
    """One supplied-reference base position inside a match's alignment."""

    kind: str  # 'base' (maps to an internal column) or 'pending' (inserted bases)
    column: int  # ref_positions index (full-CDS coordinates)
    char_idx: int  # index within the owning column's pending string (pending only)
    q: int  # forward query position


@dataclass
class _CallPlan:
    """One verified VCF call ready for state surgery."""

    call: VariantCall
    slots: list[_Slot]  # coding-ordered slots covered by the REF span
    block: str  # ALT in coding orientation


@dataclass
class _MatchAlignment:
    """Precomputed alignment context for one feature match."""

    match: FeatureMatch
    feature: FeatureRecord
    q2c: dict[int, int]
    # Per-ungapped-ref-position baseline state in alignment order:
    # [internal ref base, supplied base, pending insertion before this base].
    # Entries are mutable lists so call-state surgery can edit them.
    ref_positions: list[list[str]]
    slot_map: dict[int, _Slot]

    @property
    def coding(self) -> list[list[str]]:
        """Baseline state per coding nucleotide (codon_start frame applied)."""
        return self.ref_positions[self.feature.codon_start:]

    @property
    def covered_cds_end(self) -> int:
        """CDS nt end (exclusive) of the aligned span."""
        return self.match.cds_start + _aligned_cds_len(self.match)


def _build_match_alignment(
    match: FeatureMatch,
    query_sequence: str,
) -> _MatchAlignment | None:
    """Build the alignment context for one match, or None when unusable."""
    feature = match.feature
    if not feature.nt_sequence:
        logger.warning('Feature %r has no stored CDS sequence — skipping', feature.name)
        return None

    query_len = len(query_sequence)
    q2c = _build_query_to_cds_map(
        match.cigar, match.query_start, match.query_end,
        match.strand, query_len, match.cds_start, match.intron_intervals,
    )

    region = query_sequence[match.query_start:match.query_end].upper()
    if match.strand == '-':
        region = str(Seq(region).reverse_complement())
    region = _strip_introns_from_region(region, match.intron_intervals)

    aligned_ref, aligned_query = _gapped_strings_from_cigar(
        feature.nt_sequence.upper(), region, match.cigar, match.cds_start,
    )
    ref_positions = [list(entry) for entry in _build_ref_positions(aligned_ref, aligned_query)]
    slot_map = _build_slot_map(match, query_sequence, q2c, ref_positions)
    return _MatchAlignment(
        match=match, feature=feature, q2c=q2c,
        ref_positions=ref_positions, slot_map=slot_map,
    )


def _build_slot_map(
    match: FeatureMatch,
    query_sequence: str,
    q2c: dict[int, int],
    ref_positions: list[list[str]],
) -> dict[int, _Slot]:
    """
    Map every aligned forward query position to its alignment slot.

    Base slots come from ``q2c`` (query position -> internal CDS position).
    Inserted supplied bases (unmapped, non-intron positions inside the aligned
    region) belong to the pending string of the internal column they precede in
    coding orientation: for '+' matches the column after the run, for '-'
    matches the column before it. Runs at the region edge with no anchor column
    are left unmapped (trailing insertions cannot be anchored — the same
    limitation as the FASTA baseline walk).

    :param match: feature match providing strand, region bounds and introns
    :param query_sequence: supplied reference sequence (forward strand)
    :param q2c: forward query position -> internal CDS position
    :param ref_positions: per-column baseline state (pending strings included)
    :return: forward query position -> :class:`_Slot`
    """
    query_len = len(query_sequence)
    if match.strand == '+':
        intron_spans = [
            (match.query_start + iv.query_start,
             match.query_start + iv.query_start + iv.length)
            for iv in match.intron_intervals
        ]
    else:
        rc_start = query_len - match.query_end
        intron_spans = [
            (query_len - (rc_start + iv.query_start + iv.length),
             query_len - (rc_start + iv.query_start))
            for iv in match.intron_intervals
        ]

    def in_intron(q: int) -> bool:
        return any(lo <= q < hi for lo, hi in intron_spans)

    slot_map: dict[int, _Slot] = {}
    for q, cds in q2c.items():
        slot_map[q] = _Slot('base', cds, -1, q)

    mapped = sorted(q2c)
    q = match.query_start
    while q < match.query_end:
        if q in q2c or in_intron(q):
            q += 1
            continue
        run_start = q
        while q < match.query_end and q not in q2c and not in_intron(q):
            q += 1
        run_end = q  # exclusive
        if match.strand == '+':
            anchor = next((m for m in mapped if m >= run_end), None)
            if anchor is None:
                continue
            column = q2c[anchor]
            for run_q in range(run_start, run_end):
                slot_map[run_q] = _Slot('pending', column, run_q - run_start, run_q)
        else:
            anchor = next((m for m in reversed(mapped) if m < run_start), None)
            if anchor is None:
                continue
            column = q2c[anchor]
            for run_q in range(run_start, run_end):
                slot_map[run_q] = _Slot('pending', column, run_end - 1 - run_q, run_q)
    return slot_map


def _aligned_cds_len(match: FeatureMatch) -> int:
    """Return the number of CDS bases the match's CIGAR consumes."""
    return sum(int(n) for n, op in re.findall(r'(\d+)([MD])', match.cigar))


def _emit_baseline(
    ctx: _MatchAlignment,
    warnings: list[str],
) -> list[tuple[VariantCall, set[int]]]:
    """
    Emit baseline variants: supplied-reference differences vs the internal reference.

    Reuses the FASTA alignment-diff walk with the supplied reference as the
    query. Emitted variants carry AF 1.0 (assumed, not measured) and the
    ``reference_derived`` source label. NNN codon stretches in the supplied
    reference are unresolvable: no emission, one warning per stretch.
    """
    feature = ctx.feature
    variants, _gaps = _variants_from_alignment(
        _gapped_ref_string(ctx),
        _gapped_query_string(ctx),
        feature,
        covered_cds_start=ctx.match.cds_start,
        covered_cds_end=ctx.covered_cds_end,
        ref_positions=ctx.ref_positions,
    )
    baseline: list[tuple[VariantCall, set[int]]] = []
    for var in variants:
        var.source = REFERENCE_DERIVED
        baseline.append((var, _variant_occupied_columns(ctx, var)))

    nnn_indices = _nnn_and_uncovered_codon_indices(
        ctx.coding, feature.codon_start, ctx.match.cds_start, ctx.covered_cds_end,
    )
    for start, end in _merge_stretches(nnn_indices):
        warnings.append(
            f'Feature {feature.name}: supplied reference contains an ambiguous N '
            f'stretch covering codon(s) {start + 1}-{end + 1}; no baseline variants '
            'emitted for this stretch'
        )
    return baseline


def _gapped_ref_string(ctx: _MatchAlignment) -> str:
    """Rebuild the gapped internal-reference string for the diff walk."""
    return ''.join(ref for ref, _query, _pending in ctx.ref_positions)


def _gapped_query_string(ctx: _MatchAlignment) -> str:
    """Rebuild the gapped supplied-reference string for the diff walk."""
    parts: list[str] = []
    for _ref, query, pending in ctx.ref_positions:
        parts.append(pending)
        parts.append(query)
    return ''.join(parts)


def _merge_stretches(codon_indices: list[int]) -> list[tuple[int, int]]:
    """Merge sorted codon indices into inclusive (start, end) stretches."""
    stretches: list[tuple[int, int]] = []
    for idx in sorted(codon_indices):
        if stretches and idx == stretches[-1][1] + 1:
            stretches[-1] = (stretches[-1][0], idx)
        else:
            stretches.append((idx, idx))
    return stretches


def _plan_calls(
    ctx: _MatchAlignment,
    variants: list[VariantCall],
    query_upper: str,
    warnings: list[str],
) -> tuple[list[_CallPlan], list[str]]:
    """
    Group, verify and order the VCF calls for state surgery.

    Calls are grouped into allele sets (one VCF record's ALTs share
    CHROM/POS/REF). A set is verified when its REF block matches the supplied
    reference, every REF position has an attachable slot, and its allele
    frequencies sum to at most 1. Two verified sets whose slot-column ranges
    overlap are jointly unverified (no phase): all their calls are skipped with
    a warning and the baseline is retained for the span.

    :return: (verified call plans in input order, rejection warnings)
    """
    groups: dict[tuple[str, int, str], list[VariantCall]] = {}
    order: list[tuple[str, int, str]] = []
    for var in variants:
        key = (var.chrom, var.pos, var.ref)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(var)

    rejections: list[str] = []
    slots_by_key: dict[tuple[str, int, str], list[_Slot] | None] = {}
    for key in order:
        var = groups[key][0]
        ref_len = len(var.ref)
        supplied = query_upper[var.pos:var.pos + ref_len].upper()
        if supplied != var.ref.upper():
            rejections.append(
                f'pos {var.pos + 1}: VCF REF {var.ref!r} does not match supplied '
                f'reference {supplied!r}; call skipped (baseline retained)'
            )
            slots_by_key[key] = None
            continue
        slots: list[_Slot] = []
        unresolved = False
        for offset in range(ref_len):
            slot = ctx.slot_map.get(var.pos + offset)
            if slot is None or not _slot_char_matches(ctx, slot, query_upper):
                unresolved = True
                break
            slots.append(slot)
        if unresolved:
            rejections.append(
                f'pos {var.pos + 1}: VCF {var.ref}>{var.alt} span is not fully mapped '
                'to the internal reference; call skipped (baseline retained)'
            )
            slots_by_key[key] = None
            continue
        total_af = sum(call.allele_freq for call in groups[key])
        if total_af > 1.0 + _AF_MASS_EPS:
            rejections.append(
                f'pos {var.pos + 1}: allele frequencies sum to {total_af:.3f} (> 1); '
                'calls cannot be jointly phased and are skipped (baseline retained)'
            )
            slots_by_key[key] = None
            continue
        slots_by_key[key] = slots

    verified = [key for key in order if slots_by_key.get(key) is not None]
    conflicting: set[tuple[str, int, str]] = set()
    for i, key_a in enumerate(verified):
        slots_a = slots_by_key[key_a]
        assert slots_a is not None
        cols_a = [s.column for s in slots_a]
        for key_b in verified[i + 1:]:
            slots_b = slots_by_key[key_b]
            assert slots_b is not None
            cols_b = [s.column for s in slots_b]
            if min(cols_a) <= max(cols_b) and min(cols_b) <= max(cols_a):
                conflicting.add(key_a)
                conflicting.add(key_b)
    if conflicting:
        span_text = ', '.join(
            f'{key[0]}:{key[1] + 1}{key[2]}>' + '/'.join(c.alt for c in groups[key])
            for key in verified if key in conflicting
        )
        rejections.append(
            'Overlapping VCF records cannot be jointly phased; calls skipped '
            f'(baseline retained): {span_text}'
        )

    plans: list[_CallPlan] = []
    for key in order:
        key_slots = slots_by_key.get(key)
        if key_slots is None or key in conflicting:
            continue
        for call in groups[key]:
            coding_slots = key_slots if ctx.match.strand == '+' else list(reversed(key_slots))
            block = call.alt if ctx.match.strand == '+' else str(Seq(call.alt).reverse_complement())
            plans.append(_CallPlan(call=call, slots=coding_slots, block=block))
    return plans, rejections


def _slot_char_matches(
    ctx: _MatchAlignment,
    slot: _Slot,
    query_upper: str,
) -> bool:
    """Verify a slot's stored character against the supplied reference base."""
    if slot.kind == 'base':
        return True
    entry = ctx.ref_positions[slot.column]
    pending = entry[2]
    if slot.char_idx < 0 or slot.char_idx >= len(pending):
        return False
    if ctx.match.strand == '+':
        expected = query_upper[slot.q]
    else:
        expected = str(Seq(query_upper[slot.q]).complement())
    return pending[slot.char_idx] == expected


def _emit_call_state(
    ctx: _MatchAlignment,
    plan: _CallPlan,
    warnings: list[str],
) -> tuple[list[VariantCall], tuple[int, int]]:
    """
    Emit one call's sample state against the internal reference.

    The state replaces the supplied REF span's slots with the ALT (in coding
    orientation) inside the match's alignment context, collapses coincident
    insertion+deletion gaps into substitutions, then walks a window around the
    span for state-vs-internal differences. Every emitted variant carries the
    call's allele frequency, the ``vcf_derived`` source label, and the original
    supplied coordinates.

    :return: (emitted variants, inclusive coding-column walk window) — the
        window is used by the residual pass to attribute frequency mass.
    """
    feature = ctx.feature
    call = plan.call
    slots = plan.slots
    block = plan.block
    alt_len = len(call.alt)
    ref_len = len(call.ref)
    off = feature.codon_start
    coding = ctx.coding
    ref_positions = ctx.ref_positions

    col_lo = min(s.column for s in slots)
    col_hi = max(s.column for s in slots)
    snap_lo = max(0, col_lo - 1)
    snap_hi = min(len(ref_positions) - 1, col_hi + 1)
    saved = {j: list(ref_positions[j]) for j in range(snap_lo, snap_hi + 1)}

    # Attachment point for ALT bases beyond the REF span. '+' matches read the
    # ALT block in forward order (extras trail the coding-last slot); '-'
    # matches read it reversed (extras lead the coding-first slot).
    plus_strand = ctx.match.strand == '+'
    first = slots[0]
    last = slots[-1]
    if plus_strand:
        if last.kind == 'base':
            extra_column = last.column + 1
            extra_pos = 0
        else:
            extra_column = last.column
            extra_pos = last.char_idx + 1
    else:
        if first.kind == 'base':
            extra_column = first.column
            extra_pos = 0
        else:
            extra_column = first.column
            extra_pos = first.char_idx

    pending_tail = -1
    trailing_extra = ''
    try:
        shift = alt_len - ref_len
        for i, slot in enumerate(slots):
            entry = ref_positions[slot.column]
            if plus_strand:
                block_idx = i
            else:
                block_idx = i + shift
            if 0 <= block_idx < alt_len:
                new_char = block[block_idx]
                if slot.kind == 'base':
                    ref_positions[slot.column] = [entry[0], new_char, entry[2]]
                else:
                    p = entry[2]
                    ref_positions[slot.column] = [
                        entry[0], entry[1], p[:slot.char_idx] + new_char + p[slot.char_idx + 1:],
                    ]
            else:
                if slot.kind == 'base':
                    ref_positions[slot.column] = [entry[0], '-', entry[2]]
                else:
                    p = entry[2]
                    ref_positions[slot.column] = [
                        entry[0], entry[1], p[:slot.char_idx] + p[slot.char_idx + 1:],
                    ]
        if shift > 0:
            trailing_extra = block[:shift] if not plus_strand else block[ref_len:]
            if extra_column < len(ref_positions):
                entry = ref_positions[extra_column]
                p = entry[2]
                ref_positions[extra_column] = [
                    entry[0], entry[1], p[:extra_pos] + trailing_extra + p[extra_pos:],
                ]
                if plus_strand and extra_column - off > col_hi - off:
                    pending_tail = extra_column - off
            # else: trailing insertion past the last column, handled below

        _collapse_coincident_gaps(ref_positions, snap_lo, snap_hi)

        lo = max(0, col_lo - off - 1)
        hi = min(len(coding) - 1, col_hi - off + 1)
        while lo > 0 and coding[lo][1] == '-' and coding[lo - 1][1] == '-':
            lo -= 1
        while hi + 1 < len(coding) and coding[hi][1] == '-' and coding[hi + 1][1] == '-':
            hi += 1

        emitted = _walk_diff(ctx, lo, hi, pending_tail)
        if alt_len > ref_len and extra_column >= len(ref_positions):
            # Insertion past the last internal column: only '+'-strand features
            # have a representable anchor (the last internal base); '-'-strand
            # features would need the following genomic base, which is outside
            # the feature.
            if feature.strand == '+':
                emitted.extend(_emit_trailing_insertion(ctx, col_hi, trailing_extra, call))
            else:
                warnings.append(
                    f'pos {call.pos + 1}: VCF insertion {call.ref}>{call.alt} extends '
                    f'past the 3\u2032 end of feature {feature.name!r}; event skipped'
                )
    finally:
        for j, entry in saved.items():
            ref_positions[j] = entry

    for var in emitted:
        var.allele_freq = call.allele_freq
        var.depth = call.depth
        var.source = VCF_DERIVED
        var.user_chrom = call.chrom
        var.user_pos = call.pos
        var.user_ref = call.ref
        var.user_alt = call.alt
        _attach_supplied_codon(ctx, var)
    return emitted, (lo, hi)


def _collapse_coincident_gaps(
    ref_positions: list[list[str]],
    lo: int,
    hi: int,
) -> None:
    """
    Collapse coincident insertion+deletion gaps into substitutions.

    Call surgery can produce a column whose supplied base is deleted while its
    pending string gained (or already had) characters — the gapped state then
    shows an insertion and a deletion where the sequence has a substitution.
    Moving one pending character into the deleted slot preserves the sequence
    and yields the minimal event representation.
    """
    for j in range(lo, hi + 1):
        entry = ref_positions[j]
        while entry[1] == '-' and entry[2]:
            ref_positions[j] = [entry[0], entry[2][0], entry[2][1:]]
            entry = ref_positions[j]
        if (
            entry[1] == '-'
            and j + 1 <= hi
            and ref_positions[j + 1][2]
            and ref_positions[j + 1][1] != '-'
        ):
            nxt = ref_positions[j + 1]
            ref_positions[j] = [entry[0], nxt[2][0], entry[2]]
            ref_positions[j + 1] = [nxt[0], nxt[1], nxt[2][1:]]


def _walk_diff(
    ctx: _MatchAlignment,
    lo: int,
    hi: int,
    pending_tail: int = -1,
) -> list[VariantCall]:
    """
    Walk the state's coding window for differences vs the internal reference.

    Emits SNPs, insertion payloads attached at their columns (including a
    pending tail just past the window), and complete deletion runs. The window
    bounds are extended by the caller so deletion runs are never truncated.
    """
    feature = ctx.feature
    coding = ctx.coding
    emitted: list[VariantCall] = []

    idx = lo
    while idx <= hi:
        ref_base, sup, pending = coding[idx]
        if pending:
            var = _make_fasta_insertion_from_alignment(feature, coding, idx, pending)
            if var is not None:
                emitted.append(var)
        if sup == '-':
            run_end = idx
            while run_end + 1 <= hi and coding[run_end + 1][1] == '-':
                run_end += 1
            var = _make_fasta_deletion_from_alignment(feature, coding, idx, run_end)
            if var is not None:
                emitted.append(var)
            idx = run_end + 1
            continue
        if sup != ref_base:
            var = _make_variant_from_coding_nt(feature, idx, ref_base, sup)
            emitted.append(var)
        idx += 1

    # A pending tail strictly beyond the walked window is emitted here; tails
    # inside the window were already emitted by the main loop above (the window
    # is extended to include the '+'-match insertion attachment column).
    if pending_tail > hi and pending_tail < len(coding):
        tail = coding[pending_tail][2]
        if tail:
            var = _make_fasta_insertion_from_alignment(
                feature, coding, pending_tail, tail,
            )
            if var is not None:
                emitted.append(var)
    return emitted


def _emit_trailing_insertion(
    ctx: _MatchAlignment,
    anchor_idx: int,
    extra: str,
    call: VariantCall,
) -> list[VariantCall]:
    """
    Emit an insertion whose payload falls after the last internal column.

    The alignment walk cannot represent a pending insertion beyond the final
    reference base, so the event is emitted directly with the same anchor
    convention as :func:`_make_fasta_insertion_from_alignment` ('+'-strand
    features only).
    """
    feature = ctx.feature
    ref_base = ctx.coding[anchor_idx][0]
    genomic_pos = feature.cds_to_genomic_position(feature.codon_start + anchor_idx)
    assert genomic_pos is not None  # anchor is inside the CDS by construction
    var = VariantCall(
        chrom=feature.name,
        pos=genomic_pos,
        ref=ref_base,
        alt=ref_base + extra,
        allele_freq=1.0,
        depth=0,
        filter_status='PASS',
    )
    var.allele_freq = call.allele_freq
    var.source = VCF_DERIVED
    var.user_chrom = call.chrom
    var.user_pos = call.pos
    var.user_ref = call.ref
    var.user_alt = call.alt
    _attach_supplied_codon(ctx, var)
    return [var]


def _attach_supplied_codon(ctx: _MatchAlignment, var: VariantCall) -> None:
    """Bind the supplied-reference codon context to an emitted call variant.

    For minus-strand features the VCF anchor sits coding-downstream of the
    indel payload, so the amino-acid context shifts left by the REF length —
    the same convention the former event-level remap applied
    (``_indel_anchor_cds_pos``).
    """
    feature = ctx.feature
    cds_pos = feature.genomic_to_cds_position(var.pos)
    if cds_pos is None:
        return
    coding_idx = cds_pos - feature.codon_start
    if len(var.ref) != len(var.alt) and feature.strand == '-':
        coding_idx -= len(var.ref)
    if coding_idx < 0 or coding_idx >= len(ctx.coding):
        return
    codon = _get_query_codon(ctx.coding, coding_idx)
    if '-' in codon:
        return
    var.query_ref_codon = codon
    var.query_codon_feature_id = feature.id


def _baseline_residual(
    var: VariantCall,
    occupied: set[int],
    windows: list[tuple[int, int, float]],
) -> tuple[float, str]:
    """
    Compute a baseline variant's residual frequency mass and source label.

    The baseline variant exists in the residual (plain supplied-reference)
    haplotype and in every call haplotype whose walk window overlaps the
    variant's occupied columns. Its residual mass is therefore its baseline
    frequency scaled by the mass not claimed by overlapping calls; the source
    is ``vcf_derived`` when any call overlaps (the frequency decomposition
    derives from caller AFs), else ``reference_derived``.

    :param var: baseline variant (its allele_freq is the baseline frequency)
    :param occupied: coding columns the variant occupies
    :param windows: (lo, hi, allele_freq) per processed call state window
    :return: (residual frequency, source label)
    """
    overlapping = sum(
        af for lo, hi, af in windows
        if occupied and lo <= max(occupied) and hi >= min(occupied)
    )
    residual = var.allele_freq * (1.0 - overlapping)
    source = VCF_DERIVED if overlapping > _RESIDUAL_EPS else REFERENCE_DERIVED
    return residual, source


def _variant_occupied_columns(ctx: _MatchAlignment, var: VariantCall) -> set[int]:
    """
    Return the coding columns a baseline variant occupies.

    SNPs occupy their column; deletions occupy their deleted run; insertions
    occupy the column carrying their pending payload ('+'-strand features anchor
    one column before it, '-'-strand features anchor on it).
    """
    feature = ctx.feature
    cds_pos = feature.genomic_to_cds_position(var.pos)
    if cds_pos is None:
        return set()
    coding_idx = cds_pos - feature.codon_start
    if len(var.ref) == 1 and len(var.alt) == 1:
        return {coding_idx}
    payload_len = abs(len(var.ref) - len(var.alt))
    if len(var.alt) > len(var.ref):
        if feature.strand == '+':
            return {coding_idx + 1}
        return {coding_idx}
    if feature.strand == '+':
        return {coding_idx + 1 + offset for offset in range(payload_len)}
    return {coding_idx - 1 - offset for offset in range(payload_len)}


def _aggregate_emissions(emissions: list[VariantCall]) -> list[VariantCall]:
    """
    Sum frequency mass per (chrom, pos, ref, alt) key within one match.

    The same internal allele can be emitted by several mutually exclusive
    haplotype states (a call state and the residual state, or several call
    states); their frequencies add up to the allele's total frequency. The
    aggregated variant keeps the first contributor's identity and adopts
    ``vcf_derived`` provenance, user coordinates, depth and codon context from
    the first call-derived contributor.
    """
    by_key: dict[tuple[str, int, str, str], VariantCall] = {}
    order: list[tuple[str, int, str, str]] = []
    for var in emissions:
        key = (var.chrom, var.pos, var.ref, var.alt)
        first = by_key.get(key)
        if first is None:
            by_key[key] = var
            order.append(key)
            continue
        first.allele_freq += var.allele_freq
        if var.source == VCF_DERIVED:
            first.source = VCF_DERIVED
            if not first.user_ref:
                first.user_chrom = var.user_chrom
                first.user_pos = var.user_pos
                first.user_ref = var.user_ref
                first.user_alt = var.user_alt
            if var.depth > first.depth:
                first.depth = var.depth
        if not first.query_ref_codon and var.query_ref_codon:
            first.query_ref_codon = var.query_ref_codon
            first.query_codon_feature_id = var.query_codon_feature_id
    return [by_key[key] for key in order]


def _match_rank(match: FeatureMatch) -> tuple[float, float, float, str]:
    """Best-match ordering key: identity desc, cds_coverage desc, query_coverage desc,
    feature name asc — identical to the ``pick_best_reference_id`` ordering."""
    return (-match.identity, -match.cds_coverage, -match.query_coverage, match.feature.name)


def _dedupe_remapped_variants(
    remapped: list[tuple[VariantCall, FeatureMatch]],
) -> tuple[list[VariantCall], list[str]]:
    """
    Collapse remapped variants with identical (chrom, pos, ref, alt) keys.

    A query position may lie inside the aligned spans of several features on the
    same reference; each match remaps the variant independently. When the projected
    internal position and alleles coincide, only the best-scoring match's copy is
    kept — the duplicates would otherwise be annotated repeatedly and, before
    codon-source binding, with conflicting query codons.

    :param remapped: (variant, producing match) pairs in emission order
    :return: (deduplicated variants in emission order, warnings listing collapses)
    """
    best_by_key: dict[tuple[str, int, str, str], tuple[VariantCall, FeatureMatch]] = {}
    order: list[tuple[str, int, str, str]] = []
    for var, match in remapped:
        key = (var.chrom, var.pos, var.ref, var.alt)
        if key not in best_by_key:
            best_by_key[key] = (var, match)
            order.append(key)
        elif _match_rank(match) < _match_rank(best_by_key[key][1]):
            best_by_key[key] = (var, match)

    warnings: list[str] = []
    collapsed = len(remapped) - len(order)
    if collapsed:
        details = ', '.join(
            f'{key[0]}:{key[1] + 1}{key[2]}>{key[3]}' for key in order
            if sum(1 for v, _ in remapped
                   if (v.chrom, v.pos, v.ref, v.alt) == key) > 1
        )
        warnings.append(
            f'Collapsed {collapsed} duplicate remapped variant(s) projected from '
            f'overlapping feature matches: {details}'
        )
    return [best_by_key[key][0] for key in order], warnings


def _build_query_to_cds_map(
    cigar: str,
    query_start: int,
    query_end: int,
    strand: str,
    query_len: int,
    cds_start: int = 0,
    intron_intervals: tuple[IntronInterval, ...] = (),
) -> dict[int, int]:
    """
    Invert a CIGAR-based coordinate map to query-position → CDS-position.

    For '-' strand matches the CIGAR was built against the reverse-complement
    query, so positions are first converted back to forward-strand coordinates.

    For spliced features aligned against an unspliced query, ``cigar`` is the
    exon-only CIGAR (intron ``I`` ops removed) and ``intron_intervals`` carries
    the intron query spans. Because the exon-only CIGAR consumes query
    positions sequentially with no intron gap, exon-2 (and later) CDS positions
    would otherwise map into the intron query span. Each mapped query position
    is therefore shifted past the cumulative length of all introns whose
    coding-orientation start precedes it, and intron query positions themselves
    are excluded from the inverted map.

    :param cigar: exon-only CIGAR string from alignment
    :param query_start: 0-based forward-strand start (from FeatureMatch)
    :param query_end: 0-based forward-strand end (from FeatureMatch)
    :param strand: alignment strand ('+' or '-')
    :param query_len: total query sequence length
    :param cds_start: 0-based CDS offset where this alignment starts
    :param intron_intervals: intron intervals (query_start relative to the
        coding-orientation region start) carried on the FeatureMatch
    :return: mapping {forward_query_pos: cds_pos}
    """
    # Intron spans in the orientation cigar_to_coordinate_map produces.
    # iv.query_start is relative to the CIGAR's coding-orientation region start.
    # For '-' strand the CIGAR is in RC-orientation, so the shift must happen
    # in RC space BEFORE converting to forward-strand coordinates — otherwise
    # the RC-orientation intron spans would be matched against forward-strand
    # query positions, silently dropping exon-1 variants and mis-remapping
    # exon-2 variants.
    if strand == '+':
        orient_start = query_start
        intron_spans = [
            (orient_start + iv.query_start, orient_start + iv.query_start + iv.length)
            for iv in intron_intervals
        ]
        cds_to_query = cigar_to_coordinate_map(cigar, query_start)
        if intron_spans:
            cds_to_query = _shift_past_introns(cds_to_query, intron_spans)
    else:
        rc_start = query_len - query_end
        orient_start = rc_start
        intron_spans = [
            (orient_start + iv.query_start, orient_start + iv.query_start + iv.length)
            for iv in intron_intervals
        ]
        cds_to_query_rc = cigar_to_coordinate_map(cigar, rc_start)
        if intron_spans:
            cds_to_query_rc = _shift_past_introns(cds_to_query_rc, intron_spans)
        # Convert RC-orientation positions to forward-strand AFTER the shift.
        cds_to_query = {}
        for cds_pos, rc_pos in cds_to_query_rc.items():
            if rc_pos is not None:
                cds_to_query[cds_pos] = query_len - 1 - rc_pos
            else:
                cds_to_query[cds_pos] = None

    # cds_start is the 0-based offset of this alignment's first CDS
    # position within the full coding sequence; shift all mapped positions
    # so coordinates are relative to the entire CDS rather than the local
    # alignment fragment.
    if cds_start:
        cds_to_query = {cds_pos + cds_start: qpos for cds_pos, qpos in cds_to_query.items()}

    # Invert: query_pos → cds_pos; skip deletions (None values)
    query_to_cds: dict[int, int] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is not None:
            query_to_cds[qpos] = cds_pos

    return query_to_cds


def _shift_past_introns(
    cds_to_query: dict[int, int | None],
    intron_spans: list[tuple[int, int]],
) -> dict[int, int | None]:
    """
    Shift each mapped query position past the cumulative length of preceding introns.

    The exon-only CIGAR maps CDS positions to query offsets that ignore intron
    query spans. For a CDS position whose raw mapped offset falls at or after
    an intron's coding-orientation start, add the cumulative intron length so
    the position lands in the correct exon in the full (unspliced) query.

    Mapped positions that fall *inside* an intron span (should not happen for
    true exon positions, but defensively) are set to None.

    Operates in a single orientation space (the CIGAR's coding orientation:
    forward for '+' strand, RC for '-' strand). The caller converts to
    forward-strand coordinates afterward for '-' strand.

    :param cds_to_query: CDS-position → raw orientation-absolute query position
    :param intron_spans: intron spans in the same orientation as the raw positions
    :return: CDS-position → corrected query position (same orientation as input)
    """
    if not intron_spans:
        return cds_to_query
    spans = sorted(intron_spans)
    corrected: dict[int, int | None] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is None:
            corrected[cds_pos] = None
            continue
        # Cumulative intron length for introns whose start is at or before qpos.
        shift = sum(end - start for start, end in spans if start <= qpos)
        new_pos = qpos + shift
        # Exclude positions that land inside an intron span after shifting.
        if any(start <= new_pos < end for start, end in spans):
            corrected[cds_pos] = None
            continue
        corrected[cds_pos] = new_pos
    return corrected
