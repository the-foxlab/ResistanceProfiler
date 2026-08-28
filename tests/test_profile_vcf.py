"""
Canonical VCF remap/annotation orientation tests (E1-E7).
"""

from __future__ import annotations

import logging
import re

import pytest

from respro.core.annotation import annotate_variants, reverse_complement
from respro.core.vcf_remap import _transform_allele, remap_variants
from respro.db.models import FeatureMatch, FeatureRecord, FeatureSegment, VariantCall


def _make_feature(*, strand: str) -> FeatureRecord:
    """Build the shared 18-nt test feature (MQVGN* coding sequence)."""
    return FeatureRecord(
        id=1,
        reference_id=1,
        name='feature',
        protein='P',
        start=0,
        end=18,
        strand=strand,
        codon_start=0,
        nt_sequence='ATGCAAGTCGGAAACTAA',
    )


def _make_split_feature(*, strand: str = '+') -> FeatureRecord:
    """Build a split CDS with a non-coding envelope gap between segments."""
    return FeatureRecord(
        id=2,
        reference_id=1,
        name='split_feature',
        protein='P',
        start=0,
        end=18,
        strand=strand,
        codon_start=0,
        nt_sequence='ATGAAAGGGTCC',
        segments=(
            FeatureSegment(segment_index=0, start=0, end=6),
            FeatureSegment(segment_index=1, start=12, end=18),
        ),
    )


def _make_match(feature: FeatureRecord, *, match_strand: str, query: str, cigar: str) -> FeatureMatch:
    """Build a controlled FeatureMatch for remap tests."""
    return FeatureMatch(
        feature=feature,
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
    ('feature_strand', 'match_strand', 'query_seq', 'query_token', 'expected_token', 'expected_aa'),
    [
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'A4G', 'A4G', 'Q1R'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'A5ATT', 'A5ATT', 'Q1fsX'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'CA3C', 'CA3C', 'Q1fsX'),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'C3CTTT', 'C3CTTT', ('Q1L', 'Q1Q*')),
        ('+', '+', 'ATGCAAGTCGGAAACTAA', 'CAAG3C', 'CAAG3C', ('Q1L', 'QV2Q')),
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
    feature_strand: str,
    match_strand: str,
    query_seq: str,
    query_token: str,
    expected_token: str,
    expected_aa: str,
) -> None:
    """Validate the canonical strand/orientation examples E1-E4."""
    feature = _make_feature(strand=feature_strand)
    match = _make_match(feature, match_strand=match_strand, query=query_seq, cigar='18M')
    var = _variant_from_token(query_token)

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == expected_token, (
        f'feature={feature_strand} match={match_strand} query={query_seq} '
        f'in={query_token} out={_token_from_variant(remapped[0])} expected={expected_token}'
    )

    anns = annotate_variants(remapped, [feature])
    if isinstance(expected_aa, tuple):
        assert len(anns) == len(expected_aa)
        for ann, exp in zip(anns, expected_aa):
            _assert_aa_token(ann, exp)
    else:
        assert len(anns) == 1
        _assert_aa_token(anns[0], expected_aa)


def test_example_e5_query_deletion_rc_orientation() -> None:
    """E5: query has deletion vs reference and query aligns as reverse-complement."""
    feature = _make_feature(strand='+')
    query_seq = 'TTAGTTTCCTTGCAT'
    match = _make_match(feature, match_strand='-', query=query_seq, cigar='6M3D9M')
    var = _variant_from_token('C8T')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G9A'

    anns = annotate_variants(remapped, [feature])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'G3R')


def test_example_e6_query_insertion_projection_and_no_projection() -> None:
    """E6: one SNP projects through insertion, one SNP in insertion has no projection."""
    feature = _make_feature(strand='+')
    query_seq = 'ATGCAATTTGTCGGAAACTAA'
    match = _make_match(feature, match_strand='+', query=query_seq, cigar='6M3I12M')
    projected = _variant_from_token('G9A')
    non_projected = _variant_from_token('T7C')

    remapped, warnings = remap_variants([projected, non_projected], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G6A'

    anns = annotate_variants(remapped, [feature])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'V2I')


def test_example_e7_mismatch_column_projection() -> None:
    """E7: SNP at mismatch column still projects and annotates correctly."""
    feature = _make_feature(strand='+')
    query_seq = 'ATGCAGGTCGGAAGCTAA'
    match = _make_match(feature, match_strand='+', query=query_seq, cigar='18M')
    var = _variant_from_token('G5T')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert not warnings
    assert len(remapped) == 1
    assert _token_from_variant(remapped[0]) == 'G5T'

    anns = annotate_variants(remapped, [feature])
    assert len(anns) == 1
    _assert_aa_token(anns[0], 'Q1H')


def test_overlapping_cds_matches_emit_one_remap_per_match() -> None:
    """A single query variant should remap once for each matching CDS map."""
    query_seq = 'ATGAAATTT'
    var = VariantCall(chrom='c', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100)

    feature_a = FeatureRecord(
        id=10,
        reference_id=1,
        name='orf_a',
        protein='A',
        start=0,
        end=9,
        strand='+',
        codon_start=0,
        nt_sequence='ATGAAATTT',
    )
    feature_b = FeatureRecord(
        id=11,
        reference_id=1,
        name='orf_b',
        protein='B',
        start=3,
        end=12,
        strand='+',
        codon_start=0,
        nt_sequence='ATGAAATTT',
    )

    matches = [
        _make_match(feature_a, match_strand='+', query=query_seq, cigar='9M'),
        _make_match(feature_b, match_strand='+', query=query_seq, cigar='9M'),
    ]

    remapped, warnings = remap_variants([var], matches, query_seq)

    assert not warnings
    assert len(remapped) == 2
    assert {v.pos for v in remapped} == {4, 7}


def test_anchor_ref_mismatch_produces_warning() -> None:
    """VCF REF anchor mismatch to query emits warning and excludes the variant."""
    feature = _make_feature(strand='+')
    query = 'ATGCAAGTCGGAAACTAA'
    match = _make_match(feature, match_strand='+', query=query, cigar='18M')
    var = VariantCall(chrom='c', pos=3, ref='T', alt='G', allele_freq=0.9, depth=100)

    remapped, warnings = remap_variants([var], [match], query)

    assert len(remapped) == 0
    assert len(warnings) == 1


def test_split_feature_remap_projects_second_segment_coordinates() -> None:
    """Split CDS remap must land in the second coding segment, not the envelope gap."""
    feature = _make_split_feature(strand='+')
    query = feature.nt_sequence
    match = _make_match(feature, match_strand='+', query=query, cigar='12M')
    var = VariantCall(chrom='c', pos=6, ref='G', alt='A', allele_freq=0.9, depth=50)

    remapped, warnings = remap_variants([var], [match], query)

    assert not warnings
    assert len(remapped) == 1
    assert remapped[0].pos == 12


def test_split_feature_envelope_gap_is_treated_as_non_coding() -> None:
    """A position inside the split-feature envelope gap must not annotate against the CDS."""
    feature = _make_split_feature(strand='+')
    gap_variant = VariantCall(chrom='c', pos=6, ref='A', alt='G', allele_freq=0.9, depth=50)

    annotations = annotate_variants([gap_variant], [feature])

    assert len(annotations) == 1
    assert annotations[0].feature_name == ''


def test_split_feature_envelope_gap_remap_logs_debug_skip_reason(caplog: pytest.LogCaptureFixture) -> None:
    """Split-feature envelope gaps should emit a debug skip reason during remap."""
    feature = _make_split_feature(strand='+')
    query = 'ATGAAATTTTTTGGGTCC'
    match = _make_match(feature, match_strand='+', query=query, cigar='6M6I6M')
    var = VariantCall(chrom='c', pos=6, ref='T', alt='G', allele_freq=0.9, depth=50)

    with caplog.at_level(logging.DEBUG, logger='respro.core.vcf_remap'):
        remapped, warnings = remap_variants([var], [match], query)

    assert not remapped
    assert not warnings
    assert any(
        'query pos 6' in message and 'no match / outside mapped CDS' in message
        for message in caplog.messages
    )


def test_minus_feature_reference_sequence_is_reverse_complement_of_coding_sequence() -> None:
    """Guardrail: minus-feature genomic-forward reference sequence used in examples."""
    assert reverse_complement('ATGCAAGTCGGAAACTAA') == 'TTAGTTTCCGACTTGCAT'


def test_anchor_changed_indel_is_split_forward_orientation() -> None:
    """Non-canonical indel with changed anchor is split into SNP + indel before remap."""
    feature = _make_feature(strand='+')
    query_seq = 'ATGCAAGTCGGAAACTAA'
    match = _make_match(feature, match_strand='+', query=query_seq, cigar='18M')
    # Encodes A->G at anchor plus 3-nt deletion payload in one record.
    var = _variant_from_token('AAGT4G')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert any('Split' in w for w in warnings)
    assert len(remapped) == 2
    tokens = {_token_from_variant(v) for v in remapped}
    assert 'A4G' in tokens
    assert 'AAGT4A' in tokens

    anns = annotate_variants(remapped, [feature])
    consequences = {a.consequence for a in anns}
    assert 'missense' in consequences
    assert any(c in consequences for c in ('deletion', 'frameshift'))


def test_anchor_changed_indel_is_split_reverse_orientation() -> None:
    """Reverse-orientation remap preserves both SNP and indel from changed-anchor record."""
    feature = _make_feature(strand='+')
    query_seq = 'TTAGTTTCCGACTTGCAT'
    match = _make_match(feature, match_strand='-', query=query_seq, cigar='18M')
    var = _variant_from_token('CAAA11G')

    remapped, warnings = remap_variants([var], [match], query_seq)

    assert any('Split' in w for w in warnings)
    assert len(remapped) == 2
    snp_count = sum(1 for v in remapped if len(v.ref) == 1 and len(v.alt) == 1)
    indel_count = sum(1 for v in remapped if len(v.ref) != len(v.alt))
    assert snp_count == 1
    assert indel_count == 1

    anns = annotate_variants(remapped, [feature])
    consequences = {a.consequence for a in anns}
    assert 'missense' in consequences
    assert any(c in consequences for c in ('deletion', 'frameshift'))


class TestUserRefCoordsPreserved:
    """remap_variants must carry the original user-reference coordinates through remap."""

    def test_snp_user_ref_coords_survive_forward_remap(self) -> None:
        """A forward-orientation SNP preserves user chrom/pos/ref/alt on the remapped variant."""
        feature = _make_feature(strand='+')
        query_seq = 'ATGCAAGTCGGAAACTAA'
        match = _make_match(feature, match_strand='+', query=query_seq, cigar='18M')
        var = VariantCall(
            chrom='userchr', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100,
        )

        remapped, warnings = remap_variants([var], [match], query_seq)

        assert not warnings
        assert len(remapped) == 1
        out = remapped[0]
        assert out.user_chrom == 'userchr'
        assert out.user_pos == 4
        assert out.user_ref == 'A'
        assert out.user_alt == 'G'

    def test_snp_user_ref_coords_survive_reverse_remap(self) -> None:
        """A reverse-orientation SNP preserves the original user coords (not the transformed ones)."""
        feature = _make_feature(strand='+')
        query_seq = 'TTAGTTTCCGACTTGCAT'
        match = _make_match(feature, match_strand='-', query=query_seq, cigar='18M')
        var = VariantCall(
            chrom='userchr', pos=13, ref='T', alt='C', allele_freq=0.9, depth=100,
        )

        remapped, warnings = remap_variants([var], [match], query_seq)

        assert not warnings
        assert len(remapped) == 1
        out = remapped[0]
        # Internal coords are transformed (A4G), but user coords stay as supplied.
        assert _token_from_variant(out) == 'A4G'
        assert out.user_chrom == 'userchr'
        assert out.user_pos == 13
        assert out.user_ref == 'T'
        assert out.user_alt == 'C'

    def test_indel_user_ref_coords_survive_reverse_remap(self) -> None:
        """A reverse-orientation indel preserves the original user coords through anchor switching."""
        feature = _make_feature(strand='+')
        query_seq = 'TTAGTTTCCGACTTGCAT'
        match = _make_match(feature, match_strand='-', query=query_seq, cigar='18M')
        var = VariantCall(
            chrom='userchr', pos=11, ref='C', alt='CAAA', allele_freq=0.9, depth=100,
        )

        remapped, warnings = remap_variants([var], [match], query_seq)

        assert not warnings
        assert len(remapped) == 1
        out = remapped[0]
        assert out.user_chrom == 'userchr'
        assert out.user_pos == 11
        assert out.user_ref == 'C'
        assert out.user_alt == 'CAAA'

    def test_split_anchor_changed_indel_preserves_user_coords_on_both_events(self) -> None:
        """Both split events of a changed-anchor indel carry the original user coords."""
        feature = _make_feature(strand='+')
        query_seq = 'ATGCAAGTCGGAAACTAA'
        match = _make_match(feature, match_strand='+', query=query_seq, cigar='18M')
        var = VariantCall(
            chrom='userchr', pos=4, ref='AAGT', alt='G', allele_freq=0.9, depth=100,
        )

        remapped, warnings = remap_variants([var], [match], query_seq)

        assert any('Split' in w for w in warnings)
        assert len(remapped) == 2
        for out in remapped:
            assert out.user_chrom == 'userchr'
            assert out.user_pos == 4
            assert out.user_ref == 'AAGT'
            assert out.user_alt == 'G'


def test_fasta_emitted_variant_has_empty_user_ref_coords() -> None:
    """FASTA-emitted variants leave user-ref coords empty (no supplied user reference)."""
    from respro.core.fasta_to_vcf import _make_variant_from_coding_nt

    feature = _make_feature(strand='+')
    var = _make_variant_from_coding_nt(feature, coding_nt_idx=3, ref='A', alt='G')

    assert var.user_chrom == ''
    assert var.user_pos == 0
    assert var.user_ref == ''
    assert var.user_alt == ''
