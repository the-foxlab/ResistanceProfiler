"""
Boolean formula expression engine for resistance formula rules — tokenization, parsing, and AST.
"""

from __future__ import annotations

import re

_RE_FORMULA_TOKEN = re.compile(
    r'\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bXOR\b|[A-Za-z0-9_.:-]+',
    re.IGNORECASE,
)
_FORMULA_OPERATORS = {'AND', 'OR', 'NOT', 'XOR'}


def _parse_formula_expression(expression: str) -> tuple[str, list[str]]:
    """
    Parse a boolean formula expression into an AST (abstract syntax tree) and canonical string.

    This implements a **recursive descent parser** with operator precedence to convert
    expressions like "mut_1 AND mut_2 OR mut_3" into:
    1. A canonical normalized string (for duplicate detection)
    2. A list of all referenced atomic rule IDs

    **Operator Precedence (lowest to highest):**
    - OR   (lowest precedence)
    - XOR
    - AND
    - NOT  (highest precedence, unary operator)
    - Parentheses and atoms (atomic rule IDs) (highest)

    **How it works:**
    - Tokenization: the expression is first split into tokens (operators, parens, IDs)
    - Parsing: each precedence level has its own function that handles that operator:
      * parse_or_expression () → handles OR (lowest precedence, called first)
      * parse_xor_expression() → handles XOR
      * parse_and_expression() → handles AND
      * parse_not_expression() → handles NOT (unary prefix operator)
      * parse_primary()       → handles atoms and parentheses (highest precedence)
    - Each function calls the next higher precedence function to parse its operands,
      ensuring that lower-precedence operators are parsed at the top level of the tree.

    Example: "A AND B OR C"
    - Parsed as (A AND B) OR C because AND has higher precedence
    - AST structure: ('OR', [('AND', [('ATOM', 'A'), ('ATOM', 'B')]), ('ATOM', 'C')])

    Example: "NOT A AND B"
    - Parsed as (NOT A) AND B because NOT is highest precedence
    - AST structure: ('AND', [('NOT', ('ATOM', 'A')), ('ATOM', 'B')])
    """
    #  Tokenize the expression into a list of operators, parens, and IDs
    tokens = _tokenize_formula_expression(expression)
    position = 0  # Current position in the token stream
    referenced_ids: list[str] = []  # Accumulate all atomic rule IDs referenced in the expression

    # Manage the token stream (position, lookahead, validation)

    def peek() -> str | None:
        """Return the current token WITHOUT advancing position (lookahead)."""
        return tokens[position] if position < len(tokens) else None

    def consume(expected: str | None = None) -> str:
        """
        Advance position and return the current token.

        :param expected: if provided, raise error if current token doesn't match
        :return: the token that was consumed
        """
        nonlocal position
        token = peek()
        if token is None:
            raise ValueError('unexpected end of expression')
        if expected is not None and token != expected:
            raise ValueError(f'expected {expected!r}, found {token!r}')
        position += 1
        return token

    # PARSING FUNCTIONS: Implement recursive descent with operator precedence

    def parse_primary() -> tuple:
        """
        Parse HIGHEST PRECEDENCE items: atoms (atomic rule IDs) and parentheses.

        Grammar:
          primary := '(' or_expression ')' | ATOM

        Returns AST node:
          - ('ATOM', 'rule_id') for atomic rule references
          - Result of parse_or_expression() for parenthesized sub-expressions
        """
        token = peek()
        if token is None:
            raise ValueError('unexpected end of expression')

        # Case 1: Parenthesized sub-expression
        if token == '(':
            consume('(')
            # Recursively parse the full expression inside parens (restart at lowest precedence)
            node = parse_or_expression()
            consume(')')
            return node

        # Case 2: Invalid token (operator or closing paren where atom expected)
        if token in _FORMULA_OPERATORS or token == ')':
            raise ValueError(f'unexpected token {token!r}')

        # Case 3: Atomic rule ID (the leaf of the AST tree)
        referenced_ids.append(token)  # Track that we've seen this ID
        consume()  # Move past this token
        return ('ATOM', token)

    def parse_not_expression() -> tuple:
        """
        Parse NOT operator (unary prefix; applies to next higher-precedence expression).

        Grammar:
          not_expr := 'NOT' not_expr | primary

        Returns AST node:
          - ('NOT', operand) if NOT is present
          - Result of parse_primary() otherwise

        **Key insight:** NOT is right-associative, so "NOT NOT A" means "NOT (NOT A)".
        """
        if peek() == 'NOT':
            consume('NOT')
            # Recursively parse another NOT (or PRIMARY) — allows chaining NOT operators
            return ('NOT', parse_not_expression())
        # No NOT found; parse the next highest-precedence level (primary)
        return parse_primary()

    def parse_and_expression() -> tuple:
        """
        Parse AND operator (binary; left-associative).

        Grammar:
          and_expr := not_expr ('AND' not_expr)*

        Returns AST node:
          - If multiple operands: ('AND', [operand1, operand2, ...])
          - If single operand: that operand (unwrapped)

        **Left-associative:** "A AND B AND C" becomes ('AND', [A, B, C])
        **Flattening:** Multiple ANDs at the same level are combined into a single AND node
        with a list of children (not nested pairs), which simplifies the tree.
        """
        nodes = [parse_not_expression()]
        # Accumulate all AND-separated operands
        while peek() == 'AND':
            consume('AND')
            nodes.append(parse_not_expression())
        # If no AND operators found, return the single operand unwrapped
        if len(nodes) == 1:
            return nodes[0]
        # Multiple operands: wrap in AND node
        return ('AND', nodes)

    def parse_xor_expression() -> tuple:
        """
        Parse XOR operator (binary; left-associative).

        Grammar:
          xor_expr := and_expr ('XOR' and_expr)*

        Returns AST node:
          - If multiple operands: ('XOR', [operand1, operand2, ...])
          - If single operand: that operand (unwrapped)

        **Note:** XOR has the same precedence as AND in this grammar, but is parsed
        at a separate level to establish precedence above AND and below OR.
        """
        nodes = [parse_and_expression()]
        while peek() == 'XOR':
            consume('XOR')
            nodes.append(parse_and_expression())
        if len(nodes) == 1:
            return nodes[0]
        return ('XOR', nodes)

    def parse_or_expression() -> tuple:
        """
        Parse OR operator (binary; left-associative; LOWEST PRECEDENCE).

        Grammar:
          or_expr := xor_expr ('OR' xor_expr)*

        Returns AST node:
          - If multiple operands: ('OR', [operand1, operand2, ...])
          - If single operand: that operand (unwrapped)

        **Lowest precedence:** OR is parsed at the outermost level of the tree.
        "A AND B OR C" → OR applied last, meaning (A AND B) is one operand, C is the other.
        """
        nodes = [parse_xor_expression()]
        while peek() == 'OR':
            consume('OR')
            nodes.append(parse_xor_expression())
        if len(nodes) == 1:
            return nodes[0]
        return ('OR', nodes)

    # Start parsing at the lowest-precedence level (OR), which will recursively
    # call down through XOR → AND → NOT → PRIMARY, building the tree bottom-up.
    ast = parse_or_expression()

    # Verify we've consumed all tokens (no garbage after the final expression)
    if peek() is not None:
        raise ValueError(f'unexpected token {peek()!r}')

    # Convert the AST to canonical form (deterministic ordering for duplicate detection)
    canonical_ast = _canonicalize_formula_ast(ast)

    return _formula_ast_to_string(canonical_ast), referenced_ids


def _tokenize_formula_expression(expression: str) -> list[str]:
    """Tokenize a boolean formula expression and reject unsupported characters."""
    tokens = _RE_FORMULA_TOKEN.findall(expression)
    if not tokens:
        raise ValueError('empty expression')

    condensed_expression = re.sub(r'\s+', '', expression)
    condensed_tokens = ''.join(token.replace(' ', '') for token in tokens)
    if condensed_expression != condensed_tokens:
        raise ValueError('contains unsupported characters')

    normalized_tokens: list[str] = []
    for token in tokens:
        upper = token.upper()
        if upper in _FORMULA_OPERATORS or token in {'(', ')'}:
            normalized_tokens.append(upper)
            continue
        normalized_tokens.append(token)
    return normalized_tokens


def _canonicalize_formula_ast(node: tuple) -> tuple:
    """Return a deterministic AST for duplicate detection and stable storage."""
    node_type = node[0]
    if node_type == 'ATOM':
        return node
    if node_type == 'NOT':
        return ('NOT', _canonicalize_formula_ast(node[1]))

    children = [_canonicalize_formula_ast(child) for child in node[1]]
    flattened: list[tuple] = []
    for child in children:
        if child[0] == node_type:
            flattened.extend(child[1])
        else:
            flattened.append(child)
    flattened.sort(key=_formula_sort_key)
    return (node_type, flattened)


def _formula_sort_key(node: tuple) -> tuple[int, str]:
    """Return a stable sort key that keeps negated branches after positive branches."""
    rank_map = {
        'ATOM': 0,
        'AND': 1,
        'OR': 1,
        'XOR': 1,
        'NOT': 2,
    }
    return rank_map.get(node[0], 99), _formula_ast_to_string(node)


def _formula_ast_to_string(node: tuple) -> str:
    """Serialize a canonical formula AST to a deterministic normalized expression."""
    node_type = node[0]
    if node_type == 'ATOM':
        return str(node[1])
    if node_type == 'NOT':
        return f'(NOT {_formula_ast_to_string(node[1])})'
    joiner = f' {node_type} '
    return '(' + joiner.join(_formula_ast_to_string(child) for child in node[1]) + ')'
