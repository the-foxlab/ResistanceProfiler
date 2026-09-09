"""Tests for the rank-based phenotype vocabulary and strict normalizer (T1)."""

from __future__ import annotations

import pytest

from respro.db._rules_normalize import normalize_phenotype_label
from respro.db.phenotype_ranks import (
    RANK_CONTRADICTORY,
    RANK_UNKNOWN,
    RANK_VOCABULARY,
    allowed_ranks_for_labels,
    label_to_rank,
    rank_to_colour,
    rank_to_label,
)


class TestLabelToRank:
    """label_to_rank maps known labels to their severity rank."""

    def test_rank_1_labels(self):
        assert label_to_rank('Susceptible') == 1
        assert label_to_rank('Sensitive') == 1
        assert label_to_rank('Normal inhibition') == 1
        assert label_to_rank('NI') == 1
        assert label_to_rank('Normal') == 1

    def test_rank_2_labels(self):
        assert label_to_rank('Potential low-level resistance') == 2
        assert label_to_rank('Possibly resistant') == 2
        assert label_to_rank('Suspected reduced') == 2

    def test_rank_3_labels(self):
        assert label_to_rank('Low-level resistance') == 3
        assert label_to_rank('Reduced susceptibility') == 3
        assert label_to_rank('Limited susceptibility') == 3

    def test_rank_4_labels(self):
        assert label_to_rank('Intermediate') == 4
        assert label_to_rank('Intermediate resistance') == 4
        assert label_to_rank('Reduced inhibition') == 4
        assert label_to_rank('RI') == 4

    def test_rank_5_labels(self):
        assert label_to_rank('Resistant') == 5
        assert label_to_rank('High-level resistance') == 5
        assert label_to_rank('Highly reduced inhibition') == 5
        assert label_to_rank('HRI') == 5

    def test_sentinels(self):
        assert label_to_rank('contradictory') == RANK_CONTRADICTORY
        assert label_to_rank('unknown') == RANK_UNKNOWN
        assert label_to_rank('not analysed') == RANK_UNKNOWN
        assert label_to_rank('') == RANK_UNKNOWN

    def test_case_insensitive(self):
        assert label_to_rank('SUSCEPTIBLE') == 1
        assert label_to_rank('low-level resistance') == 3
        assert label_to_rank('Hri') == 5

    def test_strips_whitespace(self):
        assert label_to_rank('  Low-level resistance  ') == 3
        assert label_to_rank('\tresistant\n') == 5

    def test_unknown_label_returns_none(self):
        assert label_to_rank('nonsense') is None
        assert label_to_rank('foobar') is None


class TestRankToLabel:
    """rank_to_label returns the canonical fallback label for a rank."""

    def test_fallback_labels(self):
        assert rank_to_label(1) == 'susceptible'
        assert rank_to_label(2) == 'potential low-level resistance'
        assert rank_to_label(3) == 'low-level resistance'
        assert rank_to_label(4) == 'intermediate'
        assert rank_to_label(5) == 'resistant'

    def test_sentinel_labels(self):
        assert rank_to_label(RANK_UNKNOWN) == 'unknown'
        assert rank_to_label(RANK_CONTRADICTORY) == 'contradictory'

    def test_unknown_rank_raises(self):
        with pytest.raises(ValueError):
            rank_to_label(99)


class TestRankToColour:
    """rank_to_colour returns the colour for a rank."""

    def test_severity_colours(self):
        assert rank_to_colour(1) == RANK_VOCABULARY.colours[1]
        assert rank_to_colour(5) == RANK_VOCABULARY.colours[5]

    def test_sentinel_colours(self):
        assert rank_to_colour(RANK_UNKNOWN) == RANK_VOCABULARY.colours[RANK_UNKNOWN]
        assert rank_to_colour(RANK_CONTRADICTORY) == RANK_VOCABULARY.colours[RANK_CONTRADICTORY]

    def test_unknown_rank_raises(self):
        with pytest.raises(ValueError):
            rank_to_colour(99)


class TestAllowedRanksForLabels:
    """allowed_ranks_for_labels returns the set of ranks for a set of labels."""

    def test_subset(self):
        assert allowed_ranks_for_labels({'Susceptible', 'Resistant'}) == {1, 5}

    def test_three_tier(self):
        assert allowed_ranks_for_labels({'Susceptible', 'Intermediate', 'Resistant'}) == {1, 4, 5}

    def test_ignores_unknown_labels(self):
        assert allowed_ranks_for_labels({'Susceptible', 'nonsense'}) == {1}


class TestNormalizePhenotypeLabel:
    """Strict normalizer: lowercase + whitespace-strip + exact match; hard-fail on unknown."""

    def test_known_label_lowercased(self):
        assert normalize_phenotype_label('Low-level resistance') == 'low-level resistance'
        assert normalize_phenotype_label('  Low-Level Resistance  ') == 'low-level resistance'
        assert normalize_phenotype_label('HRI') == 'hri'

    def test_empty_returns_empty(self):
        assert normalize_phenotype_label('') == ''
        assert normalize_phenotype_label('   ') == ''

    def test_bare_rank_resolves_to_fallback_label(self):
        assert normalize_phenotype_label('1') == 'susceptible'
        assert normalize_phenotype_label('2') == 'potential low-level resistance'
        assert normalize_phenotype_label('3') == 'low-level resistance'
        assert normalize_phenotype_label('4') == 'intermediate'
        assert normalize_phenotype_label('5') == 'resistant'

    def test_bare_sentinel_rank_rejected(self):
        """Bare sentinel ranks (0, -1) are not valid phenotype cells — only
        severity ranks 1–5 may be written as bare integers."""
        with pytest.raises(ValueError):
            normalize_phenotype_label('0')
        with pytest.raises(ValueError):
            normalize_phenotype_label('-1')

    def test_sentinel_labels_pass_through(self):
        assert normalize_phenotype_label('contradictory') == 'contradictory'
        assert normalize_phenotype_label('unknown') == 'unknown'
        # Synonyms for unknown collapse to the canonical 'unknown' label.
        assert normalize_phenotype_label('not analysed') == 'unknown'
        assert normalize_phenotype_label('None') == 'unknown'

    def test_old_shorthand_rejected(self):
        """Old fuzzy synonyms must be rejected under the strict normalizer."""
        with pytest.raises(ValueError):
            normalize_phenotype_label('res')
        with pytest.raises(ValueError):
            normalize_phenotype_label('r')
        with pytest.raises(ValueError):
            normalize_phenotype_label('s')
        with pytest.raises(ValueError):
            normalize_phenotype_label('i')

    def test_unknown_label_raises(self):
        with pytest.raises(ValueError):
            normalize_phenotype_label('nonsense')
        with pytest.raises(ValueError):
            normalize_phenotype_label('mixed')
