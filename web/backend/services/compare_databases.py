"""Database comparison service: atomic-rule overlap across 2-3 project databases.

Databases are compared on a shared reference matched by accession. Each rule's
identity is ``(feature, position, reference, mutation, drug)``; formula rules
are excluded.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from pathlib import Path

from respro.db.models import is_internal_formula_component_drug_name
from respro.db.rules_queries import list_references_for_display
from web.backend.startup_config import list_project_db_paths

_MIN_DATABASES = 2
_MAX_DATABASES = 3
_METADATA_FIELDS = ('phenotype', 'ic50', 'fold_ic50', 'score', 'publication')

_RULES_BY_REFERENCE_SQL = (
    'SELECT g.name AS feature, rr.position, rr.reference, rr.mutation, '
    'd.name AS drug, rr.phenotype, rr.ic50, rr.fold_ic50, rr.score, '
    "COALESCE(NULLIF(rr.publication, ''), "
    "(SELECT GROUP_CONCAT(citation, '; ') FROM ("
    "SELECT DISTINCT COALESCE(NULLIF(p.doi, ''), "
    "CASE WHEN p.pubmed_id IS NOT NULL AND p.pubmed_id != '' "
    "THEN 'PMID:' || p.pubmed_id END, NULLIF(p.raw_input, '')) AS citation "
    'FROM rule_publication rp '
    'JOIN publication p ON p.id = rp.publication_id '
    'WHERE rp.rule_id = rr.id) '
    "WHERE citation IS NOT NULL AND citation != '' ORDER BY citation), '') AS publication "
    'FROM resistance_rule rr '
    'JOIN feature g ON g.id = rr.feature_id '
    'JOIN drug d ON d.id = rr.drug_id '
    'WHERE g.reference_id = ? '
    'ORDER BY g.name, rr.position, rr.reference, rr.mutation, d.name, rr.id'
)


def list_database_reference_accessions(
    project_databases_dir: Path,
    database_ids: list[str],
) -> dict[str, dict[str, dict[str, str]]]:
    """Return ``{database_id: {accession: {name, organism}}}`` for each selected database."""
    db_paths = _resolve_db_paths(project_databases_dir, database_ids)
    return {db_id: _load_reference_accessions(db_paths[db_id]) for db_id in database_ids}


def list_shared_references(
    project_databases_dir: Path,
    database_ids: list[str],
) -> list[dict]:
    """Return sorted references (by accession) present in every selected database."""
    _validate_database_count(database_ids)
    accessions_by_db = list_database_reference_accessions(project_databases_dir, database_ids)
    shared = set.intersection(*(set(acc) for acc in accessions_by_db.values()))

    result: list[dict] = []
    for accession in sorted(shared):
        info = next(
            (accessions_by_db[db_id][accession] for db_id in database_ids if accessions_by_db[db_id].get(accession)),
            {'name': '', 'organism': ''},
        )
        result.append({
            'accession': accession,
            'name': info['name'],
            'organism': info['organism'],
            'present_in': list(database_ids),
        })
    return result


def compare_databases(
    project_databases_dir: Path,
    database_ids: list[str],
    accession: str,
) -> dict:
    """Compare atomic-rule mutation overlap across 2-3 databases on one shared reference."""
    _validate_database_count(database_ids)
    db_paths = _resolve_db_paths(project_databases_dir, database_ids)

    rules_by_db: dict[str, dict[tuple, list[dict]]] = {}
    populated_fields = {'drug'}
    reference_name = ''
    for db_id in database_ids:
        ref_name, rules, fields = _load_rules_for_accession(db_paths[db_id], accession, db_id)
        reference_name = reference_name or ref_name
        rules_by_db[db_id] = rules
        populated_fields.update(fields)

    keys_by_db = {db_id: set(rules) for db_id, rules in rules_by_db.items()}
    all_keys = sorted(set().union(*keys_by_db.values()))
    rows = _build_rows(all_keys, rules_by_db, database_ids)
    region_counts = Counter(
        frozenset(db_id for db_id, keys in keys_by_db.items() if key in keys)
        for key in all_keys
    )
    venn = [
        {'region': sorted(region), 'count': count}
        for region, count in sorted(region_counts.items(), key=lambda item: (len(item[0]), item[0]))
    ]
    intersection_count = region_counts.get(frozenset(keys_by_db), 0)

    return {
        'reference': {'accession': accession, 'name': reference_name},
        'databases': list(database_ids),
        'metadata_fields': [f for f in ('drug', *_METADATA_FIELDS) if f in populated_fields],
        'venn': venn,
        'rows': rows,
        'counts': {
            'total_unique': len(all_keys),
            'intersection': intersection_count,
            'per_db': {db_id: len(keys) for db_id, keys in keys_by_db.items()},
        },
    }


def _validate_database_count(database_ids: list[str]) -> None:
    if not (_MIN_DATABASES <= len(database_ids) <= _MAX_DATABASES):
        raise ValueError(
            f'Compare requires between {_MIN_DATABASES} and {_MAX_DATABASES} databases, '
            f'got {len(database_ids)}.'
        )


def _resolve_db_paths(project_databases_dir: Path, database_ids: list[str]) -> dict[str, Path]:
    """Validate and resolve all requested database IDs with a single directory scan."""
    if not database_ids:
        return {}
    catalog = list_project_db_paths(project_databases_dir)
    if not catalog:
        raise FileNotFoundError(
            'No project database available. Add a .db file or enable maintained bootstrap.'
        )
    by_name = {path.name: path for path in catalog}
    resolved: dict[str, Path] = {}
    for db_id in database_ids:
        path = by_name.get(db_id) if db_id else catalog[0]
        if path is None:
            raise ValueError(f'Unknown database_id {db_id!r}.')
        resolved[db_id] = path
    return resolved


def _load_reference_accessions(db_path: Path) -> dict[str, dict[str, str]]:
    """Return ``{accession: {name, organism}}`` for all references with a non-empty accession."""
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        return {
            ref['accession']: {'name': ref['name'], 'organism': ref['organism'] or ''}
            for ref in list_references_for_display(conn)
            if ref.get('accession')
        }


def _load_rules_for_accession(
    db_path: Path,
    accession: str,
    database_id: str,
) -> tuple[str, dict[tuple, list[dict]], set[str]]:
    """Load atomic rules for *accession* grouped by rule key plus populated metadata fields."""
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        ref_row = conn.execute(
            'SELECT id, name FROM reference WHERE accession = ? LIMIT 1',
            (accession,),
        ).fetchone()
        if ref_row is None:
            raise ValueError(f'Reference accession {accession!r} not found in database {database_id!r}.')
        rows = conn.execute(_RULES_BY_REFERENCE_SQL, (ref_row['id'],)).fetchall()

    rules: dict[tuple, list[dict]] = {}
    populated: set[str] = set()
    for row in rows:
        drug = row['drug'] or ''
        if is_internal_formula_component_drug_name(drug):
            continue
        key = (row['feature'], row['position'], row['reference'], row['mutation'], drug)
        entry = {'drug': drug, **{field: row[field] or '' for field in _METADATA_FIELDS}}
        for field in _METADATA_FIELDS:
            if entry[field]:
                populated.add(field)
        rules.setdefault(key, []).append(entry)

    return ref_row['name'], rules, populated


def _build_rows(
    all_keys: list[tuple],
    rules_by_db: dict[str, dict[tuple, list[dict]]],
    database_ids: list[str],
) -> list[dict]:
    """Build one union row per mutation key with aggregated per-database metadata."""
    rows: list[dict] = []
    for key in all_keys:
        feature, position, reference, mutation, drug = key
        per_db = {db_id: _aggregate_metadata(rules_by_db[db_id].get(key)) for db_id in database_ids}
        rows.append({
            'feature': feature,
            'position': position,
            'reference': reference,
            'mutation': mutation,
            'drug': drug,
            'region': sorted(db_id for db_id in database_ids if per_db[db_id] is not None),
            'per_db': per_db,
        })
    return rows


def _aggregate_metadata(rules: list[dict] | None) -> dict | None:
    """Merge duplicate rules for one key by joining distinct non-empty values in appearance order."""
    if not rules:
        return None
    aggregated: dict[str, str] = {}
    for field in ('drug', *_METADATA_FIELDS):
        seen: list[str] = []
        for rule in rules:
            value = (rule.get(field) or '').strip()
            if value and value not in seen:
                seen.append(value)
        aggregated[field] = '; '.join(seen)
    return aggregated
