"""Database comparison service: rule overlap across 2-3 project databases.

Databases are only comparable when they share a reference, matched by
accession. A compared rule's identity is ``(feature, position, reference,
mutation, drug)``. The reference AA distinguishes deletion/insertion anchors,
and the drug keeps separate resistance interpretations from being merged. Only
atomic single mutations are considered (formula rules are excluded).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from respro.db.models import is_internal_formula_component_drug_name
from web.backend.startup_config import resolve_project_db_path

_MIN_DATABASES = 2
_MAX_DATABASES = 3

_METADATA_FIELDS = ('phenotype', 'ic50', 'fold_ic50', 'score', 'publication')


def list_database_reference_accessions(
    project_databases_dir: Path,
    database_ids: list[str],
) -> dict[str, dict[str, dict[str, str]]]:
    """
    Return each database's reference accessions as ``{accession: {name, organism}}
``.

    Used by the frontend to constrain which databases can be compared: two
    databases are comparable when they share at least one reference accession.

    :param project_databases_dir: directory containing project ``.db`` files
    :param database_ids: database file names to inspect
    :return: ``{database_id: {accession: {name, organism}}}``
    """
    return {
        database_id: _load_reference_accessions(
            resolve_project_db_path(project_databases_dir, database_id),
        )
        for database_id in database_ids
    }


def list_shared_references(
    project_databases_dir: Path,
    database_ids: list[str],
) -> list[dict]:
    """
    Return references (by accession) present in every selected database.

    :param project_databases_dir: directory containing project ``.db`` files
    :param database_ids: 2-3 database file names to compare
    :return: list of ``{accession, name, organism, present_in}`` dicts sorted by
        accession
    """
    _validate_database_count(database_ids)
    accessions_by_db: dict[str, dict[str, dict[str, str]]] = {}
    for database_id in database_ids:
        db_path = resolve_project_db_path(project_databases_dir, database_id)
        accessions_by_db[database_id] = _load_reference_accessions(db_path)

    shared = _intersect_accessions(accessions_by_db)
    result: list[dict] = []
    for accession in sorted(shared):
        info = next(
            (accessions_by_db[db_id][accession] for db_id in database_ids
             if accessions_by_db[db_id].get(accession)),
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
    """
    Compare atomic-rule mutation overlap across 2-3 databases on one reference.

    :param project_databases_dir: directory containing project ``.db`` files
    :param database_ids: 2-3 database file names to compare
    :param accession: shared reference accession to compare on
    :return: dict with ``reference``, ``databases``, ``venn``, ``rows``, ``counts``
    :raises ValueError: if the database count is out of range or the accession
        is missing from any selected database
    """
    _validate_database_count(database_ids)
    rules_by_db: dict[str, dict[tuple, list[dict]]] = {}
    available_metadata_fields = {'drug'}
    reference_name = ''
    for database_id in database_ids:
        db_path = resolve_project_db_path(project_databases_dir, database_id)
        ref_name, rules, metadata_fields = _load_rules_for_accession(
            db_path, accession, database_id,
        )
        if not reference_name:
            reference_name = ref_name
        rules_by_db[database_id] = rules
        available_metadata_fields.update(metadata_fields)

    keys_by_db = {db_id: set(rules.keys()) for db_id, rules in rules_by_db.items()}
    all_keys = sorted(set().union(*keys_by_db.values()))

    venn = _compute_venn(keys_by_db)
    rows = _build_rows(all_keys, rules_by_db, database_ids)
    counts = _compute_counts(keys_by_db, all_keys)

    return {
        'reference': {'accession': accession, 'name': reference_name},
        'databases': list(database_ids),
        'metadata_fields': [
            field for field in ('drug', *_METADATA_FIELDS)
            if field in available_metadata_fields
        ],
        'venn': venn,
        'rows': rows,
        'counts': counts,
    }


def _validate_database_count(database_ids: list[str]) -> None:
    """Reject selections outside the supported 2-3 database range."""
    if len(database_ids) < _MIN_DATABASES or len(database_ids) > _MAX_DATABASES:
        raise ValueError(
            f'Compare requires between {_MIN_DATABASES} and {_MAX_DATABASES} databases, '
            f'got {len(database_ids)}.'
        )


def _load_reference_accessions(db_path: Path) -> dict[str, dict[str, str]]:
    """Return ``{accession: {name, organism}}`` for a project database (non-empty accessions)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, accession, organism FROM reference "
            "WHERE accession IS NOT NULL AND accession != ''"
        ).fetchall()
        return {
            row['accession']: {
                'name': row['name'],
                'organism': row['organism'] or '',
            }
            for row in rows
        }
    finally:
        conn.close()


def _intersect_accessions(accessions_by_db: dict[str, dict[str, dict[str, str]]]) -> set[str]:
    """Return the set of accessions present in every database."""
    if not accessions_by_db:
        return set()
    shared = set(next(iter(accessions_by_db.values())).keys())
    for accessions in accessions_by_db.values():
        shared &= set(accessions.keys())
    return shared


def _load_rules_for_accession(
    db_path: Path,
    accession: str,
    database_id: str,
) -> tuple[str, dict[tuple, list[dict]], set[str]]:
    """
    Load atomic rules for one reference (by accession) grouped by mutation key.

    :return: ``(reference_name, {key: [metadata, ...]}, available_metadata_fields)``
    :raises ValueError: if the accession is not present in the database
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rule_columns = {
            row['name'] for row in conn.execute('PRAGMA table_info(resistance_rule)')
        }
        tables = {
            row['name'] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        linked_publications_available = {'rule_publication', 'publication'} <= tables
        available_metadata_fields = {
            field for field in _METADATA_FIELDS
            if field != 'publication' and field in rule_columns
        }
        if 'publication' in rule_columns or linked_publications_available:
            available_metadata_fields.add('publication')

        ref_row = conn.execute(
            "SELECT id, name FROM reference WHERE accession = ? LIMIT 1",
            (accession,),
        ).fetchone()
        if ref_row is None:
            raise ValueError(
                f'Reference accession {accession!r} not found in database {database_id!r}.'
            )
        rule_metadata = [
            f'rr.{field}' if field in rule_columns else f"'' AS {field}"
            for field in ('phenotype', 'ic50', 'fold_ic50', 'score')
        ]
        publication_sources = []
        if 'publication' in rule_columns:
            publication_sources.append("NULLIF(rr.publication, '')")
        if linked_publications_available:
            publication_sources.append(
                "(SELECT GROUP_CONCAT(citation, '; ') FROM ("
                "SELECT DISTINCT COALESCE(NULLIF(p.doi, ''), "
                "CASE WHEN p.pubmed_id IS NOT NULL AND p.pubmed_id != '' "
                "THEN 'PMID:' || p.pubmed_id END, NULLIF(p.raw_input, '')) AS citation "
                'FROM rule_publication rp '
                'JOIN publication p ON p.id = rp.publication_id '
                'WHERE rp.rule_id = rr.id) '
                "WHERE citation IS NOT NULL AND citation != '' ORDER BY citation)"
            )
        publication_expr = (
            f"COALESCE({', '.join(publication_sources)}, '') AS publication"
            if publication_sources else "'' AS publication"
        )
        selected_metadata = ', '.join([*rule_metadata, publication_expr])
        rows = conn.execute(
            'SELECT g.name AS feature, rr.position, rr.reference, rr.mutation, '
            f'd.name AS drug, {selected_metadata} '
            'FROM resistance_rule rr '
            'JOIN feature g ON g.id = rr.feature_id '
            'JOIN drug d ON d.id = rr.drug_id '
            'WHERE g.reference_id = ? '
            'ORDER BY g.name, rr.position, rr.reference, rr.mutation, d.name, rr.id',
            (ref_row['id'],),
        ).fetchall()
    finally:
        conn.close()

    rules: dict[tuple, list[dict]] = {}
    for row in rows:
        if is_internal_formula_component_drug_name(row['drug']):
            continue
        key = (
            row['feature'], row['position'], row['reference'],
            row['mutation'], row['drug'],
        )
        rules.setdefault(key, []).append({
            'drug': row['drug'] or '',
            **{field: row[field] or '' for field in _METADATA_FIELDS},
        })
    populated_metadata_fields = {
        field
        for field in available_metadata_fields
        if any(
            metadata.get(field)
            for entries in rules.values()
            for metadata in entries
        )
    }
    return ref_row['name'], rules, populated_metadata_fields


def _compute_venn(keys_by_db: dict[str, set[tuple]]) -> list[dict]:
    """Group mutation keys by the exact subset of databases containing them."""
    all_keys = set().union(*keys_by_db.values()) if keys_by_db else set()
    region_counts: dict[frozenset, int] = {}
    for key in all_keys:
        region = frozenset(db_id for db_id, keys in keys_by_db.items() if key in keys)
        region_counts[region] = region_counts.get(region, 0) + 1
    return [
        {'region': sorted(region), 'count': count}
        for region, count in sorted(region_counts.items(), key=lambda item: (len(item[0]), item[0]))
    ]


def _build_rows(
    all_keys: list[tuple],
    rules_by_db: dict[str, dict[tuple, list[dict]]],
    database_ids: list[str],
) -> list[dict]:
    """Build one union row per mutation key with per-database metadata."""
    rows: list[dict] = []
    for key in all_keys:
        feature, position, reference, mutation, drug = key
        per_db: dict[str, dict | None] = {}
        for db_id in database_ids:
            rules = rules_by_db[db_id].get(key)
            per_db[db_id] = _aggregate_metadata(rules) if rules else None
        region = sorted(db_id for db_id in database_ids if per_db[db_id] is not None)
        rows.append({
            'feature': feature,
            'position': position,
            'reference': reference,
            'mutation': mutation,
            'drug': drug,
            'region': region,
            'per_db': per_db,
        })
    return rows


def _aggregate_metadata(rules: list[dict] | None) -> dict | None:
    """Aggregate multiple rules sharing a key into one metadata dict.

    Drug names and non-empty metadata values are joined with ``"; "`` in
    first-appearance order (rows arrive pre-sorted, so this is deterministic).
    """
    if not rules:
        return None
    aggregated: dict[str, str] = {}
    for field in ('drug', *_METADATA_FIELDS):
        values: list[str] = []
        for rule in rules:
            value = (rule.get(field) or '').strip()
            if value and value not in values:
                values.append(value)
        aggregated[field] = '; '.join(values)
    return aggregated


def _compute_counts(keys_by_db: dict[str, set[tuple]], all_keys: list[tuple]) -> dict:
    """Return per-database and intersection counts."""
    per_db = {db_id: len(keys) for db_id, keys in keys_by_db.items()}
    intersection = set(all_keys)
    for keys in keys_by_db.values():
        intersection &= keys
    return {
        'total_unique': len(all_keys),
        'intersection': len(intersection),
        'per_db': per_db,
    }
