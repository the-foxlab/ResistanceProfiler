"""
FASTA consensus profiling — direct amino acid comparison from aligned query sequence.

The query FASTA is aligned to each matched gene CDS using global PairwiseAligner.
The aligned region is translated in the reference reading frame, and amino acid
differences are extracted directly — no VCF required for this path.

All mutation types are supported:
- SNPs (including synonymous, missense, stop-gained, start-lost)
- In-frame insertions and deletions
- Frameshifts (non-3n insertions or deletions)

Ambiguous IUPAC bases are expanded to all possible codons. Each unique
non-reference amino acid is emitted as a separate AnnotatedVariant with
allele_freq = 1 / number_of_possible_amino_acids.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from itertools import product as itertools_product

from Bio.Seq import Seq

from respro.core.annotation import reverse_complement, translate_codon
from respro.db.models import AnnotatedVariant, CoverageGap, GeneMatch, GeneRecord, VariantCall

logger = logging.getLogger(__name__)


def profile_fasta_consensus(
    query_seq: str,
    matches: list[GeneMatch],
) -> tuple[list[AnnotatedVariant], list[CoverageGap]]:
    """
    Profile a FASTA consensus sequence against internal gene references.

    For each matched gene the query region is globally aligned to the internal CDS,
    translated in the reference reading frame, and compared amino acid by amino acid.
    SNPs, in-frame insertions/deletions, and frameshifts are all detected.

    Ambiguous IUPAC bases are expanded to all possible codons. Each unique
    non-reference amino acid is emitted as a separate AnnotatedVariant with
    allele_freq = 1 / number_of_distinct_possible_amino_acids.

    Codons where all three query positions are 'N' are treated as non-covered and
    returned as CoverageGap entries rather than being IUPAC-expanded.

    :param query_seq: full query FASTA sequence (forward strand, upper case)
    :param matches: gene matches from sequence alignment
    :return: (annotated variants, coverage gaps)
    """
    annotations: list[AnnotatedVariant] = []
    coverage_gaps: list[CoverageGap] = []
    for match in matches:
        gene_anns, gene_gaps = _profile_gene(query_seq, match)
        annotations.extend(gene_anns)
        coverage_gaps.extend(gene_gaps)

    logger.info(
        'FASTA consensus: %d annotation(s) from %d gene(s), %d non-covered stretch(es) '
        '(%d codon position(s) total)',
        len(annotations), len(matches), len(coverage_gaps),
        sum(gap.codon_end - gap.codon_start + 1 for gap in coverage_gaps),
    )
    return annotations, coverage_gaps


def fasta_to_vcf(
    query_seq: str,
    matches: list[GeneMatch],
) -> tuple[list[VariantCall], list[CoverageGap]]:
    """
    Convert FASTA consensus differences into a VCF-like VariantCall stream.

    This initial compatibility implementation reuses the existing FASTA consensus
    annotation engine and returns the underlying synthetic VariantCall objects.

    :param query_seq: full query FASTA sequence (forward strand, upper case)
    :param matches: gene matches from sequence alignment
    :return: (variant calls, coverage gaps)
    """
    annotations, coverage_gaps = profile_fasta_consensus(query_seq, matches)
    variants = [annotation.variant for annotation in annotations]
    return variants, coverage_gaps


def _profile_gene(query_seq: str, match: GeneMatch) -> tuple[list[AnnotatedVariant], list[CoverageGap]]:
    """
    Align query region to one gene CDS and emit amino acid differences.

    Gapped alignment strings are reconstructed from the stored CIGAR instead of
    re-running a second alignment, avoiding the O(n×m) cost of a duplicate pass.

    Codons outside the aligned CDS span are treated as non-covered. This keeps
    terminal missing sequence and terminal N-runs equivalent in coverage handling.

    :param query_seq: full query sequence (forward strand)
    :param match: gene match from sequence alignment
    :return: (annotated differences, coverage gaps) for this gene
    """
    gene = match.gene
    if not gene.nt_sequence:
        logger.warning('Gene %r has no stored CDS sequence — skipping', gene.name)
        return [], []

    # Extract query region oriented to coding strand
    region = query_seq[match.query_start:match.query_end].upper()
    if match.strand == '-':
        region = str(Seq(region).reverse_complement())

    aligned_cds_len = sum(int(n) for n, op in re.findall(r'(\d+)([MD])', match.cigar))
    covered_cds_start = match.cds_start
    covered_cds_end = match.cds_start + aligned_cds_len

    aligned_ref, aligned_query = _gapped_strings_from_cigar(
        gene.nt_sequence.upper(), region, match.cigar, match.cds_start,
    )
    return _annotate_from_alignment(
        aligned_ref,
        aligned_query,
        gene,
        covered_cds_start=covered_cds_start,
        covered_cds_end=covered_cds_end,
    )


# ─────────────────────────────────────────────────────────────────────
# Alignment-based codon annotation helpers
# ─────────────────────────────────────────────────────────────────────

def _gapped_strings_from_cigar(
    cds: str,
    region: str,
    cigar: str,
    cds_start: int,
) -> tuple[str, str]:
    """
    Reconstruct gapped alignment strings from a CIGAR string without re-aligning.

    Leading and trailing unaligned CDS bases (when coverage < 100%) are represented
    as query gaps so the frame walk in ``_annotate_from_alignment`` covers the full CDS.

    CIGAR operations are CDS-relative: M=match/mismatch, I=insertion in query, D=deletion in query.

    :param cds: full internal CDS nucleotide sequence
    :param region: extracted query region (query[query_start:query_end], coding strand)
    :param cigar: CIGAR string from sequence matching
    :param cds_start: 0-based CDS position where the alignment begins
    :return: (aligned_ref, aligned_query) of equal length
    """
    aligned_ref: list[str] = []
    aligned_query: list[str] = []

    # Unaligned CDS bases before alignment start
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
        elif op == 'I':  # insertion in query
            aligned_ref.append('-' * n)
            aligned_query.append(region[query_pos:query_pos + n])
            query_pos += n
        elif op == 'D':  # deletion in query
            aligned_ref.append(cds[ref_pos:ref_pos + n])
            aligned_query.append('-' * n)
            ref_pos += n

    # Trailing unaligned CDS bases
    if ref_pos < len(cds):
        aligned_ref.append(cds[ref_pos:])
        aligned_query.append('-' * (len(cds) - ref_pos))

    return ''.join(aligned_ref), ''.join(aligned_query)


@dataclass(frozen=True)
class _CodonContext:
    """Per-codon alignment context used by the FASTA annotation walker."""

    triples: list[tuple[str, str, str]]
    ref_codon: str
    query_codon: str
    ins_before: str
    mid_insertions: str
    insertion_after_pos1: str
    insertion_after_pos2: str


def _codon_outside_coverage(
    *,
    codon_idx: int,
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> bool:
    """Return whether one codon falls outside the aligned/assessable CDS span."""
    if covered_cds_start is None or covered_cds_end is None:
        return False
    codon_nt_start = frame + codon_idx * 3
    codon_nt_end = codon_nt_start + 3
    return codon_nt_start < covered_cds_start or codon_nt_end > covered_cds_end


def _build_codon_context(coding: list[tuple[str, str, str]], i: int) -> _CodonContext:
    """Build reusable per-codon context for one alignment step."""
    triples = coding[i:i + 3]
    return _CodonContext(
        triples=triples,
        ref_codon=''.join(r for r, _, _ in triples),
        query_codon=''.join(q for _, q, _ in triples),
        ins_before=triples[0][2],
        mid_insertions=triples[1][2] + triples[2][2],
        insertion_after_pos1=triples[1][2],
        insertion_after_pos2=triples[2][2],
    )


def _annotate_boundary_insertion_codon(
    *,
    gene: GeneRecord,
    codon_idx: int,
    context: _CodonContext,
    last_valid_codon_idx: int,
    last_valid_ref_codon: str,
    last_valid_query_codon: str,
) -> tuple[AnnotatedVariant | None, bool]:
    """
    Handle insertion-before-codon events.

    :return: (annotation or None, mark_current_codon_as_gap)
    """
    if context.mid_insertions or '-' in context.query_codon:
        return (
            _annotate_fasta_inframe_complex_codon(
                gene,
                codon_idx,
                context.ref_codon,
                context.query_codon,
            ),
            False,
        )

    if last_valid_query_codon and last_valid_codon_idx >= 0:
        n_ins = len(context.ins_before)
        if n_ins % 3 != 0:
            return (
                _annotate_fasta_frameshift_insertion(
                    gene,
                    last_valid_codon_idx,
                    last_valid_ref_codon,
                    last_valid_query_codon,
                    context.ins_before,
                    codon_idx * 3,
                    context.query_codon[0],
                ),
                False,
            )
        return (
            _annotate_fasta_insertion_codon(
                gene,
                last_valid_codon_idx,
                last_valid_ref_codon,
                last_valid_query_codon,
                context.ins_before,
                codon_idx * 3,
                context.query_codon[0],
            ),
            False,
        )

    # No preceding anchor codon available (insertion before first codon): non-assessable.
    return None, True


def _annotate_mid_codon_insertion(
    *,
    gene: GeneRecord,
    codon_idx: int,
    context: _CodonContext,
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
) -> AnnotatedVariant:
    """Handle insertions inside codons, including non-3n mid-codon frameshifts."""
    if '-' not in context.query_codon and len(context.mid_insertions) % 3 != 0:
        if context.insertion_after_pos1 and not context.insertion_after_pos2:
            return _annotate_fasta_frameshift_mid_codon_insertion(
                gene=gene,
                codon_idx=codon_idx,
                ref_codon=context.ref_codon,
                query_codon=context.query_codon,
                last_valid_codon_idx=last_valid_codon_idx,
                last_valid_query_codon=last_valid_query_codon,
                inserted_bases=context.insertion_after_pos1,
                anchor_coding_nt_idx=codon_idx * 3,
                anchor_query_nt=context.query_codon[0],
                current_coding_nt_idx=codon_idx * 3 + 1,
                boundary_query_nt=context.query_codon[1],
            )

        if context.insertion_after_pos2 and not context.insertion_after_pos1:
            return _annotate_fasta_frameshift_mid_codon_insertion(
                gene=gene,
                codon_idx=codon_idx,
                ref_codon=context.ref_codon,
                query_codon=context.query_codon,
                last_valid_codon_idx=last_valid_codon_idx,
                last_valid_query_codon=last_valid_query_codon,
                inserted_bases=context.insertion_after_pos2,
                anchor_coding_nt_idx=codon_idx * 3 + 1,
                anchor_query_nt=context.query_codon[1],
                current_coding_nt_idx=codon_idx * 3 + 2,
                boundary_query_nt=context.query_codon[2],
            )

    return _annotate_fasta_inframe_complex_codon(
        gene,
        codon_idx,
        context.ref_codon,
        context.query_codon,
    )


def _collect_deletion_run(
    *,
    coding: list[tuple[str, str, str]],
    i: int,
    codon_idx: int,
    frame: int,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
    first_context: _CodonContext,
) -> tuple[list[str], list[str], int, int]:
    """Collect one contiguous deletion-affected codon run."""
    run_ref_codons = [first_context.ref_codon]
    run_query_codons = [first_context.query_codon]
    j = i + 3
    next_c_idx = codon_idx + 1

    while j + 3 <= len(coding):
        nxt_context = _build_codon_context(coding, j)
        if nxt_context.ins_before or nxt_context.mid_insertions:
            break
        if '-' not in nxt_context.query_codon:
            break
        if run_query_codons[-1][-1] != '-' or nxt_context.query_codon[0] != '-':
            break
        if _codon_outside_coverage(
            codon_idx=next_c_idx,
            frame=frame,
            covered_cds_start=covered_cds_start,
            covered_cds_end=covered_cds_end,
        ):
            break
        run_ref_codons.append(nxt_context.ref_codon)
        run_query_codons.append(nxt_context.query_codon)
        j += 3
        next_c_idx += 1

    return run_ref_codons, run_query_codons, j, next_c_idx


def _annotate_deletion_run(
    *,
    gene: GeneRecord,
    run_start_idx: int,
    run_ref_codons: list[str],
    run_query_codons: list[str],
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
    next_codon_query_nt: str | None,
) -> tuple[AnnotatedVariant | None, range | None]:
    """Classify one deletion run as canonical deletion, frameshift, or non-assessable gap."""
    total_deleted_nt = sum(
        1
        for run_query_codon in run_query_codons
        for base in run_query_codon
        if base == '-'
    )
    all_full_gap_codons = all(run_query_codon == '---' for run_query_codon in run_query_codons)

    if total_deleted_nt % 3 == 0 and all_full_gap_codons:
        if not last_valid_query_codon:
            run_end_idx = run_start_idx + len(run_ref_codons)
            return None, range(run_start_idx, run_end_idx)
        return (
            _annotate_fasta_deletion_codons(
                gene,
                last_valid_codon_idx,
                last_valid_query_codon,
                run_ref_codons,
                next_codon_query_nt,
            ),
            None,
        )

    return (
        _annotate_fasta_frameshift_partial_deletion(
            gene,
            run_start_idx,
            run_ref_codons,
            run_query_codons,
            last_valid_codon_idx,
            last_valid_query_codon,
            next_codon_query_nt,
        ),
        None,
    )


def _annotate_snp_step(
    *,
    context: _CodonContext,
    codon_idx: int,
    gene: GeneRecord,
) -> tuple[list[AnnotatedVariant], int, str, str]:
    """Annotate one plain codon step (no gaps and no insertions)."""
    ref_aa = translate_codon(context.ref_codon)
    annotations = _annotate_snp_codon(
        context.ref_codon,
        context.query_codon,
        ref_aa,
        codon_idx,
        gene,
    )
    return annotations, codon_idx, context.ref_codon, context.query_codon


def _handle_codon_context(
    *,
    coding: list[tuple[str, str, str]],
    i: int,
    codon_idx: int,
    frame: int,
    gene: GeneRecord,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
    last_valid_codon_idx: int,
    last_valid_ref_codon: str,
    last_valid_query_codon: str,
) -> tuple[list[AnnotatedVariant], list[int], int, int, int, str, str]:
    """Process one codon step and return updated walk state pieces."""
    annotations: list[AnnotatedVariant] = []
    gap_codon_indices: list[int] = []

    if _codon_outside_coverage(
        codon_idx=codon_idx,
        frame=frame,
        covered_cds_start=covered_cds_start,
        covered_cds_end=covered_cds_end,
    ):
        return [], [codon_idx], i + 3, codon_idx + 1, last_valid_codon_idx, last_valid_ref_codon, last_valid_query_codon

    if i + 3 > len(coding):
        return [], [], len(coding), codon_idx, last_valid_codon_idx, last_valid_ref_codon, last_valid_query_codon

    context = _build_codon_context(coding, i)

    if context.ins_before:
        annotation, mark_gap = _annotate_boundary_insertion_codon(
            gene=gene,
            codon_idx=codon_idx,
            context=context,
            last_valid_codon_idx=last_valid_codon_idx,
            last_valid_ref_codon=last_valid_ref_codon,
            last_valid_query_codon=last_valid_query_codon,
        )
        if annotation is not None:
            annotations.append(annotation)
        if mark_gap:
            gap_codon_indices.append(codon_idx)
        return (
            annotations,
            gap_codon_indices,
            i + 3,
            codon_idx + 1,
            last_valid_codon_idx,
            last_valid_ref_codon,
            last_valid_query_codon,
        )

    if context.mid_insertions:
        annotations.append(
            _annotate_mid_codon_insertion(
                gene=gene,
                codon_idx=codon_idx,
                context=context,
                last_valid_codon_idx=last_valid_codon_idx,
                last_valid_query_codon=last_valid_query_codon,
            )
        )
        return (
            annotations,
            gap_codon_indices,
            i + 3,
            codon_idx + 1,
            last_valid_codon_idx,
            last_valid_ref_codon,
            last_valid_query_codon,
        )

    if '-' in context.query_codon:
        run_start_idx = codon_idx
        run_ref_codons, run_query_codons, j, next_c_idx = _collect_deletion_run(
            coding=coding,
            i=i,
            codon_idx=codon_idx,
            frame=frame,
            covered_cds_start=covered_cds_start,
            covered_cds_end=covered_cds_end,
            first_context=context,
        )
        next_codon_query_nt = coding[j][1] if j < len(coding) else None
        deletion_annotation, gap_range = _annotate_deletion_run(
            gene=gene,
            run_start_idx=run_start_idx,
            run_ref_codons=run_ref_codons,
            run_query_codons=run_query_codons,
            last_valid_codon_idx=last_valid_codon_idx,
            last_valid_query_codon=last_valid_query_codon,
            next_codon_query_nt=next_codon_query_nt,
        )
        if deletion_annotation is not None:
            annotations.append(deletion_annotation)
        if gap_range is not None:
            gap_codon_indices.extend(gap_range)
        return (
            annotations,
            gap_codon_indices,
            j,
            next_c_idx,
            last_valid_codon_idx,
            last_valid_ref_codon,
            last_valid_query_codon,
        )

    if context.query_codon.upper() == 'NNN':
        return [], [codon_idx], i + 3, codon_idx + 1, last_valid_codon_idx, last_valid_ref_codon, last_valid_query_codon

    snp_annotations, last_idx, last_ref, last_query = _annotate_snp_step(
        context=context,
        codon_idx=codon_idx,
        gene=gene,
    )
    return snp_annotations, [], i + 3, codon_idx + 1, last_idx, last_ref, last_query


def _walk_coding_codons(
    *,
    coding: list[tuple[str, str, str]],
    frame: int,
    gene: GeneRecord,
    covered_cds_start: int | None,
    covered_cds_end: int | None,
) -> tuple[list[AnnotatedVariant], list[int]]:
    """Walk coding-aligned codons and collect annotations plus non-covered codon indices."""
    annotations: list[AnnotatedVariant] = []
    gap_codon_indices: list[int] = []
    codon_idx = 0
    i = 0
    last_valid_codon_idx: int = -1
    last_valid_ref_codon = ''
    last_valid_query_codon = ''

    while i < len(coding):
        (
            step_annotations,
            step_gaps,
            i,
            codon_idx,
            last_valid_codon_idx,
            last_valid_ref_codon,
            last_valid_query_codon,
        ) = _handle_codon_context(
            coding=coding,
            i=i,
            codon_idx=codon_idx,
            frame=frame,
            gene=gene,
            covered_cds_start=covered_cds_start,
            covered_cds_end=covered_cds_end,
            last_valid_codon_idx=last_valid_codon_idx,
            last_valid_ref_codon=last_valid_ref_codon,
            last_valid_query_codon=last_valid_query_codon,
        )
        annotations.extend(step_annotations)
        gap_codon_indices.extend(step_gaps)

    return annotations, gap_codon_indices


def _annotate_from_alignment(
    aligned_ref: str,
    aligned_query: str,
    gene: GeneRecord,
    covered_cds_start: int | None = None,
    covered_cds_end: int | None = None,
) -> tuple[list[AnnotatedVariant], list[CoverageGap]]:
    """
    Walk the pairwise alignment in reference reading frame and emit AA differences.

    Each ref position records (ref_base, query_base_or_gap, insertions_before),
    where 'insertions_before' are query bases that appear before this ref position
    in the alignment (i.e., at positions where ref is '-').

    Codons are defined by triplets of consecutive ref positions after the frame offset.
    Mutation consequences:

    - Boundary insertion only (ins_before, no mid-codon insertions, no query gaps):
      annotated as insertion (in-frame, 3n) or frameshift (non-3n).
    - Mid-codon insertion (before position 1 or 2 of codon), or ins_before combined with
      query gaps: annotated as inframe_complex.
        - Consecutive codons with query gaps are treated as one deletion run.
        - Run is annotated as deletion only when every affected codon is fully deleted
            (all query positions are '-') and total deleted nucleotides are 3n. If no valid
            preceding codon is available, treated as a coverage gap.
        - Otherwise, run is annotated as one frameshift event.
    - All three query positions are 'N': non-covered codon, reported as CoverageGap.
    - Ungapped, non-insertion codon: IUPAC-expanded SNP comparison.

    The anchor amino acid for insertions and deletions uses the query codon context
    (not the internal reference), matching the VCF annotation convention.

    :param aligned_ref: reference CDS with gap characters
    :param aligned_query: query CDS with gap characters
    :param gene: gene record with stored nt_sequence and codon_start
    :param covered_cds_start: optional CDS nt start (inclusive) of the aligned/assessable region
    :param covered_cds_end: optional CDS nt end (exclusive) of the aligned/assessable region
    :return: (annotated variants, coverage gaps) — amino acid changes and non-covered codons
    """
    frame = gene.codon_start

    # Build per-ref-position list: (ref_base, query_base_or_gap, insertions_before)
    ref_positions: list[tuple[str, str, str]] = []
    pending_ins = ''
    for r, q in zip(aligned_ref, aligned_query):
        if r == '-':
            pending_ins += q
        else:
            ref_positions.append((r, q, pending_ins))
            pending_ins = ''

    # Trailing query insertions beyond the reference end are ignored
    coding = ref_positions[frame:]  # skip leading non-coding frame offset
    annotations, gap_codon_indices = _walk_coding_codons(
        coding=coding,
        frame=frame,
        gene=gene,
        covered_cds_start=covered_cds_start,
        covered_cds_end=covered_cds_end,
    )
    return annotations, _merge_codon_gaps(gene.name, gap_codon_indices)


def _merge_codon_gaps(gene_name: str, codon_indices: list[int]) -> list[CoverageGap]:
    """
    Merge a list of non-covered codon indices into contiguous CoverageGap stretches.

    :param gene_name: gene the indices belong to
    :param codon_indices: unsorted list of 0-based codon indices without coverage
    :return: list of CoverageGap objects with merged codon ranges
    """
    if not codon_indices:
        return []

    sorted_indices = sorted(codon_indices)
    gaps: list[CoverageGap] = []
    start = sorted_indices[0]
    end = sorted_indices[0]
    for idx in sorted_indices[1:]:
        if idx == end + 1:
            end = idx
        else:
            gaps.append(CoverageGap(gene_name=gene_name, codon_start=start, codon_end=end))
            start = idx
            end = idx
    gaps.append(CoverageGap(gene_name=gene_name, codon_start=start, codon_end=end))
    return gaps


def _codon_genomic_pos(gene: GeneRecord, codon_idx: int) -> int:
    """
    Return the 0-based internal genomic position of the first NT in a codon.

    :param gene: gene record
    :param codon_idx: 0-based codon index in the protein
    :return: 0-based genomic position on the internal reference
    """
    genomic_pos = gene.cds_to_genomic_position(gene.codon_start + codon_idx * 3)
    if genomic_pos is None:
        raise ValueError(f'Codon index {codon_idx} is outside CDS for gene {gene.name!r}')
    return genomic_pos


def _coding_nt_genomic_pos(gene: GeneRecord, coding_nt_idx: int) -> int:
    """
    Return the 0-based internal genomic position of one coding nucleotide index.

    :param gene: gene record
    :param coding_nt_idx: 0-based nucleotide index in coding orientation (after codon_start)
    :return: 0-based genomic position on the internal reference
    """
    genomic_pos = gene.cds_to_genomic_position(gene.codon_start + coding_nt_idx)
    if genomic_pos is None:
        raise ValueError(
            f'Coding nucleotide index {coding_nt_idx} is outside CDS for gene {gene.name!r}'
        )
    return genomic_pos


def _make_variant(
    gene: GeneRecord,
    codon_idx: int,
    ref: str,
    alt: str,
    af: float = 1.0,
) -> VariantCall:
    """Build a synthetic VariantCall for a FASTA-derived amino acid difference."""
    return VariantCall(
        chrom=gene.name,
        pos=_codon_genomic_pos(gene, codon_idx),
        ref=ref,
        alt=alt,
        allele_freq=af,
        depth=0,
        filter_status='PASS',
    )


def _make_variant_from_coding_nt(
    gene: GeneRecord,
    coding_nt_idx: int,
    ref: str,
    alt: str,
    af: float = 1.0,
) -> VariantCall:
    """Build a synthetic VariantCall anchored at one coding nucleotide position."""
    return VariantCall(
        chrom=gene.name,
        pos=_coding_nt_genomic_pos(gene, coding_nt_idx),
        ref=ref,
        alt=alt,
        allele_freq=af,
        depth=0,
        filter_status='PASS',
    )



def _annotate_snp_codon(
    ref_codon: str,
    query_codon: str,
    ref_aa: str,
    codon_idx: int,
    gene: GeneRecord,
) -> list[AnnotatedVariant]:
    """
    Annotate a codon with possible IUPAC ambiguity bases.

    Expands all possible codons from IUPAC ambiguity codes. Emits one
    AnnotatedVariant per unique non-reference amino acid. Each variant gets
    allele_freq = 1 / total_possible_amino_acids.

    Synonymous changes (all possible AAs equal ref_aa) produce no output.

    :param ref_codon: 3-base internal reference codon
    :param query_codon: 3-base query codon (may contain IUPAC codes)
    :param ref_aa: translated reference amino acid
    :param codon_idx: 0-based codon index
    :param gene: gene record
    :return: list of AnnotatedVariant (empty if synonymous or no change)
    """
    possible_aas = _expand_iupac_codon(query_codon)
    non_ref = sorted(possible_aas - {ref_aa})
    if not non_ref:
        return []

    af_each = 1.0 / len(possible_aas)
    changed_offsets = [idx for idx, (r, q) in enumerate(zip(ref_codon, query_codon)) if r != q]
    diff_offset = changed_offsets[0] if changed_offsets else 0
    var_ref = ref_codon[diff_offset]
    var_alt = query_codon[diff_offset]
    coding_nt_idx = codon_idx * 3 + diff_offset
    annotations = []
    for alt_aa in non_ref:
        consequence = _snp_consequence(ref_aa, alt_aa, codon_idx)
        var = _make_variant_from_coding_nt(gene, coding_nt_idx, var_ref, var_alt, af=af_each)
        annotations.append(AnnotatedVariant(
            variant=var,
            gene_name=gene.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon=query_codon,
            ref_aa=ref_aa,
            alt_aa=alt_aa,
            consequence=consequence,
            is_fasta_mode=True,
        ))

    return annotations


def _expand_iupac_codon(codon: str) -> set[str]:
    """
    Return all unique amino acids possible for a codon with IUPAC ambiguity.

    :param codon: 3-base codon (may contain IUPAC ambiguity codes)
    :return: set of possible amino acids (single-letter codes)
    """

    iupac_code = {
        'R': 'AG', 'Y': 'CT', 'S': 'GC', 'W': 'AT', 'K': 'GT', 'M': 'AC',
        'B': 'CGT', 'D': 'AGT', 'H': 'ACT', 'V': 'ACG', 'N': 'ACGT',
    }

    bases = [iupac_code.get(b, b) for b in codon.upper()]
    aas: set[str] = set()
    for combo in itertools_product(*bases):
        aa = translate_codon(''.join(combo))
        if aa != '?':
            aas.add(aa)
    return aas


def _snp_consequence(ref_aa: str, alt_aa: str, codon_idx: int) -> str:
    """Classify the amino acid consequence of a single codon difference."""
    if ref_aa == alt_aa:
        return 'synonymous'
    if codon_idx == 0 and ref_aa == 'M':
        return 'start_lost'
    if alt_aa == '*' and ref_aa != '*':
        return 'stop_gained'
    if ref_aa == '*':
        return 'stop_loss'
    return 'missense'


def _annotate_fasta_insertion_codon(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    anchor_codon: str,
    inserted_bases: str,
    current_coding_nt_idx: int,
    boundary_query_nt: str,
) -> AnnotatedVariant:
    """
    Annotate an in-frame insertion detected in a FASTA alignment.

    The anchor codon (the ref codon following the insertion in query context) provides the
    anchor amino acid. The inserted bases are in CDS orientation (already RC'd by the caller
    for minus-strand genes).

    :param gene: gene record
    :param codon_idx: 0-based codon index of the anchor codon (directly after the insertion)
    :param ref_codon: internal reference codon at codon_idx
    :param anchor_codon: query bases at codon_idx (CDS orientation, length 3)
    :param inserted_bases: inserted query bases (CDS orientation, length multiple of 3)
    :param current_coding_nt_idx: coding NT index of the first base in the codon after insertion
    :param boundary_query_nt: mapped query NT at the insertion boundary (coding orientation)
    :return: AnnotatedVariant with consequence='insertion'
    """
    anchor_aa = translate_codon(anchor_codon)
    inserted_aas = str(Seq(inserted_bases).translate())
    if gene.strand == '+':
        anchor_nt = anchor_codon[-1]
        inserted_nt = inserted_bases
        anchor_nt_idx = codon_idx * 3 + 2
    else:
        # For '-' strand, report NT alleles in genomic 5'->3' orientation.
        # The genomic anchor is the NT mapped at the right codon boundary.
        anchor_nt = reverse_complement(boundary_query_nt)
        inserted_nt = reverse_complement(inserted_bases)
        anchor_nt_idx = current_coding_nt_idx
    var = _make_variant_from_coding_nt(
        gene,
        anchor_nt_idx,
        anchor_nt,
        anchor_nt + inserted_nt,
    )
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=anchor_aa + inserted_aas,
        consequence='insertion',
        is_fasta_mode=True,
    )


def _annotate_fasta_deletion_codons(
    gene: GeneRecord,
    anchor_codon_idx: int,
    anchor_query_codon: str,
    deleted_ref_codons: list[str],
    next_codon_query_nt: str | None = None,
) -> AnnotatedVariant:
    """
    Annotate a contiguous run of fully deleted codons detected in a FASTA alignment.

    The last valid query codon preceding the deletion provides the anchor amino acid.
    Deleted amino acids are translated from the internal reference codons (coordinate anchor).

    The NT anchor follows VCF convention: the nucleotide immediately 5' of the deletion in
    genomic orientation. For plus-strand genes this is the last NT of the anchor codon; for
    minus-strand genes it is the first NT of the codon after the deletion (which is 5'
    genomically because CDS and genomic order are reversed).

    :param gene: gene record
    :param anchor_codon_idx: 0-based codon index of the anchor codon (last valid before deletion)
    :param anchor_query_codon: query codon immediately before the deletion (CDS orientation)
    :param deleted_ref_codons: internal reference nucleotide codon(s) for each deleted position
    :param next_codon_query_nt: first query NT of the codon after the deletion (CDS orientation);
        required for minus-strand genes
    :return: AnnotatedVariant with consequence='deletion'
    """
    anchor_aa = translate_codon(anchor_query_codon)
    deleted_aas = ''.join(translate_codon(c) for c in deleted_ref_codons)
    deleted_nt = ''.join(deleted_ref_codons)

    if gene.strand == '-':
        # Minus strand: VCF anchor is the 5' genomic side = first NT after deletion in CDS order
        if next_codon_query_nt is None:
            raise ValueError('Minus-strand deletion at gene end has no anchor nucleotide after deletion')
        next_codon_idx = anchor_codon_idx + 1 + len(deleted_ref_codons)
        anchor_nt_idx = next_codon_idx * 3
        nt_anchor_query = next_codon_query_nt
    else:
        anchor_nt_idx = anchor_codon_idx * 3 + 2
        nt_anchor_query = anchor_query_codon[-1]

    var = _make_fasta_deletion_variant(gene, anchor_nt_idx, nt_anchor_query, deleted_nt)
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=anchor_codon_idx,
        ref_codon=deleted_ref_codons[0],
        alt_codon='',
        ref_aa=anchor_aa + deleted_aas,
        alt_aa=anchor_aa,
        consequence='deletion',
        is_fasta_mode=True,
    )


def _annotate_fasta_frameshift_insertion(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    anchor_codon: str,
    inserted_bases: str,
    current_coding_nt_idx: int,
    boundary_query_nt: str,
) -> AnnotatedVariant:
    """
    Annotate a frameshift insertion detected in a FASTA alignment.

    :param gene: gene record
    :param codon_idx: 0-based codon index where the frameshift starts
    :param ref_codon: internal reference codon at codon_idx
    :param anchor_codon: codon used to derive the anchor amino acid (CDS orientation)
    :param inserted_bases: inserted query bases in CDS orientation
    :param current_coding_nt_idx: coding NT index of the first base in the codon after insertion
    :param boundary_query_nt: mapped query NT at the insertion boundary in CDS orientation
    :return: AnnotatedVariant with consequence='frameshift'
    """
    anchor_aa = translate_codon(anchor_codon)
    anchor_nt_idx = codon_idx * 3 + 2
    var = _make_fasta_insertion_variant(
        gene,
        anchor_nt_idx,
        anchor_codon[-1],
        inserted_bases,
        current_coding_nt_idx,
        boundary_query_nt,
    )
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=f'{anchor_aa}fsX',
        consequence='frameshift',
        is_fasta_mode=True,
    )


def _annotate_fasta_frameshift_mid_codon_insertion(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    query_codon: str,
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
    inserted_bases: str,
    anchor_coding_nt_idx: int,
    anchor_query_nt: str,
    current_coding_nt_idx: int,
    boundary_query_nt: str,
) -> AnnotatedVariant:
    """
    Annotate a non-3n insertion occurring within a codon as frameshift.

    :param gene: gene record
    :param codon_idx: 0-based codon index where the frameshift starts
    :param ref_codon: internal reference codon at codon_idx
    :param query_codon: query codon at codon_idx in CDS orientation
    :param last_valid_codon_idx: previous ungapped codon index, or -1 when unavailable
    :param last_valid_query_codon: previous ungapped query codon in CDS orientation
    :param inserted_bases: inserted query bases in CDS orientation
    :param anchor_coding_nt_idx: coding index of the nucleotide before insertion
    :param anchor_query_nt: query nucleotide before insertion in CDS orientation
    :param current_coding_nt_idx: coding index of the nucleotide after insertion
    :param boundary_query_nt: query nucleotide after insertion in CDS orientation
    :return: AnnotatedVariant with consequence='frameshift'
    """
    aa_anchor_codon_idx = codon_idx
    anchor_codon = _resolve_fasta_anchor_codon(ref_codon, query_codon)
    anchor_aa = translate_codon(anchor_codon)
    if gene.strand == '-' and last_valid_codon_idx >= 0 and last_valid_query_codon:
        aa_anchor_codon_idx = last_valid_codon_idx
        anchor_aa = translate_codon(last_valid_query_codon)
    var = _make_fasta_insertion_variant(
        gene,
        anchor_coding_nt_idx,
        anchor_query_nt,
        inserted_bases,
        current_coding_nt_idx,
        boundary_query_nt,
    )
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=aa_anchor_codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=f'{anchor_aa}fsX',
        consequence='frameshift',
        is_fasta_mode=True,
    )


def _annotate_fasta_frameshift_deletion(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    anchor_codon: str,
    query_codon: str,
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
) -> AnnotatedVariant:
    """
    Annotate a frameshift deletion detected in a FASTA alignment.

    The amino-acid anchor stays at the affected codon, while the nucleotide change
    follows FASTA indel anchor conventions analogous to VCF-mode rendering.

    :param gene: gene record
    :param codon_idx: 0-based codon index where the frameshift starts
    :param ref_codon: internal reference codon at codon_idx
    :param anchor_codon: codon used to derive the anchor amino acid (CDS orientation)
    :param query_codon: aligned query codon, including gaps
    :param last_valid_codon_idx: previous ungapped codon index, or -1 when unavailable
    :param last_valid_query_codon: previous ungapped query codon in CDS orientation
    :return: AnnotatedVariant with consequence='frameshift'
    """
    gap_offsets = [idx for idx, base in enumerate(query_codon) if base == '-']
    if not gap_offsets:
        raise ValueError('Frameshift deletion annotation requires at least one gap in query codon')

    deleted_nt = ''.join(ref_codon[idx] for idx in gap_offsets)
    first_gap = gap_offsets[0]
    if first_gap == 0:
        if last_valid_codon_idx < 0 or not last_valid_query_codon:
            raise ValueError('Frameshift deletion at gene start has no valid anchor nucleotide')
        anchor_nt_idx = last_valid_codon_idx * 3 + 2
        anchor_query_nt = last_valid_query_codon[-1]
    else:
        anchor_nt_idx = codon_idx * 3 + (first_gap - 1)
        anchor_query_nt = query_codon[first_gap - 1]

    anchor_aa = translate_codon(anchor_codon)
    var = _make_fasta_deletion_variant(gene, anchor_nt_idx, anchor_query_nt, deleted_nt)
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=f'{anchor_aa}fsX',
        consequence='frameshift',
        is_fasta_mode=True,
    )


def _annotate_fasta_frameshift_partial_deletion(
    gene: GeneRecord,
    first_codon_idx: int,
    ref_codons: list[str],
    query_codons: list[str],
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
    next_codon_query_nt: str | None = None,
) -> AnnotatedVariant:
    """
    Annotate a frameshift deletion spanning one or more codons with partial gaps.

    Collects all deleted nucleotides across the run and emits a single frameshift event.

    The amino acid anchor follows protein/CDS order (strand-independent): the last fully
    intact codon before the frameshift starts. The nucleotide anchor follows VCF convention
    (strand-aware): the nucleotide immediately 5' of the deletion in genomic orientation.
    For plus-strand genes this is before the first gap in CDS order; for minus-strand genes
    it is after the last gap in CDS order (which is 5' genomically).

    :param gene: gene record
    :param first_codon_idx: 0-based codon index of the first affected codon
    :param ref_codons: list of internal reference codons in the affected run
    :param query_codons: list of query codons in the affected run (with gaps)
    :param last_valid_codon_idx: previous ungapped codon index, or -1 when unavailable
    :param last_valid_query_codon: previous ungapped query codon in CDS orientation
    :param next_codon_query_nt: first query NT of the codon after the run (CDS orientation);
        required for minus-strand genes when the last gap falls at codon position 2
    :return: AnnotatedVariant with consequence='frameshift'
    """
    if not ref_codons or not query_codons:
        raise ValueError('Frameshift deletion requires at least one affected codon')

    deleted_nt = _collect_deleted_nt(ref_codons, query_codons)
    aa_anchor_codon_idx, aa_anchor_ref_codon, first_gap = _resolve_partial_deletion_aa_anchor(
        gene=gene,
        first_codon_idx=first_codon_idx,
        ref_codons=ref_codons,
        query_codons=query_codons,
        last_valid_codon_idx=last_valid_codon_idx,
        last_valid_query_codon=last_valid_query_codon,
    )
    anchor_nt_idx, anchor_query_nt = _resolve_partial_deletion_nt_anchor(
        gene=gene,
        first_codon_idx=first_codon_idx,
        query_codons=query_codons,
        last_valid_codon_idx=last_valid_codon_idx,
        last_valid_query_codon=last_valid_query_codon,
        next_codon_query_nt=next_codon_query_nt,
        first_gap=first_gap,
    )

    anchor_aa = translate_codon(aa_anchor_ref_codon)

    var = _make_fasta_deletion_variant(gene, anchor_nt_idx, anchor_query_nt, deleted_nt)
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=aa_anchor_codon_idx,
        ref_codon=ref_codons[0],
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=f'{anchor_aa}fsX',
        consequence='frameshift',
        is_fasta_mode=True,
    )


def _collect_deleted_nt(ref_codons: list[str], query_codons: list[str]) -> str:
    """Collect deleted nucleotide payload across one deletion-affected codon run."""
    deleted_nt = ''
    for ref_codon, query_codon in zip(ref_codons, query_codons):
        gap_offsets = [idx for idx, base in enumerate(query_codon) if base == '-']
        deleted_nt += ''.join(ref_codon[idx] for idx in gap_offsets)
    return deleted_nt


def _resolve_partial_deletion_aa_anchor(
    *,
    gene: GeneRecord,
    first_codon_idx: int,
    ref_codons: list[str],
    query_codons: list[str],
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
) -> tuple[int, str, int]:
    """Resolve AA anchor codon for partial-deletion frameshift events."""
    first_query_codon = query_codons[0]
    first_ref_codon = ref_codons[0]
    first_gap_offsets = [idx for idx, base in enumerate(first_query_codon) if base == '-']
    first_gap = min(first_gap_offsets) if first_gap_offsets else 0

    if first_gap != 0:
        aa_anchor_codon_idx = first_codon_idx
        aa_anchor_ref_codon = first_ref_codon
    else:
        if last_valid_codon_idx < 0 or not last_valid_query_codon:
            raise ValueError('Frameshift deletion at gene start has no valid anchor nucleotide')
        aa_anchor_codon_idx = last_valid_codon_idx
        aa_anchor_ref_codon = gene.nt_sequence[last_valid_codon_idx * 3:last_valid_codon_idx * 3 + 3]

    if gene.strand == '-':
        last_query_codon = query_codons[-1]
        last_gap_in_last_codon = max(idx for idx, base in enumerate(last_query_codon) if base == '-')
        if last_gap_in_last_codon == 2:
            if last_valid_codon_idx < 0:
                raise ValueError(
                    'Minus-strand frameshift deletion at gene start has no valid amino-acid anchor'
                )
            aa_anchor_codon_idx = last_valid_codon_idx
            aa_anchor_ref_codon = gene.nt_sequence[last_valid_codon_idx * 3:last_valid_codon_idx * 3 + 3]

    return aa_anchor_codon_idx, aa_anchor_ref_codon, first_gap


def _resolve_partial_deletion_nt_anchor(
    *,
    gene: GeneRecord,
    first_codon_idx: int,
    query_codons: list[str],
    last_valid_codon_idx: int,
    last_valid_query_codon: str,
    next_codon_query_nt: str | None,
    first_gap: int,
) -> tuple[int, str]:
    """Resolve NT anchor for partial-deletion frameshift events (strand-aware)."""
    if gene.strand != '-':
        if first_gap == 0:
            return last_valid_codon_idx * 3 + 2, last_valid_query_codon[-1]
        first_query_codon = query_codons[0]
        return first_codon_idx * 3 + (first_gap - 1), first_query_codon[first_gap - 1]

    last_codon_idx_in_run = first_codon_idx + len(query_codons) - 1
    last_query_codon = query_codons[-1]
    last_gap_in_last_codon = max(idx for idx, base in enumerate(last_query_codon) if base == '-')
    if last_gap_in_last_codon == 2:
        if next_codon_query_nt is None:
            raise ValueError('Minus-strand frameshift deletion at gene end has no anchor nucleotide')
        return (last_codon_idx_in_run + 1) * 3, next_codon_query_nt
    return (
        last_codon_idx_in_run * 3 + last_gap_in_last_codon + 1,
        last_query_codon[last_gap_in_last_codon + 1],
    )


def _annotate_fasta_inframe_complex_codon(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    query_codon: str,
) -> AnnotatedVariant:
    """
    Annotate a mid-codon indel as inframe_complex.

    Used when an insertion is embedded within a codon (not at a clean boundary)
    or when both a boundary insertion and a mid-codon insertion overlap, making
    the amino acid consequence non-resolvable to a canonical token.

    :param gene: gene record
    :param codon_idx: 0-based codon index of the affected codon
    :param ref_codon: internal reference codon at codon_idx
    :param query_codon: query codon at codon_idx (may include gaps)
    :return: AnnotatedVariant with consequence='inframe_complex'
    """
    var = _make_variant(gene, codon_idx, ref_codon, ref_codon)
    anchor_codon = _resolve_fasta_anchor_codon(ref_codon, query_codon)
    anchor_aa = translate_codon(anchor_codon)
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa='?',
        consequence='inframe_complex',
        is_fasta_mode=True,
    )


def _resolve_fasta_anchor_codon(ref_codon: str, query_codon: str) -> str:
    """Return query anchor codon when valid, otherwise internal reference codon."""
    if len(query_codon) == 3 and '-' not in query_codon:
        return query_codon
    return ref_codon


def _make_fasta_insertion_variant(
    gene: GeneRecord,
    anchor_nt_idx: int,
    anchor_query_nt: str,
    inserted_bases: str,
    current_coding_nt_idx: int,
    boundary_query_nt: str,
) -> VariantCall:
    """Build a FASTA-derived insertion VariantCall using genomic-orientation alleles."""
    if gene.strand == '+':
        return _make_variant_from_coding_nt(
            gene,
            anchor_nt_idx,
            anchor_query_nt,
            anchor_query_nt + inserted_bases,
        )

    anchor_nt = reverse_complement(boundary_query_nt)
    inserted_nt = reverse_complement(inserted_bases)
    return _make_variant_from_coding_nt(
        gene,
        current_coding_nt_idx,
        anchor_nt,
        anchor_nt + inserted_nt,
    )


def _make_fasta_deletion_variant(
    gene: GeneRecord,
    anchor_nt_idx: int,
    anchor_query_nt: str,
    deleted_bases: str,
) -> VariantCall:
    """Build a FASTA-derived deletion VariantCall using genomic-orientation alleles."""
    if gene.strand == '+':
        return _make_variant_from_coding_nt(
            gene,
            anchor_nt_idx,
            anchor_query_nt + deleted_bases,
            anchor_query_nt,
        )

    anchor_nt = reverse_complement(anchor_query_nt)
    deleted_nt = reverse_complement(deleted_bases)
    return _make_variant_from_coding_nt(
        gene,
        anchor_nt_idx,
        anchor_nt + deleted_nt,
        anchor_nt,
    )

