"""Tests for the algorithm-label-vs-DB warning (T7)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from respro.db.algorithms import warn_algorithm_labels_not_in_db
from respro.db.schema import create_schema


@pytest.fixture()
def project_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    db_path = tmp_path / 'project.db'
    conn = create_schema(db_path)
    conn.execute(
        "INSERT INTO project (name, schema_version, uuid) VALUES ('test', 1, 'test-uuid')"
    )
    conn.commit()
    project_id = conn.execute('SELECT id FROM project LIMIT 1').fetchone()[0]
    ref_id = conn.execute(
        "INSERT INTO reference (project_id, name, length) VALUES (?, 'ref1', 1000)",
        (project_id,),
    ).lastrowid
    feat_id = conn.execute(
        "INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, 'gene1', 0, 300, '+')",
        (ref_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO feature_segment (feature_id, segment_index, start, end) VALUES (?, 0, 0, 300)",
        (feat_id,),
    )
    drug_id = conn.execute(
        "INSERT INTO drug (project_id, name) VALUES (?, 'DrugA')", (project_id,)
    ).lastrowid
    # Two rules: one 'susceptible', one 'resistant'.
    conn.execute(
        "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, phenotype) "
        "VALUES (?, ?, 1, 'E', 'susceptible')",
        (feat_id, drug_id),
    )
    conn.execute(
        "INSERT INTO resistance_rule (feature_id, drug_id, position, mutation, phenotype) "
        "VALUES (?, ?, 2, 'K', 'resistant')",
        (feat_id, drug_id),
    )
    conn.commit()
    return conn, int(project_id)


class TestWarnAlgorithmLabelsNotInDb:
    """Non-fatal warning when an algorithm config label is not among stored labels."""

    def test_matching_label_no_warning(self, project_db, caplog):
        conn, project_id = project_db
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'resistant': 1},
            }
        ]
        with caplog.at_level(logging.WARNING, logger='respro.db.algorithms'):
            warn_algorithm_labels_not_in_db(conn, project_id, algorithms)
        assert not any('not among' in r.message for r in caplog.records)

    def test_mismatched_label_warns_but_does_not_raise(self, project_db, caplog):
        # 'sensitive' is in the vocabulary (rank 1) but the DB stores 'susceptible'.
        conn, project_id = project_db
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'sensitive': 1, 'resistant': 1},
            }
        ]
        with caplog.at_level(logging.WARNING, logger='respro.db.algorithms'):
            warn_algorithm_labels_not_in_db(conn, project_id, algorithms)
        messages = ' '.join(r.message for r in caplog.records)
        assert 'sensitive' in messages
        assert 'not among' in messages
        # 'resistant' matches the DB and should not be the flagged label.
        flagged = [
            r.message for r in caplog.records
            if r.message.startswith("Algorithm config declares phenotype label 'resistant'")
        ]
        assert flagged == []

    def test_effect_as_resistant_label_checked(self, project_db, caplog):
        conn, project_id = project_db
        algorithms = [
            {
                'name': 'effect_as_resistant',
                'rules': [
                    {'feature': 'gene1', 'effect': ['stop_gained'], 'reference': 'ref1', 'drug': 'DrugA'},
                ],
            }
        ]
        with caplog.at_level(logging.WARNING, logger='respro.db.algorithms'):
            warn_algorithm_labels_not_in_db(conn, project_id, algorithms)
        # effect_as_resistant declares 'resistant' which IS stored → no warning.
        assert not any('not among' in r.message for r in caplog.records)

    def test_unknown_label_still_resolves_via_vocabulary(self, project_db, caplog):
        # 'low-level resistance' is in the vocabulary but not stored in this DB.
        conn, project_id = project_db
        algorithms = [
            {
                'name': 'drug_interpretation',
                'method': 'by_score',
                'thresholds': {'low-level resistance': 1},
            }
        ]
        with caplog.at_level(logging.WARNING, logger='respro.db.algorithms'):
            warn_algorithm_labels_not_in_db(conn, project_id, algorithms)
        messages = ' '.join(r.message for r in caplog.records)
        assert 'low-level resistance' in messages
