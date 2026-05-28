"""
Tests for standalone results database schema.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
import uuid
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from respro.cli.init import init_project
from respro.core.rules import load_rules
from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FormulaRuleHit,
    ProfilingResult,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
)
from respro.db.results import (
    delete_run,
    list_runs,
    load_coverage_gaps,
    load_formula_rule_hits,
    load_run,
    project_fingerprint,
    reconstruct_annotations,
    reconstruct_formula_rule_hits,
    save_run,
)
from respro.db.schema import create_schema, init_results_db, open_project_db
from respro.io.reference import load_features_for_reference
from respro.report.non_html_exports import export_results


def _init_split_project(
    tmp_path: Path,
    *,
    feature_name: str,
    strand: str,
    segments: list[tuple[int, int]],
    reference_aa: str,
    mutation_aa: str,
) -> Path:
    gb_path = tmp_path / f'{feature_name}.gb'
    record = SeqRecord(Seq('GCT' * 120), id='tiny_ref', name='tiny_ref', description='')
    record.annotations['molecule_type'] = 'DNA'
    record.annotations['accessions'] = ['tiny_ref']
    record.features = [
        SeqFeature(
            CompoundLocation(
                [
                    FeatureLocation(start, end, strand=1 if strand == '+' else -1)
                    for start, end in segments
                ]
            ),
            type='CDS',
            qualifiers={
                'gene': [feature_name],
                'product': ['DNA polymerase'],
                'codon_start': ['1'],
            },
        )
    ]
    with open(gb_path, 'w') as handle:
        SeqIO.write([record], handle, 'genbank')

    rules_tsv = tmp_path / f'{feature_name}.tsv'
    rules_tsv.write_text(
        textwrap.dedent(
            f'''\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            {feature_name}\ttiny_ref\t2\t{reference_aa}\t{mutation_aa}\tDrugA\tresistant
            '''
        ),
        encoding='utf-8',
    )

    db_path = tmp_path / f'{feature_name}.db'
    init_project(
        db_path=db_path,
        name='split-test',
        genbank_paths=[gb_path],
        rules_tsv=rules_tsv,
        additional_info=False,
    )
    return db_path


class TestProjectSchemaBoundary:
    def test_project_schema_excludes_results_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'project.db'
        conn = create_schema(db_path)
        tables = {
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'sample' not in tables
        assert 'run' not in tables
        assert 'variant_result' not in tables

    def test_open_project_db_rejects_incompatible_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'broken_project.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        conn.close()

        with pytest.raises(ValueError) as exc_info:
            open_project_db(db_path)

        message = str(exc_info.value)
        assert 'schema mismatch' in message
        assert 'missing tables' in message

    def test_open_project_db_adds_missing_optional_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'project_optional_migration.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            'CREATE TABLE project ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name TEXT NOT NULL, '
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            'schema_version INTEGER NOT NULL DEFAULT 1'
            ')'
        )
        conn.execute(
            'CREATE TABLE reference ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_id INTEGER NOT NULL, '
            'name TEXT NOT NULL, '
            'length INTEGER NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE feature ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'reference_id INTEGER NOT NULL, '
            'name TEXT NOT NULL, '
            'start INTEGER NOT NULL, '
            'end INTEGER NOT NULL, '
            "strand TEXT NOT NULL DEFAULT '+'"
            ')'
        )
        conn.execute(
            'CREATE TABLE drug ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_id INTEGER NOT NULL, '
            'name TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'feature_id INTEGER NOT NULL, '
            'drug_id INTEGER NOT NULL, '
            'position INTEGER NOT NULL, '
            'mutation TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule_set ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'drug_id INTEGER NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE resistance_rule_set_member ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'rule_set_id INTEGER NOT NULL, '
            'feature_id INTEGER NOT NULL, '
            'position INTEGER NOT NULL, '
            'mutation TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE query_reference ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name TEXT NOT NULL, '
            'sequence TEXT NOT NULL, '
            'length INTEGER NOT NULL, '
            'checksum TEXT NOT NULL, '
            'UNIQUE(checksum)'
            ')'
        )
        conn.execute(
            'CREATE TABLE query_feature_mapping ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'query_ref_id INTEGER NOT NULL, '
            'feature_id INTEGER NOT NULL, '
            'identity REAL NOT NULL, '
            'cds_coverage REAL NOT NULL, '
            'query_start INTEGER NOT NULL, '
            'query_end INTEGER NOT NULL, '
            "strand TEXT NOT NULL DEFAULT '+', "
            'cigar TEXT NOT NULL, '
            'UNIQUE(query_ref_id, feature_id)'
            ')'
        )
        conn.execute(
            'INSERT INTO project (name, created_at, schema_version) VALUES (?, datetime(\'now\'), ?)',
            ('Legacy Project', 1),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref_legacy', 100),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)',
            (1, 'gag', 0, 30, '+'),
        )
        conn.execute(
            'INSERT INTO drug (project_id, name) VALUES (?, ?)',
            (1, 'drugx'),
        )
        conn.execute(
            'INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)',
            (1, 1, 1, 'E'),
        )
        conn.execute(
            'INSERT INTO query_reference (name, sequence, length, checksum) VALUES (?, ?, ?, ?)',
            ('legacy_query_ref', 'ATGAAACCC', 9, 'legacy_checksum'),
        )
        conn.execute(
            'INSERT INTO query_feature_mapping ('
            'query_ref_id, feature_id, identity, cds_coverage, query_start, query_end, strand, cigar'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (1, 1, 0.95, 0.75, 0, 9, '+', '9M'),
        )
        conn.commit()
        conn.close()

        migrated_conn = open_project_db(db_path)
        reference_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(reference)').fetchall()
        }
        rule_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(resistance_rule)').fetchall()
        }
        mapping_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(query_feature_mapping)').fetchall()
        }
        migrated_reference = migrated_conn.execute(
            'SELECT accession, organism, taxonomy FROM reference WHERE id = 1'
        ).fetchone()
        migrated_rule = migrated_conn.execute(
            'SELECT reference_identifier, reference, phenotype, clinical_phenotype '
            'FROM resistance_rule WHERE id = 1'
        ).fetchone()
        mapping_count = migrated_conn.execute(
            'SELECT COUNT(*) AS total FROM query_feature_mapping'
        ).fetchone()
        migrated_mapping_stats = migrated_conn.execute(
            'SELECT COUNT(*) AS total, COALESCE(MAX(cds_start), 0) AS max_cds_start '
            'FROM query_feature_mapping'
        ).fetchone()
        reference_count = migrated_conn.execute(
            'SELECT COUNT(*) AS total FROM query_reference'
        ).fetchone()
        migrated_conn.close()

        assert 'organism' in reference_columns
        assert 'query_coverage' in mapping_columns  # auto-added by optional migration
        assert 'cds_start' in mapping_columns  # auto-added by optional migration
        assert 'reference_identifier' in rule_columns
        assert 'clinical_phenotype' in rule_columns
        assert migrated_reference is not None
        assert migrated_reference['accession'] == ''
        assert migrated_reference['organism'] == ''
        assert migrated_reference['taxonomy'] == ''
        assert migrated_rule is not None
        assert migrated_rule['reference_identifier'] == ''
        assert migrated_rule['reference'] == ''
        assert migrated_rule['phenotype'] == 'unknown'
        assert migrated_rule['clinical_phenotype'] == 'unknown'
        assert migrated_mapping_stats is not None
        assert int(migrated_mapping_stats['total']) == 1
        assert int(migrated_mapping_stats['max_cds_start']) == 0
        assert mapping_count is not None
        assert int(mapping_count['total']) == 1
        assert reference_count is not None
        assert int(reference_count['total']) == 1


class TestResultsDbSchema:
    def test_init_results_db_creates_expected_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results.db'
        conn = init_results_db(db_path)
        tables = {
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'results_meta' in tables
        assert 'run' in tables
        assert 'variant_result' in tables
        assert 'coverage_gap' in tables
        assert 'formula_rule_hit' in tables

    def test_init_results_db_validates_existing_compatible_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results_existing.db'
        conn = init_results_db(db_path)
        conn.close()

        validated_conn = init_results_db(db_path)
        meta_row = validated_conn.execute(
            'SELECT value FROM results_meta WHERE key = ?',
            ('results_schema_version',),
        ).fetchone()
        validated_conn.close()

        assert meta_row is not None
        assert meta_row['value'] == '1'

    def test_init_results_db_rejects_existing_incompatible_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'broken_results.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.execute('CREATE TABLE variant_result (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.commit()
        conn.close()

        with pytest.raises(ValueError) as exc_info:
            init_results_db(db_path)

        message = str(exc_info.value)
        assert 'schema mismatch' in message
        assert 'results_meta' in message

    def test_init_results_db_adds_missing_optional_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'results_optional_migration.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute(
            'CREATE TABLE run ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_name TEXT NOT NULL, '
            'project_db_path TEXT NOT NULL, '
            'reference_name TEXT NOT NULL, '
            'vcf_path TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'CREATE TABLE variant_result ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'run_id INTEGER NOT NULL, '
            'chrom TEXT NOT NULL, '
            'pos INTEGER NOT NULL, '
            'ref TEXT NOT NULL, '
            'alt TEXT NOT NULL'
            ')'
        )
        conn.execute(
            'INSERT INTO run (project_name, project_db_path, reference_name, vcf_path) '
            'VALUES (?, ?, ?, ?)',
            ('p', '/tmp/project.db', 'ref', '/tmp/sample.vcf'),
        )
        conn.execute(
            'INSERT INTO variant_result (run_id, chrom, pos, ref, alt) VALUES (?, ?, ?, ?, ?)',
            (1, 'ref', 4, 'A', 'G'),
        )
        conn.commit()
        conn.close()

        migrated_conn = init_results_db(db_path)
        run_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(run)').fetchall()
        }
        variant_columns = {
            row['name']
            for row in migrated_conn.execute('PRAGMA table_info(variant_result)').fetchall()
        }
        migrated_run = migrated_conn.execute(
            'SELECT sample_name, total_variants, resistance_hits, status FROM run WHERE id = 1'
        ).fetchone()
        migrated_variant = migrated_conn.execute(
            'SELECT feature_name, af_bin, drug_hits FROM variant_result WHERE id = 1'
        ).fetchone()
        migrated_conn.close()

        assert 'sample_name' in run_columns
        assert 'resistance_hits' in run_columns
        assert 'af_bin' in variant_columns
        assert 'drug_hits' in variant_columns
        assert migrated_run is not None
        assert migrated_run['sample_name'] == ''
        assert migrated_run['total_variants'] == 0
        assert migrated_run['resistance_hits'] == 0
        assert migrated_run['status'] == 'complete'
        assert migrated_variant is not None
        assert migrated_variant['feature_name'] == ''
        assert migrated_variant['af_bin'] == ''
        assert migrated_variant['drug_hits'] == '[]'


class TestResultsPersistence:
    """Tests for save_run, list_runs, load_run, and reconstruct_annotations."""

    @pytest.fixture()
    def minimal_project_conn(self, tmp_path: Path):
        db_path = tmp_path / 'project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('Test Project', 1, str(uuid.uuid4())),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
            (1, 'ref1', 100),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)',
            (1, 'gag', 0, 90, '+'),
        )
        conn.execute(
            'INSERT INTO drug (project_id, name) VALUES (?, ?)',
            (1, 'drugx'),
        )
        conn.execute(
            'INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)',
            (1, 1, 1, 'E'),
        )
        conn.commit()
        return conn

    @pytest.fixture()
    def results_conn(self, tmp_path: Path):
        conn = init_results_db(tmp_path / 'results.db')
        yield conn
        conn.close()

    def _make_result(self, annotated: bool = True, with_combo_hit: bool = False) -> ProfilingResult:
        v = VariantCall(chrom='ref1', pos=3, ref='A', alt='G', allele_freq=0.9, depth=100)
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1, drug_name='drugx', drug_id=1,
            reference_identifier='', position=1, reference='K', mutation='E',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=v,
            feature_name='gag',
            codon_pos=1,
            ref_codon='AAA',
            alt_codon='GAA',
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
            af_bin='high',
            rule_matches=[rule] if annotated else [],
        )
        formula_hits: list[FormulaRuleHit] = []
        if with_combo_hit:
            rule_set = ResistanceRuleSet(
                id=1,
                drug_name='drugx',
                drug_id=1,
                phenotype='resistant',
                group_name='combo_1',
            )
            rule_set.members = [
                ResistanceRuleSetMember(
                    id=1,
                    rule_set_id=1,
                    feature_name='gag',
                    feature_id=1,
                    reference_identifier='ref1',
                    position=1,
                    reference='K',
                    mutation='E',
                ),
                ResistanceRuleSetMember(
                    id=2,
                    rule_set_id=1,
                    feature_name='gag',
                    feature_id=1,
                    reference_identifier='ref1',
                    position=5,
                    reference='A',
                    mutation='V',
                ),
            ]
            formula_hits = [FormulaRuleHit(rule_set=rule_set, matched_variants=[ann])]

        return ProfilingResult(
            project_name='Test Project',
            reference_name='ref1',
            sample_name='sample01',
            vcf_name='sample.vcf',
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1 if annotated else 0,
            annotations=[ann],
            formula_hits=formula_hits,
        )

    def test_save_run_inserts_run_row(self, results_conn, minimal_project_conn, tmp_path) -> None:
        result = self._make_result()
        run_id = save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, result)

        assert run_id == 1
        row = results_conn.execute('SELECT * FROM run WHERE id = 1').fetchone()
        assert row['project_name'] == 'Test Project'
        assert row['reference_name'] == 'ref1'
        assert row['sample_name'] == 'sample01'
        assert row['resistance_hits'] == 1
        assert row['project_fingerprint'] != ''

    def test_save_run_inserts_variant_result_rows(self, results_conn, minimal_project_conn, tmp_path) -> None:
        result = self._make_result()
        save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, result)

        count = results_conn.execute('SELECT COUNT(*) FROM variant_result WHERE run_id = 1').fetchone()[0]
        assert count == 1

        row = results_conn.execute('SELECT * FROM variant_result WHERE run_id = 1').fetchone()
        assert row['feature_name'] == 'gag'
        assert row['ref_aa'] == 'K'
        assert row['alt_aa'] == 'E'
        assert row['rule_match'] == 1
        drug_hits = json.loads(row['drug_hits'])
        assert len(drug_hits) == 1
        assert drug_hits[0]['drug'] == 'drugx'

    def test_list_runs_returns_all_runs(self, results_conn, minimal_project_conn, tmp_path) -> None:
        save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, self._make_result())
        save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, self._make_result(annotated=False))

        runs = list_runs(results_conn)
        assert len(runs) == 2
        assert runs[0]['id'] == 1
        assert runs[1]['id'] == 2

    def test_load_run_returns_run_and_variants(self, results_conn, minimal_project_conn, tmp_path) -> None:
        save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, self._make_result())

        run_dict, variant_rows = load_run(results_conn, 1)
        assert run_dict['sample_name'] == 'sample01'
        assert len(variant_rows) == 1

    def test_load_run_raises_for_missing_id(self, results_conn) -> None:
        with pytest.raises(ValueError, match='No run found with id 999'):
            load_run(results_conn, 999)

    def test_reconstruct_annotations_restores_rule_matches(self, results_conn, minimal_project_conn, tmp_path) -> None:
        save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, self._make_result())
        _, variant_rows = load_run(results_conn, 1)

        annotations = reconstruct_annotations(variant_rows)
        assert len(annotations) == 1
        ann = annotations[0]
        assert ann.feature_name == 'gag'
        assert ann.ref_aa == 'K'
        assert ann.is_resistance_hit
        assert ann.rule_matches[0].drug_name == 'drugx'

    def test_save_run_persists_formula_rule_hits(self, results_conn, minimal_project_conn, tmp_path) -> None:
        save_run(
            results_conn,
            tmp_path / 'project.db',
            minimal_project_conn,
            self._make_result(with_combo_hit=True),
        )

        row = results_conn.execute(
            'SELECT run_id, hit_json FROM formula_rule_hit WHERE run_id = 1'
        ).fetchone()
        assert row is not None
        assert row['run_id'] == 1
        payload = json.loads(row['hit_json'])
        assert payload['rule_group'] == 'combo_1'
        assert payload['drug'] == 'drugx'

    def test_reconstruct_formula_rule_hits_restores_combo_data(
        self,
        results_conn,
        minimal_project_conn,
        tmp_path,
    ) -> None:
        save_run(
            results_conn,
            tmp_path / 'project.db',
            minimal_project_conn,
            self._make_result(with_combo_hit=True),
        )
        _, variant_rows = load_run(results_conn, 1)
        annotations = reconstruct_annotations(variant_rows)
        combo_rows = load_formula_rule_hits(results_conn, 1)

        formula_hits = reconstruct_formula_rule_hits(combo_rows, annotations)

        assert len(formula_hits) == 1
        hit = formula_hits[0]
        assert hit.rule_set.group_name == 'combo_1'
        assert hit.rule_set.drug_name == 'drugx'
        assert len(hit.rule_set.members) == 2
        assert len(hit.matched_variants) == 1
        assert hit.matched_variants[0].feature_name == 'gag'

    def test_project_fingerprint_is_deterministic(self, minimal_project_conn) -> None:
        fp1 = project_fingerprint(minimal_project_conn)
        fp2 = project_fingerprint(minimal_project_conn)
        assert fp1 == fp2
        assert len(fp1) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    def test_project_fingerprint_differs_for_different_projects(self, tmp_path: Path) -> None:
        conn_a = create_schema(tmp_path / 'a.db')
        conn_a.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('ProjectA', 1, str(uuid.uuid4())),
        )
        conn_a.execute('INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)', (1, 'ref_a', 50))
        conn_a.execute('INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)', (1, 'g', 0, 30, '+'))
        conn_a.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'd'))
        conn_a.execute('INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)', (1, 1, 0, 'E'))
        conn_a.commit()

        conn_b = create_schema(tmp_path / 'b.db')
        conn_b.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('ProjectB', 1, str(uuid.uuid4())),
        )
        conn_b.execute('INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)', (1, 'ref_b', 50))
        conn_b.execute('INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)', (1, 'g', 0, 30, '+'))
        conn_b.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'd'))
        conn_b.execute('INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)', (1, 1, 0, 'E'))
        conn_b.commit()

        assert project_fingerprint(conn_a) != project_fingerprint(conn_b)
        conn_a.close()
        conn_b.close()

    def test_split_feature_run_roundtrip_reconstructs_and_exports_report(self, tmp_path: Path) -> None:
        project_db = _init_split_project(
            tmp_path,
            feature_name='split_pol',
            strand='+',
            segments=[(0, 18), (60, 78)],
            reference_aa='A',
            mutation_aa='V',
        )
        results_conn = init_results_db(tmp_path / 'results.db')
        project_conn = open_project_db(project_db)

        try:
            ref_row = project_conn.execute(
                'SELECT id, organism, length FROM reference WHERE name = ?',
                ('tiny_ref',),
            ).fetchone()
            assert ref_row is not None

            rules = load_rules(project_conn, int(ref_row['id']))
            assert len(rules) == 1

            ann = AnnotatedVariant(
                variant=VariantCall(
                    chrom='tiny_ref',
                    pos=4,
                    ref='C',
                    alt='T',
                    allele_freq=0.95,
                    depth=250,
                ),
                feature_name='split_pol',
                codon_pos=1,
                ref_codon='GCT',
                alt_codon='GTT',
                ref_aa='A',
                alt_aa='V',
                consequence='missense',
                af_bin='high',
                rule_matches=rules,
            )
            run_id = save_run(
                results_conn,
                project_db.resolve(),
                project_conn,
                ProfilingResult(
                    project_name='split-test',
                    organism=ref_row['organism'] or '',
                    reference_name='tiny_ref',
                    reference_length_nt=int(ref_row['length'] or 0),
                    sample_name='split-sample',
                    vcf_name='split.vcf',
                    total_variants=1,
                    variants_in_cds=1,
                    resistance_hits=1,
                    annotations=[ann],
                ),
            )

            run_dict, variant_rows = load_run(results_conn, run_id)
            annotations = reconstruct_annotations(variant_rows)
            features = load_features_for_reference(project_conn, int(ref_row['id']))
            outputs = export_results(
                ProfilingResult(
                    project_name=run_dict['project_name'],
                    organism=ref_row['organism'] or '',
                    reference_name=run_dict['reference_name'],
                    reference_length_nt=int(ref_row['length'] or 0),
                    sample_name=run_dict.get('sample_name', ''),
                    vcf_name=run_dict['vcf_path'],
                    run_timestamp=run_dict.get('created_at', ''),
                    total_variants=run_dict.get('total_variants', 0),
                    variants_in_cds=run_dict.get('variants_in_cds', 0),
                    resistance_hits=run_dict.get('resistance_hits', 0),
                    annotations=annotations,
                ),
                tmp_path / 'roundtrip_report',
                output_html_path=tmp_path / 'roundtrip_report.report.html',
                features=features,
                rule_feature_names={rule.feature_name for rule in rules},
                project_conn=project_conn,
                rules=rules,
            )

            assert len(annotations) == 1
            assert annotations[0].feature_name == 'split_pol'
            assert annotations[0].rule_matches[0].drug_name == 'druga'
            assert len(features) == 1
            assert [
                (segment.segment_index, segment.start, segment.end)
                for segment in features[0].segments
            ] == [(0, 0, 18), (1, 60, 78)]
            assert 'html' in outputs
            assert outputs['html'].exists()
            assert 'split_pol' in outputs['html'].read_text(encoding='utf-8')
        finally:
            project_conn.close()
            results_conn.close()


class TestCoverageGapPersistence:
    """Tests for coverage gap save and load."""

    @pytest.fixture()
    def minimal_project_conn(self, tmp_path: Path):
        db_path = tmp_path / 'project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('Test', 1, str(uuid.uuid4())),
        )
        conn.execute('INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)', (1, 'ref1', 100))
        conn.execute('INSERT INTO feature (reference_id, name, start, end, strand) VALUES (?, ?, ?, ?, ?)', (1, 'gag', 0, 90, '+'))
        conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'd'))
        conn.execute('INSERT INTO resistance_rule (feature_id, drug_id, position, mutation) VALUES (?, ?, ?, ?)', (1, 1, 1, 'E'))
        conn.commit()
        return conn

    @pytest.fixture()
    def results_conn(self, tmp_path: Path):
        conn = init_results_db(tmp_path / 'results.db')
        yield conn
        conn.close()

    def _make_result_with_gaps(self) -> ProfilingResult:
        v = VariantCall(chrom='ref1', pos=3, ref='A', alt='G', allele_freq=1.0, depth=0)
        ann = AnnotatedVariant(variant=v, feature_name='gag', codon_pos=1, consequence='missense', is_fasta_mode=True)
        return ProfilingResult(
            project_name='Test',
            reference_name='ref1',
            vcf_name='sample.fasta',
            annotations=[ann],
            coverage_gaps=[
                CoverageGap(feature_name='gag', codon_start=3, codon_end=3),
                CoverageGap(feature_name='gag', codon_start=5, codon_end=5),
                CoverageGap(feature_name='pol', codon_start=0, codon_end=0),
            ],
        )

    def test_save_run_persists_coverage_gaps(self, results_conn, minimal_project_conn, tmp_path) -> None:
        result = self._make_result_with_gaps()
        run_id = save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, result)

        rows = results_conn.execute(
            'SELECT feature_name, codon_start, codon_end FROM coverage_gap WHERE run_id = ? ORDER BY feature_name, codon_start',
            (run_id,),
        ).fetchall()
        assert len(rows) == 3
        assert (rows[0]['feature_name'], rows[0]['codon_start'], rows[0]['codon_end']) == ('gag', 3, 3)
        assert (rows[1]['feature_name'], rows[1]['codon_start'], rows[1]['codon_end']) == ('gag', 5, 5)
        assert (rows[2]['feature_name'], rows[2]['codon_start'], rows[2]['codon_end']) == ('pol', 0, 0)

    def test_load_coverage_gaps_restores_gaps(self, results_conn, minimal_project_conn, tmp_path) -> None:
        result = self._make_result_with_gaps()
        run_id = save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, result)

        gaps = load_coverage_gaps(results_conn, run_id)
        assert len(gaps) == 3
        assert CoverageGap(feature_name='gag', codon_start=3, codon_end=3) in gaps
        assert CoverageGap(feature_name='pol', codon_start=0, codon_end=0) in gaps

    def test_load_coverage_gaps_empty_for_run_without_gaps(self, results_conn, minimal_project_conn, tmp_path) -> None:
        v = VariantCall(chrom='ref1', pos=3, ref='A', alt='G', allele_freq=0.9, depth=100)
        result = ProfilingResult(
            project_name='Test',
            reference_name='ref1',
            vcf_name='sample.vcf',
            annotations=[AnnotatedVariant(variant=v, feature_name='gag', codon_pos=1)],
        )
        run_id = save_run(results_conn, tmp_path / 'project.db', minimal_project_conn, result)

        gaps = load_coverage_gaps(results_conn, run_id)
        assert gaps == []

    def test_legacy_db_without_coverage_gap_table_returns_empty(self, tmp_path: Path) -> None:
        """A results DB that pre-dates coverage_gap returns [] gracefully."""
        db_path = tmp_path / 'legacy_results.db'
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute(
            'CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_name TEXT NOT NULL, project_db_path TEXT NOT NULL, '
            'reference_name TEXT NOT NULL, vcf_path TEXT NOT NULL)'
        )
        conn.execute(
            'CREATE TABLE variant_result (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'run_id INTEGER NOT NULL, chrom TEXT NOT NULL, pos INTEGER NOT NULL, '
            'ref TEXT NOT NULL, alt TEXT NOT NULL)'
        )
        conn.execute(
            'INSERT INTO run (project_name, project_db_path, reference_name, vcf_path) '
            'VALUES (?, ?, ?, ?)',
            ('p', '/tmp/p.db', 'ref', '/tmp/s.vcf'),
        )
        conn.commit()

        gaps = load_coverage_gaps(conn, 1)
        assert gaps == []
        conn.close()

    def test_init_results_db_adds_coverage_gap_table_to_existing_db(self, tmp_path: Path) -> None:
        """Existing results DB without optional tables gets them added on open."""
        db_path = tmp_path / 'old_results.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute(
            'CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'project_name TEXT NOT NULL, project_db_path TEXT NOT NULL, '
            'reference_name TEXT NOT NULL, vcf_path TEXT NOT NULL)'
        )
        conn.execute(
            'CREATE TABLE variant_result (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'run_id INTEGER NOT NULL, chrom TEXT NOT NULL, pos INTEGER NOT NULL, '
            'ref TEXT NOT NULL, alt TEXT NOT NULL)'
        )
        conn.commit()
        conn.close()

        migrated = init_results_db(db_path)
        tables = {
            row['name']
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        migrated.close()
        assert 'coverage_gap' in tables
        assert 'formula_rule_hit' in tables


class TestDeleteRun:
    def test_delete_run_removes_run_and_related_rows(self, tmp_path: Path) -> None:
        conn = init_results_db(tmp_path / 'results.db')
        conn.execute(
            'INSERT INTO run (project_name, project_db_path, reference_name, sample_name, vcf_path) '
            'VALUES (?, ?, ?, ?, ?)',
            ('project', '/tmp/project.db', 'ref', 'sampleA', '/tmp/sample.vcf'),
        )
        run_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
        conn.execute(
            'INSERT INTO variant_result (run_id, chrom, pos, ref, alt) VALUES (?, ?, ?, ?, ?)',
            (run_id, 'ref', 10, 'A', 'G'),
        )
        conn.execute(
            'INSERT INTO coverage_gap (run_id, feature_name, codon_start, codon_end) VALUES (?, ?, ?, ?)',
            (run_id, 'gag', 1, 3),
        )
        conn.execute(
            'INSERT INTO formula_rule_hit (run_id, hit_json) VALUES (?, ?)',
            (run_id, json.dumps({'drug': 'x'})),
        )
        conn.execute(
            'INSERT INTO sample_classification (run_id, drug, phenotype) VALUES (?, ?, ?)',
            (run_id, 'DrugA', 'resistant'),
        )
        conn.commit()

        deleted = delete_run(conn, str(run_id))

        assert int(deleted['id']) == run_id
        assert deleted['sample_name'] == 'sampleA'
        assert conn.execute('SELECT COUNT(*) FROM run WHERE id = ?', (run_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM variant_result WHERE run_id = ?', (run_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM coverage_gap WHERE run_id = ?', (run_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM formula_rule_hit WHERE run_id = ?', (run_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM sample_classification WHERE run_id = ?', (run_id,)).fetchone()[0] == 0
        conn.close()

    def test_delete_run_raises_on_missing_id(self, tmp_path: Path) -> None:
        conn = init_results_db(tmp_path / 'results.db')
        with pytest.raises(ValueError, match='No run found'):
            delete_run(conn, '999')
        conn.close()


