"""
Tests for transactional maintenance of feature.has_rules during rule import.

The flag must be flipped to 1 for every feature that gains a resistance rule
(atomic or formula-member) within the same transaction as the rule insert;
validate-only must leave the flag untouched.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ, write_genbank
from typer.testing import CliRunner

from respro.cli.init import init_project
from respro.cli.main import app
from respro.db.schema import open_project_db


def _has_rules_by_name(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        r['name']: r['has_rules']
        for r in conn.execute('SELECT name, has_rules FROM feature').fetchall()
    }


@pytest.fixture()
def tiny_genbank(tmp_path: Path) -> Path:
    return write_genbank(
        tmp_path / 'tiny.gb',
        [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [
                    {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                ],
            }
        ],
    )


class TestHasRulesOnImport:
    """Rule import flips has_rules for the referenced features."""

    def test_init_sets_flag_for_ruled_feature(self, tmp_path: Path, tiny_genbank: Path) -> None:
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=rules_tsv, additional_info=False)

        conn = open_project_db(db)
        try:
            assert _has_rules_by_name(conn) == {'gag': 1}
        finally:
            conn.close()

    def test_init_without_rules_leaves_flag_zero(
        self, tmp_path: Path, tiny_genbank: Path,
    ) -> None:
        rules_tsv = tmp_path / 'empty_rules.tsv'
        rules_tsv.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\n'
        )
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=rules_tsv, additional_info=False)

        conn = open_project_db(db)
        try:
            assert _has_rules_by_name(conn) == {'gag': 0}
        finally:
            conn.close()

    def test_add_flips_flag_for_previously_ruleless_feature(
        self, tmp_path: Path, tiny_genbank: Path,
    ) -> None:
        """`respro add` with a rule for a ruleless feature flips its flag."""
        empty_rules = tmp_path / 'empty_rules.tsv'
        empty_rules.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\n'
        )
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=empty_rules, additional_info=False)

        add_rules = tmp_path / 'add.tsv'
        add_rules.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        result = CliRunner().invoke(app, [
            'add',
            '--project', str(db),
            '--rules', str(add_rules),
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = open_project_db(db)
        try:
            assert _has_rules_by_name(conn) == {'gag': 1}
        finally:
            conn.close()

    def test_validate_only_does_not_touch_flag(
        self, tmp_path: Path, tiny_genbank: Path,
    ) -> None:
        empty_rules = tmp_path / 'empty_rules.tsv'
        empty_rules.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\n'
        )
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=empty_rules, additional_info=False)

        add_rules = tmp_path / 'add.tsv'
        add_rules.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        result = CliRunner().invoke(app, [
            'add',
            '--project', str(db),
            '--rules', str(add_rules),
            '--validate',
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = open_project_db(db)
        try:
            assert _has_rules_by_name(conn) == {'gag': 0}
        finally:
            conn.close()

    def test_formula_member_rule_flips_flag(
        self, tmp_path: Path, tiny_genbank: Path,
    ) -> None:
        """A formula rule referencing a ruleless feature flips the flag via its
        member rules."""
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tphenotype\tmember_id
            gag\ttiny_ref\t2\tK\tE\tunknown\tmut_A
            gag\ttiny_ref\t6\tP\tV\tunknown\tmut_B
        """))
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(textwrap.dedent("""\
            group_id\tantiviral\texpression\tphenotype
            group_1\tDrugA\tmut_A AND mut_B\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=rules_tsv, formula_rules_tsv=formula_tsv,
                     additional_info=False)

        conn = open_project_db(db)
        try:
            assert _has_rules_by_name(conn) == {'gag': 1}
        finally:
            conn.close()

    def test_failed_import_rolls_back_flag(
        self, tmp_path: Path, tiny_genbank: Path,
    ) -> None:
        """A rollback of the import transaction leaves no partial flag state.

        Simulate by importing inside a savepoint and rolling back — the flag
        must return to its pre-import value.
        """
        db = tmp_path / 'proj.db'
        empty_rules = tmp_path / 'empty_rules.tsv'
        empty_rules.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\n'
        )
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=empty_rules, additional_info=False)

        add_rules = tmp_path / 'add.tsv'
        add_rules.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))

        conn = open_project_db(db)
        try:
            conn.execute('SAVEPOINT import_test')
            from respro.core.rules import import_rules_with_summary
            import_rules_with_summary(
                conn, 1, add_rules, additional_info=False,
            )
            assert _has_rules_by_name(conn) == {'gag': 1}
            conn.execute('ROLLBACK TO SAVEPOINT import_test')
            conn.execute('RELEASE SAVEPOINT import_test')
            assert _has_rules_by_name(conn) == {'gag': 0}
        finally:
            conn.close()
