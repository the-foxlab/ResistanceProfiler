"""
Privacy-minimal query-reference cache — schema, migration, and store/load behaviour.

The project database must not persist user-identifying data for cached FASTA
queries: only the sequence checksum (computed from the sequence alone) and the
per-feature CIGAR mappings are stored. No FASTA header, sequence, length, or
timestamp is written, and legacy databases are purged of those columns.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
from respro.db.models import FeatureMatch, FeatureRecord
from respro.db.schema import PROJECT_SCHEMA_VERSION, create_schema, open_project_db

QUERY_SEQ = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCCAAAGCTTTTGGCCCCAAATTTGGGCCCAAATAA'


def _query_reference_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        row['name']
        for row in conn.execute('PRAGMA table_info(query_reference)').fetchall()
    }


def _make_match(feature_id: int = 1) -> FeatureMatch:
    feature = FeatureRecord(
        id=feature_id,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=87,
        strand='+',
        codon_start=0,
        nt_sequence=QUERY_SEQ,
    )
    return FeatureMatch(
        feature=feature,
        identity=0.99,
        cds_coverage=1.0,
        query_coverage=1.0,
        cds_start=0,
        query_start=0,
        query_end=len(QUERY_SEQ),
        strand='+',
        cigar=f'{len(QUERY_SEQ)}M',
        intron_intervals=(),
    )


class TestMinimalQueryReferenceSchema:
    def test_fresh_db_query_reference_has_only_id_and_checksum(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)
        cols = _query_reference_columns(conn)
        conn.close()

        assert cols == {'id', 'checksum'}

    def test_schema_version_is_bumped_for_the_minimal_cache(self) -> None:
        assert PROJECT_SCHEMA_VERSION == 4

    def test_fresh_db_rejects_writing_name_or_sequence(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'fresh.db'
        conn = create_schema(db_path)

        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                'INSERT INTO query_reference (name, checksum) VALUES (?, ?)',
                ('patient_42', sequence_checksum(QUERY_SEQ)),
            )
        conn.close()


class TestChecksumOnlyStoreLoad:
    def test_store_mappings_persists_checksum_and_mappings_only(self, project_db: Path) -> None:
        conn = open_project_db(project_db)
        checksum = sequence_checksum(QUERY_SEQ)

        store_mappings(conn, checksum, [_make_match()])
        row = conn.execute(
            'SELECT * FROM query_reference WHERE checksum = ?', (checksum,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert set(row.keys()) == {'id', 'checksum'}

    def test_store_load_round_trip_preserves_match_data(self, project_db: Path) -> None:
        conn = open_project_db(project_db)
        checksum = sequence_checksum(QUERY_SEQ)
        stored = _make_match()

        store_mappings(conn, checksum, [stored])
        loaded = load_cached_mappings(conn, checksum)
        conn.close()

        assert loaded is not None
        assert len(loaded) == 1
        match = loaded[0]
        assert match.feature.id == stored.feature.id
        assert match.identity == stored.identity
        assert match.cds_coverage == stored.cds_coverage
        assert match.query_coverage == stored.query_coverage
        assert match.cds_start == stored.cds_start
        assert match.query_start == stored.query_start
        assert match.query_end == stored.query_end
        assert match.strand == stored.strand
        assert match.cigar == stored.cigar
        assert match.intron_intervals == stored.intron_intervals

    def test_repeated_store_reuses_single_checksum_row(self, project_db: Path) -> None:
        conn = open_project_db(project_db)
        checksum = sequence_checksum(QUERY_SEQ)

        store_mappings(conn, checksum, [_make_match()])
        store_mappings(conn, checksum, [_make_match()])
        count = conn.execute('SELECT COUNT(*) AS n FROM query_reference').fetchone()['n']
        conn.close()

        assert count == 1


class TestChecksumOnlyQueryResolution:
    @pytest.fixture()
    def fasta_project(self, tmp_path: Path) -> Path:
        """Project DB with a ruled feature covering the tiny reference."""
        db_path = tmp_path / 'fasta_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('Fasta Cache', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'tiny_ref', len(QUERY_SEQ)),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, '
            'nt_sequence, has_rules) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (1, 'gag', 'Gag', 0, len(QUERY_SEQ), '+', QUERY_SEQ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_same_sequence_different_header_reuses_cached_mappings(
        self, fasta_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import respro.core.query as query_module

        fasta_one = tmp_path / 'first.fasta'
        fasta_one.write_text(f'>header_one\n{QUERY_SEQ}\n')
        fasta_two = tmp_path / 'second.fasta'
        fasta_two.write_text(f'>header_two\n{QUERY_SEQ}\n')

        conn = open_project_db(fasta_project)
        query_module.resolve_fasta_query_multi(conn, fasta_one)

        def _fail(*args, **kwargs):
            raise AssertionError('match_query_to_features must not be called on cache hit')

        monkeypatch.setattr(query_module, 'match_query_to_features', _fail)
        records = query_module.resolve_fasta_query_multi(conn, fasta_two)
        conn.close()

        assert [r.query_name for r in records] == ['header_two']
        assert len(records[0].feature_matches) >= 1

    def test_same_header_different_sequence_is_realigned(
        self, fasta_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import respro.core.query as query_module

        real_match = query_module.match_query_to_features
        calls: list[str] = []
        other_seq = QUERY_SEQ[:-3] + 'AAA'

        def _counting_match(query_sequence, features, **kwargs):
            calls.append(query_sequence)
            return real_match(query_sequence, features, **kwargs)

        monkeypatch.setattr(query_module, 'match_query_to_features', _counting_match)

        fasta_one = tmp_path / 'first.fasta'
        fasta_one.write_text(f'>shared_header\n{QUERY_SEQ}\n')
        fasta_two = tmp_path / 'second.fasta'
        fasta_two.write_text(f'>shared_header\n{other_seq}\n')

        conn = open_project_db(fasta_project)
        query_module.resolve_fasta_query_multi(conn, fasta_one)
        records = query_module.resolve_fasta_query_multi(conn, fasta_two)
        conn.close()

        assert len(calls) == 2
        assert records[0].query_sequence == other_seq

    def test_header_lookup_functions_are_removed(self) -> None:
        import respro.core.query as query_module

        assert not hasattr(query_module, 'resolve_cached_query_reference')
        assert not hasattr(query_module, '_list_cached_query_headers')


class TestPrivacyRegression:
    """A full FASTA-mode profile run must not leave the FASTA header or the
    query sequence anywhere in the project database."""

    @pytest.fixture()
    def profiled_project(self, tmp_path: Path) -> tuple[Path, str]:
        """Profile a one-SNP query under a patient-identifying header.

        Returns (project db path, profiled query sequence).
        """
        from typer.testing import CliRunner

        from respro.cli.main import app

        db_path = tmp_path / 'privacy_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('Privacy', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'tiny_ref', len(QUERY_SEQ)),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, '
            'nt_sequence, has_rules) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (1, 'gag', 'Gag', 0, len(QUERY_SEQ), '+', QUERY_SEQ),
        )
        conn.execute(
            'INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'TestDrug'),
        )
        conn.execute(
            'INSERT INTO resistance_rule (feature_id, drug_id, position, reference, '
            "mutation, phenotype) VALUES (?, ?, ?, ?, ?, 'resistant')",
            (1, 1, 1, 'K', 'E'),
        )
        conn.commit()
        conn.close()

        # SNPs every 10 nt so no 20-nt window of the query matches the stored
        # reference feature sequence (which legitimately contains reference data).
        query_seq = list(QUERY_SEQ)
        for i in range(5, len(QUERY_SEQ), 10):
            query_seq[i] = 'A' if query_seq[i] != 'A' else 'C'
        query = ''.join(query_seq)

        fasta_path = tmp_path / 'query.fasta'
        fasta_path.write_text(f'>patient_42|sample_1\n{query}\n')

        result = CliRunner().invoke(app, [
            'fasta',
            '--project', str(db_path),
            '--fasta', str(fasta_path),
            '--output', str(tmp_path / 'out'),
            '--cache',
        ])
        assert result.exit_code == 0, result.output
        return db_path, query

    def _all_text_values(self, conn: sqlite3.Connection) -> list[str]:
        tables = [
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        values: list[str] = []
        for table in tables:
            table_identifier = f'"{table}"'
            columns = [
                row['name']
                for row in conn.execute(f'PRAGMA table_info({table_identifier})').fetchall()
            ]
            # feature.nt_sequence/aa_sequence store the internal REFERENCE by
            # design, not user data.
            selected = [
                c for c in columns
                if not (table == 'feature' and c in ('nt_sequence', 'aa_sequence'))
            ]
            column_list = ', '.join(f'"{c}"' for c in selected)
            for row in conn.execute(f'SELECT {column_list} FROM {table_identifier}').fetchall():
                values.extend(str(v) for v in row if v is not None)
        return values

    def test_header_and_sequence_absent_from_project_db(
        self, profiled_project: tuple[Path, str]
    ) -> None:
        db_path, query = profiled_project
        header = 'patient_42|sample_1'
        conn = open_project_db(db_path)
        values = self._all_text_values(conn)
        checksum = sequence_checksum(query)
        conn.close()

        assert any(checksum in v for v in values), 'cache must store the checksum'
        assert not any(header in v for v in values), 'FASTA header must not be stored'
        for start in range(0, len(query) - 19):
            window = query[start:start + 20]
            assert not any(window in v for v in values), (
                f'query sequence window {window!r} found in project DB'
            )

    def test_header_absent_from_raw_database_file(
        self, profiled_project: tuple[Path, str]
    ) -> None:
        db_path, _ = profiled_project
        raw = db_path.read_bytes()
        wal = db_path.parent / (db_path.name + '-wal')
        if wal.exists():
            raw += wal.read_bytes()

        assert b'patient_42|sample_1' not in raw

    def test_nondefault_alignment_skips_cache_and_add_to_project_invalidates(self, tmp_path: Path) -> None:
        from dataclasses import replace
        from unittest.mock import patch

        from conftest import TINY_REF_NAME, TINY_REF_SEQ, write_genbank

        from respro.cli.init import add_to_project, init_project
        from respro.config.cli_settings import CLI_CONFIG
        from respro.core.query import resolve_fasta_query_multi

        gb = write_genbank(
            tmp_path / 'ref.gb',
            [{
                'id': TINY_REF_NAME,
                'accession': TINY_REF_NAME,
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': len(TINY_REF_SEQ), 'strand': '+'}],
            }],
        )
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\n'
            f'gag\t{TINY_REF_NAME}\t2\tK\tE\tDrugA\tresistant\n',
            encoding='utf-8',
        )
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='CacheTest', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = open_project_db(db)
        try:
            qref = tmp_path / 'q.fasta'
            qref.write_text(f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n', encoding='utf-8')
            recs1 = resolve_fasta_query_multi(conn, qref)
            assert len(recs1) == 1
            assert conn.execute('SELECT COUNT(*) FROM query_feature_mapping').fetchone()[0] == 1

            custom_cfg = replace(CLI_CONFIG, alignment=replace(CLI_CONFIG.alignment, k=11))
            with patch('respro.core.query.load_cached_mappings') as mock_load:
                recs2 = resolve_fasta_query_multi(conn, qref, cfg=custom_cfg)
                assert len(recs2) == 1
                mock_load.assert_not_called()
        finally:
            conn.close()

        add_to_project(db_path=db, rules_tsv=tsv, additional_info=False)
        conn2 = open_project_db(db)
        try:
            assert conn2.execute('SELECT COUNT(*) FROM query_feature_mapping').fetchone()[0] == 0
            assert conn2.execute('SELECT COUNT(*) FROM query_reference').fetchone()[0] == 0
        finally:
            conn2.close()
