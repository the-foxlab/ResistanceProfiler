"""
Tests for feature and reference loading.

Covers: respro/db/features.py
- load_features_for_reference()
- load_feature_segments_by_feature_id()
- _is_ncbi_protein_accession()
- _resolve_ncbi_protein_url()
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

import pytest

from respro.db.features import (
    _is_ncbi_protein_accession,
    _resolve_ncbi_protein_url,
    load_feature_segments_by_feature_id,
    load_features_for_reference,
)
from respro.db.models import FeatureRecord, FeatureSegment


@pytest.fixture
def in_memory_db():
    """Create in-memory database with minimal schema."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    # Create only the tables we need for these tests
    conn.execute('''
        CREATE TABLE reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            accession TEXT,
            organism TEXT,
            taxonomy TEXT,
            length INTEGER NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE feature (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            protein TEXT,
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            strand TEXT NOT NULL,
            codon_start INTEGER,
            nt_sequence TEXT,
            aa_sequence TEXT,
            feature_type TEXT DEFAULT 'CDS',
            parent_feature_name TEXT,
            FOREIGN KEY (reference_id) REFERENCES reference(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE feature_segment (
            feature_id INTEGER NOT NULL,
            segment_index INTEGER NOT NULL,
            start INTEGER NOT NULL,
            end INTEGER NOT NULL,
            PRIMARY KEY (feature_id, segment_index),
            FOREIGN KEY (feature_id) REFERENCES feature(id)
        )
    ''')
    conn.commit()
    return conn


class TestLoadFeatureSegmentsByFeatureId:
    """Tests for load_feature_segments_by_feature_id()."""

    def test_returns_empty_dict_for_empty_list(self, in_memory_db):
        """Should return empty dict for empty feature ID list."""
        result = load_feature_segments_by_feature_id(in_memory_db, [])
        assert result == {}

    def test_returns_empty_dict_for_nonexistent_ids(self, in_memory_db):
        """Should return empty dict for nonexistent IDs."""
        result = load_feature_segments_by_feature_id(in_memory_db, [999])
        assert result == {}

    def test_loads_segments_for_existing_features(self, in_memory_db):
        """Should load segments for existing features."""
        # Insert test data
        in_memory_db.execute(
            'INSERT INTO reference (id, project_id, name, length) VALUES (1, 1, "test", 1000)'
        )
        in_memory_db.execute(
            'INSERT INTO feature (id, reference_id, name, start, end, strand) VALUES (1, 1, "gene1", 100, 500, "+")'
        )
        in_memory_db.execute(
            'INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (1, 0, 100, 300)'
        )
        in_memory_db.execute(
            'INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (1, 1, 400, 500)'
        )
        in_memory_db.commit()

        result = load_feature_segments_by_feature_id(in_memory_db, [1])
        assert 1 in result
        assert len(result[1]) == 2
        assert isinstance(result[1][0], FeatureSegment)
        assert result[1][0].segment_index == 0
        assert result[1][1].segment_index == 1


class TestLoadFeaturesForReference:
    """Tests for load_features_for_reference()."""

    def test_returns_empty_list_for_no_features(self, in_memory_db):
        """Should return empty list when no features exist."""
        in_memory_db.execute(
            'INSERT INTO reference (id, project_id, name, length) VALUES (1, 1, "test", 1000)'
        )
        in_memory_db.commit()
        result = load_features_for_reference(in_memory_db, 1)
        assert result == []

    def test_loads_features_with_segments(self, in_memory_db):
        """Should load features with their segments."""
        # Insert test data
        in_memory_db.execute(
            'INSERT INTO reference (id, project_id, name, length) VALUES (1, 1, "test", 1000)'
        )
        in_memory_db.execute(
            'INSERT INTO feature (id, reference_id, name, start, end, strand, nt_sequence, aa_sequence, feature_type) '
            'VALUES (1, 1, "gene1", 100, 500, "+", "ATGC", "MK", "CDS")'
        )
        in_memory_db.execute(
            'INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (1, 0, 100, 300)'
        )
        in_memory_db.commit()

        result = load_features_for_reference(in_memory_db, 1)
        assert len(result) == 1
        assert isinstance(result[0], FeatureRecord)
        assert result[0].name == 'gene1'
        assert len(result[0].segments) == 1


class TestIsNcbiProteinAccession:
    """Tests for _is_ncbi_protein_accession()."""

    def test_matches_xxx_format(self):
        """Should match XXX12345.1 format."""
        assert _is_ncbi_protein_accession('AAA12345.1') is True
        assert _is_ncbi_protein_accession('NP_123456.2') is True

    def test_matches_xx_underscore_format(self):
        """Should match XX_123456.1 format."""
        assert _is_ncbi_protein_accession('YP_009137097.1') is True
        assert _is_ncbi_protein_accession('NP_123456.2') is True

    def test_matches_xxxx_format(self):
        """Should match XXXX12345678.1 format."""
        assert _is_ncbi_protein_accession('KAFS00000001.1') is True

    def test_rejects_no_version(self):
        """Should reject accessions without version suffix."""
        assert _is_ncbi_protein_accession('AAA12345') is False

    def test_rejects_invalid_format(self):
        """Should reject invalid formats."""
        assert _is_ncbi_protein_accession('invalid') is False
        assert _is_ncbi_protein_accession('12345') is False

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert _is_ncbi_protein_accession('aaa12345.1') is True
        assert _is_ncbi_protein_accession('yp_009137097.1') is True


class TestResolveNcbiProteinUrl:
    """Tests for _resolve_ncbi_protein_url()."""

    def test_returns_url_for_valid_accession(self):
        """Should return URL for valid accession."""
        cache: dict[str, str] = {}
        url = _resolve_ncbi_protein_url('AAA12345.1', cache)
        parsed = urlparse(url)
        assert parsed.hostname == 'ncbi.nlm.nih.gov'
        assert 'AAA12345.1' in url

    def test_returns_empty_for_invalid_accession(self):
        """Should return empty string for invalid accession."""
        cache: dict[str, str] = {}
        url = _resolve_ncbi_protein_url('invalid', cache)
        assert url == ''

    def test_returns_empty_for_empty_input(self):
        """Should return empty string for empty input."""
        cache: dict[str, str] = {}
        url = _resolve_ncbi_protein_url('', cache)
        assert url == ''

    def test_uses_cache(self):
        """Should use cached URL."""
        cache: dict[str, str] = {}
        url1 = _resolve_ncbi_protein_url('AAA12345.1', cache)
        assert 'AAA12345.1' in cache
        url2 = _resolve_ncbi_protein_url('AAA12345.1', cache)
        assert url1 == url2
        assert len(cache) == 1  # Only one entry

    def test_strips_whitespace(self):
        """Should strip whitespace from protein_id."""
        cache: dict[str, str] = {}
        url = _resolve_ncbi_protein_url('  AAA12345.1  ', cache)
        assert 'AAA12345.1' in url