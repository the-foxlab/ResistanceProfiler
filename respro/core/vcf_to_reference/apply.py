"""Direct VCF remapping, surgical overlap resolution, residual emission, and variant merging."""

from __future__ import annotations

from dataclasses import dataclass, replace

from respro.core.coordinates import match_rank
from respro.core.fasta_to_vcf import (
    make_coding_deletion,
    make_coding_insertion,
    make_coding_variant,
)
from respro.core.vcf_to_reference.reference_alignment import (
    REFERENCE_DERIVED,
    VCF_DERIVED,
    AlignedColumn,
    FeatureAlignment,
    ReferenceMismatch,
    SuppliedPosition,
    attach_internal_codon,
    variant_coding_columns,
)
from respro.core.vcf_to_reference.validate import ValidatedCall
from respro.db.models import FeatureMatch, VariantCall

_RESIDUAL_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class AppliedOverlap:
    """Coding columns touched by one surgically resolved VCF call (for residual AF calculation)."""

    record_id: int
    alt_id: int
    allele_freq: float
    covered_base: set[int]
    covered_inserted: set[int]
    spanned_base: set[int]
    spanned_inserted: set[int]


# ─── Partition calls ONCE by overlap with reference mismatches ───────────────


def partition_overlapping_calls(
    alignment: FeatureAlignment,
    calls: list[ValidatedCall],
) -> tuple[list[ValidatedCall], list[ValidatedCall]]:
    """Split *calls* into ``(direct_calls, overlapping_calls)`` by VCF record.

    Calls whose local span sits in a region where supplied == internal need no
    surgical editing or residual attribution. Sibling ALTs of one multi-allelic
    VCF record always stay in the same partition.
    """
    records: dict[int, list[ValidatedCall]] = {}
    order: list[int] = []
    for call in calls:
        if call.record_id not in records:
            records[call.record_id] = []
            order.append(call.record_id)
        records[call.record_id].append(call)

    direct: list[ValidatedCall] = []
    overlapping: list[ValidatedCall] = []
    for rec_id in order:
        group = records[rec_id]
        if _record_overlaps_ref_mismatch(alignment, group):
            overlapping.extend(group)
        else:
            direct.extend(group)
    return direct, overlapping


def _record_overlaps_ref_mismatch(
    alignment: FeatureAlignment,
    record_calls: list[ValidatedCall],
) -> bool:
    """Return True when a VCF record touches or borders any supplied-vs-internal difference."""
    positions = record_calls[0].positions
    if any(pos.kind != 'aligned' for pos in positions):
        return True

    columns = alignment.columns
    col_lo = min(pos.column for pos in positions)
    col_hi = max(pos.column for pos in positions)
    has_indel = any(len(c.coding_alt) != len(positions) for c in record_calls)

    if not has_indel:
        return any(columns[col].supplied != columns[col].internal for col in range(col_lo, col_hi + 1))

    win_lo, win_hi = _local_window_bounds(columns, col_lo, col_hi)
    return any(
        columns[col].supplied != columns[col].internal or bool(columns[col].inserted)
        for col in range(win_lo, win_hi + 1)
    )


# ─── Direct path: call sits where supplied reference == internal CDS ─────────


def remap_direct_call(
    alignment: FeatureAlignment,
    vcall: ValidatedCall,
    warnings: list[str],
) -> list[VariantCall]:
    """Translate one VCF call directly into internal coordinates (no reference mismatches in span)."""
    feature = alignment.feature
    columns = alignment.columns
    off = feature.codon_start
    coding_len = len(columns) - off
    plus_strand = alignment.match.strand == '+'

    positions = vcall.positions
    alt = vcall.coding_alt
    ref_len = len(positions)
    alt_len = len(alt)
    shift = alt_len - ref_len

    emitted: list[VariantCall] = []

    # 1. Substitution(s) at aligned positions (handles SNPs, MNVs, and anchor-changed indels).
    deleted_coding_cols: list[int] = []
    for i, pos in enumerate(positions):
        coding_idx = pos.column - off
        alt_idx = i if plus_strand else i + shift
        if 0 <= alt_idx < alt_len:
            if 0 <= coding_idx < coding_len and alt[alt_idx] != columns[pos.column].internal:
                emitted.append(make_coding_variant(
                    feature, coding_idx, columns[pos.column].internal, alt[alt_idx],
                ))
        elif 0 <= coding_idx < coding_len:
            deleted_coding_cols.append(coding_idx)

    # 2. Net deletion across removed positions.
    if deleted_coding_cols:
        var = make_coding_deletion(
            feature, alignment.ref_coding_seq, min(deleted_coding_cols), max(deleted_coding_cols),
        )
        if var is not None:
            emitted.append(var)

    # 3. Net insertion of extra ALT bases.
    if shift > 0:
        extra = alt[ref_len:] if plus_strand else alt[:shift]
        ins_col = positions[-1].column + 1 if plus_strand else positions[0].column
        if ins_col < len(columns):
            ins_idx = ins_col - off
            if 0 <= ins_idx < coding_len:
                var = make_coding_insertion(feature, alignment.ref_coding_seq, ins_idx, extra)
                if var is not None:
                    emitted.append(var)
        elif feature.strand == '+':
            emitted.append(_make_trailing_insertion(alignment, positions[-1].column - off, extra))
        else:
            warnings.append(
                f'pos {vcall.call.pos + 1}: VCF insertion {vcall.call.ref}>{vcall.call.alt} extends past '
                f'the end of feature {feature.name!r} — variant skipped'
            )

    for var in emitted:
        _stamp_vcf_variant(alignment, vcall, var)
    return emitted


# ─── Surgical path: call overlaps or borders a reference mismatch ────────────


def resolve_overlapping_call(
    alignment: FeatureAlignment,
    vcall: ValidatedCall,
    warnings: list[str],
) -> tuple[list[VariantCall], AppliedOverlap]:
    """Surgically apply one overlapping VCF call in a local window and restore the alignment."""
    feature = alignment.feature
    call = vcall.call
    positions = vcall.positions
    off = feature.codon_start
    columns = alignment.columns
    coding_len = len(columns) - off

    col_lo = min(p.column for p in positions)
    col_hi = max(p.column for p in positions)
    win_lo, win_hi = _local_window_bounds(columns, col_lo, col_hi)
    saved = {j: columns[j].copy() for j in range(win_lo, win_hi + 1)}

    plus_strand = alignment.match.strand == '+'
    extra_col, extra_offset = _extra_attachment_point(positions, plus_strand)
    alt_len = len(call.alt)
    ref_len = len(call.ref)

    try:
        inserted_tail, trailing_extra = _apply_window_edits(
            columns, positions, vcall.coding_alt, plus_strand, extra_col, extra_offset, off, col_hi,
        )
        _collapse_coincident_gaps(columns, win_lo, win_hi)

        # Length-reducing calls spanning a ref insertion also claim their aligned slots so a
        # replacement countered by a deletion emits the surviving difference as vcf_derived.
        saved_coding = {j - off: col for j, col in saved.items()}
        claimed_base = (
            {p.column - off for p in positions if p.kind == 'aligned' and 0 <= p.column - off < coding_len}
            if (alt_len < ref_len and any(p.kind == 'inserted' for p in positions))
            else set()
        )
        changed_base = {
            j for j, prev in saved_coding.items()
            if 0 <= j < coding_len and columns[j + off].supplied != prev.supplied
        } | claimed_base
        changed_inserted = {
            j for j, prev in saved_coding.items()
            if 0 <= j < coding_len and columns[j + off].inserted != prev.inserted
        }

        lo = max(0, win_lo - off)
        hi = min(coding_len - 1, win_hi - off)
        emitted = _emit_window_differences(alignment, lo, hi, changed_base, changed_inserted, inserted_tail)

        if alt_len > ref_len and extra_col >= len(columns):
            if feature.strand == '+':
                emitted.append(_make_trailing_insertion(alignment, col_hi - off, trailing_extra))
            else:
                warnings.append(
                    f'pos {call.pos + 1}: VCF insertion {call.ref}>{call.alt} extends past the end '
                    f'of feature {feature.name!r} — variant skipped'
                )
    finally:
        for j, prev in saved.items():
            columns[j] = prev

    for var in emitted:
        _stamp_vcf_variant(alignment, vcall, var)

    covered_base = set(changed_base)
    covered_inserted = set(changed_inserted)
    for var in emitted:
        cols = variant_coding_columns(alignment, var)
        if len(var.alt) > len(var.ref):
            covered_inserted |= cols
        else:
            covered_base |= cols

    applied = AppliedOverlap(
        record_id=vcall.record_id,
        alt_id=vcall.alt_id,
        allele_freq=call.allele_freq,
        covered_base=covered_base,
        covered_inserted=covered_inserted,
        spanned_base={p.column - off for p in positions if p.kind == 'aligned' and 0 <= p.column - off < coding_len},
        spanned_inserted={p.column - off for p in positions if p.kind == 'inserted' and 0 <= p.column - off < coding_len},
    )
    return emitted, applied


def _stamp_vcf_variant(alignment: FeatureAlignment, vcall: ValidatedCall, var: VariantCall) -> None:
    """Attach VCF-derived provenance, user coordinates, haplotype link, and codon context."""
    call = vcall.call
    var.allele_freq = call.allele_freq
    var.depth = call.depth
    var.source = VCF_DERIVED
    var.user_chrom = call.chrom
    var.user_pos = call.pos
    var.user_ref = call.ref
    var.user_alt = call.alt
    var.record_index = vcall.record_id
    var.alt_index = vcall.alt_id
    var.haplotype_links = ((vcall.record_id, vcall.alt_id, call.allele_freq),)
    attach_internal_codon(alignment, var)


def _local_window_bounds(
    columns: list[AlignedColumn],
    col_lo: int,
    col_hi: int,
) -> tuple[int, int]:
    """Return inclusive bounds covering the call span, bordering deletions/repeats, and 1-bp flanks."""
    win_lo = max(0, col_lo - 1)
    while win_lo > 0 and (
        columns[win_lo].supplied == '-'
        or columns[win_lo - 1].supplied == '-'
        or columns[win_lo].internal == columns[min(col_hi, win_lo + 1)].internal
    ):
        win_lo -= 1
    if win_lo > 0:
        win_lo -= 1

    win_hi = min(len(columns) - 1, col_hi + 1)
    while win_hi + 1 < len(columns) and (
        columns[win_hi].supplied == '-'
        or columns[win_hi + 1].supplied == '-'
        or columns[win_hi].internal == columns[max(col_lo, win_hi - 1)].internal
    ):
        win_hi += 1
    if win_hi + 1 < len(columns):
        win_hi += 1

    return win_lo, win_hi


def _extra_attachment_point(
    positions: list[SuppliedPosition],
    plus_strand: bool,
) -> tuple[int, int]:
    """Return ``(column, insert_offset)`` where ALT bases beyond REF length attach."""
    if plus_strand:
        last = positions[-1]
        return (last.column + 1, 0) if last.kind == 'aligned' else (last.column, last.insert_offset + 1)
    first = positions[0]
    return (first.column, 0) if first.kind == 'aligned' else (first.column, first.insert_offset)


def _apply_window_edits(
    columns: list[AlignedColumn],
    positions: list[SuppliedPosition],
    coding_alt: str,
    plus_strand: bool,
    extra_col: int,
    extra_offset: int,
    frame_offset: int,
    col_hi: int,
) -> tuple[int, str]:
    """Mutate ``columns`` in place with one call's ALT block; return ``(inserted_tail, trailing_extra)``."""
    alt_len = len(coding_alt)
    ref_len = len(positions)
    shift = alt_len - ref_len

    base_edits: list[tuple[int, str | None]] = []
    insert_edits: dict[int, list[tuple[int, str | None]]] = {}
    for i, pos in enumerate(positions):
        # '+' anchors ALT at slot 0; '-' anchors reversed ALT so extras lead slot 0.
        alt_idx = i if plus_strand else i + shift
        new_char = coding_alt[alt_idx] if 0 <= alt_idx < alt_len else None
        if pos.kind == 'aligned':
            base_edits.append((pos.column, new_char))
        else:
            insert_edits.setdefault(pos.column, []).append((pos.insert_offset, new_char))

    for column, new_char in base_edits:
        columns[column].supplied = new_char if new_char is not None else '-'

    # Apply insert edits in descending offset order so deletions do not shift earlier offsets.
    for column, edits in insert_edits.items():
        inserted = columns[column].inserted
        for offset, new_char in sorted(edits, key=lambda e: e[0], reverse=True):
            replacement = '' if new_char is None else new_char
            inserted = inserted[:offset] + replacement + inserted[offset + 1:]
        columns[column].inserted = inserted

    inserted_tail = -1
    trailing_extra = ''
    if shift > 0:
        trailing_extra = coding_alt[ref_len:] if plus_strand else coding_alt[:shift]
        if extra_col < len(columns):
            inserted = columns[extra_col].inserted
            columns[extra_col].inserted = (
                inserted[:extra_offset] + trailing_extra + inserted[extra_offset:]
            )
            if plus_strand and extra_col - frame_offset > col_hi - frame_offset:
                inserted_tail = extra_col - frame_offset
    return inserted_tail, trailing_extra


def _collapse_coincident_gaps(columns: list[AlignedColumn], lo: int, hi: int) -> None:
    """Cancel coincident insertions+deletions in the edited window and merge repeat-separated deletions."""
    j = lo
    while j <= hi:
        if columns[j].supplied != '-':
            j += 1
            continue
        c_end = j
        while c_end + 1 <= hi and columns[c_end + 1].supplied == '-':
            c_end += 1
        _fill_gap_with_inserted(columns, j, c_end, has_next=c_end + 1 <= hi)
        j = c_end + 1
    _merge_repeat_separated_deletions(columns, lo, hi)


def _fill_gap_with_inserted(
    columns: list[AlignedColumn],
    c_start: int,
    c_end: int,
    *,
    has_next: bool,
) -> None:
    """Place inserted bases sitting across deletion run ``c_start..c_end`` back into the gap."""
    p_cols = range(c_start, (c_end + 2) if has_next else (c_end + 1))
    inserted_all = ''.join(columns[k].inserted for k in p_cols)
    if not inserted_all:
        return

    for k in p_cols:
        columns[k].inserted = ''
    internal_slice = [columns[k].internal for k in range(c_start, c_end + 1)]
    d_len = c_end - c_start + 1
    i_len = len(inserted_all)

    if i_len <= d_len:
        prefix_matches = sum(a == b for a, b in zip(inserted_all, internal_slice[:i_len]))
        suffix_matches = sum(a == b for a, b in zip(inserted_all, internal_slice[d_len - i_len:]))
        start_col = c_end - i_len + 1 if suffix_matches >= prefix_matches else c_start
        for offset, ch in enumerate(inserted_all):
            columns[start_col + offset].supplied = ch
        return

    prefix_matches = sum(a == b for a, b in zip(inserted_all[:d_len], internal_slice))
    suffix_matches = sum(a == b for a, b in zip(inserted_all[i_len - d_len:], internal_slice))
    if has_next and prefix_matches > suffix_matches:
        for offset in range(d_len):
            columns[c_start + offset].supplied = inserted_all[offset]
        columns[c_end + 1].inserted = inserted_all[d_len:]
    else:
        columns[c_start].inserted = inserted_all[:i_len - d_len]
        fill = inserted_all[i_len - d_len:]
        for offset in range(d_len):
            columns[c_start + offset].supplied = fill[offset]


def _merge_repeat_separated_deletions(columns: list[AlignedColumn], lo: int, hi: int) -> None:
    """Slide homopolymer deletions across identical repeat bases to merge adjacent deletions."""
    j = lo
    while j <= hi:
        if columns[j].supplied != '-' or columns[j].inserted:
            j += 1
            continue
        d_lo = d_hi = j
        while d_hi + 1 <= hi and columns[d_hi + 1].supplied == '-' and not columns[d_hi + 1].inserted:
            d_hi += 1

        repeat_base = columns[d_lo].internal
        if not all(columns[k].internal == repeat_base for k in range(d_lo, d_hi + 1)):
            j = d_hi + 1
            continue

        # Try sliding left across identical repeat bases to merge with a preceding deletion.
        r_start = d_lo
        while r_start > lo and columns[r_start - 1].supplied == repeat_base and not columns[r_start - 1].inserted:
            r_start -= 1
        if r_start < d_lo and r_start > lo and columns[r_start - 1].supplied == '-':
            del_count = d_hi - d_lo + 1
            for k in range(r_start, r_start + del_count):
                columns[k].supplied = '-'
            for k in range(r_start + del_count, d_hi + 1):
                columns[k].supplied = repeat_base
            j = d_hi + 1
            continue

        # Try sliding right across identical repeat bases to merge with a following deletion.
        r_end = d_hi
        while (
            r_end + 1 <= hi
            and columns[r_end + 1].supplied == repeat_base
            and columns[r_end + 1].internal == repeat_base
            and not columns[r_end + 1].inserted
        ):
            r_end += 1
        if r_end > d_hi and r_end + 1 <= hi and columns[r_end + 1].supplied == '-':
            rep_count = r_end - d_hi
            for k in range(d_lo, d_lo + rep_count):
                columns[k].supplied = repeat_base
            for k in range(d_lo + rep_count, r_end + 1):
                columns[k].supplied = '-'
            j = r_end + 1
            continue

        j = d_hi + 1


def _emit_window_differences(
    alignment: FeatureAlignment,
    lo: int,
    hi: int,
    changed_base: set[int],
    changed_inserted: set[int],
    inserted_tail: int = -1,
) -> list[VariantCall]:
    """Emit call-attributable variants across coding window ``lo..hi``."""
    feature = alignment.feature
    coding = alignment.coding
    ref_bases = alignment.ref_coding_seq
    emitted: list[VariantCall] = []

    idx = lo
    while idx <= hi:
        col = coding[idx]
        if col.inserted and idx in changed_inserted:
            var = make_coding_insertion(feature, ref_bases, idx, col.inserted)
            if var is not None:
                emitted.append(var)
        if col.supplied == '-':
            run_end = idx
            while run_end + 1 <= hi and coding[run_end + 1].supplied == '-':
                run_end += 1
            if any(c in changed_base for c in range(idx, run_end + 1)):
                var = make_coding_deletion(feature, ref_bases, idx, run_end)
                if var is not None:
                    emitted.append(var)
            idx = run_end + 1
            continue
        if idx in changed_base and col.supplied != col.internal:
            emitted.append(make_coding_variant(feature, idx, col.internal, col.supplied))
        idx += 1

    if hi < inserted_tail < len(coding) and inserted_tail in changed_inserted:
        tail = coding[inserted_tail].inserted
        if tail:
            var = make_coding_insertion(feature, ref_bases, inserted_tail, tail)
            if var is not None:
                emitted.append(var)
    return emitted


def _make_trailing_insertion(
    alignment: FeatureAlignment,
    anchor_idx: int,
    extra: str,
) -> VariantCall:
    """Build an insertion anchored on the final coding base ('+'-strand features only)."""
    ref_base = alignment.ref_coding_seq[anchor_idx]
    return make_coding_variant(alignment.feature, anchor_idx, ref_base, ref_base + extra)


# ─── Surviving reference mismatches & variant merging ────────────────────────


def emit_surviving_ref_mismatches(
    ref_mismatches: list[ReferenceMismatch],
    applied_overlaps: list[AppliedOverlap],
) -> list[VariantCall]:
    """Emit untouched reference mismatches at AF 1.0 and partially covered ones at residual AF."""
    surviving: list[VariantCall] = []
    for mismatch in ref_mismatches:
        covering = [
            ao for ao in applied_overlaps
            if mismatch.columns and (ao.covered_inserted if mismatch.is_insertion else ao.covered_base) & mismatch.columns
        ]
        if not covering:
            surviving.append(replace(mismatch.variant))
            continue

        residual = max(mismatch.variant.allele_freq - sum(ao.allele_freq for ao in covering), 0.0)
        if residual <= _RESIDUAL_EPS:
            continue

        covering_records = {ao.record_id for ao in covering}
        if len(covering_records) == 1:
            rec_id = next(iter(covering_records))
            links: list[tuple[int, int, float]] = []
            for ao in applied_overlaps:
                if ao.record_id != rec_id:
                    continue
                cov = ao.covered_inserted if mismatch.is_insertion else ao.covered_base
                spanned = ao.spanned_inserted if mismatch.is_insertion else ao.spanned_base
                if (spanned & mismatch.columns) and not (cov & mismatch.columns):
                    links.append((rec_id, ao.alt_id, ao.allele_freq))
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

        surviving.append(replace(
            mismatch.variant,
            allele_freq=residual,
            source=REFERENCE_DERIVED,
            depth=0,
            record_index=residual_record_idx,
            alt_index=residual_alt_idx,
            haplotype_links=residual_links,
        ))
    return surviving


def _merge_haplotype_links(
    links_a: tuple[tuple[int, int, float], ...],
    links_b: tuple[tuple[int, int, float], ...],
) -> tuple[tuple[int, int, float], ...]:
    """Merge haplotype-link entries across contributors to one aggregated variant."""
    if not links_a:
        return links_b
    if not links_b:
        return links_a
    merged: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    for rec_id, alt_id, af in (*links_a, *links_b):
        pair = (rec_id, alt_id)
        if pair not in merged:
            merged[pair] = float(af)
            order.append(pair)
        else:
            merged[pair] += float(af)
    return tuple((rec_id, alt_id, merged[(rec_id, alt_id)]) for rec_id, alt_id in order)


def merge_identical_variants(variants: list[VariantCall]) -> list[VariantCall]:
    """Sum allele frequencies per ``(chrom, pos, ref, alt)`` key within one feature match."""
    by_key: dict[tuple[str, int, str, str], VariantCall] = {}
    order: list[tuple[str, int, str, str]] = []
    for var in variants:
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


def dedupe_across_features(
    remapped: list[tuple[VariantCall, FeatureMatch]],
) -> tuple[list[VariantCall], list[str]]:
    """Collapse duplicate ``(chrom, pos, ref, alt)`` variants across overlapping features, keeping best rank."""
    best_by_key: dict[tuple[str, int, str, str], tuple[VariantCall, FeatureMatch]] = {}
    counts: dict[tuple[str, int, str, str], int] = {}
    order: list[tuple[str, int, str, str]] = []
    for var, match in remapped:
        key = (var.chrom, var.pos, var.ref, var.alt)
        counts[key] = counts.get(key, 0) + 1
        if key not in best_by_key:
            best_by_key[key] = (var, match)
            order.append(key)
        elif match_rank(match) < match_rank(best_by_key[key][1]):
            best_by_key[key] = (var, match)

    warnings: list[str] = []
    collapsed = len(remapped) - len(order)
    if collapsed:
        details = ', '.join(
            f'{key[0]}:{key[1] + 1}{key[2]}>{key[3]}'
            for key in order if counts[key] > 1
        )
        warnings.append(
            f'Collapsed {collapsed} duplicate variant(s) from overlapping feature matches: {details}'
        )
    return [best_by_key[key][0] for key in order], warnings
