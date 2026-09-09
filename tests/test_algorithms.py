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
    compute_drug_assessment,
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
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_by_phenotype_rejects_thresholds_key(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
            }
        ]
        with pytest.raises(ValueError, match='by_phenotype'):
            validate_interpretation_algorithms(algorithms)

    def test_by_phenotype_rejects_drug_thresholds_overrides(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'resistant': 2}},
                ],
            }
        ]
        with pytest.raises(ValueError, match='by_phenotype'):
            validate_interpretation_algorithms(algorithms)

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
                'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_valid_drug_interpretation_by_fold_ic50(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_fold_ic50',
                'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_by_ic50_without_rank1_label_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
            }
        ]
        with pytest.raises(ValueError, match='rank-1'):
            validate_interpretation_algorithms(algorithms)

    def test_by_fold_ic50_without_rank1_label_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_fold_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
            }
        ]
        with pytest.raises(ValueError, match='rank-1'):
            validate_interpretation_algorithms(algorithms)

    def test_by_score_without_rank1_label_accepted(self) -> None:
        # by_score does NOT require a rank-1 label — only numeric methods do.
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'resistant': 10, 'intermediate': 3},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_by_score_rank1_zero_threshold_accepted(self) -> None:
        # Consistent with numeric methods: rank-1 labels are the lower-bound
        # fallback ceiling and accept 0; their value has no effect on matching.
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'susceptible': 0, 'intermediate': 3, 'resistant': 10},
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
                'name': 'drug_groups',
                'groups': {'Group1': ['DrugA']},
            },
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
            },
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert len(result) == 2

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
            {'name': 'drug_interpretation', 'method': 'by_phenotype'},
            {'name': 'drug_interpretation', 'method': 'by_score', 'thresholds': {'resistant': 5}},
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert len(result) == 2

    def test_rejects_two_drug_interpretation_entries_same_method(self) -> None:
        """Two drug_interpretation entries with the same method must fail."""
        algorithms = [
            {'name': 'drug_interpretation', 'method': 'by_phenotype'},
            {'name': 'drug_interpretation', 'method': 'by_phenotype'},
        ]
        with pytest.raises(ValueError, match="Duplicate drug_interpretation method 'by_phenotype'"):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_non_list_input(self) -> None:
        with pytest.raises(ValueError, match='must be a list'):
            validate_interpretation_algorithms({'name': 'drug_groups'})

    def test_rejects_non_dict_item(self) -> None:
        with pytest.raises(ValueError, match='must be a dict'):
            validate_interpretation_algorithms(['not_a_dict'])

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
                    'thresholds': {'susceptible': 0.0, 'resistant': 'high'},
                }
            ])

    def test_drug_interpretation_numeric_method_rejects_invalid_threshold_order(self) -> None:
        # resistant (rank 5) threshold below intermediate (rank 4) violates the
        # rank-generic non-decreasing rule. Equal thresholds are accepted.
        with pytest.raises(ValueError, match='non-decreasing with rank'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_fold_ic50',
                    'thresholds': {'susceptible': 0.0, 'resistant': 2.0, 'intermediate': 3.0},
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
            {'name': 'drug_interpretation', 'method': 'by_phenotype'},
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
        # Translate legacy count kwargs into the rank_counts dict expected by
        # the rank-based algorithms. rank 5 = resistant, 4 = intermediate,
        # 1 = sensitive, -1 = contradictory.
        legacy_to_rank = {
            'resistant_count': 5,
            'intermediate_count': 4,
            'sensitive_count': 1,
            'contradictory_count': -1,
        }
        rank_counts: dict[int, int] = {}
        for key, rank in legacy_to_rank.items():
            if key in overrides:
                val = overrides.pop(key)
                if val:
                    rank_counts[rank] = val
        base = {
            'hit_count': 0,
            'rank_counts': rank_counts,
            'score_total': 0.0,
            'ic50_values': [], 'fold_ic50_values': [],
        }
        base.update(overrides)
        return base

    def test_single_method_by_phenotype_resistant(self):
        drug = self._drug(hit_count=2, resistant_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 1
        assert methods[0]['method'] == 'by_phenotype'
        assert methods[0]['label'] == 'Phenotype'
        assert methods[0]['assessment'] == 'resistant'

    def test_single_method_by_phenotype_susceptible(self):
        drug = self._drug(hit_count=1, sensitive_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'

    def test_single_method_no_hits_defaults_to_susceptible(self):
        drug = self._drug(hit_count=0)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert len(methods) == 1
        assert methods[0]['assessment'] == 'susceptible'

    def test_two_methods_strongest_wins_resistant_over_intermediate(self):
        drug = self._drug(hit_count=2, resistant_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 2

    def test_two_methods_strongest_wins_intermediate_over_sensitive(self):
        drug = self._drug(hit_count=1, sensitive_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'
        assert len(methods) == 2

    def test_two_methods_contradictory_loses_to_intermediate(self):
        drug = self._drug(hit_count=2, contradictory_count=1, score_total=3.0)
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        # by_phenotype: contradictory (no severity hit >= rank 2, contradictory > 0)
        # by_score: intermediate (3 >= 2)
        # strongest: intermediate (rank 4) wins over contradictory (rank -1,
        # strength between rank 1 and rank 2)
        assert final == 'intermediate'

    def test_ic50_method(self):
        drug = self._drug(hit_count=1, ic50_values=[15.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert methods[0]['label'] == 'IC50'

    def test_ic50_below_intermediate_returns_rank1_label(self):
        drug = self._drug(hit_count=1, ic50_values=[1.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert methods[0]['assessment'] == 'susceptible'

    def test_ic50_intermediate_returns_intermediate(self):
        drug = self._drug(hit_count=1, ic50_values=[5.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'
        assert methods[0]['assessment'] == 'intermediate'

    def test_ic50_custom_rank1_label_returned_below_breakpoints(self):
        # 'sensitive' is a rank-1 label; it is returned below the lowest breakpoint.
        drug = self._drug(hit_count=1, ic50_values=[1.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'sensitive': 0.0, 'intermediate': 3.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'sensitive'
        assert methods[0]['assessment'] == 'sensitive'

    def test_fold_ic50_below_intermediate_returns_rank1_label(self):
        drug = self._drug(hit_count=1, fold_ic50_values=[1.0])
        configs = [{'method': 'by_fold_ic50', 'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert methods[0]['assessment'] == 'susceptible'

    def test_fold_ic50_method_no_values_defaults_to_susceptible(self):
        drug = self._drug(hit_count=1)
        configs = [{'method': 'by_fold_ic50', 'thresholds': {'susceptible': 0.0, 'resistant': 10.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert len(methods) == 1
        assert methods[0]['assessment'] == 'susceptible'

    def test_three_methods_resistant_wins(self):
        drug = self._drug(hit_count=3, resistant_count=1, sensitive_count=2, score_total=1.0, ic50_values=[15.0])
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'resistant': 5}},
            {'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'resistant': 10.0}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert len(methods) == 3

    def test_method_with_no_data_defaults_to_susceptible(self):
        # by_phenotype sees a susceptible hit and returns susceptible; by_ic50 has
        # no ic50_values and defaults to susceptible. Both are rank 1; strongest-wins
        # keeps the first (susceptible).
        drug = self._drug(hit_count=1, sensitive_count=1)
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'resistant': 10.0}},
        ]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert len(methods) == 2
        assert methods[0]['method'] == 'by_phenotype'
        assert methods[0]['assessment'] == 'susceptible'
        assert methods[1]['method'] == 'by_ic50'
        assert methods[1]['assessment'] == 'susceptible'

    def test_by_score_zero_score_defaults_to_susceptible(self):
        drug = self._drug(hit_count=0, score_total=0.0)
        configs = [{'method': 'by_score', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert methods[0]['assessment'] == 'susceptible'

    def test_by_phenotype_no_hits_defaults_to_susceptible(self):
        drug = self._drug(hit_count=0, resistant_count=0, intermediate_count=0)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert methods[0]['assessment'] == 'susceptible'


# ──────────────────────────────────────────────────────────────────────
# drug_thresholds override validation — drug_interpretation
# ──────────────────────────────────────────────────────────────────────

class TestValidateDrugInterpretationOverrides:

    def test_valid_drug_thresholds_by_phenotype(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
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
                'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'intermediate': 3.0},
                'drug_thresholds': [
                    {'reference': 'ref1', 'drug': 'ACV', 'thresholds': {'resistant': 5.0, 'intermediate': 2.0}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result == algorithms

    def test_drug_thresholds_without_resistant_key_rejected(self) -> None:
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'unknown': 1}},
                    ],
                }
            ])

    def test_drug_thresholds_missing_drug_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
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
                    'method': 'by_score',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'drug': '  ', 'thresholds': {'resistant': 2}},
                    ],
                }
            ])

    def test_drug_thresholds_non_integer_for_by_score_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a positive integer'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
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
                    'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'intermediate': 3.0},
                    'drug_thresholds': [
                        {'drug': 'ACV', 'thresholds': {'resistant': 'high'}},
                    ],
                }
            ])

    def test_drug_thresholds_resistant_not_greater_than_intermediate_numeric_rejected(self) -> None:
        # resistant (rank 5) below intermediate (rank 4) violates the rank-generic
        # non-decreasing rule (previously a hardcoded intermediate/resistant check).
        with pytest.raises(ValueError, match='non-decreasing with rank'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_ic50',
                    'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'intermediate': 3.0},
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
                    'method': 'by_score',
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
                    'method': 'by_score',
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
                    'method': 'by_score',
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
                    'method': 'by_score',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': {'drug': 'ACV', 'thresholds': {'resistant': 2}},
                }
            ])

    def test_drug_thresholds_entry_must_be_dict(self) -> None:
        with pytest.raises(ValueError, match='must be a dict'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': ['ACV'],
                }
            ])

    def test_drug_thresholds_empty_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a non-empty string'):
            validate_interpretation_algorithms([
                {
                    'name': 'drug_interpretation',
                    'method': 'by_score',
                    'thresholds': {'resistant': 1},
                    'drug_thresholds': [
                        {'reference': '', 'drug': 'ACV', 'thresholds': {'resistant': 2}},
                    ],
                }
            ])


# ──────────────────────────────────────────────────────────────────────
# compute_drug_assessment with drug_thresholds overrides
# ──────────────────────────────────────────────────────────────────────

class TestComputeDrugAssessmentWithOverrides:

    def _drug(self, **overrides) -> dict:
        # Translate legacy count kwargs into the rank_counts dict expected by
        # the rank-based algorithms. rank 5 = resistant, 4 = intermediate,
        # 1 = sensitive, -1 = contradictory.
        legacy_to_rank = {
            'resistant_count': 5,
            'intermediate_count': 4,
            'sensitive_count': 1,
            'contradictory_count': -1,
        }
        rank_counts: dict[int, int] = {}
        for key, rank in legacy_to_rank.items():
            if key in overrides:
                val = overrides.pop(key)
                if val:
                    rank_counts[rank] = val
        base = {
            'hit_count': 0,
            'rank_counts': rank_counts,
            'score_total': 0.0,
            'ic50_values': [], 'fold_ic50_values': [],
        }
        base.update(overrides)
        return base

    def test_reference_drug_override_applied_in_assessment(self) -> None:
        # Global resistant threshold is 2; override sets it to 1 for (ref1, ACV).
        drug = self._drug(hit_count=1, score_total=1.0)
        configs = [
            {
                'method': 'by_score',
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
        drug = self._drug(hit_count=1, score_total=1.0)
        configs = [
            {
                'method': 'by_score',
                'thresholds': {'resistant': 2},
                'drug_thresholds': [
                    {'reference': 'ref2', 'drug': 'ACV', 'thresholds': {'resistant': 1}},
                ],
            }
        ]
        final, methods = compute_drug_assessment(drug, configs, reference_name='ref1', drug_name='ACV')
        assert final == 'susceptible'

    def test_drug_only_override_applied(self) -> None:
        drug = self._drug(hit_count=1, score_total=1.0)
        configs = [
            {
                'method': 'by_score',
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
        drug = self._drug(hit_count=2, score_total=1.0)
        configs = [{'method': 'by_score', 'thresholds': {'resistant': 1}}]
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
