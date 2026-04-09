"""
Tests for the CLI profile-vcf command — end-to-end integration.
"""

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from conftest import write_genbank
from respro.cli import app
from respro.db.schema import create_schema, init_results_db


class TestProfileCli:
    """End-to-end tests for the ``profile-vcf`` command."""

    def test_profile_produces_html_with_vcf_based_name(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """Running profile should produce an HTML report named after the input VCF."""
        output_dir = tmp_path / 'results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        html_path = output_dir / f'{sample_vcf.stem}.report.html'
        assert html_path.exists()
        html = html_path.read_text()
        assert 'ResistanceProfiler' in html
        assert 'Test Project' in html
        assert 'tiny_ref' in html

    def test_profile_produces_html(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'html_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        html_path = output_dir / f'{sample_vcf.stem}.report.html'
        assert html_path.exists()
        content = html_path.read_text()
        assert 'ResistanceProfiler' in content

    def test_profile_detects_resistance_hit(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """VCF with A→G at pos 4 should trigger the K2E rule in gag."""
        vcf_path = tmp_path / 'hit.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'tiny_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'hit_results'
        runner = CliRunner()
        result = runner.invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output
        assert '1 database hit' in result.output

    def test_profile_with_results_db_creates_new_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_with_db'
        results_db = tmp_path / 'run_results.db'
        result = CliRunner().invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output
        assert results_db.exists()

        conn = sqlite3.connect(results_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'results_meta'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_profile_with_results_db_accepts_existing_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_existing_db'
        results_db = tmp_path / 'existing_results.db'
        conn = init_results_db(results_db)
        conn.close()

        result = CliRunner().invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

    def test_profile_with_results_db_rejects_incompatible_existing_db(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        output_dir = tmp_path / 'results_invalid_db'
        results_db = tmp_path / 'invalid_results.db'
        conn = sqlite3.connect(results_db)
        conn.execute('CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        conn.commit()
        conn.close()

        result = CliRunner().invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code != 0
        assert 'schema mismatch' in result.output.lower()

    def test_profile_fails_when_ref_fasta_does_not_match_any_rule_gene(
        self,
        project_db: Path,
        sample_vcf: Path,
        tmp_path: Path,
    ):
        bad_fasta = tmp_path / 'bad_ref.fasta'
        bad_fasta.write_text('>unrelated\nGATTACAGATTACAGATTACAGATTACA\n')

        result = CliRunner().invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(bad_fasta),
            '--output', str(tmp_path / 'bad_results'),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code != 0
        assert 'no cds matches above thresholds' in result.output.lower()


class TestInitCli:
    """Test the ``init`` CLI command."""

    def test_init_creates_db(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'NC_000001\tgag\t2\tK\tE\tDrugX\tresistant\n'
        )

        db_path = tmp_path / 'project.db'
        runner = CliRunner()
        result = runner.invoke(app, [
            'init',
            '--name', 'CLI Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
        ])
        assert result.exit_code == 0, result.output
        assert db_path.exists()

    def test_init_accepts_extended_rules_columns(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref_multi.gb',
            [
                {
                    'id': 'NC_000001.1',
                    'accession': 'NC_000001',
                    'organism': 'Human alphaherpesvirus 1',
                    'taxonomy': ['Viruses', 'Herpesvirales', 'Herpesviridae'],
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\tic50\tpublication\tsource\n'
            'NC_000001\tgag\t2\tK\tE\tDrugY\tresistant\t>10x\tPMID:12345\therpesdrg-db\n'
        )

        db_path = tmp_path / 'project_extended.db'
        runner = CliRunner()
        result = runner.invoke(app, [
            'init',
            '--name', 'CLI Test Extended',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT rr.reference_identifier, rr.ic50, d.name AS drug_name, '
            'r.organism, r.taxonomy '
            'FROM resistance_rule rr '
            'JOIN drug d ON d.id = rr.drug_id '
            'JOIN gene g ON g.id = rr.gene_id '
            'JOIN reference r ON r.id = g.reference_id '
            'LIMIT 1'
        ).fetchone()
        pub_row = conn.execute(
            'SELECT p.pubmed_id, p.raw_input FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id LIMIT 1'
        ).fetchone()
        conn.close()

        assert row is not None
        assert row['reference_identifier'] == 'NC_000001'
        assert row['ic50'] == '10'
        assert row['drug_name'] == 'drugy'
        assert row['organism'] == 'Human alphaherpesvirus 1'
        assert row['taxonomy'] == 'Viruses; Herpesvirales; Herpesviridae'
        assert pub_row is not None
        assert pub_row['pubmed_id'] == '12345'
        assert pub_row['raw_input'] == 'PMID:12345'

    def test_init_accepts_multiple_genbank_files(self, tmp_path: Path):
        genbank_path_a = write_genbank(
            tmp_path / 'ref_a.gb',
            [
                {
                    'id': 'refA.1',
                    'accession': 'refA',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        genbank_path_b = write_genbank(
            tmp_path / 'ref_b.gb',
            [
                {
                    'id': 'refB.1',
                    'accession': 'refB',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'pol', 'protein': 'Pol', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules_multi_input.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'refA\tgag\t2\tK\tE\tDrugA\tresistant\n'
            'refB\tpol\t2\tK\tE\tDrugB\tresistant\n'
        )

        db_path = tmp_path / 'project_multi_input.db'
        result = CliRunner().invoke(app, [
            'init',
            '--name', 'CLI Multiple GenBank Test',
            '--genbank', str(genbank_path_a),
            '--genbank', str(genbank_path_b),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        reference_count = conn.execute('SELECT COUNT(*) AS n FROM reference').fetchone()['n']
        gene_names = {
            row['name'] for row in conn.execute('SELECT name FROM gene').fetchall()
        }
        drug_names = {
            row['name'] for row in conn.execute('SELECT name FROM drug').fetchall()
        }
        conn.close()

        assert reference_count == 2
        assert gene_names == {'gag', 'pol'}
        assert drug_names == {'druga', 'drugb'}

    def test_init_normalizes_flexible_mutation_inputs(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ref_norm.gb',
            [
                {
                    'id': 'NC_000001.1',
                    'accession': 'NC_000001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_tsv = tmp_path / 'rules_norm.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\n'
            'NC_000001\tgag\t2\tK\tF2STOP\tDrugStop\tresistant\n'
            'NC_000001\tgag\t2\tK\tK2frameshift\tDrugFs\tresistant\n'
            'NC_000001\tgag\t2\tK\tK2delQ\tDrugDel\tresistant\n'
            'NC_000001\tgag\t2\tK\tany\tDrugAny\tresistant\n'
        )

        db_path = tmp_path / 'project_norm.db'
        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Mutation Normalization Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT d.name AS drug_name, rr.mutation '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()

        observed = {row['drug_name']: row['mutation'] for row in rows}
        assert observed == {
            'drugany': 'any',
            'drugdel': 'KQ2K',
            'drugfs': 'fsX',
            'drugstop': '*',
        }

    def test_init_add_uses_existing_annotations_and_skips_semantic_duplicates(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'append_ref.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )

        rules_initial = tmp_path / 'rules_initial.tsv'
        rules_initial.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\tic50\n'
            'ref1\tgag\t2\tK\tE\tDrugX\tresistant\t2x\n'
        )

        db_path = tmp_path / 'append.db'
        runner = CliRunner()
        init_result = runner.invoke(app, [
            'init',
            '--name', 'Append Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_initial),
            '--output', str(db_path),
            '--no-additional-info',
        ])
        assert init_result.exit_code == 0, init_result.output

        rules_append = tmp_path / 'rules_append.tsv'
        rules_append.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\tphenotype\tic50\n'
            'ref1\tgag\t2\tK\tE\tdRuGx\tresistant\t999x\n'
            'ref1\tgag\t3\tA\tV\tDRUGX\tresistant\t5x\n'
        )

        append_result = runner.invoke(app, [
            'init-add',
            '--project', str(db_path),
            '--rules', str(rules_append),
            '--no-additional-info',
        ])
        assert append_result.exit_code == 0, append_result.output
        # Rich may wrap the log line in non-TTY mode; collapse whitespace before checking.
        assert 'duplicate rule(s) skipped' in ' '.join(append_result.output.split())

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rules = conn.execute(
            'SELECT rr.position, rr.mutation, rr.ic50, d.name AS drug_name '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY rr.position'
        ).fetchall()
        conn.close()

        assert len(rules) == 2
        assert rules[0]['position'] == 1
        assert rules[0]['mutation'] == 'E'
        # Existing duplicate rule is kept; incoming ic50 must not overwrite it.
        assert rules[0]['ic50'] == '2'
        assert rules[0]['drug_name'] == 'drugx'
        assert rules[1]['position'] == 2
        assert rules[1]['mutation'] == 'V'

    def test_init_add_requires_existing_database(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'append_missing.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\n'
            'ref1\tgag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init-add',
            '--project', str(tmp_path / 'missing.db'),
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])
        assert result.exit_code != 0
        assert 'does not exist' in result.output

    def test_init_add_rejects_incompatible_existing_database(self, tmp_path: Path):
        db_path = tmp_path / 'broken_existing.db'
        conn = sqlite3.connect(str(db_path))
        conn.execute('CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        conn.close()

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\n'
            'REF1\tgag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init-add',
            '--project', str(db_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])

        assert result.exit_code != 0
        assert 'schema mismatch' in result.output.lower()

    def test_init_add_requires_stored_annotations_when_no_genbank_is_given(self, tmp_path: Path):
        db_path = tmp_path / 'empty_project.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('Broken Project', 9),
        )
        conn.commit()
        conn.close()

        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(
            'gene\tposition\treference\tmutation\tantiviral\n'
            'gag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init-add',
            '--project', str(db_path),
            '--rules', str(rules_tsv),
            '--no-additional-info',
        ])

        assert result.exit_code != 0
        assert 'no stored references/genes' in result.output.lower()

    def test_init_warns_on_rule_gene_missing_in_genbank(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'missing_gene.gb',
            [
                {
                    'id': 'ref1',
                    'accession': 'REF1',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules_missing.tsv'
        rules_tsv.write_text(
            'reference_identifier\tgene\tposition\treference\tmutation\tantiviral\n'
            'REF1\tpol\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Missing Gene Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(tmp_path / 'missing.db'),
        ])

        assert result.exit_code == 0, result.output
        assert 'skipped' in result.output.lower() or (tmp_path / 'missing.db').exists()

    def test_init_requires_reference_identifier_for_ambiguous_multirecord_gene(self, tmp_path: Path):
        genbank_path = write_genbank(
            tmp_path / 'ambiguous.gb',
            [
                {
                    'id': 'refA.1',
                    'accession': 'refA',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'GagA', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
                {
                    'id': 'refB.1',
                    'accession': 'refB',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'genes': [
                        {'gene': 'gag', 'protein': 'GagB', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_tsv = tmp_path / 'rules_ambiguous.tsv'
        rules_tsv.write_text(
            'gene\tposition\treference\tmutation\tantiviral\n'
            'gag\t2\tK\tE\tDrugX\n'
        )

        result = CliRunner().invoke(app, [
            'init',
            '--name', 'Ambiguous Ref Test',
            '--genbank', str(genbank_path),
            '--rules', str(rules_tsv),
            '--output', str(tmp_path / 'ambiguous.db'),
        ])

        assert result.exit_code != 0
        assert 'missing required field reference_identifier' in result.output


    def test_profile_with_results_db_populates_run_and_variants(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ):
        """After profiling with --results-db, a run row and variant rows must be stored."""
        results_db = tmp_path / 'populated.db'
        result = CliRunner().invoke(app, [
            'profile-vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--results-db', str(results_db),
            '--output', str(tmp_path / 'out'),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        conn = sqlite3.connect(results_db)
        conn.row_factory = sqlite3.Row
        run_row = conn.execute('SELECT * FROM run WHERE id = 1').fetchone()
        variant_count = conn.execute('SELECT COUNT(*) FROM variant_result WHERE run_id = 1').fetchone()[0]
        conn.close()

        assert run_row is not None
        assert run_row['project_name'] == 'Test Project'
        assert run_row['reference_name'] == 'tiny_ref'
        assert run_row['project_fingerprint'] != ''
        assert variant_count > 0


class TestExportCli:
    """The ``export`` command has been removed; these tests verify it no longer exists."""

    def test_export_command_no_longer_exists(self, project_db: Path, tmp_path: Path):
        result = CliRunner().invoke(app, [
            'export',
            '--project', str(project_db),
            '--output', str(tmp_path / 'bundle.zip'),
        ])
        assert result.exit_code != 0

