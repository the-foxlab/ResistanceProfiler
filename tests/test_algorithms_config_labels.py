"""Tests for strict phenotype labels in algorithm config validation (T6)."""

from __future__ import annotations

import pytest

from respro.db.algorithms import validate_interpretation_algorithms


class TestConfigStrictLabels:
    """Algorithm config threshold keys are phenotype labels or bare ranks."""

    def test_bare_rank_key_resolves_to_canonical_label(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {5: 1, 3: 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        thresholds = result[0]['thresholds']
        assert 'resistant' in thresholds
        assert 'low-level resistance' in thresholds

    def test_bare_rank_string_key_resolves_to_canonical_label(self) -> None:
        # A bare-rank string '4' should also resolve to the canonical label.
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'4': 1, 'resistant': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        thresholds = result[0]['thresholds']
        assert 'intermediate' in thresholds
        assert 'resistant' in thresholds

    def test_verbatim_label_key_lowercased(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'Resistant': 1, 'Low-Level Resistance': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        thresholds = result[0]['thresholds']
        assert 'resistant' in thresholds
        assert 'low-level resistance' in thresholds

    def test_rejects_shorthand_r(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'r': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_shorthand_s(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'s': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_shorthand_res(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'res': 1, 'resistant': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_ic50_thresholds_bare_rank_keys_resolved(self) -> None:
        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'ic50',
                'thresholds': {'ACV': {5: 10.0, 4: 3.0}},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        acv = result[0]['thresholds']['ACV']
        assert 'resistant' in acv
        assert 'intermediate' in acv

    def test_ic50_thresholds_rejects_shorthand(self) -> None:
        algorithms = [
            {
                'name': 'ic50_thresholds',
                'use': 'ic50',
                'thresholds': {'ACV': {'r': 10.0, 'intermediate': 3.0}},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_drug_thresholds_override_bare_rank_keys_resolved(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'resistant': 10.0, 'intermediate': 3.0},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {5: 5.0, 4: 2.0}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        override = result[0]['drug_thresholds'][0]['thresholds']
        assert 'resistant' in override
        assert 'intermediate' in override


class TestMultiTierWithoutResistant:
    """A drug_interpretation config need not include 'resistant'.

    Any severity label (rank 1–5) is a valid threshold key. The config must
    contain at least one severity label; a config with only sentinels
    (unknown/contradictory) or no keys is rejected.
    """

    def test_low_level_resistance_only_accepted(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'low-level resistance': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'low-level resistance': 1}

    def test_intermediate_only_accepted(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'intermediate': 5},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'intermediate': 5}

    def test_susceptible_only_accepted(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'susceptible': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'susceptible': 1}

    def test_empty_thresholds_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {},
            }
        ]
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms(algorithms)

    def test_only_unknown_label_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'unknown': 1},
            }
        ]
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms(algorithms)

    def test_override_without_resistant_accepted(self) -> None:
        """A drug_thresholds override need not include 'resistant' either."""
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'low-level resistance': 2}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        override = result[0]['drug_thresholds'][0]['thresholds']
        assert override == {'low-level resistance': 2}

    def test_override_with_no_severity_label_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'unknown': 1}},
                ],
            }
        ]
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms(algorithms)
