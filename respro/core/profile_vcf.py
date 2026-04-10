"""
VCF variant remapping — remap variants from user-provided reference coordinates to internal CDS positions.
"""

from __future__ import annotations

import logging

from Bio.Seq import Seq

from respro.core.profile_helpers import _build_query_to_cds_map, _cds_pos_to_genomic_pos
from respro.core.sequence_matching import GeneMatch
from respro.db.models import VariantCall

logger = logging.getLogger(__name__)


def remap_variants(
    variants: list[VariantCall],
    matches: list[GeneMatch],
    query_sequence: str,
) -> tuple[list[VariantCall], list[str]]:
    """
    Filter and remap VCF variants from user query to internal reference coordinates.

    For each variant the function:

    1. Excludes positions outside any matched CDS region in the query.
    2. Maps the query position to a CDS position via the inverted CIGAR.
    3. Sanity-checks that the VCF REF anchor base agrees with the query FASTA.
    4. Stores the query codon context in every remapped variant for downstream annotation.
    5. Converts the CDS position to an internal genomic position and transforms
       REF/ALT alleles to the internal forward strand (anchor complement + payload RC
       for indels when the alignment strand and gene strand differ).

    :param variants: parsed VCF variants (0-based on user reference)
    :param matches: gene matches from FASTA alignment
    :param query_sequence: user query nucleotide sequence
    :return: (remapped_variants, warnings)
    """
    query_len = len(query_sequence)
    query_upper = query_sequence.upper()

    # Pre-build inverted coordinate maps for each match
    match_maps: list[tuple[GeneMatch, dict[int, int]]] = []
    for match in matches:
        q2c = _build_query_to_cds_map(
            match.cigar, match.query_start, match.query_end,
            match.strand, query_len,
        )
        match_maps.append((match, q2c))

    remapped: list[VariantCall] = []
    warnings: list[str] = []
    for var in variants:
        hit = False
        for match, q2c in match_maps:
            if var.pos not in q2c:
                continue

            cds_pos = q2c[var.pos]
            gene = match.gene

            # VCF position must be within query sequence
            if not (0 <= var.pos < query_len):
                continue

            # Sanity check: VCF anchor REF base must agree with query FASTA
            query_base = query_upper[var.pos]
            if query_base != var.ref[0].upper():
                warnings.append(
                    f'pos {var.pos + 1}: VCF REF anchor {var.ref[0]!r} \u2260 FASTA '
                    f'{query_base!r}'
                )
                continue

            # Convert CDS position to internal genomic position.
            genomic_pos = _cds_pos_to_genomic_pos(gene, cds_pos)

            # Transform REF/ALT to internal reference forward strand.
            # Complement is needed when alignment strand and gene strand differ.
            need_comp = (match.strand != gene.strand)
            ref_base = _transform_allele(var.ref, need_comp)
            alt_base = _transform_allele(var.alt, need_comp)

            query_ref_codon = _extract_query_ref_codon(q2c, query_upper, cds_pos)
            if match.strand == '-' and len(query_ref_codon) == 3:
                query_ref_codon = str(Seq(query_ref_codon).complement())

            remapped.append(VariantCall(
                chrom=var.chrom,
                pos=genomic_pos,
                ref=ref_base,
                alt=alt_base,
                allele_freq=var.allele_freq,
                depth=var.depth,
                filter_status=var.filter_status,
                query_ref_codon=query_ref_codon,
            ))
            hit = True
            break

        if not hit:
            logger.debug(
                'Variant at query pos %d excluded (outside mapped CDS)',
                var.pos,
            )

    logger.info(
        'Remapped %d of %d variant(s); %d warning(s)',
        len(remapped), len(variants), len(warnings),
    )
    return remapped, warnings


def _extract_query_ref_codon(
    query_to_cds: dict[int, int],
    query_sequence: str,
    cds_pos: int,
) -> str:
    """
    Build the three-base query codon for one CDS nucleotide position.

    :param query_to_cds: mapping of forward query position to CDS position
    :param query_sequence: query sequence (upper-case)
    :param cds_pos: CDS position (0-based)
    :return: three-base codon in CDS orientation, or empty string if incomplete
    """
    codon_start = (cds_pos // 3) * 3
    codon_bases: list[str] = []
    for codon_pos in range(codon_start, codon_start + 3):
        query_pos = next((q for q, c in query_to_cds.items() if c == codon_pos), None)
        if query_pos is None:
            return ''
        codon_bases.append(query_sequence[query_pos])
    return ''.join(codon_bases)


def _transform_allele(allele: str, need_comp: bool) -> str:
    """
    Transform a VCF allele to internal forward-strand orientation.

    For SNPs, complements the single base. For indels, complements the anchor
    base (allele[0]) and reverse-complements the payload (allele[1:]).

    :param allele: VCF allele string (REF or ALT)
    :param need_comp: True when alignment strand and gene strand differ
    :return: transformed allele string
    """
    if not need_comp or not allele:
        return allele
    anchor = str(Seq(allele[0]).complement())
    payload = str(Seq(allele[1:]).reverse_complement()) if len(allele) > 1 else ''
    return anchor + payload


def _complement_base(base: str) -> str:
    """Return the nucleotide complement using Biopython semantics."""
    return str(Seq(base).complement())
