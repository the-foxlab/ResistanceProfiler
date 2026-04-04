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
from itertools import product as itertools_product

from Bio.Align import PairwiseAligner
from Bio.Seq import Seq

from respro.core.annotation import translate_codon
from respro.core.sequence_matching import GeneMatch
from respro.db.models import AnnotatedVariant, GeneRecord, VariantCall

logger = logging.getLogger(__name__)

# IUPAC ambiguity codes → possible standard bases
_IUPAC = {
    'R': 'AG', 'Y': 'CT', 'S': 'GC', 'W': 'AT', 'K': 'GT', 'M': 'AC',
    'B': 'CGT', 'D': 'AGT', 'H': 'ACT', 'V': 'ACG', 'N': 'ACGT',
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def profile_fasta_consensus(
    query_seq: str,
    matches: list[GeneMatch],
) -> list[AnnotatedVariant]:
    """
    Profile a FASTA consensus sequence against internal gene references.

    For each matched gene the query region is globally aligned to the internal CDS,
    translated in the reference reading frame, and compared amino acid by amino acid.
    SNPs, in-frame insertions/deletions, and frameshifts are all detected.

    Ambiguous IUPAC bases are expanded to all possible codons. Each unique
    non-reference amino acid is emitted as a separate AnnotatedVariant with
    allele_freq = 1 / number_of_distinct_possible_amino_acids.

    :param query_seq: full query FASTA sequence (forward strand, upper case)
    :param matches: gene matches from sequence alignment
    :return: list of annotated amino acid differences
    """
    annotations: list[AnnotatedVariant] = []
    for match in matches:
        annotations.extend(_profile_gene(query_seq, match))

    logger.info(
        'FASTA consensus: %d annotation(s) from %d gene(s)',
        len(annotations), len(matches),
    )
    return annotations


# ──────────────────────────────────────────────────────────────────────
# Per-gene profiling
# ──────────────────────────────────────────────────────────────────────

def _profile_gene(query_seq: str, match: GeneMatch) -> list[AnnotatedVariant]:
    """
    Align query region to one gene CDS and emit amino acid differences.

    :param query_seq: full query sequence (forward strand)
    :param match: gene match from sequence alignment
    :return: annotated differences for this gene
    """
    gene = match.gene
    if not gene.nt_sequence:
        logger.warning('Gene %r has no stored CDS sequence — skipping', gene.name)
        return []

    # Extract query region that aligns to this CDS, oriented to coding strand
    region = query_seq[match.query_start:match.query_end].upper()
    if match.strand == '-':
        region = str(Seq(region).reverse_complement())

    aligned_ref, aligned_query = _global_align(gene.nt_sequence.upper(), region)
    return _annotate_from_alignment(aligned_ref, aligned_query, gene)


def _global_align(ref: str, query: str) -> tuple[str, str]:
    """
    Globally align two nucleotide sequences and return gapped strings.

    :param ref: reference sequence (internal CDS)
    :param query: query sequence (extracted from user FASTA)
    :return: (aligned_ref, aligned_query) with '-' for gaps
    """
    aligner = PairwiseAligner()
    aligner.mode = 'global'
    aligner.match_score = 2.0
    aligner.mismatch_score = -1.0
    aligner.open_gap_score = -3.0
    aligner.extend_gap_score = -0.5

    alignments = aligner.align(ref, query)
    try:
        best = alignments[0]
    except (IndexError, OverflowError):
        # Degenerate case: sequences too different to align reliably
        logger.warning('Global alignment failed for sequences of length %d / %d', len(ref), len(query))
        return ref, query

    return _extract_gapped_strings(best, ref, query)


def _extract_gapped_strings(alignment, ref: str, query: str) -> tuple[str, str]:
    """
    Reconstruct gapped aligned strings from a Biopython PairwiseAlignment.

    alignment.aligned[0] = ref (target) block coordinates.
    alignment.aligned[1] = query block coordinates.

    Unaligned ref positions between blocks = deletion in query.
    Unaligned query positions between blocks = insertion in query.

    :param alignment: Biopython PairwiseAlignment object
    :param ref: reference sequence
    :param query: query sequence
    :return: (gapped_ref, gapped_query) of equal length
    """
    ref_blocks = alignment.aligned[0]
    query_blocks = alignment.aligned[1]

    aligned_ref: list[str] = []
    aligned_query: list[str] = []
    r_prev = 0
    q_prev = 0

    for (r_s, r_e), (q_s, q_e) in zip(ref_blocks, query_blocks):
        q_gap = q_s - q_prev  # unaligned query bases → insertion in query (gap in ref)
        r_gap = r_s - r_prev  # unaligned ref bases → deletion in query (gap in query)

        if q_gap > 0:
            aligned_query.append(query[q_prev:q_s])
            aligned_ref.append('-' * q_gap)

        if r_gap > 0:
            aligned_ref.append(ref[r_prev:r_s])
            aligned_query.append('-' * r_gap)

        aligned_ref.append(ref[r_s:r_e])
        aligned_query.append(query[q_s:q_e])

        r_prev = r_e
        q_prev = q_e

    # Trailing unaligned positions
    if r_prev < len(ref):
        aligned_ref.append(ref[r_prev:])
        aligned_query.append('-' * (len(ref) - r_prev))

    if q_prev < len(query):
        aligned_query.append(query[q_prev:])
        aligned_ref.append('-' * (len(query) - q_prev))

    return ''.join(aligned_ref), ''.join(aligned_query)


# ──────────────────────────────────────────────────────────────────────
# Walking the alignment in reading frame
# ──────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────
# Annotation helpers
# ──────────────────────────────────────────────────────────────────────

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

