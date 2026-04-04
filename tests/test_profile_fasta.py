"""
Tests for FASTA-based profiling — coordinate remapping, FASTA consensus profiling,
and end-to-end CLI workflow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from conftest import TINY_REF_SEQ, TINY_REF_NAME
from respro.cli import main
from respro.core.fasta_profile import (
    _annotate_from_alignment,
    _expand_iupac_codon,
    profile_fasta_consensus,
)
from respro.core.profile import (
    _build_query_to_cds_map,
    _cds_pos_to_genomic_pos,
    remap_variants,
    resolve_cached_query_reference,
    resolve_fasta_query,
)
from respro.core.sequence_matching import (
    GeneMatch, _align_cds_to_query, match_query_to_genes, sequence_checksum, store_mappings,
)
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
                coverage=1.0,
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
    def test_profile_requires_one_query_reference_source(self, fasta_db: Path, tmp_path: Path) -> None:
        vcf_path = tmp_path / 'sample.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        result = CliRunner().invoke(main, [
            'profile-vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'exactly one of --ref-fasta or --query-ref-header' in result.output

    def test_profile_rejects_both_query_reference_sources(self, fasta_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'user_ref.fasta'
        fasta_path.write_text(f'>user_ref\n{TINY_REF_SEQ}\n')
        vcf_path = tmp_path / 'sample.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        result = CliRunner().invoke(main, [
            'profile-vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--query-ref-header', 'user_ref',
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'exactly one of --ref-fasta or --query-ref-header' in result.output

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
            'profile-vcf',
            '--project', str(fasta_db_multi_reference),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        html = (output_dir / f'{vcf_path.stem}.report.html').read_text()
        assert 'refB' in html
        assert 'Organism B' in html

    def test_header_only_profile_reuses_stored_query_reference(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        fasta_path = tmp_path / 'user_ref.fasta'
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        fasta_path.write_text(f'>user_ref\n{query}\n')

        conn = open_project_db(fasta_db)
        resolve_fasta_query(conn, fasta_path)
        conn.close()

        vcf_path = tmp_path / 'header_hit.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'user_ref\t9\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        output_dir = tmp_path / 'header_results'
        result = CliRunner().invoke(main, [
            'profile-vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--query-ref-header', 'user_ref',
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '1 database hit' in result.output
        assert (output_dir / f'{vcf_path.stem}.report.html').exists()

    def test_header_only_profile_reports_available_headers(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        fasta_path = tmp_path / 'user_ref.fasta'
        fasta_path.write_text(f'>stored_ref\n{TINY_REF_SEQ}\n')

        conn = open_project_db(fasta_db)
        resolve_fasta_query(conn, fasta_path)
        conn.close()

        vcf_path = tmp_path / 'missing_header.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'stored_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )

        result = CliRunner().invoke(main, [
            'profile-vcf',
            '--project', str(fasta_db),
            '--vcf', str(vcf_path),
            '--query-ref-header', 'missing_ref',
            '--output', str(tmp_path / 'out'),
        ])

        assert result.exit_code != 0
        assert 'Available cached headers: stored_ref' in result.output

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
            'profile-vcf',
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
        result = runner.invoke(main, [
            'profile-vcf',
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
        result = runner.invoke(main, [
            'profile-vcf',
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
            gene=gene, identity=0.0, coverage=0.0,
            query_start=0, query_end=len(query), strand=strand, cigar='',
        )
    result = _align_cds_to_query(gene.nt_sequence.upper(), query.upper(), strand)
    return GeneMatch(
        gene=gene,
        identity=result.identity,
        coverage=result.coverage,
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
        anns = profile_fasta_consensus(_SIMPLE_CDS, [match])
        assert anns == []

    def test_missense_snp_detected(self, simple_gene: GeneRecord) -> None:
        """Single K→E substitution at codon 1 → one missense annotation."""
        # ATGGAAGCTTAA: codon 1 = GAA = E (was AAA = K)
        query = 'ATGGAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        ann = anns[0]
        assert ann.gene_name == 'gag'
        assert ann.ref_aa == 'K'
        assert ann.alt_aa == 'E'
        assert ann.consequence == 'missense'
        assert ann.codon_pos == 1
        assert ann.variant.allele_freq == pytest.approx(1.0)

    def test_synonymous_change_not_emitted(self, simple_gene: GeneRecord) -> None:
        """Synonymous codon change produces no annotations."""
        # AAA → AAG: both encode K
        query = 'ATGAAGGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])
        assert anns == []

    def test_stop_gained(self, simple_gene: GeneRecord) -> None:
        """K → * at codon 1 → stop_gained annotation."""
        # ATGTAAGCTTAA: codon 1 = TAA = *
        query = 'ATGTAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        assert anns[0].alt_aa == '*'
        assert anns[0].consequence == 'stop_gained'

    def test_frameshift_insertion_stops_processing(self, simple_gene: GeneRecord) -> None:
        """Single-base insertion → frameshift annotation; no further codons processed."""
        # Insert 'A' after codon 1 → ATGAAAAGCTTAA (13 nt): frameshift at some codon
        query = 'ATGAAAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        assert anns[0].consequence == 'frameshift'
        assert anns[0].alt_aa == 'fsX'

    def test_inframe_insertion_continues_processing(self, simple_gene: GeneRecord) -> None:
        """Three-base insertion → insertion annotation; subsequent codons still processed."""
        # 'ATGAAAGGGGCTTAA' = ATG AAA [GGG inserted] GCT TAA (15 nt)
        query = 'ATGAAAGGGGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

        # One insertion at codon 2 (A→AG), no downstream frameshifts
        assert any(a.consequence == 'insertion' for a in anns)
        assert not any(a.consequence == 'frameshift' for a in anns)

    def test_frameshift_deletion_stops_processing(self, simple_gene: GeneRecord) -> None:
        """Single-base deletion → frameshift; subsequent codons not emitted."""
        # 'ATGAAGCTTAA' (11 nt): 1 base deleted from codon 1 → frameshift
        query = 'ATGAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

        assert len(anns) == 1
        assert anns[0].consequence == 'frameshift'
        assert anns[0].alt_aa == 'fsX'

    def test_iupac_ambiguous_base_emits_split_frequency(self, simple_gene: GeneRecord) -> None:
        """IUPAC 'R' at codon 1 → K (ref) or E: only E emitted with af=0.5."""
        # 'ATGRAAGCTTAA': codon 1 = RAA = AAA (K) or GAA (E)
        query = 'ATGRAAGCTTAA'
        match = _make_match(simple_gene, query)
        anns = profile_fasta_consensus(query, [match])

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
        anns = profile_fasta_consensus(query, [match])

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
        anns = profile_fasta_consensus(_SIMPLE_CDS, [match])
        assert anns == []


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
        result = CliRunner().invoke(main, [
            'profile-fasta',
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
        result = CliRunner().invoke(main, [
            'profile-fasta',
            '--project', str(fasta_db),
            '--fasta', str(fasta_path),
            '--output', str(output_dir),
        ])

        assert result.exit_code == 0, result.output
        assert '0 database hit' in result.output



