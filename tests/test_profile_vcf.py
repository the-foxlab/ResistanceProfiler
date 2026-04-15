"""
Canonical VCF remap/annotation orientation tests (E1-E7).
"""

from __future__ import annotations

import re

import pytest

from respro.core.annotation import annotate_variants, reverse_complement
from respro.core.vcf_remap import _transform_allele, remap_variants
from respro.db.models import GeneMatch, GeneRecord, VariantCall


def _make_gene(*, strand: str) -> GeneRecord:
    """Build the shared 18-nt test gene (MQVGN* coding sequence)."""
    return GeneRecord(
        id=1,
        reference_id=1,
        name='gene',
        protein='P',
        start=0,
        end=18,
        strand=strand,
        codon_start=0,
        nt_sequence='ATGCAAGTCGGAAACTAA',
    )


def _make_match(gene: GeneRecord, *, match_strand: str, query: str, cigar: str) -> GeneMatch:
    """Build a controlled GeneMatch for remap tests."""
    return GeneMatch(
        gene=gene,
        identity=1.0,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=len(query),
        strand=match_strand,
        cigar=cigar,
    )


def _variant_from_token(token: str) -> VariantCall:
    """Parse compact mutation token like A4G, AGTC5A, C11CAAA."""
    m = re.match(r'^([ACGT]+)(\d+)([ACGT]+)$', token)
    if m is None:
        raise ValueError(f'Invalid token: {token!r}')
    ref, pos_str, alt = m.groups()
    return VariantCall(chrom='c', pos=int(pos_str), ref=ref, alt=alt, allele_freq=0.9, depth=100)


def _token_from_variant(var: VariantCall) -> str:
    """Convert a VariantCall back to compact token form used in examples."""
    return f'{var.ref}{var.pos}{var.alt}'


def _assert_aa_token(ann, expected: str) -> None:
    """Assert expected AA token such as Q1R, Q1fsX, Q1?, QV2Q."""
    if expected.endswith('fsX'):
        assert ann.ref_aa == expected[0]
        assert ann.codon_pos == int(expected[1:-3])
        assert ann.alt_aa == f'{ann.ref_aa}fsX'
        assert ann.consequence == 'frameshift'
        return
    if expected.endswith('?'):
        assert ann.ref_aa == expected[0]
        assert ann.codon_pos == int(expected[1:-1])
        assert ann.alt_aa == '?'
        assert ann.consequence == 'inframe_complex'
        return

    m = re.match(r'^([A-Z*]+)(\d+)([A-Z*]+)$', expected)
    if m is None:
        raise ValueError(f'Invalid AA expectation: {expected!r}')
    ref_aa, pos_str, alt_aa = m.groups()
    assert ann.ref_aa == ref_aa
    if ann.consequence == 'deletion' and len(ref_aa) > len(alt_aa):
        assert ann.codon_pos + 1 == int(pos_str)
    else:
        assert ann.codon_pos == int(pos_str)
    assert ann.alt_aa == alt_aa


class TestTransformAllele:
    """Keep direct allele transformation helper checks."""

    def test_snp_need_comp_false(self) -> None:
        assert _transform_allele('A', need_comp=False) == 'A'

    def test_snp_need_comp_true(self) -> None:
        assert _transform_allele('A', need_comp=True) == 'T'
        assert _transform_allele('C', need_comp=True) == 'G'

    def test_indel_need_comp_true(self) -> None:
        assert _transform_allele('AGGG', need_comp=True) == 'TCCC'
        assert _transform_allele('CTGG', need_comp=True) == 'GCCA'


@pytest.mark.parametrize(
    ('gene_strand', 'match_strand', 'query_seq', 'query_token', 'expected_token', 'expected_aa'),
    [
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'A4G', 'A4G', 'Q1R'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'A5ATT', 'A5ATT', 'Q1fsX'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'CA3C', 'CA3C', 'Q1fsX'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'C3CTTT', 'C3CTTT', 'Q1?'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'CAAG3C', 'CAAG3C', 'Q1?'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'A5ATTT', 'A5ATTT', 'Q1QF'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'AGTC5A', 'AGTC5A', 'QV2Q'),
        ('+', '-', 'TTAGTTTCCGACTTGCAT', 'T13C', 'A4G', 'Q1R'),
        ('+', '-', 'TTAGTTTCCGACTTGCAT', 'C11CAAA', 'A5ATTT', 'Q1QF'),
        ('+', '-', 'TTAGTTTCCGACTTGCAT', 'CGAC8C', 'AGTC5A', 'QV2Q'),
        ('-', '-', 'TTAGTTTCCGACTTGCAT', 'T13C', 'T13C', 'Q1R'),
        ('-', '-', 'TTAGTTTCCGACTTGCAT', 'C11CAAA', 'C11CAAA', 'Q1QF'),
        ('-', '-', 'TTAGTTTCCGACTTGCAT', 'CGAC8C', 'CGAC8C', 'QV2Q'),
        ('-', '+', 'ATGCAAGTCGGAAACTAA', 'A4G', 'T13C', 'Q1R'),
        ('-', '+', 'ATGCAAGTCGGAAACTAA', 'A5ATTT', 'C11CAAA', 'Q1QF'),
        ('-', '+', 'ATGCAAGTCGGAAACTAA', 'AGTC5A', 'CGAC8C', 'QV2Q'),
    ],
)
def test_examples_e1_to_e4(
    gene_strand: str,
    match_strand: str,
    query_seq: str,
    query_token: str,
    expected_token: str,
    expected_aa: str,
) -> None:
    """Validate the canonical strand/orientation examples E1-E4."""
    gene = _make_gene(strand=gene_strand)
    match = _make_match(gene, match_strand=match_strand, query=query_seq, cigar='18M')
    var = _variant_from_token(query_token)

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == expected_token, (
        f'gene={gene_strand} match={match_strand} query={query_seq} '
        f'in={query_token} out={_token_from_variant(remapped[0])} expected={expected_token}'
    )

    anns = annotate_variants(remapped, [gene])
    assert len(anns) == 1
    _assert_aa_token(anns[0], expected_aa)


def test_example_e5_query_deletion_rc_orientation() -> None:
    """E5: query has deletion vs reference and query aligns as reverse-complement."""
    gene = _make_gene(strand='+')
    query_seq = 'TTAGTTTCCTTGCAT'
    match = _make_match(gene, match_strand='-', query=query_seq, cigar='6M3D9M')
    var = _variant_from_token('C8T')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G9A'

    anns = annotate_variants(remapped, [gene])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'G3R')


def test_example_e6_query_insertion_projection_and_no_projection() -> None:
    """E6: one SNP projects through insertion, one SNP in insertion has no projection."""
    gene = _make_gene(strand='+')
    query_seq = 'ATGCAATTTGTCGGAAACTAA'
    match = _make_match(gene, match_strand='+', query=query_seq, cigar='6M3I12M')
    projected = _variant_from_token('G9A')
    non_projected = _variant_from_token('T7C')

    remapped, warnings = remap_variants([projected, non_projected], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G6A'

    anns = annotate_variants(remapped, [gene])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'V2I')


def test_example_e7_mismatch_column_projection() -> None:
    """E7: SNP at mismatch column still projects and annotates correctly."""
    gene = _make_gene(strand='+')
    query_seq = 'ATGCAGGTCGGAAGCTAA'
    match = _make_match(gene, match_strand='+', query=query_seq, cigar='18M')
    var = _variant_from_token('G5T')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G5T'

    anns = annotate_variants(remapped, [gene])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'Q1H')


def test_anchor_ref_mismatch_produces_warning() -> None:
    """VCF REF anchor mismatch to query emits warning and excludes the variant."""
    gene = _make_gene(strand='+')
    query = 'ATGCAAGTCGGAAACTAA'
    match = _make_match(gene, match_strand='+', query=query, cigar='18M')
    var = VariantCall(chrom='c', pos=3, ref='T', alt='G', allele_freq=0.9, depth=100)

    remapped, warnings = remap_variants([var], [match], query)

    assert len(remapped) == 0
    assert len(warnings) == 1


def test_minus_gene_reference_sequence_is_reverse_complement_of_coding_sequence() -> None:
    """Guardrail: minus-gene genomic-forward reference sequence used in examples."""
    assert reverse_complement('ATGCAAGTCGGAAACTAA') == 'TTAGTTTCCGACTTGCAT'


def test_anchor_changed_indel_is_split_forward_orientation() -> None:
    """Non-canonical indel with changed anchor is split into SNP + indel before remap."""
    gene = _make_gene(strand='+')
    query_seq = 'ATGCAAGTCGGAAACTAA'
    match = _make_match(gene, match_strand='+', query=query_seq, cigar='18M')
    # Encodes A->G at anchor plus 3-nt deletion payload in one record.
    var = _variant_from_token('AAGT4G')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert any('Split' in w for w in warnings)
    assert len(remapped) == 2
    tokens = {_token_from_variant(v) for v in remapped}
    assert 'A4G' in tokens
    assert 'AAGT4A' in tokens

    anns = annotate_variants(remapped, [gene])
    consequences = {a.consequence for a in anns}
    assert 'missense' in consequences
    assert any(c in consequences for c in ('deletion', 'inframe_complex', 'frameshift'))


def test_anchor_changed_indel_is_split_reverse_orientation() -> None:
    """Reverse-orientation remap preserves both SNP and indel from changed-anchor record."""
    gene = _make_gene(strand='+')
    query_seq = 'TTAGTTTCCGACTTGCAT'
    match = _make_match(gene, match_strand='-', query=query_seq, cigar='18M')
    var = _variant_from_token('CAAA11G')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert any('Split' in w for w in warnings)
    assert len(remapped) == 2
    snp_count = sum(1 for v in remapped if len(v.ref) == 1 and len(v.alt) == 1)
    indel_count = sum(1 for v in remapped if len(v.ref) != len(v.alt))
    assert snp_count == 1
    assert indel_count == 1

    anns = annotate_variants(remapped, [gene])
    consequences = {a.consequence for a in anns}
    assert 'missense' in consequences
    assert any(c in consequences for c in ('deletion', 'inframe_complex', 'frameshift'))
