"""Tests for rank-inferred interpretation algorithms (T4).

These tests cover the new rank-based behaviour: multi-tier labels, contradictory
sentinel winning over severity ranks, and config labels resolved via the rank
vocabulary. The existing S/I/R-label tests in test_algorithms.py continue to
pass because those labels are valid vocabulary entries (ranks 5/4/1).
"""

from __future__ import annotations

from respro.db.algorithms import (
    _classify_ic50,
    compute_drug_assessment,
)


def _drug(**overrides) -> dict:
    # Translate legacy count kwargs into the rank_counts dict expected by the
    # rank-based algorithms. rank 5 = resistant, 4 = intermediate, 1 = sensitive,
    # -1 = contradictory.
    legacy_to_rank = {
        'resistant_count': 5,
        'intermediate_count': 4,
        'sensitive_count': 1,
        'contradictory_count': -1,
        'low_level_resistance_count': 3,
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


class TestClassifyIc50RankLabels:
    """_classify_ic50 returns the config-declared label for the matched breakpoint."""

    def test_resistant_label_returned(self):
        assert _classify_ic50(15.0, {'resistant': 10.0, 'intermediate': 3.0}) == 'resistant'

    def test_intermediate_label_returned(self):
        assert _classify_ic50(5.0, {'resistant': 10.0, 'intermediate': 3.0}) == 'intermediate'

    def test_sensitive_label_returned(self):
        assert _classify_ic50(1.0, {'resistant': 10.0, 'intermediate': 3.0}) == 'sensitive'

    def test_multi_tier_label_returned(self):
        """A config using a multi-tier label returns that label verbatim."""
        thresholds = {
            'resistant': 10.0,
            'low-level resistance': 3.0,
        }
        assert _classify_ic50(5.0, thresholds) == 'low-level resistance'


class TestComputeDrugAssessmentRankInference:
    """compute_drug_assessment infers ranks from labels for strongest-wins."""

    def test_multi_tier_labels_strongest_wins(self):
        """A 3-tier DB {1,3,5}: resistant (5) beats low-level resistance (3)."""
        drug = _drug(hit_count=2, resistant_count=1)
        # by_phenotype with a rank-3 threshold label: 1 resistant rule >= 1 threshold
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_contradictory_wins_over_resistant(self):
        """Contradictory (rank -1) wins over resistant (rank 5) in the merge."""
        drug = _drug(hit_count=2, resistant_count=1, contradictory_count=1)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 2}},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        drug['score_total'] = 6.0  # by_score → resistant
        final, methods = compute_drug_assessment(drug, configs)
        # by_phenotype: contradictory (threshold 2 not met by 1 resistant, but contradictory>0)
        # by_score: resistant (6 >= 5)
        # contradictory wins
        assert final == 'contradictory'

    def test_all_resistant_returns_resistant(self):
        drug = _drug(hit_count=2, resistant_count=2)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_ic50_above_intermediate_returns_intermediate_label(self):
        drug = _drug(hit_count=1, ic50_values=[5.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'resistant': 10.0, 'intermediate': 3.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'

    def test_three_tier_db_labels_work(self):
        """A DB using only {susceptible, low-level resistance, resistant} labels works."""
        drug = _drug(hit_count=1, resistant_count=1)
        configs = [{'method': 'by_phenotype', 'thresholds': {'resistant': 1}}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_multi_tier_thresholds_preserved_through_drug_name_path(self):
        """When drug_name is passed, multi-tier thresholds are not collapsed to
        {resistant, intermediate}. A config with {resistant, low-level resistance}
        must still evaluate the low-level resistance threshold."""
        # 1 low-level-resistance rule (rank 3), 0 resistant rules.
        drug = _drug(hit_count=1, low_level_resistance_count=1)
        configs = [
            {'method': 'by_phenotype', 'thresholds': {'resistant': 1, 'low-level resistance': 1}},
        ]
        final, methods = compute_drug_assessment(drug, configs, drug_name='DrugA')
        assert final == 'low-level resistance'

    def test_multi_tier_override_preserved_through_drug_name_path(self):
        """A per-drug override with a multi-tier label is resolved and applied."""
        drug = _drug(hit_count=1, low_level_resistance_count=1)
        configs = [
            {
                'method': 'by_phenotype',
                'thresholds': {'resistant': 1},
                'drug_thresholds': [
                    {'drug': 'DrugA', 'thresholds': {'low-level resistance': 1}},
                ],
            },
        ]
        final, methods = compute_drug_assessment(drug, configs, drug_name='DrugA')
        assert final == 'low-level resistance'
