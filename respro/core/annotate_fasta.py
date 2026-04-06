"""
FASTA annotation helpers — codon walking, consequence classification, and IUPAC expansion.

Provides the per-codon annotation logic used by the FASTA profiling pipeline.
Orchestration lives in respro/core/fasta_profile.py.
"""

from __future__ import annotations

import logging
import re
from itertools import product as itertools_product

from Bio.Seq import Seq

from respro.core.annotate_vcf import translate_codon
from respro.db.models import AnnotatedVariant, GeneRecord, VariantCall

logger = logging.getLogger(__name__)

# IUPAC ambiguity codes → possible standard bases
_IUPAC = {
    'R': 'AG', 'Y': 'CT', 'S': 'GC', 'W': 'AT', 'K': 'GT', 'M': 'AC',
    'B': 'CGT', 'D': 'AGT', 'H': 'ACT', 'V': 'ACG', 'N': 'ACGT',
}


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

    # Unaligned CDS bases before alignment start → gaps in query
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

    # Trailing unaligned CDS bases → gaps in query
    if ref_pos < len(cds):
        aligned_ref.append(cds[ref_pos:])
        aligned_query.append('-' * (len(cds) - ref_pos))

    return ''.join(aligned_ref), ''.join(aligned_query)


def _annotate_from_alignment(
    aligned_ref: str,
    aligned_query: str,
    gene: GeneRecord,
) -> list[AnnotatedVariant]:
    """
    Walk the pairwise alignment in reference reading frame and emit AA differences.

    Each ref position records (ref_base, query_base_or_gap, insertions_before),
    where 'insertions_before' are query bases that appear before this ref position
    in the alignment (i.e., at positions where ref is '-').

    Codons are defined by triplets of consecutive ref positions after the frame offset.
    Insertions are processed before each codon; deletions and SNPs within codons
    are handled per triplet. A frameshift stops further processing of that gene.

    :param aligned_ref: reference CDS with gap characters
    :param aligned_query: query CDS with gap characters
    :param gene: gene record with stored nt_sequence and codon_start
    :return: annotated variants (non-synonymous changes only)
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
    codon_idx = 0
    i = 0

    while i < len(coding):
        _, _, ins_before = coding[i]

        # Insertions before this codon (between previous and current codon boundary)
        if ins_before:
            ann = _annotate_insertion(ins_before, codon_idx, gene)
            annotations.append(ann)
            if ann.consequence == 'frameshift':
                break

        if i + 3 > len(coding):
            break  # incomplete codon at end of CDS

        codon_triples = coding[i:i + 3]

        # Insertions embedded within this codon (before positions 1 or 2)
        mid_insertions = codon_triples[1][2] + codon_triples[2][2]
        if mid_insertions:
            ann = _annotate_insertion(mid_insertions, codon_idx, gene)
            annotations.append(ann)
            if ann.consequence == 'frameshift':
                break
            # In-frame mid-codon insertion: codon boundaries after this are still valid
            i += 3
            codon_idx += 1
            continue

        ref_codon = ''.join(r for r, q, ins in codon_triples)
        query_codon = ''.join(q for r, q, ins in codon_triples)
        ref_aa = translate_codon(ref_codon)

        if '-' in query_codon:
            deleted_count = query_codon.count('-')
            ann = _annotate_deletion(deleted_count, ref_codon, query_codon, ref_aa, codon_idx, gene)
            annotations.append(ann)
            if ann.consequence == 'frameshift':
                break
        else:
            # No gaps — expand IUPAC ambiguity and emit non-synonymous changes
            anns = _annotate_snp_codon(ref_codon, query_codon, ref_aa, codon_idx, gene)
            annotations.extend(anns)

        i += 3
        codon_idx += 1

    return annotations


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


def _annotate_insertion(inserted: str, codon_idx: int, gene: GeneRecord) -> AnnotatedVariant:
    """
    Annotate a query insertion at codon_idx.

    Non-3n insertions → frameshift (stops further processing).
    Triplet insertions → in-frame insertion with translated inserted amino acids.

    The variant alt is stored as ref_codon + inserted (anchor style) so the NT
    change display can reconstruct the full codon context.

    :param inserted: inserted query bases
    :param codon_idx: 0-based codon index where insertion occurs
    :param gene: gene record
    :return: AnnotatedVariant
    """
    ref_aa = gene.aa_sequence[codon_idx] if codon_idx < len(gene.aa_sequence) else '?'
    nt_start = gene.codon_start + codon_idx * 3
    ref_nt = gene.nt_sequence[nt_start:nt_start + 3]

    # Store alt as anchor codon + inserted bases so the NT display is unambiguous
    var = _make_variant(gene, codon_idx, ref_nt, ref_nt + inserted)

    if len(inserted) % 3 != 0:
        return AnnotatedVariant(
            variant=var,
            gene_name=gene.name,
            codon_pos=codon_idx,
            ref_codon=ref_nt,
            alt_codon=ref_nt + inserted,
            ref_aa=ref_aa,
            alt_aa='fsX',
            consequence='frameshift',
            is_fasta_mode=True,
        )

    inserted_aa = str(Seq(inserted).translate())
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_nt,
        alt_codon=ref_nt + inserted,
        ref_aa=ref_aa,
        alt_aa=f'{ref_aa}{inserted_aa}',
        consequence='insertion',
        is_fasta_mode=True,
    )


def _annotate_deletion(
    deleted_count: int,
    ref_codon: str,
    query_codon: str,
    ref_aa: str,
    codon_idx: int,
    gene: GeneRecord,
) -> AnnotatedVariant:
    """
    Annotate a deletion within one codon.

    Non-3n deletions → frameshift (stops further processing).
    Triplet deletions → in-frame deletion with translated remaining bases.

    :param deleted_count: number of '-' characters in query_codon
    :param ref_codon: 3-base reference codon
    :param query_codon: 3-base query codon with '-' for deleted positions
    :param ref_aa: reference amino acid
    :param codon_idx: 0-based codon index
    :param gene: gene record
    :return: AnnotatedVariant
    """
    remaining = query_codon.replace('-', '')
    var = _make_variant(gene, codon_idx, ref_codon, remaining or '-')

    if deleted_count % 3 != 0:
        return AnnotatedVariant(
            variant=var,
            gene_name=gene.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon='',
            ref_aa=ref_aa,
            alt_aa='fsX',
            consequence='frameshift',
            is_fasta_mode=True,
        )

    # In-frame deletion: pad to 3 bases if fewer remain (partial codon)
    alt_aa = translate_codon(remaining.ljust(3, 'N')) if remaining else '?'
    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon=remaining,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence='deletion',
        is_fasta_mode=True,
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
    bases = [_IUPAC.get(b, b) for b in codon.upper()]
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

