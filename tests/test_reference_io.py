"""
Tests for reference resolution.
"""

from pathlib import Path

from respro.db.schema import open_project_db
from respro.io.reference import (
    load_genes_for_reference,
    read_fasta,
)
from respro.io.vcf import parse_vcf


class TestLoadGenes:
    def test_loads_genes(self, project_db: Path):
        conn = open_project_db(project_db)
        genes = load_genes_for_reference(conn, 1)
        conn.close()

        assert len(genes) == 1
        assert genes[0].name == 'gag'
        assert genes[0].start == 0
        assert genes[0].end == 87


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
