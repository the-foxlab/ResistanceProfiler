"""
VCF variant remapping — remap variants from user-provided reference coordinates to internal CDS positions.
"""

from __future__ import annotations

import logging

from Bio.Seq import Seq

from respro.core.alignment import cigar_to_coordinate_map
from respro.db.models import GeneMatch, GeneRecord, VariantCall

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
            match.strand, query_len, match.cds_start,
        )
        match_maps.append((match, q2c))

    remapped: list[VariantCall] = []
    warnings: list[str] = []
    remap_input_variants = _expand_anchor_changed_indels(variants, warnings)

    for var in remap_input_variants:
        hit = False
        for match, q2c in match_maps:
            if var.pos not in q2c:
                continue

            gene = match.gene
            reverse_to_reference = (match.strand != gene.strand)

            cds_pos = _map_variant_anchor_cds_pos(var, q2c, reverse_to_reference, gene.strand)
            if cds_pos is None:
                continue

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
            # Reverse-orientation indels also switch anchor side.
            if _is_indel(var.ref, var.alt) and reverse_to_reference:
                ref_base, alt_base = _remap_reverse_indel_alleles(var, gene, genomic_pos)
            else:
                ref_base = _transform_allele(var.ref, reverse_to_reference)
                alt_base = _transform_allele(var.alt, reverse_to_reference)

            codon_context_pos = cds_pos
            if _is_indel(var.ref, var.alt):
                codon_context_pos = _indel_anchor_cds_pos(cds_pos, len(var.ref), gene.strand)

            query_ref_codon = ''
            if codon_context_pos >= 0:
                query_ref_codon = _extract_query_ref_codon(q2c, query_upper, codon_context_pos)
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
        len(remapped), len(remap_input_variants), len(warnings),
    )
    return remapped, warnings


def _expand_anchor_changed_indels(
    variants: list[VariantCall],
    warnings: list[str],
) -> list[VariantCall]:
    """
    Split non-canonical indels with changed anchor base into two deterministic events.

    Some callers may encode a base substitution at the VCF anchor together with an indel,
    e.g. ``ATTT -> G``. If left as one event, anchor switching during remap can hide the
    substitution signal. To preserve information deterministically, such records are expanded
    into:

    1) anchor SNP: ``A -> G`` at the same query position
    2) canonical indel: same REF with ALT anchor reset to REF anchor (``ATTT -> A``)
    """
    expanded: list[VariantCall] = []
    split_count = 0
    for var in variants:
        if _is_indel(var.ref, var.alt) and var.ref and var.alt and var.ref[0] != var.alt[0]:
            split_count += 1
            expanded.append(
                VariantCall(
                    chrom=var.chrom,
                    pos=var.pos,
                    ref=var.ref[0],
                    alt=var.alt[0],
                    allele_freq=var.allele_freq,
                    depth=var.depth,
                    filter_status=var.filter_status,
                )
            )
            expanded.append(
                VariantCall(
                    chrom=var.chrom,
                    pos=var.pos,
                    ref=var.ref,
                    alt=var.ref[0] + var.alt[1:],
                    allele_freq=var.allele_freq,
                    depth=var.depth,
                    filter_status=var.filter_status,
                )
            )
            continue
        expanded.append(var)

    if split_count:
        warnings.append(
            f'Split {split_count} indel record(s) with changed VCF anchor into '
            'anchor SNP + canonical indel to preserve anchor substitution signal'
        )
    return expanded


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


def _is_indel(ref: str, alt: str) -> bool:
    """Return True when the VCF allele pair represents an insertion or deletion."""
    return len(ref) != 1 or len(alt) != 1


def _map_variant_anchor_cds_pos(
    var: VariantCall,
    query_to_cds: dict[int, int],
    reverse_to_reference: bool,
    gene_strand: str,
) -> int | None:
    """
    Map variant anchor to CDS position, including reverse-orientation indel anchor switching.

    For reverse-orientation indels, the VCF anchor switches to the opposite side after
    projection to the internal forward reference.
    """
    if var.pos not in query_to_cds:
        return None
    if not reverse_to_reference or not _is_indel(var.ref, var.alt):
        return query_to_cds[var.pos]

    query_ref_end = var.pos + len(var.ref) - 1
    cds_ref_end = query_to_cds.get(query_ref_end)
    if cds_ref_end is None:
        return None
    shift = -1 if gene_strand == '+' else 1
    mapped = cds_ref_end + shift
    if mapped < 0:
        return None
    return mapped


def _remap_reverse_indel_alleles(
    var: VariantCall,
    gene: GeneRecord,
    genomic_anchor_pos: int,
) -> tuple[str, str]:
    """
    Remap reverse-orientation indels using an internal-reference anchor base.

    The anchor nucleotide must come from the projected internal reference position,
    while the inserted/deleted payload is reverse-complemented.
    """
    anchor = _internal_forward_base(gene, genomic_anchor_pos)
    if len(var.alt) > len(var.ref):
        inserted = str(Seq(var.alt[1:]).reverse_complement())
        return anchor, anchor + inserted

    deleted = str(Seq(var.ref[1:]).reverse_complement())
    return anchor + deleted, anchor


def _internal_forward_base(gene: GeneRecord, genomic_pos: int) -> str:
    """Return the internal forward-reference nucleotide at one genomic position."""
    cds_pos = genomic_pos - gene.start if gene.strand == '+' else (gene.end - 1) - genomic_pos
    if cds_pos < 0 or cds_pos >= len(gene.nt_sequence):
        return ''
    coding_base = gene.nt_sequence.upper()[cds_pos]
    if gene.strand == '+':
        return coding_base
    return str(Seq(coding_base).complement())


def _indel_anchor_cds_pos(cds_pos: int, ref_len: int, gene_strand: str) -> int:
    """Return CDS anchor position used for indel amino-acid context extraction."""
    if gene_strand == '+':
        return cds_pos
    return cds_pos - ref_len



def _build_query_to_cds_map(
    cigar: str,
    query_start: int,
    query_end: int,
    strand: str,
    query_len: int,
    cds_start: int = 0,
) -> dict[int, int]:
    """
    Invert a CIGAR-based coordinate map to query-position → CDS-position.

    For '-' strand matches the CIGAR was built against the reverse-complement
    query, so positions are first converted back to forward-strand coordinates.

    :param cigar: CIGAR string from alignment
    :param query_start: 0-based forward-strand start (from GeneMatch)
    :param query_end: 0-based forward-strand end (from GeneMatch)
    :param strand: alignment strand ('+' or '-')
    :param query_len: total query sequence length
    :param cds_start: 0-based CDS offset where this alignment starts
    :return: mapping {forward_query_pos: cds_pos}
    """
    if strand == '+':
        cds_to_query = cigar_to_coordinate_map(cigar, query_start)
    else:
        # Recover RC-space start from the stored forward-strand end
        rc_start = query_len - query_end
        cds_to_query_rc = cigar_to_coordinate_map(cigar, rc_start)
        cds_to_query: dict[int, int | None] = {}
        for cds_pos, rc_pos in cds_to_query_rc.items():
            if rc_pos is not None:
                cds_to_query[cds_pos] = query_len - 1 - rc_pos
            else:
                cds_to_query[cds_pos] = None

    if cds_start:
        cds_to_query = {cds_pos + cds_start: qpos for cds_pos, qpos in cds_to_query.items()}

    # Invert: query_pos → cds_pos; skip deletions (None values)
    query_to_cds: dict[int, int] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is not None:
            query_to_cds[qpos] = cds_pos

    return query_to_cds


def _cds_pos_to_genomic_pos(gene: GeneRecord, cds_pos: int) -> int:
    """
    Convert a 0-based CDS nucleotide position to a 0-based internal genomic position.
    Inverse of ``GeneRecord.nt_offset()``.

    :param gene: gene record
    :param cds_pos: 0-based CDS nucleotide offset
    :return: 0-based genomic position on the internal reference
    """
    if gene.strand == '+':
        return gene.start + cds_pos
    return (gene.end - 1) - cds_pos
