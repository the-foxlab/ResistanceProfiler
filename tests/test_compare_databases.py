"""Tests for the database-comparison service (rule overlap on a shared reference)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from respro.db.schema import create_schema
from web.backend.services.compare_databases import (
    compare_databases,
    list_database_reference_accessions,
    list_shared_references,
)

# A rule key is (feature, position, reference, mutation, drug). The reference AA
# distinguishes deletion/insertion anchors; drug separates rules with different
# resistance interpretations for the same amino-acid change.


def _build_db(
    path: Path,
    *,
    name: str,
    accession: str,
    rules: list[dict],
    organism: str = '',
) -> Path:
    """Create a minimal project DB with one reference and the given atomic rules.

    Each rule dict: feature, position, reference, mutation, drug, phenotype,
    ic50, fold_ic50, score, publication.
    """
    conn = create_schema(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        (name, 6, str(uuid.uuid4())),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, accession, organism, length) VALUES (?, ?, ?, ?, ?)',
        (1, f'ref_{accession}', accession, organism, 100),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gag', 'Gag', 0, 90, '+', 'ATG' * 30),
    )
    drug_ids: dict[str, int] = {}
    for rule in rules:
        drug = rule['drug']
        if drug not in drug_ids:
            conn.execute(
                'INSERT INTO drug (project_id, name) VALUES (?, ?)',
                (1, drug),
            )
            drug_ids[drug] = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.execute(
            'INSERT INTO resistance_rule '
            '(feature_id, drug_id, position, reference, mutation, phenotype, ic50, fold_ic50, score, publication) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                1,
                drug_ids[drug],
                rule['position'],
                rule['reference'],
                rule['mutation'],
                rule.get('phenotype', 'resistant'),
                rule.get('ic50', ''),
                rule.get('fold_ic50', ''),
                rule.get('score', ''),
                rule.get('publication', ''),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _rule(feature='gag', position=1, reference='K', mutation='E', drug='DrugA', **kw) -> dict:
    base = {
        'feature': feature,
        'position': position,
        'reference': reference,
        'mutation': mutation,
        'drug': drug,
    }
    base.update(kw)
    return base


@pytest.fixture()
def db_dir(tmp_path: Path) -> Path:
    return tmp_path


def _region_ids(region: dict) -> frozenset:
    return frozenset(region['region'])


class TestCompareDatabases:
    def test_two_db_overlap_and_disjoint(self, db_dir: Path) -> None:
        # Arrange: two DBs sharing accession ACC1.
        #   A: K1E (DrugA), M2V (DrugB)
        #   B: K1E (DrugA), R3Q (DrugD)
        # Shared key K1E -> intersection; M2V -> A only; R3Q -> B only.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA', phenotype='resistant', ic50='1.0'),
            _rule(position=2, reference='M', mutation='V', drug='DrugB'),
        ])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA', phenotype='intermediate'),
            _rule(position=3, reference='R', mutation='Q', drug='DrugD'),
        ])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert: venn regions
        regions = {_region_ids(r): r['count'] for r in result['venn']}
        assert regions[frozenset({'a.db', 'b.db'})] == 1  # K1E
        assert regions[frozenset({'a.db'})] == 1  # M2V
        assert regions[frozenset({'b.db'})] == 1  # R3Q
        assert sum(regions.values()) == 3

        # Assert: union rows (3)
        assert len(result['rows']) == 3
        by_key = {
            (r['feature'], r['position'], r['reference'], r['mutation'], r['drug']): r
            for r in result['rows']
        }
        shared = by_key[('gag', 1, 'K', 'E', 'DrugA')]
        assert _region_ids(shared) == frozenset({'a.db', 'b.db'})
        assert shared['per_db']['a.db']['drug'] == 'DrugA'
        assert shared['per_db']['a.db']['phenotype'] == 'resistant'
        assert shared['per_db']['a.db']['ic50'] == '1.0'
        assert shared['per_db']['b.db']['drug'] == 'DrugA'
        assert shared['per_db']['b.db']['phenotype'] == 'intermediate'
        only_a = by_key[('gag', 2, 'M', 'V', 'DrugB')]
        assert _region_ids(only_a) == frozenset({'a.db'})
        assert only_a['per_db']['b.db'] is None
        assert only_a['per_db']['a.db']['drug'] == 'DrugB'

    def test_key_in_one_db_has_empty_cells_for_others(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA'),
        ])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[
            _rule(position=9, reference='L', mutation='F', drug='DrugZ'),
        ])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert: two disjoint keys, no intersection
        regions = {_region_ids(r): r['count'] for r in result['venn']}
        assert regions.get(frozenset({'a.db', 'b.db'}), 0) == 0
        assert regions[frozenset({'a.db'})] == 1
        assert regions[frozenset({'b.db'})] == 1
        for row in result['rows']:
            present = [db for db, meta in row['per_db'].items() if meta is not None]
            assert len(present) == 1

    def test_same_mutation_for_different_drugs_is_not_matched(self, db_dir: Path) -> None:
        # Arrange: the same amino-acid change has two distinct drug rules in A.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA'),
            _rule(position=1, reference='K', mutation='E', drug='DrugB'),
        ])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA'),
        ])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert: DrugA is shared; DrugB remains an A-only rule.
        assert len(result['rows']) == 2
        rows_by_drug = {row['drug']: row for row in result['rows']}
        assert rows_by_drug['DrugA']['region'] == ['a.db', 'b.db']
        assert rows_by_drug['DrugB']['region'] == ['a.db']
        assert rows_by_drug['DrugA']['per_db']['a.db']['drug'] == 'DrugA'
        assert rows_by_drug['DrugB']['per_db']['b.db'] is None

    def test_repeated_rules_for_same_drug_aggregate_metadata(self, db_dir: Path) -> None:
        # Arrange: duplicate rules for one matching drug carry distinct values.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA', phenotype='resistant'),
            _rule(position=1, reference='K', mutation='E', drug='DrugA', phenotype='intermediate'),
        ])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[
            _rule(position=1, reference='K', mutation='E', drug='DrugA'),
        ])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert
        assert len(result['rows']) == 1
        assert result['rows'][0]['per_db']['a.db']['drug'] == 'DrugA'
        assert result['rows'][0]['per_db']['a.db']['phenotype'] == 'resistant; intermediate'

    def test_linked_publication_is_included_in_metadata(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[_rule()])
        conn = sqlite3.connect(db_dir / 'a.db')
        conn.execute(
            'INSERT INTO publication (doi, title) VALUES (?, ?)',
            ('10.1000/example', 'Example study'),
        )
        conn.execute(
            'INSERT INTO rule_publication (rule_id, publication_id) '
            'SELECT 1, id FROM publication WHERE doi = ?',
            ('10.1000/example',),
        )
        conn.commit()
        conn.close()

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert
        assert result['rows'][0]['per_db']['a.db']['publication'] == '10.1000/example'

    def test_linked_pubmed_id_is_included_when_doi_is_missing(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[_rule()])
        conn = sqlite3.connect(db_dir / 'a.db')
        conn.execute(
            'INSERT INTO publication (pubmed_id, raw_input) VALUES (?, ?)',
            ('12345', 'PMID:12345'),
        )
        conn.execute(
            'INSERT INTO rule_publication (rule_id, publication_id) '
            'SELECT 1, id FROM publication WHERE pubmed_id = ?',
            ('12345',),
        )
        conn.commit()
        conn.close()

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert
        assert result['rows'][0]['per_db']['a.db']['publication'] == 'PMID:12345'

    def test_metadata_fields_exclude_columns_missing_from_all_databases(
        self, db_dir: Path,
    ) -> None:
        # Arrange
        for database_id in ('a.db', 'b.db'):
            _build_db(db_dir / database_id, name=database_id, accession='ACC1', rules=[_rule()])
            conn = sqlite3.connect(db_dir / database_id)
            conn.execute('ALTER TABLE resistance_rule DROP COLUMN score')
            conn.commit()
            conn.close()

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert
        assert 'score' not in result['metadata_fields']
        assert result['metadata_fields'] == ['drug', 'phenotype']

    def test_ref_aa_distinguishes_keys(self, db_dir: Path) -> None:
        # Arrange: same feature/position/mutation but different reference AA.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[
            _rule(position=50, reference='A', mutation='T', drug='DrugA'),
            _rule(position=50, reference='AA', mutation='T', drug='DrugB'),
        ])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[
            _rule(position=50, reference='A', mutation='T', drug='DrugA'),
        ])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert: A50T and AA50T are distinct keys
        assert len(result['rows']) == 2
        regions = {_region_ids(r): r['count'] for r in result['venn']}
        # A50T shared by both; AA50T only in A.
        assert regions[frozenset({'a.db', 'b.db'})] == 1
        assert regions[frozenset({'a.db'})] == 1

    def test_accession_missing_from_one_db_raises(self, db_dir: Path) -> None:
        # Arrange: accession ACC1 in A and B, but C has a different accession.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'c.db', name='C', accession='ACC2', rules=[_rule()])

        # Act / Assert
        with pytest.raises(ValueError, match='c.db'):
            compare_databases(db_dir, ['a.db', 'b.db', 'c.db'], 'ACC1')

    def test_four_databases_raises(self, db_dir: Path) -> None:
        # Arrange
        for i in range(4):
            _build_db(db_dir / f'd{i}.db', name=f'D{i}', accession='ACC1', rules=[_rule()])

        # Act / Assert
        with pytest.raises(ValueError, match='3'):
            compare_databases(db_dir, ['d0.db', 'd1.db', 'd2.db', 'd3.db'], 'ACC1')

    def test_one_database_raises(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])

        # Act / Assert
        with pytest.raises(ValueError, match='2'):
            compare_databases(db_dir, ['a.db'], 'ACC1')

    def test_database_without_rules_yields_empty_result(self, db_dir: Path) -> None:
        # Arrange: B shares the reference but carries no atomic rules.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[])

        # Act
        result = compare_databases(db_dir, ['a.db', 'b.db'], 'ACC1')

        # Assert: A's rule sits alone in the A-only region; B contributes
        # nothing and the intersection is empty. Zero-count regions are
        # omitted from the Venn payload (the frontend drops size-0 areas).
        assert result['venn'] == [
            {'region': ['a.db'], 'count': 1},
        ]
        assert result['counts'] == {
            'total_unique': 1,
            'intersection': 0,
            'per_db': {'a.db': 1, 'b.db': 0},
        }
        assert len(result['rows']) == 1
        assert result['rows'][0]['per_db']['b.db'] is None


class TestListSharedReferences:
    def test_returns_only_accessions_in_all_selected(self, db_dir: Path) -> None:
        # Arrange: A and B share ACC1; A also has ACC2 (not in B).
        a = _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        # Add a second reference ACC2 to A only.
        conn = sqlite3.connect(a)
        conn.execute(
            'INSERT INTO reference (project_id, name, accession, length) VALUES (?, ?, ?, ?)',
            (1, 'ref_acc2', 'ACC2', 100),
        )
        conn.commit()
        conn.close()
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[_rule()])

        # Act
        result = list_shared_references(db_dir, ['a.db', 'b.db'])

        # Assert: only ACC1 is shared
        accessions = [r['accession'] for r in result]
        assert accessions == ['ACC1']
        assert result[0]['present_in'] == ['a.db', 'b.db']

    def test_no_shared_reference_returns_empty(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC2', rules=[_rule()])

        # Act
        result = list_shared_references(db_dir, ['a.db', 'b.db'])

        # Assert
        assert result == []

    def test_includes_organism_from_the_reference(self, db_dir: Path) -> None:
        # Arrange: both DBs annotate ACC1 with the same organism.
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', organism='Monkeypox virus', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', organism='Monkeypox virus', rules=[_rule()])

        # Act
        result = list_shared_references(db_dir, ['a.db', 'b.db'])

        # Assert
        assert result[0]['organism'] == 'Monkeypox virus'

    def test_organism_is_empty_string_when_not_annotated(self, db_dir: Path) -> None:
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', organism='Monkeypox virus', rules=[_rule()])

        result = list_shared_references(db_dir, ['a.db', 'b.db'])

        # First DB has no organism → empty string (frontend omits the bracket).
        assert result[0]['organism'] == ''


class TestListDatabaseReferenceAccessions:
    def test_returns_accessions_per_database(self, db_dir: Path) -> None:
        # Arrange: A has ACC1 + ACC2, B has ACC1, C has ACC3.
        a = _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])
        conn = sqlite3.connect(a)
        conn.execute(
            'INSERT INTO reference (project_id, name, accession, length) VALUES (?, ?, ?, ?)',
            (1, 'ref_acc2', 'ACC2', 100),
        )
        conn.commit()
        conn.close()
        _build_db(db_dir / 'b.db', name='B', accession='ACC1', rules=[_rule()])
        _build_db(db_dir / 'c.db', name='C', accession='ACC3', rules=[_rule()])

        # Act
        result = list_database_reference_accessions(db_dir, ['a.db', 'b.db', 'c.db'])

        # Assert
        assert result == {
            'a.db': {
                'ACC1': {'name': 'ref_ACC1', 'organism': ''},
                'ACC2': {'name': 'ref_acc2', 'organism': ''},
            },
            'b.db': {'ACC1': {'name': 'ref_ACC1', 'organism': ''}},
            'c.db': {'ACC3': {'name': 'ref_ACC3', 'organism': ''}},
        }

    def test_unknown_database_raises(self, db_dir: Path) -> None:
        # Arrange
        _build_db(db_dir / 'a.db', name='A', accession='ACC1', rules=[_rule()])

        # Act / Assert
        with pytest.raises((FileNotFoundError, ValueError)):
            list_database_reference_accessions(db_dir, ['missing.db'])
