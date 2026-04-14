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
    - All three query positions are gaps (full-codon deletion): annotated as deletion
      (consecutive fully-deleted codons are merged into one annotation). If no valid
      preceding codon is available, treated as a coverage gap.
    - One or two query gaps (partial deletion): annotated as frameshift.
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
    annotations: list[AnnotatedVariant] = []
    gap_codon_indices: list[int] = []
    codon_idx = 0
    i = 0
    last_valid_codon_idx: int = -1
    last_valid_ref_codon = ''
    last_valid_query_codon = ''

    while i < len(coding):
        codon_nt_start = frame + codon_idx * 3
        codon_nt_end = codon_nt_start + 3
        if covered_cds_start is not None and covered_cds_end is not None:
            if codon_nt_start < covered_cds_start or codon_nt_end > covered_cds_end:
                gap_codon_indices.append(codon_idx)
                i += 3
                codon_idx += 1
                continue

        _, _, ins_before = coding[i]

        if i + 3 > len(coding):
            break  # incomplete codon at end of CDS

        codon_triples = coding[i:i + 3]
        mid_insertions = codon_triples[1][2] + codon_triples[2][2]
        ref_codon = ''.join(r for r, q, ins in codon_triples)
        query_codon = ''.join(q for r, q, ins in codon_triples)

        if ins_before:
            if mid_insertions or '-' in query_codon:
                # Boundary insertion combined with mid-codon insertion or gap -> inframe_complex.
                annotations.append(
                    _annotate_fasta_inframe_complex_codon(gene, codon_idx, ref_codon, query_codon)
                )
            elif last_valid_query_codon and last_valid_codon_idx >= 0:
                n_ins = len(ins_before)
                if n_ins % 3 != 0:
                    annotations.append(
                        _annotate_fasta_frameshift_codon(
                            gene,
                            last_valid_codon_idx,
                            last_valid_ref_codon,
                            last_valid_query_codon,
                        )
                    )
                else:
                    annotations.append(
                        _annotate_fasta_insertion_codon(
                            gene,
                            last_valid_codon_idx,
                            last_valid_ref_codon,
                            last_valid_query_codon,
                            ins_before,
                            codon_idx * 3,
                            query_codon[0],
                        )
                    )
            else:
                # No preceding anchor codon available (insertion before first codon): non-assessable.
                gap_codon_indices.append(codon_idx)
            i += 3
            codon_idx += 1
            continue

        if mid_insertions:
            annotations.append(
                _annotate_fasta_inframe_complex_codon(gene, codon_idx, ref_codon, query_codon)
            )
            i += 3
            codon_idx += 1
            continue

        if '-' in query_codon:
            if query_codon == '---':
                # Full codon deletion: collect consecutive all-gap codons
                gap_start_idx = codon_idx
                deleted_ref_codons = [ref_codon]
                j = i + 3
                next_c_idx = codon_idx + 1
                while j + 3 <= len(coding):
                    nxt = coding[j:j + 3]
                    if nxt[0][2] or nxt[1][2] + nxt[2][2]:
                        break  # insertion in next gap codon → stop run
                    nxt_qc = ''.join(q for r, q, ins in nxt)
                    if nxt_qc != '---':
                        break
                    # Honour coverage boundaries when extending the run
                    nxt_nt_start = frame + next_c_idx * 3
                    nxt_nt_end = nxt_nt_start + 3
                    if covered_cds_start is not None and covered_cds_end is not None:
                        if nxt_nt_start < covered_cds_start or nxt_nt_end > covered_cds_end:
                            break
                    deleted_ref_codons.append(''.join(r for r, q, ins in nxt))
                    j += 3
                    next_c_idx += 1

                if last_valid_query_codon:
                    annotations.append(
                        _annotate_fasta_deletion_codons(
                            gene, last_valid_codon_idx, last_valid_query_codon, deleted_ref_codons,
                        )
                    )
                else:
                    # Deletion at gene start with no preceding anchor → coverage gap
                    gap_codon_indices.extend(range(gap_start_idx, next_c_idx))
                i = j
                codon_idx = next_c_idx
            else:
                # Partial deletion (1–2 gaps) → frameshift; anchor from internal ref codon
                annotations.append(
                    _annotate_fasta_frameshift_codon(gene, codon_idx, ref_codon, ref_codon)
                )
                i += 3
                codon_idx += 1
            continue

        if query_codon.upper() == 'NNN':
            # Full-codon N-stretch: no coverage → do not IUPAC-expand
            gap_codon_indices.append(codon_idx)
            i += 3
            codon_idx += 1
            continue

        # No gaps, no insertions: expand IUPAC ambiguity and emit non-synonymous changes
        ref_aa = translate_codon(ref_codon)
        anns = _annotate_snp_codon(ref_codon, query_codon, ref_aa, codon_idx, gene)
        annotations.extend(anns)
        last_valid_codon_idx = codon_idx
        last_valid_ref_codon = ref_codon
        last_valid_query_codon = query_codon
        i += 3
        codon_idx += 1

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
    nt_offset = gene.codon_start + codon_idx * 3
    if gene.strand == '+':
        return gene.start + nt_offset
    # For '-' strand genes, codon 0 starts at the highest genomic position
    return (gene.end - 1) - nt_offset


def _coding_nt_genomic_pos(gene: GeneRecord, coding_nt_idx: int) -> int:
    """
    Return the 0-based internal genomic position of one coding nucleotide index.

    :param gene: gene record
    :param coding_nt_idx: 0-based nucleotide index in coding orientation (after codon_start)
    :return: 0-based genomic position on the internal reference
    """
    nt_offset = gene.codon_start + coding_nt_idx
    if gene.strand == '+':
        return gene.start + nt_offset
    return (gene.end - 1) - nt_offset


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
) -> AnnotatedVariant:
    """
    Annotate a contiguous run of fully deleted codons detected in a FASTA alignment.

    The last valid query codon preceding the deletion provides the anchor amino acid.
    Deleted amino acids are translated from the internal reference codons (coordinate anchor).

    :param gene: gene record
    :param anchor_codon_idx: 0-based codon index of the anchor codon (last valid before deletion)
    :param anchor_query_codon: query codon immediately before the deletion (CDS orientation)
    :param deleted_ref_codons: internal reference nucleotide codon(s) for each deleted position
    :return: AnnotatedVariant with consequence='deletion'
    """
    anchor_aa = translate_codon(anchor_query_codon)
    deleted_aas = ''.join(translate_codon(c) for c in deleted_ref_codons)
    deleted_nt = ''.join(deleted_ref_codons)
    anchor_nt = anchor_query_codon[-1]
    anchor_nt_idx = anchor_codon_idx * 3 + 2
    # ref = anchor + deleted (VCF anchor convention); alt = anchor only.
    var = _make_variant_from_coding_nt(
        gene,
        anchor_nt_idx,
        anchor_nt + deleted_nt,
        anchor_nt,
    )
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


def _annotate_fasta_frameshift_codon(
    gene: GeneRecord,
    codon_idx: int,
    ref_codon: str,
    anchor_codon: str,
) -> AnnotatedVariant:
    """
    Annotate a frameshift indel detected in a FASTA alignment.

    :param gene: gene record
    :param codon_idx: 0-based codon index where the frameshift starts
    :param ref_codon: internal reference codon at codon_idx
    :param anchor_codon: codon used to derive the anchor amino acid (CDS orientation)
    :return: AnnotatedVariant with consequence='frameshift'
    """
    anchor_aa = translate_codon(anchor_codon)
    anchor_nt = anchor_codon[-1]
    anchor_nt_idx = codon_idx * 3 + 2
    var = _make_variant_from_coding_nt(gene, anchor_nt_idx, anchor_nt, anchor_nt)
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa='fsX',
        consequence='frameshift',
        is_fasta_mode=True,
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

