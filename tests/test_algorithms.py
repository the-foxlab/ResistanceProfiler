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
    compute_drug_assessment,
    load_interpretation_algorithms,
    resolve_thresholds,
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

    def test_allows_two_drug_interpretation_entries_with_different_methods(self) -> None:
        """by_phenotype and by_score can coexist when methods differ."""
        algorithms = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'name': 'drug_interpretation', 'method': 'by_score', 'thresholds': {'resistant': 5}},
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert len(result) == 2

    def test_rejects_two_drug_interpretation_entries_same_method(self) -> None:
        """Two drug_interpretation entries with the same method must fail."""
        algorithms = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 2}},
        ]
        with pytest.raises(ValueError, match="Duplicate drug_interpretation method 'by_phenotype'"):
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

    def test_store_and_load_multiple_drug_interpretation(
        self, project_db: sqlite3.Connection, project_id: int
    ) -> None:
        config = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'name': 'drug_interpretation', 'method': 'by_score', 'thresholds': {'resistant': 5}},
        ]
        store_interpretation_algorithms(project_db, project_id, config)
        project_db.commit()
        loaded = load_interpretation_algorithms(project_db, project_id)
        assert loaded == config


# ──────────────────────────────────────────────────────────────────────
# compute_drug_assessment tests
# ──────────────────────────────────────────────────────────────────────

class TestComputeDrugAssessment:

    def _drug(self, **overrides) -> dict:
        base = {
            'hit_count': 0,
            'resistant_count': 0, 'intermediate_count': 0,
            'sensitive_count': 0, 'contradictory_count': 0,
            'score_total': 0.0,
            'ic50_values': [], 'fold_ic50_values': [],
        }
        base.update(overrides)
        return base

    def test_single_method_by_phenotype_resistant(self):
        drug = self._drug(hit_count=2, resistant_count=1)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 1
        assert methods[0]['method'] == 'by_phenotype'
        assert methods[0]['label'] == 'Phenotype'
        assert methods[0]['assessment'] == 'resistant'

    def test_single_method_by_phenotype_sensitive(self):
        drug = self._drug(hit_count=1, sensitive_count=1)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'

    def test_single_method_no_hits_defaults_to_sensitive(self):
        drug = self._drug(hit_count=0)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert len(methods) == 1
        assert methods[0]['assessment'] == 'sensitive'

    def test_two_methods_strongest_wins_resistant_over_intermediate(self):
        drug = self._drug(hit_count=2, resistant_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 2

    def test_two_methods_strongest_wins_intermediate_over_sensitive(self):
        drug = self._drug(hit_count=1, sensitive_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'
        assert len(methods) == 2

    def test_two_methods_contradictory_ranks_between_resistant_and_intermediate(self):
        drug = self._drug(hit_count=2, contradictory_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 2}},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        # by_phenotype: contradictory (no threshold met, contradictory > 0)
        # by_score: intermediate (3 >= 2)
        # strongest: contradictory (rank 1) > intermediate (rank 2)
        assert final == 'contradictory'

    def test_ic50_method(self):
        drug = self._drug(hit_count=1, ic50_values=[15.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'resistant': 10.0, 'intermediate': 3.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert methods[0]['label'] == 'IC50'

    def test_fold_ic50_method_no_values_defaults_to_sensitive(self):
        drug = self._drug(hit_count=1)
        configs = [{'method': 'by_fold_ic50', 'thresholds': {'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert len(methods) == 1
        assert methods[0]['assessment'] == 'sensitive'

    def test_three_methods_resistant_wins(self):
        drug = self._drug(hit_count=3, resistant_count=1, sensitive_count=2, score_total=1.0, ic50_values=[15.0])
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'method': 'by_score', 'thresholds': {'resistant': 5}},
            {'method': 'by_ic50', 'thresholds': {'resistant': 10.0, 'intermediate': 3.0}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 3

    def test_method_with_no_data_defaults_to_sensitive(self):
        # by_phenotype sees hits and returns sensitive; by_ic50 has no ic50_values and defaults to sensitive
        drug = self._drug(hit_count=1, sensitive_count=1)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 1}},
            {'method': 'by_ic50', 'thresholds': {'resistant': 10.0}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert len(methods) == 2
        assert methods[0]['method'] == 'by_phenotype'
        assert methods[0]['assessment'] == 'sensitive'
        assert methods[1]['method'] == 'by_ic50'
        assert methods[1]['assessment'] == 'sensitive'

    def test_by_score_zero_score_defaults_to_sensitive(self):
        drug = self._drug(hit_count=0, score_total=0.0)
        configs = [{'method': 'by_score', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert methods[0]['assessment'] == 'sensitive'

    def test_by_phenotype_no_hits_defaults_to_sensitive(self):
        drug = self._drug(hit_count=0, resistant_count=0, intermediate_count=0)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert methods[0]['assessment'] == 'sensitive'


# ──────────────────────────────────────────────────────────────────────
# drug_thresholds override validation — drug_interpretation
# ──────────────────────────────────────────────────────────────────────

class TestValidateDrugInterpretationOverrides:

    def test_valid_drug_thresholds_by_phenotype(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1, 'intermediate': 1},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'resistant': 2, 'intermediate': 1}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_thresholds_with_reference(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 5.0, 'intermediate': 2.0}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_drug_thresholds_without_resistant_key_rejected(self) -> None:
        with pytest.raises(ValueError, match='must include the "resistant" key'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'intermediate': 1}},
                    ],
                }
            ])

    def test_drug_thresholds_missing_drug_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'thresholds': {'resistant': 2}},
                    ],
                }
            ])

    def test_drug_thresholds_empty_drug_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': '  ', 'thresholds': {'resistant': 2}},
                    ],
                }
            ])

    def test_drug_thresholds_non_integer_for_by_phenotype_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a positive integer'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 1.5}},
                    ],
                }
            ])

    def test_drug_thresholds_non_number_for_by_ic50_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a positive number'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 'high'}},
                    ],
                }
            ])

    def test_drug_thresholds_resistant_not_greater_than_intermediate_numeric_rejected(self) -> None:
        with pytest.raises(ValueError, match='strictly greater than'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 2.0, 'intermediate': 3.0}},
                    ],
                }
            ])

    def test_drug_thresholds_duplicate_reference_drug_rejected(self) -> None:
        with pytest.raises(ValueError, match='duplicate'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
                        {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 3}},
                    ],
                }
            ])

    def test_drug_thresholds_duplicate_drug_only_rejected(self) -> None:
        with pytest.raises(ValueError, match='duplicate'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 2}},
                        {'drug': 'ACV', 'thresholds': {'resistant': 3}},
                    ],
                }
            ])

    def test_drug_thresholds_duplicate_accession_version_normalized_rejected(self) -> None:
        """Regression: NC_001345 and NC_001345.1 are the same accession base, so two
        overrides for the same drug under these two reference strings must be
        detected as a duplicate (otherwise resolution becomes order-dependent)."""
        with pytest.raises(ValueError, match='duplicate'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'reference': 'NC_001345', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
                        {'reference': 'NC_001345.1', 'drug': 'ACV', 'thresholds': {'resistant': 3}},
                    ],
                }
            ])

    def test_drug_thresholds_must_be_list(self) -> None:
        with pytest.raises(ValueError, match='must be a list'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': {'drug': 'ACV', 'thresholds': {'resistant': 2}},
                }
            ])

    def test_drug_thresholds_entry_must_be_dict(self) -> None:
        with pytest.raises(ValueError, match='must be a dict'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': ['ACV'],
                }
            ])

    def test_drug_thresholds_empty_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_phenotype',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'reference': '', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
                    ],
                }
            ])


# ──────────────────────────────────────────────────────────────────────
# drug_thresholds override validation — ic50_thresholds
# ──────────────────────────────────────────────────────────────────────

class TestValidateIc50ThresholdsOverrides:

    def test_valid_drug_thresholds(self) -> None:
        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'fold_ic50',
                'thresholds': {'ACV': {'intermediate': 3.0, 'resistant': 10.0}},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'intermediate': 2.0, 'resistant': 5.0}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_ic50_drug_thresholds_missing_intermediate_rejected(self) -> None:
        with pytest.raises(ValueError, match='must include the "intermediate" key'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'use': 'ic50',
                    'thresholds': {'ACV': {'intermediate': 3.0, 'resistant': 10.0}},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 5.0}},
                    ],
                }
            ])

    def test_ic50_drug_thresholds_resistant_not_greater_rejected(self) -> None:
        with pytest.raises(ValueError, match='strictly greater than'):
            validate_interpretation_algorithms([
                {
                    'name': 'ic50_thresholds',
                    'use': 'ic50',
                    'thresholds': {'ACV': {'intermediate': 3.0, 'resistant': 10.0}},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'intermediate': 5.0, 'resistant': 5.0}},
                    ],
                }
            ])


# ──────────────────────────────────────────────────────────────────────
# resolve_thresholds precedence
# ──────────────────────────────────────────────────────────────────────

class TestResolveThresholds:

    def test_global_fallback_when_no_overrides(self) -> None:
        config = {'method': 'by_phenotype', 'thresholds': {'resistant': 1, 'intermediate': 1}}
        assert resolve_thresholds(config, 'ref1', 'ACV') == (1, 1)

    def test_drug_only_override_wins_over_global(self) -> None:
        config = {
            'method': 'by_phenotype',
            'thresholds': {'resistant': 1, 'intermediate': 1},
            'drug_thresholds': [
                {'drug': 'ACV', 'thresholds': {'resistant': 2, 'intermediate': 1}},
            ],
        }
        assert resolve_thresholds(config, 'ref1', 'ACV') == (2, 1)

    def test_reference_drug_override_wins_over_drug_only(self) -> None:
        config = {
            'method': 'by_phenotype',
            'thresholds': {'resistant': 1, 'intermediate': 1},
            'drug_thresholds': [
                {'drug': 'ACV', 'thresholds': {'resistant': 2, 'intermediate': 1}},
                {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 3, 'intermediate': 2}},
            ],
        }
        assert resolve_thresholds(config, 'ref1', 'ACV') == (3, 2)

    def test_reference_drug_override_skipped_when_reference_mismatch(self) -> None:
        config = {
            'method': 'by_phenotype',
            'thresholds': {'resistant': 1, 'intermediate': 1},
            'drug_thresholds': [
                {'reference': 'ref2', 'drug': 'ACV', 'thresholds': {'resistant': 3, 'intermediate': 2}},
            ],
        }
        # reference doesn't match → fall back to global
        assert resolve_thresholds(config, 'ref1', 'ACV') == (1, 1)

    def test_reference_drug_override_matches_accession_version(self) -> None:
        config = {
            'method': 'by_ic50',
            'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
            'drug_thresholds': [
                {'reference': 'NC_001345.1', 'drug': 'ACV', 'thresholds': {'resistant': 5.0, 'intermediate': 2.0}},
            ],
        }
        # observed reference without version still matches accession base
        assert resolve_thresholds(config, 'NC_001345', 'ACV') == (5.0, 2.0)

    def test_no_intermediate_global_returns_none_for_intermediate(self) -> None:
        config = {'method': 'by_score', 'thresholds': {'resistant': 5}}
        assert resolve_thresholds(config, 'ref1', 'ACV') == (5, None)

    def test_drug_only_override_without_intermediate(self) -> None:
        config = {
            'method': 'by_score',
            'thresholds': {'resistant': 5, 'intermediate': 2},
            'drug_thresholds': [
                {'drug': 'ACV', 'thresholds': {'resistant': 8}},
            ],
        }
        assert resolve_thresholds(config, 'ref1', 'ACV') == (8, None)


# ──────────────────────────────────────────────────────────────────────
# compute_drug_assessment with drug_thresholds overrides
# ──────────────────────────────────────────────────────────────────────

class TestComputeDrugAssessmentWithOverrides:

    def _drug(self, **overrides) -> dict:
        base = {
            'hit_count': 0,
            'resistant_count': 0, 'intermediate_count': 0,
            'sensitive_count': 0, 'contradictory_count': 0,
            'score_total': 0.0,
            'ic50_values': [], 'fold_ic50_values': [],
        }
        base.update(overrides)
        return base

    def test_reference_drug_override_applied_in_assessment(self) -> None:
        # Global resistant threshold is 2; override sets it to 1 for (ref1, ACV).
        drug = self._drug(hit_count=1, resistant_count=1)
        configs = [
            {
                'method': 'by_phenotype',
                'thresholds': {'resistant': 2},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 1}},
                ],
            }
        ]
        final, methods = compute_drug_assessment(drug, configs, reference_name='ref1', drug_name='ACV')
        assert final == 'resistant'
        assert methods[0]['assessment'] == 'resistant'

    def test_override_skipped_when_reference_mismatch(self) -> None:
        # Override is for ref2; observed reference is ref1 → global threshold (2) applies.
        drug = self._drug(hit_count=1, resistant_count=1)
        configs = [
            {
                'method': 'by_phenotype',
                'thresholds': {'resistant': 2},
                'drug_thresholds': [
                    {'reference': 'ref2', 'drug': 'ACV', 'thresholds': {'resistant': 1}},
                ],
            }
        ]
        final, methods = compute_drug_assessment(drug, configs, reference_name='ref1', drug_name='ACV')
        assert final == 'sensitive'

    def test_drug_only_override_applied(self) -> None:
        drug = self._drug(hit_count=1, resistant_count=1)
        configs = [
            {
                'method': 'by_phenotype',
                'thresholds': {'resistant': 2},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'resistant': 1}},
                ],
            }
        ]
        final, methods = compute_drug_assessment(drug, configs, reference_name='ref1', drug_name='ACV')
        assert final == 'resistant'

    def test_no_overrides_backward_compatible(self) -> None:
        # No reference_name/drug_name passed → behaves exactly as before.
        drug = self._drug(hit_count=2, resistant_count=1)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'


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

    def test_reference_drug_override_classifies_against_override(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        # Global: DrugA intermediate=3.0, resistant=10.0.
        # Override for (ref1, DrugA): intermediate=1.0, resistant=2.0.
        # ic50=5.0 is above override resistant (2.0) → resistant (not intermediate as globally).
        conn, project_id = db_with_rules
        config = {
            'name': 'ic50_thresholds',
            'use': 'ic50',
            'thresholds': self._THRESHOLDS,
            'drug_thresholds': [
                {'reference': 'ref1', 'drug': 'DrugA', 'thresholds': {'intermediate': 1.0, 'resistant': 2.0}},
            ],
        }
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '5.0'"
        ).fetchone()
        assert row['phenotype'] == 'resistant'

    def test_drug_only_override_classifies_against_override(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        # Drug-only override (no reference): DrugA intermediate=1.0, resistant=2.0.
        conn, project_id = db_with_rules
        config = {
            'name': 'ic50_thresholds',
            'use': 'ic50',
            'thresholds': self._THRESHOLDS,
            'drug_thresholds': [
                {'drug': 'DrugA', 'thresholds': {'intermediate': 1.0, 'resistant': 2.0}},
            ],
        }
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '0.5'"
        ).fetchone()
        # 0.5 < intermediate (1.0) → sensitive
        assert row['phenotype'] == 'sensitive'
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '5.0'"
        ).fetchone()
        # 5.0 >= resistant (2.0) → resistant
        assert row['phenotype'] == 'resistant'

    def test_override_skipped_when_reference_mismatch(
        self, db_with_rules: tuple[sqlite3.Connection, int]
    ) -> None:
        # Override for ref2; rules are on ref1 → global thresholds apply.
        conn, project_id = db_with_rules
        config = {
            'name': 'ic50_thresholds',
            'use': 'ic50',
            'thresholds': self._THRESHOLDS,
            'drug_thresholds': [
                {'reference': 'ref2', 'drug': 'DrugA', 'thresholds': {'intermediate': 1.0, 'resistant': 2.0}},
            ],
        }
        apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugA' AND r.ic50 = '5.0'"
        ).fetchone()
        # global: 5.0 between 3.0 and 10.0 → intermediate
        assert row['phenotype'] == 'intermediate'

    def test_override_present_but_no_global_entry_and_reference_mismatch_skips_rule(
        self, tmp_path: Path
    ) -> None:
        """Regression: a drug listed in drug_thresholds (override for a different
        reference) but absent from the global thresholds dict must be SKIPPED when
        the rule's reference does not match any override — not crash with a
        TypeError from comparing against a None intermediate breakpoint."""
        db_path = tmp_path / 'mismatch.db'
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
            "INSERT INTO drug (project_id, name) VALUES (?, 'DrugX')", (project_id,)
        ).lastrowid
        # DrugX has an ic50 value but NO global thresholds entry, only an override
        # scoped to ref2 (which does not match the rule's ref1).
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) "
            "VALUES (?, ?, 1, 'E', '0.5')",
            (feat_id, drug_id),
        )
        conn.commit()
        config = {
            'name': 'ic50_thresholds',
            'use': 'ic50',
            'thresholds': {'DrugA': {'intermediate': 3.0, 'resistant': 10.0}},
            'drug_thresholds': [
                {'reference': 'ref2', 'drug': 'DrugX', 'thresholds': {'intermediate': 1.0, 'resistant': 2.0}},
            ],
        }
        updated = apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        # Rule must be skipped (no applicable threshold), not crash.
        assert updated == 0
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugX'"
        ).fetchone()
        assert row['phenotype'] == 'unknown'

    def test_override_only_drug_only_without_global_skips_rule(
        self, tmp_path: Path
    ) -> None:
        """A drug-only override (no reference) still classifies even without a
        global thresholds entry, because the drug-only override applies to all
        references. This confirms the drug-only path is not skipped."""
        db_path = tmp_path / 'drugonly.db'
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
            "INSERT INTO drug (project_id, name) VALUES (?, 'DrugX')", (project_id,)
        ).lastrowid
        conn.execute(
            "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, ic50) "
            "VALUES (?, ?, 1, 'E', '5.0')",
            (feat_id, drug_id),
        )
        conn.commit()
        config = {
            'name': 'ic50_thresholds',
            'use': 'ic50',
            'thresholds': {'DrugA': {'intermediate': 3.0, 'resistant': 10.0}},
            'drug_thresholds': [
                {'drug': 'DrugX', 'thresholds': {'intermediate': 1.0, 'resistant': 2.0}},
            ],
        }
        updated = apply_ic50_threshold_classification(conn, project_id, config)
        conn.commit()
        # drug-only override applies → 5.0 >= 2.0 → resistant
        assert updated == 1
        row = conn.execute(
            "SELECT r.phenotype FROM resistance_rule r JOIN drug d ON d.id = r.drug_id "
            "WHERE d.name = 'DrugX'"
        ).fetchone()
        assert row['phenotype'] == 'resistant'


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
