"""
Tests for reference resolution.
"""

from pathlib import Path

import pytest

from respro.db.features import load_features_for_reference
from respro.db.schema import open_project_db
from respro.io.reference import (
    read_fasta,
)
from respro.io.vcf import parse_vcf


class TestLoadFeatures:
    def test_loads_features(self, project_db: Path):
        conn = open_project_db(project_db)
        features = load_features_for_reference(conn, 1)
        conn.close()

        assert len(features) == 1
        assert features[0].name == 'gag'
        assert features[0].start == 0
        assert features[0].end == 87


class TestReadFasta:
    def test_rna_sequence_converted_to_dna(self, tmp_path: Path) -> None:
        fasta = tmp_path / 'rna.fasta'
        fasta.write_text('>seq1\nAUGAAAGCUUUUGGCCCC\n')
        result = read_fasta(fasta)
        assert result == {'seq1': 'ATGAAAGCTTTTGGCCCC'}

    def test_dna_sequence_unchanged(self, tmp_path: Path) -> None:
        fasta = tmp_path / 'dna.fasta'
        fasta.write_text('>seq1\nATGAAAGCTTTTGGCCCC\n')
        result = read_fasta(fasta)
        assert result == {'seq1': 'ATGAAAGCTTTTGGCCCC'}

    def test_lowercase_rna_normalised(self, tmp_path: Path) -> None:
        fasta = tmp_path / 'rna_lower.fasta'
        fasta.write_text('>seq1\nauggcuacu\n')
        result = read_fasta(fasta)
        assert 'U' not in result['seq1']
        assert result['seq1'] == 'ATGGCTACT'


class TestParseVcfRnaNormalisation:
    def test_rna_ref_and_alt_converted(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'rna.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.1\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tU\tA\t.\tPASS\tAF=0.5\n'
            'seq1\t20\t.\tA\tU\t.\tPASS\tAF=0.9\n'
        )
        variants = parse_vcf(vcf)
        assert variants[0].ref == 'T'
        assert variants[1].alt == 'T'

    def test_dna_vcf_unchanged(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'dna.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.1\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tT\t.\tPASS\tAF=0.8\n'
        )
        variants = parse_vcf(vcf)
        assert variants[0].ref == 'A'
        assert variants[0].alt == 'T'

    def test_extracts_af_and_depth_from_format_fields(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'format_fields.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
            'seq1\t10\t.\tA\tT\t.\tPASS\t.\tGT:AD:DP\t0/1:3,9:12\n'
        )
        variants = parse_vcf(vcf)

        assert len(variants) == 1
        assert abs(variants[0].allele_freq - 0.75) < 1e-9
        assert variants[0].depth == 12

    def test_depth_missing_uses_sentinel_minus_one(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'no_depth.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tT\t.\tPASS\tAF=0.2\n'
        )
        variants = parse_vcf(vcf)

        assert len(variants) == 1
        assert variants[0].depth == -1

    def test_drops_variants_with_non_matching_chrom(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'mixed_chrom.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tT\t.\tPASS\tAF=0.6\n'
            'other_ref\t12\t.\tA\tG\t.\tPASS\tAF=0.7\n'
        )

        variants = parse_vcf(vcf, expected_query_name='seq1')

        assert len(variants) == 1
        assert variants[0].chrom == 'seq1'
        assert variants[0].pos == 9


class TestParseVcfAlleleSpecificArrays:
    """F3/F4: positional indexing and residual fallback for multiallelic AF/AD arrays."""

    def test_missing_entry_keeps_positional_indexing(self, tmp_path: Path) -> None:
        """F3: AF=0.1,.,0.3 must not drop the None and shift positions."""
        vcf = tmp_path / 'multi_missing.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1,.,0.3\n'
        )
        variants = parse_vcf(vcf)
        by_alt = {v.alt: v.allele_freq for v in variants}
        assert by_alt == {'C': pytest.approx(0.1), 'G': pytest.approx(0.6), 'T': pytest.approx(0.3)}

    def test_short_array_does_not_clamp_to_last_value(self, tmp_path: Path) -> None:
        """F4: AF=0.1,0.2 for ALT=C,G,T must give T the residual 0.7, not 0.2."""
        vcf = tmp_path / 'short_array.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1,0.2\n'
        )
        variants = parse_vcf(vcf)
        by_alt = {v.alt: v.allele_freq for v in variants}
        assert by_alt == {'C': pytest.approx(0.1), 'G': pytest.approx(0.2), 'T': pytest.approx(0.7)}

    def test_biallelic_missing_af_still_falls_back_to_one(self, tmp_path: Path) -> None:
        """A single ALT with AF=. keeps the legacy 'called = fully present' AF=1.0."""
        vcf = tmp_path / 'biallelic_missing.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC\t.\t.\tAF=.\n'
        )
        variants = parse_vcf(vcf)
        assert len(variants) == 1
        assert variants[0].allele_freq == pytest.approx(1.0)

    def test_multiple_missing_split_residual_equally(self, tmp_path: Path) -> None:
        """Two missing alleles share the residual equally (keeps per-site total at 1.0)."""
        vcf = tmp_path / 'two_missing.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1\n'
        )
        variants = parse_vcf(vcf)
        by_alt = {v.alt: v.allele_freq for v in variants}
        # residual = 1 - 0.1 = 0.9, split over G and T → 0.45 each
        assert by_alt == {'C': pytest.approx(0.1), 'G': pytest.approx(0.45), 'T': pytest.approx(0.45)}

    def test_known_alleles_summing_above_one_yield_zero_residual(self, tmp_path: Path) -> None:
        """When known AFs already sum to >= 1, the residual floor is 0."""
        vcf = tmp_path / 'over_one.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.6,0.6\n'
        )
        variants = parse_vcf(vcf)
        by_alt = {v.alt: v.allele_freq for v in variants}
        # known sum = 1.2 → residual = max(0, 1-1.2) = 0 → T = 0
        assert by_alt == {'C': pytest.approx(0.6), 'G': pytest.approx(0.6), 'T': pytest.approx(0.0)}

    def test_short_ad_array_residual_differs_from_clamp(self, tmp_path: Path) -> None:
        """F4: AD with one ALT depth for two ALTs — residual must not equal the clamped last value."""
        vcf = tmp_path / 'short_ad2.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.2\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\t.\tGT:AD\t0/1/2:8,2\n'
        )
        variants = parse_vcf(vcf)
        by_alt = {v.alt: v.allele_freq for v in variants}
        # C = 2/10 = 0.2. Old clamp: G = T = 2/10 = 0.2 (total 0.6).
        # Residual: G = T = (1 - 0.2)/2 = 0.4 (total 1.0).
        assert by_alt['C'] == pytest.approx(0.2, abs=1e-6)
        assert by_alt['G'] == pytest.approx(0.4, abs=1e-6)
        assert by_alt['T'] == pytest.approx(0.4, abs=1e-6)


class TestParseVcfAlleleArrayWarnings:
    """F3/F4: a warning is logged when an allele-specific array is short or has missing entries."""

    def test_short_af_array_logs_cardinality_warning(self, tmp_path: Path, caplog) -> None:
        """A short AF array (fewer values than ALTs) must log a cardinality warning."""
        vcf = tmp_path / 'short_warn.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1,0.2\n'
        )
        with caplog.at_level('WARNING', logger='respro.io.vcf'):
            parse_vcf(vcf)
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert any('AF' in r.getMessage() or 'cardinal' in r.getMessage().lower() for r in warnings), (
            f'expected a cardinality/AF warning, got: {[r.getMessage() for r in warnings]}'
        )

    def test_missing_af_entry_logs_warning(self, tmp_path: Path, caplog) -> None:
        """A missing AF entry (VCF .) must log a warning."""
        vcf = tmp_path / 'missing_warn.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1,.,0.3\n'
        )
        with caplog.at_level('WARNING', logger='respro.io.vcf'):
            parse_vcf(vcf)
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert any('AF' in r.getMessage() or 'missing' in r.getMessage().lower() for r in warnings), (
            f'expected a missing-AF warning, got: {[r.getMessage() for r in warnings]}'
        )

    def test_well_formed_af_array_logs_no_warning(self, tmp_path: Path, caplog) -> None:
        """A fully-specified AF array must not log an AF/cardinality warning."""
        vcf = tmp_path / 'clean.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,G,T\t.\t.\tAF=0.1,0.2,0.3\n'
        )
        with caplog.at_level('WARNING', logger='respro.io.vcf'):
            parse_vcf(vcf)
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert not any('AF' in r.getMessage() or 'cardinal' in r.getMessage().lower() for r in warnings), (
            f'unexpected AF warning: {[r.getMessage() for r in warnings]}'
        )


class TestParseVcfSymbolicAndBreakendAlts:
    """F5: symbolic and breakend ALTs must be skipped at the nucleotide-only boundary."""

    def test_symbolic_del_skipped(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'symbolic.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\t<DEL>\t.\t.\tAF=0.5\n'
        )
        assert parse_vcf(vcf) == []

    def test_symbolic_ins_skipped(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'symbolic_ins.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\t<INS>\t.\t.\tAF=0.5\n'
        )
        assert parse_vcf(vcf) == []

    def test_breakend_alt_skipped(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'breakend.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC[ref:100[\t.\t.\tAF=0.5\n'
        )
        assert parse_vcf(vcf) == []

    def test_mixed_record_skips_symbolic_keeps_nucleotide(self, tmp_path: Path) -> None:
        """A record with one nucleotide ALT and one symbolic ALT keeps only the nucleotide one."""
        vcf = tmp_path / 'mixed_symbolic.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC,<DEL>\t.\t.\tAF=0.4,0.6\n'
        )
        variants = parse_vcf(vcf)
        assert len(variants) == 1
        assert variants[0].alt == 'C'
        assert variants[0].allele_freq == pytest.approx(0.4)


class TestParseVcfFilterStatus:
    """F6: FILTER=. (unfiltered) must be distinguished from explicit PASS."""

    def test_explicit_pass_yields_pass(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'pass.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC\t.\tPASS\tAF=0.5\n'
        )
        variants = parse_vcf(vcf)
        assert variants[0].filter_status == 'PASS'

    def test_unfiltered_dot_yields_dot(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'unfiltered.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC\t.\t.\tAF=0.5\n'
        )
        variants = parse_vcf(vcf)
        assert variants[0].filter_status == '.'

    def test_failed_filters_joined(self, tmp_path: Path) -> None:
        vcf = tmp_path / 'failed.vcf'
        vcf.write_text(
            '##fileformat=VCFv4.3\n'
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##FILTER=<ID=LowQual,Description="Low quality">\n'
            '##FILTER=<ID=q10,Description="Q10">\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Alt allele freq">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'seq1\t10\t.\tA\tC\t.\tq10;LowQual\tAF=0.5\n'
        )
        variants = parse_vcf(vcf)
        assert variants[0].filter_status == 'q10;LowQual'
