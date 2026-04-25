"""
Resistance rule loading — TSV parsing, validation, coordinate detection, and combo rule sets.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from pathlib import Path

from respro.config.settings import CLI_CONFIG
from respro.core.annotation import normalize_mutation
from respro.db.drugs import _get_or_create_drug_id
from respro.db.models import _INTERNAL_FORMULA_COMPONENT_DRUG_NAME
from respro.io.publications import fetch_publication_metadata, fetch_pubmed_metadata

logger = logging.getLogger(__name__)

# Detects anchor-less deletion tokens emitted by companion database converters.
# Form: one or more AA letters + position digits + "del" (case-insensitive), e.g. "Q35del", "DD676del".
_RE_ANCHORLESS_DEL = re.compile(r'^([A-Za-z]+)\d+del$', re.IGNORECASE)
_RE_REWRITE_TOKEN = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$')
_AUTO_SPLIT_GROUP_PREFIX = '__auto_anchor_split_row_'
_RE_FORMULA_TOKEN = re.compile(
    r'\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bXOR\b|[A-Za-z0-9_.:-]+',
    re.IGNORECASE,
)
_FORMULA_OPERATORS = {'AND', 'OR', 'NOT', 'XOR'}
_CONTRADICTORY_COMMENT = 'Publications have contradictory phenotype associations.'


def _append_contradictory_comment(
    comment: str,
    *,
    phenotype: str,
    clinical_phenotype: str,
) -> str:
    """Append a standard explanatory comment when a row is labeled contradictory."""
    if phenotype != 'contradictory' and clinical_phenotype != 'contradictory':
        return comment

    normalized_comment = comment.strip()
    if _CONTRADICTORY_COMMENT.lower() in normalized_comment.lower():
        return normalized_comment
    if not normalized_comment:
        return _CONTRADICTORY_COMMENT
    if normalized_comment.endswith(('.', '!', '?')):
        return f'{normalized_comment} {_CONTRADICTORY_COMMENT}'
    return f'{normalized_comment}. {_CONTRADICTORY_COMMENT}'


def _normalize_publication_token(token: str) -> tuple[str, str, str]:
    """
    Normalise a single publication token to (doi, pubmed_id, raw_input).

    Accepted forms:
    - ``https://doi.org/10.xxx`` or ``http://doi.org/10.xxx``
    - ``doi.org/10.xxx``
    - ``doi:10.xxx``
    - ``PMID:12345678`` (case-insensitive) — pubmed_id only; doi resolved at insert time
    - anything else → kept as raw_input only

    :param token: single publication string
    :return: (doi, pubmed_id, raw_input) tuple
    """
    t = token.strip()
    if not t:
        return '', '', ''

    lower = t.lower()

    if lower.startswith('pmid:'):
        return '', t[5:].strip(), t

    for prefix in CLI_CONFIG.parsing.doi_prefixes:
        if lower.startswith(prefix):
            return t[len(prefix):].strip(), '', t

    if lower.startswith('doi.org/'):
        return t[8:].strip(), '', t

    if lower.startswith('doi:'):
        return t[4:].strip(), '', t

    return '', '', t


def _parse_publication_entries(raw: str) -> list[tuple[str, str, str]]:
    """
    Split a comma-separated publication string into normalised (doi, pubmed_id, raw_input) tuples.

    :param raw: raw publication string from TSV cell
    :return: list of (doi, pubmed_id, raw_input) tuples; empty entries are dropped
    """
    entries = []
    for token in raw.split(','):
        doi, pubmed_id, raw_input = _normalize_publication_token(token.strip())
        if doi or pubmed_id or raw_input:
            entries.append((doi, pubmed_id, raw_input))
    return entries


def _get_or_create_publication(
    conn: sqlite3.Connection,
    doi: str,
    pubmed_id: str,
    raw_input: str,
    additional_info: bool,
    pub_cache: dict[str, int],
) -> int:
    """
    Return the id of an existing publication row, creating one if needed.

    Dedup key: ``doi`` when non-empty (including DOIs resolved from a PMID);
    otherwise ``raw_input``.  Both the resolved key and the original
    ``raw_input`` token are stored in the cache so that repeated references
    to the same PMID skip the network lookup on every call after the first.

    When ``additional_info`` is True:
    - A PMID is looked up via NCBI E-utilities, which returns both the title
      and the DOI (when available) in a single call.
    - If no PMID is present but a DOI is, the title is fetched from CrossRef.
    Both lookups are best-effort and non-fatal; a missing title is acceptable.

    :param conn: SQLite database connection
    :param doi: bare DOI string (may be empty)
    :param pubmed_id: PubMed ID digits string (may be empty)
    :param raw_input: original curator token (preserved as fallback)
    :param additional_info: whether to attempt HTTP metadata resolution
    :param pub_cache: in-process cache mapping dedup key → publication id
    :return: publication row id
    """
    # Fast path: raw_input is always known before any network call; if we have
    # already processed this exact token (e.g. the same PMID appears on many
    # rules), return immediately without hitting the network again.
    if raw_input in pub_cache:
        return pub_cache[raw_input]

    prefetched_title = ''
    if additional_info and pubmed_id:
        meta = fetch_pubmed_metadata(pubmed_id)
        if meta:
            if meta['doi'] and not doi:
                doi = meta['doi']
                logger.info('Resolved PMID:%s → DOI %s', pubmed_id, doi)
            prefetched_title = meta['title']

    cache_key = doi if doi else raw_input

    if cache_key in pub_cache:
        # The resolved DOI is already cached (e.g. reached via a different raw form).
        # Also register raw_input so future calls hit this fast path.
        pub_cache[raw_input] = pub_cache[cache_key]
        return pub_cache[cache_key]

    conn.row_factory = sqlite3.Row
    if doi:
        row = conn.execute(
            'SELECT id FROM publication WHERE doi = ? LIMIT 1', (doi,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM publication WHERE doi = '' AND raw_input = ? LIMIT 1", (raw_input,)
        ).fetchone()

    if row is not None:
        pub_id = int(row['id'])
        pub_cache[cache_key] = pub_id
        pub_cache[raw_input] = pub_id
        return pub_id

    # Title: from NCBI (already prefetched), or CrossRef fallback for DOI-only entries.
    title = prefetched_title
    if additional_info and not title and doi:
        meta = fetch_publication_metadata(doi)
        if meta:
            title = meta.get('title', '')

    cur = conn.execute(
        'INSERT INTO publication (doi, title, pubmed_id, raw_input) VALUES (?, ?, ?, ?)',
        (doi, title, pubmed_id, raw_input),
    )
    pub_id = int(cur.lastrowid)  # type: ignore[arg-type]
    pub_cache[cache_key] = pub_id
    pub_cache[raw_input] = pub_id
    return pub_id


def _link_rule_publications(
    conn: sqlite3.Connection,
    rule_id: int,
    raw_publication: str,
    additional_info: bool,
    pub_cache: dict[str, int],
) -> None:
    """Parse, resolve, and link all publications in a raw TSV cell to a single rule."""
    for doi, pubmed_id, raw_input in _parse_publication_entries(raw_publication):
        pub_id = _get_or_create_publication(
            conn, doi, pubmed_id, raw_input, additional_info, pub_cache,
        )
        conn.execute(
            'INSERT OR IGNORE INTO rule_publication (rule_id, publication_id) VALUES (?, ?)',
            (rule_id, pub_id),
        )


def _link_formula_rule_publications(
    conn: sqlite3.Connection,
    formula_rule_id: int,
    raw_publication: str,
    additional_info: bool,
    pub_cache: dict[str, int],
) -> None:
    """Parse, resolve, and link all publications in a raw TSV cell to one formula rule."""
    for doi, pubmed_id, raw_input in _parse_publication_entries(raw_publication):
        pub_id = _get_or_create_publication(
            conn, doi, pubmed_id, raw_input, additional_info, pub_cache,
        )
        conn.execute(
            'INSERT OR IGNORE INTO resistance_formula_rule_publication '
            '(formula_rule_id, publication_id) VALUES (?, ?)',
            (formula_rule_id, pub_id),
        )


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


def _parse_formula_expression(expression: str) -> tuple[tuple, str, list[str]]:
    """
    Parse a boolean formula expression into an AST (abstract syntax tree) and canonical string.

    This implements a **recursive descent parser** with operator precedence to convert
    expressions like "mut_1 AND mut_2 OR mut_3" into:
    1. An AST (nested tuples) representing the logical hierarchy
    2. A canonical normalized string (for duplicate detection)
    3. A list of all referenced atomic rule IDs

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


def _formula_ast_to_string(node: tuple) -> str:
    """Serialize a canonical formula AST to a deterministic normalized expression."""
    node_type = node[0]
    if node_type == 'ATOM':
        return node[1]
    if node_type == 'NOT':
        return f'(NOT {_formula_ast_to_string(node[1])})'
    joiner = f' {node_type} '
    return '(' + joiner.join(_formula_ast_to_string(child) for child in node[1]) + ')'


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


def _get_value(row: dict[str, str], *keys: str) -> str:
    """Return the first non-empty value for *keys* from a TSV row."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            value = value.strip()
            if value:
                return value
    return ''


def _parse_ic50_value(raw: str) -> float | None:
    """Parse a numeric IC50 fold-change from a raw TSV cell value."""
    value = raw.strip()
    if not value or value.lower() == 'none':
        return None

    match = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', value)
    if match is None:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_single_ic50(raw: str, *, errors: list[str], context: str) -> str:
    """Parse one IC50 cell value and return a canonical numeric string or empty string."""
    value = raw.strip()
    if not value or value.lower() == 'none':
        return ''
    parsed = _parse_ic50_value(value)
    if parsed is None:
        errors.append(f'{context}: invalid ic50 value {value!r}')
        return ''
    return f'{parsed:g}'


def _normalize_ic50_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical IC50 text or empty string; reads ic50/ic_50 columns only."""
    return _parse_single_ic50(_get_value(row, 'ic50', 'ic_50'), errors=errors, context=context)


def _normalize_fold_ic50_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical fold-IC50 text or empty string; reads fold_ic50/fold_ic_50 columns only."""
    return _parse_single_ic50(_get_value(row, 'fold_ic50', 'fold_ic_50'), errors=errors, context=context)


def _normalize_score_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical score text or empty string; reads the score column."""
    raw = _get_value(row, 'score')
    if not raw:
        return ''
    value = raw.strip()
    if not value or value.lower() == 'none':
        return ''
    try:
        return f'{float(value):g}'
    except ValueError:
        errors.append(f'{context}: invalid score value {value!r}')
        return ''



def _normalize_phenotype_token(raw: str) -> str | None:
    """Map supported phenotype inputs to canonical internal values."""
    value = raw.strip().lower()
    if not value or value == 'none':
        return 'unknown'

    mapping = {
        'resistant': 'resistant',
        'resistance': 'resistant',
        'res': 'resistant',
        'r': 'resistant',
        'true': 'resistant',
        '1': 'resistant',
        'intermediate': 'intermediate',
        'interm': 'intermediate',
        'i': 'intermediate',
        'sensitive': 'sensitive',
        'susceptible': 'sensitive',
        'sensi': 'sensitive',
        'sens': 'sensitive',
        's': 'sensitive',
        'false': 'sensitive',
        '0': 'sensitive',
        'unknown': 'unknown',
        'na': 'unknown',
        'n/a': 'unknown',
        'nd': 'unknown',
        'contradictory': 'contradictory',
        'contra': 'contradictory',
        'conflict': 'contradictory',
        'conflicting': 'contradictory',
    }
    return mapping.get(value)


def _normalize_phenotypes_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> tuple[str, str]:
    """Normalize phenotype and clinical_phenotype to canonical values independently."""
    phenotype_raw = _get_value(row, 'phenotype')
    clinical_raw = _get_value(row, 'clinical_phenotype')

    phenotype_normalized = _normalize_phenotype_token(phenotype_raw) if phenotype_raw else 'unknown'
    if phenotype_raw and phenotype_normalized is None:
        errors.append(f'{context}: invalid phenotype value {phenotype_raw!r}')
        phenotype_normalized = 'unknown'

    clinical_normalized = _normalize_phenotype_token(clinical_raw) if clinical_raw else 'unknown'
    if clinical_raw and clinical_normalized is None:
        errors.append(f'{context}: invalid clinical_phenotype value {clinical_raw!r}')
        clinical_normalized = 'unknown'

    return phenotype_normalized, clinical_normalized


def _is_noop_mutation(reference_aa: str, mutation: str) -> bool:
    """Return True when a rule encodes no amino-acid change."""
    return reference_aa.upper() == mutation.upper()


def _is_supported_mutation_token(mutation: str) -> bool:
    """Return True when a normalized mutation token uses supported AA letters."""
    aa_letters = frozenset('ACDEFGHIKLMNPQRSTVWY')

    token = mutation.upper()
    if token == 'ANY':
        return False
    if token in {'FSX', '*'} or (token.endswith('FSX') and len(token) == 4):
        return True
    if re.fullmatch(r'[A-Z]+', token):
        return set(token) <= aa_letters
    return False


def _normalize_rule_alleles_for_storage(
    *,
    reference_aa: str,
    mutation_raw: str,
    position_0based: int,
    context: str,
    errors: list[str],
) -> tuple[int, str, str] | None:
    """
    Normalize one rule row to canonical DB storage columns.

    Supports both legacy rewrite notation (e.g. ``Y4YDDD``, ``YP4Y``) and
    the new spaltenorientierte representation (e.g. ``reference='YP', mutation='Y'``).

    :param reference_aa: value from TSV reference column
    :param mutation_raw: value from TSV mutation column
    :param position_0based: row position converted to 0-based
    :param context: human-readable context for validation errors
    :param errors: shared error collector
    :return: (position_0based, reference, mutation) or None on validation failure
    """
    reference = reference_aa.strip().upper()
    mutation_input = mutation_raw.strip()
    if not reference or not mutation_input:
        return None

    token_upper = mutation_input.upper()
    is_direct_aa_token = (
        re.fullmatch(r'[A-Za-z*]+', mutation_input) is not None
        and not token_upper.startswith('INS')
        and token_upper != 'DEL'
    )

    if is_direct_aa_token:
        if token_upper in {'*', 'STOP'}:
            mutation = '*'
        elif token_upper.startswith('FS') or token_upper.startswith('FRAMESHIFT'):
            mutation = f'{reference}fsX'
        elif token_upper.endswith('FSX') and len(token_upper) == 4:
            mutation = f'{reference}fsX'
        else:
            mutation = token_upper
    else:
        mutation = normalize_mutation(
            mutation_input,
            reference=reference,
            position_1based=position_0based + 1,
        )
        if mutation is None:
            errors.append(f'{context}: unrecognised mutation {mutation_raw!r}')
            return None

    if mutation == 'fsX':
        mutation = f'{reference}fsX'

    rewrite_match = _RE_REWRITE_TOKEN.fullmatch(mutation.upper())
    if rewrite_match is None:
        return position_0based, reference, mutation

    left, pos_text, right = rewrite_match.groups()

    # SNP-style rewrites keep mutation-only storage (alt AA token).
    if len(left) == 1 and len(right) == 1:
        return position_0based, reference, right

    anchor_pos_0based = int(pos_text) - 1
    if anchor_pos_0based != position_0based:
        errors.append(
            f'{context}: position {position_0based + 1} conflicts with mutation token '
            f'{mutation!r} (anchor position is {anchor_pos_0based + 1})'
        )
        return None

    return anchor_pos_0based, left, right


def _split_anchor_changed_indel_token(
    mutation_raw: str,
) -> tuple[str, str, str, str] | None:
    """
    Split mixed anchor-change indel rewrite tokens into two events.

    Examples:
    - ``GY50A`` -> ``G50A`` + ``GY50G``
    - ``G50AW`` -> ``G50A`` + ``G50GW``

    Returns tuple ``(sub_ref, sub_mut, indel_ref, indel_mut)`` using canonical
    storage alleles (reference/mutation columns), or ``None`` when the token is
    not a mixed anchor-change indel.
    """
    lowered = mutation_raw.lower()
    if (
        lowered.endswith('del')
        or 'ins' in lowered
        or 'frameshift' in lowered
        or 'stop' in lowered
    ):
        return None

    match = _RE_REWRITE_TOKEN.fullmatch(mutation_raw.upper())
    if match is None:
        return None

    left, _, right = match.groups()
    if (
        right.startswith('DEL')
        or right.startswith('INS')
        or right.startswith('FS')
        or right.startswith('STOP')
    ):
        return None

    if len(left) == len(right):
        return None
    if left[0] == right[0]:
        return None

    left_tail = left[1:]
    right_tail = right[1:]

    # Simple insertion/deletion around a changed anchor: after removing the
    # first AA, one side must be a strict prefix extension of the other.
    if not (
        (right_tail.startswith(left_tail) and len(right_tail) > len(left_tail))
        or (left_tail.startswith(right_tail) and len(left_tail) > len(right_tail))
    ):
        return None

    sub_ref = left[0]
    sub_mut = right[0]
    indel_ref = left
    indel_mut = left[0] + right_tail
    return sub_ref, sub_mut, indel_ref, indel_mut


def _expand_anchor_changed_indel_rules(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Expand mixed anchor-change indel rows into explicit two-member grouped rows.

    Rows that cannot be split safely are returned unchanged and continue through
    normal validation.
    """
    expanded_rows: list[dict[str, str]] = []
    split_count = 0

    for row_number, row in enumerate(rows, start=2):
        mutation_raw = _get_value(row, 'mutation')
        position_raw = _get_value(row, 'position')
        reference_raw = _get_value(row, 'reference').upper()

        split = _split_anchor_changed_indel_token(mutation_raw)
        if split is None:
            expanded_rows.append(row)
            continue

        rewrite_match = _RE_REWRITE_TOKEN.fullmatch(mutation_raw.upper())
        if rewrite_match is None:
            expanded_rows.append(row)
            continue
        left, token_pos_text, _ = rewrite_match.groups()

        try:
            token_pos = int(token_pos_text)
            row_pos = int(position_raw)
        except ValueError:
            expanded_rows.append(row)
            continue

        if token_pos != row_pos:
            expanded_rows.append(row)
            continue

        if reference_raw and reference_raw not in {left, left[0]}:
            expanded_rows.append(row)
            continue

        sub_ref, sub_mut, indel_ref, indel_mut = split
        group_name = _get_value(row, 'group_id', 'rule_group') or (
            f'{_AUTO_SPLIT_GROUP_PREFIX}{row_number}'
        )

        substitution_row = dict(row)
        substitution_row['reference'] = sub_ref
        substitution_row['mutation'] = sub_mut
        substitution_row['group_id'] = group_name
        member_id = _get_value(row, 'member_id', 'rule_id')
        if member_id:
            substitution_row['member_id'] = f'{member_id}__sub'
        else:
            substitution_row['member_id'] = f'{group_name}__sub'

        indel_row = dict(row)
        indel_row['reference'] = indel_ref
        indel_row['mutation'] = indel_mut
        indel_row['group_id'] = group_name
        if member_id:
            indel_row['member_id'] = f'{member_id}__indel'
        else:
            indel_row['member_id'] = f'{group_name}__indel'

        expanded_rows.extend([substitution_row, indel_row])
        split_count += 1

    if split_count:
        logger.info(
            'Expanded %d mixed anchor-change indel rule(s) into explicit combo members',
            split_count,
        )

    return expanded_rows


def _resolve_rule_gene_id(candidates: list[sqlite3.Row], reference_identifier: str) -> int | None:
    """Resolve a rule row to a unique gene_id using optional reference information."""
    if not candidates:
        return None
    if len(candidates) == 1 and not reference_identifier:
        return candidates[0]['gene_id']
    if not reference_identifier:
        return None

    matched = [
        c for c in candidates
        if reference_identifier in {c['reference_name'], c['reference_accession']}
    ]
    if len(matched) == 1:
        return matched[0]['gene_id']
    return None


def _get_gene_aa_sequence(
    candidates: list[sqlite3.Row],
    reference_identifier: str,
) -> str:
    """
    Return the aa_sequence for the best-matching gene candidate.

    If a reference_identifier is given, match it; otherwise use the only
    candidate if unambiguous.

    :param candidates: list of gene rows from the DB
    :param reference_identifier: optional reference identifier from the rules row
    :return: amino acid sequence string or empty string if ambiguous/unavailable
    """
    if reference_identifier:
        for c in candidates:
            if reference_identifier in {c['reference_name'], c['reference_accession']}:
                return c['aa_sequence'] or ''
    if len(candidates) == 1:
        return candidates[0]['aa_sequence'] or ''
    return ''


def _resolve_anchorless_deletion(
    deleted_block: str,
    position_0based: int,
    aa_seq: str,
) -> tuple[int, str, str] | None:
    """
    Resolve an anchor-less deletion token to canonical form.

    The anchor residue is the AA immediately preceding the deleted block.  It is
    fetched from the gene sequence and used to build the canonical deletion token
    ``ANCHOR + DELETED_BLOCK + ANCHOR_POS_1BASED + ANCHOR``.

    :param deleted_block: uppercase deleted AA block (e.g. ``'Q'`` or ``'DD'``)
    :param position_0based: 0-based start index of the deletion in the gene sequence
    :param aa_seq: amino-acid sequence of the gene
    :return: ``(anchor_position_0based, anchor_aa, canonical_mutation)`` or ``None``
             when the anchor cannot be resolved (position 0, or block mismatch)
    """
    anchor_idx = position_0based - 1
    if anchor_idx < 0:
        return None  # no preceding residue

    end_idx = position_0based + len(deleted_block)
    if end_idx > len(aa_seq):
        return None  # deletion extends beyond sequence

    actual_block = aa_seq[position_0based:end_idx].upper()
    if actual_block != deleted_block.upper():
        return None  # gene sequence does not match claimed deleted block

    anchor_aa = aa_seq[anchor_idx].upper()
    anchor_pos_1based = anchor_idx + 1
    canonical = f'{anchor_aa}{deleted_block.upper()}{anchor_pos_1based}{anchor_aa}'
    return anchor_idx, anchor_aa, canonical


def _detect_coordinate_base(
    rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
) -> int:
    """
    Detect whether the rules TSV uses 0-based or 1-based amino acid positions.

    Compares the ``reference`` column against the pre-translated ``aa_sequence``
    stored for each gene. Returns 1 if all verifiable positions match the
    1-based interpretation, 0 if they match the 0-based interpretation.

    :param rows: all parsed rows from the rules TSV
    :param genes_by_name: gene lookup built from the project DB
    :return: 0 or 1 indicating the detected coordinate base
    :raises ValueError: if positions match neither system consistently
    """
    matches_1based = 0
    matches_0based = 0
    verifiable = 0

    for row in rows:
        # Only rows with gene + position + reference AA can contribute to detection.
        gene_name = _get_value(row, 'gene')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or gene_name not in genes_by_name:
            continue
        anchor_ref = ref_aa[0].upper()

        reference_identifier = _get_value(row, 'reference_identifier')
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError:
            continue

        verifiable += 1
        if 1 <= pos <= len(aa_seq) and aa_seq[pos - 1].upper() == anchor_ref:
            matches_1based += 1
        if 0 <= pos < len(aa_seq) and aa_seq[pos].upper() == anchor_ref:
            matches_0based += 1

    if verifiable == 0:
        # Keep initialization usable when source rules do not carry verifiable ref AAs.
        logger.warning(
            'Cannot verify coordinate base — no rules have both a reference AA '
            'and a gene with aa_sequence; assuming 1-based'
        )
        return 1

    if matches_1based > matches_0based:
        return 1
    if matches_0based > matches_1based:
        return 0

    # Equal non-zero: both systems match the same set of positions (e.g. all
    # ref AAs happen to be identical at both offsets). Default to 1-based.
    if matches_1based == matches_0based > 0:
        logger.warning(
            'Both 0-based and 1-based positions match all %d verifiable rules; '
            'assuming 1-based (standard biochemistry convention)',
            verifiable,
        )
        return 1

    # Nothing matched in either system
    raise ValueError(
        f'Rules TSV coordinate system could not be determined: none of the '
        f'{verifiable} verifiable reference AAs match the gene sequences in either '
        'a 0-based or 1-based interpretation. '
        'Check that the reference amino acids in the rules file match the GenBank sequence.'
    )


def _validate_reference_amino_acids(
    rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
    coord_base: int,
) -> set[tuple[str, str, str, str]]:
    """
    Check reference AAs in the rules TSV against the stored gene aa_sequence.

    Out-of-range positions and reference AA mismatches are both logged as
    warnings and collected for skipping — callers must filter out returned keys
    before inserting rules.

    :param rows: all parsed rows from the rules TSV
    :param genes_by_name: gene lookup with aa_sequence from the project DB
    :param coord_base: detected coordinate base (0 or 1)
    :return: set of ``(gene_name, position_raw, reference_identifier, ref_aa)``
             tuples for rows whose reference AA does not match the gene sequence
    """
    mismatch_keys: set[tuple[str, str, str, str]] = set()
    mismatch_details: list[str] = []
    out_of_range: list[str] = []

    for row in rows:
        gene_name = _get_value(row, 'gene')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or gene_name not in genes_by_name:
            continue

        reference_identifier = _get_value(row, 'reference_identifier')
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError:
            continue

        pos_0based = pos - coord_base
        ref_block = ref_aa.upper()
        end_pos = pos_0based + len(ref_block)
        if 0 <= pos_0based and end_pos <= len(aa_seq):
            actual = aa_seq[pos_0based:end_pos].upper()
            if actual != ref_block:
                mismatch_keys.add((gene_name, position_raw, reference_identifier, ref_aa))
                mismatch_details.append(
                    f'  {reference_identifier} gene {gene_name!r} pos {pos} ({coord_base}-based): '
                    f'rule says {ref_aa!r}, gene sequence has {actual!r} — rule will be skipped'
                )
        else:
            out_of_range.append(
                f'  {reference_identifier} gene {gene_name!r} pos {pos} ({coord_base}-based): '
                f'out of range (aa_sequence length = {len(aa_seq)}) — rule will be skipped'
            )

    if out_of_range:
        logger.warning(
            '%d rule(s) reference positions beyond the end of the annotated protein '
            'and will be skipped:\n%s',
            len(out_of_range),
            '\n'.join(out_of_range),
        )

    if mismatch_details:
        logger.warning(
            '%d rule(s) have reference AA mismatches and will be skipped:\n%s',
            len(mismatch_details),
            '\n'.join(mismatch_details),
        )

    return mismatch_keys


def _rule_exists(
    conn: sqlite3.Connection,
    *,
    gene_id: int,
    drug_id: int,
    reference_identifier: str,
    position: int,
    reference: str,
    mutation: str,
) -> bool:
    """Return True when a semantically identical rule is already stored."""
    row = conn.execute(
        'SELECT id FROM resistance_rule '
        'WHERE gene_id = ? AND drug_id = ? AND reference_identifier = ? '
        'AND position = ? AND reference = ? AND mutation = ? '
        'LIMIT 1',
        (gene_id, drug_id, reference_identifier, position, reference, mutation),
    ).fetchone()
    return row is not None


def _external_rule_id_exists(conn: sqlite3.Connection, external_id: str) -> bool:
    """Return True when an atomic external rule id is already stored."""
    row = conn.execute(
        'SELECT id FROM resistance_rule WHERE external_id = ? LIMIT 1',
        (external_id,),
    ).fetchone()
    return row is not None


def _load_rule_ids_by_external_id(
    conn: sqlite3.Connection,
    external_ids: set[str],
) -> dict[str, int]:
    """Return a mapping from atomic external rule ids to resistance_rule row ids."""
    if not external_ids:
        return {}

    placeholders = ','.join('?' * len(external_ids))
    rows = conn.execute(
        f'SELECT id, external_id FROM resistance_rule WHERE external_id IN ({placeholders})',
        sorted(external_ids),
    ).fetchall()
    return {row['external_id']: int(row['id']) for row in rows}


def _formula_rule_exists(
    conn: sqlite3.Connection,
    *,
    formula_id: str,
    drug_id: int,
    normalized_expression: str,
) -> tuple[bool, bool]:
    """Return whether a formula id or a canonical drug-level expression already exists."""
    id_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule WHERE formula_id = ? LIMIT 1',
        (formula_id,),
    ).fetchone() is not None
    expression_exists = conn.execute(
        'SELECT 1 FROM resistance_formula_rule '
        'WHERE drug_id = ? AND normalized_expression = ? LIMIT 1',
        (drug_id, normalized_expression),
    ).fetchone() is not None
    return id_exists, expression_exists


def _build_gene_lookup(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """
    Build a gene lookup table from the project database.

    :param conn: SQLite database connection
    :return: dictionary mapping gene names to lists of gene rows
    """
    conn.row_factory = sqlite3.Row
    gene_lookup_rows = conn.execute(
        """
        SELECT
            g.id AS gene_id,
            g.name AS gene_name,
            g.aa_sequence AS aa_sequence,
            r.name AS reference_name,
            r.accession AS reference_accession
        FROM gene g
        JOIN reference r ON r.id = g.reference_id
        """
    ).fetchall()

    genes_by_name: dict[str, list[sqlite3.Row]] = {}
    for row in gene_lookup_rows:
        genes_by_name.setdefault(row['gene_name'], []).append(row)

    return genes_by_name


def _load_resistance_rules(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    require_external_ids: bool = False,
    additional_info: bool = False,
) -> tuple[int, set[str], set[str], dict[str, str]]:
    """
    Load resistance rules from TSV file; return count of inserted rules and grouped IDs.

    All rows are imported as atomic single rules into ``resistance_rule``.
    Grouping metadata from ``group_id``/``member_id`` is captured for formula
    validation only; no implicit combination rules are created during this step.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param rules_tsv: path to resistance rules TSV file
    :return: (inserted atomic-rule count, set of group_id values found in rules TSV,
             set of declared external_ids in rules TSV,
             dict of external_id -> skip reason for ids that were skipped)
    """
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}
    count = 0
    skipped_duplicates = 0

    conn.row_factory = sqlite3.Row
    genes_by_name = _build_gene_lookup(conn)

    errors: list[str] = []
    skipped_ref: list[str] = []
    skipped_gene: list[str] = []
    skipped_invalid_aa: list[str] = []
    skipped_duplicates_detail: list[str] = []
    skipped_identical_member_id_rows: list[str] = []
    grouped_missing_antiviral_rows: list[str] = []
    # Maps external_id → skip reason, for formula rule skip messages.
    skipped_external_ids: dict[str, str] = {}
    seen_external_id_signatures: dict[str, tuple[int, tuple[int, str, int, str, str]]] = {}

    with open(rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        all_rows = _expand_anchor_changed_indel_rules(list(reader))

    header_columns = {col.strip() for col in (reader.fieldnames or []) if col}
    present_ic50 = sorted(header_columns & {'ic50', 'ic_50'})
    present_fold = sorted(header_columns & {'fold_ic50', 'fold_ic_50'})
    if len(present_ic50) > 1:
        raise ValueError(
            'Rules validation failed:\n'
            '- only one IC50 column is allowed; found '
            + ', '.join(repr(col) for col in present_ic50)
        )
    if len(present_fold) > 1:
        raise ValueError(
            'Rules validation failed:\n'
            '- only one fold-IC50 column is allowed; found '
            + ', '.join(repr(col) for col in present_fold)
        )

    required_field_errors: list[str] = []
    external_ids: list[str] = []
    declared_external_ids: set[str] = set()
    grouped_ids: set[str] = set()
    for row_number, row in enumerate(all_rows, start=2):
        if not _get_value(row, 'reference_identifier'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference_identifier'
            )
        if not _get_value(row, 'reference'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference'
            )
        group_ids = [
            value.strip()
            for value in _get_value(row, 'group_id', 'rule_group').split(',')
            if value.strip()
        ]
        grouped_ids.update(group_ids)

        external_id = _get_value(row, 'member_id', 'rule_id')
        if external_id:
            if external_id.upper() in _FORMULA_OPERATORS:
                required_field_errors.append(
                    f'row {row_number}: member_id {external_id!r} uses a reserved boolean keyword'
                )
            external_ids.append(external_id)
            declared_external_ids.add(external_id)
        elif group_ids:
            required_field_errors.append(
                f'row {row_number}: missing required field member_id'
            )

    existing_external_ids = sorted(
        external_id for external_id in set(external_ids) if _external_rule_id_exists(conn, external_id)
    )
    if existing_external_ids:
        required_field_errors.append(
            'atomic rule ids already exist in project: '
            + ', '.join(repr(external_id) for external_id in existing_external_ids)
        )

    if required_field_errors:
        formatted = '\n'.join(f'- {message}' for message in required_field_errors)
        raise ValueError(f'Rules validation failed:\n{formatted}')

    # Detect coordinate base once globally and use it consistently for all rows.
    coord_base = _detect_coordinate_base(all_rows, genes_by_name)
    logger.info('Detected %d-based amino acid positions in rules TSV', coord_base)
    mismatch_keys = _validate_reference_amino_acids(all_rows, genes_by_name, coord_base)

    for row_number, row in enumerate(all_rows, start=2):
        gene_name = _get_value(row, 'gene')
        reference_identifier = _get_value(row, 'reference_identifier')
        if not gene_name or gene_name not in genes_by_name:
            gene_label = gene_name or '<empty>'
            reference_label = reference_identifier or '<empty>'
            skipped_gene.append(
                f'row {row_number}: gene {gene_label!r}, reference_identifier {reference_label!r}'
            )
            continue

        gene_id = _resolve_rule_gene_id(genes_by_name[gene_name], reference_identifier)
        if gene_id is None:
            # Missing reference context can make same gene name ambiguous across records.
            candidate_refs = sorted(
                {
                    candidate['reference_accession'] or candidate['reference_name']
                    for candidate in genes_by_name[gene_name]
                }
            )
            if reference_identifier:
                skipped_ref.append(
                    f'gene {gene_name!r}: reference_identifier {reference_identifier!r} '
                    f'not found (available: {candidate_refs})'
                )
            else:
                errors.append(
                    f'Rules gene {gene_name!r} is ambiguous across references {candidate_refs}; '
                    'add reference_identifier to the rules row'
                )
            continue

        drug_name = _get_value(row, 'antiviral')
        external_id = _get_value(row, 'member_id', 'rule_id')
        group_ids = [
            value.strip()
            for value in _get_value(row, 'group_id', 'rule_group').split(',')
            if value.strip()
        ]

        if not drug_name:
            if require_external_ids and external_id:
                grouped_missing_antiviral_rows.append(
                    f'row {row_number}: gene {gene_name!r}, member_id {external_id!r}'
                )
                drug_name = _INTERNAL_FORMULA_COMPONENT_DRUG_NAME
            else:
                errors.append(f'Rule for gene {gene_name!r} has no antiviral value')
                continue

        position_raw = _get_value(row, 'position')
        mutation_raw = _get_value(row, 'mutation')
        if not position_raw or not mutation_raw:
            errors.append(f'Rule for gene {gene_name!r} is missing position or mutation')
            continue

        try:
            position_0based = int(position_raw) - coord_base
        except ValueError:
            errors.append(
                f'Rule for gene {gene_name!r} has invalid position {position_raw!r}'
            )
            continue

        reference_aa = _get_value(row, 'reference')
        if (gene_name, position_raw, reference_identifier, reference_aa) in mismatch_keys:
            if external_id:
                skipped_external_ids[external_id] = (
                    f'reference AA mismatch at {reference_identifier} {gene_name} pos {position_raw}'
                )
            continue

        # Resolve anchor-less deletion tokens (e.g. 'Q35del', 'DD676del') emitted by
        m_del = _RE_ANCHORLESS_DEL.match(mutation_raw)
        if m_del:
            deleted_block = m_del.group(1).upper()
            aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
            if not aa_seq:
                errors.append(
                    f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                    f'gene has no aa_sequence, cannot resolve deletion anchor for {mutation_raw!r}'
                )
                continue
            resolved = _resolve_anchorless_deletion(deleted_block, position_0based, aa_seq)
            if resolved is None:
                errors.append(
                    f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                    f'cannot resolve anchor for deletion {mutation_raw!r} — '
                    'check that the deleted block matches the gene sequence and '
                    'that the deletion does not start at position 1'
                )
                continue
            position_0based, reference_aa, mutation_raw = resolved

        ic50_value = _normalize_ic50_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        fold_ic50_value = _normalize_fold_ic50_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        score_value = _normalize_score_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        phenotype_value, clinical_phenotype_value = _normalize_phenotypes_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        normalized = _normalize_rule_alleles_for_storage(
            reference_aa=reference_aa,
            mutation_raw=mutation_raw,
            position_0based=position_0based,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
            errors=errors,
        )
        if normalized is None:
            continue
        position_0based, reference_aa, mutation = normalized

        if _is_noop_mutation(reference_aa, mutation):
            errors.append(
                f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                f'mutation {mutation_raw!r} does not change reference {reference_aa!r}'
            )
            continue

        if not _is_supported_mutation_token(mutation):
            skipped_invalid_aa.append(
                f'gene {gene_name!r} pos {position_raw!r}: unsupported amino-acid token '
                f'{mutation_raw!r} (normalized {mutation!r})'
            )
            continue

        # Reuse/create drug IDs through a tiny cache to avoid repeated lookups.
        drug_id = _get_or_create_drug_id(conn, project_id, drug_name, drug_cache)
        comment_value = _append_contradictory_comment(
            _get_value(row, 'comment'),
            phenotype=phenotype_value,
            clinical_phenotype=clinical_phenotype_value,
        )

        if external_id:
            signature = (gene_id, reference_identifier, position_0based, reference_aa, mutation)
            seen = seen_external_id_signatures.get(external_id)
            if seen is not None:
                first_row, first_signature = seen
                if signature == first_signature:
                    skipped_identical_member_id_rows.append(
                        f'row {row_number}: member_id {external_id!r} duplicates identical atomic '
                        f'definition from row {first_row}'
                    )
                    skipped_external_ids[external_id] = 'duplicate of an earlier identical row'
                    continue
                errors.append(
                    f'duplicate atomic rule ids: {external_id!r} '
                    f'(conflicting definitions in rows {first_row} and {row_number})'
                )
                continue
            seen_external_id_signatures[external_id] = (row_number, signature)

        # Formula component rows (no antiviral, linked via external_id) may share the same
        # mutation across multiple formula groups. Each has a unique external_id, so skip
        # mutation-level deduplication; the unique index on external_id prevents true duplicates.
        if not group_ids and _rule_exists(
            conn,
            gene_id=gene_id,
            drug_id=drug_id,
            reference_identifier=reference_identifier,
            position=position_0based,
            reference=reference_aa,
            mutation=mutation,
        ):
            skipped_duplicates += 1
            skipped_duplicates_detail.append(
                f'{reference_identifier} gene {gene_name!r} pos {position_raw} '
                f'{reference_aa!r}>{mutation!r} ({drug_name})'
            )
            if external_id:
                skipped_external_ids[external_id] = 'duplicate of an existing rule'
            continue

        conn.execute(
            'INSERT INTO resistance_rule '
            '('
            'gene_id, drug_id, external_id, reference_identifier, position, reference, mutation, '
            'phenotype, clinical_phenotype, ic50, fold_ic50, score, source, comment'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                gene_id,
                drug_id,
                external_id,
                reference_identifier,
                position_0based,
                reference_aa,
                mutation,
                phenotype_value,
                clinical_phenotype_value,
                ic50_value,
                fold_ic50_value,
                score_value,
                _get_value(row, 'source'),
                comment_value,
            ),
        )
        rule_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        raw_publication = _get_value(row, 'publication')
        if raw_publication:
            _link_rule_publications(conn, rule_id, raw_publication, additional_info, pub_cache)
        count += 1

    if skipped_gene:
        unique_rows = sorted(set(skipped_gene))
        logger.warning(
            '%d rule(s) skipped — gene(s) not found in GenBank annotations: %s\n%s',
            len(skipped_gene),
            '\n'.join(f'  - {detail}' for detail in unique_rows),
        )

    if skipped_ref:
        unique_skipped = sorted(set(skipped_ref))
        logger.warning(
            '%d rule(s) skipped — reference_identifier not in this project:\n%s',
            len(unique_skipped),
            '\n'.join(f'  - {msg}' for msg in unique_skipped),
        )

    if skipped_invalid_aa:
        unique_invalid = sorted(set(skipped_invalid_aa))
        logger.warning(
            '%d rule(s) skipped — unsupported amino-acid tokens:\n%s',
            len(unique_invalid),
            '\n'.join(f'  - {msg}' for msg in unique_invalid),
        )

    if grouped_missing_antiviral_rows:
        logger.warning(
            '%d grouped atomic rule row(s) without antiviral were imported as formula members '
            'using an internal placeholder drug entry:\n%s',
            len(grouped_missing_antiviral_rows),
            '\n'.join(f'  - {msg}' for msg in grouped_missing_antiviral_rows),
        )

    if skipped_duplicates_detail:
        logger.warning(
            '%d duplicate rule(s) skipped — existing rows were kept:\n%s',
            len(skipped_duplicates_detail),
            '\n'.join(f'  {rule}' for rule in sorted(skipped_duplicates_detail)),
        )

    if skipped_identical_member_id_rows:
        logger.warning(
            '%d row(s) skipped — duplicate member_id with identical atomic definition '
            '(first occurrence kept):\n%s',
            len(skipped_identical_member_id_rows),
            '\n'.join(f'  - {msg}' for msg in sorted(skipped_identical_member_id_rows)),
        )

    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    if grouped_ids and not require_external_ids:
        logger.warning(
            'Detected grouped atomic rules (%d group_id values), but no formula TSV was provided; '
            'combinatorial rules are ignored while atomic rules are still imported',
            len(grouped_ids),
        )

    logger.info('Loaded %d single resistance rule(s)', count)
    return count, grouped_ids, declared_external_ids, skipped_external_ids


def _load_formula_rules(
    conn: sqlite3.Connection,
    project_id: int,
    formula_rules_tsv: Path,
    expected_group_ids: set[str] | None = None,
    declared_atomic_ids: set[str] | None = None,
    skipped_atomic_ids: dict[str, str] | None = None,
    additional_info: bool = False,
) -> int:
    """Load formula rules from a second TSV and return the inserted formula-rule count."""
    drug_cache: dict[str, int] = {}
    pub_cache: dict[str, int] = {}
    errors: list[str] = []

    with open(formula_rules_tsv, newline='') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        rows = list(reader)

    formula_ids: list[str] = []
    formula_id_to_row: dict[str, int] = {}  # Track row number for each formula_id
    normalized_by_drug: dict[tuple[str, str], str] = {}
    prepared_rows: list[tuple[dict[str, str], str, list[str]]] = []
    skipped_formula_validation: list[str] = []  # Track rows skipped due to duplicates/conflicts
    for row_number, row in enumerate(rows, start=2):
        formula_id = _get_value(row, 'group_id', 'formula_id')
        drug_name = _get_value(row, 'antiviral')
        expression = _get_value(row, 'expression', 'formula')
        context = f'Formula rule row {row_number}'

        if not formula_id:
            errors.append(f'{context}: missing required field group_id')
            continue
        if not drug_name:
            errors.append(f'{context}: missing required field antiviral')
            continue
        if not expression:
            errors.append(f'{context}: missing required field expression')
            continue
        if formula_id.upper() in _FORMULA_OPERATORS:
            errors.append(f'{context}: formula_id {formula_id!r} uses a reserved boolean keyword')
            continue

        try:
            normalized_expression, referenced_ids = _parse_formula_expression(expression)
        except ValueError as exc:
            errors.append(f'{context}: invalid expression {expression!r} ({exc})')
            continue

        duplicate_refs = sorted(
            {ref_id for ref_id in referenced_ids if referenced_ids.count(ref_id) > 1}
        )
        if duplicate_refs:
            skipped_formula_validation.append(
                f'{context}: duplicate atomic rule ids in expression '
                + ', '.join(repr(ref_id) for ref_id in duplicate_refs)
            )
            continue

        key = (drug_name.lower(), normalized_expression)
        if key in normalized_by_drug:
            skipped_formula_validation.append(
                f'{context}: duplicate formula rule for drug {drug_name!r}; '
                f'matches {normalized_by_drug[key]!r} after normalization'
            )
            continue

        # Check for duplicate formula_id but only report the second and later occurrences
        if formula_id in formula_id_to_row:
            skipped_formula_validation.append(
                f'{context}: duplicate formula rule id {formula_id!r} (first occurrence at row {formula_id_to_row[formula_id]})'
            )
            continue

        normalized_by_drug[key] = formula_id
        formula_ids.append(formula_id)
        formula_id_to_row[formula_id] = row_number
        prepared_rows.append((row, normalized_expression, referenced_ids))

    # Warn about duplicate/conflict validation issues
    if skipped_formula_validation:
        logger.warning(
            '%d formula rule(s) skipped due to duplicates or conflicts:\n%s',
            len(skipped_formula_validation),
            '\n'.join(f'  - {msg}' for msg in skipped_formula_validation),
        )

    if expected_group_ids:
        provided_group_ids = set(formula_ids)
        missing_group_ids = sorted(expected_group_ids - provided_group_ids)
        if missing_group_ids:
            missing_list = ', '.join(repr(group_id) for group_id in missing_group_ids)
            logger.warning(
                'missing formula rule(s) for group id(s): %s',
                missing_list
            )

        unknown_group_ids = sorted(provided_group_ids - expected_group_ids)
        if unknown_group_ids:
            unknown_list = ', '.join(
                f'{group_id!r} (row {formula_id_to_row.get(group_id, "?")})'
                for group_id in unknown_group_ids
            )
            logger.warning(
                'formula rule(s) reference unknown atomic rule id(s) from grouped rules: %s',
                unknown_list
            )

    referenced_atomic_ids = {
        ref_id
        for _, _, referenced_ids in prepared_rows
        for ref_id in referenced_ids
    }
    rule_ids_by_external_id = _load_rule_ids_by_external_id(conn, referenced_atomic_ids)

    inserted = 0
    skipped_formula_rules: list[str] = []
    for row, normalized_expression, referenced_ids in prepared_rows:
        formula_id = _get_value(row, 'group_id', 'formula_id')
        drug_name = _get_value(row, 'antiviral')
        missing_members = sorted(ref_id for ref_id in referenced_ids if ref_id not in rule_ids_by_external_id)
        if missing_members:
            # Member rules were skipped during atomic import or are unknown; skip this formula rule.
            reasons = []
            for m in missing_members:
                if skipped_atomic_ids and m in skipped_atomic_ids:
                    reasons.append(f'{m!r} ({skipped_atomic_ids[m]})')
                elif declared_atomic_ids and m not in declared_atomic_ids:
                    reasons.append(f'{m!r} (unknown atomic rule id)')
                else:
                    reasons.append(repr(m))
            skipped_formula_rules.append(
                f'{formula_id!r}: member(s) not imported: ' + ', '.join(reasons)
            )
            continue

        phenotype_value, clinical_phenotype_value = _normalize_phenotypes_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        ic50_value = _normalize_ic50_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        fold_ic50_value = _normalize_fold_ic50_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        score_value = _normalize_score_from_row(
            row,
            errors=errors,
            context=f'Formula rule {formula_id!r}',
        )
        comment_value = _append_contradictory_comment(
            _get_value(row, 'comment'),
            phenotype=phenotype_value,
            clinical_phenotype=clinical_phenotype_value,
        )

        drug_id = _get_or_create_drug_id(conn, project_id, drug_name, drug_cache)
        formula_id_exists, normalized_exists = _formula_rule_exists(
            conn,
            formula_id=formula_id,
            drug_id=drug_id,
            normalized_expression=normalized_expression,
        )
        if formula_id_exists:
            errors.append(f'Formula rule {formula_id!r}: formula_id already exists in project')
            continue
        if normalized_exists:
            errors.append(
                f'Formula rule {formula_id!r}: duplicate normalized expression for drug {drug_name!r}'
            )
            continue

        cur = conn.execute(
            'INSERT INTO resistance_formula_rule '
            '('
            'drug_id, formula_id, label, normalized_expression, phenotype, '
            'clinical_phenotype, ic50, fold_ic50, score, source, comment'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                drug_id,
                formula_id,
                _get_value(row, 'label'),
                normalized_expression,
                phenotype_value,
                clinical_phenotype_value,
                ic50_value,
                fold_ic50_value,
                score_value,
                _get_value(row, 'source'),
                comment_value,
            ),
        )
        formula_rule_id = int(cur.lastrowid)

        for ref_id in sorted(referenced_ids):
            conn.execute(
                'INSERT INTO resistance_formula_rule_member (formula_rule_id, rule_id) VALUES (?, ?)',
                (formula_rule_id, rule_ids_by_external_id[ref_id]),
            )

        raw_publication = _get_value(row, 'publication')
        if raw_publication:
            _link_formula_rule_publications(
                conn,
                formula_rule_id,
                raw_publication,
                additional_info,
                pub_cache,
            )
        inserted += 1

    if skipped_formula_rules:
        logger.warning(
            '%d formula rule(s) skipped — one or more member rules were not imported:\n%s',
            len(skipped_formula_rules),
            '\n'.join(f'  - {msg}' for msg in skipped_formula_rules),
        )

    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    logger.info('Loaded %d formula resistance rule(s)', inserted)
    return inserted

