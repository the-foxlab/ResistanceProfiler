"""
VCF reference comparison — sample states relative to the internal reference.

Also hosts the shared VCF coordinate machinery (the intron-aware query-to-CDS
coordinate map and the cross-match dedupe helper) used by the BAM coverage
projection (:mod:`respro.core.vcf_coverage`) and the BAM codon co-occurrence
context. The former event-level remap pipeline (``respro.core.vcf_remap``) was
superseded by this module and removed.

VCF variants are called against the supplied reference FASTA, but resistance
rules are defined against the internal project reference. This module emits the
complete variant set in internal-reference coordinates:

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
   insertion plus a baseline indel resolve into the net state (while a VCF
   deletion overlapping or touching a baseline deletion is skipped as
   unverified with a warning, retaining the baseline deletion).
3. **Residual states** — the residual frequency mass (1 - sum of call AFs) at
   an affected position belongs to the plain supplied reference, so the
   baseline variant at that position is re-emitted at the residual frequency
   with the ``reference_derived`` label (the allele itself comes from the
   reference comparison, not from a VCF call).

The comparison is tiered for performance: the supplied-internal alignment
is computed once per feature match; calls are located by coordinate lookup and
only their local window is re-walked. When the supplied and internal references
are identical the baseline is empty and the output equals the previous
event-level remap behaviour.

Unverified inputs (overlapping separate records, REF blocks that do not match
the supplied reference, spans crossing alignment gaps, inconsistent allele
frequencies) are skipped with a warning instead of producing a false result.

Flow (per feature match, see ``reconcile_variants_for_matches``):

1. ``_build_match_alignment`` — parse the CIGAR into the baseline column state
   (``ref_positions``) and the forward-position slot map.
2. ``_emit_baseline`` — diff the supplied reference against the internal
   reference with the FASTA alignment walk; each baseline variant records the
   coding columns it occupies.
3. ``_plan_calls`` — group calls into allele sets (one VCF record's ALTs),
   verify them (REF block, slot coverage, frequency mass), reject overlapping
   spans.
4. ``_emit_call_state`` — per call, apply the ALT to a scratch copy of the
   baseline state, walk the affected window for call-attributable differences,
   restore the baseline state.
5. Residual pass — re-emit baseline variants at the frequency mass not claimed
   by the calls covering them.
6. ``_aggregate_emissions`` + ``_dedupe_remapped_variants`` — sum per-allele
   frequencies within a match, then collapse cross-match duplicates.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from Bio.Seq import Seq

from respro.core.alignment import cigar_to_coordinate_map, full_intron_spans
from respro.core.fasta_to_vcf import (
    _build_ref_positions,
    _gapped_strings_from_cigar,
    _make_fasta_deletion_from_alignment,
    _make_fasta_insertion_from_alignment,
    _make_variant_from_coding_nt,
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

    # A VCF CHROM with no matching query record cannot be reconciled.
    dropped_chroms = sorted(chrom for chrom in grouped if chrom not in records_by_name)
    reconciled_all: list[VariantCall] = []
    warnings_all: list[str] = []

    # Iterate the selected query records, not the retained calls: a contig
    # whose calls all failed the AF/depth cutoffs (or that had no calls at all)
    # still contributes its reference-derived baseline. Record selection is the
    # caller's responsibility — the VCF CLI resolves exactly the CHROMs
    # observed in the VCF, so no unrelated reference is profiled here.
    for record in query_records:
        if not record.feature_matches:
            # Unusable record (aligned to nothing): contributes no baseline and
            # cannot reconcile calls; variant chroms pointing at it are dropped
            # via the dropped_chroms report below.
            continue
        group = grouped.get(record.query_name, [])
        # Narrow to the single best internal reference (same policy as the
        # previous event-level routing): a query may align to features on
        # multiple internal references; without narrowing, overlapping feature
        # copies would produce cross-reference contamination.
        ref_id = pick_best_reference_id(record.feature_matches)
        narrowed_matches = select_matches_for_reference(record.feature_matches, ref_id)
        reconciled, warnings = reconcile_variants_for_matches(
            group, narrowed_matches, record.query_sequence, output_chrom=record.query_name,
        )
        reconciled_all.extend(reconciled)
        warnings_all.extend(warnings)

    any_chrom_matched = any(chrom in records_by_name for chrom in grouped)
    for chrom in dropped_chroms:
        logger.warning(
            'VCF CHROM %r has no matching reference record — dropping %d variant(s)',
            chrom, len(grouped.get(chrom, [])),
        )
    if variants and not any_chrom_matched:
        raise ValueError(
            'No VCF CHROM matches an internal project reference. '
            f'VCF CHROMs={sorted(grouped)} '
            f'resolved FASTA records={sorted(records_by_name)}. '
            'Ensure the reference FASTA records align to features in the project database.'
        )

    logger.info(
        'Compared %d variant(s) across %d CHROM(s); %d emitted, %d CHROM(s) dropped',
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
    all_rejections: list[_Rejection] = []

    for match in matches:
        ctx = _build_match_alignment(match, query_sequence)
        if ctx is None:
            continue
        baseline = _emit_baseline(ctx, warnings)
        plans, rejected = _plan_calls(ctx, variants, query_upper, warnings)
        warnings.extend(r.message for r in rejected)
        all_rejections.extend(rejected)
        off = ctx.feature.codon_start
        emissions: list[VariantCall] = []
        covered_base: list[tuple[set[int], float]] = []
        covered_pending: list[tuple[set[int], float]] = []
        plan_records: list[
            tuple[int, int, float, set[int], set[int], set[int], set[int]]
        ] = []
        for plan in plans:
            events, cov_base, cov_pending = _emit_call_state(ctx, plan, warnings)
            emissions.extend(events)
            covered_base.append((cov_base, plan.call.allele_freq))
            covered_pending.append((cov_pending, plan.call.allele_freq))
            spanned_base = {
                s.column - off
                for s in plan.slots
                if s.kind == 'base' and 0 <= s.column - off < len(ctx.coding)
            }
            spanned_pending = {
                s.column - off
                for s in plan.slots
                if s.kind == 'pending' and 0 <= s.column - off < len(ctx.coding)
            }
            plan_records.append((
                plan.record_id,
                plan.alt_id,
                plan.call.allele_freq,
                cov_base,
                cov_pending,
                spanned_base,
                spanned_pending,
            ))
        for var, occupied in baseline:
            # A baseline variant lives in one sub-space of its column: SNPs and
            # deletions in the base sub-space, insertions in the pending
            # sub-space. A call's haplotype drops the baseline allele only when
            # the call changed that sub-space (an insertion attached next to a
            # baseline SNP keeps the SNP, and vice versa).
            kind_pending = len(var.alt) > len(var.ref)
            sets = covered_pending if kind_pending else covered_base
            residual, source = _baseline_residual(var, occupied, sets)
            if residual <= _RESIDUAL_EPS:
                continue
            covering_records = {
                rec_id
                for rec_id, _alt_id, _af, cov_b, cov_p, _sp_b, _sp_p in plan_records
                if occupied and ((cov_p if kind_pending else cov_b) & occupied)
            }
            if len(covering_records) == 1:
                rec_id = next(iter(covering_records))
                links: list[tuple[int, int, float]] = []
                for r_id, alt_id, af, cov_b, cov_p, sp_b, sp_p in plan_records:
                    if r_id != rec_id:
                        continue
                    cov = cov_p if kind_pending else cov_b
                    spanned = sp_p if kind_pending else sp_b
                    if (spanned & occupied) and not (cov & occupied):
                        links.append((rec_id, alt_id, af))
                uncalled_mass = residual - sum(af for _, _, af in links)
                if uncalled_mass > _RESIDUAL_EPS:
                    links.append((rec_id, -1, uncalled_mass))
                residual_links: tuple[tuple[int, int, float], ...] = tuple(links)
                residual_record_idx = rec_id
                residual_alt_idx = links[0][1] if len(links) == 1 else -1
            else:
                residual_links = ()
                residual_record_idx = -1
                residual_alt_idx = -1
            emissions.append(replace(
                var,
                allele_freq=residual,
                source=source,
                depth=0,
                record_index=residual_record_idx,
                alt_index=residual_alt_idx,
                haplotype_links=residual_links,
            ))
        pairs.extend((var, match) for var in _aggregate_emissions(emissions))

    if output_chrom is not None:
        for var, _match in pairs:
            var.chrom = output_chrom

    mismatch_positions = sorted({r.pos for r in all_rejections if r.kind == 'ref_mismatch' and r.pos})
    if len(mismatch_positions) >= 2:
        warnings.append(
            f'{len(mismatch_positions)} VCF positions do not match the supplied reference '
            '— the VCF was probably called against a different reference; these variants '
            'were skipped'
        )

    deduped, dedupe_warnings = _dedupe_remapped_variants(pairs)
    warnings.extend(dedupe_warnings)
    return deduped, warnings


@dataclass(frozen=True)
class _Slot:
    """One supplied-reference base position inside a match's alignment.

    The alignment is stored per internal-reference column as
    ``ref_positions[column] = [internal_base, supplied_base, pending]``, where
    ``pending`` holds supplied bases that sit between the previous internal
    column and this one (i.e. insertions relative to the internal reference).
    Every aligned supplied base is therefore one of two slot kinds:

    - ``'base'`` — the base aligns 1:1 to an internal column; its character is
      that column's ``supplied_base``.
    - ``'pending'`` — the base is one character of the owning column's
      ``pending`` string; ``char_idx`` indexes it (``column`` is the column
      whose pending string carries it, in coding orientation).

    ``q`` is the base's forward-strand position in the supplied sequence, used
    to verify the slot's stored character against the VCF REF block.
    """

    kind: str  # 'base' (maps to an internal column) or 'pending' (inserted bases)
    column: int  # ref_positions index (full-CDS coordinates)
    char_idx: int  # index within the owning column's pending string (pending only)
    q: int  # forward query position


@dataclass
class _CallPlan:
    """One verified VCF call ready for state surgery.

    ``slots`` lists the alignment slots covered by the call's REF span in
    **coding orientation** (reversed for '-' matches, where the supplied block
    reads right-to-left against the CDS). ``block`` is the ALT in coding
    orientation: verbatim for '+' matches, reverse-complemented for '-'
    matches. Slot ``i`` of the span receives ``block[i]`` on '+' matches and
    ``block[i + (len(alt) - len(ref))]`` on '-' matches (the reversed block is
    anchored at the coding-first slot, so extra ALT bases lead the span).
    """

    call: VariantCall
    slots: list[_Slot]  # coding-ordered slots covered by the REF span
    block: str  # ALT in coding orientation
    record_id: int = -1
    alt_id: int = -1


@dataclass(frozen=True)
class _Rejection:
    """One skipped VCF call with its user-facing reason."""

    kind: str  # 'ref_mismatch' | 'unmapped' | 'af_mass' | 'phase'
    pos: int | None  # 1-based VCF position, when position-specific
    message: str


@dataclass
class _MatchAlignment:
    """Precomputed alignment context for one feature match.

    ``ref_positions`` is the baseline (plain supplied-reference) state, one
    entry per ungapped internal-reference column of the aligned region::

        [internal_base, supplied_base, pending]

    For example, internal ``ACGT`` vs supplied ``ACGGT`` (CIGAR ``3M1I1M``)
    yields ``[['A','A',''], ['C','C',''], ['G','G',''], ['T','T','G']]`` — the
    inserted supplied ``G`` is carried as the pending string of column 3, the
    column it precedes in coding orientation. Call-state surgery edits these
    entries in place (hence mutable lists) and restores them afterwards, so
    every call is applied to the pristine baseline context.

    ``slot_map`` maps each aligned forward query position to its
    :class:`_Slot`; ``q2c`` is the underlying forward query position to
    internal CDS position map (introns excluded).
    """

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
    intron_spans: list[tuple[int, int]] = []
    rc_start = query_len - match.query_end
    for full_start, full_end in full_intron_spans(match.intron_intervals):
        if match.strand == '+':
            intron_spans.append((
                match.query_start + full_start,
                match.query_start + full_end,
            ))
        else:
            intron_spans.append((
                query_len - (rc_start + full_end),
                query_len - (rc_start + full_start),
            ))

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
        var.query_ref_codon = ''
        var.query_codon_feature_id = 0
        _attach_supplied_codon(ctx, var)
        baseline.append((var, _variant_occupied_columns(ctx, var)))

    # N stretches in the supplied reference are unresolvable: suppress every
    # baseline variant touching an N run and warn once per run. (FASTA-mode
    # consensus profiling expands IUPAC codes instead — meaningful for a
    # consensus, but a reference-derived allele frequency would be an invented
    # assumption, so reconciliation degrades instead.)
    n_runs = _n_run_stretches(ctx.coding)
    if n_runs:
        run_columns = [set(range(lo, hi + 1)) for lo, hi in n_runs]
        baseline = [
            (var, occ) for var, occ in baseline
            if not any(occ & cols for cols in run_columns)
        ]
        for lo, hi in n_runs:
            warnings.append(
                f'Feature {feature.name}: supplied reference contains an ambiguous N stretch '
                f'at positions {lo + 1}-{hi + 1} — no variants emitted for this stretch'
            )
    return baseline


_UNAMBIGUOUS_BASES = frozenset('ACGT')


def _n_run_stretches(
    coding: list[list[str]],
) -> list[tuple[int, int]]:
    """Return maximal runs of coding columns whose supplied base or pending insertion has N/ambiguity bases."""
    runs: list[tuple[int, int]] = []
    for idx, entry in enumerate(coding):
        sup = entry[1].upper()
        has_ambig = (sup != '-' and sup not in _UNAMBIGUOUS_BASES) or any(
            ch not in _UNAMBIGUOUS_BASES for ch in entry[2].upper()
        )
        if not has_ambig:
            continue
        if runs and idx == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], idx)
        else:
            runs.append((idx, idx))
    return runs


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


def _plan_calls(
    ctx: _MatchAlignment,
    variants: list[VariantCall],
    query_upper: str,
    warnings: list[str],
) -> tuple[list[_CallPlan], list[_Rejection]]:
    """
    Group, verify and order the VCF calls for state surgery.

    Calls are grouped into allele sets by ``(chrom, pos, ref, record_index)``:
    the ALTs of one VCF record are mutually exclusive states, while two
    separate records at the same position are distinct phase-unknown events
    (the parser stamps ``record_index`` so they stay distinguishable). A set is
    verified when its REF block matches the supplied reference, every REF
    position has an attachable slot, and its allele frequencies sum to at most
    1. Two verified sets whose supplied-reference spans overlap are jointly
    unverified (no phase): all their calls are skipped with a warning and the
    baseline is retained for the span.

    :return: (verified call plans in input order, rejections)
    """
    groups: dict[tuple[str, int, str, int], list[VariantCall]] = {}
    order: list[tuple[str, int, str, int]] = []
    for var in variants:
        key = (var.chrom, var.pos, var.ref, var.record_index)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(var)

    rejections: list[_Rejection] = []
    slots_by_key: dict[tuple[str, int, str, int], list[_Slot] | None] = {}
    for key in order:
        var = groups[key][0]
        ref_len = len(var.ref)
        # Gate 1: the REF block must match the supplied reference — a VCF whose
        # REF disagrees with its own reference cannot be applied.
        supplied = query_upper[var.pos:var.pos + ref_len].upper()
        if supplied != var.ref.upper():
            rejections.append(_Rejection(
                kind='ref_mismatch', pos=var.pos + 1,
                message=f'pos {var.pos + 1}: VCF REF {var.ref!r} does not match the supplied '
                        f'reference {supplied!r} — variant skipped',
            ))
            slots_by_key[key] = None
            continue
        # Gate 2: every REF position must resolve to an attachable slot whose
        # stored character matches the supplied base (catches spans crossing
        # alignment gaps, introns, or the aligned region's edge).
        slots: list[_Slot] = []
        unresolved = False
        for offset in range(ref_len):
            slot = ctx.slot_map.get(var.pos + offset)
            if slot is None or not _slot_char_matches(ctx, slot, query_upper):
                unresolved = True
                break
            slots.append(slot)
        if unresolved:
            rejections.append(_Rejection(
                kind='unmapped', pos=var.pos + 1,
                message=f'pos {var.pos + 1}: VCF {var.ref}>{var.alt} is outside the aligned '
                        'region — variant skipped',
            ))
            slots_by_key[key] = None
            continue
        # Gate 2b: a VCF deletion overlapping or directly bordering a baseline
        # (supplied-vs-internal) deletion — or a multi-base REF block spanning
        # across a baseline deletion — cannot be resolved unambiguously without
        # full haplotype phase; skip the call with a warning and retain the
        # baseline deletion.
        col_lo = min(s.column for s in slots)
        col_hi = max(s.column for s in slots)
        is_deletion = any(len(c.alt) < ref_len for c in groups[key])
        check_lo = max(0, col_lo - 1) if is_deletion else col_lo
        check_hi = min(len(ctx.ref_positions) - 1, col_hi + 1) if is_deletion else col_hi
        if (is_deletion or ref_len > 1) and any(
            ctx.ref_positions[c][1] == '-' for c in range(check_lo, check_hi + 1)
        ):
            label = 'VCF deletion' if is_deletion else 'VCF'
            rejections.append(_Rejection(
                kind='overlapping_deletion', pos=var.pos + 1,
                message=f'pos {var.pos + 1}: {label} {var.ref}>{var.alt} overlaps a '
                        'reference deletion — variant skipped',
            ))
            slots_by_key[key] = None
            continue
        # Gate 3: one record's ALTs are mutually exclusive — their frequencies
        # must not exceed the position's total frequency mass.
        total_af = sum(call.allele_freq for call in groups[key])
        if total_af > 1.0 + _AF_MASS_EPS:
            rejections.append(_Rejection(
                kind='af_mass', pos=var.pos + 1,
                message=f'pos {var.pos + 1}: allele frequencies sum to {total_af:.3f} (> 1) — '
                        'variants skipped',
            ))
            slots_by_key[key] = None
            continue
        slots_by_key[key] = slots

    # Gate 4: two verified allele sets whose supplied-reference spans overlap
    # cannot be jointly phased — skip both and keep the baseline for the span.
    # Spans are compared in supplied coordinates (POS .. POS+len(REF)-1,
    # including the anchor), not projected slot columns: two records at
    # distinct supplied positions never overlap, even when their slots share a
    # column across an insertion boundary.
    verified = [key for key in order if slots_by_key.get(key) is not None]
    conflicting: set[tuple[str, int, str, int]] = set()
    for i, key_a in enumerate(verified):
        slots_a = slots_by_key[key_a]
        assert slots_a is not None
        span_a = (key_a[1], key_a[1] + len(key_a[2]) - 1)
        for key_b in verified[i + 1:]:
            slots_b = slots_by_key[key_b]
            assert slots_b is not None
            span_b = (key_b[1], key_b[1] + len(key_b[2]) - 1)
            if span_a[0] <= span_b[1] and span_b[0] <= span_a[1]:
                conflicting.add(key_a)
                conflicting.add(key_b)
    if conflicting:
        span_text = ', '.join(
            f'{key[0]}:{key[1] + 1}{key[2]}>' + '/'.join(c.alt for c in groups[key])
            for key in verified if key in conflicting
        )
        rejections.append(_Rejection(
            kind='phase', pos=None,
            message='Overlapping VCF records cannot be phased together — variants skipped: '
                    + span_text,
        ))

    used_record_ids = {key[3] for key in order if key[3] >= 0}
    next_synthetic_record_id = max(used_record_ids, default=-1) + 1
    record_id_by_key: dict[tuple[str, int, str, int], int] = {}
    for key in order:
        if key[3] >= 0:
            record_id_by_key[key] = key[3]
        else:
            record_id_by_key[key] = next_synthetic_record_id
            next_synthetic_record_id += 1

    plans: list[_CallPlan] = []
    for key in order:
        key_slots = slots_by_key.get(key)
        if key_slots is None or key in conflicting:
            continue
        record_id = record_id_by_key[key]
        used_alt_ids = {c.alt_index for c in groups[key] if c.alt_index >= 0}
        next_alt_id = max(used_alt_ids, default=-1) + 1
        for call in groups[key]:
            if call.alt_index >= 0:
                alt_id = call.alt_index
            else:
                alt_id = next_alt_id
                next_alt_id += 1
            coding_slots = key_slots if ctx.match.strand == '+' else list(reversed(key_slots))
            block = call.alt if ctx.match.strand == '+' else str(Seq(call.alt).reverse_complement())
            plans.append(_CallPlan(
                call=call, slots=coding_slots, block=block, record_id=record_id, alt_id=alt_id,
            ))
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
) -> tuple[list[VariantCall], set[int], set[int]]:
    """
    Emit one call's sample state against the internal reference.

    The state replaces the supplied REF span's slots with the ALT (in coding
    orientation) inside the match's alignment context, collapses coincident
    insertion+deletion gaps into substitutions, then walks a window around the
    span for state-vs-internal differences. Every emitted variant carries the
    call's allele frequency, the ``vcf_derived`` source label, and the original
    supplied coordinates.

    Only **call-attributable** differences are emitted, per sub-space: a
    column's base and its pending insertion payload change independently, so a
    call that edits the base must not re-emit the column's unchanged pending
    payload, and vice versa. Differences the call merely sits next to belong to
    the baseline emission, which carries them at full frequency mass.

    :return: (emitted variants, covered base columns, covered pending columns)
        — the covered sets are the columns whose base / pending payload the
        call changed, plus the columns of emitted events (a merged deletion
        run absorbs baseline-deleted columns beyond the span). The residual
        pass subtracts the call's frequency mass from baseline variants in the
        matching sub-space whose columns intersect.
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
    # Snapshot the baseline state over the surgery's reach: the span columns,
    # any contiguous run of deleted or repeat-adjacent columns bordering the
    # span, and one anchor flank on each side. Columns outside the snapshot are
    # guaranteed unmodified, which the walk relies on to skip them.
    snap_lo = max(0, col_lo - 1)
    while snap_lo > 0 and (
        ref_positions[snap_lo][1] == '-'
        or ref_positions[snap_lo - 1][1] == '-'
        or ref_positions[snap_lo][0] == ref_positions[min(col_hi, snap_lo + 1)][0]
    ):
        snap_lo -= 1
    if snap_lo > 0:
        snap_lo -= 1

    snap_hi = min(len(ref_positions) - 1, col_hi + 1)
    while snap_hi + 1 < len(ref_positions) and (
        ref_positions[snap_hi][1] == '-'
        or ref_positions[snap_hi + 1][1] == '-'
        or ref_positions[snap_hi][0] == ref_positions[max(col_lo, snap_hi - 1)][0]
    ):
        snap_hi += 1
    if snap_hi + 1 < len(ref_positions):
        snap_hi += 1
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
        # Collect the surgery edits per slot, then apply them: base-slot edits
        # are per-column and order-independent, but several pending slots can
        # share one column's pending string — their character indexes were
        # computed against the original string, so pending edits must be
        # applied per column in DESCENDING char_idx order (removals otherwise
        # shift the indexes of the edits still to come).
        base_edits: list[tuple[int, str | None]] = []
        pending_edits: dict[int, list[tuple[int, str | None]]] = {}
        for i, slot in enumerate(slots):
            # '+' matches read the ALT block in coding order: slot i gets
            # block[i], and slots beyond the ALT are deleted. '-' matches read
            # it reversed with the block anchored at the coding-first slot, so
            # slot i gets block[i + shift] — extra ALT bases lead the span.
            block_idx = i if plus_strand else i + shift
            new_char = block[block_idx] if 0 <= block_idx < alt_len else None
            if slot.kind == 'base':
                base_edits.append((slot.column, new_char))
            else:
                pending_edits.setdefault(slot.column, []).append((slot.char_idx, new_char))

        for column, new_char in base_edits:
            entry = ref_positions[column]
            # None means the slot is deleted (ALT shorter than REF).
            ref_positions[column] = [entry[0], new_char if new_char is not None else '-', entry[2]]
        for column, edits in pending_edits.items():
            entry = ref_positions[column]
            p = entry[2]
            for char_idx, new_char in sorted(edits, key=lambda e: e[0], reverse=True):
                if new_char is None:
                    p = p[:char_idx] + p[char_idx + 1:]
                else:
                    p = p[:char_idx] + new_char + p[char_idx + 1:]
            ref_positions[column] = [entry[0], entry[1], p]

        if shift > 0:
            # ALT bases beyond the REF span become an insertion attached at the
            # span's free end in coding orientation: after the coding-last slot
            # for '+' matches (prepended to the next column's pending string),
            # before the coding-first slot for '-' matches.
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

        # Columns whose base / pending payload the call changed relative to the
        # baseline — per sub-space, so the walk and the residual pass can
        # attribute each event kind independently. Keys are converted to coding
        # coordinates once: the walk and covered sets work in coding
        # coordinates, while the snapshot keys are full-CDS columns. Base slots
        # spanned by length-reducing calls (alt_len < ref_len) are included in
        # changed_base so a length-changing replacement countered by a deletion
        # (e.g. A -> GG with G2del) emits and covers the surviving base
        # difference as vcf_derived, while equal-length block substitutions do
        # not falsely claim unchanged baseline-divergent positions.
        saved_coding = {j - off: entry for j, entry in saved.items()}
        call_base_cols = {
            s.column - off for s in slots
            if s.kind == 'base' and 0 <= s.column - off < len(coding)
        } if (alt_len < ref_len and any(s.kind == 'pending' for s in slots)) else set()
        changed_base = {
            j for j, entry in saved_coding.items()
            if 0 <= j < len(coding) and ref_positions[j + off][1] != entry[1]
        } | call_base_cols
        changed_pending = {
            j for j, entry in saved_coding.items()
            if 0 <= j < len(coding) and ref_positions[j + off][2] != entry[2]
        }

        lo = max(0, snap_lo - off)
        hi = min(len(coding) - 1, snap_hi - off)

        emitted = _walk_diff(ctx, lo, hi, changed_base, changed_pending, pending_tail)
        if alt_len > ref_len and extra_column >= len(ref_positions):
            # Insertion past the last internal column: only '+'-strand features
            # have a representable anchor (the last internal base); '-'-strand
            # features would need the following genomic base, which is outside
            # the feature.
            if feature.strand == '+':
                emitted.extend(_emit_trailing_insertion(
                    ctx, col_hi - off, trailing_extra, call,
                    record_id=plan.record_id, alt_id=plan.alt_id,
                ))
            else:
                warnings.append(
                    f'pos {call.pos + 1}: VCF insertion {call.ref}>{call.alt} extends past the end '
                    f'of feature {feature.name!r} — variant skipped'
                )
    finally:
        for j, entry in saved.items():
            ref_positions[j] = entry

    call_link = ((plan.record_id, plan.alt_id, call.allele_freq),)
    for var in emitted:
        var.allele_freq = call.allele_freq
        var.depth = call.depth
        var.source = VCF_DERIVED
        var.user_chrom = call.chrom
        var.user_pos = call.pos
        var.user_ref = call.ref
        var.user_alt = call.alt
        var.record_index = plan.record_id
        var.alt_index = plan.alt_id
        var.haplotype_links = call_link
        _attach_supplied_codon(ctx, var)

    # Covered columns per sub-space: the columns the call changed, plus the
    # columns of emitted events (a merged deletion run absorbs
    # baseline-deleted columns beyond the span; insertions occupy their
    # pending column).
    covered_base = set(changed_base)
    covered_pending = set(changed_pending)
    for var in emitted:
        cols = _variant_occupied_columns(ctx, var)
        if len(var.alt) > len(var.ref):
            covered_pending |= cols
        else:
            covered_base |= cols
    return emitted, covered_base, covered_pending


def _collapse_coincident_gaps(
    ref_positions: list[list[str]],
    lo: int,
    hi: int,
) -> None:
    """
    Collapse coincident insertion+deletion gaps and merge repeat-separated deletions.

    1. Across each maximal contiguous run of deleted columns ``c_start..c_end``,
       collect the concatenated pending insertion bases ``P`` sitting in that
       gap (``pending[c_start..c_end]`` plus ``pending[c_end + 1]`` when
       ``c_end + 1 <= hi``) and distribute them in forward sequence order across
       ``c_start..c_end`` without inverting ``P``.
    2. When a deletion run of a single repeated base is separated from another
       deletion run only by columns carrying that same repeat base (with empty
       pending strings), shift the repeat-base deletion across the identical
       repeat columns so the two deletions merge into one contiguous run.
    """
    j = lo
    while j <= hi:
        if ref_positions[j][1] != '-':
            j += 1
            continue
        c_start = j
        c_end = j
        while c_end + 1 <= hi and ref_positions[c_end + 1][1] == '-':
            c_end += 1
        d_len = c_end - c_start + 1
        has_next = c_end + 1 <= hi
        p_cols = range(c_start, (c_end + 2) if has_next else (c_end + 1))
        pending_all = ''.join(ref_positions[k][2] for k in p_cols)
        if pending_all:
            for k in p_cols:
                ref_positions[k][2] = ''
            ref_slice = [ref_positions[k][0] for k in range(c_start, c_end + 1)]
            p_len = len(pending_all)
            if p_len <= d_len:
                prefix_matches = sum(
                    1 for a, b in zip(pending_all, ref_slice[:p_len]) if a == b
                )
                suffix_matches = sum(
                    1 for a, b in zip(pending_all, ref_slice[d_len - p_len:]) if a == b
                )
                start_col = (
                    c_end - p_len + 1
                    if suffix_matches >= prefix_matches
                    else c_start
                )
                for offset, ch in enumerate(pending_all):
                    ref_positions[start_col + offset][1] = ch
            else:
                prefix_matches = sum(
                    1 for a, b in zip(pending_all[:d_len], ref_slice) if a == b
                )
                suffix_matches = sum(
                    1 for a, b in zip(pending_all[p_len - d_len:], ref_slice) if a == b
                )
                if has_next and prefix_matches > suffix_matches:
                    for offset in range(d_len):
                        ref_positions[c_start + offset][1] = pending_all[offset]
                    ref_positions[c_end + 1][2] = pending_all[d_len:]
                else:
                    extra = pending_all[:p_len - d_len]
                    fill = pending_all[p_len - d_len:]
                    ref_positions[c_start][2] = extra
                    for offset in range(d_len):
                        ref_positions[c_start + offset][1] = fill[offset]
        j = c_end + 1

    # Merge deletions separated only by identical repeat bases.
    j = lo
    while j <= hi:
        if ref_positions[j][1] != '-' or ref_positions[j][2]:
            j += 1
            continue
        d_lo = j
        d_hi = j
        while (
            d_hi + 1 <= hi
            and ref_positions[d_hi + 1][1] == '-'
            and not ref_positions[d_hi + 1][2]
        ):
            d_hi += 1
        repeat_base = ref_positions[d_lo][0]
        if all(ref_positions[k][0] == repeat_base for k in range(d_lo, d_hi + 1)):
            r_start = d_lo
            while (
                r_start > lo
                and ref_positions[r_start - 1][1] == repeat_base
                and not ref_positions[r_start - 1][2]
            ):
                r_start -= 1
            if r_start < d_lo and r_start > lo and ref_positions[r_start - 1][1] == '-':
                del_count = d_hi - d_lo + 1
                for k in range(r_start, r_start + del_count):
                    ref_positions[k][1] = '-'
                for k in range(r_start + del_count, d_hi + 1):
                    ref_positions[k][1] = repeat_base
                j = d_hi + 1
                continue

            r_end = d_hi
            while (
                r_end + 1 <= hi
                and ref_positions[r_end + 1][1] == repeat_base
                and ref_positions[r_end + 1][0] == repeat_base
                and not ref_positions[r_end + 1][2]
            ):
                r_end += 1
            if r_end > d_hi and r_end + 1 <= hi and ref_positions[r_end + 1][1] == '-':
                rep_count = r_end - d_hi
                for k in range(d_lo, d_lo + rep_count):
                    ref_positions[k][1] = repeat_base
                for k in range(d_lo + rep_count, r_end + 1):
                    ref_positions[k][1] = '-'
                j = r_end + 1
                continue
        j = d_hi + 1


def _walk_diff(
    ctx: _MatchAlignment,
    lo: int,
    hi: int,
    changed_base: set[int],
    changed_pending: set[int],
    pending_tail: int = -1,
) -> list[VariantCall]:
    """
    Walk the state's coding window for call-attributable differences.

    A column's difference vs the internal reference is emitted only when the
    call affected the **matching sub-space** of that column relative to the
    baseline (the plain supplied reference): insertion payloads (the pending
    string) are attributed to ``changed_pending``, SNPs and deletions to
    ``changed_base``. This keeps shared neighbour differences (a baseline SNP
    or insertion next to the call's span) with the baseline pass, which emits
    them once at full frequency mass instead of once per flanking call state.

    Deletion runs are emitted as one merged event when **any** base of the run
    changed — the merged event then also represents the baseline-deleted
    columns it absorbs (their residual mass is reduced via the covered set).
    Runs where nothing changed are pure baseline deletions and are skipped.

    :param changed_base: coding columns whose base sub-space is call-attributed
    :param changed_pending: coding columns whose pending sub-space changed
    :param pending_tail: coding column just past the window carrying a modified
        pending payload ('+'-match insertion extras), or -1
    """
    feature = ctx.feature
    coding = ctx.coding
    emitted: list[VariantCall] = []

    def _base_changed(col: int) -> bool:
        return col in changed_base

    def _pending_changed(col: int) -> bool:
        return col in changed_pending

    idx = lo
    while idx <= hi:
        ref_base, sup, pending = coding[idx]
        if pending and _pending_changed(idx):
            var = _make_fasta_insertion_from_alignment(feature, coding, idx, pending)
            if var is not None:
                emitted.append(var)
        if sup == '-':
            run_end = idx
            while run_end + 1 <= hi and coding[run_end + 1][1] == '-':
                run_end += 1
            if any(_base_changed(col) for col in range(idx, run_end + 1)):
                var = _make_fasta_deletion_from_alignment(feature, coding, idx, run_end)
                if var is not None:
                    emitted.append(var)
            idx = run_end + 1
            continue
        if _base_changed(idx) and sup != ref_base:
            var = _make_variant_from_coding_nt(feature, idx, ref_base, sup)
            emitted.append(var)
        idx += 1

    # A pending tail strictly beyond the walked window is emitted here; tails
    # inside the window were already emitted by the main loop above (the window
    # is extended to include the '+'-match insertion attachment column).
    if pending_tail > hi and pending_tail < len(coding):
        tail = coding[pending_tail][2]
        if _pending_changed(pending_tail) and tail:
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
    *,
    record_id: int = -1,
    alt_id: int = -1,
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
    var.record_index = record_id
    var.alt_index = alt_id
    var.haplotype_links = ((record_id, alt_id, call.allele_freq),) if record_id >= 0 else ()
    _attach_supplied_codon(ctx, var)
    return [var]


def _attach_supplied_codon(ctx: _MatchAlignment, var: VariantCall) -> None:
    """Bind the internal-reference codon context to an emitted variant.

    Because reconciliation already emits every surviving supplied-vs-internal
    difference in a codon as its own ``VariantCall``, ``query_ref_codon`` must
    carry the internal reference codon (``ctx.coding[...][0]``) so downstream
    annotation does not apply reverted or rejected baseline differences a
    second time or overwrite internal ``ref_aa`` on indels.

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
    codon_start = (coding_idx // 3) * 3
    if codon_start + 3 > len(ctx.coding):
        return
    if any(ctx.coding[codon_start + offset][1] == '-' for offset in range(3)):
        return
    codon = ''.join(ctx.coding[codon_start + offset][0] for offset in range(3))
    if '-' in codon:
        return
    var.query_ref_codon = codon
    var.query_codon_feature_id = feature.id


def _baseline_residual(
    var: VariantCall,
    occupied: set[int],
    covered_sets: list[tuple[set[int], float]],
) -> tuple[float, str]:
    """
    Compute a baseline variant's residual frequency mass and source label.

    A call's haplotype fails to carry the baseline allele at a column only when
    the call affected that column (its covered set intersects the variant's
    occupied columns) — a call whose span merely sits next to the baseline
    variant leaves the baseline allele intact on its molecules. The residual
    mass is therefore the baseline frequency minus the total frequency of the
    calls covering it; the source remains ``reference_derived`` because the
    allele itself is inferred from the supplied-vs-internal reference
    difference rather than from a VCF call.

    :param var: baseline variant (its allele_freq is the baseline frequency)
    :param occupied: coding columns the variant occupies
    :param covered_sets: (covered columns, allele frequency) per call state
    :return: (residual frequency, source label)
    """
    overlapping = sum(
        af for covered, af in covered_sets if occupied and covered & occupied
    )
    residual = var.allele_freq - overlapping
    return max(residual, 0.0), REFERENCE_DERIVED


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


def _merge_haplotype_links(
    links_a: tuple[tuple[int, int, float], ...],
    links_b: tuple[tuple[int, int, float], ...],
) -> tuple[tuple[int, int, float], ...]:
    """Merge haplotype-link entries across contributors for one aggregated variant."""
    if not links_a:
        return links_b
    if not links_b:
        return links_a
    merged: dict[tuple[int, int], float] = {}
    link_order: list[tuple[int, int]] = []
    for rec_id, alt_id, af in (*links_a, *links_b):
        pair = (rec_id, alt_id)
        if pair not in merged:
            merged[pair] = float(af)
            link_order.append(pair)
        else:
            merged[pair] += float(af)
    return tuple((rec_id, alt_id, merged[(rec_id, alt_id)]) for rec_id, alt_id in link_order)


def _aggregate_emissions(emissions: list[VariantCall]) -> list[VariantCall]:
    """
    Sum frequency mass per (chrom, pos, ref, alt) key within one match.

    The same internal allele can be emitted by several mutually exclusive
    sources: a call state and the residual baseline state (partial
    cancellation), or several call states that each reach the allele through a
    different haplotype. Their frequencies add up to the allele's total
    frequency — e.g. a call at AF 0.5 that reverts a baseline difference emits
    the reverted allele at 0.5 from the call state, while the baseline pass
    emits the original difference at the residual 0.5.

    The aggregated variant keeps the first contributor's identity and adopts
    ``vcf_derived`` provenance, user coordinates, depth and codon context from
    the first call-derived contributor, while merging ``haplotype_links`` across
    all contributors.
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
        first.haplotype_links = _merge_haplotype_links(first.haplotype_links, var.haplotype_links)
        if var.source == VCF_DERIVED:
            first.source = VCF_DERIVED
            if not first.user_ref:
                first.user_chrom = var.user_chrom
                first.user_pos = var.user_pos
                first.user_ref = var.user_ref
                first.user_alt = var.user_alt
            if first.record_index < 0 and var.record_index >= 0:
                first.record_index = var.record_index
                first.alt_index = var.alt_index
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
            f'Collapsed {collapsed} duplicate variant(s) from overlapping feature matches: '
            f'{details}'
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
    full_spans = full_intron_spans([
        IntronInterval(cds_junction_pos=0, query_start=start, length=end - start)
        for start, end in spans
    ])

    corrected: dict[int, int | None] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is None:
            corrected[cds_pos] = None
            continue
        # Cumulative intron length for introns whose exon-only start is at or before qpos.
        shift = sum(end - start for start, end in spans if start <= qpos)
        new_pos = qpos + shift
        # Exclude positions that land inside an intron span in full-region coordinates.
        if any(full_start <= new_pos < full_end for full_start, full_end in full_spans):
            corrected[cds_pos] = None
            continue
        corrected[cds_pos] = new_pos
    return corrected
