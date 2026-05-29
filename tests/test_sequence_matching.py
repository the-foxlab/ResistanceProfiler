"""
Tests for sequence alignment — CDS alignment, CIGAR mapping, and DB caching.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from Bio.Seq import Seq

from respro.core.alignment import (
    _normalize_mappy_cigar,
    _reverse_cigar_operations,
    _swap_cigar_indels,
    cigar_to_coordinate_map,
    load_features_with_rules,
    match_query_to_features,
    parse_cigar,
)
from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
from respro.db.models import FeatureMatch, FeatureRecord
from respro.db.schema import create_schema

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

_CDS_SEQ = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt, 10 codons


@pytest.fixture()
def feature_with_rules() -> FeatureRecord:
    return FeatureRecord(
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
def feature_no_seq() -> FeatureRecord:
    return FeatureRecord(
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
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (1, 'gag', 'Gag', 0, 30, '+', _CDS_SEQ, 'MKAFGPKFGP'),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (1, 'pol', 'Pol', 30, 60, '+', 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC', 'MKAFGPKFGP'),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence, aa_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (2, 'rt', 'RT', 0, 30, '+', _CDS_SEQ, 'MKAFGPKFGP'),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'drugx'))
    # Only gag has a rule
    conn.execute(
        'INSERT INTO resistance_rule (feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.execute(
        'INSERT INTO resistance_rule (feature_id, drug_id, position, reference, mutation, phenotype) '
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

class TestMatchQueryToFeatures:
    def test_exact_match(self, feature_with_rules: FeatureRecord) -> None:
        matches = match_query_to_features(_CDS_SEQ, [feature_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.feature.name == 'gag'
        assert m.identity == pytest.approx(1.0)
        assert m.cds_coverage == pytest.approx(1.0)
        assert m.query_coverage == pytest.approx(1.0)
        assert m.strand == '+'
        assert m.cigar == '30M'

    def test_cds_within_larger_query(self, feature_with_rules: FeatureRecord) -> None:
        flanking = 'NNNNNNNNNN'
        query = flanking + _CDS_SEQ + flanking
        matches = match_query_to_features(query, [feature_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0)
        assert m.query_start == 10
        assert m.query_end == 40

    def test_snps_reduce_identity(self, feature_with_rules: FeatureRecord) -> None:
        # Introduce mismatches in the middle so the local aligner must include them
        mutated = list(_CDS_SEQ)
        mutated[10] = 'A' if mutated[10] != 'A' else 'C'
        mutated[20] = 'A' if mutated[20] != 'A' else 'C'
        query = ''.join(mutated)
        matches = match_query_to_features(query, [feature_with_rules], min_identity=0.80)
        assert len(matches) == 1
        assert matches[0].identity < 1.0

    def test_unrelated_sequence_rejected(self, feature_with_rules: FeatureRecord) -> None:
        unrelated = 'GATTACA' * 10
        matches = match_query_to_features(unrelated, [feature_with_rules])
        assert len(matches) == 0

    def test_skips_feature_without_nt_sequence(self, feature_no_seq: FeatureRecord) -> None:
        matches = match_query_to_features(_CDS_SEQ, [feature_no_seq])
        assert len(matches) == 0

    def test_reverse_complement_match(self) -> None:
        from Bio.Seq import Seq
        cds = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
        rc_cds = str(Seq(cds).reverse_complement())
        feature = FeatureRecord(
            id=1, reference_id=1, name='minus_feature', protein='',
            start=0, end=30, strand='-', codon_start=0,
            nt_sequence=cds, aa_sequence='',
        )
        # Query is the RC of the CDS → should match on '-' strand
        matches = match_query_to_features(rc_cds, [feature])
        assert len(matches) == 1
        assert matches[0].strand == '-'
        assert matches[0].identity == pytest.approx(1.0)

    def test_partial_query_accepted_when_identity_passes(self, feature_with_rules: FeatureRecord) -> None:
        # A partial query is accepted when identity passes.
        # CDS coverage is low, but query coverage is ~1.0 for the aligned query.
        long_cds = _CDS_SEQ * 3  # 90 nt
        feature = FeatureRecord(
            id=feature_with_rules.id,
            reference_id=feature_with_rules.reference_id,
            name=feature_with_rules.name,
            protein=feature_with_rules.protein,
            start=0,
            end=len(long_cds),
            strand=feature_with_rules.strand,
            codon_start=feature_with_rules.codon_start,
            nt_sequence=long_cds,
            aa_sequence='',
        )
        partial = long_cds[:36]  # first 36 of 90 nt → 40% CDS coverage
        matches = match_query_to_features(partial, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.query_coverage == pytest.approx(1.0)
        assert m.cds_coverage < 0.90  # confirms it would have been rejected under old logic

    def test_cds_and_query_coverage_both_stored(self, feature_with_rules: FeatureRecord) -> None:
        matches = match_query_to_features(_CDS_SEQ, [feature_with_rules])
        assert len(matches) == 1
        m = matches[0]
        assert m.cds_coverage == pytest.approx(1.0)
        assert m.query_coverage == pytest.approx(1.0)


# ──────────────────────────────────────────────────────────────────────
# DB: load_features_with_rules
# ──────────────────────────────────────────────────────────────────────

class TestLoadFeaturesWithRules:
    def test_returns_only_features_with_rules(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)
        features = load_features_with_rules(conn, reference_id=1)
        conn.close()
        names = {g.name for g in features}
        assert 'gag' in names
        assert 'pol' not in names

    def test_without_reference_filter_loads_all_rule_features(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)
        features = load_features_with_rules(conn)
        conn.close()
        names = {g.name for g in features}
        assert names == {'gag', 'rt'}


# ──────────────────────────────────────────────────────────────────────
# DB caching
# ──────────────────────────────────────────────────────────────────────

class TestDbCaching:
    def test_store_and_load_roundtrip(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)

        features = load_features_with_rules(conn, reference_id=1)
        matches = match_query_to_features(_CDS_SEQ, features)
        assert len(matches) == 1

        chk = sequence_checksum(_CDS_SEQ)
        store_mappings(conn, 'test_ref', _CDS_SEQ, chk, matches)

        loaded = load_cached_mappings(conn, chk)
        conn.close()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].feature.name == 'gag'
        assert loaded[0].cigar == matches[0].cigar
        assert loaded[0].identity == pytest.approx(matches[0].identity)
        assert loaded[0].cds_coverage == pytest.approx(matches[0].cds_coverage)
        assert loaded[0].query_coverage == pytest.approx(matches[0].query_coverage)
        assert loaded[0].cds_start == matches[0].cds_start

    def test_store_and_load_preserves_nonzero_cds_start(self, project_db: Path) -> None:
        from respro.db.schema import open_project_db
        conn = open_project_db(project_db)

        feature = FeatureRecord(
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
        match = [
            FeatureMatch(
                feature=feature,
                identity=1.0,
                cds_coverage=0.8,
                query_coverage=0.8,
                query_start=0,
                query_end=24,
                strand='+',
                cigar='24M',
                cds_start=6,
            )
        ]
        query = _CDS_SEQ[:24]
        chk = sequence_checksum(query)
        store_mappings(conn, 'test_ref_nonzero_start', query, chk, match)

        loaded = load_cached_mappings(conn, chk)
        conn.close()

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].cds_start == 6

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

# A realistic feature-scale CDS (repeated to exceed mappy's minimum k-mer seeding length)
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


class TestNormalizeMappyCigar:
    def test_reverse_cigar_operations(self) -> None:
        assert _reverse_cigar_operations('10M2D5M1I8M') == '8M1I5M2D10M'

    def test_normalize_mappy_cigar_forward(self) -> None:
        assert _normalize_mappy_cigar('10M2I5M1D8M', '+') == '10M2D5M1I8M'

    def test_normalize_mappy_cigar_reverse(self) -> None:
        assert _normalize_mappy_cigar('10M2I5M1D8M', '-') == '8M1I5M2D10M'


class TestMappyBackend:
    """Tests for the mappy alignment backend via match_query_to_features(...)."""

    def _make_feature(
        self, cds: str, strand: str = '+', name: str = 'feature1',
    ) -> FeatureRecord:
        return FeatureRecord(
            id=1, reference_id=1, name=name, protein='',
            start=0, end=len(cds), strand=strand, codon_start=0,
            nt_sequence=cds, aa_sequence='',
        )

    def test_exact_match_forward_strand(self) -> None:
        feature = self._make_feature(_LONG_CDS)
        matches = match_query_to_features(_LONG_CDS, [feature])
        assert len(matches) == 1
        m = matches[0]
        assert m.identity == pytest.approx(1.0, abs=0.01)
        assert m.cds_coverage == pytest.approx(1.0, abs=0.01)
        assert m.strand == '+'

    def test_cds_within_larger_query(self) -> None:
        flanking = 'A' * 500
        query = flanking + _LONG_CDS + flanking
        feature = self._make_feature(_LONG_CDS)
        matches = match_query_to_features(query, [feature])
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
        feature = self._make_feature(_LONG_CDS, strand='-')
        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        assert matches[0].strand == '-'
        assert matches[0].identity == pytest.approx(1.0, abs=0.01)

    def test_snps_reduce_identity(self) -> None:
        # Introduce ~5% mismatches
        mutated = list(_LONG_CDS)
        for i in range(0, len(mutated), 20):
            mutated[i] = 'A' if mutated[i] != 'A' else 'C'
        query = ''.join(mutated)
        feature = self._make_feature(_LONG_CDS)
        matches = match_query_to_features(query, [feature], min_identity=0.80)
        assert len(matches) == 1
        assert matches[0].identity < 1.0

    def test_unrelated_sequence_rejected(self) -> None:
        feature = self._make_feature(_LONG_CDS)
        unrelated = 'GATTACA' * 300
        matches = match_query_to_features(unrelated, [feature])
        assert len(matches) == 0

    def test_skips_feature_without_nt_sequence(self) -> None:
        feature = FeatureRecord(
            id=2, reference_id=1, name='empty', protein='', start=0, end=30,
            strand='+', codon_start=0, nt_sequence='', aa_sequence='',
        )
        matches = match_query_to_features(_LONG_CDS, [feature])
        assert len(matches) == 0

    def test_cigar_coordinate_map_compatible(self) -> None:
        """CIGAR produced by mappy backend must generate a valid coordinate map."""
        flanking = 'T' * 300
        query = flanking + _LONG_CDS + flanking
        feature = self._make_feature(_LONG_CDS)
        matches = match_query_to_features(query, [feature])
        assert len(matches) == 1
        m = matches[0]
        coord = cigar_to_coordinate_map(m.cigar, m.query_start)
        # Position 0 in CDS should map to query position 300 (after flanking)
        assert coord[0] == 300

