"""
Tests for FASTA-based profiling — coordinate remapping, FASTA consensus profiling,
and end-to-end CLI workflow.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Literal

import pytest
from Bio.Seq import Seq
from conftest import TINY_REF_NAME, TINY_REF_SEQ
from typer.testing import CliRunner

from respro.cli.main import app
from respro.core.alignment import (
    _align_cds_to_query,
    match_query_to_genes,
    sequence_checksum,
    store_mappings,
)
from respro.core.fasta_profile import (
    _annotate_from_alignment,
    _expand_iupac_codon,
    _make_variant,
    _make_variant_from_coding_nt,
    profile_fasta_consensus,
)
from respro.core.query import (
    resolve_cached_query_reference,
    resolve_fasta_query,
)
from respro.core.vcf_remap import (
    _build_query_to_cds_map,
    _cds_pos_to_genomic_pos,
    remap_variants,
)
from respro.db.models import GeneMatch, GeneRecord, GeneSegment, VariantCall
from respro.db.schema import create_schema, open_project_db

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fasta_db(tmp_path: Path) -> Path:
    """Project DB with the tiny reference and a K2E resistance rule."""
    db_path = tmp_path / 'fasta_project.db'
    conn = create_schema(db_path)
    conn.execute(
        'INSERT INTO project (name, schema_version) VALUES (?, ?)',
        ('FASTA Test', 15),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
        (1, TINY_REF_NAME, len(TINY_REF_SEQ)),
    )
    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, '
        'nt_sequence) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gag', 'Gag', 0, 87, '+', TINY_REF_SEQ),
    )
    conn.execute(
        'INSERT INTO drug (project_id, name) VALUES (?, ?)',
        (1, 'TestDrug'),
    )
    # Rule: 0-based codon 1 = K, mutation E → resistant
    conn.execute(
        'INSERT INTO resistance_rule '
        '(gene_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def fasta_db_multi_reference(tmp_path: Path) -> Path:
    """Project DB with two references where only refB should match the FASTA."""
    db_path = tmp_path / 'fasta_project_multi.db'
    conn = create_schema(db_path)
    conn.execute(
        'INSERT INTO project (name, schema_version) VALUES (?, ?)',
        ('FASTA Test Multi', 15),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refA', 30, 'Organism A'),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refB', 30, 'Organism B'),
    )

    ref_a_seq = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
    ref_b_seq = 'CCCCGGGAAATTTCCCGGGAAATTTCCCGG'

    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gagA', 'GagA', 0, 30, '+', ref_a_seq),
    )
    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (2, 'gagB', 'GagB', 0, 30, '+', ref_b_seq),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
    conn.execute(
        'INSERT INTO resistance_rule '
        '(gene_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.execute(
        'INSERT INTO resistance_rule '
        '(gene_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (2, 1, 1, 'P', 'A', 'resistant'),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def gene_fwd() -> GeneRecord:
    """Forward-strand gene spanning the tiny reference."""
    return GeneRecord(
        id=1, reference_id=1, name='gag', protein='Gag',
        start=0, end=87, strand='+', codon_start=0,
        nt_sequence=TINY_REF_SEQ,
    )


def _split_gene(*, strand: str) -> GeneRecord:
    """Build a two-segment CDS with a non-coding envelope gap."""
    return GeneRecord(
        id=99,
        reference_id=1,
        name=f'split_{strand}',
        protein='Split',
        start=0,
        end=18,
        strand=strand,
        codon_start=0,
        nt_sequence='ATGAAAGGGTCC',
        segments=(
            GeneSegment(segment_index=0, start=0, end=6),
            GeneSegment(segment_index=1, start=12, end=18),
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Unit tests: coordinate mapping helpers
# ──────────────────────────────────────────────────────────────────────

class TestBuildQueryToCdsMap:
    def test_forward_perfect_match(self) -> None:
        """30M at query_start=10 maps query 10–39 to CDS 0–29."""
        q2c = _build_query_to_cds_map('30M', 10, 40, '+', 100)
        assert q2c[10] == 0
        assert q2c[39] == 29
        assert 9 not in q2c
        assert 40 not in q2c

    def test_forward_with_insertion(self) -> None:
        """5M3I5M: CDS 10 bases, query 13; insertion positions excluded."""
        q2c = _build_query_to_cds_map('5M3I5M', 0, 13, '+', 20)
        assert q2c[0] == 0
        assert q2c[4] == 4
        # After 3-base insertion, CDS pos 5 maps to query pos 8
        assert q2c[8] == 5
        # Insertion positions should NOT appear in the map
        assert 5 not in q2c
        assert 6 not in q2c
        assert 7 not in q2c

    def test_forward_with_deletion(self) -> None:
        """5M2D5M: CDS 12 bases, query 10; deletion positions excluded."""
        q2c = _build_query_to_cds_map('5M2D5M', 0, 10, '+', 20)
        assert q2c[0] == 0
        assert q2c[4] == 4
        # CDS positions 5–6 are deletions (no query position)
        assert q2c[5] == 7
        assert q2c[9] == 11

    def test_reverse_strand(self) -> None:
        """10M on '-' strand; forward start=80, end=90, query_len=100."""
        q2c = _build_query_to_cds_map('10M', 80, 90, '-', 100)
        # CDS pos 0 → RC pos 10 → fwd pos 99-10 = 89
        assert q2c[89] == 0
        # CDS pos 9 → RC pos 19 → fwd pos 99-19 = 80
        assert q2c[80] == 9


class TestCdsPosToGenomic:
    def test_forward_strand(self) -> None:
        gene = GeneRecord(
            id=1, reference_id=1, name='g', protein='',
            start=10, end=40, strand='+', codon_start=0,
        )
        assert _cds_pos_to_genomic_pos(gene, 0) == 10
        assert _cds_pos_to_genomic_pos(gene, 29) == 39

    def test_reverse_strand(self) -> None:
        gene = GeneRecord(
            id=1, reference_id=1, name='g', protein='',
            start=10, end=40, strand='-', codon_start=0,
        )
        assert _cds_pos_to_genomic_pos(gene, 0) == 39
        assert _cds_pos_to_genomic_pos(gene, 29) == 10

    def test_roundtrip_with_nt_offset(self) -> None:
        """cds_pos_to_genomic_0based should be the inverse of GeneRecord.nt_offset."""
        gene = GeneRecord(
            id=1, reference_id=1, name='g', protein='',
            start=5, end=35, strand='+', codon_start=0,
        )
        for cds in range(30):
            genomic = _cds_pos_to_genomic_pos(gene, cds)
            assert gene.nt_offset(genomic) == cds

    def test_roundtrip_reverse_strand(self) -> None:
        gene = GeneRecord(
            id=1, reference_id=1, name='g', protein='',
            start=5, end=35, strand='-', codon_start=0,
        )
        for cds in range(30):
            genomic = _cds_pos_to_genomic_pos(gene, cds)
            assert gene.nt_offset(genomic) == cds

    def test_split_gene_roundtrip_forward_strand(self) -> None:
        gene = _split_gene(strand='+')

        assert gene.contains(0)
        assert gene.contains(12)
        assert not gene.contains(6)
        assert gene.nt_offset(0) == 0
        assert gene.nt_offset(5) == 5
        assert gene.nt_offset(12) == 6
        assert gene.nt_offset(17) == 11
        assert gene.nt_offset(6) is None
        assert _cds_pos_to_genomic_pos(gene, 0) == 0
        assert _cds_pos_to_genomic_pos(gene, 5) == 5
        assert _cds_pos_to_genomic_pos(gene, 6) == 12
        assert _cds_pos_to_genomic_pos(gene, 11) == 17

    def test_split_gene_roundtrip_reverse_strand(self) -> None:
        gene = _split_gene(strand='-')

        assert gene.contains(0)
        assert gene.contains(17)
        assert not gene.contains(6)
        assert gene.nt_offset(17) == 0
        assert gene.nt_offset(12) == 5
        assert gene.nt_offset(5) == 6
        assert gene.nt_offset(0) == 11
        assert gene.nt_offset(6) is None
        assert _cds_pos_to_genomic_pos(gene, 0) == 17
        assert _cds_pos_to_genomic_pos(gene, 5) == 12
        assert _cds_pos_to_genomic_pos(gene, 6) == 5
        assert _cds_pos_to_genomic_pos(gene, 11) == 0


class TestSplitGeneFastaProjection:
    def test_synthetic_variant_projection_uses_segment_coordinates(self) -> None:
        gene = _split_gene(strand='+')

        codon_variant = _make_variant(gene, 2, 'G', 'A')
        nt_variant = _make_variant_from_coding_nt(gene, 7, 'G', 'A')

        assert codon_variant.pos == 12
        assert nt_variant.pos == 13


# ──────────────────────────────────────────────────────────────────────
# Unit tests: remap_variants
# ──────────────────────────────────────────────────────────────────────

class TestRemapVariants:
    def test_exact_match_remaps_position(self, gene_fwd: GeneRecord) -> None:
        """Variant at flanked query pos 8 remaps to internal pos 3."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_genes(query, [gene_fwd])
        assert len(matches) == 1

        # Query 0-based pos 8 -> CDS pos 3 -> genomic 0-based pos 3
        variants = [
            VariantCall(
                chrom='user_ref', pos=8, ref='A', alt='G',
                allele_freq=0.9, depth=100,
            ),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(warnings) == 0
        assert len(remapped) == 1
        assert remapped[0].pos == 3
        assert remapped[0].ref == 'A'
        assert remapped[0].alt == 'G'


    def test_variant_outside_cds_excluded(self, gene_fwd: GeneRecord) -> None:
        """Variant in flanking region should be silently excluded."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_genes(query, [gene_fwd])

        variants = [
            VariantCall(
                chrom='user_ref', pos=1, ref='N', alt='A',
                allele_freq=0.5, depth=50,
            ),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(remapped) == 0
        assert len(warnings) == 0

    def test_ref_base_mismatch_warns(self, gene_fwd: GeneRecord) -> None:
        """VCF REF disagreeing with FASTA should produce a warning."""
        query = TINY_REF_SEQ
        matches = match_query_to_genes(query, [gene_fwd])

        # Position 0 in TINY_REF_SEQ is 'A', but VCF says REF='T'
        variants = [
            VariantCall(
                chrom='user_ref', pos=0, ref='T', alt='G',
                allele_freq=0.5, depth=50,
            ),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(remapped) == 0
        assert len(warnings) == 1
        assert 'VCF REF' in warnings[0]

    def test_multiple_variants_some_inside_some_outside(
        self, gene_fwd: GeneRecord,
    ) -> None:
        """Mixed set: one variant inside CDS, one outside."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_genes(query, [gene_fwd])

        variants = [
            VariantCall(chrom='c', pos=2, ref='N', alt='A', allele_freq=0.5, depth=50),
            VariantCall(chrom='c', pos=8, ref='A', alt='G', allele_freq=0.9, depth=100),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].pos == 3

    def test_preserves_allele_freq_and_depth(self, gene_fwd: GeneRecord) -> None:
        """AF and depth should be carried through from the original variant."""
        query = TINY_REF_SEQ
        matches = match_query_to_genes(query, [gene_fwd])

        variants = [
            VariantCall(
                chrom='c', pos=3, ref='A', alt='G',
                allele_freq=0.42, depth=999,
            ),
        ]
        remapped, _ = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].allele_freq == pytest.approx(0.42)
        assert remapped[0].depth == 999

    def test_snp_stores_query_ref_codon(self, gene_fwd: GeneRecord) -> None:
        """SNP remapping stores the query codon for downstream SNP annotation."""
        query = TINY_REF_SEQ
        matches = match_query_to_genes(query, [gene_fwd])

        variants = [
            VariantCall(chrom='c', pos=3, ref='A', alt='G', allele_freq=0.4, depth=20),
        ]
        remapped, _ = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].query_ref_codon == 'AAA'

    def test_snp_stores_query_ref_codon_for_reverse_match(self) -> None:
        """Reverse-strand matches must reconstruct query codon in CDS orientation."""
        gene_rev = GeneRecord(
            id=1,
            reference_id=1,
            name='rev',
            protein='Rev',
            start=0,
            end=6,
            strand='-',
            codon_start=0,
            nt_sequence='ATGAAA',
        )
        query = 'TTTCAT'  # reverse complement of ATGAAA
        matches = [
            GeneMatch(
                gene=gene_rev,
                identity=1.0,
                cds_coverage=1.0,
                query_coverage=1.0,
                query_start=0,
                query_end=6,
                strand='-',
                cigar='6M',
            ),
        ]

        variants = [
            VariantCall(chrom='c', pos=5, ref='T', alt='C', allele_freq=0.8, depth=20),
        ]
        remapped, _ = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].query_ref_codon == 'ATG'


# ──────────────────────────────────────────────────────────────────────
# Integration: resolve_fasta_reference
# ──────────────────────────────────────────────────────────────────────

class TestResolveFastaReference:
    def test_resolves_and_caches(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'query.fasta'
        fasta_path.write_text(f'>user_ref\n{TINY_REF_SEQ}\n')

        conn = open_project_db(fasta_db)
        name, seq, matches = resolve_fasta_query(
            conn, fasta_path,
        )

        assert name == 'user_ref'
        assert seq == TINY_REF_SEQ
        assert len(matches) >= 1
        assert matches[0].gene.name == 'gag'

        # Second call should hit cache
        name2, seq2, matches2 = resolve_fasta_query(
            conn, fasta_path,
        )
        assert len(matches2) == len(matches)
        conn.close()

    def test_empty_fasta_raises(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'empty.fasta'
        fasta_path.write_text('')

        conn = open_project_db(fasta_db)
        with pytest.raises(ValueError, match='No sequences'):
            resolve_fasta_query(conn, fasta_path)
        conn.close()

    def test_multi_record_fasta_raises(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        fasta_path = tmp_path / 'multi.fasta'
        fasta_path.write_text(
            f'>ref1\n{TINY_REF_SEQ}\n>ref2\n{TINY_REF_SEQ}\n'
        )

        conn = open_project_db(fasta_db)
        with pytest.raises(ValueError, match='single-record'):
            resolve_fasta_query(conn, fasta_path)
        conn.close()

    def test_trailing_ns_are_trimmed_before_matching(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'query_trailing_n.fasta'
        fasta_path.write_text(f'>user_ref\n{TINY_REF_SEQ}NNNNNN\n')

        conn = open_project_db(fasta_db)
        name, seq, matches = resolve_fasta_query(conn, fasta_path)

        assert name == 'user_ref'
        assert seq == TINY_REF_SEQ
        assert len(matches) >= 1
        conn.close()


class TestResolveCachedQueryReference:
    def test_resolves_stored_header(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'query.fasta'
        fasta_path.write_text(f'>stored_ref\n{TINY_REF_SEQ}\n')

        conn = open_project_db(fasta_db)
        resolve_fasta_query(conn, fasta_path)

        name, seq, matches = resolve_cached_query_reference(conn, 'stored_ref')

        assert name == 'stored_ref'
        assert seq == TINY_REF_SEQ
        assert len(matches) >= 1
        assert matches[0].gene.name == 'gag'
        conn.close()

    def test_unknown_header_lists_available_cached_headers(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'query.fasta'
        fasta_path.write_text(f'>stored_ref\n{TINY_REF_SEQ}\n')

        conn = open_project_db(fasta_db)
        resolve_fasta_query(conn, fasta_path)

        with pytest.raises(ValueError, match='Available cached headers: stored_ref'):
            resolve_cached_query_reference(conn, 'missing_ref')
        conn.close()

    def test_header_without_cached_mappings_raises(self, fasta_db: Path) -> None:
        conn = open_project_db(fasta_db)
        conn.execute(
            'INSERT INTO query_reference (name, sequence, length, checksum) VALUES (?, ?, ?, ?)',
            ('orphan_ref', TINY_REF_SEQ, len(TINY_REF_SEQ), 'orphan-checksum'),
        )
        conn.commit()

        with pytest.raises(ValueError, match='no cached gene mappings'):
            resolve_cached_query_reference(conn, 'orphan_ref')
        conn.close()

    def test_ambiguous_header_raises(self, fasta_db: Path) -> None:
        conn = open_project_db(fasta_db)
        genes = [
            GeneRecord(
                id=1,
                reference_id=1,
                name='gag',
                protein='Gag',
                start=0,
                end=87,
                strand='+',
                codon_start=0,
                nt_sequence=TINY_REF_SEQ,
            )
        ]
        query_one = TINY_REF_SEQ
        query_two = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches_one = match_query_to_genes(query_one, genes)
        matches_two = match_query_to_genes(query_two, genes)
        store_mappings(conn, 'dup_ref', query_one, sequence_checksum(query_one), matches_one)
        store_mappings(conn, 'dup_ref', query_two, sequence_checksum(query_two), matches_two)

        with pytest.raises(ValueError, match='ambiguous'):
            resolve_cached_query_reference(conn, 'dup_ref')
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# CLI end-to-end: profile --ref-fasta
# ──────────────────────────────────────────────────────────────────────

class TestProfileFastaCli:
    def test_profile_requires_ref_fasta(self, fasta_db: Path, tmp_path: Path) -> None:
        vcf_path = tmp_path / 'sample.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'Missing option' in result.output
        assert '--ref-fasta' in result.output

    def test_fasta_profile_uses_metadata_of_matched_reference(
        self, fasta_db_multi_reference: Path, tmp_path: Path,
    ) -> None:
        """Report metadata should come from the reference of the matched gene."""
        fasta_path = tmp_path / 'user_ref_b.fasta'
        query = 'CCCCGGGAAATTTCCCGGGAAATTTCCCGG'
        fasta_path.write_text(f'>user_ref_b\n{query}\n')

        vcf_path = tmp_path / 'fasta_ref_b.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref_b\t4\t.\tC\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        output_dir = tmp_path / 'fasta_ref_b_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(fasta_db_multi_reference),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--aligner', 'pairwise',
        ])

        assert result.exit_code == 0, result.output
        html = (output_dir / f'{vcf_path.stem}.report.html').read_text()
        assert 'refB' in html
        assert 'Organism B' in html

    def test_fasta_profile_detects_resistance_hit(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """VCF variant at user FASTA pos 9 remaps to pos 4 and triggers K2E."""
        fasta_path = tmp_path / 'user_ref.fasta'
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        fasta_path.write_text(f'>user_ref\n{query}\n')

        vcf_path = tmp_path / 'fasta_hit.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t9\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        output_dir = tmp_path / 'fasta_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '1 database hit' in result.output

    def test_fasta_profile_excludes_non_cds_variants(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """Variants outside CDS should be excluded after remapping."""
        fasta_path = tmp_path / 'user_ref.fasta'
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        fasta_path.write_text(f'>user_ref\n{query}\n')

        vcf_path = tmp_path / 'outside.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t2\t.\tN\tA\t50\tPASS\tAF=0.5;DP=100\n'
        )

        output_dir = tmp_path / 'outside_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '0 database hit' in result.output

    def test_fasta_profile_html_output_contains_expected_fields(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """HTML output with FASTA remapping should contain key fields."""
        fasta_path = tmp_path / 'user_ref.fasta'
        fasta_path.write_text(f'>user_ref\n{TINY_REF_SEQ}\n')

        vcf_path = tmp_path / 'json_test.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        output_dir = tmp_path / 'json_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        html = (output_dir / f'{vcf_path.stem}.report.html').read_text()
        assert TINY_REF_NAME in html
        assert 'gag' in html


# ──────────────────────────────────────────────────────────────────────
# FASTA consensus profiling — unit + integration tests
# ──────────────────────────────────────────────────────────────────────

# Simple 12-nt CDS: ATG AAA GCT TAA = M K A *
_SIMPLE_CDS = 'ATGAAAGCTTAA'


@pytest.fixture()
def simple_gene() -> GeneRecord:
    """Minimal 4-codon gene for FASTA consensus profiling tests."""
    return GeneRecord(
        id=1, reference_id=1, name='gag', protein='Gag',
        start=0, end=12, strand='+', codon_start=0,
        nt_sequence=_SIMPLE_CDS,
        aa_sequence='MKA*',
    )


def _make_match(gene: GeneRecord, query: str, strand: str = '+') -> GeneMatch:
    """Build a GeneMatch with a real CIGAR by aligning query against the gene CDS."""
    if not gene.nt_sequence:
        return GeneMatch(
            gene=gene, identity=0.0, cds_coverage=0.0, query_coverage=0.0,
            query_start=0, query_end=len(query), strand=strand, cigar='',
        )
    result = _align_cds_to_query(gene.nt_sequence.upper(), query.upper(), strand)
    return GeneMatch(
        gene=gene,
        identity=result.identity,
        cds_coverage=result.cds_coverage,
        query_coverage=result.query_coverage,
        query_start=result.query_start,
        query_end=result.query_end,
        strand=strand,
        cigar=result.cigar,
        cds_start=result.cds_start,
    )


class TestIupacExpansion:
    def test_unambiguous_codon_returns_single_aa(self) -> None:
        assert _expand_iupac_codon('ATG') == {'M'}

    def test_r_expands_to_two_aas(self) -> None:
        # RAA = AAA (K) or GAA (E)
        aas = _expand_iupac_codon('RAA')
        assert aas == {'K', 'E'}

    def test_n_expands_to_all_possibilities(self) -> None:
        # NNN covers all 64 codons; result must include at least standard AAs
        aas = _expand_iupac_codon('NNN')
        assert 'M' in aas
        assert len(aas) > 5

    def test_stop_codon_included(self) -> None:
        # TAR = TAA or TAG = both stops
        aas = _expand_iupac_codon('TAR')
        assert aas == {'*'}


class TestFastaConsensusProfile:
    def test_perfect_match_produces_no_annotations(self, simple_gene: GeneRecord) -> None:
        """Identical consensus emits zero annotations (all synonymous)."""
        match = _make_match(simple_gene, _SIMPLE_CDS)
        anns, gaps = profile_fasta_consensus(_SIMPLE_CDS, [match])
        assert anns == []
        assert gaps == []

    def test_missense_snp_detected(self, simple_gene: GeneRecord) -> None:
        """Single K→E substitution at codon 1 → one missense annotation."""
        # ATGGAAGCTTAA: codon 1 = GAA = E (was AAA = K)
        query = 'ATGGAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        ann = anns[0]
        assert ann.gene_name == 'gag'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'
        assert ann.codon_pos == 1
        assert ann.variant.ref == 'A'
        assert ann.variant.alt == 'G'
        assert ann.variant.allele_freq == pytest.approx(1.0)

    def test_synonymous_change_not_emitted(self, simple_gene: GeneRecord) -> None:
        """Synonymous codon change produces no annotations."""
        # AAA → AAG: both encode K
        query = 'ATGAAGGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])
        assert anns == []

    def test_stop_gained(self, simple_gene: GeneRecord) -> None:
        """K → * at codon 1 → stop_gained annotation."""
        # ATGTAAGCTTAA: codon 1 = TAA = *
        query = 'ATGTAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        assert anns[0].alt_aa == '*'
        assert anns[0].consequence == 'stop_gained'

    def test_insertion_produces_frameshift_annotation(self, simple_gene: GeneRecord) -> None:
        """Single-base insertion shifts the reading frame and produces a frameshift annotation."""
        query = 'ATGAAAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert any(a.consequence == 'frameshift' and a.alt_aa == 'MfsX' for a in anns)

    def test_deletion_produces_frameshift_annotation(self, simple_gene: GeneRecord) -> None:
        """Single-base deletion shifts the reading frame and produces a frameshift annotation."""
        query = 'ATGAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert any(a.consequence == 'frameshift' and a.alt_aa == 'MfsX' for a in anns)

    def test_iupac_ambiguous_base_emits_split_frequency(self, simple_gene: GeneRecord) -> None:
        """IUPAC 'R' at codon 1 → K (ref) or E: only E emitted with af=0.5."""
        # 'ATGRAAGCTTAA': codon 1 = RAA = AAA (K) or GAA (E)
        query = 'ATGRAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        ann = anns[0]
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'
        assert ann.variant.allele_freq == pytest.approx(0.5)

    def test_position_is_0based_genomic_codon_start(self, simple_gene: GeneRecord) -> None:
        """Variant pos should be the 0-based genomic NT position of the affected codon."""
        # K→E at codon 1 (codons: 0=ATG pos 0, 1=AAA pos 3, 2=GCT pos 6, 3=TAA pos 9)
        query = 'ATGGAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        # Codon 1 starts at genomic pos 3 (0-based)
        assert anns[0].variant.pos == 3

    def test_no_nt_sequence_skips_gene(self, tmp_path: Path) -> None:
        """Gene with empty nt_sequence should be silently skipped."""
        gene_no_seq = GeneRecord(
            id=1, reference_id=1, name='empty_gene', protein='',
            start=0, end=12, strand='+', codon_start=0,
            nt_sequence='',
        )
        match = _make_match(gene_no_seq, _SIMPLE_CDS)
        anns, gaps = profile_fasta_consensus(_SIMPLE_CDS, [match])
        assert anns == []
        assert gaps == []


class TestNStretchCoverageGaps:
    """N-stretch detection in FASTA mode — full-codon NNN → CoverageGap, partial N → IUPAC."""

    def test_all_n_codon_produces_gap_not_annotation(self, simple_gene: GeneRecord) -> None:
        """NNN at codon 1 yields a CoverageGap stretch; no annotation emitted for that codon."""
        # ATGNNNGCTTAA: codon 1 all-N
        query = 'ATGNNNGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert not any(a.codon_pos == 1 for a in anns)
        assert any(g.gene_name == 'gag' and g.codon_start <= 1 <= g.codon_end for g in gaps)

    def test_partial_n_codon_produces_iupac_expansion(self, simple_gene: GeneRecord) -> None:
        """Single N in a codon stays as IUPAC expansion — no coverage gap emitted."""
        # ATGAAN GCTTAA: codon 1 = AAN keeps internal ambiguity handling.
        query = 'ATGAANGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        codon1_anns = [a for a in anns if a.codon_pos == 1]
        assert len(codon1_anns) > 0
        assert not any(g.codon_start <= 1 <= g.codon_end for g in gaps)

    def test_processing_continues_after_n_stretch(self, simple_gene: GeneRecord) -> None:
        """Codons after an NNN gap are still annotated normally."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATGNNNGAATAA'
        anns, gaps = _annotate_from_alignment(
            aligned_ref,
            aligned_query,
            simple_gene,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        # Gap covers codon 1
        assert any(g.codon_start <= 1 <= g.codon_end for g in gaps)
        # Missense at codon 2 (A→E)
        assert any(a.codon_pos == 2 and a.ref_aa == 'A' and a.alt_aa == 'E' for a in anns)

    def test_consecutive_n_codons_merge_into_one_stretch(self, simple_gene: GeneRecord) -> None:
        """Two consecutive all-N codons and uncovered tail merge into a single CoverageGap stretch."""
        # ATGNNNNNN TAA: codons 1+2 all-N; aligner only reaches ATG, so codon 3 is also non-covered.
        # All three non-covered codons (1, 2, 3) merge into one contiguous stretch.
        query = 'ATGNNNNNNTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert len(gaps) == 1
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 3

    def test_lowercase_nnn_also_treated_as_gap(self, simple_gene: GeneRecord) -> None:
        """Lower-case 'nnn' in a codon is treated as NNN (non-covered)."""
        query = 'ATGnnnGCTTAA'
        match = _make_match(simple_gene, query)
        anns, gaps = profile_fasta_consensus(query, [match])

        assert any(g.codon_start <= 1 <= g.codon_end for g in gaps)

    def test_terminal_missing_sequence_and_terminal_n_are_equivalent(
        self, simple_gene: GeneRecord,
    ) -> None:
        """Missing tail and trailing N tail produce identical uncovered codon stretches."""
        trimmed_query = 'ATGAAAGCT'  # last codon missing
        trailing_n_query = 'ATGAAAGCTNNN'

        match_trimmed = _make_match(simple_gene, trimmed_query)
        _, gaps_trimmed = profile_fasta_consensus(trimmed_query, [match_trimmed])

        match_n = _make_match(simple_gene, trailing_n_query)
        _, gaps_n = profile_fasta_consensus(trailing_n_query, [match_n])

        assert gaps_trimmed == gaps_n


class TestFastaConsensusCli:
    """End-to-end CLI test for --fasta consensus input mode."""

    def test_fasta_consensus_detects_resistance_hit(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """Consensus FASTA with K→E at codon 1 should trigger the resistance rule."""
        # Introduce K→E at codon 1 of TINY_REF_SEQ
        mutant = 'ATG' + 'GAA' + TINY_REF_SEQ[6:]
        fasta_path = tmp_path / 'consensus.fasta'
        fasta_path.write_text(f'>consensus\n{mutant}\n')

        output_dir = tmp_path / 'out'
        result = CliRunner().invoke(app, [
            'fasta',
            '--project', str(fasta_db),
            '--fasta', str(fasta_path),
            '--output', str(output_dir),
        ])

        assert result.exit_code == 0, result.output
        assert '1 database hit' in result.output

    def test_fasta_consensus_no_change_no_hits(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """Identical consensus → 0 database hits."""
        fasta_path = tmp_path / 'identical.fasta'
        fasta_path.write_text(f'>identical\n{TINY_REF_SEQ}\n')

        output_dir = tmp_path / 'out'
        result = CliRunner().invoke(app, [
            'fasta',
            '--project', str(fasta_db),
            '--fasta', str(fasta_path),
            '--output', str(output_dir),
        ])

        assert result.exit_code == 0, result.output
        assert '0 database hit' in result.output

    def test_fasta_consensus_writes_optional_json_export(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        fasta_path = tmp_path / 'identical_json.fasta'
        fasta_path.write_text(f'>identical\n{TINY_REF_SEQ}\n')

        output_dir = tmp_path / 'out_json'
        result = CliRunner().invoke(app, [
            'fasta',
            '--project', str(fasta_db),
            '--fasta', str(fasta_path),
            '--output', str(output_dir),
            '--export', 'json',
        ])

        assert result.exit_code == 0, result.output
        json_path = output_dir / f'{fasta_path.stem}.results.json'
        assert json_path.exists()


# ──────────────────────────────────────────────────────────────────────
# FASTA alignment: insertion annotation
# ──────────────────────────────────────────────────────────────────────

def _make_fasta_gene(nt_seq: str, strand: str = '+') -> GeneRecord:
    """Build a minimal GeneRecord for alignment annotation tests."""
    return GeneRecord(
        id=1, reference_id=1, name='gene', protein='P',
        start=0, end=len(nt_seq), strand=strand, codon_start=0,
        nt_sequence=nt_seq,
    )


class TestFastaInsertionAnnotation:
    """Inline-frame and frameshift insertions detected in pairwise FASTA alignments."""

    def test_inframe_insertion_at_codon_boundary(self) -> None:
        """3-nt insertion before codon 1 uses preceding anchor codon: M -> MP."""
        # Gene: ATG GGG TTT (M G F); insert CCC before codon 1
        # aligned_ref:   ATG---GGGTTT  (12 chars)
        # aligned_query: ATGCCCGGGTTT  (12 chars)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATG---GGGTTT'
        aligned_query = 'ATGCCCGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        assert len(anns) == 1
        ann = anns[0]
        assert ann.gene_name == 'gene'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MP'
        assert ann.consequence == 'insertion'
        assert ann.is_fasta_mode is True
        assert ann.variant.ref == 'G'
        assert ann.variant.alt == 'GCCC'

    def test_inframe_insertion_anchor_from_query_not_internal_ref(self) -> None:
        """Anchor AA uses preceding query codon context, not internal reference codon."""
        # Gene codon 0 = ATG -> M; query codon 0 = ACG -> T (divergent); insert CCC before codon 1.
        # aligned_ref:   ATG---GGGTTT
        # aligned_query: ACGCCCAGGTTT
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATG---GGGTTT'
        aligned_query = 'ACGCCCAGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 2
        ann = next(a for a in anns if a.consequence == 'insertion')
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'T'      # anchor from QUERY ACG -> T (not internal ATG -> M)
        assert ann.alt_aa == 'TP'     # T + CCC -> P
        assert ann.consequence == 'insertion'

    def test_frameshift_insertion_at_codon_boundary(self) -> None:
        """1-nt insertion before codon 1 -> frameshift with preceding codon anchor."""
        # aligned_ref:   ATG-GGGTTT  (10 chars)
        # aligned_query: ATGCGGGTTT  (10 chars: insert C before codon 1)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATG-GGGTTT'
        aligned_query = 'ATGCGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'
        assert ann.is_fasta_mode is True
        assert ann.variant.ref == 'G'
        assert ann.variant.alt == 'GC'

    def test_mid_codon_insertion_is_inframe_complex(self) -> None:
        """Insertion embedded before position 2 of a codon → inframe_complex."""
        # Gene: ATG GGG TTT; insert CCC between ref positions 1 (T) and 2 (G) of codon 0
        # aligned_ref:   AT---GGGGTTT  (12 chars)
        # aligned_query: ATCCCGGGGTTT  (12 chars)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'AT---GGGGTTT'
        aligned_query = 'ATCCCGGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == '?'
        assert ann.consequence == 'inframe_complex'
        assert ann.is_fasta_mode is True

    def test_single_nt_mid_codon_insertion_is_frameshift(self) -> None:
        """A non-3n insertion within a codon is a frameshift, not inframe_complex."""
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref = 'AT-GGGGTTT'
        aligned_query = 'ATCGGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        assert len(anns) == 1
        ann = anns[0]
        assert ann.consequence == 'frameshift'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.variant.ref == 'T'
        assert ann.variant.alt == 'TC'

    def test_single_nt_mid_codon_insertion_is_frameshift_on_reverse_gene(self) -> None:
        """Reverse-strand single-nt mid-codon insertion remains a frameshift indel event."""
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        aligned_ref = 'AT-GGGGTTT'
        aligned_query = 'ATCGGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        assert len(anns) == 1
        ann = anns[0]
        assert ann.consequence == 'frameshift'
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert len(ann.variant.alt) - len(ann.variant.ref) == 1

    def test_negative_strand_mid_codon_frameshift_insertion_anchors_previous_codon(self) -> None:
        """Minus-strand mid-codon frameshift insertion anchors AA change on previous valid codon."""
        gene = _make_fasta_gene('CCCAGCCTCCCCCCC', strand='-')
        aligned_ref = 'CCCAGCCT-CCCCCCC'
        aligned_query = 'CCCAGCCTCCCCCCCC'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        frameshift_anns = [ann for ann in anns if ann.consequence == 'frameshift']
        assert len(frameshift_anns) == 1
        ann = frameshift_anns[0]
        assert ann.codon_pos == 1
        assert ann.ref_aa == 'S'
        assert ann.alt_aa == 'SfsX'
        assert len(ann.variant.alt) - len(ann.variant.ref) == 1

    def test_boundary_and_mid_codon_insertion_is_inframe_complex(self) -> None:
        """Insertions at both codon boundary and mid-codon → inframe_complex."""
        # Insert C before codon 0 AND CCC between positions 1 and 2 of codon 0
        # aligned_ref:   -AT---GGGGTTT  (13 chars)
        # aligned_query: CATCCCGGGGTTT  (13 chars)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = '-AT---GGGGTTT'
        aligned_query = 'CATCCCGGGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        assert anns[0].consequence == 'inframe_complex'

    def test_insertion_followed_by_snp_annotated_separately(self) -> None:
        """A 3-nt insertion before codon 1 and a SNP at codon 2 each emit one annotation."""
        # Gene: ATG GGG TTT; insert CCC before codon 1; codon 2 TTT→ TAT (F→Y)
        # aligned_ref:   ATG---GGGTTT  (12 chars)
        # aligned_query: ATGCCCGGGTAT  (12 chars: codon 2 in query = TAT → Y)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATG---GGGTTT'
        aligned_query = 'ATGCCCGGGTAT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        consequences = {a.consequence for a in anns}
        assert 'insertion' in consequences
        assert 'missense' in consequences
        ins_ann = next(a for a in anns if a.consequence == 'insertion')
        snp_ann = next(a for a in anns if a.consequence == 'missense')
        assert ins_ann.codon_pos == 0
        assert snp_ann.alt_aa == 'Y'

    def test_negative_strand_insertion_bases_already_cds_oriented(self) -> None:
        """Minus-strand gene: inserted bases are passed in CDS orientation directly."""
        # Minus-strand gene with coding seq ATG GGG TTT (M G F)
        # _profile_gene aligns in CDS orientation; _annotate_from_alignment
        # receives CDS-oriented strings, so no extra RC is needed here.
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        # Same alignment as forward strand — already CDS oriented
        aligned_ref   = 'ATG---GGGTTT'
        aligned_query = 'ATGCCCGGGTTT'   # insert CCC before codon 1

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MP'
        assert ann.consequence == 'insertion'
        # For '-' strand insertion, NT anchor follows genomic 5'->3' mapping (coding idx 3 -> pos 5).
        assert ann.variant.pos == 5

    def test_negative_strand_insertion_uses_query_mapped_nt_anchor(self) -> None:
        """Reverse-complement alignment reports NT anchor in genomic 5'->3' orientation."""
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        aligned_ref = 'ATG---GGGTTT'
        aligned_query = 'ATGTTTGGGTTT'  # inserted in coding orientation; report should use genomic RC

        anns, _ = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        ann = next(a for a in anns if a.consequence == 'insertion')
        assert ann.variant.ref == 'C'
        assert ann.variant.alt == 'CAAA'

    def test_negative_strand_frameshift_insertion_uses_genomic_indel_notation(self) -> None:
        """Reverse-complement FASTA frameshift insertion uses genomic anchor-plus-payload alleles."""
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        aligned_ref = 'ATG-GGGTTT'
        aligned_query = 'ATGTGGGTTT'

        anns, _ = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        ann = next(a for a in anns if a.consequence == 'frameshift')
        assert ann.variant.ref == 'C'
        assert ann.variant.alt == 'CA'


# ──────────────────────────────────────────────────────────────────────
# FASTA alignment: deletion annotation
# ──────────────────────────────────────────────────────────────────────

class TestFastaDeletionAnnotation:
    """In-frame and frameshift deletions detected in pairwise FASTA alignments."""

    def test_inframe_single_codon_deletion(self) -> None:
        """Codon 1 (G) fully deleted: anchor M from codon 0, ref_aa MG, alt_aa M."""
        # Gene: ATG GGG TTT; codon 1 deleted
        # aligned_ref:   ATGGGGTTT  (9 chars)
        # aligned_query: ATG---TTT  (9 chars)
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATG---TTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        assert len(anns) == 1
        ann = anns[0]
        assert ann.gene_name == 'gene'
        assert ann.codon_pos == 0   # anchor codon (M at codon 0), consistent with VCF mode
        assert ann.ref_aa == 'MG'    # anchor M (codon 0 query ATG) + deleted G (codon 1 GGG)
        assert ann.alt_aa == 'M'     # anchor only
        assert ann.consequence == 'deletion'
        assert ann.is_fasta_mode is True

    def test_inframe_multi_codon_deletion_merged_into_one_annotation(self) -> None:
        """Two consecutive deleted codons emit a single deletion annotation."""
        # Gene: ATG GGG AAA TTT (M G K F, 12 nt); delete codons 1 (G) and 2 (K)
        # aligned_ref:   ATGGGGAAATTT  (12 chars)
        # aligned_query: ATG------TTT  (12 chars)
        gene = _make_fasta_gene('ATGGGGAAATTT')
        aligned_ref   = 'ATGGGGAAATTT'
        aligned_query = 'ATG------TTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0   # anchor at codon 0 (M)
        assert ann.ref_aa == 'MGK'   # anchor M + deleted G + deleted K
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'

    def test_deletion_anchor_uses_query_codon_not_internal_ref(self) -> None:
        """Anchor AA comes from the last valid query codon (query context)."""
        # Gene: ATG GGG TTT; codon 0 in query = ACG (T, not M); codon 1 deleted
        # aligned_ref:   ATGGGGTTT
        # aligned_query: ACG---TTT  ← codon 0 query = ACG → T
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ACG---TTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        del_ann = next(a for a in anns if a.consequence == 'deletion')
        assert del_ann.codon_pos == 0   # anchor at codon 0
        assert del_ann.ref_aa == 'TG'   # anchor T (from query ACG) + deleted G
        assert del_ann.alt_aa == 'T'    # anchor T

    def test_partial_deletion_is_frameshift(self) -> None:
        """1-gap query codon (partial deletion) → frameshift."""
        # Gene: ATG GGG TTT; codon 1 has 1 gap → partial deletion
        # aligned_ref:   ATGGGGTTT  (9 chars)
        # aligned_query: ATG-GGTTT  (9 chars: codon 1 = '-GG')
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATG-GGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'     # first gap at codon pos 0 anchors to previous codon
        assert ann.alt_aa == 'MfsX'
        assert ann.consequence == 'frameshift'
        assert ann.is_fasta_mode is True
        assert ann.variant.ref == 'GG'
        assert ann.variant.alt == 'G'

    def test_two_gap_query_codon_is_also_frameshift(self) -> None:
        """2-gap query codon (partial deletion) → frameshift."""
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATGGG--TT'  # codon 2 = 'G--'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert any(a.consequence == 'frameshift' for a in anns)

    def test_non_consecutive_partial_deletions_in_separate_codons_emit_separate_frameshifts(
        self,
    ) -> None:
        """
        Non-consecutive single-nt deletions in different codons emit two separate frameshifts.

        Gene: ATG GGG AAA TTT (M G K F, 12 nt)
        Query: ATG G-A A-A TTT  ← gap at codon 1 pos 1, gap at codon 2 pos 1
        The deletions are separated by non-gap nucleotides, so they are NOT consecutive
        and must not be merged → TWO separate frameshift annotations.
        """
        gene = _make_fasta_gene('ATGGGGAAATTT')
        aligned_ref   = 'ATGGGGAAATTT'
        aligned_query = 'ATGG-AA-ATTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        frameshift_anns = [a for a in anns if a.consequence == 'frameshift']
        assert len(frameshift_anns) == 2, f'Expected 2 frameshifts, got {len(frameshift_anns)}'
        codon_positions = {ann.codon_pos for ann in frameshift_anns}
        assert 1 in codon_positions
        assert 2 in codon_positions
        for ann in frameshift_anns:
            assert len(ann.variant.ref) - len(ann.variant.alt) == 1

    def test_multi_codon_inframe_deletion_merged_into_one_annotation(self) -> None:
        """
        In-frame deletion spanning multiple codons (3n total) emits ONE deletion annotation.

        Gene: ATG GGG AAA TTT CCC (M G K F P, 15 nt)
        Query: ATG GGG --- --- CCC  ← codons 2-3 fully deleted (6 nt = 2 codons)
        Anchor: codon 1 (last valid before deletion)
        """
        gene = _make_fasta_gene('ATGGGGAAATTTCCC')
        aligned_ref   = 'ATGGGGAAATTTCCC'
        aligned_query = 'ATGGGG------CCC'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        deletion_anns = [a for a in anns if a.consequence == 'deletion']
        assert len(deletion_anns) == 1, f'Expected 1 deletion, got {len(deletion_anns)}'

        ann = deletion_anns[0]
        assert ann.codon_pos == 1  # anchor at codon 1 (G, last valid before deletion starts)
        assert ann.ref_aa == 'GKF'  # anchor G + deleted K + deleted F
        assert ann.alt_aa == 'G'

    def test_frameshift_deletion_anchor_before_gap_not_after(self) -> None:
        """
        Frameshift deletion: AA anchor follows the NT anchor codon.

        Gene: ATG GGG TTT (M G F, 9 nt)
        Query: ATG -GG TTT  ← gap at position 0 of codon 1
        AA anchor: previous codon 0 = ATG -> M (same codon as NT anchor)
        NT anchor: from last valid codon 0 (last NT = G)
        """
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATG-GGTTT'  # codon 1 = '-GG' → gap at pos 0, anchor from codon 0

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        frameshift_anns = [a for a in anns if a.consequence == 'frameshift']
        assert len(frameshift_anns) == 1
        ann = frameshift_anns[0]
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        # NT anchor should start with G (last NT of codon 0)
        assert ann.variant.ref[0] == 'G'

    def test_non_consecutive_deletions_emit_separate_frameshifts(self) -> None:
        """
        Non-consecutive single-nt deletions in different codons emit two separate frameshift events.

        Gene: ATG GGG AAA TTT (M G K F, 12 nt)
        Query: ATG GG- A-A TTT  ← gap at position 2 of codon 1, gap at position 1 of codon 2
        The deletions are separated by a non-gap nucleotide, so they are NOT consecutive
        and must not be merged → TWO separate frameshift annotations.
        """
        gene = _make_fasta_gene('ATGGGGAAATTT')
        aligned_ref   = 'ATGGGGAAATTT'
        aligned_query = 'ATGGG-A-ATTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        frameshift_anns = [a for a in anns if a.consequence == 'frameshift']
        # Two non-consecutive deletions must produce two separate frameshift annotations
        assert len(frameshift_anns) == 2, f'Expected 2 frameshifts, got {len(frameshift_anns)}'
        codon_positions = {ann.codon_pos for ann in frameshift_anns}
        assert 1 in codon_positions
        assert 2 in codon_positions
        for ann in frameshift_anns:
            # Each deletion removes 1 nt
            assert len(ann.variant.ref) - len(ann.variant.alt) == 1

    def test_mixed_partial_and_full_deletion_run_is_one_frameshift(self) -> None:
        """
        Consecutive partial+full deletion codons emit one frameshift annotation.

        Gene: ATG GGG AAA TTT (M G K F, 12 nt)
        Query: ATG GG- --- TTT  ← gap at position 2 of codon 1, all gaps in codon 2
        Total deleted: 4 nt (non-3n) -> one frameshift and no deletion annotation.
        """
        gene = _make_fasta_gene('ATGGGGAAATTT')
        aligned_ref = 'ATGGGGAAATTT'
        aligned_query = 'ATGGG----TTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(gaps) == 0
        frameshift_anns = [a for a in anns if a.consequence == 'frameshift']
        deletion_anns = [a for a in anns if a.consequence == 'deletion']
        assert len(frameshift_anns) == 1
        assert len(deletion_anns) == 0
        ann = frameshift_anns[0]
        assert len(ann.variant.ref) - len(ann.variant.alt) == 4

    def test_deletion_at_gene_start_no_anchor_becomes_gap(self) -> None:
        """Full-codon deletion at codon 0 with no preceding anchor → coverage gap."""
        # Gene: ATG GGG TTT; codon 0 deleted
        # aligned_ref:   ATGGGGTTT
        # aligned_query: ---GGGTTT
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = '---GGGTTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 0
        assert len(gaps) == 1
        assert gaps[0].codon_start == 0
        assert gaps[0].codon_end == 0

    def test_negative_strand_deletion_correct_genomic_position(self) -> None:
        """Minus-strand gene deletion uses correct codon genomic coordinate."""
        # Minus-strand gene: coding sequence ATG GGG TTT stored in coding orientation
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATG---TTT'   # codon 1 deleted

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        assert len(anns) == 1
        ann = anns[0]
        assert ann.codon_pos == 0   # anchor at codon 0
        assert ann.ref_aa == 'MG'
        assert ann.alt_aa == 'M'
        assert ann.consequence == 'deletion'
        # Minus-strand VCF anchor is after the deletion in CDS order (5' genomically).
        # First NT of next codon (TTT) is at CDS index 6; pos = (end-1) - 6 = 2.
        assert ann.variant.pos == 2
        assert ann.variant.ref == 'ACCC'
        assert ann.variant.alt == 'A'

    def test_negative_strand_frameshift_deletion_uses_genomic_indel_notation(self) -> None:
        """Reverse-complement FASTA frameshift deletion uses genomic anchor-plus-payload alleles."""
        gene = _make_fasta_gene('ATGGGGTTT', strand='-')
        aligned_ref = 'ATGGGGTTT'
        aligned_query = 'ATG-GGTTT'

        anns, _ = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        ann = next(a for a in anns if a.consequence == 'frameshift')
        assert ann.variant.ref == 'CC'
        assert ann.variant.alt == 'C'

    def test_negative_strand_frameshift_deletion_last_codon_base_anchors_previous_amino_acid(
        self,
    ) -> None:
        gene = _make_fasta_gene('ATGCCCTTT', strand='-')
        aligned_ref = 'ATGCCCTTT'
        aligned_query = 'ATGCC-TTT'

        anns, _ = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        ann = next(a for a in anns if a.consequence == 'frameshift')
        assert ann.codon_pos == 0
        assert ann.ref_aa == 'M'
        assert ann.alt_aa == 'MfsX'
        assert ann.variant.ref == 'AG'
        assert ann.variant.alt == 'A'

    def test_snp_before_deletion_is_emitted_separately(self) -> None:
        """A start_lost at codon 0 and a deletion at codon 1 each emit their own annotation."""
        # Gene: ATG GGG TTT; codon 0 ATG→ACG (M→T = start_lost); codon 1 GGG deleted
        # aligned_ref:   ATGGGGTTT
        # aligned_query: ACG---TTT
        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ACG---TTT'

        anns, gaps = _annotate_from_alignment(aligned_ref, aligned_query, gene)

        consequences = {a.consequence for a in anns}
        assert 'start_lost' in consequences
        assert 'deletion' in consequences

    def test_deletion_rule_matching_compatible_format(self) -> None:
        """Deletion annotation ref_aa/alt_aa follows anchor+deleted / anchor convention."""
        # Ensures _matches_rule_alleles can match a rule with reference='MG', mutation='M'
        from respro.core.rules import _matches_rule_alleles

        gene = _make_fasta_gene('ATGGGGTTT')
        aligned_ref   = 'ATGGGGTTT'
        aligned_query = 'ATG---TTT'

        anns, _ = _annotate_from_alignment(aligned_ref, aligned_query, gene)
        ann = next(a for a in anns if a.consequence == 'deletion')

        assert _matches_rule_alleles(
            reference='MG', mutation='M', ann_ref=ann.ref_aa, ann_alt=ann.alt_aa,
        )


class TestReverseStrandMappyParity:
    """Regression tests for reverse-strand FASTA profiling with mappy CIGAR handling."""

    def _build_long_coding_reference(self) -> str:
        rng = random.Random(42)
        codons = (
            'GCT', 'GAT', 'GAA', 'TCC', 'CAG', 'AAC', 'CTG', 'TAC',
            'GGA', 'ATC', 'CAA', 'TTG', 'GTC', 'AGC', 'AAG', 'TTC',
            'GCG', 'ACC', 'GGT', 'CAT',
        )
        return ''.join(rng.choice(codons) for _ in range(700))

    def _profile_reverse_query(
        self,
        coding_reference: str,
        coding_query: str,
        aligner: Literal['mappy', 'pairwise'],
    ) -> list[tuple[int, str, str, str]]:
        query = str(Seq(coding_query).reverse_complement())
        gene = GeneRecord(
            id=1,
            reference_id=1,
            name='rev',
            protein='Rev',
            start=0,
            end=len(coding_reference),
            strand='-',
            codon_start=0,
            nt_sequence=coding_reference,
            aa_sequence='',
        )

        matches = match_query_to_genes(
            query,
            [gene],
            min_identity=0.7,
            min_coverage=0.7,
            aligner=aligner,
        )
        assert len(matches) == 1
        assert matches[0].strand == '-'

        annotations, gaps = profile_fasta_consensus(query, [matches[0]])
        assert gaps == []
        return [
            (ann.codon_pos, ann.consequence, ann.ref_aa, ann.alt_aa)
            for ann in annotations
        ]

    def test_reverse_frameshift_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 901
        coding_query = coding_reference[:event_pos] + coding_reference[event_pos + 1:]

        mappy = self._profile_reverse_query(coding_reference, coding_query, 'mappy')

        assert len(mappy) == 1
        assert mappy[0][1] == 'frameshift'

    def test_reverse_triplet_deletion_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 900
        coding_query = coding_reference[:event_pos] + coding_reference[event_pos + 3:]

        mappy = self._profile_reverse_query(coding_reference, coding_query, 'mappy')

        assert len(mappy) == 1
        assert mappy[0][1] == 'deletion'

    def test_reverse_triplet_insertion_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 900
        coding_query = coding_reference[:event_pos] + 'GCC' + coding_reference[event_pos:]

        mappy = self._profile_reverse_query(coding_reference, coding_query, 'mappy')

        assert len(mappy) == 1
        assert mappy[0][1] == 'insertion'

