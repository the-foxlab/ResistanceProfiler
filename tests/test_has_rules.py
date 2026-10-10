"""
Tests for the feature.has_rules flag: schema, migration backfill, and filtered loading.

Covers the rules-only-feature-mapping feature: only features that carry
resistance rules are ever aligned/mapped against.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from respro.db.schema import (
    _OPTIONAL_PROJECT_COLUMN_DEFS,
    PROJECT_SCHEMA_VERSION,
    create_schema,
    open_project_db,
)


def _make_db(db_path: Path, *, ruled: list[str], ruleless: list[str]) -> Path:
    """Create a project DB with one reference; `ruled` features get a rule each."""
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        ('HasRules', 1, str(uuid.uuid4())),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refX', 1000, 'OrgX'),
    )
    for name in ruled + ruleless:
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (1, name, name, 0, 30, '+', 'ATGGGGTTTAAACCCGGGTTTAAACCCGGG'),
        )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'd1'))
    for i, name in enumerate(ruled):
        conn.execute(
            'INSERT INTO resistance_rule '
            '(feature_id, drug_id, position, reference, mutation, phenotype) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (i + 1, 1, 0, 'M', 'T', 'resistant'),
        )
        # Mirror the import contract: inserting a rule flags the feature.
        conn.execute('UPDATE feature SET has_rules = 1 WHERE id = ?', (i + 1,))
    conn.commit()
    conn.close()
    return db_path


class TestHasRulesSchema:
    """feature.has_rules exists in the CREATE TABLE schema and optional-column defs."""

    def test_create_schema_includes_has_rules(self, tmp_path: Path) -> None:
        conn = create_schema(tmp_path / 'fresh.db')
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(feature)').fetchall()}
        conn.close()
        assert 'has_rules' in cols

    def test_optional_column_defs_include_has_rules(self) -> None:
        assert 'has_rules' in _OPTIONAL_PROJECT_COLUMN_DEFS['feature']

    def test_fresh_feature_defaults_to_zero(self, tmp_path: Path) -> None:
        conn = create_schema(tmp_path / 'fresh.db')
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('P', 1, str(uuid.uuid4())),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'refX', 1000),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, start, end) VALUES (?, ?, ?, ?)',
            (1, 'f1', 0, 30),
        )
        row = conn.execute(
            'SELECT has_rules FROM feature WHERE name = ?', ('f1',),
        ).fetchone()
        conn.close()
        assert row['has_rules'] == 0


class TestHasRulesMigrationBackfill:
    """Opening a legacy DB adds has_rules and backfills it from resistance_rule."""

    def _legacy_db(self, tmp_path: Path, *, ruled: list[str], ruleless: list[str]) -> Path:
        """Build a DB, then strip the has_rules column to simulate a legacy schema."""
        db_path = _make_db(tmp_path / 'legacy.db', ruled=ruled, ruleless=ruleless)
        conn = sqlite3.connect(str(db_path))
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('PRAGMA legacy_alter_table=ON')
        # Rebuild the feature table without has_rules (SQLite 3.35+ DROP COLUMN
        # cannot be used here because we simulate a pre-3.35 legacy schema).
        conn.executescript(
            'ALTER TABLE feature RENAME TO feature_new;\n'
            'CREATE TABLE feature (\n'
            '    id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
            '    reference_id INTEGER NOT NULL REFERENCES reference(id),\n'
            '    name TEXT NOT NULL,\n'
            '    protein TEXT DEFAULT \'\',\n'
            '    protein_id TEXT DEFAULT \'\',\n'
            '    ncbi_protein_url TEXT DEFAULT \'\',\n'
            '    locus_tag TEXT DEFAULT \'\',\n'
            '    note TEXT DEFAULT \'\',\n'
            '    start INTEGER NOT NULL,\n'
            '    end INTEGER NOT NULL,\n'
            '    strand TEXT NOT NULL DEFAULT \'+\',\n'
            '    codon_start INTEGER NOT NULL DEFAULT 0,\n'
            '    nt_sequence TEXT NOT NULL DEFAULT \'\',\n'
            '    aa_sequence TEXT NOT NULL DEFAULT \'\',\n'
            '    feature_type TEXT NOT NULL DEFAULT \'CDS\',\n'
            '    parent_feature_name TEXT NOT NULL DEFAULT \'\',\n'
            '    UNIQUE(reference_id, name)\n'
            ');\n'
            'INSERT INTO feature (id, reference_id, name, protein, protein_id, '
            'ncbi_protein_url, locus_tag, note, start, end, strand, codon_start, '
            'nt_sequence, aa_sequence, feature_type, parent_feature_name) '
            'SELECT id, reference_id, name, protein, protein_id, ncbi_protein_url, '
            'locus_tag, note, start, end, strand, codon_start, nt_sequence, '
            'aa_sequence, feature_type, parent_feature_name FROM feature_new;\n'
            'DROP TABLE feature_new;\n'
        )
        conn.commit()
        conn.close()
        return db_path

    def test_open_project_db_adds_and_backfills_column(self, tmp_path: Path) -> None:
        db_path = self._legacy_db(
            tmp_path, ruled=['ruledA', 'ruledB'], ruleless=['orphanC'],
        )
        conn = open_project_db(db_path)
        try:
            rows = {
                r['name']: r['has_rules']
                for r in conn.execute('SELECT name, has_rules FROM feature').fetchall()
            }
        finally:
            conn.close()
        assert rows == {'ruledA': 1, 'ruledB': 1, 'orphanC': 0}

    def test_backfill_is_idempotent(self, tmp_path: Path) -> None:
        db_path = self._legacy_db(tmp_path, ruled=['ruledA'], ruleless=['orphanC'])
        conn = open_project_db(db_path)
        conn.close()
        # Re-open: flags must not change.
        conn = open_project_db(db_path)
        try:
            rows = {
                r['name']: r['has_rules']
                for r in conn.execute('SELECT name, has_rules FROM feature').fetchall()
            }
        finally:
            conn.close()
        assert rows == {'ruledA': 1, 'orphanC': 0}

    def test_legacy_db_without_rules_all_zero(self, tmp_path: Path) -> None:
        db_path = self._legacy_db(tmp_path, ruled=[], ruleless=['a', 'b'])
        conn = open_project_db(db_path)
        try:
            rows = {
                r['name']: r['has_rules']
                for r in conn.execute('SELECT name, has_rules FROM feature').fetchall()
            }
        finally:
            conn.close()
        assert rows == {'a': 0, 'b': 0}


class TestLoadFeaturesFiltersHasRules:
    """load_features and load_features_for_reference return only has_rules = 1 features."""

    def test_load_features_returns_only_ruled(self, tmp_path: Path) -> None:
        from respro.core.alignment import load_features

        db_path = _make_db(tmp_path / 'filtered.db', ruled=['ruledA'], ruleless=['orphanC'])
        conn = open_project_db(db_path)
        try:
            features = load_features(conn)
        finally:
            conn.close()
        assert [f.name for f in features] == ['ruledA']

    def test_load_features_for_reference_returns_only_ruled(self, tmp_path: Path) -> None:
        from respro.db.features import load_features_for_reference

        db_path = _make_db(tmp_path / 'filtered_ref.db', ruled=['ruledA'], ruleless=['orphanC'])
        conn = open_project_db(db_path)
        try:
            features = load_features_for_reference(conn, 1)
        finally:
            conn.close()
        assert [f.name for f in features] == ['ruledA']


class TestSchemaVersionBumped:
    """PROJECT_SCHEMA_VERSION reflects the privacy-minimal query cache."""

    def test_schema_version_is_4(self) -> None:
        assert PROJECT_SCHEMA_VERSION == 4
