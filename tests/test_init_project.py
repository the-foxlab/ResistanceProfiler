"""
Tests for project initialization — coordinate base detection and reference AA validation.
"""

from __future__ import annotations

import sqlite3
import textwrap

import pytest

from respro.db.project import init_project
from respro.db.project.genes import _is_ncbi_protein_accession
from respro.db.project.rules import _detect_coordinate_base, _validate_reference_amino_acids
from respro.db.schema import create_schema
from conftest import write_genbank, TINY_REF_SEQ

# Amino acid sequence used across tests:
#   index:  0  1  2  3  4  5
#   1-based: 1  2  3  4  5  6
#   AA:      M  K  A  F  G  P
_AA_SEQ = 'MKAFGP'

# Sequence where index 0 and 1 are identical (used for ambiguous tie tests).
#   index:  0  1  2  3  4  5
#   AA:      M  M  K  A  F  G
_AA_SEQ_TIE = 'MMKAFG'


def _gene_row(
    gene_name: str,
    aa_sequence: str,
    reference_name: str = 'ref1',
    reference_accession: str = 'ACC1',
    gene_id: int = 1,
) -> dict:
    """Build a dict that mimics a sqlite3.Row for gene lookups."""
    return {
        'gene_id': gene_id,
        'gene_name': gene_name,
        'aa_sequence': aa_sequence,
        'reference_name': reference_name,
        'reference_accession': reference_accession,
    }


def _genes(gene_name: str, aa_seq: str, **kwargs) -> dict[str, list[dict]]:
    """Convenience wrapper — single-gene lookup dict."""
    return {gene_name: [_gene_row(gene_name, aa_seq, **kwargs)]}


def _rule(
    gene: str,
    position: int | str,
    reference: str,
    mutation: str = 'E',
    reference_identifier: str = '',
) -> dict[str, str]:
    """Build a minimal TSV row dict for a single resistance rule."""
    row = {
        'gene': gene,
        'position': str(position),
        'reference': reference,
        'mutation': mutation,
        'antiviral': 'DrugX',
    }
    if reference_identifier:
        row['reference_identifier'] = reference_identifier
    return row


# ──────────────────────────────────────────────────────────────────────
# _detect_coordinate_base
# ──────────────────────────────────────────────────────────────────────

class TestDetectCoordinateBase:
    def test_detects_1based_when_all_positions_are_1indexed(self) -> None:
        # pos=1 → aa_seq[0]='M', pos=2 → aa_seq[1]='K'  (1-based)
        rows = [
            _rule('gX', position=1, reference='M'),
            _rule('gX', position=2, reference='K'),
        ]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 1

    def test_detects_0based_when_all_positions_are_0indexed(self) -> None:
        # pos=0 → aa_seq[0]='M', pos=1 → aa_seq[1]='K'  (0-based)
        rows = [
            _rule('gX', position=0, reference='M'),
            _rule('gX', position=1, reference='K'),
        ]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 0

    def test_defaults_to_1based_when_no_verifiable_rows(self) -> None:
        # rows with no reference AA → nothing to verify
        rows = [
            {'gene': 'gX', 'position': '3', 'mutation': 'E', 'antiviral': 'DrugX'},
        ]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 1

    def test_defaults_to_1based_when_gene_not_in_lookup(self) -> None:
        rows = [_rule('unknown_gene', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 1

    def test_defaults_to_1based_when_gene_has_no_aa_sequence(self) -> None:
        rows = [_rule('gX', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _genes('gX', aa_seq=''))
        assert result == 1

    def test_prefers_1based_when_majority_of_rows_match(self) -> None:
        # Two rules clearly 1-based, one ambiguous (skipped because gene missing)
        rows = [
            _rule('gX', position=1, reference='M'),   # 1-based match only
            _rule('gX', position=2, reference='K'),   # 1-based match only
        ]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 1

    def test_prefers_0based_when_majority_of_rows_match(self) -> None:
        # Three 0-based rules, none matching 1-based
        rows = [
            _rule('gX', position=0, reference='M'),
            _rule('gX', position=1, reference='K'),
            _rule('gX', position=2, reference='A'),
        ]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 0

    def test_warns_and_returns_1based_on_tie(self) -> None:
        # _AA_SEQ_TIE = 'MMKAFG': pos=1 with ref='M' matches BOTH systems
        #   1-based: aa_seq[0]='M' ✓   0-based: aa_seq[1]='M' ✓
        rows = [_rule('gX', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ_TIE))
        assert result == 1

    def test_raises_when_ref_aa_matches_neither_system(self) -> None:
        # pos=3 with ref='Z' (not in _AA_SEQ at any position near 3)
        #   1-based: aa_seq[2]='A' ≠ 'Z'   0-based: aa_seq[3]='F' ≠ 'Z'
        rows = [_rule('gX', position=3, reference='Z')]
        with pytest.raises(ValueError, match='coordinate system could not be determined'):
            _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))

    def test_uses_reference_identifier_to_resolve_correct_gene(self) -> None:
        # Two genes with same name but different references — pick ACC2
        genes_by_name = {
            'gX': [
                _gene_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', gene_id=1),
                _gene_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', gene_id=2),
            ]
        }
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC2')]
        result = _detect_coordinate_base(rows, genes_by_name)
        assert result == 1

    def test_skips_row_with_non_integer_position(self) -> None:
        rows = [_rule('gX', position='n/a', reference='M')]
        # non-integer position → verifiable=0 → defaults to 1-based
        result = _detect_coordinate_base(rows, _genes('gX', _AA_SEQ))
        assert result == 1


# ──────────────────────────────────────────────────────────────────────
# _validate_reference_amino_acids
# ──────────────────────────────────────────────────────────────────────

class TestValidateReferenceAminoAcids:
    def test_passes_when_all_1based_rules_match(self) -> None:
        rows = [
            _rule('gX', position=1, reference='M'),  # aa_seq[0]='M'
            _rule('gX', position=3, reference='A'),  # aa_seq[2]='A'
            _rule('gX', position=6, reference='P'),  # aa_seq[5]='P'
        ]
        # Should complete without raising
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)

    def test_passes_when_all_0based_rules_match(self) -> None:
        rows = [
            _rule('gX', position=0, reference='M'),  # aa_seq[0]='M'
            _rule('gX', position=2, reference='A'),  # aa_seq[2]='A'
            _rule('gX', position=5, reference='P'),  # aa_seq[5]='P'
        ]
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=0)

    def test_raises_on_single_ref_aa_mismatch(self) -> None:
        # pos=1 (1-based) → aa_seq[0]='M', rule says 'K' → mismatch
        rows = [_rule('gX', position=1, reference='K')]
        with pytest.raises(ValueError) as exc_info:
            _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)
        msg = str(exc_info.value)
        assert 'mismatch' in msg.lower()
        assert "rule says 'K'" in msg
        assert "gene sequence has 'M'" in msg

    def test_warns_on_out_of_range_position(self, caplog) -> None:
        # _AA_SEQ has length 6; pos=10 (1-based) → index 9 → out of range.
        # Must warn but NOT raise — the rule is simply skipped.
        import logging
        rows = [_rule('gX', position=10, reference='M')]
        with caplog.at_level(logging.WARNING, logger='respro.db.project.rules'):
            _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)
        assert any('out of range' in r.message for r in caplog.records)

    def test_warns_on_out_of_range_0based_position(self, caplog) -> None:
        # _AA_SEQ has length 6; pos=6 (0-based) → out of range.
        # Must warn but NOT raise.
        import logging
        rows = [_rule('gX', position=6, reference='M')]
        with caplog.at_level(logging.WARNING, logger='respro.db.project.rules'):
            _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=0)
        assert any('out of range' in r.message for r in caplog.records)

    def test_reports_all_mismatches_in_single_error(self) -> None:
        rows = [
            _rule('gX', position=1, reference='Z'),  # mismatch 1
            _rule('gX', position=2, reference='Z'),  # mismatch 2
            _rule('gX', position=3, reference='Z'),  # mismatch 3
        ]
        with pytest.raises(ValueError) as exc_info:
            _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)
        msg = str(exc_info.value)
        assert '3 mismatch(es)' in msg

    def test_skips_rows_without_ref_aa(self) -> None:
        # Row has no 'reference'/'ref_aa' key — must not raise
        rows = [
            {'gene': 'gX', 'position': '1', 'mutation': 'E', 'antiviral': 'DrugX'},
        ]
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)

    def test_skips_rows_with_gene_not_in_lookup(self) -> None:
        rows = [_rule('unknown_gene', position=1, reference='Z')]
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)

    def test_skips_rows_where_gene_has_no_aa_sequence(self) -> None:
        rows = [_rule('gX', position=1, reference='M')]
        _validate_reference_amino_acids(rows, _genes('gX', aa_seq=''), coord_base=1)

    def test_skips_rows_with_non_integer_position(self) -> None:
        rows = [_rule('gX', position='n/a', reference='Z')]
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)

    def test_case_insensitive_comparison(self) -> None:
        # Rule ref in lowercase, aa_seq in uppercase — must match
        rows = [_rule('gX', position=1, reference='m')]
        _validate_reference_amino_acids(rows, _genes('gX', _AA_SEQ), coord_base=1)

    def test_uses_reference_identifier_to_pick_correct_gene(self) -> None:
        # Two genes under the same name; ACC1 has wrong AA, ACC2 has correct one
        genes_by_name = {
            'gX': [
                _gene_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', gene_id=1),
                _gene_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', gene_id=2),
            ]
        }
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC2')]
        # Should pass: ACC2 gene has 'M' at position 1 (1-based)
        _validate_reference_amino_acids(rows, genes_by_name, coord_base=1)

    def test_raises_for_wrong_gene_via_reference_identifier(self) -> None:
        genes_by_name = {
            'gX': [
                _gene_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', gene_id=1),
                _gene_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', gene_id=2),
            ]
        }
        # Pointing to ACC1 which has 'Q' at pos 1, not 'M'
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC1')]
        with pytest.raises(ValueError, match='mismatch'):
            _validate_reference_amino_acids(rows, genes_by_name, coord_base=1)


class TestSchemaCombinedRuleSets:
    def test_schema_includes_future_combined_rule_tables(self, tmp_path) -> None:
        db_path = tmp_path / 'schema.db'
        conn = create_schema(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'resistance_rule_set' in tables
        assert 'resistance_rule_set_member' in tables


class TestComboRuleParsing:
    """Verify that rule_group rows in the TSV are loaded as combination rule sets."""

    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        # TINY_REF_SEQ starts ATG AAA … so AA[0]=M, AA[1]=K, AA[5]=P (1-based: pos 2=K, pos 6=P)
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'genes': [{'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_combo_rows_are_loaded_as_rule_sets(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        # Two combo rows + one single row. Positions are 1-based (K=pos2, P=pos6 in TINY_REF_SEQ).
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\t
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_KE_PV
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_KE_PV
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        set_count = conn.execute('SELECT COUNT(*) FROM resistance_rule_set').fetchone()[0]
        member_count = conn.execute('SELECT COUNT(*) FROM resistance_rule_set_member').fetchone()[0]
        conn.close()

        assert single_count == 1       # the row without rule_group
        assert set_count == 1          # one combo rule set
        assert member_count == 2       # two members

    def test_combo_group_with_fewer_than_two_members_raises(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_solo
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='at least 2 member'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, drug_info=False)

    def test_combo_group_inconsistent_drug_raises(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tconflict_group
            gag\ttiny_ref\t6\tP\tV\tDrugB\tresistant\tconflict_group
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='inconsistent antiviral'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, drug_info=False)

    def test_duplicate_combo_group_is_skipped_on_reinit(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_KE_PV
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_KE_PV
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, drug_info=False)
        # init-add with the same TSV must not create a second copy
        from respro.db.project import add_to_project
        add_to_project(db_path=db, rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        set_count = conn.execute('SELECT COUNT(*) FROM resistance_rule_set').fetchone()[0]
        conn.close()
        assert set_count == 1

    def test_single_rule_publication_doi_gets_https_prefix(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        publication = conn.execute('SELECT publication FROM resistance_rule').fetchone()[0]
        conn.close()
        assert publication == 'https://doi.org/10.1086/590668'

    def test_single_rule_publication_already_https_is_unchanged(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\thttps://doi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        publication = conn.execute('SELECT publication FROM resistance_rule').fetchone()[0]
        conn.close()
        assert publication == 'https://doi.org/10.1086/590668'

    def test_single_rule_publication_pmid_is_unchanged(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tPMID:12345678
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        publication = conn.execute('SELECT publication FROM resistance_rule').fetchone()[0]
        conn.close()
        assert publication == 'PMID:12345678'

    def test_combo_rule_set_publication_doi_gets_https_prefix(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_KE_PV\tdoi.org/10.1086/590668
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_KE_PV\t
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        publication = conn.execute('SELECT publication FROM resistance_rule_set').fetchone()[0]
        conn.close()
        assert publication == 'https://doi.org/10.1086/590668'


class TestNcbiProteinAccession:
    def test_accepts_refseq_protein_accessions(self) -> None:
        assert _is_ncbi_protein_accession('YP_009137097.1') is True
        assert _is_ncbi_protein_accession('NP_123456.2') is True

    def test_accepts_genbank_style_accessions(self) -> None:
        assert _is_ncbi_protein_accession('AAA12345.1') is True

    def test_rejects_missing_version_suffix(self) -> None:
        assert _is_ncbi_protein_accession('YP_009137097') is False

    def test_rejects_non_accession_tokens(self) -> None:
        assert _is_ncbi_protein_accession('thymidine_kinase') is False
        assert _is_ncbi_protein_accession('YP-009137097.1') is False


class TestIc50ParsingAndAggregation:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'genes': [{'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_accepts_ic50_alias_columns_and_extracts_numeric(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tfold_ic50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t>10x
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        ic50 = conn.execute('SELECT ic50 FROM resistance_rule').fetchone()[0]
        conn.close()
        assert ic50 == '10'

    def test_allows_empty_or_none_ic50(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t
            gag\ttiny_ref\t6\tP\tV\tDrugA\tNone
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        values = [row[0] for row in conn.execute('SELECT ic50 FROM resistance_rule ORDER BY id').fetchall()]
        conn.close()
        assert values == ['', '']

    def test_raises_on_non_numeric_ic50(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\tnot_numeric
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='invalid ic50 value'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

    def test_combo_group_uses_highest_numeric_ic50(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group\tfold_ic50
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_1\t2.0x
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_1\t8.5 fold
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        ic50 = conn.execute('SELECT ic50 FROM resistance_rule_set').fetchone()[0]
        conn.close()
        assert ic50 == '8.5'

    def test_rejects_multiple_ic50_alias_columns(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tic50\tfold_ic50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t4\t5
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='only one IC50 column is allowed'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)


class TestPhenotypeNormalization:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'genes': [{'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_normalizes_supported_phenotype_inputs(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugR\tTrue
            gag\ttiny_ref\t3\tA\tV\tDrugI\tinterm
            gag\ttiny_ref\t4\tF\tL\tDrugS\tSENSI
            gag\ttiny_ref\t5\tG\tA\tDrugU\tNone
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT d.name AS drug_name, rr.phenotype '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()
        observed = {row['drug_name']: row['phenotype'] for row in rows}
        assert observed == {
            'drugi': 'intermediate',
            'drugs': 'sensitive',
            'drugr': 'resistant',
            'drugu': 'unknown',
        }

    def test_allows_phenotype_and_clinical_phenotype_when_consistent(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tclinical_phenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tres\tR
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        phenotype, clinical = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule'
        ).fetchone()
        conn.close()
        assert phenotype == 'resistant'
        assert clinical == 'resistant'

    def test_allows_distinct_phenotype_and_clinical_phenotype(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tclinical_phenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tres\ts
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        phenotype, clinical = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule'
        ).fetchone()
        conn.close()
        assert phenotype == 'resistant'
        assert clinical == 'sensitive'

    def test_rejects_ambiguous_deletion_tokens(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tF67del\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='unrecognised mutation'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

    def test_rejects_noop_single_rule(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tK\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='does not change reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

    def test_rejects_noop_combo_member(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tK\tDrugA\tresistant\tcombo_noop
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_noop
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='does not change reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

    def test_skips_single_rule_with_unsupported_amino_acid_token(self, tmp_path, tiny_genbank, caplog) -> None:
        import logging

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tK2Z\tDrugBad\tresistant
            gag\ttiny_ref\t6\tP\tV\tDrugGood\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.project.rules'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        drugs = {row[0] for row in conn.execute('SELECT name FROM drug').fetchall()}
        conn.close()

        assert count == 1
        assert drugs == {'druggood'}
        assert any('unsupported amino-acid tokens' in rec.message for rec in caplog.records)

    def test_skips_combo_group_with_unsupported_amino_acid_token(self, tmp_path, tiny_genbank, caplog) -> None:
        import logging

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\trule_group
            gag\ttiny_ref\t2\tK\tK2Z\tDrugA\tresistant\tcombo_bad
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_bad
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.project.rules'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

        conn = sqlite3.connect(str(db))
        combo_count = conn.execute('SELECT COUNT(*) FROM resistance_rule_set').fetchone()[0]
        conn.close()

        assert combo_count == 0
        assert any('unsupported amino-acid tokens' in rec.message for rec in caplog.records)


class TestGenbankTranslationQuality:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'genes': [{'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_init_fails_on_internal_stop_in_translation(self, tmp_path) -> None:
        gb = tmp_path / 'invalid_translation.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'genes': [
                    {
                        'gene': 'gag',
                        'protein': 'Gag',
                        'start': 1,
                        'end': 87,
                        'strand': '+',
                        'translation': 'MK*FGP',
                    }
                ],
            }
        ])

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))

        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='internal stop codon'):
            init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, drug_info=False)

    def test_raises_when_reference_identifier_is_missing(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\tposition\treference\tmutation\tantiviral
            gag\t2\tK\tE\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='missing required field reference_identifier'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

    def test_raises_when_reference_aa_is_missing(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\tmutation\tantiviral
            gag\ttiny_ref\t2\tE\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='missing required field reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, drug_info=False)

