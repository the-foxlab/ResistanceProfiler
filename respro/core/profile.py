"""
FASTA-based profiling — remap VCF variants from a user-provided reference
to internal CDS coordinates for amino acid annotation.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from Bio.Seq import Seq

from respro.core.sequence_matching import (
    GeneMatch,
    cigar_to_coordinate_map,
    load_cached_mappings,
    load_genes_with_rules,
    match_query_to_genes,
    sequence_checksum,
    store_mappings,
)
from respro.db.models import GeneRecord, VariantCall
from respro.io.reference import read_fasta

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def resolve_fasta_query(
    conn: sqlite3.Connection,
    fasta_path: Path,
    *,
    min_identity: float = 0.80,
    min_coverage: float = 0.90,
    use_cache: bool = True,
) -> tuple[str, str, list[GeneMatch]]:
    """
    Read a user FASTA and align to internal CDS annotations.

    :param conn: project database connection
    :param fasta_path: path to single-record user FASTA
    :param min_identity: minimum nucleotide identity
    :param min_coverage: minimum CDS coverage fraction
    :param use_cache: if True, reuse/store mapping cache in project DB
    :return: (query_name, query_sequence, gene_matches)
    """
    seqs = read_fasta(fasta_path)
    if not seqs:
        raise ValueError(f'No sequences found in {fasta_path}')
    if len(seqs) > 1:
        raise ValueError(
            f'Expected single-record FASTA, got {len(seqs)} records in '
            f'{fasta_path}. Multi-record FASTA is not yet supported for profiling.'
        )

    query_name, query_seq = next(iter(seqs.items()))
    chk = sequence_checksum(query_seq)

    if use_cache:
        cached = _load_cached_query_matches(conn, query_name, query_seq, chk)
        if cached is not None:
            logger.info('Using cached gene mappings for %r', query_name)
            return query_name, query_seq, cached

    genes = load_genes_with_rules(conn)
    if not genes:
        raise ValueError('No genes with resistance rules in project database')

    matches = match_query_to_genes(
        query_seq, genes,
        min_identity=min_identity,
        min_coverage=min_coverage,
    )
    if not matches:
        raise ValueError(
            f'No CDS matches above thresholds '
            f'(identity>={min_identity:.0%}, coverage>={min_coverage:.0%}) '
            f'in {fasta_path.name}'
        )

    if use_cache:
        store_mappings(conn, query_name, query_seq, chk, matches)
    return query_name, query_seq, matches


def resolve_cached_query_reference(
    conn: sqlite3.Connection,
    query_header: str,
) -> tuple[str, str, list[GeneMatch]]:
    """
    Reuse a previously stored query reference by its header.

    :param conn: project database connection
    :param query_header: exact stored query header
    :return: (query_name, query_sequence, gene_matches)
    """
    header = query_header.strip()
    if not header:
        raise ValueError('Query reference header must not be empty')

    rows = conn.execute(
        'SELECT id, name, sequence, checksum '
        'FROM query_reference '
        'WHERE name = ? '
        'ORDER BY id DESC',
        (header,),
    ).fetchall()
    if not rows:
        available_headers = _list_cached_query_headers(conn)
        if available_headers:
            raise ValueError(
                f'Stored query reference header {header!r} not found. '
                f'Available cached headers: {", ".join(available_headers)}'
            )
        raise ValueError(
            f'Stored query reference header {header!r} not found. '
            'No cached query-reference mappings are available in this project database.'
        )

    cached_rows: list[tuple[sqlite3.Row, list[GeneMatch]]] = []
    for row in rows:
        matches = load_cached_mappings(conn, row['checksum'])
        if matches:
            cached_rows.append((row, matches))

    if not cached_rows:
        raise ValueError(
            f'Stored query reference header {header!r} exists, but no cached gene mappings '
            'are available for it. Re-run profiling once with --ref-fasta to create them.'
        )

    if len(cached_rows) > 1:
        details = ', '.join(
            f"{row['checksum'][:12]}… ({len(matches)} mapping(s))"
            for row, matches in cached_rows
        )
        raise ValueError(
            f'Stored query reference header {header!r} is ambiguous. '
            f'Multiple cached sequences share this header: {details}. '
            'Please profile with --ref-fasta to resolve the correct query reference.'
        )

    row, matches = cached_rows[0]
    return row['name'], row['sequence'], matches


def pick_best_reference_id(matches: list[GeneMatch]) -> int:
    """
    Select the most likely internal reference from FASTA gene matches.

    The best single gene match defines the reference. Sorting keys are:
    identity desc, coverage desc, gene id asc.

    :param matches: accepted gene matches
    :return: internal reference id
    """
    if not matches:
        raise ValueError('No FASTA gene matches available for reference selection')

    best = max(matches, key=lambda m: (m.identity, m.coverage, -m.gene.id))
    return best.gene.reference_id


def select_matches_for_reference(
    matches: list[GeneMatch],
    reference_id: int,
) -> list[GeneMatch]:
    """
    Keep only matches belonging to one internal reference.

    :param matches: gene matches from FASTA alignment
    :param reference_id: selected internal reference id
    :return: filtered matches for the selected reference
    """
    selected = [match for match in matches if match.gene.reference_id == reference_id]
    if not selected:
        raise ValueError(f'No FASTA gene matches for reference_id={reference_id}')
    return selected


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
    3. Sanity-checks that the VCF REF base agrees with the query FASTA.
    4. For SNPs, stores query codon context for downstream annotation.
    5. Converts the CDS position to an internal genomic position and transforms
       REF/ALT bases to the internal forward strand.

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

            # Sanity check: VCF REF must agree with query FASTA
            query_base = query_upper[var.pos]
            if query_base != var.ref.upper():
                warnings.append(
                    f'pos {var.pos + 1}: VCF REF {var.ref!r} \u2260 FASTA '
                    f'{query_base!r}'
                )
                continue

            # Convert CDS position to internal genomic position.
            genomic_pos = _cds_pos_to_genomic_pos(gene, cds_pos)

            # Transform REF/ALT to internal reference forward strand.
            # Complement is needed when alignment strand and gene strand differ.
            need_comp = (match.strand != gene.strand)
            ref_base = _complement_base(var.ref) if need_comp else var.ref
            alt_base = _complement_base(var.alt) if need_comp else var.alt

            query_ref_codon = ''
            if len(var.ref) == 1 and len(var.alt) == 1:
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


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _load_cached_query_matches(
    conn: sqlite3.Connection,
    query_name: str,
    query_sequence: str,
    checksum: str,
) -> list[GeneMatch] | None:
    """
    Reuse cached mappings if the query is already known by checksum or header.

    Header reuse is only accepted when the stored sequence is identical to the
    current FASTA sequence.

    :param conn: project database connection
    :param query_name: FASTA header / record id
    :param query_sequence: FASTA sequence
    :param checksum: SHA-256 checksum of the FASTA sequence
    :return: cached matches or None
    """
    cached = load_cached_mappings(conn, checksum)
    if cached is not None:
        return cached

    row = conn.execute(
        'SELECT checksum, sequence FROM query_reference WHERE name = ? ORDER BY id DESC LIMIT 1',
        (query_name,),
    ).fetchone()
    if row is None:
        return None

    if (row['sequence'] or '').upper() != query_sequence.upper():
        return None

    return load_cached_mappings(conn, row['checksum'])


def _list_cached_query_headers(conn: sqlite3.Connection) -> list[str]:
    """Return stored query headers that already have cached gene mappings."""
    rows = conn.execute(
        'SELECT DISTINCT qr.name '
        'FROM query_reference qr '
        'JOIN query_gene_mapping qgm ON qgm.query_ref_id = qr.id '
        'ORDER BY qr.name'
    ).fetchall()
    return [row['name'] for row in rows if (row['name'] or '').strip()]


def _build_query_to_cds_map(
    cigar: str,
    query_start: int,
    query_end: int,
    strand: str,
    query_len: int,
) -> dict[int, int]:
    """
    Invert a CIGAR-based coordinate map to query-position \u2192 CDS-position.

    For '-' strand matches the CIGAR was built against the reverse-complement
    query, so positions are first converted back to forward-strand coordinates.

    :param cigar: CIGAR string from alignment
    :param query_start: 0-based forward-strand start (from GeneMatch)
    :param query_end: 0-based forward-strand end (from GeneMatch)
    :param strand: alignment strand ('+' or '-')
    :param query_len: total query sequence length
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

    # Invert: query_pos → cds_pos; skip deletions (None values)
    query_to_cds: dict[int, int] = {}
    for cds_pos, qpos in cds_to_query.items():
        if qpos is not None:
            query_to_cds[qpos] = cds_pos

    return query_to_cds


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


def _cds_pos_to_genomic_pos(gene: GeneRecord, cds_pos: int) -> int:
    """
    Convert a 0-based CDS nucleotide position to a 0-based internal genomic
    position.  Inverse of ``GeneRecord.nt_offset()``.

    :param gene: gene record
    :param cds_pos: 0-based CDS nucleotide offset
    :return: 0-based genomic position on the internal reference
    """
    if gene.strand == '+':
        return gene.start + cds_pos
    return (gene.end - 1) - cds_pos


def _complement_base(base: str) -> str:
    """Return the nucleotide complement using Biopython semantics."""
    return str(Seq(base).complement())
