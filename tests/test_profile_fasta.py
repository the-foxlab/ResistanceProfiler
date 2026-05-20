"""
Tests for FASTA-based profiling — coordinate remapping, FASTA consensus profiling,
and end-to-end CLI workflow.
"""

from __future__ import annotations

import random
import re
from inspect import signature
from pathlib import Path

import pytest
from Bio.Seq import Seq
from conftest import TINY_REF_NAME, TINY_REF_SEQ
from typer.testing import CliRunner

from respro.cli.main import app
from respro.core.alignment import (
    load_cached_mappings,
    match_query_to_features,
    sequence_checksum,
    store_mappings,
)
from respro.core.annotation import annotate_variants
from respro.core.fasta_to_vcf import (
    _make_variant_from_coding_nt,
    _variants_from_alignment,
    fasta_to_vcf,
)
from respro.core.query import (
    resolve_cached_query_reference,
    resolve_fasta_query,
)
from respro.core.vcf_remap import (
    _build_query_to_cds_map,
    remap_variants,
)
from respro.db.models import FeatureMatch, FeatureRecord, FeatureSegment, VariantCall
from respro.db.schema import create_schema, open_project_db


def _strip_ansi(text: str) -> str:
    """Return text with ANSI escape sequences removed."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

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
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, '
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
        '(feature_id, drug_id, position, reference, mutation, phenotype) '
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
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gagA', 'GagA', 0, 30, '+', ref_a_seq),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (2, 'gagB', 'GagB', 0, 30, '+', ref_b_seq),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
    conn.execute(
        'INSERT INTO resistance_rule '
        '(feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.execute(
        'INSERT INTO resistance_rule '
        '(feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (2, 1, 1, 'P', 'A', 'resistant'),
    )
    conn.commit()
    conn.close()
    return db_path

@pytest.fixture()
def feature_fwd() -> FeatureRecord:
    """Forward-strand feature spanning the tiny reference."""
    return FeatureRecord(
        id=1, reference_id=1, name='gag', protein='Gag',
        start=0, end=87, strand='+', codon_start=0,
        nt_sequence=TINY_REF_SEQ,
    )

def _split_feature(*, strand: str) -> FeatureRecord:
    """Build a two-segment CDS with a non-coding envelope gap."""
    return FeatureRecord(
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
            FeatureSegment(segment_index=0, start=0, end=6),
            FeatureSegment(segment_index=1, start=12, end=18),
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

class TestCdsToGenomicPosition:
    def test_forward_strand(self) -> None:
        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=10, end=40, strand='+', codon_start=0,
        )
        assert feature.cds_to_genomic_position(0) == 10
        assert feature.cds_to_genomic_position(29) == 39

    def test_reverse_strand(self) -> None:
        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=10, end=40, strand='-', codon_start=0,
        )
        assert feature.cds_to_genomic_position(0) == 39
        assert feature.cds_to_genomic_position(29) == 10

    def test_roundtrip(self) -> None:
        """cds_to_genomic_position and genomic_to_cds_position should be inverses."""
        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=5, end=35, strand='+', codon_start=0,
        )
        for cds in range(30):
            genomic = feature.cds_to_genomic_position(cds)
            assert feature.genomic_to_cds_position(genomic) == cds

    def test_roundtrip_reverse_strand(self) -> None:
        feature = FeatureRecord(
            id=1, reference_id=1, name='g', protein='',
            start=5, end=35, strand='-', codon_start=0,
        )
        for cds in range(30):
            genomic = feature.cds_to_genomic_position(cds)
            assert feature.genomic_to_cds_position(genomic) == cds

    def test_split_feature_roundtrip_forward_strand(self) -> None:
        feature = _split_feature(strand='+')

        assert feature.contains(0)
        assert feature.contains(12)
        assert not feature.contains(6)
        assert feature.genomic_to_cds_position(0) == 0
        assert feature.genomic_to_cds_position(5) == 5
        assert feature.genomic_to_cds_position(12) == 6
        assert feature.genomic_to_cds_position(17) == 11
        assert feature.genomic_to_cds_position(6) is None
        assert feature.cds_to_genomic_position(0) == 0
        assert feature.cds_to_genomic_position(5) == 5
        assert feature.cds_to_genomic_position(6) == 12
        assert feature.cds_to_genomic_position(11) == 17

    def test_split_feature_roundtrip_reverse_strand(self) -> None:
        feature = _split_feature(strand='-')

        assert feature.contains(0)
        assert feature.contains(17)
        assert not feature.contains(6)
        assert feature.genomic_to_cds_position(17) == 0
        assert feature.genomic_to_cds_position(12) == 5
        assert feature.genomic_to_cds_position(5) == 6
        assert feature.genomic_to_cds_position(0) == 11
        assert feature.genomic_to_cds_position(6) is None
        assert feature.cds_to_genomic_position(0) == 17
        assert feature.cds_to_genomic_position(5) == 12
        assert feature.cds_to_genomic_position(6) == 5
        assert feature.cds_to_genomic_position(11) == 0

class TestSplitFeatureFastaProjection:
    def test_synthetic_variant_projection_uses_segment_coordinates(self) -> None:
        feature = _split_feature(strand='+')

        nt_variant = _make_variant_from_coding_nt(feature, 7, 'G', 'A')

        assert nt_variant.pos == 13

# ──────────────────────────────────────────────────────────────────────
# Unit tests: remap_variants
# ──────────────────────────────────────────────────────────────────────

class TestRemapVariants:
    def test_exact_match_remaps_position(self, feature_fwd: FeatureRecord) -> None:
        """Variant at flanked query pos 8 remaps to internal pos 3."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_features(query, [feature_fwd])
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

    def test_variant_outside_cds_excluded(self, feature_fwd: FeatureRecord) -> None:
        """Variant in flanking region should be silently excluded."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_features(query, [feature_fwd])

        variants = [
            VariantCall(
                chrom='user_ref', pos=1, ref='N', alt='A',
                allele_freq=0.5, depth=50,
            ),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(remapped) == 0
        assert len(warnings) == 0

    def test_ref_base_mismatch_warns(self, feature_fwd: FeatureRecord) -> None:
        """VCF REF disagreeing with FASTA should produce a warning."""
        query = TINY_REF_SEQ
        matches = match_query_to_features(query, [feature_fwd])

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
        self, feature_fwd: FeatureRecord,
    ) -> None:
        """Mixed set: one variant inside CDS, one outside."""
        query = 'NNNNN' + TINY_REF_SEQ + 'NNNNN'
        matches = match_query_to_features(query, [feature_fwd])

        variants = [
            VariantCall(chrom='c', pos=2, ref='N', alt='A', allele_freq=0.5, depth=50),
            VariantCall(chrom='c', pos=8, ref='A', alt='G', allele_freq=0.9, depth=100),
        ]
        remapped, warnings = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].pos == 3

    def test_preserves_allele_freq_and_depth(self, feature_fwd: FeatureRecord) -> None:
        """AF and depth should be carried through from the original variant."""
        query = TINY_REF_SEQ
        matches = match_query_to_features(query, [feature_fwd])

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

    def test_snp_stores_query_ref_codon(self, feature_fwd: FeatureRecord) -> None:
        """SNP remapping stores the query codon for downstream SNP annotation."""
        query = TINY_REF_SEQ
        matches = match_query_to_features(query, [feature_fwd])

        variants = [
            VariantCall(chrom='c', pos=3, ref='A', alt='G', allele_freq=0.4, depth=20),
        ]
        remapped, _ = remap_variants(variants, matches, query)

        assert len(remapped) == 1
        assert remapped[0].query_ref_codon == 'AAA'

    def test_snp_stores_query_ref_codon_for_reverse_match(self) -> None:
        """Reverse-strand matches must reconstruct query codon in CDS orientation."""
        feature_rev = FeatureRecord(
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
            FeatureMatch(
                feature=feature_rev,
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
    def test_defaults_use_fixed_alignment_constants(self) -> None:
        parameters = signature(resolve_fasta_query).parameters
        assert parameters['min_identity'].default == 0.9

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
        assert matches[0].feature.name == 'gag'

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
        assert matches[0].feature.name == 'gag'
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

        with pytest.raises(ValueError, match='no cached feature mappings'):
            resolve_cached_query_reference(conn, 'orphan_ref')
        conn.close()

    def test_ambiguous_header_raises(self, fasta_db: Path) -> None:
        conn = open_project_db(fasta_db)
        features = [
            FeatureRecord(
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
        matches_one = match_query_to_features(query_one, features)
        matches_two = match_query_to_features(query_two, features)
        store_mappings(conn, 'dup_ref', query_one, sequence_checksum(query_one), matches_one)
        store_mappings(conn, 'dup_ref', query_two, sequence_checksum(query_two), matches_two)

        with pytest.raises(ValueError, match='ambiguous'):
            resolve_cached_query_reference(conn, 'dup_ref')
        conn.close()


class TestFastaCacheRegression:
    def test_cached_minus_strand_partial_coverage_matches_uncached(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'minus_cache_regression.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('Minus Cache Regression', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref1', 18),
        )
        feature_nt = 'ATGCAAGTCGGAAACTAA'
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (1, 'minus_feature', 'Minus', 0, 18, '-', feature_nt),
        )
        conn.execute('UPDATE feature SET codon_start = ? WHERE id = ?', (1, 1))
        conn.commit()

        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='minus_feature',
            protein='Minus',
            start=0,
            end=18,
            strand='-',
            codon_start=1,
            nt_sequence=feature_nt,
        )
        coding_region = 'NNNNN' + feature_nt[6:]
        query_seq = str(Seq(coding_region).reverse_complement())
        direct_match = FeatureMatch(
            feature=feature,
            identity=1.0,
            cds_coverage=12 / 18,
            query_coverage=1.0,
            query_start=0,
            query_end=len(query_seq),
            strand='-',
            cigar='5I12M',
            cds_start=6,
        )

        uncached_variants, uncached_gaps = fasta_to_vcf(query_seq, [direct_match])
        assert not any(len(v.alt) > len(v.ref) for v in uncached_variants)

        checksum = sequence_checksum(query_seq)
        store_mappings(conn, 'minus_cached', query_seq, checksum, [direct_match])
        loaded = load_cached_mappings(conn, checksum)
        conn.close()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].cds_start == 6

        cached_variants, cached_gaps = fasta_to_vcf(query_seq, loaded)

        uncached_signature = sorted((v.pos, v.ref, v.alt) for v in uncached_variants)
        cached_signature = sorted((v.pos, v.ref, v.alt) for v in cached_variants)
        assert cached_signature == uncached_signature
        assert [(g.feature_name, g.codon_start, g.codon_end) for g in cached_gaps] == [
            (g.feature_name, g.codon_start, g.codon_end) for g in uncached_gaps
        ]
        assert not any(len(v.alt) > len(v.ref) for v in cached_variants)

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
        clean_output = _strip_ansi(result.output)
        assert 'Missing option' in clean_output
        assert '--ref-fasta' in clean_output

    def test_fasta_profile_uses_metadata_of_matched_reference(
        self, fasta_db_multi_reference: Path, tmp_path: Path,
    ) -> None:
        """Report metadata should come from the reference of the matched feature."""
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
def simple_feature() -> FeatureRecord:
    """Minimal 4-codon feature for FASTA consensus profiling tests."""
    return FeatureRecord(
        id=1, reference_id=1, name='gag', protein='Gag',
        start=0, end=12, strand='+', codon_start=0,
        nt_sequence=_SIMPLE_CDS,
        aa_sequence='MKA*',
    )

class TestFastaToVcf:
    def test_compacts_consecutive_deletion_run_into_single_variant(
        self,
        simple_feature: FeatureRecord,
    ) -> None:
        """A contiguous 3-nt deletion run should emit one compacted deletion variant."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATG---GCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert gaps == []
        deletion_variants = [variant for variant in variants if len(variant.ref) > len(variant.alt)]
        assert len(deletion_variants) == 1
        deletion = deletion_variants[0]
        assert deletion.ref == 'GAAA'
        assert deletion.alt == 'G'

    def test_iupac_snp_emits_fractional_variants(self, simple_feature: FeatureRecord) -> None:
        """IUPAC bases should split into per-base alternatives with fractional AF."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATGRAAGCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert gaps == []
        assert len(variants) == 1
        variant = variants[0]
        assert variant.ref == 'A'
        assert variant.alt == 'G'
        assert variant.allele_freq == pytest.approx(1.0)
        assert variant.query_ref_codon == 'RAA'

    def test_full_n_codon_is_coverage_gap_and_not_emitted_as_variants(
        self,
        simple_feature: FeatureRecord,
    ) -> None:
        """A full NNN codon should be marked as uncovered and skipped for variant emission."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATGNNNGCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert variants == []
        assert len(gaps) == 1
        assert gaps[0].feature_name == simple_feature.name
        assert gaps[0].codon_start == 1
        assert gaps[0].codon_end == 1

    def test_partial_n_codon_remains_assessable_for_iupac_variants(
        self,
        simple_feature: FeatureRecord,
    ) -> None:
        """Partial ambiguity (e.g. AAN) should stay assessable and emit IUPAC SNP variants."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATGAANGCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert gaps == []
        assert len(variants) == 3
        assert {variant.alt for variant in variants} == {'C', 'G', 'T'}
        assert all(variant.ref == 'A' for variant in variants)
        assert all(variant.allele_freq == pytest.approx(1 / 3) for variant in variants)
        assert all(variant.query_ref_codon == 'AAN' for variant in variants)

    def test_minus_strand_insertion_keeps_anchor_first_in_genomic_orientation(self) -> None:
        """Minus-strand insertion should emit VCF-style REF/ALT with anchor at ALT start."""
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='rev',
            protein='Rev',
            start=0,
            end=6,
            strand='-',
            codon_start=0,
            nt_sequence='CTCATC',
            aa_sequence='',
        )
        aligned_ref = 'CTC------ATC'
        aligned_query = 'CTCCCCAAAATC'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            feature,
            covered_cds_start=0,
            covered_cds_end=6,
        )

        assert gaps == []
        assert len(variants) == 1
        variant = variants[0]
        assert variant.pos == 2
        assert variant.ref == 'T'
        assert variant.alt == 'TTTTGGG'

    def test_insertion_uses_reference_anchor_even_if_query_anchor_is_snp(
        self,
        simple_feature: FeatureRecord,
    ) -> None:
        """Insertion REF/ALT must remain reference-anchored even with anchor SNP in query."""
        aligned_ref = 'ATGA-AAGCTTAA'
        aligned_query = 'ATGGTAAGCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert gaps == []
        insertion_variants = [v for v in variants if len(v.alt) > len(v.ref)]
        assert len(insertion_variants) == 1
        insertion = insertion_variants[0]
        assert insertion.pos == 3
        assert insertion.ref == 'A'
        assert insertion.alt == 'AT'

    def test_minus_strand_deletion_keeps_anchor_first_in_genomic_orientation(self) -> None:
        """Minus-strand deletion should emit VCF-style REF/ALT with anchor at REF start."""
        feature = FeatureRecord(
            id=1,
            reference_id=1,
            name='rev',
            protein='Rev',
            start=0,
            end=8,
            strand='-',
            codon_start=0,
            nt_sequence='CTCAAATC',
            aa_sequence='',
        )
        aligned_ref = 'CTCAAATC'
        aligned_query = 'CTC---TC'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            feature,
            covered_cds_start=0,
            covered_cds_end=8,
        )

        assert gaps == []
        assert len(variants) == 1
        variant = variants[0]
        assert variant.pos == 1
        assert variant.ref == 'ATTT'
        assert variant.alt == 'A'

    def test_deletion_uses_reference_anchor_even_if_query_anchor_is_snp(
        self,
        simple_feature: FeatureRecord,
    ) -> None:
        """Deletion REF/ALT must remain reference-anchored even with anchor SNP in query."""
        aligned_ref = 'ATGAAAGCTTAA'
        aligned_query = 'ATGG--GCTTAA'

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            simple_feature,
            covered_cds_start=0,
            covered_cds_end=12,
        )

        assert gaps == []
        deletion_variants = [v for v in variants if len(v.ref) > len(v.alt)]
        assert len(deletion_variants) == 1
        deletion = deletion_variants[0]
        assert deletion.pos == 3
        assert deletion.ref == 'AAA'
        assert deletion.alt == 'A'

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

    def test_fasta_consensus_writes_repeated_export_formats(
        self, fasta_db: Path, tmp_path: Path,
    ) -> None:
        fasta_path = tmp_path / 'identical_multi_export.fasta'
        fasta_path.write_text(f'>identical\n{TINY_REF_SEQ}\n')

        output_dir = tmp_path / 'out_multi_export'
        result = CliRunner().invoke(app, [
            'fasta',
            '--project', str(fasta_db),
            '--fasta', str(fasta_path),
            '--output', str(output_dir),
            '--export', 'json',
            '--export', 'tabular',
        ])

        assert result.exit_code == 0, result.output
        html_path = output_dir / f'{fasta_path.stem}.report.html'
        json_path = output_dir / f'{fasta_path.stem}.results.json'
        tsv_path = output_dir / f'{fasta_path.stem}.mutations.tsv'
        assert html_path.exists()
        assert json_path.exists()
        assert tsv_path.exists()

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
    ) -> list[tuple[int, str, str, str]]:
        query = str(Seq(coding_query).reverse_complement())
        feature = FeatureRecord(
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

        matches = match_query_to_features(
            query,
            [feature],
            min_identity=0.7,
        )
        assert len(matches) == 1
        assert matches[0].strand == '-'

        variants, gaps = fasta_to_vcf(query, [matches[0]])
        assert gaps == []
        annotations = annotate_variants(variants, [feature], is_fasta_mode=True)
        return [
            (ann.codon_pos, ann.consequence, ann.ref_aa, ann.alt_aa)
            for ann in annotations
        ]

    def test_reverse_frameshift_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 901
        coding_query = coding_reference[:event_pos] + coding_reference[event_pos + 1:]

        mappy = self._profile_reverse_query(coding_reference, coding_query)

        assert len(mappy) == 1
        assert mappy[0][1] == 'frameshift'

    def test_reverse_triplet_deletion_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 900
        coding_query = coding_reference[:event_pos] + coding_reference[event_pos + 3:]

        mappy = self._profile_reverse_query(coding_reference, coding_query)

        assert len(mappy) == 1
        assert mappy[0][1] == 'deletion'

    def test_reverse_triplet_insertion_is_single_event_in_mappy(self) -> None:
        coding_reference = self._build_long_coding_reference()
        event_pos = 900
        coding_query = coding_reference[:event_pos] + 'GCC' + coding_reference[event_pos:]

        mappy = self._profile_reverse_query(coding_reference, coding_query)

        assert len(mappy) == 1
        assert mappy[0][1] == 'insertion'

    def test_minus_strand_nucleotide_assessed_in_internal_reference_direction(self) -> None:
        """
        Verify that FASTA-profiled minus-strand variants are assessed in internal
        reference coordinates (like VCF mode).

        For a minus-strand feature, nt_sequence is stored in coding (5'→3') orientation.
       When we profile a query on minus strand, aligned_ref and aligned_query are
        built relative to this internal coding sequence, independent of the
        actual genomic orientation.
        """
        # Tiny 18-nt ref: ATG CAA GTC GGA AAC TAA (M Q V G N * in protein)
        internal_ref = 'ATGCAAGTCGGAAACTAA'

        # Create mutation in internal frame: codon 1 CAA → AAG (Q → K)
        internal_variant = 'ATGAAGGTCGGAAACTAA'

        # Create a genomic RC query (how it appears when matching to minus-strand feature)
        query_on_minus_strand = str(Seq(internal_variant).reverse_complement())

        # After matching and reverse-complement handling in _profile_feature_to_variants,
        # the region gets reverse-complemented back to coding orientation
        region_in_coding_orientation = str(Seq(query_on_minus_strand).reverse_complement())
        assert region_in_coding_orientation == internal_variant

        # Create feature with internal ref
        feature = FeatureRecord(
            id=1, reference_id=1, name='minus_feature', protein='Test',
            start=0, end=18, strand='-', codon_start=0,
            nt_sequence=internal_ref,
        )

        # Directly test variant emission using fasta_to_vcf logic
        # Manually call _variants_from_alignment with aligned strings
        aligned_ref = internal_ref.upper()
        aligned_query = region_in_coding_orientation.upper()

        variants, gaps = _variants_from_alignment(
            aligned_ref,
            aligned_query,
            feature,
            covered_cds_start=0,
            covered_cds_end=18,
        )

        # Annotate the variants
        annotations = annotate_variants(variants, [feature], is_fasta_mode=True)

        # Must detect Q→K at codon 1 in the internal reference frame
        aa_changes = [(a.codon_pos, a.ref_aa, a.alt_aa) for a in annotations]
        assert any(
            pos == 1 and ref == 'Q' and alt == 'K'
            for pos, ref, alt in aa_changes
        ), f'Expected Q→K at codon 1, got: {aa_changes}'
