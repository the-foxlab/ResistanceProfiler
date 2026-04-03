"""
Tests for reference resolution.
"""

import sqlite3
from pathlib import Path

from respro.db.schema import open_project_db
from respro.io.reference import (
    load_genes_for_reference,
    load_reference_sequence,
    resolve_reference_from_vcf,
)


class TestLoadReferenceSequence:
    def test_primary_reference(self, project_db: Path):
        conn = open_project_db(project_db)
        seq = load_reference_sequence(conn)
        conn.close()

        assert len(seq) > 0

    def test_by_name(self, project_db: Path):
        conn = open_project_db(project_db)
        seq = load_reference_sequence(conn, 'tiny_ref')
        conn.close()

        assert len(seq) > 0

    def test_unknown_raises(self, project_db: Path):
        conn = open_project_db(project_db)
        import pytest
        with pytest.raises(ValueError, match='not found'):
            load_reference_sequence(conn, 'nonexistent')
        conn.close()


class TestResolveReferenceFromVcf:
    def test_exact_contig_match(self, project_db: Path):
        conn = open_project_db(project_db)
        ref_id, name = resolve_reference_from_vcf(conn, {'tiny_ref'})
        conn.close()

        assert name == 'tiny_ref'

    def test_fallback_to_primary(self, project_db: Path):
        conn = open_project_db(project_db)
        ref_id, name = resolve_reference_from_vcf(conn, {'chr1', 'unknown'})
        conn.close()

        assert name == 'tiny_ref'

    def test_user_override(self, project_db: Path):
        conn = open_project_db(project_db)
        ref_id, name = resolve_reference_from_vcf(conn, {'whatever'}, user_reference='tiny_ref')
        conn.close()

        assert name == 'tiny_ref'


class TestLoadGenes:
    def test_loads_genes(self, project_db: Path):
        conn = open_project_db(project_db)
        genes = load_genes_for_reference(conn, 1)
        conn.close()

        assert len(genes) == 1
        assert genes[0].name == 'gag'
        assert genes[0].start == 0
        assert genes[0].end == 87

