"""
Tests for FASTA-based profiling — coordinate remapping and end-to-end CLI workflow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from conftest import TINY_REF_SEQ, TINY_REF_NAME
from respro.cli import main
from respro.core.profile import (
    _build_query_to_cds_map,
    _cds_pos_to_genomic_pos,
    remap_variants,
    resolve_fasta_reference,
)
from respro.core.sequence_matching import match_query_to_genes
from respro.db.models import GeneRecord, VariantCall
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

    def test_indel_does_not_store_query_ref_codon(self, gene_fwd: GeneRecord) -> None:
        """Indels remain anchored to internal CDS without query codon overrides."""
        query = TINY_REF_SEQ
        matches = match_query_to_genes(query, [gene_fwd])

        variants = [
            VariantCall(chrom='c', pos=3, ref='A', alt='AG', allele_freq=0.4, depth=20),
        ]
        remapped, _ = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].query_ref_codon == ''


# ──────────────────────────────────────────────────────────────────────
# Integration: resolve_fasta_reference
# ──────────────────────────────────────────────────────────────────────

class TestResolveFastaReference:
    def test_resolves_and_caches(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'query.fasta'
        fasta_path.write_text(f'>user_ref\n{TINY_REF_SEQ}\n')

        conn = open_project_db(fasta_db)
        name, seq, matches = resolve_fasta_reference(
            conn, fasta_path,
        )

        assert name == 'user_ref'
        assert seq == TINY_REF_SEQ
        assert len(matches) >= 1
        assert matches[0].gene.name == 'gag'

        # Second call should hit cache
        name2, seq2, matches2 = resolve_fasta_reference(
            conn, fasta_path,
        )
        assert len(matches2) == len(matches)
        conn.close()

    def test_empty_fasta_raises(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'empty.fasta'
        fasta_path.write_text('')

        conn = open_project_db(fasta_db)
        with pytest.raises(ValueError, match='No sequences'):
            resolve_fasta_reference(conn, fasta_path)
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
            resolve_fasta_reference(conn, fasta_path)
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# CLI end-to-end: profile --ref-fasta
# ──────────────────────────────────────────────────────────────────────

class TestProfileFastaCli:
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
        result = runner.invoke(main, [
            'profile',
            '--project', str(fasta_db_multi_reference),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--format', 'json',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        data = json.loads((output_dir / 'results.json').read_text())
        assert data['reference'] == 'refB'
        assert data['organism'] == 'Organism B'

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
        result = runner.invoke(main, [
            'profile',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--format', 'json',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '1 resistance hit' in result.output

        data = json.loads((output_dir / 'results.json').read_text())
        hits = [v for v in data['variants'] if v['resistance_hit']]
        assert len(hits) == 1
        assert hits[0]['alt_aa'] == 'E'
        assert hits[0]['ref_aa'] == 'K'

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
        result = runner.invoke(main, [
            'profile',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--format', 'json',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '0 resistance hit' in result.output

    def test_fasta_profile_json_output_correct(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        """JSON output with FASTA remapping should contain the correct fields."""
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
        result = runner.invoke(main, [
            'profile',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--format', 'json',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output

        data = json.loads((output_dir / 'results.json').read_text())
        assert data['reference'] == TINY_REF_NAME
        assert len(data['variants']) == 1
        assert data['variants'][0]['gene'] == 'gag'

