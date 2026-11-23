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

    anchor_pos = _variant_native_pos(ann, alignment)
    anchor_idx = next((i for i, pos in enumerate(native_pos) if pos == anchor_pos), None)
    if anchor_idx is None:
        return ref_window, query_window, coding_positions, native_positions, native_anchor_positions

    ref_allele = ann.variant.ref.upper()
    alt_allele = ann.variant.alt.upper()
    common_len = min(len(ref_allele), len(alt_allele))
    replace_positions = set(range(anchor_pos, anchor_pos + common_len))
    replace_idxs = [
        i
        for _pos, i in sorted(
            (pos, i) for i, pos in enumerate(native_pos) if pos is not None and pos in replace_positions
        )
    ]
    for offset, idx in enumerate(replace_idxs[:common_len]):
        query_chars[idx] = alt_allele[offset]

    if len(alt_allele) > len(ref_allele):
        insert_after = replace_idxs[common_len - 1] if len(replace_idxs) >= common_len else anchor_idx
        for offset, base in enumerate(alt_allele[common_len:]):
            idx = insert_after + 1 + offset
            ref_chars.insert(idx, '-')
            query_chars.insert(idx, base)
            coding_pos.insert(idx, None)
            native_pos.insert(idx, None)
            native_anchor_pos.insert(idx, anchor_pos + common_len - 1)
    elif len(ref_allele) > len(alt_allele):
        deleted_len = len(ref_allele) - common_len
        deleted_positions = set(range(anchor_pos + common_len, anchor_pos + len(ref_allele)))
        deleted_idxs = [i for i, pos in enumerate(native_pos) if pos is not None and pos in deleted_positions]
        if len(deleted_idxs) < deleted_len:
            tail_start = (replace_idxs[common_len - 1] if len(replace_idxs) >= common_len else anchor_idx) + 1
            deleted_idxs = [i for i in range(tail_start, len(native_pos)) if native_pos[i] is not None][:deleted_len]
        for idx in deleted_idxs:
            query_chars[idx] = '-'

    return ''.join(ref_chars), ''.join(query_chars), coding_pos, native_pos, native_anchor_pos


def _window_displays_variant(
    ann: AnnotatedVariant,
    alignment: FeatureAlignment,
    ref_window: str,
    query_window: str,
    native_positions: list[int | None],
    native_anchor_positions: list[int],
    codon_nt_start: int,
) -> bool:
    """Return True when the aligned window already displays the variant event."""
    anchor_pos = _variant_native_pos(ann, alignment)
    ref_allele = ann.variant.ref.upper()
    alt_allele = ann.variant.alt.upper()

    if len(alt_allele) > len(ref_allele):
        inserted_len = len(alt_allele) - len(ref_allele)
        insertion_idxs = [
            idx
            for idx, (ref_pos, cell_anchor) in enumerate(zip(native_positions, native_anchor_positions))
            if ref_pos is None and (
                cell_anchor == anchor_pos
                or (alignment.strand == '-' and cell_anchor - 1 == anchor_pos)
            )
        ]
        return len(insertion_idxs) >= inserted_len and any(
            query_window[idx] != '-' for idx in insertion_idxs[:inserted_len]
        )

    if len(ref_allele) > len(alt_allele):
        deleted_len = len(ref_allele) - len(alt_allele)
        deleted_positions = set(range(anchor_pos + 1, anchor_pos + 1 + deleted_len))
        deleted_idxs = [i for i, pos in enumerate(native_positions) if pos is not None and pos in deleted_positions]
        return len(deleted_idxs) >= deleted_len and all(
            query_window[idx] == '-' for idx in deleted_idxs[:deleted_len]
        )

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
    is_indel_like = (
        ann.consequence in {'insertion', 'deletion', 'frameshift'}
        or len(ann.variant.ref) != len(ann.variant.alt)
    )
    is_insertion_like = len(ann.variant.alt) > len(ann.variant.ref)
    variant_anchor_pos = _variant_native_pos(ann, alignment)

    mask: list[bool] = []
    for ref_pos, cell_anchor in zip(native_positions, native_anchor_positions):
        if ref_pos is None:
            hit = cell_anchor in affected_ref_positions or (
                is_insertion_like and alignment.strand == '-' and (cell_anchor - 1) in affected_ref_positions
            )
            mask.append(hit)
        elif is_indel_like and ref_pos == variant_anchor_pos:
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
    anchor_pos = _variant_native_pos(ann, alignment)
    ref_len = len(ann.variant.ref)
    alt_len = len(ann.variant.alt)

    if alt_len > ref_len:
        return {anchor_pos}
    if ref_len > alt_len:
        return set(range(anchor_pos + 1, anchor_pos + 1 + (ref_len - alt_len)))
    if ref_len == alt_len and ref_len > 0:
        return set(range(anchor_pos, anchor_pos + ref_len))

    if len(ann.ref_codon) == 3 and len(ann.alt_codon) == 3:
        affected: set[int] = set()
        for idx, (ref_nt, alt_nt) in enumerate(zip(ann.ref_codon, ann.alt_codon)):
            if ref_nt == alt_nt:
                continue
            coding_pos = codon_nt_start + idx
            affected.add(alignment.feature_length - 1 - coding_pos if alignment.strand == '-' else coding_pos)
        return affected
    return {anchor_pos}


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
    deleted_nt = max(0, len(ann.variant.ref) - len(ann.variant.alt))
    if deleted_nt > 0:
        extra = (deleted_nt + 2) // 3
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
        ann, alignment, ref_window, query_window, native_positions, native_anchor_positions, codon_nt_start,
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
