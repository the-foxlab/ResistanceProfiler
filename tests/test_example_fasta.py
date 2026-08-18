"""
Tests for the per-database example FASTA feature (Feature: db-example-fasta).

Covers the schema storage layer: the ``project.example_fasta`` column, its auto-migration
on open, the ``get_project_example_fasta`` accessor, and the ``get_project_summary_for_display``
SELECT surfacing the column. Also covers ``init_project``/``add_to_project`` example wiring.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ, write_genbank

from respro.cli.init import add_to_project, init_project
from respro.db.rules_queries import get_project_example_fasta, get_project_summary_for_display
from respro.db.schema import PROJECT_SCHEMA_SQL, create_schema, open_project_db

_EXAMPLE_FASTA = '>example_sample\nATGAAAGCTTTTGGCCCC\n'


class TestExampleFastaSchema:
    def test_new_db_has_example_fasta_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        columns = {
            row['name']
            for row in conn.execute('PRAGMA table_info(project)').fetchall()
        }
        conn.close()

        assert 'example_fasta' in columns

    def test_example_fasta_defaults_empty_for_new_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('P', 1, 'u')")
        value = conn.execute('SELECT example_fasta FROM project').fetchone()[0]
        conn.close()

        assert value == ''

    def test_open_project_db_auto_adds_example_fasta_column(self, tmp_path: Path) -> None:
        # Build a complete DB, then strip the example_fasta column to simulate a legacy DB.
        db_path = tmp_path / 'legacy.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('Legacy', 1, 'u')")
        conn.execute(
            'CREATE TABLE project_legacy AS '
            'SELECT id, name, uuid, created_at, updated_at, schema_version, '
            'metadata_maintainers, metadata_contact, metadata_publication_pmid, '
            'metadata_publication_doi, metadata_website, metadata_description, '
            'metadata_maintainer_update, metadata_license, metadata_tsv_checksum '
            'FROM project'
        )
        conn.execute('DROP TABLE project')
        conn.execute('ALTER TABLE project_legacy RENAME TO project')
        conn.commit()
        conn.close()

        migrated_conn = open_project_db(db_path)
        columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(project)').fetchall()
        }
        value = migrated_conn.execute('SELECT example_fasta FROM project').fetchone()[0]
        migrated_conn.close()

        assert 'example_fasta' in columns
        assert value == ''

    def test_get_project_example_fasta_returns_none_when_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('P', 1, 'u')")

        assert get_project_example_fasta(conn) is None
        conn.close()

    def test_get_project_example_fasta_returns_stored_text(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('P', 1, 'u')")
        conn.execute('UPDATE project SET example_fasta = ? WHERE id = 1', (_EXAMPLE_FASTA,))

        assert get_project_example_fasta(conn) == _EXAMPLE_FASTA
        conn.close()

    def test_get_project_example_fasta_raises_when_no_project_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)

        with pytest.raises(ValueError):
            get_project_example_fasta(conn)
        conn.close()

    def test_summary_for_display_includes_example_fasta(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        conn.execute("INSERT INTO project (name, schema_version, uuid) VALUES ('P', 1, 'u')")
        conn.execute('UPDATE project SET example_fasta = ? WHERE id = 1', (_EXAMPLE_FASTA,))

        summary = get_project_summary_for_display(conn)
        conn.close()

        assert summary['example_fasta'] == _EXAMPLE_FASTA

    def test_example_fasta_column_in_schema_sql(self) -> None:
        assert 'example_fasta' in PROJECT_SCHEMA_SQL


# ─── init_project / add_to_project ───────────────────────────────────

_VALID_EXAMPLE = '>example_sample\nATGAAAGCTTTTGGCCCC\n'
_MULTI_RECORD_EXAMPLE = '>a\nATGAAA\n>b\nGGGCCC\n'
_INVALID_FASTA = 'not a fasta at all\nno header line\n'


def _tiny_genbank(tmp_path: Path) -> Path:
    gb = tmp_path / 'tiny.gb'
    write_genbank(gb, [
        {
            'id': 'tiny_ref',
            'accession': 'tiny_ref',
            'sequence': TINY_REF_SEQ,
            'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
        }
    ])
    return gb


def _rules_tsv(tmp_path: Path) -> Path:
    tsv = tmp_path / 'rules.tsv'
    tsv.write_text(textwrap.dedent("""\
        feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
        gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
    """))
    return tsv


class TestInitProjectExample:
    def test_init_stores_example_fasta_text(self, tmp_path: Path) -> None:
        example = tmp_path / 'example.fasta'
        example.write_text(_VALID_EXAMPLE)
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=_rules_tsv(tmp_path),
            example_fasta=example,
            additional_info=False,
        )

        conn = open_project_db(db)
        assert get_project_example_fasta(conn) == _VALID_EXAMPLE
        conn.close()

    def test_init_without_example_leaves_empty(self, tmp_path: Path) -> None:
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=_rules_tsv(tmp_path),
            additional_info=False,
        )

        conn = open_project_db(db)
        assert get_project_example_fasta(conn) is None
        conn.close()

    def test_init_rejects_multi_record_example(self, tmp_path: Path) -> None:
        example = tmp_path / 'example.fasta'
        example.write_text(_MULTI_RECORD_EXAMPLE)
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='single record'):
            init_project(
                db_path=db,
                name='test',
                genbank_paths=[_tiny_genbank(tmp_path)],
                rules_tsv=_rules_tsv(tmp_path),
                example_fasta=example,
                additional_info=False,
            )
        assert not db.exists()

    def test_init_rejects_invalid_fasta(self, tmp_path: Path) -> None:
        example = tmp_path / 'example.fasta'
        example.write_text(_INVALID_FASTA)
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='(?i)example fasta'):
            init_project(
                db_path=db,
                name='test',
                genbank_paths=[_tiny_genbank(tmp_path)],
                rules_tsv=_rules_tsv(tmp_path),
                example_fasta=example,
                additional_info=False,
            )
        assert not db.exists()


class TestAddToProjectExample:
    def test_add_overwrites_existing_example(self, tmp_path: Path) -> None:
        example = tmp_path / 'example.fasta'
        example.write_text(_VALID_EXAMPLE)
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=_rules_tsv(tmp_path),
            example_fasta=example,
            additional_info=False,
        )

        new_example = tmp_path / 'example2.fasta'
        new_example.write_text('>other\nGGGCCC\n')
        add_to_project(
            db_path=db,
            rules_tsv=_rules_tsv(tmp_path),
            example_fasta=new_example,
            additional_info=False,
        )

        conn = open_project_db(db)
        assert get_project_example_fasta(conn) == '>other\nGGGCCC\n'
        conn.close()

    def test_add_clears_example_when_none(self, tmp_path: Path) -> None:
        example = tmp_path / 'example.fasta'
        example.write_text(_VALID_EXAMPLE)
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=_rules_tsv(tmp_path),
            example_fasta=example,
            additional_info=False,
        )

        add_to_project(
            db_path=db,
            rules_tsv=_rules_tsv(tmp_path),
            example_fasta=None,
            clear_example=True,
            additional_info=False,
        )

        conn = open_project_db(db)
        assert get_project_example_fasta(conn) is None
        conn.close()

    def test_add_rejects_multi_record_example(self, tmp_path: Path) -> None:
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=_rules_tsv(tmp_path),
            additional_info=False,
        )
        example = tmp_path / 'example.fasta'
        example.write_text(_MULTI_RECORD_EXAMPLE)
        with pytest.raises(ValueError, match='single record'):
            add_to_project(
                db_path=db,
                rules_tsv=_rules_tsv(tmp_path),
                example_fasta=example,
                additional_info=False,
            )
