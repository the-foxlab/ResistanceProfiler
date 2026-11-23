"""Alignment snippet rendering for report row expansion."""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup, escape

from respro.core.annotation import reverse_complement
from respro.core.coordinates import gapped_strings_from_cigar, strip_introns_from_region
from respro.db.models import AnnotatedVariant, FeatureMatch, FeatureRecord


@dataclass(frozen=True)
class FeatureAlignment:
    """Gapped feature-level alignment in native reference display orientation."""

    feature_name: str
    feature_start: int
    feature_end: int
    feature_length: int
    strand: str
    codon_start: int
    aligned_ref: str
    aligned_query: str
    aln_coding_pos: list[int | None]
    aln_coding_anchor_pos: list[int]
    aln_native_pos: list[int | None]
    aln_native_anchor_pos: list[int]
    coding_to_aln_idx: list[int]
    feature: FeatureRecord | None = None


def _build_alignment_index(aligned_ref: str) -> tuple[list[int | None], list[int], list[int]]:
    """Build per-column reference positions and gap anchors for a gapped reference string."""
    aln_ref_pos: list[int | None] = []
    aln_ref_anchor_pos: list[int] = []
    ref_to_aln_idx: list[int] = []

    ref_pos = 0
    for idx, ch in enumerate(aligned_ref):
        if ch == '-':
            aln_ref_pos.append(None)
            aln_ref_anchor_pos.append(max(0, ref_pos - 1))
            continue
        aln_ref_pos.append(ref_pos)
        aln_ref_anchor_pos.append(ref_pos)
        ref_to_aln_idx.append(idx)
        ref_pos += 1

    return aln_ref_pos, aln_ref_anchor_pos, ref_to_aln_idx


def _reverse_complement_gapped(seq: str) -> str:
    """Reverse-complement a gapped sequence while keeping ``'-'`` unchanged."""
    return seq.translate(str.maketrans('ACGTNacgtn-', 'TGCANtgcan-'))[::-1].upper()


def _build_coding_to_aln_index(
    aln_coding_pos: list[int | None],
    coding_len: int,
) -> list[int]:
    """Map each coding-sequence nucleotide position to its column index in the display string."""
    mapping = [-1] * coding_len
    for idx, coding_pos in enumerate(aln_coding_pos):
        if coding_pos is not None and 0 <= coding_pos < coding_len:
            mapping[coding_pos] = idx
    return mapping


def build_feature_alignments(
    query_sequence: str,
    feature_matches: list[FeatureMatch],
) -> dict[str, FeatureAlignment]:
    """Build gapped feature-level alignments for all matched features."""
    query_upper = (query_sequence or '').upper()
    if not query_upper:
        return {}

    alignments: dict[str, FeatureAlignment] = {}
    best_identity: dict[str, float] = {}
    for match in feature_matches:
        feature = match.feature
        if not feature.nt_sequence:
            continue

        region = query_upper[match.query_start:match.query_end]
        if match.strand == '-':
            region = reverse_complement(region)
        # Drop intron intervals from unspliced query regions so exon-only CIGARs stay in register.
        region = strip_introns_from_region(region, match.intron_intervals)

        aligned_ref_coding, aligned_query_coding = gapped_strings_from_cigar(
            feature.nt_sequence.upper(), region, match.cigar, match.cds_start,
        )
        coding_ref_pos, coding_anchor_pos, _ = _build_alignment_index(aligned_ref_coding)

        feature_len = len(feature.nt_sequence)
        if feature.strand == '-':
            aligned_ref = _reverse_complement_gapped(aligned_ref_coding)
            aligned_query = _reverse_complement_gapped(aligned_query_coding)
            aln_coding_pos = list(reversed(coding_ref_pos))
            aln_coding_anchor_pos = list(reversed(coding_anchor_pos))
            aln_native_pos = [(feature_len - 1 - p) if p is not None else None for p in aln_coding_pos]
            aln_native_anchor_pos = [feature_len - 1 - p for p in aln_coding_anchor_pos]
        else:
            aligned_ref = aligned_ref_coding
            aligned_query = aligned_query_coding
            aln_coding_pos = coding_ref_pos
            aln_coding_anchor_pos = coding_anchor_pos
            aln_native_pos = list(aln_coding_pos)
            aln_native_anchor_pos = list(aln_coding_anchor_pos)

        entry = FeatureAlignment(
            feature_name=feature.name,
            feature_start=feature.start,
            feature_end=feature.end,
            feature_length=feature_len,
            strand=feature.strand,
            codon_start=feature.codon_start,
            aligned_ref=aligned_ref,
            aligned_query=aligned_query,
            aln_coding_pos=aln_coding_pos,
            aln_coding_anchor_pos=aln_coding_anchor_pos,
            aln_native_pos=aln_native_pos,
            aln_native_anchor_pos=aln_native_anchor_pos,
            coding_to_aln_idx=_build_coding_to_aln_index(aln_coding_pos, feature_len),
            feature=feature,
        )
        if feature.name not in best_identity or match.identity > best_identity[feature.name]:
            alignments[feature.name] = entry
            best_identity[feature.name] = match.identity

    return alignments


def _render_spaced_line(
    seq: str,
    affected_mask: list[bool],
    coding_positions: list[int | None],
    codon_start: int,
    strand: str,
) -> str:
    """Render one alignment line with fixed-width cells and codon boundary separators."""
    parts: list[str] = []
    for idx, ch in enumerate(seq):
        coding_pos = coding_positions[idx]
        if idx > 0 and coding_pos is not None and coding_pos >= codon_start:
            # Minus-strand display runs left-to-right on the native strand, so codons run right-to-left.
            boundary_mod = 0 if strand == '+' else 2
            if (coding_pos - codon_start) % 3 == boundary_mod:
                parts.append("<span class='aln-sep'></span>")

        css = 'aln-cell aln-affected' if affected_mask[idx] else 'aln-cell'
        parts.append(f"<span class='{css}'>{escape(ch)}</span>")
    return ''.join(parts)


def _build_match_line(
    ref_seq: str,
    query_seq: str,
    coding_positions: list[int | None],
    codon_start: int,
    strand: str,
) -> str:
    """Render a ``|`` guide line for exact non-gap matches in the displayed snippet."""
    match_chars = [
        '|' if ref_base == query_base and ref_base != '-' else ' '
        for ref_base, query_base in zip(ref_seq, query_seq)
    ]
    rendered = _render_spaced_line(
        ''.join(match_chars), [False] * len(match_chars), coding_positions, codon_start, strand,
    )
    return rendered.replace("<span class='aln-cell'>|</span>", "<span class='aln-cell aln-match-cell'>|</span>")


def _variant_coding_pos(ann: AnnotatedVariant, alignment: FeatureAlignment) -> int:
    """Return 0-based CDS nucleotide offset (before subtracting ``codon_start``) for the anchor."""
    if alignment.feature is not None and len(alignment.feature.segments) > 1:
        cds_pos = alignment.feature.genomic_to_cds_position(ann.variant.pos)
        if cds_pos is not None:
            return cds_pos
    if alignment.strand == '+':
        return ann.variant.pos - alignment.feature_start
    return (alignment.feature_end - 1) - ann.variant.pos


def _variant_native_pos(ann: AnnotatedVariant, alignment: FeatureAlignment) -> int:
    """Return native 5'-to-3' spliced-CDS display offset for the variant anchor."""
    if alignment.feature is not None and len(alignment.feature.segments) > 1:
        cds_pos = alignment.feature.genomic_to_cds_position(ann.variant.pos)
        if cds_pos is not None:
            return (alignment.feature_length - 1 - cds_pos) if alignment.strand == '-' else cds_pos
    return ann.variant.pos - alignment.feature_start


@dataclass(frozen=True)
class _VariantSpan:
    """Native-direction allele span shared across overlay, detection, and highlight helpers."""

    anchor_pos: int
    ref_allele: str
    alt_allele: str
    common_len: int
    ins_anchor: int
    changed_common: frozenset[int]

    def matches_insertion_cell(self, cell_anchor: int, strand: str) -> bool:
        """Return True when an insertion gap column is anchored within this variant's common span."""
        return (
            self.anchor_pos <= cell_anchor <= self.ins_anchor
            or (strand == '-' and self.anchor_pos <= cell_anchor - 1 <= self.ins_anchor)
        )


def _variant_span(ann: AnnotatedVariant, alignment: FeatureAlignment) -> _VariantSpan:
    anchor_pos = _variant_native_pos(ann, alignment)
    ref_allele = ann.variant.ref.upper()
    alt_allele = ann.variant.alt.upper()
    common_len = min(len(ref_allele), len(alt_allele))
    ins_anchor = anchor_pos + common_len - 1 if common_len > 0 else anchor_pos
    changed_common = frozenset(
        anchor_pos + offset
        for offset in range(common_len)
        if ref_allele[offset] != alt_allele[offset]
    )
    return _VariantSpan(
        anchor_pos=anchor_pos,
        ref_allele=ref_allele,
        alt_allele=alt_allele,
        common_len=common_len,
        ins_anchor=ins_anchor,
        changed_common=changed_common,
    )


def _contiguous_insertion_indices(
    native_positions: list[int | None],
    insert_after: int,
) -> list[int]:
    """Return contiguous insertion-column indices (``None`` in *native_positions*) after *insert_after*."""
    indices: list[int] = []
    idx = insert_after + 1
    while idx < len(native_positions) and native_positions[idx] is None:
        indices.append(idx)
        idx += 1
    return indices


def _apply_vcf_overlay(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    ref_window: str,
    query_window: str,
    coding_positions: list[int | None],
    native_positions: list[int | None],
    native_anchor_positions: list[int],
) -> tuple[str, str, list[int | None], list[int | None], list[int]]:
    """Overlay a VCF variant onto the displayed window when not already reflected in the query."""
    ref_chars = list(ref_window)
    query_chars = list(query_window)
    coding_pos = list(coding_positions)
    native_pos = list(native_positions)
    native_anchor_pos = list(native_anchor_positions)

    span = _variant_span(ann, alignment)
    anchor_idx = next((i for i, pos in enumerate(native_pos) if pos == span.anchor_pos), None)
    if anchor_idx is None:
        return ref_window, query_window, coding_positions, native_positions, native_anchor_positions

    ins_anchor_idx: int | None = None
    for i, pos in enumerate(native_pos):
        if pos is not None and span.anchor_pos <= pos < span.anchor_pos + span.common_len:
            query_chars[i] = span.alt_allele[pos - span.anchor_pos]
            if pos == span.ins_anchor:
                ins_anchor_idx = i

    if len(span.alt_allele) > len(span.ref_allele):
        if ins_anchor_idx is not None:
            ins_tail = _contiguous_insertion_indices(native_pos, ins_anchor_idx)
            span_end = (ins_tail[-1] if ins_tail else ins_anchor_idx) + 1
            for idx in range(span_end - 1, anchor_idx, -1):
                if native_pos[idx] is None:
                    del ref_chars[idx]
                    del query_chars[idx]
                    del coding_pos[idx]
                    del native_pos[idx]
                    del native_anchor_pos[idx]
                    if idx < ins_anchor_idx:
                        ins_anchor_idx -= 1
            for offset, base in enumerate(span.alt_allele[span.common_len:]):
                idx = ins_anchor_idx + 1 + offset
                ref_chars.insert(idx, '-')
                query_chars.insert(idx, base)
                coding_pos.insert(idx, None)
                native_pos.insert(idx, None)
                native_anchor_pos.insert(idx, span.ins_anchor)
    elif len(span.ref_allele) > len(span.alt_allele):
        deleted_positions = set(range(span.anchor_pos + span.common_len, span.anchor_pos + len(span.ref_allele)))
        deleted_idxs = [idx for idx, pos in enumerate(native_pos) if pos is not None and pos in deleted_positions]
        for idx in deleted_idxs:
            query_chars[idx] = '-'
        # Restore flanking gap cells from a longer supplied-ref deletion overlapping this call.
        left = anchor_idx - 1
        while left >= 0 and native_pos[left] is not None and query_chars[left] == '-':
            query_chars[left] = ref_chars[left]
            left -= 1
        if deleted_idxs:
            right = deleted_idxs[-1] + 1
            while right < len(native_pos) and native_pos[right] is not None and query_chars[right] == '-':
                query_chars[right] = ref_chars[right]
                right += 1
            tail_start = (ins_anchor_idx if ins_anchor_idx is not None else anchor_idx) + 1
            # Remove any supplied-ref insertion columns embedded inside the deleted span.
            for idx in range(deleted_idxs[-1] - 1, tail_start - 1, -1):
                if native_pos[idx] is None:
                    del ref_chars[idx]
                    del query_chars[idx]
                    del coding_pos[idx]
                    del native_pos[idx]
                    del native_anchor_pos[idx]

    return ''.join(ref_chars), ''.join(query_chars), coding_pos, native_pos, native_anchor_pos


def _window_displays_variant(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    ref_window: str,
    query_window: str,
    native_positions: list[int | None],
    codon_nt_start: int,
) -> bool:
    """Return True when the aligned window already displays the variant event."""
    if ann.is_fasta_mode:
        return True

    span = _variant_span(ann, alignment)
    if span.common_len > 0:
        pos_to_idx = {pos: i for i, pos in enumerate(native_positions) if pos is not None}

        def _prefix_matches() -> bool:
            return all(
                (idx := pos_to_idx.get(span.anchor_pos + offset)) is not None
                and query_window[idx].upper() == span.alt_allele[offset]
                for offset in range(span.common_len)
            )

        if len(span.alt_allele) > len(span.ref_allele):
            start_idx = pos_to_idx.get(span.anchor_pos)
            end_idx = pos_to_idx.get(span.ins_anchor)
            if start_idx is None or end_idx is None:
                return False
            ins_tail = _contiguous_insertion_indices(native_positions, end_idx)
            span_end = (ins_tail[-1] if ins_tail else end_idx) + 1
            return query_window[start_idx:span_end].upper() == span.alt_allele

        if len(span.ref_allele) > len(span.alt_allele):
            if not _prefix_matches():
                return False
            deleted_len = len(span.ref_allele) - span.common_len
            deleted_idxs = [
                idx
                for pos in range(span.anchor_pos + span.common_len, span.anchor_pos + len(span.ref_allele))
                if (idx := pos_to_idx.get(pos)) is not None
            ]
            if len(deleted_idxs) < deleted_len or not all(query_window[idx] == '-' for idx in deleted_idxs):
                return False
            tail_start = pos_to_idx[span.ins_anchor] + 1
            if any(native_positions[i] is None for i in range(tail_start, deleted_idxs[-1])):
                return False
            next_idx = deleted_idxs[-1] + 1
            if (
                next_idx < len(native_positions)
                and native_positions[next_idx] is not None
                and query_window[next_idx] == '-'
            ):
                return False
            return True

        return _prefix_matches()

    affected_positions = _affected_nt_positions(ann, alignment, codon_nt_start)
    visible_affected = [i for i, pos in enumerate(native_positions) if pos is not None and pos in affected_positions]
    return bool(visible_affected) and any(query_window[i] != ref_window[i] for i in visible_affected)


def _build_affected_mask(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    native_positions: list[int | None],
    native_anchor_positions: list[int],
    codon_nt_start: int,
) -> list[bool]:
    """Return a per-column boolean mask marking cells directly affected by *ann*."""
    affected_ref_positions = _affected_nt_positions(ann, alignment, codon_nt_start)
    span = _variant_span(ann, alignment)
    is_indel_like = (
        ann.consequence in {'insertion', 'deletion', 'frameshift'}
        or len(span.ref_allele) != len(span.alt_allele)
    )
    is_insertion_like = len(span.alt_allele) > len(span.ref_allele)

    mask: list[bool] = []
    for ref_pos, cell_anchor in zip(native_positions, native_anchor_positions):
        if ref_pos is None:
            if span.ref_allele or span.alt_allele:
                hit = is_insertion_like and span.matches_insertion_cell(cell_anchor, alignment.strand)
            else:
                hit = cell_anchor in affected_ref_positions
            mask.append(hit)
        elif is_indel_like and ref_pos == span.ins_anchor and ref_pos not in span.changed_common:
            mask.append(False)
        else:
            mask.append(ref_pos in affected_ref_positions)
    return mask


def _affected_nt_positions(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    codon_nt_start: int,
) -> set[int]:
    """Return native-direction positions to highlight as directly affected by *ann*."""
    span = _variant_span(ann, alignment)
    ref_len = len(span.ref_allele)
    alt_len = len(span.alt_allele)

    if alt_len > ref_len:
        return set(span.changed_common) | {span.ins_anchor}
    if ref_len > alt_len:
        return set(span.changed_common) | set(range(span.anchor_pos + span.common_len, span.anchor_pos + ref_len))
    if ref_len == alt_len and ref_len > 0:
        return set(range(span.anchor_pos, span.anchor_pos + ref_len))

    if len(ann.ref_codon) == 3 and len(ann.alt_codon) == 3:
        affected: set[int] = set()
        for idx, (ref_nt, alt_nt) in enumerate(zip(ann.ref_codon, ann.alt_codon)):
            if ref_nt == alt_nt:
                continue
            coding_pos = codon_nt_start + idx
            affected.add(alignment.feature_length - 1 - coding_pos if alignment.strand == '-' else coding_pos)
        return affected
    return {span.anchor_pos}


def build_alignment_html(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    context_codons: int = 2,
) -> Markup | None:
    """Build an HTML alignment snippet centered on *ann* with *context_codons* on each side."""
    if ann.codon_pos < 0:
        return None
    total_coding_codons = (alignment.feature_length - alignment.codon_start) // 3
    if total_coding_codons <= 0:
        return None

    center_codon = ann.codon_pos
    anchor_coding_pos = _variant_coding_pos(ann, alignment)
    if anchor_coding_pos >= alignment.codon_start:
        anchor_codon = (anchor_coding_pos - alignment.codon_start) // 3
        if 0 <= anchor_codon < total_coding_codons:
            center_codon = anchor_codon

    if not (0 <= center_codon < total_coding_codons):
        return None

    codon_nt_start = alignment.codon_start + center_codon * 3
    if codon_nt_start + 3 > alignment.feature_length:
        return None

    left_context = context_codons
    right_context = context_codons
    span_nt = max(0, len(ann.variant.ref) - 1)
    if span_nt > 0:
        extra = (span_nt + 2) // 3
        if alignment.strand == '-':
            left_context += extra
        else:
            right_context += extra

    window_nt_start = alignment.codon_start + max(0, center_codon - left_context) * 3
    window_nt_end = alignment.codon_start + min(total_coding_codons, center_codon + right_context + 1) * 3
    if window_nt_start >= window_nt_end:
        return None

    visible_indices = [
        alignment.coding_to_aln_idx[pos]
        for pos in range(window_nt_start, window_nt_end)
        if 0 <= pos < alignment.feature_length and alignment.coding_to_aln_idx[pos] >= 0
    ]
    if not visible_indices:
        return None

    aln_start, aln_end = min(visible_indices), max(visible_indices) + 1
    ref_window = alignment.aligned_ref[aln_start:aln_end]
    query_window = alignment.aligned_query[aln_start:aln_end]
    coding_positions = alignment.aln_coding_pos[aln_start:aln_end]
    native_positions = alignment.aln_native_pos[aln_start:aln_end]
    native_anchor_positions = alignment.aln_native_anchor_pos[aln_start:aln_end]

    if not _window_displays_variant(
        ann, alignment, ref_window, query_window, native_positions, codon_nt_start,
    ):
        ref_window, query_window, coding_positions, native_positions, native_anchor_positions = _apply_vcf_overlay(
            ann, alignment, ref_window, query_window, coding_positions, native_positions, native_anchor_positions,
        )

    affected_mask = _build_affected_mask(
        ann, alignment, native_positions, native_anchor_positions, codon_nt_start,
    )
    ref_fmt = _render_spaced_line(
        ref_window, [False] * len(ref_window), coding_positions, alignment.codon_start, alignment.strand,
    )
    match_fmt = _build_match_line(
        ref_window, query_window, coding_positions, alignment.codon_start, alignment.strand,
    )
    query_fmt = _render_spaced_line(
        query_window, affected_mask, coding_positions, alignment.codon_start, alignment.strand,
    )

    orientation_note = (
        'Coding orientation: minus strand' if alignment.strand == '-' else 'Coding orientation: plus strand'
    )
    query_label = 'FASTA' if ann.is_fasta_mode else 'Query'

    return Markup(
        "<div class='aln-block'>"
        f"<div class='aln-meta'>{escape(orientation_note)}</div>"
        f"<div class='aln-line'><span class='aln-label'>Ref</span><span class='aln-seq'>{ref_fmt}</span></div>"
        f"<div class='aln-line aln-line-match'><span class='aln-label'></span><span class='aln-seq'>{match_fmt}</span></div>"
        f"<div class='aln-line'><span class='aln-label'>{escape(query_label)}</span><span class='aln-seq'>{query_fmt}</span></div>"
        "</div>"
    )
