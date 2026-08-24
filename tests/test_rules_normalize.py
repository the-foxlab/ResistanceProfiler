"""
Tests for TSV row normalization helpers in rule imports.

Covers: respro/db/_rules_normalize.py
- _get_value()
- _parse_ic50_value()
- _parse_single_ic50()
- _normalize_ic50_from_row()
- _normalize_fold_ic50_from_row()
- _normalize_score_from_row()
- _normalize_phenotype_token()
- _append_contradictory_comment()
"""

from __future__ import annotations

import pytest

from respro.db._rules_normalize import (
    _CONTRADICTORY_COMMENT,
    _append_contradictory_comment,
    _get_value,
    _normalize_fold_ic50_from_row,
    _normalize_ic50_from_row,
    _normalize_phenotype_token,
    _normalize_score_from_row,
    _parse_ic50_value,
    _parse_single_ic50,
)


class TestGetValue:
    """Tests for _get_value() helper."""

    def test_returns_first_non_empty_value(self):
        """Should return the first non-empty value from the provided keys."""
        row = {'a': '', 'b': 'value_b', 'c': 'value_c'}
        result = _get_value(row, 'a', 'b', 'c')
        assert result == 'value_b'

    def test_returns_stripped_value(self):
        """Should strip whitespace from the returned value."""
        row = {'key': '  value with spaces  '}
        result = _get_value(row, 'key')
        assert result == 'value with spaces'

    def test_returns_empty_string_when_all_keys_missing(self):
        """Should return empty string when no keys have values."""
        row = {'a': '', 'b': '', 'c': ''}
        result = _get_value(row, 'a', 'b', 'c')
        assert result == ''

    def test_returns_empty_string_when_row_empty(self):
        """Should return empty string for empty row."""
        row: dict[str, str] = {}
        result = _get_value(row, 'a', 'b', 'c')
        assert result == ''

    def test_handles_none_values(self):
        """Should skip None values and continue to next key."""
        row = {'a': None, 'b': 'found', 'c': 'ignored'}
        result = _get_value(row, 'a', 'b', 'c')
        assert result == 'found'

    def test_single_key_exists(self):
        """Should work with single key that exists."""
        row = {'key': 'value'}
        result = _get_value(row, 'key')
        assert result == 'value'

    def test_single_key_missing(self):
        """Should return empty string for single missing key."""
        row = {'other': 'value'}
        result = _get_value(row, 'key')
        assert result == ''


class TestParseIc50Value:
    """Tests for _parse_ic50_value() numeric parsing."""

    def test_parses_integer(self):
        """Should parse integer values."""
        assert _parse_ic50_value('10') == 10.0
        assert _parse_ic50_value('0') == 0.0

    def test_parses_float(self):
        """Should parse decimal values."""
        assert _parse_ic50_value('10.5') == 10.5
        assert _parse_ic50_value('0.001') == 0.001

    def test_parses_scientific_notation(self):
        """Should parse scientific notation."""
        assert _parse_ic50_value('1.5e3') == 1500.0
        assert _parse_ic50_value('2.0E-3') == 0.002
        assert _parse_ic50_value('1e10') == 1e10

    def test_parses_negative_values(self):
        """Should parse negative values (though biologically unusual)."""
        assert _parse_ic50_value('-5.0') == -5.0
        assert _parse_ic50_value('-1e2') == -100.0

    def test_parses_with_whitespace(self):
        """Should strip whitespace before parsing."""
        assert _parse_ic50_value('  10.5  ') == 10.5
        assert _parse_ic50_value('\t5.0\n') == 5.0

    def test_returns_none_for_empty(self):
        """Should return None for empty string."""
        assert _parse_ic50_value('') is None

    def test_returns_none_for_whitespace_only(self):
        """Should return None for whitespace-only string."""
        assert _parse_ic50_value('   ') is None

    def test_returns_none_for_none_literal(self):
        """Should return None for 'none' string."""
        assert _parse_ic50_value('none') is None
        assert _parse_ic50_value('None') is None
        assert _parse_ic50_value('NONE') is None

    def test_returns_none_for_invalid_string(self):
        """Should return None for non-numeric strings."""
        assert _parse_ic50_value('resistant') is None
        assert _parse_ic50_value('N/A') is None
        assert _parse_ic50_value('unknown') is None

    def test_extracts_number_from_text(self):
        """Should extract numeric portion from mixed text."""
        assert _parse_ic50_value('>10.5') == 10.5
        assert _parse_ic50_value('~5.0') == 5.0
        assert _parse_ic50_value('approximately 10') == 10.0

    def test_parses_leading_decimal_point(self):
        """Should parse numbers starting with decimal point."""
        assert _parse_ic50_value('.5') == 0.5
        assert _parse_ic50_value('.123') == 0.123


class TestParseSingleIc50:
    """Tests for _parse_single_ic50() with error handling."""

    def test_returns_canonical_integer(self):
        """Should return canonical format for integers."""
        errors: list[str] = []
        result = _parse_single_ic50('10', errors=errors, context='test')
        assert result == '10'
        assert errors == []

    def test_returns_canonical_float(self):
        """Should return canonical format for floats (removes trailing zeros)."""
        errors: list[str] = []
        result = _parse_single_ic50('10.500', errors=errors, context='test')
        assert result == '10.5'
        assert errors == []

    def test_returns_canonical_integer_format(self):
        """Should return integer format without decimal point for whole numbers."""
        errors: list[str] = []
        result = _parse_single_ic50('20.0', errors=errors, context='test')
        assert result == '20'  # :g format removes trailing .0
        assert errors == []

    def test_returns_empty_for_none(self):
        """Should return empty string for 'none' value."""
        errors: list[str] = []
        result = _parse_single_ic50('none', errors=errors, context='test')
        assert result == ''
        assert errors == []

    def test_returns_empty_for_empty(self):
        """Should return empty string for empty input."""
        errors: list[str] = []
        result = _parse_single_ic50('', errors=errors, context='test')
        assert result == ''
        assert errors == []

    def test_appends_error_for_invalid_value(self):
        """Should append error message for invalid values."""
        errors: list[str] = []
        result = _parse_single_ic50('invalid', errors=errors, context='test context')
        assert result == ''
        assert len(errors) == 1
        assert "test context: invalid ic50 value 'invalid'" in errors[0]

    def test_strips_whitespace(self):
        """Should strip whitespace before parsing."""
        errors: list[str] = []
        result = _parse_single_ic50('  10.5  ', errors=errors, context='test')
        assert result == '10.5'
        assert errors == []


class TestNormalizeIc50FromRow:
    """Tests for _normalize_ic50_from_row() column resolution."""

    def test_reads_ic50_column(self):
        """Should read from 'ic50' column."""
        row = {'ic50': '10.5', 'ic_50': '20.0'}
        errors: list[str] = []
        result = _normalize_ic50_from_row(row, errors=errors, context='test')
        assert result == '10.5'

    def test_reads_ic_50_column_as_fallback(self):
        """Should read from 'ic_50' column when 'ic50' missing."""
        row = {'ic_50': '20.0'}
        errors: list[str] = []
        result = _normalize_ic50_from_row(row, errors=errors, context='test')
        assert result == '20'  # :g format removes trailing .0

    def test_prefers_ic50_over_ic_50(self):
        """Should prefer 'ic50' over 'ic_50' when both present."""
        row = {'ic50': '10.5', 'ic_50': '20.0'}
        errors: list[str] = []
        result = _normalize_ic50_from_row(row, errors=errors, context='test')
        assert result == '10.5'

    def test_returns_empty_for_missing_columns(self):
        """Should return empty string when columns missing."""
        row = {'other': 'value'}
        errors: list[str] = []
        result = _normalize_ic50_from_row(row, errors=errors, context='test')
        assert result == ''
        assert errors == []

    def test_appends_error_for_invalid_value(self):
        """Should append error for invalid IC50 value."""
        row = {'ic50': 'not_a_number'}
        errors: list[str] = []
        result = _normalize_ic50_from_row(row, errors=errors, context='row 5')
        assert result == ''
        assert len(errors) == 1
        assert 'row 5: invalid ic50 value' in errors[0]


class TestNormalizeFoldIc50FromRow:
    """Tests for _normalize_fold_ic50_from_row() column resolution."""

    def test_reads_fold_ic50_column(self):
        """Should read from 'fold_ic50' column."""
        row = {'fold_ic50': '5.5', 'fold_ic_50': '10.0'}
        errors: list[str] = []
        result = _normalize_fold_ic50_from_row(row, errors=errors, context='test')
        assert result == '5.5'

    def test_reads_fold_ic_50_column_as_fallback(self):
        """Should read from 'fold_ic_50' column when 'fold_ic50' missing."""
        row = {'fold_ic_50': '10.0'}
        errors: list[str] = []
        result = _normalize_fold_ic50_from_row(row, errors=errors, context='test')
        assert result == '10'  # :g format removes trailing .0

    def test_prefers_fold_ic50_over_fold_ic_50(self):
        """Should prefer 'fold_ic50' over 'fold_ic_50'."""
        row = {'fold_ic50': '5.5', 'fold_ic_50': '10.0'}
        errors: list[str] = []
        result = _normalize_fold_ic50_from_row(row, errors=errors, context='test')
        assert result == '5.5'

    def test_returns_empty_for_missing_columns(self):
        """Should return empty string when columns missing."""
        row = {'other': 'value'}
        errors: list[str] = []
        result = _normalize_fold_ic50_from_row(row, errors=errors, context='test')
        assert result == ''


class TestNormalizeScoreFromRow:
    """Tests for _normalize_score_from_row()."""

    def test_parses_integer_score(self):
        """Should parse integer scores."""
        row = {'score': '5'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='test')
        assert result == '5'

    def test_parses_float_score(self):
        """Should parse float scores."""
        row = {'score': '5.75'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='test')
        assert result == '5.75'

    def test_returns_empty_for_none(self):
        """Should return empty string for 'none' value."""
        row = {'score': 'none'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='test')
        assert result == ''

    def test_returns_empty_for_missing_column(self):
        """Should return empty string when column missing."""
        row = {'other': 'value'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='test')
        assert result == ''

    def test_appends_error_for_invalid_score(self):
        """Should append error for invalid score value."""
        row = {'score': 'not_a_number'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='row 10')
        assert result == ''
        assert len(errors) == 1
        assert 'row 10: invalid score value' in errors[0]

    def test_uses_score_column_only(self):
        """Should only read from 'score' column."""
        row = {'score': '5.0', 'other_score': '10.0'}
        errors: list[str] = []
        result = _normalize_score_from_row(row, errors=errors, context='test')
        assert result == '5'  # :g format removes trailing .0


class TestNormalizePhenotypeToken:
    """Tests for _normalize_phenotype_token()."""

    def test_maps_resistant_variants(self):
        """Should map resistant variants to 'resistant'."""
        assert _normalize_phenotype_token('resistant') == 'resistant'
        assert _normalize_phenotype_token('resistance') == 'resistant'
        assert _normalize_phenotype_token('res') == 'resistant'

    def test_maps_sensitive_variants(self):
        """Should map sensitive variants to 'sensitive'."""
        assert _normalize_phenotype_token('sensitive') == 'sensitive'
        assert _normalize_phenotype_token('susceptible') == 'sensitive'
        assert _normalize_phenotype_token('sens') == 'sensitive'

    def test_maps_intermediate_variants(self):
        """Should map intermediate variants."""
        assert _normalize_phenotype_token('intermediate') == 'intermediate'
        assert _normalize_phenotype_token('interm') == 'intermediate'
        assert _normalize_phenotype_token('i') == 'intermediate'

    def test_maps_contradictory(self):
        """Should map contradictory variants."""
        assert _normalize_phenotype_token('contradictory') == 'contradictory'
        assert _normalize_phenotype_token('contra') == 'contradictory'
        assert _normalize_phenotype_token('conflict') == 'contradictory'
        assert _normalize_phenotype_token('conflicting') == 'contradictory'

    def test_maps_unknown_to_unknown(self):
        """Should map unknown/empty to 'unknown'."""
        assert _normalize_phenotype_token('unknown') == 'unknown'
        assert _normalize_phenotype_token('none') == 'unknown'
        assert _normalize_phenotype_token('') == 'unknown'
        assert _normalize_phenotype_token('   ') == 'unknown'

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert _normalize_phenotype_token('RESISTANT') == 'resistant'
        assert _normalize_phenotype_token('Resistant') == 'resistant'
        assert _normalize_phenotype_token('ReSiStAnT') == 'resistant'

    def test_strips_whitespace(self):
        """Should strip whitespace."""
        assert _normalize_phenotype_token('  resistant  ') == 'resistant'
        assert _normalize_phenotype_token('\tsensitive\n') == 'sensitive'

    def test_returns_none_for_unmapped(self):
        """Should return None for unmapped phenotypes."""
        assert _normalize_phenotype_token('invalid') is None
        assert _normalize_phenotype_token('mixed') is None


class TestAppendContradictoryComment:
    """Tests for _append_contradictory_comment()."""

    def test_returns_original_when_not_contradictory(self):
        """Should return original comment when phenotype is not contradictory."""
        result = _append_contradictory_comment(
            'Some comment',
            phenotype='resistant',
            clinical_phenotype='resistant',
        )
        assert result == 'Some comment'

    def test_returns_standard_comment_when_empty(self):
        """Should return standard comment when input is empty."""
        result = _append_contradictory_comment(
            '',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == _CONTRADICTORY_COMMENT

    def test_appends_when_comment_present(self):
        """Should append standard comment to existing comment."""
        result = _append_contradictory_comment(
            'Multiple studies show different results',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'Multiple studies show different results. Publications have contradictory phenotype associations.'

    def test_adds_period_before_appending(self):
        """Should add period if comment doesn't end with punctuation."""
        result = _append_contradictory_comment(
            'Results vary',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'Results vary. Publications have contradictory phenotype associations.'

    def test_preserves_existing_punctuation(self):
        """Should preserve existing punctuation."""
        result = _append_contradictory_comment(
            'Results vary.',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'Results vary. Publications have contradictory phenotype associations.'

    def test_handles_exclamation_mark(self):
        """Should handle exclamation mark as existing punctuation."""
        result = _append_contradictory_comment(
            'Results vary!',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'Results vary! Publications have contradictory phenotype associations.'

    def test_handles_question_mark(self):
        """Should handle question mark as existing punctuation."""
        result = _append_contradictory_comment(
            'Results vary?',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'Results vary? Publications have contradictory phenotype associations.'

    def test_skips_if_already_contains_standard_comment(self):
        """Should not duplicate if comment already contains standard text."""
        result = _append_contradictory_comment(
            _CONTRADICTORY_COMMENT,
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == _CONTRADICTORY_COMMENT

    def test_case_insensitive_check_for_existing_comment(self):
        """Should check for existing comment case-insensitively."""
        result = _append_contradictory_comment(
            'publications have contradictory phenotype associations.',
            phenotype='contradictory',
            clinical_phenotype='contradictory',
        )
        assert result == 'publications have contradictory phenotype associations.'

    def test_works_with_clinical_phenotype_only(self):
        """Should work when only clinical_phenotype is contradictory."""
        result = _append_contradictory_comment(
            'Comment',
            phenotype='resistant',
            clinical_phenotype='contradictory',
        )
        assert 'Publications have contradictory' in result

    def test_works_with_phenotype_only(self):
        """Should work when only phenotype is contradictory."""
        result = _append_contradictory_comment(
            'Comment',
            phenotype='contradictory',
            clinical_phenotype='resistant',
        )
        assert 'Publications have contradictory' in result