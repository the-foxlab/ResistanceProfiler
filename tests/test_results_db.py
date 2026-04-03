"""
Tests for standalone results database schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from respro.db.schema import create_schema, init_results_db, open_project_db


class TestProjectSchemaBoundary:
    def test_project_schema_excludes_results_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'project.db'
        conn = create_schema(db_path)
        tables = {
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'sample' not in tables
        assert 'run' not in tables
        assert 'variant_result' not in tables

    def test_open_project_db_rejects_incompatible_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'broken_project.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        conn.close()

        with pytest.raises(ValueError) as exc_info:
            open_project_db(db_path)

        message = str(exc_info.value)
        assert 'schema mismatch' in message
        assert 'missing tables' in message

    def test_open_project_db_adds_missing_optional_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'project_optional_migration.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            'CREATE TABLE project ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name TEXT NOT NULL, '
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            'schema_version INTEGER NOT NULL DEFAULT 1'
            ')'
        )
        conn.execute(
            'CREATE TABLE reference ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_id INTEGER NOT NULL, '
            'name TEXT NOT NULL, '
            'length INTEGER NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE gene ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'reference_id INTEGER NOT NULL, '
            'name TEXT NOT NULL, '
            'start INTEGER NOT NULL, '
            'end INTEGER NOT NULL, '
            "strand TEXT NOT NULL DEFAULT '+'"
            ')'
        )
        conn.execute(
            'CREATE TABLE drug ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_id INTEGER NOT NULL, '
            'name TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'gene_id INTEGER NOT NULL, '
            'drug_id INTEGER NOT NULL, '
            'position INTEGER NOT NULL, '
            'mutation TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule_set ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'drug_id INTEGER NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule_set_member ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'rule_set_id INTEGER NOT NULL, '
            'gene_id INTEGER NOT NULL, '
            'position INTEGER NOT NULL, '
            'mutation TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE query_reference ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name TEXT NOT NULL, '
            'sequence TEXT NOT NULL, '
            'length INTEGER NOT NULL, '
            'checksum TEXT NOT NULL, '
            'UNIQUE(checksum)'
            ')'
        )
        conn.execute(
            'CREATE TABLE query_gene_mapping ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'query_ref_id INTEGER NOT NULL, '
            'gene_id INTEGER NOT NULL, '
            'identity REAL NOT NULL, '
            'coverage REAL NOT NULL, '
            'query_start INTEGER NOT NULL, '
            'query_end INTEGER NOT NULL, '
            "strand TEXT NOT NULL DEFAULT '+', "
            'cigar TEXT NOT NULL, '
            'UNIQUE(query_ref_id, gene_id)'
            ')'
        )
        conn.execute(
            'INSERT INTO project (name, created_at, schema_version) VALUES (?, datetime(\'now\'), ?)',
            ('Legacy Project', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref_legacy', 100),
        )
        conn.execute(
            'INSERT INTO gene (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)',
            (1, 'gag', 0, 30, '+'),
        )
        conn.execute(
            'INSERT INTO drug (project_id, name) VALUES (?, ?)',
            (1, 'drugx'),
        )
        conn.execute(
            'INSERT INTO resistance_rule (gene_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)',
            (1, 1, 1, 'E'),
        )
        conn.commit()
        conn.close()

        migrated_conn = open_project_db(db_path)
        reference_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(reference)').fetchall()
        }
        rule_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(resistance_rule)').fetchall()
        }
        migrated_reference = migrated_conn.execute(
            'SELECT accession, organism, taxonomy FROM reference WHERE id = 1'
        ).fetchone()
        migrated_rule = migrated_conn.execute(
            'SELECT reference_identifier, reference, phenotype, clinical_phenotype '
            'FROM resistance_rule WHERE id = 1'
        ).fetchone()
        migrated_conn.close()

        assert 'organism' in reference_columns
        assert 'reference_identifier' in rule_columns
        assert 'clinical_phenotype' in rule_columns
        assert migrated_reference is not None
        assert migrated_reference['accession'] == ''
        assert migrated_reference['organism'] == ''
        assert migrated_reference['taxonomy'] == ''
        assert migrated_rule is not None
        assert migrated_rule['reference_identifier'] == ''
        assert migrated_rule['reference'] == ''
        assert migrated_rule['phenotype'] == 'unknown'
        assert migrated_rule['clinical_phenotype'] == 'unknown'


class TestResultsDbSchema:
    def test_init_results_db_creates_expected_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results.db'
        conn = init_results_db(db_path)
        tables = {
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'results_meta' in tables
        assert 'run' in tables
        assert 'variant_result' in tables

    def test_init_results_db_validates_existing_compatible_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results_existing.db'
        conn = init_results_db(db_path)
        conn.close()

        validated_conn = init_results_db(db_path)
        meta_row = validated_conn.execute(
            'SELECT value FROM results_meta WHERE key = ?',
            ('results_schema_version',),
        ).fetchone()
        validated_conn.close()

        assert meta_row is not None
        assert meta_row['value'] == '1'

    def test_init_results_db_rejects_existing_incompatible_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'broken_results.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.execute('CREATE TABLE variant_result (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.commit()
        conn.close()

        with pytest.raises(ValueError) as exc_info:
            init_results_db(db_path)

        message = str(exc_info.value)
        assert 'schema mismatch' in message
        assert 'results_meta' in message

    def test_init_results_db_adds_missing_optional_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results_optional_migration.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute(
            'CREATE TABLE run ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_name TEXT NOT NULL, '
            'project_db_path TEXT NOT NULL, '
            'reference_name TEXT NOT NULL, '
            'vcf_path TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE variant_result ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'run_id INTEGER NOT NULL, '
            'chrom TEXT NOT NULL, '
            'pos INTEGER NOT NULL, '
            'ref TEXT NOT NULL, '
            'alt TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'INSERT INTO run (project_name, project_db_path, reference_name, vcf_path) '
            'VALUES (?, ?, ?, ?)',
            ('p', '/tmp/project.db', 'ref', '/tmp/sample.vcf'),
        )
        conn.execute(
            'INSERT INTO variant_result (run_id, chrom, pos, ref, alt) VALUES (?, ?, ?, ?, ?)',
            (1, 'ref', 4, 'A', 'G'),
        )
        conn.commit()
        conn.close()

        migrated_conn = init_results_db(db_path)
        run_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(run)').fetchall()
        }
        variant_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(variant_result)').fetchall()
        }
        migrated_run = migrated_conn.execute(
            'SELECT sample_name, total_variants, resistance_hits, status FROM run WHERE id = 1'
        ).fetchone()
        migrated_variant = migrated_conn.execute(
            'SELECT gene_name, af_bin, drug_hits FROM variant_result WHERE id = 1'
        ).fetchone()
        migrated_conn.close()

        assert 'sample_name' in run_columns
        assert 'resistance_hits' in run_columns
        assert 'af_bin' in variant_columns
        assert 'drug_hits' in variant_columns
        assert migrated_run is not None
        assert migrated_run['sample_name'] == ''
        assert migrated_run['total_variants'] == 0
        assert migrated_run['resistance_hits'] == 0
        assert migrated_run['status'] == 'complete'
        assert migrated_variant is not None
        assert migrated_variant['gene_name'] == ''
        assert migrated_variant['af_bin'] == ''
        assert migrated_variant['drug_hits'] == '[]'


