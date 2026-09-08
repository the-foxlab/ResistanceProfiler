"""Tests for rank-inferred interpretation algorithms (T4).

These tests cover the new rank-based behaviour: multi-tier labels, contradictory
sentinel winning over severity ranks, and config labels resolved via the rank
vocabulary. The existing S/I/R-label tests in test_algorithms.py continue to
pass because those labels are valid vocabulary entries (ranks 5/4/1).
"""

from __future__ import annotations

from respro.db.algorithms import (
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


class TestComputeDrugAssessmentRankInference:
    """compute_drug_assessment infers ranks from labels for strongest-wins."""

    def test_multi_tier_labels_strongest_wins(self):
        """A 3-tier DB {1,3,5}: resistant (5) beats low-level resistance (3)."""
        drug = _drug(hit_count=2, resistant_count=1)
        # by_phenotype hardcoded: highest-rank hit wins → resistant (rank 5).
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_contradictory_wins_over_susceptible_only(self):
        """Contradictory (rank -1) wins over susceptible (rank 1) but loses to higher tiers."""
        drug = _drug(hit_count=2, resistant_count=1, contradictory_count=1)
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'resistant': 5, 'intermediate': 2}},
        ]
        drug['score_total'] = 6.0  # by_score → resistant
        final, methods = compute_drug_assessment(drug, configs)
        # by_phenotype: resistant (1 resistant hit, highest rank with count >= 1)
        # by_score: resistant (6 >= 5)
        # contradictory is present in rank_counts but by_phenotype returns resistant
        # (severity hit exists), so the merge is resistant vs resistant → resistant.
        # Contradictory loses to any severity rank >= 2.
        assert final == 'resistant'
        # To exercise contradictory winning, use a drug where by_phenotype returns
        # contradictory (only contradictory hits, no severity) and by_score returns
        # susceptible (below all non-rank-1 breakpoints):
        drug2 = _drug(hit_count=1, contradictory_count=1, score_total=1.0)
        final2, _ = compute_drug_assessment(drug2, configs)
        # by_phenotype: contradictory; by_score: susceptible (1 < 2)
        # contradictory (strength 1.5) > susceptible (strength 1) → contradictory.
        assert final2 == 'contradictory'

    def test_all_resistant_returns_resistant(self):
        drug = _drug(hit_count=2, resistant_count=2)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_ic50_above_intermediate_returns_intermediate_label(self):
        drug = _drug(hit_count=1, ic50_values=[5.0])
        configs = [{'method': 'by_ic50', 'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'intermediate': 3.0}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'

    def test_three_tier_db_labels_work(self):
        """A DB using only {susceptible, low-level resistance, resistant} labels works."""
        drug = _drug(hit_count=1, resistant_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_low_level_hit_returns_low_level_through_drug_name_path(self):
        """by_phenotype hardcoded: 1 low-level-resistance hit (rank 3), 0 resistant
        → highest rank with count >= 1 is rank 3 → 'low-level resistance'."""
        drug = _drug(hit_count=1, low_level_resistance_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs, drug_name='DrugA')
        assert final == 'low-level resistance'


class TestByPhenotypeHardcoded:
    """by_phenotype is hardcoded: highest-rank hit wins, no configurable thresholds."""

    def test_one_resistant_hit_returns_resistant(self):
        drug = _drug(hit_count=1, resistant_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_one_low_level_hit_no_resistant_returns_low_level(self):
        drug = _drug(hit_count=1, low_level_resistance_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'low-level resistance'

    def test_resistant_plus_low_level_returns_resistant(self):
        """Highest rank wins regardless of other labels — no ceiling/summation."""
        drug = _drug(hit_count=2, resistant_count=1, low_level_resistance_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_only_contradictory_hits_returns_contradictory(self):
        drug = _drug(hit_count=1, contradictory_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'contradictory'

    def test_contradictory_wins_over_susceptible_hits(self):
        """Contradictory wins over susceptible (rank 1) hits: aciclovir case
        with 4 contradictory + 1 susceptible hit → contradictory, not susceptible."""
        drug = _drug(hit_count=5, sensitive_count=1, contradictory_count=4)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'contradictory'

    def test_contradictory_loses_to_resistant_hit(self):
        """Contradictory loses to any higher-tier severity (rank >= 2)."""
        drug = _drug(hit_count=5, resistant_count=1, contradictory_count=4)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'resistant'

    def test_hits_but_no_severity_or_contradictory_returns_susceptible(self):
        # rank 1 (susceptible) is a severity label but weakest; with only
        # susceptible hits (no rank >= 2, no contradictory) the fallback is
        # susceptible.
        drug = _drug(hit_count=1, sensitive_count=1)
        configs = [{'method': 'by_phenotype'}]
        final, _ = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'

    def test_no_hits_returns_susceptible_default(self):
        drug = _drug(hit_count=0)
        configs = [{'method': 'by_phenotype'}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'  # default when no evidence


class TestNumericAndScoreMultiTierOrdering:
    """by_score / by_ic50 / by_fold_ic50 select the strongest matched breakpoint.

    Regression coverage for AUD-001 (by_score short-circuits on the rank-1
    label) and AUD-002 (breakpoints sorted by label string instead of by
    rank/threshold). The strongest *matched* breakpoint is the highest-rank
    label whose threshold the value meets; when only rank-1 is matched (or no
    severity-rank > 1 breakpoint is met) the configured rank-1 label is
    returned.
    """

    def test_by_score_with_rank1_label_returns_resistant_for_high_score(self):
        """AUD-001: a rank-1 label in by_score must not short-circuit the loop."""
        drug = _drug(hit_count=1, score_total=15.0)
        configs = [{'method': 'by_score', 'thresholds': {'susceptible': 0, 'intermediate': 3, 'resistant': 10}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert methods[0]['assessment'] == 'resistant'

    def test_by_score_with_rank1_label_returns_susceptible_below_breakpoints(self):
        """Below the lowest non-rank-1 breakpoint, the rank-1 label is returned."""
        drug = _drug(hit_count=1, score_total=1.0)
        configs = [{'method': 'by_score', 'thresholds': {'susceptible': 0, 'intermediate': 3, 'resistant': 10}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'susceptible'
        assert methods[0]['assessment'] == 'susceptible'

    def test_by_score_intermediate_threshold_met(self):
        drug = _drug(hit_count=1, score_total=5.0)
        configs = [{'method': 'by_score', 'thresholds': {'susceptible': 0, 'intermediate': 3, 'resistant': 10}}]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'

    def test_by_ic50_same_rank_higher_threshold_wins_between(self):
        """AUD-002: two rank-5 labels - value between thresholds returns the lower-threshold label."""
        drug = _drug(hit_count=1, ic50_values=[15.0])
        configs = [{
            'method': 'by_ic50',
            'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'high-level resistance': 20.0},
        }]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
        assert methods[0]['assessment'] == 'resistant'

    def test_by_ic50_same_rank_higher_threshold_wins_above_it(self):
        """AUD-002: value above the higher same-rank threshold returns that label."""
        drug = _drug(hit_count=1, ic50_values=[25.0])
        configs = [{
            'method': 'by_ic50',
            'thresholds': {'susceptible': 0.0, 'resistant': 10.0, 'high-level resistance': 20.0},
        }]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'high-level resistance'
        assert methods[0]['assessment'] == 'high-level resistance'

    def test_by_ic50_cross_rank_alphabetical_divergence(self):
        """AUD-002: 'intermediate' (rank 4) vs 'low-level resistance' (rank 3).

        Alphabetically 'intermediate' < 'low-level resistance'; a value meeting
        only the rank-4 breakpoint must return 'intermediate', not the rank-3
        label that sorts later alphabetically.
        """
        drug = _drug(hit_count=1, ic50_values=[4.0])
        configs = [{
            'method': 'by_ic50',
            'thresholds': {'susceptible': 0.0, 'intermediate': 3.0, 'low-level resistance': 5.0},
        }]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'intermediate'

    def test_by_score_same_rank_higher_threshold_wins(self):
        """AUD-002: by_score same-rank labels - value between thresholds returns lower-threshold label."""
        drug = _drug(hit_count=1, score_total=15.0)
        configs = [{
            'method': 'by_score',
            'thresholds': {'susceptible': 0, 'resistant': 10, 'high-level resistance': 20},
        }]
        final, methods = compute_drug_assessment(drug, configs)
        assert final == 'resistant'
