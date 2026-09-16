"""
Tests for BAM-to-internal-reference coverage projection in VCF mode.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from respro.config.cli_settings import CLI_CONFIG
from respro.core.vcf_coverage import _ensure_bam_index, _depth_array_from_bam, compute_coverage_gaps_from_depth
from respro.db.models import FeatureMatch, FeatureRecord


def _make_feature() -> FeatureRecord:
    return FeatureRecord(
        id=1,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=9,
        strand='+',
        codon_start=0,
        nt_sequence='ATGAAATTT',
    )


def _make_match(feature: FeatureRecord, cigar: str, query_len: int) -> FeatureMatch:
    return FeatureMatch(
        feature=feature,
        identity=1.0,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=query_len,
        strand='+',
        cigar=cigar,
        cds_start=0,
    )


class TestVcfCoverageProjection:
    def test_cds_start_offset_is_respected_for_projection(self) -> None:
        feature = _make_feature()
        # Alignment starts at CDS offset 3, so query nt 0 maps to CDS nt 3.
        match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=1.0,
            query_coverage=1.0,
            query_start=0,
            query_end=6,
            strand='+',
            cigar='6M',
            cds_start=3,
        )
        # Good depth only over mapped region (query 0..5).
        depths = [20] * 6

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=6)

        # Codon 0 (CDS nt 0..2) is unmappable, codons 1-2 are covered.
        assert len(gaps) == 1
        assert (gaps[0].codon_start, gaps[0].codon_end) == (0, 0)

    def test_marks_codon_non_covered_when_internal_nt_not_projectable(self) -> None:
        feature = _make_feature()
        # One deletion in query relative to CDS: codon 2 loses one projected nt.
        match = _make_match(feature, cigar='4M1D4M', query_len=8)
        depths = [30] * 8

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=8)

        assert len(gaps) == 1
        assert gaps[0].feature_name == 'gag'
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 1

    def test_marks_codon_non_covered_when_depth_below_threshold(self) -> None:
        feature = _make_feature()
        match = _make_match(feature, cigar='9M', query_len=9)
        depths = [20, 20, 20, 20, 20, 20, 20, 2, 20]

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=9)

        assert len(gaps) == 1
        assert gaps[0].codon_start == 2
        assert gaps[0].codon_end == 2

    def test_merges_adjacent_non_covered_codons(self) -> None:
        feature = _make_feature()
        match = _make_match(feature, cigar='9M', query_len=9)
        # Codon 2 and 3 are below depth threshold.
        depths = [20, 20, 20, 2, 2, 2, 1, 1, 1]

        gaps = compute_coverage_gaps_from_depth(depths, [match], min_depth=10, query_len=9)

        assert len(gaps) == 1
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 2


class TestBamIndexHandling:
    def test_ensure_bam_index_skips_when_bai_exists(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bam_path = tmp_path / 'sample.bam'
        bam_path.write_bytes(b'\x1f\x8b\x00\x00')
        bam_path.with_suffix('.bam.bai').write_bytes(b'index')

        called = {'value': False}

        def _fake_index(_: str) -> None:
            called['value'] = True

        monkeypatch.setattr('respro.core.vcf_coverage.pysam.index', _fake_index)

        _ensure_bam_index(bam_path)

        assert called['value'] is False

    def test_ensure_bam_index_creates_missing_index(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bam_path = tmp_path / 'sample.bam'
        bam_path.write_bytes(b'\x1f\x8b\x00\x00')

        called = {'value': False}

        def _fake_index(path: str) -> None:
            called['value'] = True
            Path(f'{path}.bai').write_bytes(b'index')

        monkeypatch.setattr('respro.core.vcf_coverage.pysam.index', _fake_index)

        _ensure_bam_index(bam_path)

        assert called['value'] is True
        assert bam_path.with_suffix('.bam.bai').exists()

    def test_ensure_bam_index_raises_actionable_error_on_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bam_path = tmp_path / 'sample.bam'
        bam_path.write_bytes(b'\x1f\x8b\x00\x00')

        def _fake_index(_: str) -> None:
            raise RuntimeError('cannot index unsorted BAM')

        monkeypatch.setattr('respro.core.vcf_coverage.pysam.index', _fake_index)

        with pytest.raises(ValueError, match='coordinate-sorted'):
            _ensure_bam_index(bam_path)


class TestBamBaseQualityThreshold:
    """Tests for the bam_base_quality_threshold config wiring (feature magic-numbers-to-toml M2)."""

    @staticmethod
    def _fake_bam(count_coverage_kwargs: dict) -> MagicMock:
        """Build a fake pysam AlignmentFile capturing count_coverage kwargs."""
        bam = MagicMock()
        # count_coverage returns a 4-tuple of array-like per-base counts.
        bam.count_coverage.return_value = (
            [0], [0], [0], [0],
        )
        return bam

    def test_depth_array_uses_configured_quality_threshold(self) -> None:
        """_depth_array_from_bam should pass the configured quality_threshold to count_coverage."""
        bam = self._fake_bam({})
        _depth_array_from_bam(bam, contig='chr1', query_len=1, bam_base_quality_threshold=20)
        _, kwargs = bam.count_coverage.call_args
        assert kwargs['quality_threshold'] == 20

    def test_depth_array_default_quality_threshold_is_config_default(self) -> None:
        """Default bam_base_quality_threshold should equal CLI_CONFIG.codon.bam_base_quality_threshold (0)."""
        bam = self._fake_bam({})
        _depth_array_from_bam(bam, contig='chr1', query_len=1)
        _, kwargs = bam.count_coverage.call_args
        assert kwargs['quality_threshold'] == CLI_CONFIG.codon.bam_base_quality_threshold
        assert kwargs['quality_threshold'] == 0

    def test_depth_array_sums_per_base_counts(self) -> None:
        """_depth_array_from_bam should sum the four base counts per position."""
        bam = MagicMock()
        bam.count_coverage.return_value = (
            [2, 0], [1, 3], [0, 1], [0, 0],
        )
        depths = _depth_array_from_bam(bam, contig='chr1', query_len=2, bam_base_quality_threshold=0)
        assert depths == [3, 4]
