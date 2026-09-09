"""Tests for schema and model phenotype defaults (T3)."""

from __future__ import annotations

from pathlib import Path

from respro.db.models import ResistanceRule, ResistanceRuleSet
from respro.db.schema import create_schema


class TestSchemaPhenotypeDefaults:
    """New DBs default phenotype/clinical_phenotype to '' (empty = rank 0)."""

    def test_resistance_rule_defaults_empty(self, tmp_path: Path) -> None:
        conn = create_schema(tmp_path / 't.db')
        conn.execute("INSERT INTO project (name) VALUES ('p')")
        conn.execute("INSERT INTO reference (project_id, name, length) VALUES (1, 'r', 9)")
        conn.execute("INSERT INTO feature (reference_id, name, start, end, strand) VALUES (1, 'g', 0, 9, '+')")
        conn.execute("INSERT INTO drug (project_id, name) VALUES (1, 'd')")
        conn.execute(
            'INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) '
            'VALUES (1, 1, 1, \'E\')'
        )
        row = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule WHERE id = 1'
        ).fetchone()
        conn.close()
        assert row['phenotype'] == ''
        assert row['clinical_phenotype'] == ''

    def test_formula_rule_defaults_empty(self, tmp_path: Path) -> None:
        conn = create_schema(tmp_path / 't.db')
        conn.execute("INSERT INTO project (name) VALUES ('p')")
        conn.execute("INSERT INTO reference (project_id, name, length) VALUES (1, 'r', 9)")
        conn.execute("INSERT INTO feature (reference_id, name, start, end, strand) VALUES (1, 'g', 0, 9, '+')")
        conn.execute("INSERT INTO drug (project_id, name) VALUES (1, 'd')")
        conn.execute(
            'INSERT INTO resistance_formula_rule '
            '(drug_id, formula_id, normalized_expression) VALUES (1, \'f1\', \'mut_A\')'
        )
        row = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_formula_rule WHERE id = 1'
        ).fetchone()
        conn.close()
        assert row['phenotype'] == ''
        assert row['clinical_phenotype'] == ''


class TestModelPhenotypeDefaults:
    """Model dataclasses default clinical_phenotype to ''."""

    def test_resistance_rule_default(self) -> None:
        rule = ResistanceRule(
            id=1, feature_name='g', feature_id=1, drug_name='d', drug_id=1,
            reference_identifier='', position=1, reference='K', mutation='E',
            phenotype='resistant',
        )
        assert rule.clinical_phenotype == ''

    def test_resistance_rule_set_default(self) -> None:
        rule_set = ResistanceRuleSet(
            id=1, drug_name='d', drug_id=1, phenotype='resistant',
        )
        assert rule_set.clinical_phenotype == ''
