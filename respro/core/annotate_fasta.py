"""
FASTA annotation helpers — codon walking, consequence classification, and IUPAC expansion.

Provides the per-codon annotation logic used by the FASTA profiling pipeline.
Orchestration lives in respro/core/fasta_profile.py.
"""

from __future__ import annotations

import logging
import re
from itertools import product as itertools_product

from respro.core.annotate_vcf import translate_codon
from respro.db.models import AnnotatedVariant, CoverageGap, GeneRecord, VariantCall

logger = logging.getLogger(__name__)


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
    SNPs are emitted only for ungapped codons. Codons affected by insertions or
    deletions are treated as non-assessable and reported as CoverageGaps.

    A codon where all three query positions are 'N' is treated as non-covered and
    recorded as a CoverageGap instead of being IUPAC-expanded.

    :param aligned_ref: reference CDS with gap characters
    :param aligned_query: query CDS with gap characters
    :param gene: gene record with stored nt_sequence and codon_start
    :param covered_cds_start: optional CDS nt start (inclusive) of the aligned/assessable region
    :param covered_cds_end: optional CDS nt end (exclusive) of the aligned/assessable region
    :return: (annotated variants, coverage gaps) — non-synonymous changes and non-covered codons
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

        # Insertions embedded within this codon (before positions 1 or 2)
        mid_insertions = codon_triples[1][2] + codon_triples[2][2]
        if ins_before or mid_insertions:
            gap_codon_indices.append(codon_idx)
            i += 3
            codon_idx += 1
            continue

        ref_codon = ''.join(r for r, q, ins in codon_triples)
        query_codon = ''.join(q for r, q, ins in codon_triples)
        ref_aa = translate_codon(ref_codon)

        if '-' in query_codon:
            gap_codon_indices.append(codon_idx)
        elif query_codon.upper() == 'NNN':
            # Full-codon N-stretch: no coverage → do not IUPAC-expand
            gap_codon_indices.append(codon_idx)
        else:
            # No gaps — expand IUPAC ambiguity and emit non-synonymous changes
            anns = _annotate_snp_codon(ref_codon, query_codon, ref_aa, codon_idx, gene)
            annotations.extend(anns)

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
    annotations = []
    for alt_aa in non_ref:
        consequence = _snp_consequence(ref_aa, alt_aa, codon_idx)
        var = _make_variant(gene, codon_idx, ref_codon, query_codon, af=af_each)
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

