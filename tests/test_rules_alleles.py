"""
Tests for mutation and allele normalization in rule imports.

Covers: respro/db/_rules_alleles.py
- _is_noop_mutation()
- _is_supported_mutation_token()
- _normalize_rule_alleles_for_storage()
- Anchorless deletion detection
- FSX/frameshift handling
"""

from __future__ import annotations

import pytest

from respro.db._rules_alleles import (
    _RE_ANCHORLESS_DEL,
    _RE_REWRITE_TOKEN,
    _is_noop_mutation,
    _is_supported_mutation_token,
    _normalize_rule_alleles_for_storage,
)


class TestIsNoopMutation:
    """Tests for _is_noop_mutation()."""

    def test_same_aa_returns_true(self):
        """Should return True when reference and mutation are the same."""
        assert _is_noop_mutation('A', 'A') is True
        assert _is_noop_mutation('G', 'G') is True

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert _is_noop_mutation('A', 'a') is True
        assert _is_noop_mutation('g', 'G') is True
        assert _is_noop_mutation('Leu', 'LEU') is True

    def test_different_aa_returns_false(self):
        """Should return False when reference and mutation differ."""
        assert _is_noop_mutation('A', 'G') is False
        assert _is_noop_mutation('K', 'R') is False

    def test_empty_strings(self):
        """Should handle empty strings."""
        assert _is_noop_mutation('', '') is True
        assert _is_noop_mutation('A', '') is False


class TestIsSupportedMutationToken:
    """Tests for _is_supported_mutation_token()."""

    def test_standard_aa_mutations_supported(self):
        """Should support standard amino acid mutations."""
        assert _is_supported_mutation_token('A') is True
        assert _is_supported_mutation_token('K') is True
        assert _is_supported_mutation_token('L') is True
        assert _is_supported_mutation_token('ACD') is True

    def test_stop_codon_supported(self):
        """Should support stop codon symbol."""
        assert _is_supported_mutation_token('*') is True

    def test_fsx_mutations_supported(self):
        """Should support FSX (frameshift) mutations."""
        assert _is_supported_mutation_token('FSX') is True
        assert _is_supported_mutation_token('KFSX') is True
        assert _is_supported_mutation_token('RFSX') is True

    def test_ins_any_supported(self):
        """Should support INS_any insertion."""
        assert _is_supported_mutation_token('INS_any') is True

    def test_any_not_supported(self):
        """Should NOT support ANY token."""
        assert _is_supported_mutation_token('ANY') is False

    def test_invalid_aa_not_supported(self):
        """Should reject invalid amino acid letters."""
        assert _is_supported_mutation_token('Z') is False
        assert _is_supported_mutation_token('X') is False
        assert _is_supported_mutation_token('BJZ') is False

    def test_numbers_not_supported(self):
        """Should reject numeric tokens."""
        assert _is_supported_mutation_token('123') is False

    def test_mixed_case_handled(self):
        """Should handle mixed case input (converted to uppercase internally)."""
        assert _is_supported_mutation_token('abc') is False  # B is not valid AA
        assert _is_supported_mutation_token('AKC') is True  # Valid AAs
        assert _is_supported_mutation_token('KFSX') is True


class TestAnchorlessDeletionRegex:
    """Tests for _RE_ANCHORLESS_DEL pattern."""

    def test_matches_single_aa_del(self):
        """Should match single AA deletion."""
        assert _RE_ANCHORLESS_DEL.match('Q35del') is not None
        assert _RE_ANCHORLESS_DEL.match('A123del') is not None

    def test_matches_multiple_aa_del(self):
        """Should match multiple AA deletion."""
        assert _RE_ANCHORLESS_DEL.match('DD676del') is not None
        assert _RE_ANCHORLESS_DEL.match('KK34del') is not None

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        assert _RE_ANCHORLESS_DEL.match('q35DEL') is not None
        assert _RE_ANCHORLESS_DEL.match('Q35DEL') is not None
        assert _RE_ANCHORLESS_DEL.match('q35del') is not None

    def test_rejects_no_del_suffix(self):
        """Should reject tokens without 'del' suffix."""
        assert _RE_ANCHORLESS_DEL.match('Q35') is None
        assert _RE_ANCHORLESS_DEL.match('Q35deletion') is None

    def test_rejects_no_position(self):
        """Should reject tokens without position."""
        assert _RE_ANCHORLESS_DEL.match('Qdel') is None
        assert _RE_ANCHORLESS_DEL.match('del') is None

    def test_rejects_empty(self):
        """Should reject empty string."""
        assert _RE_ANCHORLESS_DEL.match('') is None


class TestRewriteTokenRegex:
    """Tests for _RE_REWRITE_TOKEN pattern."""

    def test_matches_standard_mutation(self):
        """Should match standard mutation format."""
        match = _RE_REWRITE_TOKEN.match('A123G')
        assert match is not None
        assert match.groups() == ('A', '123', 'G')

    def test_matches_stop_codon(self):
        """Should match stop codon mutation."""
        match = _RE_REWRITE_TOKEN.match('K100*')
        assert match is not None
        assert match.groups() == ('K', '100', '*')

    def test_matches_from_stop(self):
        """Should match mutation from stop codon."""
        match = _RE_REWRITE_TOKEN.match('*50K')
        assert match is not None
        assert match.groups() == ('*', '50', 'K')

    def test_matches_multi_aa(self):
        """Should match multi-AA mutations."""
        match = _RE_REWRITE_TOKEN.match('DD100KK')
        assert match is not None
        assert match.groups() == ('DD', '100', 'KK')

    def test_rejects_no_position(self):
        """Should reject mutations without position."""
        assert _RE_REWRITE_TOKEN.match('AG') is None

    def test_rejects_invalid_format(self):
        """Should reject invalid formats."""
        assert _RE_REWRITE_TOKEN.match('123AG') is None
        assert _RE_REWRITE_TOKEN.match('A123') is None


class TestNormalizeRuleAllelesForStorage:
    """Tests for _normalize_rule_alleles_for_storage()."""

    def test_returns_tuple_for_valid_direct_aa(self):
        """Should return normalized tuple for direct AA token."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='A',
            mutation_raw='G',
            position_0based=10,
            context='test row',
            errors=errors,
        )
        assert result is not None
        pos, ref, mut = result
        assert pos == 10
        assert ref == 'A'
        assert mut == 'G'
        assert errors == []

    def test_normalizes_stop_codon(self):
        """Should normalize stop codon to *."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='K',
            mutation_raw='STOP',
            position_0based=50,
            context='test',
            errors=errors,
        )
        assert result is not None
        _, _, mut = result
        assert mut == '*'

    def test_normalizes_frameshift_to_fsx(self):
        """Should normalize frameshift to fsX format (case preserved from reference)."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='R',
            mutation_raw='FS',
            position_0based=100,
            context='test',
            errors=errors,
        )
        assert result is not None
        _, ref, mut = result
        assert mut == 'RfsX'  # Reference case preserved, 'fsX' suffix

    def test_handles_fsx_suffix(self):
        """Should handle existing FSX suffix."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='K',
            mutation_raw='KFSX',
            position_0based=25,
            context='test',
            errors=errors,
        )
        assert result is not None
        _, _, mut = result
        assert mut == 'KfsX'  # Case preserved from input

    def test_returns_none_for_empty_reference(self):
        """Should return None for empty reference."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='',
            mutation_raw='G',
            position_0based=10,
            context='test',
            errors=errors,
        )
        assert result is None

    def test_returns_none_for_empty_mutation(self):
        """Should return None for empty mutation."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='A',
            mutation_raw='',
            position_0based=10,
            context='test',
            errors=errors,
        )
        assert result is None

    def test_appends_error_for_unrecognized_mutation(self):
        """Should append error for unrecognized mutation format."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='A',
            mutation_raw='invalid_mutation_123',
            position_0based=10,
            context='row 5',
            errors=errors,
        )
        assert result is None
        assert len(errors) == 1
        assert 'row 5: unrecognised mutation' in errors[0]

    def test_strips_whitespace(self):
        """Should strip whitespace from inputs."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='  A  ',
            mutation_raw='  G  ',
            position_0based=10,
            context='test',
            errors=errors,
        )
        assert result is not None
        _, ref, mut = result
        assert ref == 'A'
        assert mut == 'G'

    def test_case_insensitive_reference(self):
        """Should uppercase reference."""
        errors: list[str] = []
        result = _normalize_rule_alleles_for_storage(
            reference_aa='a',
            mutation_raw='g',
            position_0based=10,
            context='test',
            errors=errors,
        )
        assert result is not None
        _, ref, mut = result
        assert ref == 'A'
        assert mut == 'G'

    def test_preserves_position(self):
        """Should preserve 0-based position."""
        errors: list[str] = []
        for pos in [0, 10, 100, 1000]:
            result = _normalize_rule_alleles_for_storage(
                reference_aa='A',
                mutation_raw='G',
                position_0based=pos,
                context='test',
                errors=errors,
            )
            assert result is not None
            assert result[0] == pos