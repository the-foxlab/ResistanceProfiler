"""
Tests for interpretation algorithm validation, storage, and loading.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from respro.db.algorithms import (
    apply_drug_alias_mappings,
    apply_ic50_threshold_classification,
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

    def test_valid_drug_interpretation_by_ic50(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_interpretation_by_fold_ic50(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_fold_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_alias(self) -> None:
        algorithms = [
            {
                'name': 'drug_alias',
                'groups': {
                    'Aciclovir': 'ACV',
                    'Ganciclovir': 'GCV',
                },
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_effect_as_resistant(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['frameshift'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_effect_as_resistant_multiple_effects(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['frameshift', 'stop_gained', 'stop_lost'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_effect_as_resistant_all_valid_effects(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['frameshift', 'stop_gained', 'stop_lost', 'start_lost', 'insertion', 'deletion'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_effect_as_resistant_rejects_duplicate_rule_tuple(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['frameshift'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    },
                    {
                        'feature': 'UL23',
                        'effect': ['stop_gained'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    },
                ],
            }
        ]
        with pytest.raises(ValueError, match='duplicate rule tuple'):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_strips_rule_whitespace(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': ' UL23 ',
                        'effect': [' frameshift '],
                        'reference': ' NC_001806 ',
                        'drug': ' Aciclovir ',
                    }
                ],
            }
        ]

        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['rules'][0] == {
            'feature': 'UL23',
            'effect': ['frameshift'],
            'reference': 'NC_001806',
            'drug': 'Aciclovir',
        }

    def test_effect_as_resistant_rejects_empty_effect_list(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': [],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match="effect.*non-empty list"):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_rejects_missing_effect_key(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match="missing required key 'effect'"):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_rejects_unknown_effect(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['nonsense'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match='invalid value'):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_rejects_missense(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['missense'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match='invalid value'):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_rejects_synonymous(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['synonymous'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match='invalid value'):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_rejects_unknown_consequence(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': ['unknown'],
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match='invalid value'):
            validate_interpretation_algorithms(algorithms)

    def test_effect_as_resistant_effect_must_be_list(self) -> None:
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'effect': 'frameshift',
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match="effect.*non-empty list"):
            validate_interpretation_algorithms(algorithms)

    def test_frameshift_as_resistant_no_longer_accepted(self) -> None:
        algorithms = [
            {
                'name': 'frameshift_as_resistant',
                'rules': [
                    {
                        'feature': 'UL23',
                        'reference': 'NC_001806',
                        'drug': 'Aciclovir',
                    }
                ],
            }
        ]
        with pytest.raises(ValueError, match='Unknown algorithm name'):
            validate_interpretation_algorithms(algorithms)

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

    def test_rejects_two_drug_interpretation_entries(self) -> None:
        """by_phenotype and by_score are mutually exclusive — two drug_interpretation entries must fail."""
        algorithms = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'name': 'drug_interpretation', 'method': 'by_score', 'thresholds': {'resistant': 5}},
        ]
        with pytest.raises(ValueError, match="Duplicate algorithm name 'drug_interpretation'"):
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

    def test_drug_interpretation_numeric_method_rejects_non_number(self) -> None:
        with pytest.raises(ValueError, match='positive number'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 'high'},
                }
            ])

    def test_drug_interpretation_numeric_method_rejects_invalid_threshold_order(self) -> None:
        with pytest.raises(ValueError, match='strictly greater than'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_fold_ic50',
                    'thresholds': {'resistant': 3.0, 'intermediate': 3.0},
                }
            ])

    def test_drug_alias_rejects_empty_groups(self) -> None:
        with pytest.raises(ValueError, match='non-empty dict'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_alias',
                    'groups': {},
                }
            ])

    def test_drug_alias_rejects_empty_key_or_value(self) -> None:
        with pytest.raises(ValueError, match='non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_alias',
                    'groups': {'': 'ACV'},
                }
            ])
        with pytest.raises(ValueError, match='non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_alias',
                    'groups': {'Aciclovir': ''},
                }
            ])

    def test_drug_alias_rejects_duplicate_alias_values(self) -> None:
        with pytest.raises(ValueError, match='duplicated across canonical names'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_alias',
                    'groups': {'Aciclovir': 'ACV', 'Acyclovir': 'ACV'},
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


# ──────────────────────────────────────────────────────────────────────
# IC50 threshold classification application tests
# ──────────────────────────────────────────────────────────────────────

class TestApplyIc50ThresholdClassification:

    _THRESHOLDS = {
        'DrugA': {'intermediate': 3.0, 'resistant': 10.0},
    }

    @pytest.fixture()
    def db_with_rules(self, tmp_path: Path) -> tuple[sqlite3.Connection, int]:
        """Minimal project DB: one gene, DrugA rules with ic50 values, DrugB without threshold."""
        db_path = tmp_path / 'apply_test.db'
        conn = create_schema(db_path)
        project_id = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('p', 1, 'uuid')"
        ).lastrowid
        ref_id = conn.execute(
            "INSERT INTO reference (project_id, name, length) VALUES (?, 'ref1', 1000)",
            (project_id,),
        ).lastrowid
        feat_id = conn.execute(
            "INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, 'gene1', 0, 300, '+')",
            (ref_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (?, 0, 0, 300)",
            (feat_id,),
        )
        drug_a_id = conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'DrugA')", (project_id,)
        ).lastrowid
        drug_b_id = conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'DrugB')", (project_id,)
        ).lastrowid
        # DrugA: above resistant threshold
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) VALUES (?, ?, 1, 'E', '15.0')",
            (feat_id, drug_a_id),
        )
        # DrugA: between thresholds → intermediate
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) VALUES (?, ?, 2, 'K', '5.0')",
            (feat_id, drug_a_id),
        )
        # DrugA: below intermediate threshold → sensitive
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) VALUES (?, ?, 3, 'R', '0.5')",
            (feat_id, drug_a_id),
        )
        # DrugA: empty ic50 → should not update
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) VALUES (?, ?, 5, 'Y', '')",
            (feat_id, drug_a_id),
        )
        # DrugB: no threshold configured → should not update
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) VALUES (?, ?, 4, 'V', '12.0')",
            (feat_id, drug_b_id),
        )
        conn.commit()
        return conn, project_id

    def test_classifies_resistant_above_resistant_threshold(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '15.0'"
        ).fetchone()
        assert row['phenotype'] == 'resistant'

    def test_classifies_intermediate_between_thresholds(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '5.0'"
        ).fetchone()
        assert row['phenotype'] == 'intermediate'

    def test_classifies_sensitive_below_intermediate_threshold(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '0.5'"
        ).fetchone()
        assert row['phenotype'] == 'sensitive'

    def test_skips_drug_without_threshold(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugB'"
        ).fetchone()
        assert row['phenotype'] == 'unknown'

    def test_skips_empty_ic50_value(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = ''"
        ).fetchone()
        assert row['phenotype'] == 'unknown'

    def test_returns_count_of_updated_rules(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        updated = apply_ic50_threshold_classification(conn, project_id, config)
        # DrugA: 3 rules with ic50 values (15.0, 5.0, 0.5) → updated
        # DrugA: 1 rule with empty ic50 → skipped
        # DrugB: 1 rule → drug not in thresholds → skipped
        assert updated == 3

    def test_uses_fold_ic50_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fold.db'
        conn = create_schema(db_path)
        project_id = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('p', 1, 'uuid')"
        ).lastrowid
        ref_id = conn.execute(
            "INSERT INTO reference (project_id, name, length) VALUES (?, 'ref1', 1000)",
            (project_id,),
        ).lastrowid
        feat_id = conn.execute(
            "INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, 'gene1', 0, 300, '+')",
            (ref_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (?, 0, 0, 300)",
            (feat_id,),
        )
        drug_id = conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'DrugA')", (project_id,)
        ).lastrowid
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, fold_ic50) VALUES (?, ?, 1, 'E', '20.0')",
            (feat_id, drug_id),
        )
        conn.commit()
        config = {
            'name': 'ic50_thresholds',
            'use': 'fold_ic50',
            'thresholds': {'DrugA': {'intermediate': 3.0, 'resistant': 10.0}},
        }
        updated = apply_ic50_threshold_classification(conn, project_id, config)
        assert updated == 1
        row = conn.execute('SELECT phenotype FROM resistance_rule').fetchone()
        assert row['phenotype'] == 'resistant'

    def test_conflicting_existing_phenotype_becomes_contradictory(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        conn, project_id = db_with_rules
        # Overwrite the stored phenotype with one that conflicts with the IC50-derived call
        # (ic50=15.0 → resistant, but existing phenotype = 'sensitive').
        conn.execute(
            "UPDATE resistance_rule SET phenotype = 'sensitive', comment = '' "
            "WHERE ic50 = '15.0'"
        )
        conn.commit()
        config = {'name': 'ic50_thresholds', 'use': 'ic50', 'thresholds': self._THRESHOLDS}
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype, r.comment FROM resistance_rule r "
            "JOIN drug d ON d.id = r.drug_id WHERE d.name = 'DrugA' AND r.ic50 = '15.0'"
        ).fetchone()
        assert row['phenotype'] == 'contradictory'
        assert 'contradictory' in row['comment'].lower()


class TestApplyDrugAliasMappings:

    def test_applies_aliases_to_matching_project_drugs(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'aliases.db'
        conn = create_schema(db_path)
        project_id = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('p', 1, 'uuid')"
        ).lastrowid
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'Aciclovir')",
            (project_id,),
        )
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'Ganciclovir')",
            (project_id,),
        )
        conn.commit()

        config = {
            'name': 'drug_alias',
            'groups': {
                'Aciclovir': 'ACV',
                'Ganciclovir': 'GCV',
            },
        }
        updated = apply_drug_alias_mappings(conn, int(project_id), config)
        conn.commit()

        assert updated == 2
        rows = conn.execute(
            'SELECT name, alias FROM drug WHERE project_id = ? ORDER BY name',
            (project_id,),
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {'name': 'Aciclovir', 'alias': 'ACV'},
            {'name': 'Ganciclovir', 'alias': 'GCV'},
        ]

    def test_applies_alias_with_mixed_case_canonical_name_to_lowercase_drug(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / 'aliases_case_insensitive.db'
        conn = create_schema(db_path)
        project_id = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('p', 1, 'uuid')"
        ).lastrowid
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'aciclovir')",
            (project_id,),
        )
        conn.commit()

        config = {
            'name': 'drug_alias',
            'groups': {'Aciclovir': 'ACV'},
        }
        updated = apply_drug_alias_mappings(conn, int(project_id), config)
        conn.commit()

        assert updated == 1
        row = conn.execute(
            "SELECT alias FROM drug WHERE project_id = ? AND name = 'aciclovir'",
            (project_id,),
        ).fetchone()
        assert row['alias'] == 'ACV'

    def test_skips_missing_drugs_without_creating_rows(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'aliases_missing.db'
        conn = create_schema(db_path)
        project_id = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('p', 1, 'uuid')"
        ).lastrowid
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'Aciclovir')",
            (project_id,),
        )
        conn.commit()

        config = {
            'name': 'drug_alias',
            'groups': {
                'Aciclovir': 'ACV',
                'Penciclovir': 'PCV',
            },
        }
        updated = apply_drug_alias_mappings(conn, int(project_id), config)
        conn.commit()

        assert updated == 1
        count = conn.execute(
            'SELECT COUNT(*) AS c FROM drug WHERE project_id = ?',
            (project_id,),
        ).fetchone()['c']
        assert count == 1
        row = conn.execute(
            "SELECT alias FROM drug WHERE project_id = ? AND name = 'Aciclovir'",
            (project_id,),
        ).fetchone()
        assert row['alias'] == 'ACV'

    def test_updates_only_the_target_project(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'aliases_scoped.db'
        conn = create_schema(db_path)
        project_a = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('A', 1, 'uuid-a')"
        ).lastrowid
        project_b = conn.execute(
            "INSERT INTO project (name, schema_version, uuid) VALUES ('B', 1, 'uuid-b')"
        ).lastrowid
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'Aciclovir')",
            (project_a,),
        )
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (?, 'Aciclovir')",
            (project_b,),
        )
        conn.commit()

        config = {
            'name': 'drug_alias',
            'groups': {'Aciclovir': 'ACV'},
        }
        updated = apply_drug_alias_mappings(conn, int(project_a), config)
        conn.commit()

        assert updated == 1
        row_a = conn.execute(
            "SELECT alias FROM drug WHERE project_id = ? AND name = 'Aciclovir'",
            (project_a,),
        ).fetchone()
        row_b = conn.execute(
            "SELECT alias FROM drug WHERE project_id = ? AND name = 'Aciclovir'",
            (project_b,),
        ).fetchone()
        assert row_a['alias'] == 'ACV'
        assert row_b['alias'] == ''
