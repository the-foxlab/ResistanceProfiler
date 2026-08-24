"""
Tests for report query helpers.

Covers: respro/db/report_queries.py
- load_numeric_metric_thresholds()
- load_drug_class_map()
- load_drug_alias_map()
- _parse_numeric_value()
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from respro.db.report_queries import (
    load_drug_alias_map,
    load_drug_class_map,
    load_numeric_metric_thresholds,
)


@pytest.fixture
def memory_db():
    """Create in-memory database."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    return conn


class TestLoadNumericMetricThresholds:
    """Tests for load_numeric_metric_thresholds()."""

    def test_returns_empty_dict_for_none_connection(self):
        """Should return empty dict for None connection."""
        result = load_numeric_metric_thresholds(None)
        assert result == {}

    def test_returns_dict_with_none_when_no_data(self, memory_db):
        """Should return dict with None values when no data exists."""
        self._create_rule_tables(memory_db)
        result = load_numeric_metric_thresholds(memory_db)
        assert result == {'ic50': None, 'fold_ic50': None, 'score': None}

    def test_returns_none_for_insufficient_values(self, memory_db):
        """Should return None when fewer than 2 values."""
        self._create_rule_tables(memory_db)
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES ("5.0")')
        memory_db.commit()
        result = load_numeric_metric_thresholds(memory_db)
        assert result.get('score') is None

    def test_computes_mean_and_std(self, memory_db):
        """Should compute mean and std for numeric fields."""
        self._create_rule_tables(memory_db)
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES ("10.0")')
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES ("20.0")')
        memory_db.commit()
        result = load_numeric_metric_thresholds(memory_db)
        assert 'score' in result
        assert result['score'] is not None
        mean, std = result['score']
        assert mean == 15.0

    def test_handles_null_values(self, memory_db):
        """Should handle NULL values gracefully."""
        self._create_rule_tables(memory_db)
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES (NULL)')
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES ("10.0")')
        memory_db.commit()
        result = load_numeric_metric_thresholds(memory_db)
        assert result.get('score') is None

    def test_parses_numeric_values_with_prefixes(self, memory_db):
        """Should parse values with >, <, ~ prefixes."""
        self._create_rule_tables(memory_db)
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES (">10.0")')
        memory_db.execute('INSERT INTO resistance_rule (score) VALUES ("~20.0")')
        memory_db.commit()
        result = load_numeric_metric_thresholds(memory_db)
        assert result.get('score') is not None

    @staticmethod
    def _create_rule_tables(conn):
        """Create resistance_rule and resistance_formula_rule tables."""
        conn.execute('''
            CREATE TABLE resistance_rule (
                id INTEGER PRIMARY KEY, feature_id INTEGER, drug_id INTEGER,
                external_id TEXT, position INTEGER, reference TEXT, mutation TEXT,
                phenotype TEXT, clinical_phenotype TEXT, ic50 TEXT, fold_ic50 TEXT,
                score TEXT, publication TEXT, source TEXT, comment TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE resistance_formula_rule (
                id INTEGER PRIMARY KEY, drug_id INTEGER, formula_id TEXT,
                normalized_expression TEXT, phenotype TEXT, clinical_phenotype TEXT,
                ic50 TEXT, fold_ic50 TEXT, score TEXT, source TEXT, comment TEXT
            )
        ''')
        conn.commit()


class TestLoadDrugClassMap:
    """Tests for load_drug_class_map()."""

    def test_returns_empty_dict_for_none_connection(self):
        """Should return empty dict for None connection."""
        result = load_drug_class_map(None)
        assert result == {}

    def test_returns_empty_dict_when_no_algorithm(self, memory_db):
        """Should return empty dict when no drug_groups algorithm."""
        memory_db.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY, algorithm_name TEXT, config_json TEXT)')
        memory_db.commit()
        result = load_drug_class_map(memory_db)
        assert result == {}

    def test_builds_drug_to_class_mapping(self, memory_db):
        """Should build drug to class mapping."""
        memory_db.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY, algorithm_name TEXT, config_json TEXT)')
        config = {
            'groups': {
                'NRTI': ['tenofovir', 'emtricitabine'],
                'NNRTI': ['efavirenz', 'rilpivirine'],
            }
        }
        memory_db.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            ('drug_groups', json.dumps(config))
        )
        memory_db.commit()
        result = load_drug_class_map(memory_db)
        assert result['tenofovir'] == 'NRTI'
        assert result['emtricitabine'] == 'NRTI'
        assert result['efavirenz'] == 'NNRTI'

    def test_lowercases_drug_names(self, memory_db):
        """Should lowercase drug names."""
        memory_db.execute('CREATE TABLE interpretation_algorithm (id INTEGER PRIMARY KEY, algorithm_name TEXT, config_json TEXT)')
        config = {'groups': {'Class': ['DrugA', 'DrugB']}}
        memory_db.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            ('drug_groups', json.dumps(config))
        )
        memory_db.commit()
        result = load_drug_class_map(memory_db)
        assert 'druga' in result
        assert 'drugb' in result


class TestLoadDrugAliasMap:
    """Tests for load_drug_alias_map()."""

    def test_returns_empty_dict_for_none_connection(self):
        """Should return empty dict for None connection."""
        result = load_drug_alias_map(None)
        assert result == {}

    def test_returns_empty_dict_when_no_drugs(self, memory_db):
        """Should return empty dict when no drugs in database."""
        memory_db.execute('CREATE TABLE drug (id INTEGER PRIMARY KEY, name TEXT, alias TEXT)')
        memory_db.commit()
        result = load_drug_alias_map(memory_db)
        assert result == {}

    def test_builds_alias_mapping(self, memory_db):
        """Should build drug alias mapping."""
        memory_db.execute('CREATE TABLE drug (id INTEGER PRIMARY KEY, name TEXT, alias TEXT)')
        memory_db.execute('INSERT INTO drug (name, alias) VALUES ("Tenofovir", "TDF")')
        memory_db.execute('INSERT INTO drug (name, alias) VALUES ("Emtricitabine", "FTC")')
        memory_db.commit()
        result = load_drug_alias_map(memory_db)
        assert 'tenofovir' in result
        assert result['tenofovir'] == 'TDF'
