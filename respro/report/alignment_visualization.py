"""Alignment snippet rendering for report row expansion."""

from __future__ import annotations

import re
from dataclasses import dataclass

from markupsafe import Markup, escape

from respro.db.models import AnnotatedVariant, GeneMatch


@dataclass(frozen=True)
class GeneAlignment:
    """Real gapped gene-level alignment in coding orientation."""

    gene_name: str
    gene_start: int
    gene_end: int
    gene_length: int
    strand: str
    codon_start: int
    aligned_ref: str
    aligned_query: str
    aln_coding_pos: list[int | None]
    aln_coding_anchor_pos: list[int]
    aln_native_pos: list[int | None]
    aln_native_anchor_pos: list[int]
    coding_to_aln_idx: list[int]


def _reverse_complement(seq: str) -> str:
    table = str.maketrans('ACGTNacgtn', 'TGCANtgcan')
    return seq.translate(table)[::-1].upper()


def _gapped_strings_from_cigar(
    cds: str,
    region: str,
    cigar: str,
    cds_start: int,
) -> tuple[str, str]:
    """Rebuild gapped reference/query strings from CIGAR."""
    aligned_ref: list[str] = []
    aligned_query: list[str] = []

    if cds_start > 0:
        aligned_ref.append(cds[:cds_start])
        aligned_query.append('-' * cds_start)

    ref_pos = cds_start
    query_pos = 0
    for n_str, op in re.findall(r'(\d+)([MID])', cigar):
        n = int(n_str)
        if op == 'M':
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append(region[query_pos:query_pos + n])
            ref_pos += n
            query_pos += n
        elif op == 'I':
            aligned_ref.append('-' * n)
            aligned_query.append(region[query_pos:query_pos + n])
            query_pos += n
        elif op == 'D':
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append('-' * n)
            ref_pos += n

    if ref_pos < len(cds):
        aligned_ref.append(cds[ref_pos:])
        aligned_query.append('-' * (len(cds) - ref_pos))

    return ''.join(aligned_ref), ''.join(aligned_query)


def _build_alignment_index(aligned_ref: str) -> tuple[list[int | None], list[int], list[int]]:
    """Build helper indices for codon-aware slicing/highlighting on gapped strings."""
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
    """Reverse-complement a gapped sequence while keeping '-' unchanged."""
    table = str.maketrans('ACGTNacgtn-', 'TGCANtgcan-')
    return seq.translate(table)[::-1].upper()


def _build_coding_to_aln_index(
    aln_coding_pos: list[int | None],
    coding_len: int,
) -> list[int]:
    """Build coding-position to alignment-index lookup for displayed alignment."""
    mapping = [-1] * coding_len
    for idx, coding_pos in enumerate(aln_coding_pos):
        if coding_pos is not None and 0 <= coding_pos < coding_len:
            mapping[coding_pos] = idx
    return mapping


def build_gene_alignments(
    query_sequence: str,
    gene_matches: list[GeneMatch],
) -> dict[str, GeneAlignment]:
    """Build real coding-orientation gapped alignments for all matched genes."""
    query_upper = (query_sequence or '').upper()
    if not query_upper:
        return {}

    alignments: dict[str, GeneAlignment] = {}
    best_identity: dict[str, float] = {}
    for match in gene_matches:
        gene = match.gene
        if not gene.nt_sequence:
            continue

        region = query_upper[match.query_start:match.query_end]
        if match.strand == '-':
            region = _reverse_complement(region)

        aligned_ref_coding, aligned_query_coding = _gapped_strings_from_cigar(
            gene.nt_sequence.upper(), region, match.cigar, match.cds_start,
        )

        coding_ref_pos, coding_anchor_pos, _ = _build_alignment_index(aligned_ref_coding)

        if gene.strand == '-':
            aligned_ref = _reverse_complement_gapped(aligned_ref_coding)
            aligned_query = _reverse_complement_gapped(aligned_query_coding)
            aln_coding_pos = list(reversed(coding_ref_pos))
            aln_coding_anchor_pos = list(reversed(coding_anchor_pos))
        else:
            aligned_ref = aligned_ref_coding
            aligned_query = aligned_query_coding
            aln_coding_pos = coding_ref_pos
            aln_coding_anchor_pos = coding_anchor_pos

        gene_len = len(gene.nt_sequence)

        if gene.strand == '-':
            aln_native_pos = [
                (gene_len - 1 - p) if p is not None else None
                for p in aln_coding_pos
            ]
            aln_native_anchor_pos = [gene_len - 1 - p for p in aln_coding_anchor_pos]
        else:
            aln_native_pos = list(aln_coding_pos)
            aln_native_anchor_pos = list(aln_coding_anchor_pos)

        coding_to_aln_idx = _build_coding_to_aln_index(aln_coding_pos, gene_len)

        entry = GeneAlignment(
            gene_name=gene.name,
            gene_start=gene.start,
            gene_end=gene.end,
            gene_length=gene_len,
            strand=gene.strand,
            codon_start=gene.codon_start,
            aligned_ref=aligned_ref,
            aligned_query=aligned_query,
            aln_coding_pos=aln_coding_pos,
            aln_coding_anchor_pos=aln_coding_anchor_pos,
            aln_native_pos=aln_native_pos,
            aln_native_anchor_pos=aln_native_anchor_pos,
            coding_to_aln_idx=coding_to_aln_idx,
        )
        if gene.name not in best_identity or match.identity > best_identity[gene.name]:
            alignments[gene.name] = entry
            best_identity[gene.name] = match.identity

    return alignments


def _render_spaced_line(
    seq: str,
    affected_mask: list[bool],
    coding_positions: list[int | None],
    codon_start: int,
    strand: str,
) -> str:
    """Render one alignment line with fixed-width cells and codon separators."""
    parts: list[str] = []
    for idx, ch in enumerate(seq):
        coding_pos = coding_positions[idx]
        if idx > 0 and coding_pos is not None and coding_pos >= codon_start:
            if strand == '+':
                is_codon_boundary = (coding_pos - codon_start) % 3 == 0
            else:
                # Display is left-to-right native reference for reverse genes, but codons
                # are defined in coding direction (right-to-left here).
                is_codon_boundary = (coding_pos - codon_start) % 3 == 2
            if is_codon_boundary:
                parts.append("<span class='aln-sep'></span>")

        if affected_mask[idx]:
            parts.append(f"<span class='aln-cell aln-affected'>{escape(ch)}</span>")
        else:
            parts.append(f"<span class='aln-cell'>{escape(ch)}</span>")
    return ''.join(parts)


def _build_match_line(
    ref_seq: str,
    query_seq: str,
    coding_positions: list[int | None],
    codon_start: int,
    strand: str,
) -> str:
    """Render a `|` guide line for exact non-gap matches in the displayed snippet."""
    match_chars = [
        '|' if ref_base == query_base and ref_base != '-' else ' '
        for ref_base, query_base in zip(ref_seq, query_seq)
    ]
    rendered = _render_spaced_line(
        ''.join(match_chars),
        [False] * len(match_chars),
        coding_positions,
        codon_start,
        strand,
    )
    return rendered.replace("<span class='aln-cell'>|</span>", "<span class='aln-cell aln-match-cell'>|</span>")


def _variant_coding_pos(ann: AnnotatedVariant, alignment: GeneAlignment) -> int:
    """Return coding-sequence nucleotide position for the variant anchor."""
    if alignment.strand == '+':
        nt_offset = ann.variant.pos - alignment.gene_start
    else:
        nt_offset = (alignment.gene_end - 1) - ann.variant.pos
    return nt_offset - alignment.codon_start


def _variant_native_pos(ann: AnnotatedVariant, alignment: GeneAlignment) -> int:
    """Return native 5'-3' reference-gene nucleotide offset for variant anchor."""
    return ann.variant.pos - alignment.gene_start


def _apply_vcf_overlay(
    ann: AnnotatedVariant,
    alignment: GeneAlignment,
    ref_window: str,
    query_window: str,
    coding_positions: list[int | None],
    native_positions: list[int | None],
    native_anchor_positions: list[int],
) -> tuple[str, str, list[int | None], list[bool]]:
    """Apply one VCF event to the displayed window and return affected-cell mask."""
    ref_chars = list(ref_window)
    query_chars = list(query_window)
    coding_pos = list(coding_positions)
    native_pos = list(native_positions)
    native_anchor_pos = list(native_anchor_positions)
    affected = [False] * len(ref_chars)

    anchor_pos = _variant_native_pos(ann, alignment)
    anchor_idx = next((i for i, pos in enumerate(native_pos) if pos == anchor_pos), None)
    if anchor_idx is None:
        return ref_window, query_window, coding_positions, affected

    ref_allele = ann.variant.ref.upper()
    alt_allele = ann.variant.alt.upper()
    if len(ref_allele) == 1 and len(alt_allele) == 1:
        affected[anchor_idx] = True
        query_chars[anchor_idx] = alt_allele
        return ''.join(ref_chars), ''.join(query_chars), coding_pos, affected

    if len(alt_allele) > len(ref_allele):
        inserted = alt_allele[1:]
        insert_at = anchor_idx + 1
        for offset, base in enumerate(inserted):
            idx = insert_at + offset
            ref_chars.insert(idx, '-')
            query_chars.insert(idx, base)
            coding_pos.insert(idx, None)
            native_pos.insert(idx, None)
            native_anchor_pos.insert(idx, anchor_pos)
            affected.insert(idx, True)
        return ''.join(ref_chars), ''.join(query_chars), coding_pos, affected

    if len(ref_allele) > len(alt_allele):
        deleted_len = len(ref_allele) - len(alt_allele)
        deleted_positions = set(range(anchor_pos + 1, anchor_pos + 1 + deleted_len))
        deleted_idxs = [
            idx for idx, pos in enumerate(native_pos)
            if pos is not None and pos in deleted_positions
        ]
        if len(deleted_idxs) < deleted_len:
            deleted_idxs = []
            for idx in range(anchor_idx + 1, len(native_pos)):
                if native_pos[idx] is None:
                    continue
                deleted_idxs.append(idx)
                if len(deleted_idxs) == deleted_len:
                    break
        for idx in deleted_idxs:
            query_chars[idx] = '-'
            affected[idx] = True
        return ''.join(ref_chars), ''.join(query_chars), coding_pos, affected

    # Equal-length block replacement (e.g. MNV): replace aligned positions from anchor onward.
    block_len = len(ref_allele)
    replace_positions = set(range(anchor_pos, anchor_pos + block_len))
    replace_idxs = [
        idx for idx, pos in enumerate(native_pos)
        if pos is not None and pos in replace_positions
    ]
    replace_idxs.sort(key=lambda idx: native_pos[idx] if native_pos[idx] is not None else -1)
    for offset, idx in enumerate(replace_idxs[:len(alt_allele)]):
        query_chars[idx] = alt_allele[offset]
        affected[idx] = True

    return ''.join(ref_chars), ''.join(query_chars), coding_pos, affected


def _affected_nt_positions(
    ann: AnnotatedVariant,
    alignment: GeneAlignment,
    codon_nt_start: int,
) -> set[int]:
    """Return native-direction positions that should be highlighted as directly affected."""
    indel_like = {'insertion', 'deletion', 'frameshift', 'inframe_complex'}
    if ann.consequence in indel_like or len(ann.variant.ref) != len(ann.variant.alt):
        anchor_pos = _variant_native_pos(ann, alignment)
        ref_len = len(ann.variant.ref)
        alt_len = len(ann.variant.alt)

        # Insertions are represented between reference bases; highlight inserted gap cells
        # (which are anchored to the previous native position), but do not highlight anchor.
        if alt_len > ref_len:
            return {anchor_pos}

        # Deletions affect reference positions after the anchor in VCF anchor convention.
        if ref_len > alt_len:
            deleted_len = ref_len - alt_len
            return set(range(anchor_pos + 1, anchor_pos + 1 + deleted_len))

        # Equal-length complex replacements: highlight replaced block including anchor.
        block_len = max(ref_len, alt_len)
        return set(range(anchor_pos, anchor_pos + block_len))

    if len(ann.ref_codon) == 3 and len(ann.alt_codon) == 3:
        affected: set[int] = set()
        for idx, (ref_nt, alt_nt) in enumerate(zip(ann.ref_codon, ann.alt_codon)):
            if ref_nt == alt_nt:
                continue
            coding_pos = codon_nt_start + idx
            if alignment.strand == '-':
                affected.add(alignment.gene_length - 1 - coding_pos)
            else:
                affected.add(coding_pos)
        return affected

    return {_variant_native_pos(ann, alignment)}


def build_alignment_html(
    ann: AnnotatedVariant,
    alignment: GeneAlignment,
    context_codons: int = 2,
) -> Markup | None:
    """
    Build a short coding-direction alignment block around one mutation.

    :param ann: annotated variant row source
    :param alignment: real gapped alignment for the annotated gene
    :param context_codons: number of codons on each side of the mutation codon
    :return: Markup-safe HTML block or None when unavailable
    """
    if ann.codon_pos < 0:
        return None

    total_coding_codons = (alignment.gene_length - alignment.codon_start) // 3
    if total_coding_codons <= 0:
        return None

    center_codon = ann.codon_pos
    anchor_coding_pos = _variant_coding_pos(ann, alignment)
    if anchor_coding_pos >= alignment.codon_start:
        anchor_codon = (anchor_coding_pos - alignment.codon_start) // 3
        if 0 <= anchor_codon < total_coding_codons:
            center_codon = anchor_codon

    if center_codon < 0 or center_codon >= total_coding_codons:
        return None

    codon_nt_start = alignment.codon_start + center_codon * 3
    codon_nt_end = codon_nt_start + 3
    if codon_nt_end > alignment.gene_length:
        return None

    left_context_codons = context_codons
    right_context_codons = context_codons
    deleted_nt = _deleted_nt_length(ann)
    if deleted_nt > 0:
        # Ensure the pseudo-alignment window can show the full deleted block.
        extra_context_codons = (deleted_nt + 2) // 3
        if alignment.strand == '-':
            left_context_codons += extra_context_codons
        else:
            right_context_codons += extra_context_codons

    codon_window_start = max(0, center_codon - left_context_codons)
    codon_window_end = min(total_coding_codons, center_codon + right_context_codons + 1)
    window_nt_start = alignment.codon_start + codon_window_start * 3
    window_nt_end = alignment.codon_start + codon_window_end * 3
    if window_nt_start >= window_nt_end:
        return None

    visible_indices = [
        alignment.coding_to_aln_idx[coding_pos]
        for coding_pos in range(window_nt_start, window_nt_end)
        if 0 <= coding_pos < alignment.gene_length and alignment.coding_to_aln_idx[coding_pos] >= 0
    ]
    if not visible_indices:
        return None

    aln_start = min(visible_indices)
    aln_end = max(visible_indices) + 1

    ref_window = alignment.aligned_ref[aln_start:aln_end]
    query_window = alignment.aligned_query[aln_start:aln_end]
    coding_positions = alignment.aln_coding_pos[aln_start:aln_end]
    native_positions = alignment.aln_native_pos[aln_start:aln_end]
    native_anchor_positions = alignment.aln_native_anchor_pos[aln_start:aln_end]

    if ann.is_fasta_mode:
        affected_ref_positions = _affected_nt_positions(ann, alignment, codon_nt_start)
        indel_like = {'insertion', 'deletion', 'frameshift', 'inframe_complex'}
        is_indel_like = ann.consequence in indel_like or len(ann.variant.ref) != len(ann.variant.alt)
        variant_anchor_pos = _variant_native_pos(ann, alignment)
        affected_mask: list[bool] = []
        for ref_pos, cell_anchor_pos in zip(native_positions, native_anchor_positions):
            if ref_pos is None:
                affected_mask.append(cell_anchor_pos in affected_ref_positions)
            else:
                if is_indel_like and ref_pos == variant_anchor_pos:
                    affected_mask.append(False)
                else:
                    affected_mask.append(ref_pos in affected_ref_positions)
    else:
        ref_window, query_window, coding_positions, affected_mask = _apply_vcf_overlay(
            ann,
            alignment,
            ref_window,
            query_window,
            coding_positions,
            native_positions,
            native_anchor_positions,
        )

    ref_fmt = _render_spaced_line(
        ref_window,
        [False] * len(ref_window),
        coding_positions,
        alignment.codon_start,
        alignment.strand,
    )
    match_fmt = _build_match_line(
        ref_window,
        query_window,
        coding_positions,
        alignment.codon_start,
        alignment.strand,
    )
    query_fmt = _render_spaced_line(
        query_window, affected_mask, coding_positions, alignment.codon_start, alignment.strand,
    )

    orientation_note = (
        'Coding orientation: minus strand'
        if alignment.strand == '-' else 'Coding orientation: plus strand'
    )
    query_label = 'FASTA' if ann.is_fasta_mode else 'Query'

    return Markup(
        "<div class='aln-block'>"
        f"<div class='aln-meta'>{escape(orientation_note)}</div>"
        "<div class='aln-line'><span class='aln-label'>Ref</span><span class='aln-seq'>"
        f"{ref_fmt}</span></div>"
        f"<div class='aln-line aln-line-match'><span class='aln-label'></span><span class='aln-seq'>"
        f"{match_fmt}</span></div>"
        f"<div class='aln-line'><span class='aln-label'>{escape(query_label)}</span><span class='aln-seq'>"
        f"{query_fmt}</span></div>"
        "</div>"
    )


def _deleted_nt_length(ann: AnnotatedVariant) -> int:
    """Return deleted nucleotide count for one event, or zero for non-deletions."""
    return max(0, len(ann.variant.ref) - len(ann.variant.alt))
