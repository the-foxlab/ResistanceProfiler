"""
Tests for interpretation algorithm validation, storage, and loading.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from respro.db.algorithms import (
    load_interpretation_algorithms,
    store_interpretation_algorithms,
    validate_interpretation_algorithms,
)
from respro.db.project_metadata import load_metadata_json
from respro.db.schema import create_schema


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def project_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / 'project.db'
    conn = create_schema(db_path)
    conn.execute(
        "INSERT INTO project (name, schema_version, uuid) VALUES ('test', 1, 'test-uuid')"
    )
    conn.commit()
    return conn


@pytest.fixture()
def project_id(project_db: sqlite3.Connection) -> int:
    row = project_db.execute('SELECT id FROM project LIMIT 1').fetchone()
    return int(row['id'])


# ──────────────────────────────────────────────────────────────────────
# Validation tests
# ──────────────────────────────────────────────────────────────────────

class TestValidateInterpretationAlgorithms:

    def test_valid_ic50_thresholds(self) -> None:
        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'ic50',
                'thresholds': {
                    'DrugA': {'intermediate': 2.0, 'resistant': 10.0},
                },
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_groups(self) -> None:
        algorithms = [
            {
                'name': 'drug_groups',
                'groups': {
                    'Nucleoside Analogues': ['ACV', 'PCV'],
                    'Pyrophosphate Analogues': ['FOS'],
                },
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_interpretation_by_phenotype(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1, 'intermediate': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_interpretation_by_score(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'resistant': 5},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_multiple_algorithms_coexist(self) -> None:
        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'fold_ic50',
                'thresholds': {'DrugA': {'intermediate': 3.0, 'resistant': 15.0}},
            },
            {
                'name': 'drug_groups',
                'groups': {'Group1': ['DrugA']},
            },
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
            },
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert len(result) == 3

    def test_rejects_unknown_algorithm_name(self) -> None:
        with pytest.raises(ValueError, match='Unknown algorithm name'):
            validate_interpretation_algorithms([{'name': 'mystery_algo'}])

    def test_rejects_duplicate_algorithm_names(self) -> None:
        algorithms = [
            {'name': 'drug_groups', 'groups': {'G1': ['A']}},
            {'name': 'drug_groups', 'groups': {'G2': ['B']}},
        ]
        with pytest.raises(ValueError, match="Duplicate algorithm name 'drug_groups'"):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_non_list_input(self) -> None:
        with pytest.raises(ValueError, match='must be a list'):
            validate_interpretation_algorithms({'name': 'drug_groups'})

    def test_rejects_non_dict_item(self) -> None:
        with pytest.raises(ValueError, match='must be a dict'):
            validate_interpretation_algorithms(['not_a_dict'])

    def test_ic50_thresholds_missing_use_field(self) -> None:
        with pytest.raises(ValueError, match='"use" must be'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'thresholds': {'DrugA': {'intermediate': 1.0, 'resistant': 5.0}},
                }
            ])

    def test_ic50_thresholds_invalid_use_value(self) -> None:
        with pytest.raises(ValueError, match='"use" must be'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'use': 'ec50',
                    'thresholds': {'DrugA': {'intermediate': 1.0, 'resistant': 5.0}},
                }
            ])

    def test_ic50_thresholds_resistant_not_greater_than_intermediate(self) -> None:
        with pytest.raises(ValueError, match='must be strictly greater than'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'use': 'ic50',
                    'thresholds': {'DrugA': {'intermediate': 10.0, 'resistant': 5.0}},
                }
            ])

    def test_ic50_thresholds_negative_threshold(self) -> None:
        with pytest.raises(ValueError, match='must be a positive number'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'use': 'ic50',
                    'thresholds': {'DrugA': {'intermediate': -1.0, 'resistant': 5.0}},
                }
            ])

    def test_drug_groups_empty_group_list(self) -> None:
        with pytest.raises(ValueError, match='non-empty list'):
            validate_interpretation_algorithms([
                {'name': 'drug_groups', 'groups': {'GroupA': []}}
            ])

    def test_drug_groups_duplicate_drug_across_groups(self) -> None:
        with pytest.raises(ValueError, match="drug 'ACV' appears in both"):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_groups',
                    'groups': {'GroupA': ['ACV'], 'GroupB': ['ACV', 'PCV']},
                }
            ])

    def test_drug_interpretation_missing_resistant_threshold(self) -> None:
        with pytest.raises(ValueError, match='"resistant" key'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'intermediate': 2},
                }
            ])

    def test_drug_interpretation_invalid_method(self) -> None:
        with pytest.raises(ValueError, match='"method" must be'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_magic',
                    'thresholds': {'resistant': 1},
                }
            ])

    def test_drug_interpretation_non_integer_threshold(self) -> None:
        with pytest.raises(ValueError, match='positive integer'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
                    'thresholds': {'resistant': 1.5},
                }
            ])

    def test_drug_interpretation_zero_threshold(self) -> None:
        with pytest.raises(ValueError, match='positive integer'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
                    'thresholds': {'resistant': 0},
                }
            ])


# ──────────────────────────────────────────────────────────────────────
# Store / load tests
# ──────────────────────────────────────────────────────────────────────

class TestStoreAndLoadAlgorithms:

    def test_store_and_load_single_algorithm(
        self, project_db: sqlite3.Connection, project_id: int
    ) -> None:
        config = [
            {
                'name': 'drug_groups',
                'groups': {'Nucleoside Analogues': ['ACV', 'PCV']},
            }
        ]
        store_interpretation_algorithms(project_db, project_id, config)
        project_db.commit()
        loaded = load_interpretation_algorithms(project_db, project_id)
        assert loaded == config

    def test_store_and_load_multiple_algorithms(
        self, project_db: sqlite3.Connection, project_id: int
    ) -> None:
        config = [
            {
                'name': 'ic50_thresholds',
                'use': 'ic50',
                'thresholds': {'DrugA': {'intermediate': 2.0, 'resistant': 10.0}},
            },
            {
                'name': 'drug_groups',
                'groups': {'Group1': ['DrugA']},
            },
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
            },
        ]
        store_interpretation_algorithms(project_db, project_id, config)
        project_db.commit()
        loaded = load_interpretation_algorithms(project_db, project_id)
        assert loaded == config

    def test_load_returns_empty_list_when_none_stored(
        self, project_db: sqlite3.Connection, project_id: int
    ) -> None:
        loaded = load_interpretation_algorithms(project_db, project_id)
        assert loaded == []

    def test_store_replaces_existing_algorithms(
        self, project_db: sqlite3.Connection, project_id: int
    ) -> None:
        first_batch = [{'name': 'drug_groups', 'groups': {'G1': ['A']}}]
        store_interpretation_algorithms(project_db, project_id, first_batch)
        project_db.commit()

        second_batch = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'resistant': 3},
            }
        ]
        store_interpretation_algorithms(project_db, project_id, second_batch)
        project_db.commit()

        loaded = load_interpretation_algorithms(project_db, project_id)
        assert loaded == second_batch


# ──────────────────────────────────────────────────────────────────────
# Integration tests with load_metadata_json
# ──────────────────────────────────────────────────────────────────────

class TestMetadataJsonWithAlgorithms:

    def test_load_metadata_json_returns_algorithms(self, tmp_path: Path) -> None:
        metadata = {
            'description': 'test project',
            'interpretation_algorithms': [
                {
                    'name': 'drug_groups',
                    'groups': {'Nucleoside Analogues': ['ACV']},
                }
            ],
        }
        json_path = tmp_path / 'metadata.json'
        json_path.write_text(json.dumps(metadata), encoding='utf-8')

        payload, algorithms = load_metadata_json(json_path)
        assert payload.get('metadata_description') == 'test project'
        assert len(algorithms) == 1
        assert algorithms[0]['name'] == 'drug_groups'

    def test_load_metadata_json_without_algorithms_returns_empty_list(
        self, tmp_path: Path
    ) -> None:
        metadata = {'description': 'no algorithms here'}
        json_path = tmp_path / 'metadata.json'
        json_path.write_text(json.dumps(metadata), encoding='utf-8')

        payload, algorithms = load_metadata_json(json_path)
        assert algorithms == []

    def test_load_metadata_json_invalid_algorithm_raises_valueerror(
        self, tmp_path: Path
    ) -> None:
        metadata = {
            'interpretation_algorithms': [
                {'name': 'does_not_exist'}
            ]
        }
        json_path = tmp_path / 'metadata.json'
        json_path.write_text(json.dumps(metadata), encoding='utf-8')

        with pytest.raises(ValueError, match='Unknown algorithm name'):
            load_metadata_json(json_path)
