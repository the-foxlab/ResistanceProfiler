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
                'method': 'by_score',
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
                'method': 'by_score',
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
                'method': 'by_score',
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
                'method': 'by_score',
                'thresholds': {'r': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_shorthand_s(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'s': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_rejects_shorthand_res(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'res': 1, 'resistant': 1},
            }
        ]
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            validate_interpretation_algorithms(algorithms)

    def test_drug_thresholds_override_bare_rank_keys_resolved(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'intermediate': 3.0},
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
                'method': 'by_score',
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
                'method': 'by_score',
                'thresholds': {'susceptible': 1},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'susceptible': 1}

    def test_empty_thresholds_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {},
            }
        ]
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms(algorithms)

    def test_only_unknown_label_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
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
                'method': 'by_score',
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
                'method': 'by_score',
                'thresholds': {'resistant': 1},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'unknown': 1}},
                ],
            }
        ]
        with pytest.raises(ValueError, match='at least one severity label'):
            validate_interpretation_algorithms(algorithms)

    def test_numeric_intermediate_without_resistant_accepted(self) -> None:
        """AUD-001: by_ic50 with intermediate but no resistant must not raise TypeError.

        Previously the redundant ``intermediate``/``resistant`` comparison did
        ``None <= 5.0`` and raised ``TypeError`` (not ``ValueError``), which
        propagated as a raw traceback because the CLI only catches ``ValueError``.
        The rank-generic monotonic check now covers this case.
        """
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'intermediate': 5.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'susceptible': 0.0, 'intermediate': 5.0}

    def test_numeric_low_level_without_resistant_accepted(self) -> None:
        """AUD-001: a rank-3 label without resistant validates OK for numeric methods."""
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'low-level resistance': 3.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'susceptible': 0.0, 'low-level resistance': 3.0}

    def test_override_numeric_intermediate_without_resistant_accepted(self) -> None:
        """AUD-001: a per-drug override with intermediate but no resistant validates OK."""
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'resistant': 10.0},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'susceptible': 0.0, 'intermediate': 5.0}},
                ],
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        override = result[0]['drug_thresholds'][0]['thresholds']
        assert override == {'susceptible': 0.0, 'intermediate': 5.0}

    def test_numeric_resistant_below_intermediate_still_rejected(self) -> None:
        """AUD-001: the rank-generic monotonic check still rejects resistant < intermediate."""
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'intermediate': 10.0, 'resistant': 5.0},
            }
        ]
        with pytest.raises(ValueError, match='non-decreasing'):
            validate_interpretation_algorithms(algorithms)


class TestNumericThresholdMonotonicity:
    """AUD-002: numeric-method thresholds must be non-decreasing with rank.

    The strongest-matched-breakpoint selection in ``_assess_numeric`` assumes
    higher ranks have thresholds at least as high as lower ranks. A config
    where a higher rank has a *lower* threshold than a lower rank would let the
    lower-rank breakpoint fire first for values in between, producing a weaker
    label than intended. Validation must reject such configs.
    """

    def test_higher_rank_lower_threshold_rejected(self) -> None:
        # rank 5 (resistant) threshold 2 < rank 3 (low-level resistance) threshold 3
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'low-level resistance': 3.0, 'resistant': 2.0},
            }
        ]
        with pytest.raises(ValueError, match='non-decreasing with rank'):
            validate_interpretation_algorithms(algorithms)

    def test_higher_rank_equal_threshold_accepted(self) -> None:
        # Equal thresholds across ranks are allowed (a breakpoint shared by two tiers).
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'low-level resistance': 3.0, 'resistant': 3.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds']['resistant'] == 3.0

    def test_properly_ordered_thresholds_accepted(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'low-level resistance': 3.0, 'resistant': 10.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds']['resistant'] == 10.0

    def test_monotonicity_checked_in_drug_thresholds_overrides(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'resistant': 10.0},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'susceptible': 0.0, 'resistant': 1.0, 'low-level resistance': 5.0}},
                ],
            }
        ]
        with pytest.raises(ValueError, match='non-decreasing with rank'):
            validate_interpretation_algorithms(algorithms)


class TestThresholdKeyCollisionRejected:
    """AUD-004: a bare rank and its label synonym must not silently collide.

    ``_normalize_thresholds_dict_keys`` reduces keys to canonical labels. When
    two raw keys normalize to the same label (e.g. bare rank ``'1'`` and the
    label ``'susceptible'``), the previous last-write-wins behaviour silently
    discarded one threshold with no warning. The normalizer must reject the
    collision explicitly.
    """

    def test_bare_rank_and_label_synonym_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'1': 0.0, 'susceptible': 5.0, 'resistant': 10.0},
            }
        ]
        with pytest.raises(ValueError, match='duplicate.*susceptible'):
            validate_interpretation_algorithms(algorithms)

    def test_two_labels_same_rank_distinct_strings_accepted(self) -> None:
        """'susceptible' and 'sensitive' are distinct canonical labels (both rank 1).

        They do NOT collide because they normalize to different strings; both
        are kept. Only keys that normalize to the *same* string collide.
        """
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'sensitive': 5.0, 'resistant': 10.0},
            }
        ]
        result = validate_interpretation_algorithms(algorithms)
        assert result[0]['thresholds'] == {'susceptible': 0.0, 'sensitive': 5.0, 'resistant': 10.0}

    def test_collision_in_drug_thresholds_override_rejected(self) -> None:
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_ic50',
                'thresholds': {'susceptible': 0.0, 'resistant': 10.0},
                'drug_thresholds': [
                    {'drug': 'ACV', 'thresholds': {'5': 5.0, 'resistant': 2.0}},
                ],
            }
        ]
        with pytest.raises(ValueError, match='duplicate.*resistant'):
            validate_interpretation_algorithms(algorithms)
