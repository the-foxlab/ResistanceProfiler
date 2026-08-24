"""
Tests for boolean formula expression parsing.

Covers: respro/db/_rules_formula.py
- _tokenize_formula_expression()
- _parse_formula_expression()
- _canonicalize_formula_ast()
- _formula_ast_to_string()
- _formula_sort_key()
"""

from __future__ import annotations

import pytest

from respro.db._rules_formula import (
    _canonicalize_formula_ast,
    _formula_ast_to_string,
    _formula_sort_key,
    _parse_formula_expression,
    _tokenize_formula_expression,
)


class TestTokenizeFormulaExpression:
    """Tests for _tokenize_formula_expression()."""

    def test_tokenizes_simple_atom(self):
        """Should tokenize a single atomic rule ID."""
        tokens = _tokenize_formula_expression('mut_1')
        assert tokens == ['mut_1']

    def test_tokenizes_multiple_atoms(self):
        """Should tokenize multiple atomic rule IDs."""
        tokens = _tokenize_formula_expression('mut_1 mut_2 mut_3')
        assert tokens == ['mut_1', 'mut_2', 'mut_3']

    def test_tokenizes_operators_uppercase(self):
        """Should convert operators to uppercase."""
        tokens = _tokenize_formula_expression('mut1 and mut2 or mut3')
        assert tokens == ['mut1', 'AND', 'mut2', 'OR', 'mut3']

    def test_tokenizes_parentheses(self):
        """Should tokenize parentheses."""
        tokens = _tokenize_formula_expression('(mut1 OR mut2) AND mut3')
        assert tokens == ['(', 'mut1', 'OR', 'mut2', ')', 'AND', 'mut3']

    def test_tokenizes_not_operator(self):
        """Should tokenize NOT operator."""
        tokens = _tokenize_formula_expression('NOT mut1')
        assert tokens == ['NOT', 'mut1']

    def test_tokenizes_xor_operator(self):
        """Should tokenize XOR operator."""
        tokens = _tokenize_formula_expression('mut1 XOR mut2')
        assert tokens == ['mut1', 'XOR', 'mut2']

    def test_handles_complex_expression(self):
        """Should tokenize complex expressions."""
        tokens = _tokenize_formula_expression('(A AND B) OR (NOT C)')
        assert tokens == ['(', 'A', 'AND', 'B', ')', 'OR', '(', 'NOT', 'C', ')']

    def test_rejects_empty_expression(self):
        """Should raise error for empty expression."""
        with pytest.raises(ValueError, match='empty expression'):
            _tokenize_formula_expression('')

    def test_rejects_whitespace_only(self):
        """Should raise error for whitespace-only expression."""
        with pytest.raises(ValueError, match='empty expression'):
            _tokenize_formula_expression('   ')

    def test_rejects_unsupported_characters(self):
        """Should raise error for unsupported characters."""
        with pytest.raises(ValueError, match='unsupported characters'):
            _tokenize_formula_expression('mut1 + mut2')

    def test_rejects_special_characters(self):
        """Should raise error for special characters."""
        with pytest.raises(ValueError, match='unsupported characters'):
            _tokenize_formula_expression('mut1 & mut2')

    def test_allows_underscores_in_ids(self):
        """Should allow underscores in rule IDs."""
        tokens = _tokenize_formula_expression('rule_123 AND rule_456')
        assert tokens == ['rule_123', 'AND', 'rule_456']

    def test_allows_dashes_in_ids(self):
        """Should allow dashes in rule IDs."""
        tokens = _tokenize_formula_expression('rule-1 AND rule-2')
        assert tokens == ['rule-1', 'AND', 'rule-2']

    def test_allows_colons_in_ids(self):
        """Should allow colons in rule IDs."""
        tokens = _tokenize_formula_expression('gene:A AND gene:B')
        assert tokens == ['gene:A', 'AND', 'gene:B']

    def test_allows_dots_in_ids(self):
        """Should allow dots in rule IDs."""
        tokens = _tokenize_formula_expression('v1.0 AND v2.0')
        assert tokens == ['v1.0', 'AND', 'v2.0']

    def test_allows_numeric_ids(self):
        """Should allow purely numeric rule IDs."""
        tokens = _tokenize_formula_expression('123 AND 456')
        assert tokens == ['123', 'AND', '456']

    def test_case_insensitive_operators(self):
        """Should handle operators in any case."""
        tokens = _tokenize_formula_expression('mut1 And mut2 Or mut3')
        assert tokens == ['mut1', 'AND', 'mut2', 'OR', 'mut3']


class TestParseFormulaExpression:
    """Tests for _parse_formula_expression()."""

    def test_parses_single_atom(self):
        """Should parse a single atomic rule ID."""
        canonical_str, referenced_ids = _parse_formula_expression('mut_1')
        assert canonical_str == 'mut_1'
        assert referenced_ids == ['mut_1']

    def test_parses_and_expression(self):
        """Should parse AND expression."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND B')
        assert canonical_str == '(A AND B)'
        assert referenced_ids == ['A', 'B']

    def test_parses_or_expression(self):
        """Should parse OR expression."""
        canonical_str, referenced_ids = _parse_formula_expression('A OR B')
        assert canonical_str == '(A OR B)'
        assert referenced_ids == ['A', 'B']

    def test_parses_xor_expression(self):
        """Should parse XOR expression."""
        canonical_str, referenced_ids = _parse_formula_expression('A XOR B')
        assert canonical_str == '(A XOR B)'
        assert referenced_ids == ['A', 'B']

    def test_parses_not_expression(self):
        """Should parse NOT expression."""
        canonical_str, referenced_ids = _parse_formula_expression('NOT A')
        assert canonical_str == '(NOT A)'
        assert referenced_ids == ['A']

    def test_operator_precedence_not_over_and(self):
        """NOT should have higher precedence than AND."""
        canonical_str, referenced_ids = _parse_formula_expression('NOT A AND B')
        # Structure: AND with (NOT A) and B - canonical form sorts operands
        assert 'NOT A' in canonical_str
        assert 'B' in canonical_str
        assert 'AND' in canonical_str
        assert referenced_ids == ['A', 'B']

    def test_operator_precedence_and_over_or(self):
        """AND should have higher precedence than OR."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND B OR C')
        # Structure: OR of (A AND B) and C
        assert 'A AND B' in canonical_str
        assert 'OR' in canonical_str
        assert referenced_ids == ['A', 'B', 'C']

    def test_operator_precedence_xor_between_and_or(self):
        """XOR should have higher precedence than OR, lower than AND."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND B XOR C OR D')
        assert 'A AND B' in canonical_str
        assert 'XOR' in canonical_str
        assert 'OR' in canonical_str
        assert referenced_ids == ['A', 'B', 'C', 'D']

    def test_left_associative_and(self):
        """AND should be left-associative."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND B AND C')
        assert canonical_str == '(A AND B AND C)'
        assert referenced_ids == ['A', 'B', 'C']

    def test_left_associative_or(self):
        """OR should be left-associative."""
        canonical_str, referenced_ids = _parse_formula_expression('A OR B OR C')
        assert canonical_str == '(A OR B OR C)'
        assert referenced_ids == ['A', 'B', 'C']

    def test_left_associative_xor(self):
        """XOR should be left-associative."""
        canonical_str, referenced_ids = _parse_formula_expression('A XOR B XOR C')
        assert canonical_str == '(A XOR B XOR C)'
        assert referenced_ids == ['A', 'B', 'C']

    def test_right_associative_not(self):
        """NOT should be right-associative."""
        canonical_str, referenced_ids = _parse_formula_expression('NOT NOT A')
        assert canonical_str == '(NOT (NOT A))'
        assert referenced_ids == ['A']

    def test_parentheses_override_precedence(self):
        """Parentheses should override default precedence."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND (B OR C)')
        assert canonical_str == '(A AND (B OR C))'
        assert referenced_ids == ['A', 'B', 'C']

    def test_nested_parentheses(self):
        """Should handle nested parentheses."""
        canonical_str, referenced_ids = _parse_formula_expression('(A OR (B AND C)) XOR D')
        assert 'B AND C' in canonical_str
        assert 'OR' in canonical_str
        assert 'XOR' in canonical_str
        assert referenced_ids == ['A', 'B', 'C', 'D']

    def test_complex_expression(self):
        """Should parse complex nested expressions."""
        expr = '(A AND B) OR (NOT C AND D)'
        canonical_str, referenced_ids = _parse_formula_expression(expr)
        assert 'A AND B' in canonical_str
        assert 'NOT C' in canonical_str
        assert 'D' in canonical_str
        assert 'OR' in canonical_str
        assert referenced_ids == ['A', 'B', 'C', 'D']

    def test_not_in_parentheses(self):
        """Should handle NOT inside parentheses."""
        canonical_str, referenced_ids = _parse_formula_expression('(NOT A) AND B')
        assert 'NOT A' in canonical_str
        assert 'B' in canonical_str
        assert 'AND' in canonical_str
        assert referenced_ids == ['A', 'B']

    def test_multiple_not_operators(self):
        """Should handle multiple NOT operators."""
        canonical_str, referenced_ids = _parse_formula_expression('NOT A AND NOT B')
        assert 'NOT A' in canonical_str
        assert 'NOT B' in canonical_str
        assert 'AND' in canonical_str
        assert referenced_ids == ['A', 'B']

    def test_rejects_unbalanced_opening_paren(self):
        """Should raise error for unbalanced opening parenthesis."""
        with pytest.raises(ValueError, match='unexpected end of expression'):
            _parse_formula_expression('(A AND B')

    def test_rejects_unbalanced_closing_paren(self):
        """Should raise error for unbalanced closing parenthesis."""
        with pytest.raises(ValueError, match=r"unexpected token '\)'"):
            _parse_formula_expression('A AND B)')

    def test_rejects_operator_after_paren(self):
        """Should raise error for operator after opening paren."""
        with pytest.raises(ValueError, match="unexpected token 'AND'"):
            _parse_formula_expression('(AND A)')

    def test_rejects_double_operator(self):
        """Should raise error for consecutive operators."""
        with pytest.raises(ValueError):
            _parse_formula_expression('A AND OR B')

    def test_tracks_duplicate_rule_ids(self):
        """Should track duplicate rule IDs in referenced_ids."""
        canonical_str, referenced_ids = _parse_formula_expression('A AND A')
        assert referenced_ids == ['A', 'A']

    def test_canonical_form_flattens_and(self):
        """Should flatten nested AND operators in canonical form."""
        canonical_str, _ = _parse_formula_expression('A AND (B AND C)')
        assert canonical_str == '(A AND B AND C)'

    def test_canonical_form_sorts_operands(self):
        """Should sort operands for deterministic canonical form."""
        canonical_str1, _ = _parse_formula_expression('B AND A')
        canonical_str2, _ = _parse_formula_expression('A AND B')
        assert canonical_str1 == canonical_str2

    def test_canonical_form_distinguishes_not(self):
        """Canonical form should distinguish NOT from positive atoms."""
        canonical_str1, _ = _parse_formula_expression('A AND B')
        canonical_str2, _ = _parse_formula_expression('NOT A AND B')
        assert canonical_str1 != canonical_str2


class TestCanonicalizeFormulaAst:
    """Tests for _canonicalize_formula_ast()."""

    def test_atom_unchanged(self):
        """Should return atom nodes unchanged."""
        atom = ('ATOM', 'mut_1')
        result = _canonicalize_formula_ast(atom)
        assert result == atom

    def test_not_wrapped(self):
        """Should wrap NOT nodes but canonicalize child."""
        not_node = ('NOT', ('ATOM', 'A'))
        result = _canonicalize_formula_ast(not_node)
        assert result == ('NOT', ('ATOM', 'A'))

    def test_flattens_nested_and(self):
        """Should flatten nested AND nodes."""
        nested = ('AND', [('AND', [('ATOM', 'A'), ('ATOM', 'B')]), ('ATOM', 'C')])
        result = _canonicalize_formula_ast(nested)
        assert result == ('AND', [('ATOM', 'A'), ('ATOM', 'B'), ('ATOM', 'C')])

    def test_flattens_nested_or(self):
        """Should flatten nested OR nodes."""
        nested = ('OR', [('OR', [('ATOM', 'A'), ('ATOM', 'B')]), ('ATOM', 'C')])
        result = _canonicalize_formula_ast(nested)
        assert result == ('OR', [('ATOM', 'A'), ('ATOM', 'B'), ('ATOM', 'C')])

    def test_sorts_and_operands(self):
        """Should sort AND operands for deterministic ordering."""
        unsorted = ('AND', [('ATOM', 'B'), ('ATOM', 'A')])
        result = _canonicalize_formula_ast(unsorted)
        assert result == ('AND', [('ATOM', 'A'), ('ATOM', 'B')])

    def test_sorts_or_operands(self):
        """Should sort OR operands for deterministic ordering."""
        unsorted = ('OR', [('ATOM', 'C'), ('ATOM', 'A'), ('ATOM', 'B')])
        result = _canonicalize_formula_ast(unsorted)
        assert result == ('OR', [('ATOM', 'A'), ('ATOM', 'B'), ('ATOM', 'C')])

    def test_sorts_xor_operands(self):
        """Should sort XOR operands for deterministic ordering."""
        unsorted = ('XOR', [('ATOM', 'Z'), ('ATOM', 'A')])
        result = _canonicalize_formula_ast(unsorted)
        assert result == ('XOR', [('ATOM', 'A'), ('ATOM', 'Z')])

    def test_keeps_not_after_positive(self):
        """Should sort NOT nodes after positive atoms."""
        mixed = ('AND', [('NOT', ('ATOM', 'B')), ('ATOM', 'A')])
        result = _canonicalize_formula_ast(mixed)
        assert result == ('AND', [('ATOM', 'A'), ('NOT', ('ATOM', 'B'))])

    def test_handles_complex_nested_structure(self):
        """Should canonicalize complex nested structures."""
        ast = (
            'OR',
            [
                ('AND', [('NOT', ('ATOM', 'C')), ('ATOM', 'A')]),
                ('ATOM', 'B'),
            ],
        )
        result = _canonicalize_formula_ast(ast)
        assert result[0] == 'OR'
        assert len(result[1]) == 2


class TestFormulaAstToString:
    """Tests for _formula_ast_to_string()."""

    def test_atom_to_string(self):
        """Should convert atom to string."""
        result = _formula_ast_to_string(('ATOM', 'mut_1'))
        assert result == 'mut_1'

    def test_not_to_string(self):
        """Should convert NOT to string."""
        result = _formula_ast_to_string(('NOT', ('ATOM', 'A')))
        assert result == '(NOT A)'

    def test_and_to_string(self):
        """Should convert AND to string."""
        result = _formula_ast_to_string(('AND', [('ATOM', 'A'), ('ATOM', 'B')]))
        assert result == '(A AND B)'

    def test_or_to_string(self):
        """Should convert OR to string."""
        result = _formula_ast_to_string(('OR', [('ATOM', 'A'), ('ATOM', 'B')]))
        assert result == '(A OR B)'

    def test_xor_to_string(self):
        """Should convert XOR to string."""
        result = _formula_ast_to_string(('XOR', [('ATOM', 'A'), ('ATOM', 'B')]))
        assert result == '(A XOR B)'

    def test_nested_expression_to_string(self):
        """Should convert nested expressions to string."""
        ast = ('OR', [('AND', [('ATOM', 'A'), ('ATOM', 'B')]), ('ATOM', 'C')])
        result = _formula_ast_to_string(ast)
        assert result == '((A AND B) OR C)'

    def test_triple_and_to_string(self):
        """Should convert triple AND to string."""
        ast = ('AND', [('ATOM', 'A'), ('ATOM', 'B'), ('ATOM', 'C')])
        result = _formula_ast_to_string(ast)
        assert result == '(A AND B AND C)'

    def test_not_of_and_to_string(self):
        """Should convert NOT of AND to string."""
        ast = ('NOT', ('AND', [('ATOM', 'A'), ('ATOM', 'B')]))
        result = _formula_ast_to_string(ast)
        assert result == '(NOT (A AND B))'


class TestFormulaSortKey:
    """Tests for _formula_sort_key()."""

    def test_atom_comes_before_not(self):
        """Atoms should sort before NOT nodes."""
        atom_key = _formula_sort_key(('ATOM', 'A'))
        not_key = _formula_sort_key(('NOT', ('ATOM', 'B')))
        assert atom_key < not_key

    def test_compound_same_rank_as_atom(self):
        """AND/OR/XOR should have same rank (1)."""
        and_key = _formula_sort_key(('AND', [('ATOM', 'A')]))
        or_key = _formula_sort_key(('OR', [('ATOM', 'A')]))
        xor_key = _formula_sort_key(('XOR', [('ATOM', 'A')]))
        assert and_key[0] == or_key[0] == xor_key[0] == 1

    def test_not_has_highest_rank(self):
        """NOT should have highest rank (2)."""
        not_key = _formula_sort_key(('NOT', ('ATOM', 'A')))
        assert not_key[0] == 2

    def test_sorts_by_string_representation(self):
        """Should use string representation as secondary sort key."""
        a_key = _formula_sort_key(('ATOM', 'A'))
        b_key = _formula_sort_key(('ATOM', 'B'))
        assert a_key < b_key