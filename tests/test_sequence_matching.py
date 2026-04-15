"""
Tests for sequence alignment — CDS alignment, CIGAR mapping, and DB caching.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from Bio.Seq import Seq

import respro.core.alignment as alignment
from respro.core.alignment import (
    _swap_cigar_indels,
    cigar_to_coordinate_map,
    load_cached_mappings,
    load_genes_with_rules,
    match_query_to_genes,
    parse_cigar,
    sequence_checksum,
    store_mappings,
)
from respro.db.models import GeneMatch
from respro.db.models import GeneRecord
from respro.db.schema import create_schema


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

_CDS_SEQ = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt, 10 codons


@pytest.fixture()
def gene_with_rules() -> GeneRecord:
    return GeneRecord(
        id=1,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=30,
        strand='+',
        codon_start=0,
        nt_sequence=_CDS_SEQ,
        aa_sequence='MKAFGPKFGP',
    )


@pytest.fixture()
def gene_no_seq() -> GeneRecord:
    return GeneRecord(
        id=2, reference_id=1, name='empty', protein='', start=0, end=30,
        strand='+', codon_start=0, nt_sequence='', aa_sequence='',
    )


@pytest.fixture()
def project_db(tmp_path: Path) -> Path:
    db_path = tmp_path / 'test_project.db'
    conn = create_schema(db_path)
    conn.execute(
        'INSERT INTO project (name, schema_version) VALUES (?, ?)', ('Test', 15),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
        (1, 'ref1', 30),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
        (1, 'ref2', 30),
    )
    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (1, 'gag', 'Gag', 0, 30, '+', _CDS_SEQ, 'MKAFGPKFGP'),
    )
    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (1, 'pol', 'Pol', 30, 60, '+', 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC', 'MKAFGPKFGP'),
    )
    conn.execute(
        'INSERT INTO gene (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (2, 'rt', 'RT', 0, 30, '+', _CDS_SEQ, 'MKAFGPKFGP'),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'drugx'))
    # Only gag has a rule
    conn.execute(
        'INSERT INTO resistance_rule (gene_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.execute(
        'INSERT INTO resistance_rule (gene_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (3, 1, 2, 'A', 'V', 'resistant'),
    )
    conn.commit()
    conn.close()
    return db_path


# ──────────────────────────────────────────────────────────────────────
# CIGAR parsing
# ──────────────────────────────────────────────────────────────────────

class TestParseCigar:
    def test_simple_match(self) -> None:
        assert parse_cigar('30M') == [(30, 'M')]

    def test_mixed_operations(self) -> None:
        assert parse_cigar('10M2I5M1D8M') == [
            (10, 'M'), (2, 'I'), (5, 'M'), (1, 'D'), (8, 'M'),
        ]

    def test_empty_string(self) -> None:
        assert parse_cigar('') == []


# ──────────────────────────────────────────────────────────────────────
# Coordinate mapping
# ──────────────────────────────────────────────────────────────────────

class TestCigarToCoordinateMap:
    def test_perfect_match(self) -> None:
        coord = cigar_to_coordinate_map('30M', query_start=0)
        assert len(coord) == 30
        assert coord[0] == 0
        assert coord[29] == 29

    def test_with_offset(self) -> None:
        coord = cigar_to_coordinate_map('10M', query_start=100)
        assert coord[0] == 100
        assert coord[9] == 109

    def test_insertion_in_query(self) -> None:
        coord = cigar_to_coordinate_map('5M3I5M', query_start=0)
        assert len(coord) == 10
        assert coord[4] == 4
        # After 3-base insertion in query, CDS pos 5 maps to query pos 8
        assert coord[5] == 8

    def test_deletion_in_query(self) -> None:
        coord = cigar_to_coordinate_map('5M2D5M', query_start=0)
        assert len(coord) == 12
        assert coord[4] == 4
        assert coord[5] is None
        assert coord[6] is None
        assert coord[7] == 5


# ──────────────────────────────────────────────────────────────────────
# Sequence matching
# ──────────────────────────────────────────────────────────────────────

class TestMatchQueryToGenes:
    def test_exact_match(self, gene_with_rules: GeneRecord) -> None:
        matches = match_query_to_genes(_CDS_SEQ, [gene_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.gene.name == 'gag'
        assert m.identity == pytest.approx(1.0)
        assert m.cds_coverage == pytest.approx(1.0)
        assert m.query_coverage == pytest.approx(1.0)
        assert m.strand == '+'
        assert m.cigar == '30M'

    def test_cds_within_larger_query(self, gene_with_rules: GeneRecord) -> None:
        flanking = 'NNNNNNNNNN'
        query = flanking + _CDS_SEQ + flanking
        matches = match_query_to_genes(query, [gene_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0)
        assert m.query_start == 10
        assert m.query_end == 40

    def test_snps_reduce_identity(self, gene_with_rules: GeneRecord) -> None:
        # Introduce mismatches in the middle so the local aligner must include them
        mutated = list(_CDS_SEQ)
        mutated[10] = 'A' if mutated[10] != 'A' else 'C'
        mutated[20] = 'A' if mutated[20] != 'A' else 'C'
        query = ''.join(mutated)
        matches = match_query_to_genes(query, [gene_with_rules], min_identity=0.80)
        assert len(matches) == 1
        assert matches[0].identity < 1.0

    def test_unrelated_sequence_rejected(self, gene_with_rules: GeneRecord) -> None:
        unrelated = 'GATTACA' * 10
        matches = match_query_to_genes(unrelated, [gene_with_rules])
        assert len(matches) == 0

    def test_skips_gene_without_nt_sequence(self, gene_no_seq: GeneRecord) -> None:
        matches = match_query_to_genes(_CDS_SEQ, [gene_no_seq])
        assert len(matches) == 0

    def test_reverse_complement_match(self) -> None:
        from Bio.Seq import Seq
        cds = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
        rc_cds = str(Seq(cds).reverse_complement())
        gene = GeneRecord(
            id=1, reference_id=1, name='minus_gene', protein='',
            start=0, end=30, strand='-', codon_start=0,
            nt_sequence=cds, aa_sequence='',
        )
        # Query is the RC of the CDS → should match on '-' strand
        matches = match_query_to_genes(rc_cds, [gene])
        assert len(matches) == 1
        assert matches[0].strand == '-'
        assert matches[0].identity == pytest.approx(1.0)

    def test_partial_query_accepted_via_query_coverage(self, gene_with_rules: GeneRecord) -> None:
        # A short prefix of the CDS — CDS coverage will be well below 0.90,
        # but query coverage should be ~1.0 (the whole query aligns).
        partial = _CDS_SEQ[:12]  # first 12 of 30 nt → 40% CDS coverage
        matches = match_query_to_genes(partial, [gene_with_rules], min_coverage=0.90)
        assert len(matches) == 1
        m = matches[0]
        assert m.query_coverage == pytest.approx(1.0)
        assert m.cds_coverage < 0.90  # confirms it would have been rejected under old logic

    def test_cds_and_query_coverage_both_stored(self, gene_with_rules: GeneRecord) -> None:
        matches = match_query_to_genes(_CDS_SEQ, [gene_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.cds_coverage == pytest.approx(1.0)
        assert m.query_coverage == pytest.approx(1.0)


class TestAlignerOverflowSafety:
    def test_align_does_not_use_len_on_alignments(self, monkeypatch) -> None:
        class _FakeAlignments:
            def __len__(self) -> int:
                raise OverflowError('too many optimal alignments')

            def __getitem__(self, idx: int):
                if idx != 0:
                    raise IndexError
                return object()

        class _FakeAligner:
            def __init__(self) -> None:
                self.mode = 'local'
                self.match_score = 1.0
                self.mismatch_score = -1.0
                self.open_gap_score = -2.0
                self.extend_gap_score = -0.5

            def align(self, query: str, cds: str):
                return _FakeAlignments()

        monkeypatch.setattr(alignment, 'PairwiseAligner', _FakeAligner)
        monkeypatch.setattr(
            alignment,
            '_alignment_to_cigar',
            lambda alignment, query, cds: ('10M', 1.0, 10, 0, 10, 0),
        )

        result = alignment._align_cds_to_query('ATGATGATGA', 'ATGATGATGA', '+')
        assert result.identity == pytest.approx(1.0)
        assert result.cigar == '10M'


# ──────────────────────────────────────────────────────────────────────
# DB: load_genes_with_rules
# ──────────────────────────────────────────────────────────────────────

class TestLoadGenesWithRules:
    def test_returns_only_genes_with_rules(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)
        genes = load_genes_with_rules(conn, reference_id=1)
        conn.close()
        names = {g.name for g in genes}
        assert 'gag' in names
        assert 'pol' not in names

    def test_without_reference_filter_loads_all_rule_genes(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)
        genes = load_genes_with_rules(conn)
        conn.close()
        names = {g.name for g in genes}
        assert names == {'gag', 'rt'}


# ──────────────────────────────────────────────────────────────────────
# DB caching
# ──────────────────────────────────────────────────────────────────────

class TestDbCaching:
    def test_store_and_load_roundtrip(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)

        genes = load_genes_with_rules(conn, reference_id=1)
        matches = match_query_to_genes(_CDS_SEQ, genes)
        assert len(matches) == 1

        chk = sequence_checksum(_CDS_SEQ)
        store_mappings(conn, 'test_ref', _CDS_SEQ, chk, matches)

        loaded = load_cached_mappings(conn, chk)
        conn.close()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].gene.name == 'gag'
        assert loaded[0].cigar == matches[0].cigar
        assert loaded[0].identity == pytest.approx(matches[0].identity)
        assert loaded[0].cds_coverage == pytest.approx(matches[0].cds_coverage)
        assert loaded[0].query_coverage == pytest.approx(matches[0].query_coverage)

    def test_cache_miss_returns_none(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)
        result = load_cached_mappings(conn, 'nonexistent_checksum')
        conn.close()
        assert result is None

    def test_checksum_is_deterministic(self) -> None:
        a = sequence_checksum('ATGCCC')
        b = sequence_checksum('atgccc')
        assert a == b
        assert len(a) == 64


# ──────────────────────────────────────────────────────────────────────
# mappy backend
# ──────────────────────────────────────────────────────────────────────

# A realistic gene-scale CDS (repeated to exceed mappy's minimum k-mer seeding length)
_LONG_CDS = (_CDS_SEQ * 80)[:2000]  # ~2 KB


class TestSwapCigarIndels:
    def test_swaps_i_and_d(self) -> None:
        assert _swap_cigar_indels('10M2I5M1D8M') == '10M2D5M1I8M'

    def test_no_indels_unchanged(self) -> None:
        assert _swap_cigar_indels('30M') == '30M'

    def test_multiple_indels(self) -> None:
        assert _swap_cigar_indels('100M3I50M2D10M') == '100M3D50M2I10M'

    def test_empty_string(self) -> None:
        assert _swap_cigar_indels('') == ''


class TestMappyBackend:
    """Tests for the mappy alignment backend via match_query_to_genes(..., aligner='mappy')."""

    def _make_gene(
        self, cds: str, strand: str = '+', name: str = 'gene1',
    ) -> GeneRecord:
        return GeneRecord(
            id=1, reference_id=1, name=name, protein='',
            start=0, end=len(cds), strand=strand, codon_start=0,
            nt_sequence=cds, aa_sequence='',
        )

    def test_exact_match_forward_strand(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        matches = match_query_to_genes(_LONG_CDS, [gene], aligner='mappy')
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0, abs=0.01)
        assert m.cds_coverage == pytest.approx(1.0, abs=0.01)
        assert m.strand == '+'

    def test_cds_within_larger_query(self) -> None:
        flanking = 'A' * 500
        query = flanking + _LONG_CDS + flanking
        gene = self._make_gene(_LONG_CDS)
        matches = match_query_to_genes(query, [gene], aligner='mappy')
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0, abs=0.01)
        # CDS starts at position 500 in the query
        assert m.query_start == 500
        assert m.query_end == 500 + len(_LONG_CDS)

    def test_reverse_complement_match(self) -> None:
        rc_cds = str(Seq(_LONG_CDS).reverse_complement())
        flanking = 'A' * 500
        # CDS appears in reverse orientation relative to the query
        query = flanking + rc_cds + flanking
        gene = self._make_gene(_LONG_CDS, strand='-')
        matches = match_query_to_genes(query, [gene], aligner='mappy')
        assert len(matches) == 1
        assert matches[0].strand == '-'
        assert matches[0].identity == pytest.approx(1.0, abs=0.01)

    def test_snps_reduce_identity(self) -> None:
        # Introduce ~5% mismatches
        mutated = list(_LONG_CDS)
        for i in range(0, len(mutated), 20):
            mutated[i] = 'A' if mutated[i] != 'A' else 'C'
        query = ''.join(mutated)
        gene = self._make_gene(_LONG_CDS)
        matches = match_query_to_genes(query, [gene], min_identity=0.80, aligner='mappy')
        assert len(matches) == 1
        assert matches[0].identity < 1.0

    def test_unrelated_sequence_rejected(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        unrelated = 'GATTACA' * 300
        matches = match_query_to_genes(unrelated, [gene], aligner='mappy')
        assert len(matches) == 0

    def test_skips_gene_without_nt_sequence(self) -> None:
        gene = GeneRecord(
            id=2, reference_id=1, name='empty', protein='', start=0, end=30,
            strand='+', codon_start=0, nt_sequence='', aa_sequence='',
        )
        matches = match_query_to_genes(_LONG_CDS, [gene], aligner='mappy')
        assert len(matches) == 0

    def test_cigar_coordinate_map_compatible(self) -> None:
        """CIGAR produced by mappy backend must generate a valid coordinate map."""
        flanking = 'T' * 300
        query = flanking + _LONG_CDS + flanking
        gene = self._make_gene(_LONG_CDS)
        matches = match_query_to_genes(query, [gene], aligner='mappy')
        assert len(matches) == 1
        m = matches[0]
        coord = cigar_to_coordinate_map(m.cigar, m.query_start)
        # Position 0 in CDS should map to query position 300 (after flanking)
        assert coord[0] == 300


class TestMappyPairwiseEquivalence:
    """Verify that mappy and pairwise backends agree on matches and coordinates."""

    def _make_gene(self, cds: str, name: str = 'gene') -> GeneRecord:
        return GeneRecord(
            id=1, reference_id=1, name=name, protein='',
            start=0, end=len(cds), strand='+', codon_start=0,
            nt_sequence=cds, aa_sequence='',
        )

    def test_same_gene_selected(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        pw = match_query_to_genes(_LONG_CDS, [gene], aligner='pairwise')
        mm = match_query_to_genes(_LONG_CDS, [gene], aligner='mappy')
        assert len(pw) == len(mm) == 1
        assert pw[0].gene.name == mm[0].gene.name

    def test_comparable_identity(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        query = 'G' * 200 + _LONG_CDS + 'G' * 200
        pw = match_query_to_genes(query, [gene], aligner='pairwise')
        mm = match_query_to_genes(query, [gene], aligner='mappy')
        assert len(pw) == len(mm) == 1
        # Both should report near-perfect identity for an exact match
        assert pw[0].identity == pytest.approx(mm[0].identity, abs=0.02)

    def test_same_query_start(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        query = 'T' * 400 + _LONG_CDS + 'T' * 400
        pw = match_query_to_genes(query, [gene], aligner='pairwise')
        mm = match_query_to_genes(query, [gene], aligner='mappy')
        assert len(pw) == len(mm) == 1
        assert pw[0].query_start == mm[0].query_start

    def test_both_reject_below_threshold(self) -> None:
        gene = self._make_gene(_LONG_CDS)
        unrelated = 'GATTACAGATTACA' * 200
        pw = match_query_to_genes(unrelated, [gene], aligner='pairwise')
        mm = match_query_to_genes(unrelated, [gene], aligner='mappy')
        assert pw == mm == []

