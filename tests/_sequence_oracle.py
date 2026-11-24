"""
Independent sequence, coordinate, mixture, and BAM read-pool oracle for tests.

This module MUST NOT import from:
- ``respro.core.vcf_to_reference``
- ``respro.core.annotation``
- ``respro.core.combined_snp``
- ``respro.core.fasta_to_vcf``
- ``respro.core.vcf_coverage``

It computes reference expectations directly from first principles (sequence
construction, standard genetic code translation, interval arithmetic, analytical
Fréchet inequalities, and read-level molecule counting) so tests never derive
expected values from the implementation under test.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

_COMPLEMENT: dict[str, str] = {
    'A': 'T',
    'T': 'A',
    'C': 'G',
    'G': 'C',
    'N': 'N',
    'R': 'Y',
    'Y': 'R',
    'S': 'S',
    'W': 'W',
    'K': 'M',
    'M': 'K',
    'B': 'V',
    'V': 'B',
    'D': 'H',
    'H': 'D',
    '-': '-',
}

# NCBI Standard Genetic Code (Translation Table 1).
_STANDARD_CODON_TABLE: dict[str, str] = {
    'TTT': 'F', 'TTC': 'F', 'TTA': 'L', 'TTG': 'L',
    'CTT': 'L', 'CTC': 'L', 'CTA': 'L', 'CTG': 'L',
    'ATT': 'I', 'ATC': 'I', 'ATA': 'I', 'ATG': 'M',
    'GTT': 'V', 'GTC': 'V', 'GTA': 'V', 'GTG': 'V',
    'TCT': 'S', 'TCC': 'S', 'TCA': 'S', 'TCG': 'S',
    'CCT': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
    'ACT': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
    'GCT': 'A', 'GCC': 'A', 'GCA': 'A', 'GCG': 'A',
    'TAT': 'Y', 'TAC': 'Y', 'TAA': '*', 'TAG': '*',
    'CAT': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
    'AAT': 'N', 'AAC': 'N', 'AAA': 'K', 'AAG': 'K',
    'GAT': 'D', 'GAC': 'D', 'GAA': 'E', 'GAG': 'E',
    'TGT': 'C', 'TGC': 'C', 'TGA': '*', 'TGG': 'W',
    'CGT': 'R', 'CGC': 'R', 'CGA': 'R', 'CGG': 'R',
    'AGT': 'S', 'AGC': 'S', 'AGA': 'R', 'AGG': 'R',
    'GGT': 'G', 'GGC': 'G', 'GGA': 'G', 'GGG': 'G',
}


def complement_dna(seq: str) -> str:
    """Complement a DNA sequence without reversing it."""
    return ''.join(_COMPLEMENT.get(ch, 'N') for ch in seq.upper())


def reverse_complement_dna(seq: str) -> str:
    """Reverse-complement a DNA sequence independently of Biopython/respro."""
    return complement_dna(seq)[::-1]


def translate_codon_independent(codon: str) -> str:
    """Translate one 3-nt DNA codon via the NCBI Standard Genetic Code."""
    c = codon.upper()
    if len(c) != 3:
        return '?'
    if c in _STANDARD_CODON_TABLE:
        return _STANDARD_CODON_TABLE[c]
    if all(ch in _COMPLEMENT and ch != '-' for ch in c):
        return 'X'
    return '?'


def translate_coding_dna(coding_seq: str, *, codon_start: int = 0) -> str:
    """Translate complete codons starting at 0-based ``codon_start``."""
    trimmed = coding_seq.upper()[codon_start:]
    n_codons = len(trimmed) // 3
    return ''.join(translate_codon_independent(trimmed[i * 3:(i + 1) * 3]) for i in range(n_codons))


def classify_consequence_independent(ref_aa: str, alt_aa: str, codon_idx: int) -> str:
    """Classify the amino-acid consequence of a codon substitution."""
    if ref_aa == '?' or alt_aa == '?':
        return 'unknown'
    if ref_aa == alt_aa:
        return 'synonymous'
    if codon_idx == 0 and ref_aa == 'M':
        return 'start_lost'
    if alt_aa == '*' and ref_aa != '*':
        return 'stop_gained'
    if ref_aa == '*':
        return 'stop_lost'
    return 'missense'


@dataclass(frozen=True)
class OracleFeatureSpec:
    """Independent genomic/coding layout for a single- or multi-segment CDS."""

    segments: tuple[tuple[int, int], ...]  # genomic 0-based [start, end)
    strand: str
    codon_start: int = 0

    @property
    def coding_genomic_positions(self) -> tuple[int, ...]:
        """0-based genomic coordinates ordered 5'->3' in coding orientation."""
        ordered: list[int] = []
        segs = self.segments if self.strand == '+' else tuple(reversed(self.segments))
        for start, end in segs:
            if self.strand == '+':
                ordered.extend(range(start, end))
            else:
                ordered.extend(range(end - 1, start - 1, -1))
        return tuple(ordered)

    def genomic_to_cds(self, genomic_pos: int) -> int | None:
        """Map 0-based genomic position to 0-based CDS offset (pre-codon_start included)."""
        try:
            return self.coding_genomic_positions.index(genomic_pos)
        except ValueError:
            return None

    def cds_to_genomic(self, cds_pos: int) -> int | None:
        """Map 0-based CDS offset to 0-based genomic position."""
        positions = self.coding_genomic_positions
        if 0 <= cds_pos < len(positions):
            return positions[cds_pos]
        return None

    def codon_index(self, genomic_pos: int) -> int | None:
        """Return 0-based translated codon index, or None outside/before translation start."""
        cds_pos = self.genomic_to_cds(genomic_pos)
        if cds_pos is None or cds_pos < self.codon_start:
            return None
        return (cds_pos - self.codon_start) // 3

    def codon_offset(self, genomic_pos: int) -> int | None:
        """Return 0/1/2 offset within codon, or None outside/before translation start."""
        cds_pos = self.genomic_to_cds(genomic_pos)
        if cds_pos is None or cds_pos < self.codon_start:
            return None
        return (cds_pos - self.codon_start) % 3


def apply_sequence_edits(seq: str, edits: list[tuple[int, str, str]]) -> str:
    """Apply non-overlapping 0-based ``(pos, ref, alt)`` edits to ``seq``."""
    out = seq.upper()
    for pos, ref, alt in sorted(edits, key=lambda e: e[0], reverse=True):
        expected = out[pos:pos + len(ref)]
        if expected != ref.upper():
            raise AssertionError(f'Edit REF {ref!r} at {pos} != sequence slice {expected!r}')
        out = out[:pos] + alt.upper() + out[pos + len(ref):]
    return out


def derive_fixed_coding_haplotype_effect(
    *,
    internal_coding: str,
    supplied_seq: str,
    match_strand: str,
    codon_start: int,
    query_edits: list[tuple[int, str, str]],
    codon_idx: int,
) -> tuple[str, str, str, str, str]:
    """Derive ``(ref_codon, alt_codon, ref_aa, alt_aa, consequence)`` independently.

    Applies ``query_edits`` directly to ``supplied_seq`` in query coordinates,
    converts the resulting sample haplotype into coding orientation according to
    ``match_strand``, and compares codon ``codon_idx`` against ``internal_coding``.
    """
    sample_query = apply_sequence_edits(supplied_seq, query_edits)
    sample_coding = (
        sample_query if match_strand == '+' else reverse_complement_dna(sample_query)
    )
    nt_start = codon_start + codon_idx * 3
    ref_codon = internal_coding.upper()[nt_start:nt_start + 3]
    alt_codon = sample_coding[nt_start:nt_start + 3]
    ref_aa = translate_codon_independent(ref_codon)
    alt_aa = translate_codon_independent(alt_codon)
    consequence = classify_consequence_independent(ref_aa, alt_aa, codon_idx)
    return ref_codon, alt_codon, ref_aa, alt_aa, consequence


@dataclass(frozen=True)
class OracleHaplotype:
    """One fixed sample haplotype in coding orientation with its population weight."""

    coding_seq: str
    freq: float


def oracle_codon_mixture_frequencies(
    haplotypes: list[OracleHaplotype],
    codon_start: int,
    codon_idx: int,
) -> dict[str, tuple[str, float]]:
    """Aggregate exact codon-state frequencies from an explicit haplotype mixture."""
    nt_start = codon_start + codon_idx * 3
    totals: dict[str, float] = {}
    for hap in haplotypes:
        codon = hap.coding_seq.upper()[nt_start:nt_start + 3]
        totals[codon] = totals.get(codon, 0.0) + hap.freq
    return {
        codon: (translate_codon_independent(codon), freq)
        for codon, freq in totals.items()
    }


def oracle_disjoint_factor_frechet(
    factor_distributions: list[dict[str, float]],
    *,
    min_fraction: float = 2.0 / 3.0,
    eps: float = 1e-9,
) -> dict[str, tuple[float, float, float, bool]]:
    """Compute sharp Fréchet bounds over independent disjoint phase-factor distributions.

    Each element of ``factor_distributions`` maps a factor sub-codon key (e.g.
    ``'CG'`` for positions 0-1 or ``'T'`` for position 2) to its marginal probability
    mass within that factor. Keys across factors are concatenated in order to form
    the full codon string.
    """
    m = len(factor_distributions)
    results: dict[str, tuple[float, float, float, bool]] = {}
    for combo in itertools.product(*(d.items() for d in factor_distributions)):
        codon = ''.join(k for k, _ in combo)
        qs = [float(q) for _, q in combo]
        lower = max(0.0, sum(qs) - (m - 1))
        upper = min(qs)
        lower = min(lower, upper)
        ff = lower / upper if upper > 0 else 0.0
        accepted = lower > eps and ff >= min_fraction - eps
        results[codon] = (lower, upper, ff, accepted)
    return results


@dataclass(frozen=True)
class OracleSplicedQuery:
    """Independent coordinate/intron ground truth for an unspliced multi-exon query."""

    feature_spec: OracleFeatureSpec
    query_seq: str
    match_strand: str
    exon_only_region: str
    expected_exon_only_introns: tuple[tuple[int, int, int], ...]  # (cds_junction, exon_only_start, length)
    expected_full_intron_spans_coding: tuple[tuple[int, int], ...]  # [full_start, full_end) in coding region
    expected_full_intron_spans_forward: tuple[tuple[int, int], ...]  # [fwd_start, fwd_end) in forward query
    expected_q2c: dict[int, int]  # forward query pos -> 0-based CDS offset


def build_spliced_oracle_query(
    *,
    exon_seqs_coding: tuple[str, ...],
    intron_seqs_coding: tuple[str, ...],
    feature_strand: str,
    match_strand: str,
    query_flank_5: str = '',
    query_flank_3: str = '',
    codon_start: int = 0,
) -> OracleSplicedQuery:
    """Construct an unspliced multi-exon query and compute every coordinate map from scratch.

    ``exon_seqs_coding`` and ``intron_seqs_coding`` are supplied in **5'->3' coding
    orientation** (``len(intron_seqs_coding) == len(exon_seqs_coding) - 1``).
    When ``match_strand == '+'``, the aligned region in ``query_seq`` is in coding
    orientation; when ``match_strand == '-'``, it is reverse-complemented on ``query_seq``.
    """
    if len(intron_seqs_coding) != len(exon_seqs_coding) - 1:
        raise ValueError('Need len(exon_seqs_coding) - 1 introns')

    # Coding-orientation unspliced region.
    coding_parts: list[str] = []
    exon_only_introns: list[tuple[int, int, int]] = []
    full_introns_coding: list[tuple[int, int]] = []
    cds_cursor = 0
    full_cursor = 0
    coding_pos_to_cds: dict[int, int] = {}

    for idx, exon in enumerate(exon_seqs_coding):
        for offset in range(len(exon)):
            coding_pos_to_cds[full_cursor + offset] = cds_cursor + offset
        coding_parts.append(exon)
        cds_cursor += len(exon)
        full_cursor += len(exon)
        if idx < len(intron_seqs_coding):
            intron = intron_seqs_coding[idx]
            exon_only_introns.append((cds_cursor, cds_cursor, len(intron)))
            full_introns_coding.append((full_cursor, full_cursor + len(intron)))
            coding_parts.append(intron)
            full_cursor += len(intron)

    coding_unspliced = ''.join(coding_parts)
    region_forward = (
        coding_unspliced if match_strand == '+' else reverse_complement_dna(coding_unspliced)
    )
    flank_5_len = len(query_flank_5)
    query_seq = query_flank_5 + region_forward + query_flank_3
    region_len = len(region_forward)

    # Map forward query positions -> CDS positions and intron spans.
    q2c: dict[int, int] = {}
    fwd_introns: list[tuple[int, int]] = []
    if match_strand == '+':
        for coding_pos, cds_pos in coding_pos_to_cds.items():
            q2c[flank_5_len + coding_pos] = cds_pos
        for lo, hi in full_introns_coding:
            fwd_introns.append((flank_5_len + lo, flank_5_len + hi))
    else:
        for coding_pos, cds_pos in coding_pos_to_cds.items():
            fwd_pos = flank_5_len + (region_len - 1 - coding_pos)
            q2c[fwd_pos] = cds_pos
        for lo, hi in full_introns_coding:
            fwd_lo = flank_5_len + (region_len - hi)
            fwd_hi = flank_5_len + (region_len - lo)
            fwd_introns.append((fwd_lo, fwd_hi))

    # Build FeatureRecord genomic segments matching ``feature_strand``.
    # Note: genomic segments represent the internal reference's genomic exon layout.
    # For '+' feature_strand, segments follow coding exon order; for '-' feature_strand,
    # segments follow reversed coding exon order (segment_index=0 is genomic 5' / coding 3').
    segments: list[tuple[int, int]] = []
    if feature_strand == '+':
        seg_cursor = flank_5_len
        for idx, exon in enumerate(exon_seqs_coding):
            segments.append((seg_cursor, seg_cursor + len(exon)))
            seg_cursor += len(exon)
            if idx < len(intron_seqs_coding):
                seg_cursor += len(intron_seqs_coding[idx])
    else:
        # Genomic 5'->3' order reverses coding exons and coding introns.
        rev_exons = list(reversed(exon_seqs_coding))
        rev_introns = list(reversed(intron_seqs_coding))
        seg_cursor = flank_5_len
        for idx, exon in enumerate(rev_exons):
            segments.append((seg_cursor, seg_cursor + len(exon)))
            seg_cursor += len(exon)
            if idx < len(rev_introns):
                seg_cursor += len(rev_introns[idx])

    return OracleSplicedQuery(
        feature_spec=OracleFeatureSpec(
            segments=tuple(segments),
            strand=feature_strand,
            codon_start=codon_start,
        ),
        query_seq=query_seq,
        match_strand=match_strand,
        exon_only_region=''.join(exon_seqs_coding),
        expected_exon_only_introns=tuple(exon_only_introns),
        expected_full_intron_spans_coding=tuple(full_introns_coding),
        expected_full_intron_spans_forward=tuple(sorted(fwd_introns)),
        expected_q2c=q2c,
    )


def oracle_coverage_gaps(
    *,
    cds_len: int,
    codon_start: int,
    q2c: dict[int, int],
    query_depths: list[int],
    min_depth: int,
) -> list[tuple[int, int]]:
    """Independently compute merged 0-based ``(codon_start, codon_end)`` coverage gaps."""
    c2q = {cds_pos: qpos for qpos, cds_pos in q2c.items()}
    n_codons = max(0, (cds_len - codon_start) // 3)
    bad: list[int] = []
    for c_idx in range(n_codons):
        base_cds = codon_start + c_idx * 3
        for offset in range(3):
            qpos = c2q.get(base_cds + offset)
            if qpos is None or qpos < 0 or qpos >= len(query_depths) or query_depths[qpos] < min_depth:
                bad.append(c_idx)
                break
    if not bad:
        return []
    merged: list[tuple[int, int]] = []
    start = end = bad[0]
    for c_idx in bad[1:]:
        if c_idx == end + 1:
            end = c_idx
        else:
            merged.append((start, end))
            start = end = c_idx
    merged.append((start, end))
    return merged


@dataclass(frozen=True)
class OracleRead:
    """Synthetic read record in BAM forward-query coordinates."""

    qname: str
    seq: str
    start: int
    mapq: int = 60
    flag: int = 0


def oracle_bam_codon_frequencies(
    *,
    reads: list[OracleRead],
    ref_codon: str,
    codon_query_positions: list[tuple[int, int]],
    complement_bases: bool,
    min_mapq: int,
    min_depth: int,
) -> dict[str, float]:
    """Independent molecule-level BAM co-occurrence counter."""
    min_q = min(qp for _, qp in codon_query_positions)
    max_q = max(qp for _, qp in codon_query_positions)
    by_qname: dict[str, list[tuple[str, ...]]] = {}

    for read in reads:
        if read.flag & (0x100 | 0x200 | 0x400 | 0x800):
            continue
        if read.mapq < min_mapq:
            continue
        if read.start > min_q or read.start + len(read.seq) < max_q + 1:
            continue
        observed: list[str] = []
        for _cp, qp in codon_query_positions:
            base = read.seq[qp - read.start].upper()
            if complement_bases:
                base = complement_dna(base)
            observed.append(base)
        by_qname.setdefault(read.qname, []).append(tuple(observed))

    counted: list[tuple[str, ...]] = []
    for obs_list in by_qname.values():
        if len(obs_list) == 1:
            counted.append(obs_list[0])
        elif len(obs_list) == 2 and obs_list[0] == obs_list[1]:
            counted.append(obs_list[0])
        elif len(obs_list) > 2:
            counted.append(obs_list[0])

    total = len(counted)
    if total < min_depth:
        return {}

    counts: dict[str, int] = {}
    for obs in counted:
        bases = list(ref_codon.upper())
        for (cp, _qp), base in zip(codon_query_positions, obs):
            bases[cp] = base
        codon = ''.join(bases)
        counts[codon] = counts.get(codon, 0) + 1
    return {codon: count / total for codon, count in counts.items()}


# ─── Shared table-driven oracle cases (V1, V3, V4, V5) ───────────────────────

# V1: frame offsets 0, 1, 2 across both feature strands.
# Each entry: (codon_start, coding_internal, query_edit_coding_idx, new_coding_base, strand)
V1_FRAME_OFFSET_CASES: tuple[tuple[int, str, int, str, str], ...] = (
    (0, 'ATGAAAGAA', 4, 'C', '+'),
    (1, 'CAAAGAAAAA', 2, 'C', '+'),  # Exact V1 acceptance case (pos 2 A>C -> AAA->ACA, K->T)
    (2, 'GTAAAGAAAA', 3, 'G', '+'),
    (0, 'ATGAAAGAA', 4, 'C', '-'),
    (1, 'CAAAGAAAAA', 2, 'C', '-'),
    (2, 'GTAAAGAAAA', 3, 'G', '-'),
)

# V3: equal-length block substitutions (single-diff inside block, multi-diff in codon,
# codon-boundary spanning, reverse strand, partial AF).
V3_BLOCK_SUB_CASES: tuple[dict, ...] = (
    {
        'name': 'single_diff_in_block_aa_ag',
        'internal_coding': 'ATGAAATTT',
        'strand': '+',
        'match_strand': '+',
        'pos': 3,
        'ref': 'AA',
        'alt': 'AG',
        'af': 1.0,
        'expected_atom_count': 1,
        'expected_codon_pos': 1,
        'expected_ref_aa': 'K',
        'expected_alt_aa': 'R',
    },
    {
        'name': 'two_diffs_in_codon_aa_cg_full_af',
        'internal_coding': 'ATGAAATTT',
        'strand': '+',
        'match_strand': '+',
        'pos': 3,
        'ref': 'AA',
        'alt': 'CG',
        'af': 1.0,
        'expected_atom_count': 2,
        'expected_codon_pos': 1,
        'expected_ref_aa': 'K',
        'expected_alt_aa': 'R',
    },
    {
        'name': 'two_diffs_in_codon_aa_cg_partial_af',
        'internal_coding': 'ATGAAATTT',
        'strand': '+',
        'match_strand': '+',
        'pos': 3,
        'ref': 'AA',
        'alt': 'CG',
        'af': 0.4,
        'expected_atom_count': 2,
        'expected_codon_pos': 1,
        'expected_ref_aa': 'K',
        'expected_alt_aa': 'R',
    },
    {
        'name': 'codon_boundary_block',
        'internal_coding': 'ATGAAATTT',
        'strand': '+',
        'match_strand': '+',
        'pos': 5,
        'ref': 'AT',
        'alt': 'GC',
        'af': 0.7,
        'expected_atom_count': 2,
        'expected_codon_positions': (1, 2),
    },
)

V3_MULTIALLELIC_BASELINE_CASE: dict = {
    'internal_coding': 'ATGAAATTT',
    'supplied_coding': 'ATGGCATTT',  # baseline A3>G, A4>C
    'pos': 3,
    'ref': 'GC',
    'alts': (('AT', 0.4), ('AC', 0.3)),
    'residual_af': 0.3,
    'codon_idx': 1,
}

# V4: 3-exon multi-intron layouts (equal and unequal exons, both strands).
V4_MULTI_INTRON_CASES: tuple[dict, ...] = (
    {
        'name': 'three_equal_exons_plus_strand',
        'exon_seqs': ('ATGAAA', 'TTTGGG', 'CCCTAA'),
        'intron_seqs': ('ACACAC', 'GTGTGTGTG'),  # lengths 6 and 9
        'feature_strand': '+',
        'match_strand': '+',
    },
    {
        'name': 'three_equal_exons_minus_strand',
        'exon_seqs': ('ATGAAA', 'TTTGGG', 'CCCTAA'),
        'intron_seqs': ('ACACAC', 'GTGTGTGTG'),
        'feature_strand': '-',
        'match_strand': '-',
    },
    {
        'name': 'unequal_exons_minus_strand',
        'exon_seqs': ('ATGAAATTTGGG', 'CCCAAACCC', 'TTTTAA'),  # 12, 9, 6 nt
        'intron_seqs': ('GTGTGTGTG', 'ACACAC'),  # lengths 9 and 6
        'feature_strand': '-',
        'match_strand': '-',
    },
)

# V5: all 4 (match_strand, feature_strand) combinations for BAM codon co-occurrence.
V5_BAM_ORIENTATION_CASES: tuple[tuple[str, str], ...] = (
    ('+', '+'),
    ('+', '-'),
    ('-', '+'),
    ('-', '-'),
)

