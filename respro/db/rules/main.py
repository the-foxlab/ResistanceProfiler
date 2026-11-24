"""Resistance-rule TSV validation and import orchestration: main entry point."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from respro.db.rules.importer import load_formula_rules, load_resistance_rules
from respro.db.rules.publications import report_publication_lookup_failures

__all__ = [
    'import_rules_with_summary',
    'load_formula_rules',
    'load_resistance_rules',
    'validate_rules_tsv',
]


def import_rules_with_summary(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    *,
    formula_rules_tsv: Path | None = None,
    additional_info: bool,
) -> dict[str, int]:
    """Import atomic and optional formula rules TSVs and return inserted row counts."""
    before_formulas = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0])
    before_members = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0])
    pub_failures: list[str] = []

    single_rules, declared_ids, skipped_ids = load_resistance_rules(
        conn,
        project_id,
        rules_tsv,
        require_external_ids=formula_rules_tsv is not None,
        additional_info=additional_info,
        publication_lookup_failures=pub_failures,
    )
    if formula_rules_tsv is not None:
        load_formula_rules(
            conn,
            project_id,
            formula_rules_tsv,
            declared_atomic_ids=declared_ids,
            skipped_atomic_ids=skipped_ids,
            additional_info=additional_info,
            publication_lookup_failures=pub_failures,
        )

    report_publication_lookup_failures(pub_failures)

    after_formulas = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0])
    after_members = int(conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0])
    return {
        'single_rules': int(single_rules),
        'formula_rules': max(0, after_formulas - before_formulas),
        'formula_rule_members': max(0, after_members - before_members),
    }


def validate_rules_tsv(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    *,
    formula_rules_tsv: Path | None = None,
) -> dict[str, int]:
    """Dry-run rule import inside a rolled-back savepoint and return summary counts."""
    conn.execute('SAVEPOINT rules_validate')
    try:
        return import_rules_with_summary(
            conn,
            project_id,
            rules_tsv,
            formula_rules_tsv=formula_rules_tsv,
            additional_info=False,
        )
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT rules_validate')
        conn.execute('RELEASE SAVEPOINT rules_validate')
