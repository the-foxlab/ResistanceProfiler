"""Boolean formula expression engine — tokenization, precedence AST parsing, and canonicalization."""

from __future__ import annotations

import re

RE_FORMULA_TOKEN = re.compile(
    r'\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bXOR\b|[A-Za-z0-9_.:-]+',
    re.IGNORECASE,
)
FORMULA_OPERATORS = frozenset({'AND', 'OR', 'NOT', 'XOR'})

_SORT_RANK = {'ATOM': 0, 'AND': 1, 'OR': 1, 'XOR': 1, 'NOT': 2}


def tokenize_formula_expression(expression: str) -> list[str]:
    """Tokenize a boolean formula expression, normalizing operators to uppercase."""
    tokens = RE_FORMULA_TOKEN.findall(expression)
    if not tokens:
        raise ValueError('empty expression')

    condensed = re.sub(r'\s+', '', expression)
    if condensed != ''.join(tok.replace(' ', '') for tok in tokens):
        raise ValueError('contains unsupported characters')

    return [
        tok.upper() if tok.upper() in FORMULA_OPERATORS or tok in {'(', ')'} else tok
        for tok in tokens
    ]


def parse_formula_ast(expression: str) -> tuple[tuple, list[str]]:
    """Parse ``expression`` into ``(ast_node, referenced_member_ids)``.

    Precedence (lowest to highest): ``OR < XOR < AND < NOT < primary``.
    """
    tokens = tokenize_formula_expression(expression)
    pos = 0
    referenced_ids: list[str] = []

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def consume(expected: str | None = None) -> str:
        nonlocal pos
        token = peek()
        if token is None:
            raise ValueError('unexpected end of expression')
        if expected is not None and token != expected:
            raise ValueError(f'expected {expected!r}, found {token!r}')
        pos += 1
        return token

    def parse_primary() -> tuple:
        token = peek()
        if token is None:
            raise ValueError('unexpected end of expression')
        if token == '(':
            consume('(')
            node = parse_or()
            consume(')')
            return node
        if token in FORMULA_OPERATORS or token == ')':
            raise ValueError(f'unexpected token {token!r}')
        referenced_ids.append(token)
        consume()
        return ('ATOM', token)

    def parse_not() -> tuple:
        if peek() == 'NOT':
            consume('NOT')
            return ('NOT', parse_not())
        return parse_primary()

    def parse_nary(op: str, subparser) -> tuple:
        nodes = [subparser()]
        while peek() == op:
            consume(op)
            nodes.append(subparser())
        return nodes[0] if len(nodes) == 1 else (op, nodes)

    def parse_and() -> tuple:
        return parse_nary('AND', parse_not)

    def parse_xor() -> tuple:
        return parse_nary('XOR', parse_and)

    def parse_or() -> tuple:
        return parse_nary('OR', parse_xor)

    ast_node = parse_or()
    if peek() is not None:
        raise ValueError(f'unexpected token {peek()!r}')
    return ast_node, referenced_ids


def canonicalize_formula_ast(node: tuple) -> tuple:
    """Flatten associative operators and sort operands deterministically for deduplication."""
    node_type = node[0]
    if node_type == 'ATOM':
        return node
    if node_type == 'NOT':
        return ('NOT', canonicalize_formula_ast(node[1]))

    flattened: list[tuple] = []
    for child in node[1]:
        canon = canonicalize_formula_ast(child)
        if canon[0] == node_type:
            flattened.extend(canon[1])
        else:
            flattened.append(canon)
    flattened.sort(key=formula_sort_key)
    return (node_type, flattened)


def formula_sort_key(node: tuple) -> tuple[int, str]:
    """Order positive branches before negated branches, breaking ties lexicographically."""
    return _SORT_RANK.get(node[0], 99), formula_ast_to_string(node)


def formula_ast_to_string(node: tuple) -> str:
    """Serialize a formula AST into a parenthesized canonical expression string."""
    node_type = node[0]
    if node_type == 'ATOM':
        return str(node[1])
    if node_type == 'NOT':
        return f'(NOT {formula_ast_to_string(node[1])})'
    return '(' + f' {node_type} '.join(formula_ast_to_string(c) for c in node[1]) + ')'


def parse_formula_expression(expression: str) -> tuple[str, list[str]]:
    """Parse, canonicalize, and return ``(normalized_expression, referenced_member_ids)``."""
    ast_node, referenced_ids = parse_formula_ast(expression)
    return formula_ast_to_string(canonicalize_formula_ast(ast_node)), referenced_ids
