"""
Resistance rule loading — TSV parsing, validation, coordinate detection, and combo rule sets.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from pathlib import Path

from respro.core.annotation import normalize_mutation
from respro.db.project.drugs import _get_or_create_drug_id
from respro.io.publications import fetch_publication_metadata, fetch_pubmed_metadata

logger = logging.getLogger(__name__)

# Detects anchor-less deletion tokens emitted by companion database converters.
# Form: one or more AA letters + position digits + "del" (case-insensitive), e.g. "Q35del", "DD676del".
_RE_ANCHORLESS_DEL = re.compile(r'^([A-Za-z]+)\d+del$', re.IGNORECASE)
_RE_REWRITE_TOKEN = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$')
_AUTO_SPLIT_GROUP_PREFIX = '__auto_anchor_split_row_'


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

    for prefix in ('https://doi.org/', 'http://doi.org/'):
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


def _link_rule_set_publications(
    conn: sqlite3.Connection,
    rule_set_id: int,
    raw_publications: list[str],
    additional_info: bool,
    pub_cache: dict[str, int],
) -> None:
    """Parse, resolve, and link all publications from a combo group to a rule set."""
    for raw in raw_publications:
        for doi, pubmed_id, raw_input in _parse_publication_entries(raw):
            pub_id = _get_or_create_publication(
                conn, doi, pubmed_id, raw_input, additional_info, pub_cache,
            )
            conn.execute(
                'INSERT OR IGNORE INTO rule_set_publication (rule_set_id, publication_id) VALUES (?, ?)',
                (rule_set_id, pub_id),
            )


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
    if token in {'FSX', '*'}:
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
            mutation = 'fsX'
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
    Expand mixed anchor-change indel rows into explicit two-member combo rows.

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
        group_name = _get_value(row, 'rule_group') or f'{_AUTO_SPLIT_GROUP_PREFIX}{row_number}'

        substitution_row = dict(row)
        substitution_row['reference'] = sub_ref
        substitution_row['mutation'] = sub_mut
        substitution_row['rule_group'] = group_name

        indel_row = dict(row)
        indel_row['reference'] = indel_ref
        indel_row['mutation'] = indel_mut
        indel_row['rule_group'] = group_name

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


def _rule_set_exists(conn: sqlite3.Connection, *, drug_id: int, group_name: str) -> bool:
    """Return True when a combination rule set with the same drug and group label already exists."""
    row = conn.execute(
        'SELECT id FROM resistance_rule_set WHERE drug_id = ? AND group_name = ? LIMIT 1',
        (drug_id, group_name),
    ).fetchone()
    return row is not None


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


def _insert_combo_rule_sets(
    conn: sqlite3.Connection,
    project_id: int,
    combo_rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
    coord_base: int,
    drug_cache: dict[str, int],
    pub_cache: dict[str, int],
    additional_info: bool,
    errors: list[str],
    skipped_gene: list[str],
    skipped_ref: list[str],
    skipped_invalid_aa: list[str],
    mismatch_keys: set[tuple[str, str, str, str]],
) -> int:
    """
    Parse combination rule rows (those with a non-empty ``rule_group`` column) and
    insert validated rule sets into ``resistance_rule_set`` and
    ``resistance_rule_set_member``.

    Each unique ``rule_group`` value defines one rule set.  All rows in a group
    must agree on ``antiviral`` and normalized phenotype. At least two valid
    member mutations are required per group.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param combo_rows: rows from the TSV that carry a non-empty ``rule_group``
    :param genes_by_name: gene lookup built from the project DB
    :param coord_base: detected coordinate base (0 or 1)
    :param drug_cache: shared drug-name → drug-id cache
    :param errors: list accumulating fatal validation errors
    :param skipped_gene: list accumulating skipped gene names (non-fatal)
    :param skipped_ref: list accumulating skipped reference warnings (non-fatal)
    :param skipped_invalid_aa: list accumulating skipped unsupported AA token rows
    :param mismatch_keys: set of (gene_name, position_raw, reference_identifier, ref_aa)
        tuples for rows with reference AA mismatches; matching members are skipped
    :return: number of combination rule sets successfully inserted
    """
    # Group rows by rule_group value (preserves insertion order in Python ≥ 3.7).
    # A row may carry a comma-separated list of group labels, assigning it to each group.
    groups: dict[str, list[dict]] = {}
    for row in combo_rows:
        for group_id in [g.strip() for g in _get_value(row, 'rule_group').split(',') if g.strip()]:
            groups.setdefault(group_id, []).append(row)

    count = 0
    for group_id, rows in groups.items():
        # --- set-level metadata validation ---

        drug_names = {_get_value(row, 'antiviral') for row in rows} - {''}
        if not drug_names:
            errors.append(f'Combo rule group {group_id!r}: no antiviral value found')
            continue
        if len(drug_names) > 1:
            errors.append(
                f'Combo rule group {group_id!r}: inconsistent antiviral values '
                f'{sorted(drug_names)} — all rows in a group must name the same drug'
            )
            continue
        drug_name = next(iter(drug_names))

        phenotype_values: set[str] = set()
        clinical_phenotype_values: set[str] = set()
        phenotype_error = False
        for combo_row in rows:
            normalized, normalized_clinical = _normalize_phenotypes_from_row(
                combo_row,
                errors=errors,
                context=f'Combo rule group {group_id!r}',
            )
            if normalized != 'unknown':
                phenotype_values.add(normalized)
            if normalized_clinical != 'unknown':
                clinical_phenotype_values.add(normalized_clinical)
        if len(phenotype_values) > 1:
            errors.append(
                f'Combo rule group {group_id!r}: inconsistent phenotype values '
                f'{sorted(phenotype_values)} — all rows in a group must have the same phenotype'
            )
            phenotype_error = True
        phenotype = next(iter(phenotype_values), 'unknown')

        if len(clinical_phenotype_values) > 1:
            errors.append(
                f'Combo rule group {group_id!r}: inconsistent clinical_phenotype values '
                f'{sorted(clinical_phenotype_values)} — all rows in a group must have the same clinical_phenotype'
            )
            phenotype_error = True
        clinical_phenotype = next(iter(clinical_phenotype_values), 'unknown')

        if phenotype_error:
            continue

        # ic50 and fold_ic50: keep the highest numeric value across members in the same group.
        ic50_values: list[float] = []
        fold_ic50_values: list[float] = []
        for combo_row in rows:
            raw_ic50 = _get_value(combo_row, 'ic50', 'ic_50')
            if raw_ic50 and raw_ic50.lower() != 'none':
                parsed = _parse_ic50_value(raw_ic50)
                if parsed is None:
                    errors.append(f'Combo rule group {group_id!r}: invalid ic50 value {raw_ic50!r}')
                else:
                    ic50_values.append(parsed)
            raw_fold = _get_value(combo_row, 'fold_ic50', 'fold_ic_50')
            if raw_fold and raw_fold.lower() != 'none':
                parsed = _parse_ic50_value(raw_fold)
                if parsed is None:
                    errors.append(f'Combo rule group {group_id!r}: invalid fold_ic50 value {raw_fold!r}')
                else:
                    fold_ic50_values.append(parsed)
        ic50 = f'{max(ic50_values):g}' if ic50_values else ''
        fold_ic50 = f'{max(fold_ic50_values):g}' if fold_ic50_values else ''

        # publication: union of all non-empty publication strings across the group.
        all_publication_raws = [
            _get_value(r, 'publication') for r in rows if _get_value(r, 'publication')
        ]
        source = next((_get_value(r, 'source') for r in rows if _get_value(r, 'source')), '')
        comment = next((_get_value(r, 'comment') for r in rows if _get_value(r, 'comment')), '')

        # --- per-member validation (pre-validate before any DB write) ---
        valid_members: list[tuple] = []
        seen_member_signatures: set[tuple[int, int, str]] = set()
        group_ok = True
        for row in rows:
            gene_name = _get_value(row, 'gene')
            if not gene_name or gene_name not in genes_by_name:
                skipped_gene.append(gene_name or '<empty>')
                group_ok = False
                continue

            reference_identifier = _get_value(row, 'reference_identifier')
            gene_id = _resolve_rule_gene_id(genes_by_name[gene_name], reference_identifier)
            if gene_id is None:
                candidate_refs = sorted(
                    {c['reference_accession'] or c['reference_name'] for c in genes_by_name[gene_name]}
                )
                if reference_identifier:
                    skipped_ref.append(
                        f'combo group {group_id!r} gene {gene_name!r}: '
                        f'reference_identifier {reference_identifier!r} not found '
                        f'(available: {candidate_refs})'
                    )
                else:
                    errors.append(
                        f'Combo rule group {group_id!r}: gene {gene_name!r} is ambiguous '
                        f'across references {candidate_refs}; add reference_identifier'
                    )
                group_ok = False
                continue

            position_raw = _get_value(row, 'position')
            mutation_raw = _get_value(row, 'mutation')
            if not position_raw or not mutation_raw:
                errors.append(
                    f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                    'is missing position or mutation'
                )
                group_ok = False
                continue

            try:
                position_0based = int(position_raw) - coord_base
            except ValueError:
                errors.append(
                    f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                    f'has invalid position {position_raw!r}'
                )
                group_ok = False
                continue

            reference_aa = _get_value(row, 'reference')
            if (gene_name, position_raw, reference_identifier, reference_aa) in mismatch_keys:
                group_ok = False
                continue

            # Resolve anchor-less deletion tokens — same logic as for single rules.
            m_del = _RE_ANCHORLESS_DEL.match(mutation_raw)
            if m_del:
                deleted_block = m_del.group(1).upper()
                aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
                if not aa_seq:
                    errors.append(
                        f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                        f'pos {position_raw!r} has no aa_sequence, cannot resolve deletion '
                        f'anchor for {mutation_raw!r}'
                    )
                    group_ok = False
                    continue
                resolved = _resolve_anchorless_deletion(deleted_block, position_0based, aa_seq)
                if resolved is None:
                    errors.append(
                        f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                        f'pos {position_raw!r}: cannot resolve anchor for deletion '
                        f'{mutation_raw!r}'
                    )
                    group_ok = False
                    continue
                position_0based, reference_aa, mutation_raw = resolved

            normalized = _normalize_rule_alleles_for_storage(
                reference_aa=reference_aa,
                mutation_raw=mutation_raw,
                position_0based=position_0based,
                context=(
                    f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                    f'pos {position_raw!r}'
                ),
                errors=errors,
            )
            if normalized is None:
                group_ok = False
                continue
            position_0based, reference_aa, mutation = normalized

            if _is_noop_mutation(reference_aa, mutation):
                errors.append(
                    f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                    f'pos {position_raw!r} does not change reference {reference_aa!r}'
                )
                group_ok = False
                continue

            if not _is_supported_mutation_token(mutation):
                skipped_invalid_aa.append(
                    f'combo group {group_id!r} gene {gene_name!r} pos {position_raw!r}: '
                    f'unsupported amino-acid token {mutation_raw!r} (normalized {mutation!r})'
                )
                group_ok = False
                continue

            member_signature = (gene_id, position_0based, mutation)
            if member_signature in seen_member_signatures:
                errors.append(
                    f'Combo rule group {group_id!r}: duplicate member '
                    f'gene {gene_name!r} pos {position_raw!r} mutation {mutation!r}'
                )
                group_ok = False
                continue

            seen_member_signatures.add(member_signature)

            valid_members.append((gene_id, reference_identifier, position_0based, reference_aa, mutation))

        if not group_ok:
            # Non-fatal member issues were already appended; skip this group.
            continue

        if len(valid_members) < 2:
            errors.append(
                f'Combo rule group {group_id!r}: only {len(valid_members)} valid member(s) — '
                'combination rules require at least 2 member mutations'
            )
            continue

        drug_id = _get_or_create_drug_id(conn, project_id, drug_name, drug_cache)

        if _rule_set_exists(conn, drug_id=drug_id, group_name=group_id):
            logger.debug('Combo rule group %r already loaded — skipped', group_id)
            continue

        cur = conn.execute(
            'INSERT INTO resistance_rule_set '
            '(drug_id, phenotype, clinical_phenotype, ic50, fold_ic50, source, group_name, comment) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (drug_id, phenotype, clinical_phenotype, ic50, fold_ic50, source, group_id, comment),
        )
        rule_set_id = cur.lastrowid

        for gene_id, reference_identifier, position_0based, reference_aa, mutation in valid_members:
            conn.execute(
                'INSERT INTO resistance_rule_set_member '
                '(rule_set_id, gene_id, reference_identifier, position, reference, mutation) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (rule_set_id, gene_id, reference_identifier, position_0based, reference_aa, mutation),
            )

        _link_rule_set_publications(conn, rule_set_id, all_publication_raws, additional_info, pub_cache)

        count += 1

    return count


def _load_resistance_rules(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    additional_info: bool = False,
) -> int:
    """
    Load resistance rules from TSV file; return count of inserted rules.

    Rows with an empty ``rule_group`` column (or no such column) are imported as
    single resistance rules into ``resistance_rule``.  Rows with a non-empty
    ``rule_group`` value are grouped and imported as combination rule sets into
    ``resistance_rule_set`` / ``resistance_rule_set_member``.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param rules_tsv: path to resistance rules TSV file
    :return: number of single rules inserted (not counting combo rule sets)
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
    for row_number, row in enumerate(all_rows, start=2):
        if not _get_value(row, 'reference_identifier'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference_identifier'
            )
        if not _get_value(row, 'reference'):
            required_field_errors.append(
                f'row {row_number}: missing required field reference'
            )

    if required_field_errors:
        formatted = '\n'.join(f'- {message}' for message in required_field_errors)
        raise ValueError(f'Rules validation failed:\n{formatted}')

    # Detect coordinate base once globally and use it consistently for all rows.
    coord_base = _detect_coordinate_base(all_rows, genes_by_name)
    logger.info('Detected %d-based amino acid positions in rules TSV', coord_base)
    mismatch_keys = _validate_reference_amino_acids(all_rows, genes_by_name, coord_base)

    # Split rows into single rules and combination rule members.
    single_rows = [r for r in all_rows if not _get_value(r, 'rule_group')]
    combo_rows = [r for r in all_rows if _get_value(r, 'rule_group')]

    for row in single_rows:
        gene_name = _get_value(row, 'gene')
        if not gene_name or gene_name not in genes_by_name:
            skipped_gene.append(gene_name or '<empty>')
            continue

        reference_identifier = _get_value(row, 'reference_identifier')
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
        if not drug_name:
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

        if _rule_exists(
            conn,
            gene_id=gene_id,
            drug_id=drug_id,
            reference_identifier=reference_identifier,
            position=position_0based,
            reference=reference_aa,
            mutation=mutation,
        ):
            skipped_duplicates += 1
            continue

        conn.execute(
            'INSERT INTO resistance_rule '
            '('
            'gene_id, drug_id, reference_identifier, position, reference, mutation, '
            'phenotype, clinical_phenotype, ic50, fold_ic50, source, comment'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                gene_id,
                drug_id,
                reference_identifier,
                position_0based,
                reference_aa,
                mutation,
                phenotype_value,
                clinical_phenotype_value,
                ic50_value,
                fold_ic50_value,
                _get_value(row, 'source'),
                _get_value(row, 'comment'),
            ),
        )
        rule_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        raw_publication = _get_value(row, 'publication')
        if raw_publication:
            _link_rule_publications(conn, rule_id, raw_publication, additional_info, pub_cache)
        count += 1

    combo_count = _insert_combo_rule_sets(
        conn, project_id, combo_rows, genes_by_name, coord_base,
        drug_cache, pub_cache, additional_info, errors, skipped_gene, skipped_ref, skipped_invalid_aa,
        mismatch_keys,
    )

    if skipped_gene:
        unique_genes = sorted(set(skipped_gene))
        logger.warning(
            '%d rule(s) skipped — gene(s) not found in GenBank annotations: %s',
            len(skipped_gene),
            ', '.join(repr(g) for g in unique_genes),
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

    if skipped_duplicates:
        logger.warning(
            '%d duplicate rule(s) skipped — existing rows were kept',
            skipped_duplicates,
        )

    if errors:
        formatted = '\n'.join(f'- {message}' for message in sorted(set(errors)))
        raise ValueError(f'Rules validation failed:\n{formatted}')

    logger.info('Loaded %d single resistance rule(s), %d combination rule set(s)', count, combo_count)
    return count

