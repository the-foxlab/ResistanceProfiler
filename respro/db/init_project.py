"""
Project initialization — populate a fresh or existing SQLite database.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from pathlib import Path

from respro.core.annotation import normalize_mutation
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema
from respro.db.schema import open_project_db
from respro.io.genbank import ParsedGenBankGene, ParsedGenBankReference, parse_genbank_sources
from respro.utils import require_file, validate_strand

logger = logging.getLogger(__name__)


def init_project(
    *,
    db_path: Path,
    name: str,
    genbank_paths: list[Path],
    rules_tsv: Path,
    overwrite: bool = False,
    drug_info: bool = True,
) -> Path:
    """
    Create and populate a new project database.

    :param db_path: where to write the SQLite file
    :param name: project name
    :param genbank_paths: one or more GenBank file paths; each file may contain
        one or more records and CDS features are imported as genes with
        GenBank-derived identifiers
    :param rules_tsv: tab-separated file with columns: gene, position, mutation,
        antiviral, reference_identifier, reference; optional columns:
        phenotype and/or clinical_phenotype, ic50, publication, source
    :param overwrite: if True, delete an existing database at db_path before
        creating a fresh one; if False (default), raise FileExistsError
    :param drug_info: if True (default), query PubChem to attach CID, URL, and a
        short description to each drug; failures are non-fatal and the project
        is still created without drug information
    :return: path to the created database
    """
    if not genbank_paths:
        raise ValueError('At least one GenBank file must be provided')

    # Validate all declared input files up front so init fails early and clearly.
    for genbank_path in genbank_paths:
        require_file(genbank_path, 'GenBank file')
    require_file(rules_tsv, 'Rules TSV')

    genbank_records = parse_genbank_sources(genbank_paths)

    if db_path.exists():
        if not overwrite:
            raise FileExistsError(f'Database already exists: {db_path}')
        db_path.unlink()
        logger.info('Removed existing database: %s', db_path)

    conn = create_schema(db_path)
    try:
        # Load curated references/genes first, then validate and import rules.
        project_id = _insert_project(conn, name)
        _load_genbank_records(conn, project_id, genbank_records)
        _load_resistance_rules(conn, project_id, rules_tsv)
        if drug_info:
            _get_drugs_from_pubchem(conn, project_id)
        conn.commit()
        logger.info('Project initialized: %s (%s)', name, db_path)
    except Exception:
        db_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    return db_path


def add_to_project(
    *,
    db_path: Path,
    rules_tsv: Path,
    genbank_paths: list[Path] | None = None,
    drug_info: bool = True,
) -> Path:
    """
    Add curated rules and optional GenBank annotations to an existing project.

    :param db_path: existing project database path
    :param rules_tsv: tab-separated rules file to add
    :param genbank_paths: optional GenBank files with additional references/genes;
        if omitted, the existing DB annotations are used for rule validation
    :param drug_info: if True, query PubChem for newly seen drugs
    :return: path to the updated database
    """
    require_file(db_path, 'Project database')
    require_file(rules_tsv, 'Rules TSV')

    records: list[ParsedGenBankReference] = []
    for genbank_path in genbank_paths or []:
        require_file(genbank_path, 'GenBank file')
    if genbank_paths:
        records = parse_genbank_sources(genbank_paths)

    conn = open_project_db(db_path)
    try:
        project_id = _get_existing_project_id(conn)
        _ensure_project_has_reference_annotations(conn)
        _consolidate_drug_names_to_lowercase(conn, project_id)
        if records:
            _load_genbank_records(conn, project_id, records)
        _load_resistance_rules(conn, project_id, rules_tsv)
        if drug_info:
            _get_drugs_from_pubchem(conn, project_id)
        conn.commit()
        logger.info('Project updated: %s', db_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return db_path


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _insert_project(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        'INSERT INTO project (name, schema_version) VALUES (?, ?)',
        (name, PROJECT_SCHEMA_VERSION),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _get_existing_project_id(conn: sqlite3.Connection) -> int:
    """Return the existing project id from an initialized database."""
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT id FROM project ORDER BY id LIMIT 1').fetchone()
    if row is None:
        raise ValueError('Existing database has no project row')
    return int(row['id'])


def _ensure_project_has_reference_annotations(conn: sqlite3.Connection) -> None:
    """Fail early when an existing DB lacks the stored references/genes needed for rule loading."""
    reference_count = conn.execute('SELECT COUNT(*) FROM reference').fetchone()[0]
    gene_count = conn.execute('SELECT COUNT(*) FROM gene').fetchone()[0]
    if reference_count == 0 or gene_count == 0:
        raise ValueError(
            'Existing database has no stored references/genes. '
            'Provide --genbank or rebuild the project with respro init.'
        )


def _load_genbank_records(
    conn: sqlite3.Connection,
    project_id: int,
    records: list[ParsedGenBankReference],
) -> None:
    """
    Load references and CDS/gene annotations from parsed GenBank records.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param records: list of ParsedGenBankReference objects
    """
    inserted_refs = 0
    reused_refs = 0
    inserted_genes = 0
    reused_genes = 0
    ncbi_protein_url_cache: dict[str, str] = {}

    for record in records:
        reference_id, created_ref = _get_or_create_reference_id(conn, project_id, record)
        if created_ref:
            inserted_refs += 1
        else:
            reused_refs += 1

        for gene in record.genes:
            created_gene = _get_or_create_gene(
                conn,
                reference_id,
                gene,
                ncbi_protein_url_cache=ncbi_protein_url_cache,
            )
            if created_gene:
                inserted_genes += 1
            else:
                reused_genes += 1

    logger.info(
        'Loaded GenBank data: references +%d (reused %d), genes +%d (reused %d)',
        inserted_refs,
        reused_refs,
        inserted_genes,
        reused_genes,
    )


def _load_resistance_rules(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
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
        all_rows = list(reader)

    header_columns = {col.strip() for col in (reader.fieldnames or []) if col}
    ic50_aliases = {'ic50', 'ic_50', 'fold_ic50'}
    present_ic50_aliases = sorted(header_columns & ic50_aliases)
    if len(present_ic50_aliases) > 1:
        raise ValueError(
            'Rules validation failed:\n'
            '- only one IC50 column is allowed; found '
            + ', '.join(repr(col) for col in present_ic50_aliases)
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
    _validate_reference_amino_acids(all_rows, genes_by_name, coord_base)

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
        ic50_value = _normalize_ic50_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        phenotype_value, clinical_phenotype_value = _normalize_phenotypes_from_row(
            row,
            errors=errors,
            context=f'Rule for gene {gene_name!r} pos {position_raw!r}',
        )
        mutation = normalize_mutation(
            mutation_raw,
            reference=reference_aa,
            position_1based=position_0based + 1,
        )
        if mutation is None:
            errors.append(
                f'Rule for gene {gene_name!r} pos {position_raw!r}: '
                f'unrecognised mutation {mutation_raw!r}'
            )
            continue

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
            'phenotype, clinical_phenotype, ic50, publication, source'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
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
                _normalize_publication_value(_get_value(row, 'publication')),
                _get_value(row, 'source'),
            ),
        )
        count += 1

    combo_count = _insert_combo_rule_sets(
        conn, project_id, combo_rows, genes_by_name, coord_base,
        drug_cache, errors, skipped_gene, skipped_ref, skipped_invalid_aa,
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


def _get_or_create_reference_id(
    conn: sqlite3.Connection,
    project_id: int,
    record: ParsedGenBankReference,
) -> tuple[int, bool]:
    """Insert a reference or reuse an existing compatible one."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT id, accession, organism, taxonomy, length '
        'FROM reference WHERE project_id = ? AND name = ?',
        (project_id, record.name),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            'INSERT INTO reference '
            '(project_id, name, accession, organism, taxonomy, length) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                project_id,
                record.name,
                record.accession,
                record.organism,
                record.taxonomy,
                record.length,
            ),
        )
        return int(cur.lastrowid), True

    if int(existing['length']) != record.length:
        raise ValueError(
            f'Reference {record.name!r} already exists with a different length; '
            'refusing to append incompatible data'
        )

    if record.accession and existing['accession'] and existing['accession'] != record.accession:
        raise ValueError(
            f'Reference {record.name!r} already exists with accession '
            f"{existing['accession']!r}, incoming accession is {record.accession!r}"
        )

    return int(existing['id']), False


def _get_or_create_gene(
    conn: sqlite3.Connection,
    reference_id: int,
    gene: ParsedGenBankGene,
    *,
    ncbi_protein_url_cache: dict[str, str],
) -> bool:
    """Insert a gene row or validate that an existing one is compatible."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        'SELECT start, end, strand, codon_start, nt_sequence, aa_sequence, protein, '
        'protein_id, ncbi_protein_url, locus_tag, note '
        'FROM gene WHERE reference_id = ? AND name = ?',
        (reference_id, gene.gene_name),
    ).fetchone()

    strand = validate_strand(gene.strand)
    ncbi_protein_url = _resolve_ncbi_protein_url(gene.protein_id, ncbi_protein_url_cache)
    if existing is None:
        conn.execute(
            'INSERT INTO gene '
            '(reference_id, name, protein, protein_id, ncbi_protein_url, locus_tag, note, '
            'start, end, strand, codon_start, nt_sequence, aa_sequence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                reference_id,
                gene.gene_name,
                gene.protein,
                gene.protein_id,
                ncbi_protein_url,
                gene.locus_tag,
                gene.note,
                gene.start,
                gene.end,
                strand,
                gene.codon_start,
                gene.nt_sequence,
                gene.aa_sequence,
            ),
        )
        return True

    same_gene = (
        int(existing['start']) == gene.start
        and int(existing['end']) == gene.end
        and existing['strand'] == strand
        and int(existing['codon_start']) == gene.codon_start
        and (existing['nt_sequence'] or '') == gene.nt_sequence
        and (existing['aa_sequence'] or '') == gene.aa_sequence
    )
    if not same_gene:
        raise ValueError(
            f'Gene {gene.gene_name!r} already exists for this reference with different '
            'coordinates/sequence; refusing to append incompatible data'
        )

    update_needed = False
    if not (existing['protein'] or '').strip() and gene.protein:
        update_needed = True
    if not (existing['protein_id'] or '').strip() and gene.protein_id:
        update_needed = True
    if not (existing['ncbi_protein_url'] or '').strip() and ncbi_protein_url:
        update_needed = True
    if not (existing['locus_tag'] or '').strip() and gene.locus_tag:
        update_needed = True
    if not (existing['note'] or '').strip() and gene.note:
        update_needed = True

    if update_needed:
        conn.execute(
            'UPDATE gene SET protein = ?, protein_id = ?, ncbi_protein_url = ?, locus_tag = ?, note = ? '
            'WHERE reference_id = ? AND name = ?',
            (
                (existing['protein'] or '').strip() or gene.protein,
                (existing['protein_id'] or '').strip() or gene.protein_id,
                (existing['ncbi_protein_url'] or '').strip() or ncbi_protein_url,
                (existing['locus_tag'] or '').strip() or gene.locus_tag,
                (existing['note'] or '').strip() or gene.note,
                reference_id,
                gene.gene_name,
            ),
        )

    return False


def _resolve_ncbi_protein_url(
    protein_id: str,
    cache: dict[str, str],
) -> str:
    """Return a canonical NCBI protein URL from protein_id when accession looks valid."""
    token = protein_id.strip()
    if not token:
        return ''
    if token in cache:
        return cache[token]

    if not _is_ncbi_protein_accession(token):
        logger.debug('NCBI protein URL skipped for non-standard protein_id %r', token)
        cache[token] = ''
        return ''

    url = f'https://www.ncbi.nlm.nih.gov/protein/{token}/'
    cache[token] = url
    return url


def _is_ncbi_protein_accession(value: str) -> bool:
    """Return True for common NCBI protein accession formats with version suffix."""
    token = value.strip().upper()
    if not token:
        return False

    patterns = (
        r'^[A-Z]{3}[0-9]{5}\.[0-9]+$',      # e.g. AAA12345.1
        r'^[A-Z]{2}_[0-9]{6,9}\.[0-9]+$',   # e.g. YP_009137097.1, NP_123456.2
        r'^[A-Z]{4}[0-9]{8,10}\.[0-9]+$',   # e.g. KAFS00000001.1
    )
    return any(re.fullmatch(pattern, token) is not None for pattern in patterns)


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


def _normalize_ic50_from_row(
    row: dict[str, str],
    *,
    errors: list[str],
    context: str,
) -> str:
    """Return canonical IC50 text or empty string for missing values."""
    raw_ic50 = _get_value(row, 'ic50', 'ic_50', 'fold_ic50')
    if not raw_ic50 or raw_ic50.lower() == 'none':
        return ''

    ic50_numeric = _parse_ic50_value(raw_ic50)
    if ic50_numeric is None:
        errors.append(f'{context}: invalid ic50 value {raw_ic50!r}')
        return ''

    return f'{ic50_numeric:g}'


def _normalize_publication_value(raw: str) -> str:
    """Normalize DOI publication links to absolute HTTPS URLs."""
    value = raw.strip()
    if not value:
        return ''

    lowered = value.lower()
    if lowered.startswith('http://') or lowered.startswith('https://'):
        return value
    if lowered.startswith('doi.org/') or lowered.startswith('dx.doi.org/'):
        return f'https://{value}'
    return value


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

    phenotype_value = (_normalize_phenotype_token(phenotype_raw) or 'unknown') if phenotype_raw else 'unknown'
    clinical_value = (_normalize_phenotype_token(clinical_raw) or 'unknown') if clinical_raw else 'unknown'

    if phenotype_raw and _normalize_phenotype_token(phenotype_raw) is None:
        errors.append(f'{context}: invalid phenotype value {phenotype_raw!r}')
        phenotype_value = 'unknown'
    if clinical_raw and _normalize_phenotype_token(clinical_raw) is None:
        errors.append(f'{context}: invalid clinical_phenotype value {clinical_raw!r}')
        clinical_value = 'unknown'

    return phenotype_value, clinical_value


def _is_noop_mutation(reference_aa: str, mutation: str) -> bool:
    """Return True when a rule encodes no amino-acid change."""
    return len(reference_aa) == 1 and len(mutation) == 1 and reference_aa.upper() == mutation.upper()


def _is_supported_mutation_token(mutation: str) -> bool:
    """Return True when a normalized mutation token uses supported AA letters."""

    aa_letters = frozenset('ACDEFGHIKLMNPQRSTVWY')

    token = mutation.upper()
    if token in {'FSX', '*', 'ANY'}:
        return True
    if len(token) == 1:
        return token in aa_letters

    match = re.fullmatch(r'([A-Z]+)(\d+)([A-Z]+)', token)
    if match is None:
        return False

    left, _, right = match.groups()
    return set(left) <= aa_letters and set(right) <= aa_letters


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

        reference_identifier = _get_value(
            row, 'reference_identifier'
        )
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError:
            continue

        verifiable += 1
        if 1 <= pos <= len(aa_seq) and aa_seq[pos - 1].upper() == ref_aa.upper():
            matches_1based += 1
        if 0 <= pos < len(aa_seq) and aa_seq[pos].upper() == ref_aa.upper():
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
) -> None:
    """
    Validate that every rule's reference AA matches the gene aa_sequence.

    Out-of-range positions are logged as warnings and skipped — they indicate
    that a rule refers to a position beyond the end of the annotated protein
    (e.g. a database entry for a truncated or divergent isoform) but do not
    imply an inconsistency between the rules file and the GenBank sequence.

    An actual AA mismatch (rule says ``M``, gene has ``K``) is fatal because
    it means the rules file and the GenBank reference are genuinely inconsistent
    and loading the rules would silently produce wrong results.

    :param rows: all parsed rows from the rules TSV
    :param genes_by_name: gene lookup with aa_sequence from the project DB
    :param coord_base: detected coordinate base (0 or 1)
    :raises ValueError: if any reference AA mismatches are found
    """
    mismatches: list[str] = []
    out_of_range: list[str] = []

    for row in rows:
        # Skip rows that cannot be validated against a concrete AA sequence.
        gene_name = _get_value(row, 'gene')
        ref_aa = _get_value(row, 'reference')
        position_raw = _get_value(row, 'position')

        if not ref_aa or not position_raw or gene_name not in genes_by_name:
            continue

        reference_identifier = _get_value(
            row, 'reference_identifier'
        )
        aa_seq = _get_gene_aa_sequence(genes_by_name[gene_name], reference_identifier)
        if not aa_seq:
            continue

        try:
            pos = int(position_raw)
        except ValueError:
            continue

        pos_0based = pos - coord_base
        if 0 <= pos_0based < len(aa_seq):
            actual = aa_seq[pos_0based].upper()
            if actual != ref_aa.upper():
                mismatches.append(
                    f'  gene {gene_name!r} pos {pos} ({coord_base}-based): '
                    f'rule says {ref_aa!r}, gene sequence has {actual!r}'
                )
        else:
            out_of_range.append(
                f'  gene {gene_name!r} pos {pos} ({coord_base}-based): '
                f'out of range (aa_sequence length = {len(aa_seq)}) — rule will be skipped'
            )

    if out_of_range:
        # Out-of-range is non-fatal: the row is ignored later, but init can continue.
        logger.warning(
            '%d rule(s) reference positions beyond the end of the annotated protein '
            'and will be skipped:\n%s',
            len(out_of_range),
            '\n'.join(out_of_range),
        )

    if mismatches:
        # True AA mismatches are fatal: they indicate inconsistent biological references.
        raise ValueError(
            f'Rules reference AA validation failed — {len(mismatches)} mismatch(es) '
            f'between rules TSV and GenBank gene sequences. '
            'This must be fixed before the project can be initialised:\n'
            + '\n'.join(mismatches)
        )


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


def _insert_combo_rule_sets(
    conn: sqlite3.Connection,
    project_id: int,
    combo_rows: list[dict],
    genes_by_name: dict[str, list[sqlite3.Row]],
    coord_base: int,
    drug_cache: dict[str, int],
    errors: list[str],
    skipped_gene: list[str],
    skipped_ref: list[str],
    skipped_invalid_aa: list[str],
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
    :return: number of combination rule sets successfully inserted
    """
    # Group rows by rule_group value (preserves insertion order in Python ≥ 3.7).
    groups: dict[str, list[dict]] = {}
    for row in combo_rows:
        group_id = _get_value(row, 'rule_group')
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
            if normalized == 'unknown':
                pass
            else:
                phenotype_values.add(normalized)
            if normalized_clinical == 'unknown':
                pass
            else:
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

        # ic50: keep the highest numeric value across members in the same group.
        ic50_values: list[float] = []
        for combo_row in rows:
            raw_ic50 = _get_value(combo_row, 'ic50', 'ic_50', 'fold_ic50')
            if not raw_ic50 or raw_ic50.lower() == 'none':
                continue
            parsed_ic50 = _parse_ic50_value(raw_ic50)
            if parsed_ic50 is None:
                errors.append(f'Combo rule group {group_id!r}: invalid ic50 value {raw_ic50!r}')
                continue
            ic50_values.append(parsed_ic50)
        ic50 = f'{max(ic50_values):g}' if ic50_values else ''

        # publication, source: first non-empty value wins.
        publication = next((_get_value(r, 'publication') for r in rows if _get_value(r, 'publication')), '')
        publication = _normalize_publication_value(publication)
        source = next((_get_value(r, 'source') for r in rows if _get_value(r, 'source')), '')

        # --- per-member validation (pre-validate before any DB write) ---
        valid_members: list[tuple] = []
        group_ok = True
        for row in rows:
            gene_name = _get_value(row, 'gene')
            if not gene_name or gene_name not in genes_by_name:
                skipped_gene.append(gene_name or '<empty>')
                group_ok = False
                continue

            reference_identifier = _get_value(
                row, 'reference_identifier'
            )
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
            mutation = normalize_mutation(
                mutation_raw,
                reference=reference_aa,
                position_1based=position_0based + 1,
            )
            if mutation is None:
                errors.append(
                    f'Combo rule group {group_id!r}: member for gene {gene_name!r} '
                    f'pos {position_raw!r} has unrecognised mutation {mutation_raw!r}'
                )
                group_ok = False
                continue

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
            '(drug_id, phenotype, clinical_phenotype, ic50, publication, source, group_name) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (drug_id, phenotype, clinical_phenotype, ic50, publication, source, group_id),
        )
        rule_set_id = cur.lastrowid

        for gene_id, reference_identifier, position_0based, reference_aa, mutation in valid_members:
            conn.execute(
                'INSERT INTO resistance_rule_set_member '
                '(rule_set_id, gene_id, reference_identifier, position, reference, mutation) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (rule_set_id, gene_id, reference_identifier, position_0based, reference_aa, mutation),
            )

        count += 1

    return count


def _consolidate_drug_names_to_lowercase(conn: sqlite3.Connection, project_id: int) -> None:
    """Collapse case-only duplicate drug rows and keep lowercase canonical names."""
    conn.row_factory = sqlite3.Row
    groups = conn.execute(
        'SELECT LOWER(name) AS normalized_name, COUNT(*) AS cnt '
        'FROM drug WHERE project_id = ? GROUP BY LOWER(name) HAVING COUNT(*) > 1',
        (project_id,),
    ).fetchall()

    for group in groups:
        normalized_name = group['normalized_name']
        rows = conn.execute(
            'SELECT id FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id',
            (project_id, normalized_name),
        ).fetchall()
        keep_id = int(rows[0]['id'])
        duplicate_ids = [int(row['id']) for row in rows[1:]]

        for duplicate_id in duplicate_ids:
            conn.execute(
                'UPDATE resistance_rule SET drug_id = ? WHERE drug_id = ?',
                (keep_id, duplicate_id),
            )
            conn.execute(
                'UPDATE resistance_rule_set SET drug_id = ? WHERE drug_id = ?',
                (keep_id, duplicate_id),
            )
            conn.execute('DELETE FROM drug WHERE id = ?', (duplicate_id,))

        conn.execute(
            'UPDATE drug SET name = ? WHERE id = ?',
            (normalized_name, keep_id),
        )

    conn.execute(
        'UPDATE drug SET name = LOWER(name) WHERE project_id = ? AND name != LOWER(name)',
        (project_id,),
    )


def _get_drugs_from_pubchem(conn: sqlite3.Connection, project_id: int) -> None:
    """
    Add missing PubChem metadata to drug records.

    Queries PubChem by drug name and writes back the CID, canonical URL, and
    a short description for each matched compound. Drugs that already have
    complete PubChem data are not queried again. Failures — including no
    network access, unrecognised drug names, or unexpected API responses — are
    logged and skipped so the database is always built successfully.

    :param conn: SQLite database connection (row_factory must be sqlite3.Row)
    :param project_id: project ID used to scope the drug lookup
    """
    from respro.io.pubchem import lookup_drug

    conn.row_factory = sqlite3.Row
    drug_rows = conn.execute(
        'SELECT id, name, pubchem_cid, pubchem_url, description, structure_url '
        'FROM drug WHERE project_id = ?',
        (project_id,),
    ).fetchall()

    if not drug_rows:
        return

    # A non-empty pubchem_cid means the drug was already resolved; description
    # may legitimately be absent for some compounds and must not trigger a retry.
    drugs_to_query = [
        drug for drug in drug_rows if not (drug['pubchem_cid'] or '').strip()
    ]

    already_present = len(drug_rows) - len(drugs_to_query)
    if not drugs_to_query:
        logger.info('PubChem: all %d drug(s) already have stored data', len(drug_rows))
        return

    # Best-effort PubChem lookup: failures never block DB creation.
    logger.info('PubChem: querying data for %d drug(s)', len(drugs_to_query))
    if already_present:
        logger.info('PubChem: skipped %d drug(s) with stored data', already_present)

    info_added = 0

    for drug in drugs_to_query:
        drug_name = drug['name']
        record = lookup_drug(drug_name)
        if record is None:
            logger.warning(
                'PubChem: no record found for %r — stored without PubChem data',
                drug_name,
            )
            continue

        conn.execute(
            'UPDATE drug SET pubchem_cid = ?, pubchem_url = ?, description = ?, structure_url = ? WHERE id = ?',
            (str(record.cid), record.url, record.description, record.structure_url, drug['id']),
        )
        info_added += 1
        logger.info('PubChem: added data for %r (CID %s)', drug_name, record.cid)

    logger.info('PubChem: added data for %d/%d queried drug(s)', info_added, len(drugs_to_query))


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

    # populate a dict of gene names to lists of gene rows
    genes_by_name: dict[str, list[sqlite3.Row]] = {}
    for row in gene_lookup_rows:
        genes_by_name.setdefault(row['gene_name'], []).append(row)

    return genes_by_name


def _get_or_create_drug_id(
    conn: sqlite3.Connection,
    project_id: int,
    drug_name: str,
    drug_cache: dict[str, int],
) -> int:
    """
    Get the drug ID for a given drug name, creating a new drug record if needed.

    :param conn: SQLite database connection
    :param project_id: ID of the project
    :param drug_name: name of the drug
    :param drug_cache: cache of drug names to drug IDs
    :return: drug ID
    """
    normalized_name = drug_name.strip().lower()
    if normalized_name in drug_cache:
        return drug_cache[normalized_name]

    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT id, name FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized_name),
    ).fetchone()
    if row is not None:
        if row['name'] != normalized_name:
            conn.execute(
                'UPDATE drug SET name = ? WHERE id = ?',
                (normalized_name, row['id']),
            )
        drug_cache[normalized_name] = int(row['id'])
        return int(row['id'])

    cur = conn.execute(
        'INSERT OR IGNORE INTO drug (project_id, name) VALUES (?, ?)',
        (project_id, normalized_name),
    )
    drug_id = cur.lastrowid
    if drug_id:
        drug_cache[normalized_name] = drug_id
        return drug_id

    row = conn.execute(
        'SELECT id FROM drug WHERE project_id = ? AND LOWER(name) = ? ORDER BY id LIMIT 1',
        (project_id, normalized_name),
    ).fetchone()
    drug_cache[normalized_name] = row[0]
    return row[0]
