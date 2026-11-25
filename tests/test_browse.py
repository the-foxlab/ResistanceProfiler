"""Unit and integration tests for project DB discovery/confinement and browse service."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from respro.db.models import INTERNAL_FORMULA_COMPONENT_DRUG_NAME
from respro.db.schema import create_schema
from web.backend import project_dbs
from web.backend.services.browse import (
    _normalize_reference_filter,
    _resolve_reference_id,
    list_databases,
    list_rules,
)


def _create_db(
    path: Path,
    *,
    name: str = 'TestDB',
    project_uuid: str | None = None,
    insert_project_row: bool = True,
    example_fasta: str = '',
    references: list[dict] | None = None,
    rules: list[dict] | None = None,
    algorithms: list[tuple[str, str]] | None = None,
) -> Path:
    conn = create_schema(path)
    conn.row_factory = sqlite3.Row
    try:
        if insert_project_row:
            uid = project_uuid if project_uuid is not None else str(uuid.uuid4())
            conn.execute(
                'INSERT INTO project (name, schema_version, uuid, example_fasta, metadata_description) '
                'VALUES (?, ?, ?, ?, ?)',
                (name, 6, uid, example_fasta, f'Description for {name}'),
            )
        ref_ids: dict[str, int] = {}
        feature_ids: dict[str, int] = {}
        for ref in references or []:
            conn.execute(
                'INSERT INTO reference (project_id, name, accession, organism, length) VALUES (?, ?, ?, ?, ?)',
                (1, ref['name'], ref.get('accession', 'ACC1'), ref.get('organism', ''), 120),
            )
            ref_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            ref_ids[ref['name']] = ref_id
            conn.execute(
                'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (ref_id, 'gag', 'Gag', 0, 90, '+', 'ATG' * 30),
            )
            feature_ids[ref['name']] = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])

        drug_ids: dict[str, int] = {}
        for rule in rules or []:
            drug_name = rule['drug']
            if drug_name not in drug_ids:
                conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, drug_name))
                drug_ids[drug_name] = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            conn.execute(
                'INSERT INTO resistance_rule '
                '(feature_id, drug_id, position, reference, mutation, phenotype) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    feature_ids[rule['ref_name']],
                    drug_ids[drug_name],
                    rule.get('position', 1),
                    rule.get('reference', 'K'),
                    rule.get('mutation', 'E'),
                    rule.get('phenotype', 'resistant'),
                ),
            )

        for alg_name, cfg_json in algorithms or []:
            conn.execute(
                'INSERT INTO interpretation_algorithm (project_id, algorithm_name, config_json) '
                'VALUES (?, ?, ?)',
                (1, alg_name, cfg_json),
            )
        conn.commit()
    finally:
        conn.close()
    return path


class TestProjectDbsReadOnlyAndIsolation:
    def test_open_project_db_readonly_blocks_writes_and_supports_special_chars(self, tmp_path: Path) -> None:
        special = tmp_path / 'db#1? space.db'
        _create_db(special, name='Special')

        conn = project_dbs.open_project_db_readonly(special)
        try:
            row = conn.execute('SELECT name FROM project LIMIT 1').fetchone()
            assert row is not None and row['name'] == 'Special'
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                conn.execute("UPDATE project SET name = 'Mutated'")
        finally:
            conn.close()

    def test_open_project_db_readonly_missing_file_does_not_create_file_or_leak_path(self, tmp_path: Path) -> None:
        missing = tmp_path / 'secret_dir' / 'missing.db'
        with pytest.raises(FileNotFoundError) as exc_info:
            project_dbs.open_project_db_readonly(missing)
        assert not missing.exists()
        assert str(tmp_path) not in str(exc_info.value)
        assert 'missing.db' in str(exc_info.value)

    def test_list_and_index_isolate_corrupt_empty_and_legacy_dbs(self, tmp_path: Path) -> None:
        valid_a = _create_db(tmp_path / 'a_valid.db', name='ValidA', project_uuid='uuid-a')
        valid_b = _create_db(tmp_path / 'd_valid.db', name='ValidB', project_uuid='uuid-b')
        (tmp_path / 'b_corrupt.db').write_bytes(b'not a sqlite file')
        (tmp_path / 'c_zero.db').write_bytes(b'')
        legacy = tmp_path / 'e_legacy.db'
        legacy_conn = sqlite3.connect(legacy)
        legacy_conn.execute('CREATE TABLE foo (id INTEGER PRIMARY KEY)')
        legacy_conn.close()
        _create_db(tmp_path / 'f_no_project_row.db', insert_project_row=False)
        _create_db(tmp_path / 'g_empty_uuid.db', project_uuid='  ')

        listed = project_dbs.list_project_db_paths(tmp_path)
        assert [p.name for p in listed] == ['a_valid.db', 'd_valid.db']

        for bad_meta_db in ('f_no_project_row.db', 'g_empty_uuid.db'):
            with pytest.raises(ValueError) as exc_meta:
                project_dbs.open_resolved_project_db_readonly(tmp_path, bad_meta_db)
            assert bad_meta_db in str(exc_meta.value)
            assert str(tmp_path) not in str(exc_meta.value)

        open_calls: list[str] = []
        real_open = project_dbs.open_project_db_readonly

        def spy_open(path: Path) -> sqlite3.Connection:
            open_calls.append(path.name)
            return real_open(path)

        with patch('web.backend.project_dbs.open_project_db_readonly', side_effect=spy_open):
            index = project_dbs.build_project_db_uuid_index(tmp_path)

        assert index == {'uuid-a': valid_a, 'uuid-b': valid_b}
        # Single-open per *.db file
        assert open_calls.count('a_valid.db') == 1
        assert open_calls.count('d_valid.db') == 1

    def test_build_project_db_uuid_index_skips_duplicate_uuid(self, tmp_path: Path) -> None:
        first = _create_db(tmp_path / 'a_first.db', name='First', project_uuid='dup-uuid')
        _create_db(tmp_path / 'b_second.db', name='Second', project_uuid='dup-uuid')

        index = project_dbs.build_project_db_uuid_index(tmp_path)
        assert index == {'dup-uuid': first}

    def test_explicit_database_resolution_isolates_siblings_and_sanitizes_errors(self, tmp_path: Path) -> None:
        _create_db(tmp_path / 'aaa_corrupt.db', insert_project_row=False)
        (tmp_path / 'aaa_corrupt.db').write_bytes(b'broken')
        valid = _create_db(tmp_path / 'zzz_valid.db', name='ValidZ')

        # Default resolution skips aaa_corrupt.db and picks zzz_valid.db
        resolved_path, conn = project_dbs.open_resolved_project_db_readonly(tmp_path, None)
        conn.close()
        assert resolved_path == valid

        # Explicit resolution does not open aaa_corrupt.db
        opened: list[str] = []
        real_open = project_dbs.open_project_db_readonly

        def spy_open(path: Path) -> sqlite3.Connection:
            opened.append(path.name)
            return real_open(path)

        with patch('web.backend.project_dbs.open_project_db_readonly', side_effect=spy_open):
            path, conn2 = project_dbs.open_resolved_project_db_readonly(tmp_path, 'zzz_valid.db')
            conn2.close()
        assert path == valid
        assert opened == ['zzz_valid.db']

        # Explicit corrupt DB raises ValueError without leaking server path
        with pytest.raises(ValueError) as exc_info:
            project_dbs.open_resolved_project_db_readonly(tmp_path, 'aaa_corrupt.db')
        assert 'aaa_corrupt.db' in str(exc_info.value)
        assert str(tmp_path) not in str(exc_info.value)

        # Path traversal rejected
        for bad_id in ('../escape.db', 'sub/valid.db', 'sub\\valid.db', '.', '..'):
            with pytest.raises(ValueError, match='Unknown database_id'):
                project_dbs.open_resolved_project_db_readonly(tmp_path, bad_id)

    def test_resolve_regenerate_project_db_path_contract(self, tmp_path: Path) -> None:
        valid = _create_db(tmp_path / 'a.db', name='A', project_uuid='fp-1')
        index = {'fp-1': valid}
        assert project_dbs.resolve_regenerate_project_db_path(
            tmp_path, index, project_fingerprint='fp-1', fallback_database_id=None,
        ) == valid
        with pytest.raises(ValueError, match='No project database found for JSON project_fingerprint'):
            project_dbs.resolve_regenerate_project_db_path(
                tmp_path, index, project_fingerprint='missing-fp', fallback_database_id=None,
            )

    def test_default_selection_skips_missing_project_row_and_empty_uuid_dbs(self, tmp_path: Path) -> None:
        _create_db(tmp_path / '00_empty_proj.db', insert_project_row=False)
        _create_db(tmp_path / '00b_empty_uuid.db', project_uuid='   ')
        real_db = _create_db(
            tmp_path / '01_real.db',
            name='RealDB',
            project_uuid='uuid-real',
            references=[{'name': 'RefA', 'accession': 'ACC1'}],
            rules=[{'ref_name': 'RefA', 'drug': 'Acyclovir', 'position': 1, 'reference': 'K', 'mutation': 'E'}],
        )

        assert project_dbs.list_project_db_paths(tmp_path) == [real_db]
        assert project_dbs.build_project_db_uuid_index(tmp_path) == {'uuid-real': real_db}
        assert project_dbs.resolve_project_db_path(tmp_path, None) == real_db

        catalog = list_databases(tmp_path)
        assert [item['id'] for item in catalog['items']] == ['01_real.db']

        rules_payload = list_rules(tmp_path, None)
        assert rules_payload['count'] == 1
        assert rules_payload['items'][0]['drug'] == 'Acyclovir'

        with pytest.raises(ValueError) as exc_info:
            project_dbs.resolve_project_db_path(tmp_path, '00_empty_proj.db')
        assert '00_empty_proj.db' in str(exc_info.value)
        assert str(tmp_path) not in str(exc_info.value)


class TestBrowseService:
    def test_list_databases_excludes_formula_component_single_open_and_isolates_corrupt(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / '00_corrupt.db').write_bytes(b'not-sqlite')
        _create_db(tmp_path / '01_no_project_row.db', insert_project_row=False)
        _create_db(
            tmp_path / '02_curated.db',
            name='Curated',
            example_fasta='>ex\nATGC\n',
            references=[{'name': 'RefA', 'accession': 'ACC1', 'organism': 'HSV-1'}],
            rules=[
                {'ref_name': 'RefA', 'drug': 'Acyclovir', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                {'ref_name': 'RefA', 'drug': INTERNAL_FORMULA_COMPONENT_DRUG_NAME, 'position': 2, 'reference': 'M', 'mutation': 'V'},
                {'ref_name': 'RefA', 'drug': ' __Formula_Component__ ', 'position': 3, 'reference': 'A', 'mutation': 'T'},
            ],
            algorithms=[('effect_as_resistant', '{"name": "effect_as_resistant", "rules": ["loss"]}')],
        )

        open_calls: list[str] = []
        real_open = project_dbs.open_project_db_readonly

        def spy_open(path: Path) -> sqlite3.Connection:
            open_calls.append(path.name)
            return real_open(path)

        with patch('web.backend.project_dbs.open_project_db_readonly', side_effect=spy_open):
            catalog = list_databases(tmp_path)

        assert catalog['count'] == 1
        item = catalog['items'][0]
        assert item['id'] == '02_curated.db'
        assert item['mutation_count'] == 1
        assert item['has_example'] is True
        assert item['supported_organisms'] == ['HSV-1']
        assert item['algorithms'] == {'effect_as_resistant': {'rules': ['loss']}}
        assert open_calls.count('02_curated.db') == 1

        # Matches list_rules single_count
        rules_payload = list_rules(tmp_path, '02_curated.db')
        assert rules_payload['single_count'] == item['mutation_count'] == 1

    def test_list_databases_empty_dir_vs_all_corrupt(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / 'empty'
        empty_dir.mkdir()
        assert list_databases(empty_dir) == {'items': [], 'count': 0}

        corrupt_dir = tmp_path / 'all_corrupt'
        corrupt_dir.mkdir()
        (corrupt_dir / 'only_bad.db').write_bytes(b'corrupt')
        with pytest.raises(ValueError) as exc_info:
            list_databases(corrupt_dir)
        assert str(corrupt_dir) not in str(exc_info.value)

    def test_list_rules_exact_before_substring_and_single_open_readonly(self, tmp_path: Path) -> None:
        _create_db(
            tmp_path / 'multi_ref.db',
            name='MultiRef',
            references=[
                {'name': 'REF1', 'accession': 'ACC1'},
                {'name': 'REF10', 'accession': 'ACC10'},
                {'name': 'OtherGene', 'accession': 'ACC2'},
            ],
            rules=[
                {'ref_name': 'REF1', 'drug': 'Drug1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                {'ref_name': 'REF10', 'drug': 'Drug10', 'position': 2, 'reference': 'M', 'mutation': 'V'},
                {'ref_name': 'OtherGene', 'drug': 'Drug2', 'position': 3, 'reference': 'A', 'mutation': 'T'},
            ],
        )

        open_calls: list[str] = []
        real_open = project_dbs.open_project_db_readonly

        def spy_open(path: Path) -> sqlite3.Connection:
            open_calls.append(path.name)
            return real_open(path)

        with patch('web.backend.project_dbs.open_project_db_readonly', side_effect=spy_open):
            exact = list_rules(tmp_path, 'multi_ref.db', reference_filter='ref1')
        assert exact['count'] == 1
        assert exact['items'][0]['drug'] == 'Drug1'
        assert open_calls == ['multi_ref.db']

        # Unique substring fallback still resolves REF10
        sub = list_rules(tmp_path, 'multi_ref.db', reference_filter='ef10')
        assert sub['count'] == 1
        assert sub['items'][0]['drug'] == 'Drug10'

        # Ambiguous substring raises ValueError
        with pytest.raises(ValueError, match='Ambiguous reference filter'):
            list_rules(tmp_path, 'multi_ref.db', reference_filter='REF')

        # Unknown reference raises ValueError
        with pytest.raises(ValueError, match='No reference matching'):
            list_rules(tmp_path, 'multi_ref.db', reference_filter='MISSING')

    def test_normalize_reference_filter_and_resolve_reference_id_helpers(self, tmp_path: Path) -> None:
        for placeholder in (None, '', '   ', 'undefined', 'UNDEFINED', 'null', ' Null '):
            assert _normalize_reference_filter(placeholder) is None
        assert _normalize_reference_filter('  REF1  ') == 'REF1'

        db = _create_db(
            tmp_path / 'refs.db',
            references=[{'name': 'REF1', 'accession': 'A1'}, {'name': 'REF10', 'accession': 'A10'}],
        )
        conn = project_dbs.open_project_db_readonly(db)
        try:
            assert _resolve_reference_id(conn, None) is None
            ref1_id = _resolve_reference_id(conn, 'REF1')
            ref10_id = _resolve_reference_id(conn, 'REF10')
            assert ref1_id is not None and ref10_id is not None and ref1_id != ref10_id
        finally:
            conn.close()

    def test_list_rules_sanitizes_corrupted_leaf_page_errors(self, tmp_path: Path) -> None:
        db_path = _create_db(
            tmp_path / 'corrupt_leaf.db',
            name='CorruptLeaf',
            references=[{'name': 'RefA', 'accession': 'ACC1'}],
            rules=[
                {
                    'ref_name': 'RefA',
                    'drug': f'Drug{i % 5}',
                    'position': i + 1,
                    'reference': 'K',
                    'mutation': 'E',
                }
                for i in range(500)
            ],
        )
        conn = sqlite3.connect(db_path)
        try:
            page_size = int(conn.execute('PRAGMA page_size').fetchone()[0])
        finally:
            conn.close()

        raw = db_path.read_bytes()
        assert len(raw) > page_size * 2
        db_path.write_bytes(raw[:-page_size] + b'\x00' * page_size)

        # Schema headers stay intact so open_project_db_readonly succeeds, then query hits leaf corruption
        probe_conn = project_dbs.open_project_db_readonly(db_path)
        probe_conn.close()

        with pytest.raises(ValueError) as exc_explicit:
            list_rules(tmp_path, 'corrupt_leaf.db')
        assert str(exc_explicit.value) == "Project database 'corrupt_leaf.db' is invalid or corrupt."
        assert str(tmp_path) not in str(exc_explicit.value)

        with pytest.raises(ValueError) as exc_default:
            list_rules(tmp_path, None)
        assert str(exc_default.value) == 'No valid project database available.'
        assert str(tmp_path) not in str(exc_default.value)

    def test_list_databases_excludes_whitespace_wrapped_formula_component(self, tmp_path: Path) -> None:
        _create_db(
            tmp_path / 'ws_formula.db',
            name='WhitespaceFormula',
            references=[{'name': 'RefA', 'accession': 'ACC1'}],
            rules=[
                {'ref_name': 'RefA', 'drug': 'Acyclovir', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                {'ref_name': 'RefA', 'drug': '\t__FORMULA_COMPONENT__\n', 'position': 2, 'reference': 'M', 'mutation': 'V'},
            ],
        )

        catalog = list_databases(tmp_path)
        rules_payload = list_rules(tmp_path, 'ws_formula.db')
        assert catalog['count'] == 1
        assert catalog['items'][0]['mutation_count'] == 1
        assert rules_payload['single_count'] == 1
