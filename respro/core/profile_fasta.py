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

from Bio.Seq import Seq

from respro.core.annotate_fasta import _annotate_from_alignment, _gapped_strings_from_cigar
from respro.core.sequence_matching import GeneMatch
from respro.db.models import AnnotatedVariant, CoverageGap

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
        'FASTA consensus: %d annotation(s) from %d gene(s), %d non-covered codon(s)',
        len(annotations), len(matches), len(coverage_gaps),
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
