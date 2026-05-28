"""
Tests for project initialization — coordinate base detection and reference AA validation.
"""

from __future__ import annotations

import logging
import sqlite3
import textwrap
from unittest.mock import patch

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from conftest import TINY_REF_SEQ, write_genbank

from respro.cli.init import init_project
from respro.core.alignment import load_cached_mappings, load_features_with_rules, sequence_checksum
from respro.db.features import _is_ncbi_protein_accession
from respro.db.models import is_internal_formula_component_drug_name
from respro.db.rules_import import (
    _detect_coordinate_base,
    _resolve_anchorless_deletion,
    _validate_reference_amino_acids,
)
from respro.db.rules_queries import list_rules_for_display
from respro.db.schema import create_schema, open_project_db
from respro.io.reference import load_features_for_reference

# Amino acid sequence used across tests:
#   index:  0  1  2  3  4  5
#   1-based: 1  2  3  4  5  6
#   AA:      M  K  A  F  G  P
_AA_SEQ = 'MKAFGP'

# Sequence where index 0 and 1 are identical (used for ambiguous tie tests).
#   index:  0  1  2  3  4  5
#   AA:      M  M  K  A  F  G
_AA_SEQ_TIE = 'MMKAFG'


def _features(feature_name: str, aa_seq: str, **kwargs) -> dict[str, list[dict]]:
    """Convenience wrapper — single-feature lookup dict."""
    return {feature_name: [_feature_row(feature_name, aa_seq, **kwargs)]}


def _feature_row(
    feature_name: str,
    aa_sequence: str,
    reference_name: str = 'ref1',
    reference_accession: str = 'ACC1',
    feature_id: int = 1,
    alias_rank: int = 0,
) -> dict:
    """Build a dict that mimics a sqlite3.Row for feature lookups."""
    return {
        'feature_id': feature_id,
        'feature_name': feature_name,
        'aa_sequence': aa_sequence,
        'reference_name': reference_name,
        'reference_accession': reference_accession,
        'alias_rank': alias_rank,
    }


def _rule(
    feature: str,
    position: int | str,
    reference: str,
    mutation: str = 'E',
    reference_identifier: str = '',
) -> dict[str, str]:
    """Build a minimal TSV row dict for a single resistance rule."""
    row = {
        'feature': feature,
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
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 1

    def test_detects_0based_when_all_positions_are_0indexed(self) -> None:
        # pos=0 → aa_seq[0]='M', pos=1 → aa_seq[1]='K'  (0-based)
        rows = [
            _rule('gX', position=0, reference='M'),
            _rule('gX', position=1, reference='K'),
        ]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 0

    def test_defaults_to_1based_when_no_verifiable_rows(self) -> None:
        # rows with no reference AA → nothing to verify
        rows = [
            {'feature': 'gX', 'position': '3', 'mutation': 'E', 'antiviral': 'DrugX'},
        ]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 1

    def test_defaults_to_1based_when_feature_not_in_lookup(self) -> None:
        rows = [_rule('unknown_feature', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 1

    def test_defaults_to_1based_when_feature_has_no_aa_sequence(self) -> None:
        rows = [_rule('gX', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _features('gX', aa_seq=''))
        assert result == 1

    def test_prefers_1based_when_majority_of_rows_match(self) -> None:
        # Two rules clearly 1-based, one ambiguous (skipped because feature missing)
        rows = [
            _rule('gX', position=1, reference='M'),   # 1-based match only
            _rule('gX', position=2, reference='K'),   # 1-based match only
        ]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 1

    def test_prefers_0based_when_majority_of_rows_match(self) -> None:
        # Three 0-based rules, none matching 1-based
        rows = [
            _rule('gX', position=0, reference='M'),
            _rule('gX', position=1, reference='K'),
            _rule('gX', position=2, reference='A'),
        ]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 0

    def test_warns_and_returns_1based_on_tie(self) -> None:
        # _AA_SEQ_TIE = 'MMKAFG': pos=1 with ref='M' matches BOTH systems
        #   1-based: aa_seq[0]='M' ✓   0-based: aa_seq[1]='M' ✓
        rows = [_rule('gX', position=1, reference='M')]
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ_TIE))
        assert result == 1

    def test_raises_when_ref_aa_matches_neither_system(self) -> None:
        # pos=3 with ref='Z' (not in _AA_SEQ at any position near 3)
        #   1-based: aa_seq[2]='A' ≠ 'Z'   0-based: aa_seq[3]='F' ≠ 'Z'
        rows = [_rule('gX', position=3, reference='Z')]
        with pytest.raises(ValueError, match='coordinate system could not be determined'):
            _detect_coordinate_base(rows, _features('gX', _AA_SEQ))

    def test_uses_reference_identifier_to_resolve_correct_feature(self) -> None:
        # Two features with same name but different references — pick ACC2
        features_by_name = {
            'gX': [
                _feature_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', feature_id=1),
                _feature_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', feature_id=2),
            ]
        }
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC2')]
        result = _detect_coordinate_base(rows, features_by_name)
        assert result == 1

    def test_skips_row_with_non_integer_position(self) -> None:
        rows = [_rule('gX', position='n/a', reference='M')]
        # non-integer position → verifiable=0 → defaults to 1-based
        result = _detect_coordinate_base(rows, _features('gX', _AA_SEQ))
        assert result == 1

    def test_ignores_rows_with_unknown_reference_identifier(self) -> None:
        rows = [_rule('gX', position=1, reference='M', reference_identifier='UNKNOWN_REF')]
        result = _detect_coordinate_base(
            rows,
            _features('gX', _AA_SEQ),
            allowed_reference_identifiers={'tiny_ref'},
        )
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
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)

    def test_passes_when_all_0based_rules_match(self) -> None:
        rows = [
            _rule('gX', position=0, reference='M'),  # aa_seq[0]='M'
            _rule('gX', position=2, reference='A'),  # aa_seq[2]='A'
            _rule('gX', position=5, reference='P'),  # aa_seq[5]='P'
        ]
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=0)

    def test_warns_and_returns_key_on_single_ref_aa_mismatch(self, caplog) -> None:

        # pos=1 (1-based) → aa_seq[0]='M', rule says 'K' → mismatch; must warn, not raise
        rows = [_rule('gX', position=1, reference='K')]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            mismatch_keys = _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)
        assert ('gX', '1', '', 'K') in mismatch_keys
        assert any('mismatch' in r.message.lower() for r in caplog.records)
        assert any("rule says 'K'" in r.message for r in caplog.records)
        assert any("feature sequence has 'M'" in r.message for r in caplog.records)

    def test_warns_on_out_of_range_position(self, caplog) -> None:
        # _AA_SEQ has length 6; pos=10 (1-based) → index 9 → out of range.
        # Must warn but NOT raise — the rule is simply skipped.
        import logging
        rows = [_rule('gX', position=10, reference='M')]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)
        assert any('out of range' in r.message for r in caplog.records)

    def test_warns_on_out_of_range_0based_position(self, caplog) -> None:
        # _AA_SEQ has length 6; pos=6 (0-based) → out of range.
        # Must warn but NOT raise.
        import logging
        rows = [_rule('gX', position=6, reference='M')]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=0)
        assert any('out of range' in r.message for r in caplog.records)

    def test_collects_all_mismatch_keys(self, caplog) -> None:
        import logging

        rows = [
            _rule('gX', position=1, reference='Z'),  # mismatch 1
            _rule('gX', position=2, reference='Z'),  # mismatch 2
            _rule('gX', position=3, reference='Z'),  # mismatch 3
        ]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            mismatch_keys = _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)
        assert len(mismatch_keys) == 3
        assert any('3' in r.message and 'mismatch' in r.message.lower() for r in caplog.records)

    def test_skips_rows_without_ref_aa(self) -> None:
        # Row has no 'reference'/'ref_aa' key — must not raise
        rows = [
            {'feature': 'gX', 'position': '1', 'mutation': 'E', 'antiviral': 'DrugX'},
        ]
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)

    def test_skips_rows_with_feature_not_in_lookup(self) -> None:
        rows = [_rule('unknown_feature', position=1, reference='Z')]
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)

    def test_skips_rows_where_feature_has_no_aa_sequence(self) -> None:
        rows = [_rule('gX', position=1, reference='M')]
        _validate_reference_amino_acids(rows, _features('gX', aa_seq=''), coord_base=1)

    def test_skips_rows_with_non_integer_position(self) -> None:
        rows = [_rule('gX', position='n/a', reference='Z')]
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)

    def test_case_insensitive_comparison(self) -> None:
        # Rule ref in lowercase, aa_seq in uppercase — must match
        rows = [_rule('gX', position=1, reference='m')]
        _validate_reference_amino_acids(rows, _features('gX', _AA_SEQ), coord_base=1)

    def test_uses_reference_identifier_to_pick_correct_feature(self) -> None:
        # Two features under the same name; ACC1 has wrong AA, ACC2 has correct one
        features_by_name = {
            'gX': [
                _feature_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', feature_id=1),
                _feature_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', feature_id=2),
            ]
        }
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC2')]
        # Should pass: ACC2 feature has 'M' at position 1 (1-based)
        _validate_reference_amino_acids(rows, features_by_name, coord_base=1)

    def test_warns_and_returns_key_for_wrong_feature_via_reference_identifier(self, caplog) -> None:
        import logging

        features_by_name = {
            'gX': [
                _feature_row('gX', 'QQQQQQ', reference_name='ref1', reference_accession='ACC1', feature_id=1),
                _feature_row('gX', _AA_SEQ,  reference_name='ref2', reference_accession='ACC2', feature_id=2),
            ]
        }
        # Pointing to ACC1 which has 'Q' at pos 1, not 'M' — must warn, not raise
        rows = [_rule('gX', position=1, reference='M', reference_identifier='ACC1')]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            mismatch_keys = _validate_reference_amino_acids(rows, features_by_name, coord_base=1)
        assert ('gX', '1', 'ACC1', 'M') in mismatch_keys
        assert any('mismatch' in r.message.lower() for r in caplog.records)

    def test_ignores_rows_with_unknown_reference_identifier(self, caplog) -> None:
        import logging

        rows = [_rule('gX', position=1, reference='Z', reference_identifier='UNKNOWN_REF')]
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            mismatch_keys = _validate_reference_amino_acids(
                rows,
                _features('gX', _AA_SEQ),
                coord_base=1,
                allowed_reference_identifiers={'tiny_ref'},
            )

        assert mismatch_keys == set()
        assert not any('mismatch' in r.message.lower() for r in caplog.records)
        assert not any('out of range' in r.message.lower() for r in caplog.records)


class TestSchemaFormulaRules:
    def test_schema_includes_formula_tables_but_not_legacy_combo_tables(self, tmp_path) -> None:
        db_path = tmp_path / 'schema.db'
        conn = create_schema(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        assert 'resistance_formula_rule' in tables
        assert 'resistance_formula_rule_member' in tables
        assert 'resistance_formula_rule_publication' in tables
        assert 'resistance_rule_set' not in tables
        assert 'resistance_rule_set_member' not in tables
        assert 'rule_set_publication' not in tables

    def test_schema_rejects_duplicate_formula_members_within_one_formula(self, tmp_path) -> None:
        db_path = tmp_path / 'schema_unique.db'
        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version) VALUES (?, ?)',
            ('test', 1),
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
            (1, 'druga'),
        )
        conn.execute(
            'INSERT INTO resistance_rule (feature_id, drug_id, external_id, position, reference, mutation) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (1, 1, 'mut_k1e', 1, 'K', 'E'),
        )
        conn.execute(
            'INSERT INTO resistance_formula_rule '
            '(drug_id, formula_id, normalized_expression, phenotype) VALUES (?, ?, ?, ?)',
            (1, 'formula_1', 'mut_k1e', 'resistant'),
        )
        conn.execute(
            'INSERT INTO resistance_formula_rule_member (formula_rule_id, rule_id) VALUES (?, ?)',
            (1, 1),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                'INSERT INTO resistance_formula_rule_member (formula_rule_id, rule_id) VALUES (?, ?)',
                (1, 1),
            )

        conn.close()


class TestComboRuleParsing:
    """Verify grouped atomic-rule behavior for group_id/member_id workflows."""

    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        # TINY_REF_SEQ starts ATG AAA … so AA[0]=M, AA[1]=K, AA[5]=P (1-based: pos 2=K, pos 6=P)
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_grouped_rows_without_formula_still_load_atomic_rules(self, tmp_path, tiny_genbank, caplog) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tgroup_1\tmut_A
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tgroup_1\tmut_B
            gag\ttiny_ref\t4\tF\tL\tDrugA\tresistant\t\t
        """))
        db = tmp_path / 'proj.db'
        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        formula_count = conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0]
        member_count = conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0]
        conn.close()

        assert single_count == 3
        assert formula_count == 0
        assert member_count == 0
        assert any('combinatorial rules are ignored' in rec.message for rec in caplog.records)

    def test_grouped_rows_require_member_id_even_without_formula_tsv(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tgroup_1
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='missing required field member_id'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

    def test_formula_member_placeholder_rows_are_hidden_from_rule_display(self, tmp_path, tiny_genbank) -> None:
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tunknown\tgroup_1\tmut_A
            gag\ttiny_ref\t6\tP\tV\tunknown\tgroup_1\tmut_B
        """))
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(textwrap.dedent("""\
            group_id\tantiviral\texpression\tphenotype
            group_1\tDrugA\tmut_A AND mut_B\tresistant
        """))

        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=rules_tsv,
            formula_rules_tsv=formula_tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = list_rules_for_display(conn)
        stored_drugs = [row['name'] for row in conn.execute('SELECT name FROM drug ORDER BY name').fetchall()]
        conn.close()

        assert any(is_internal_formula_component_drug_name(name) for name in stored_drugs)
        assert rows == []

    def test_formula_member_rows_without_group_id_are_allowed(self, tmp_path, tiny_genbank) -> None:
        rules_tsv = tmp_path / 'rules.tsv'
        rules_tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tphenotype\tmember_id
            gag\ttiny_ref\t2\tK\tE\tunknown\tmut_A
            gag\ttiny_ref\t6\tP\tV\tunknown\tmut_B
        """))
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(textwrap.dedent("""\
            group_id\tantiviral\texpression\tphenotype
            group_1\tDrugA\tmut_A AND mut_B\tresistant
        """))

        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=rules_tsv,
            formula_rules_tsv=formula_tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        formula_count = conn.execute('SELECT COUNT(*) FROM resistance_formula_rule').fetchone()[0]
        member_count = conn.execute('SELECT COUNT(*) FROM resistance_formula_rule_member').fetchone()[0]
        conn.close()

        assert single_count == 2
        assert formula_count == 1
        assert member_count == 2

    def test_member_ids_must_be_unique(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tgroup_1\tmut_dup
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tgroup_2\tmut_dup
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='duplicate atomic rule ids'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

    def test_identical_duplicate_member_id_rows_are_skipped_with_warning(self, tmp_path, tiny_genbank, caplog) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tgroup_1\tmut_same
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tgroup_2\tmut_same
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute(
            'SELECT COUNT(*) FROM resistance_rule WHERE external_id = ?',
            ('mut_same',),
        ).fetchone()[0]
        conn.close()

        assert count == 1
        assert any('duplicate member_id with identical atomic definition' in rec.message for rec in caplog.records)

    def test_single_mixed_anchor_change_insertion_splits_into_atomic_rows(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tK2EW\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        rows = conn.execute(
            'SELECT reference, mutation FROM resistance_rule ORDER BY reference, mutation'
        ).fetchall()
        conn.close()

        assert single_count == 2
        assert [(m['reference'], m['mutation']) for m in rows] == [('K', 'E'), ('K', 'KW')]

    def test_single_mixed_anchor_change_deletion_splits_into_atomic_rows(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t5\tGP\tGP5A\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        rows = conn.execute(
            'SELECT reference, mutation FROM resistance_rule ORDER BY reference, mutation'
        ).fetchall()
        conn.close()

        assert single_count == 2
        assert [(m['reference'], m['mutation']) for m in rows] == [('G', 'A'), ('GP', 'G')]

    def test_single_rule_publication_doi_is_stored_in_publication_table(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT p.doi FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == '10.1086/590668'

    def test_drug_names_are_canonicalized_case_insensitively(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t2\tK\tE\tDrugA
            gag\ttiny_ref\t6\tP\tV\tdruga
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        rows = conn.execute('SELECT name FROM drug ORDER BY id').fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == 'druga'

    def test_single_rule_publication_https_doi_is_stored(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\thttps://doi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT p.doi FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == '10.1086/590668'

    def test_doi_publication_stores_resolved_pmid_when_additional_info_is_enabled(
        self,
        tmp_path,
        tiny_genbank,
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'

        with (
            patch('respro.db._rules_publication.fetch_pubmed_id_for_doi', return_value='12345678'),
            patch(
                'respro.db._rules_publication.fetch_pubmed_metadata',
                return_value={'title': 'PubMed title', 'doi': '10.1086/590668'},
            ),
            patch(
                'respro.db._rules_publication.fetch_publication_metadata',
                return_value={'title': 'CrossRef title'},
            ),
        ):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=True)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT p.doi, p.pubmed_id FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == '10.1086/590668'
        assert row[1] == '12345678'

    def test_doi_lookup_logs_concise_success_messages(self, tmp_path, tiny_genbank, caplog) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'

        with (
            caplog.at_level(logging.INFO, logger='respro.db'),
            patch(
                'respro.db._rules_publication.fetch_pubmed_id_for_doi',
                return_value='12345678',
            ),
            patch(
                'respro.db._rules_publication.fetch_pubmed_metadata',
                return_value={'title': '', 'doi': '10.1086/590668'},
            ),
            patch(
                'respro.db._rules_publication.fetch_publication_metadata',
                return_value={'title': 'A resolved CrossRef title'},
            ),
        ):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=True)

        assert any(
            'Resolved DOI 10.1086/590668 → PMID:12345678 and title successfully fetched via CrossRef'
            in rec.message
            for rec in caplog.records
        )
        assert not any('Resolving DOI:' in rec.message for rec in caplog.records)
        assert not any('Could not resolve DOI:' in rec.message for rec in caplog.records)

    def test_pmid_lookup_logs_doi_and_crossref_title_success(
        self,
        tmp_path,
        tiny_genbank,
        caplog,
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tPMID:3034575
        """))
        db = tmp_path / 'proj.db'

        with (
            caplog.at_level(logging.INFO, logger='respro.db'),
            patch(
                'respro.db._rules_publication.fetch_pubmed_metadata',
                return_value={'title': 'PubMed title', 'doi': 'doi.org/10.1002/j.1460-2075.1987.tb04735.x'},
            ),
            patch(
                'respro.db._rules_publication.fetch_publication_metadata',
                return_value={'title': 'CrossRef title'},
            ),
        ):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=True)

        assert any(
            'Resolved PMID 3034575 → DOI 10.1002/j.1460-2075.1987.tb04735.x and title '
            'successfully fetched via CrossRef' in rec.message
            for rec in caplog.records
        )

    def test_publication_lookup_failures_are_reported_once_after_import(
        self,
        tmp_path,
        tiny_genbank,
        caplog,
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1086/590668
            gag\ttiny_ref\t6\tP\tV\tDrugA\tPMID:3034575
        """))
        db = tmp_path / 'proj.db'

        with (
            caplog.at_level(logging.WARNING, logger='respro.db.rules_import'),
            patch('respro.db._rules_publication.fetch_pubmed_id_for_doi', return_value=None),
            patch('respro.db._rules_publication.fetch_pubmed_metadata', return_value=None),
            patch('respro.db._rules_publication.fetch_publication_metadata', return_value=None),
        ):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=True)

        warning_messages = [
            rec.message for rec in caplog.records if 'publication metadata lookup(s) failed' in rec.message
        ]
        assert len(warning_messages) == 1
        assert 'DOI 10.1086/590668 → identifier lookup failed' in warning_messages[0]
        assert 'PMID:3034575 → metadata lookup failed' in warning_messages[0]

    def test_doi_lookup_logs_no_pmid_found_with_crossref_success_without_warning(
        self,
        tmp_path,
        tiny_genbank,
        caplog,
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tdoi.org/10.1093/infdis/jiz577
        """))
        db = tmp_path / 'proj.db'

        with (
            caplog.at_level(logging.INFO, logger='respro.db'),
            patch('respro.db._rules_publication.fetch_pubmed_id_for_doi', return_value=None),
            patch(
                'respro.db._rules_publication.fetch_publication_metadata',
                return_value={'title': 'CrossRef title'},
            ),
        ):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=True)

        assert any(
            'Resolved DOI 10.1093/infdis/jiz577 → No PMID found and title successfully '
            'fetched via CrossRef' in rec.message
            for rec in caplog.records
        )
        assert not any('publication metadata lookup(s) failed' in rec.message for rec in caplog.records)

    def test_single_rule_publication_pmid_is_stored(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tPMID:12345678
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT p.pubmed_id, p.raw_input FROM publication p '
            'JOIN rule_publication rp ON rp.publication_id = p.id'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == '12345678'
        assert row[1] == 'PMID:12345678'

    def test_pmid_network_lookup_fires_only_once_per_unique_pmid(
        self, tmp_path, tiny_genbank
    ) -> None:
        # helper
        def _fake_fetch(pmid: str, timeout: int = 3) -> dict:
            nonlocal call_count
            call_count += 1
            return {'title': f'Title for {pmid}', 'doi': ''}
        # The same PMID appears on three rules; the NCBI lookup must fire exactly once.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tpublication
            gag\ttiny_ref\t2\tK\tE\tDrugA\tPMID:12345678
            gag\ttiny_ref\t4\tF\tL\tDrugA\tPMID:12345678
            gag\ttiny_ref\t6\tP\tV\tDrugA\tPMID:12345678
        """))
        db = tmp_path / 'proj.db'
        call_count = 0
        # Patch the name as it exists in rules.py's namespace.
        with patch('respro.db._rules_publication.fetch_pubmed_metadata', side_effect=_fake_fetch):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=True)

        assert call_count == 1  # one PMID → one network call, regardless of how many rules use it

    def test_formula_rule_publication_doi_is_stored(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant\tcombo_KE_PV\tmut_A
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_KE_PV\tmut_B
        """))
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(textwrap.dedent("""\
            group_id\tantiviral\texpression\tpublication
            combo_KE_PV\tDrugA\tmut_A AND mut_B\tdoi.org/10.1086/590668
        """))
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=tsv,
            formula_rules_tsv=formula_tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT p.doi FROM publication p '
            'JOIN resistance_formula_rule_publication frp ON frp.publication_id = p.id'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == '10.1086/590668'


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
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_accepts_ic50_alias_columns_and_extracts_numeric(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tfold_ic50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t>10x
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        ic50, fold_ic50 = conn.execute('SELECT ic50, fold_ic50 FROM resistance_rule').fetchone()
        conn.close()
        assert ic50 == ''
        assert fold_ic50 == '10'

    def test_allows_empty_or_none_ic50(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t
            gag\ttiny_ref\t6\tP\tV\tDrugA\tNone
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        values = [row[0] for row in conn.execute('SELECT ic50 FROM resistance_rule ORDER BY id').fetchall()]
        conn.close()
        assert values == ['', '']

    def test_raises_on_non_numeric_ic50(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\tnot_numeric
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='invalid ic50 value'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_formula_rule_uses_declared_numeric_ic50_values(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tE\tDrugA\tcombo_1\tmut_A
            gag\ttiny_ref\t6\tP\tV\tDrugA\tcombo_1\tmut_B
        """))
        formula_tsv = tmp_path / 'formula.tsv'
        formula_tsv.write_text(textwrap.dedent("""\
            group_id\tantiviral\texpression\tfold_ic50
            combo_1\tDrugA\tmut_A AND mut_B\t8.5 fold
        """))
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=tsv,
            formula_rules_tsv=formula_tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        ic50, fold_ic50 = conn.execute('SELECT ic50, fold_ic50 FROM resistance_formula_rule').fetchone()
        conn.close()
        assert ic50 == ''
        assert fold_ic50 == '8.5'

    def test_allows_ic50_and_fold_ic50_together(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tic50\tfold_ic50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t4\t5
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        ic50, fold_ic50 = conn.execute('SELECT ic50, fold_ic50 FROM resistance_rule').fetchone()
        conn.close()
        assert ic50 == '4'
        assert fold_ic50 == '5'

    def test_rejects_two_ic50_alias_columns(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tic50\tic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t4\t5
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='only one IC50 column is allowed'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_rejects_two_fold_ic50_alias_columns(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tfold_ic50\tfold_ic_50
            gag\ttiny_ref\t2\tK\tE\tDrugA\t4\t5
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='only one fold-IC50 column is allowed'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)


class TestPhenotypeNormalization:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_normalizes_supported_phenotype_inputs(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugR\tTrue
            gag\ttiny_ref\t3\tA\tV\tDrugI\tinterm
            gag\ttiny_ref\t4\tF\tL\tDrugS\tSENSI
            gag\ttiny_ref\t5\tG\tA\tDrugU\tNone
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

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
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tclinical_phenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tres\tR
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

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
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tclinical_phenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tres\ts
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        phenotype, clinical = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule'
        ).fetchone()
        conn.close()
        assert phenotype == 'resistant'
        assert clinical == 'sensitive'

    def test_missing_phenotype_columns_do_not_default_to_unknown(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t2\tK\tE\tDrugA
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        phenotype, clinical = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule'
        ).fetchone()
        conn.close()
        assert phenotype == ''
        assert clinical == ''

    def test_missing_phenotype_defaults_to_unknown_when_ruleset_has_phenotypes(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
            gag\ttiny_ref\t3\tA\tV\tDrugB\t
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule ORDER BY id'
        ).fetchall()
        conn.close()
        assert rows[0] == ('resistant', '')
        assert rows[1] == ('unknown', '')

    def test_missing_clinical_phenotype_defaults_to_unknown_when_ruleset_has_clinical_values(
        self,
        tmp_path,
        tiny_genbank,
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tclinical_phenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
            gag\ttiny_ref\t3\tA\tV\tDrugB\t
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            'SELECT phenotype, clinical_phenotype FROM resistance_rule ORDER BY id'
        ).fetchall()
        conn.close()
        assert rows[0] == ('', 'resistant')
        assert rows[1] == ('', 'unknown')

    def test_rejects_ambiguous_deletion_tokens(self, tmp_path, tiny_genbank) -> None:
        # F67del at position 2 with reference K: deleted block 'F' does not match
        # the feature sequence at position 2 (which is 'K') — resolution must fail.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tF67del\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='cannot resolve anchor for deletion'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_rejects_noop_single_rule(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tK\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='does not change reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_rejects_noop_combo_member(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tK\tDrugA\tresistant\tcombo_noop\tmut_A
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_noop\tmut_B
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='does not change reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_skips_single_rule_with_unsupported_amino_acid_token(self, tmp_path, tiny_genbank, caplog) -> None:
        import logging

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tK2Z\tDrugBad\tresistant
            gag\ttiny_ref\t6\tP\tV\tDrugGood\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        drugs = {row[0] for row in conn.execute('SELECT name FROM drug').fetchall()}
        conn.close()

        assert count == 1
        assert drugs == {'druggood'}
        assert any('unsupported amino-acid tokens' in rec.message for rec in caplog.records)

    def test_skips_single_rule_with_wildcard_like_any_token(self, tmp_path, tiny_genbank, caplog) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tany\tDrugBad\tresistant
            gag\ttiny_ref\t6\tP\tV\tDrugGood\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        drugs = {row[0] for row in conn.execute('SELECT name FROM drug').fetchall()}
        conn.close()

        assert count == 1
        assert drugs == {'druggood'}
        assert any('unsupported amino-acid tokens' in rec.message for rec in caplog.records)

    def test_skips_single_rule_with_wildcard_like_x_token(self, tmp_path, tiny_genbank, caplog) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tx\tDrugBad\tresistant
            gag\ttiny_ref\t6\tP\tV\tDrugGood\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

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
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tK\tK2Z\tDrugA\tresistant\tcombo_bad\tmut_bad
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_bad\tmut_ok
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        conn.close()

        assert single_count == 1
        assert any('unsupported amino-acid tokens' in rec.message for rec in caplog.records)


class TestRefAaMismatchSkip:
    """Rules whose reference AA does not match the GenBank sequence are skipped with a warning."""

    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_mismatching_ref_aa_skips_rule_with_warning(
        self, tmp_path, tiny_genbank, caplog
    ) -> None:
        # gag pos 2 (1-based) has ref AA 'K'; rule claims 'Z' → mismatch → skip
        # gag pos 2 with correct ref 'K' → loads fine
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t2\tZ\tE\tDrugBad
            gag\ttiny_ref\t2\tK\tE\tDrugGood
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        drugs = {row[0] for row in conn.execute('SELECT name FROM drug').fetchall()}
        conn.close()

        assert count == 1
        assert 'druggood' in drugs
        assert 'drugbad' not in drugs
        assert any('mismatch' in r.message.lower() for r in caplog.records)

    def test_mismatching_ref_aa_grouped_member_is_skipped_but_other_atomic_rules_load(
        self, tmp_path, tiny_genbank, caplog
    ) -> None:
        import logging

        # pos 2 has 'K', not 'Z' → mismatching row is skipped while valid atomic rows still load.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tgroup_id\tmember_id
            gag\ttiny_ref\t2\tZ\tE\tDrugA\tresistant\tcombo_bad\tmut_bad
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant\tcombo_bad\tmut_ok
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        single_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        conn.close()

        assert single_count == 1
        assert any('mismatch' in r.message.lower() for r in caplog.records)

    def test_correct_ref_aa_loads_in_presence_of_mismatching_sibling(
        self, tmp_path, tiny_genbank
    ) -> None:
        # One mismatching rule and one correct rule at the same position — only the correct one loads
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t2\tZ\tE\tDrugBad
            gag\ttiny_ref\t6\tP\tV\tDrugGood
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        drugs = {row[0] for row in conn.execute('SELECT name FROM drug').fetchall()}
        conn.close()

        assert count == 1
        assert 'druggood' in drugs


class TestMissingFeatureWarnings:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_missing_feature_warning_includes_reference_identifier(
        self, tmp_path, tiny_genbank, caplog
    ) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            pol\ttiny_ref\t2\tK\tE\tDrugBad\tresistant
            UL89\tref_ul89\t6\tP\tV\tDrugBad\tresistant
            gag\ttiny_ref\t2\tK\tE\tDrugGood\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv,
                         additional_info=False)

        warning_messages = {
            record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        }
        feature_warnings = [m for m in warning_messages if 'feature(s) not found' in m]
        ref_warnings = [m for m in warning_messages if 'reference(s) not provided' in m]

        assert feature_warnings, 'Expected feature-not-found warning'
        assert "row 2: feature 'pol', reference_identifier 'tiny_ref'" in feature_warnings[0]
        assert 'UL89' not in feature_warnings[0]

        assert ref_warnings, 'Expected references-not-provided warning'
        assert "'ref_ul89'" in ref_warnings[0]


class TestGenbankAliasFallbacks:
    def test_gene_qualifier_is_used_for_feature_name(self, tmp_path) -> None:
        """CDS with standard GenBank gene qualifier must be matched by rules TSV feature column."""
        gb = tmp_path / 'gene_qualifier.gb'
        record = SeqRecord(Seq('GCT' * 40), id='tiny_ref', name='tiny_ref', description='')
        record.annotations['molecule_type'] = 'DNA'
        record.annotations['accessions'] = ['tiny_ref']
        record.features = [
            SeqFeature(
                FeatureLocation(0, 36, strand=1),
                type='CDS',
                qualifiers={
                    'gene': ['UL23'],
                    'product': ['thymidine kinase'],
                    'codon_start': ['1'],
                },
            )
        ]
        with open(gb, 'w') as handle:
            SeqIO.write([record], handle, 'genbank')

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            UL23\ttiny_ref\t2\tA\tV\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = open_project_db(db)
        rule_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        feature_name = conn.execute('SELECT name FROM feature').fetchone()[0]
        conn.close()

        assert feature_name == 'UL23'
        assert rule_count == 1

    def test_compound_cds_persists_segments_and_populates_loaders(self, tmp_path) -> None:
        gb = tmp_path / 'compound_product_only.gb'

        record = SeqRecord(Seq('GCT' * 100), id='tiny_ref', name='tiny_ref', description='')
        record.annotations['molecule_type'] = 'DNA'
        record.annotations['accessions'] = ['tiny_ref']
        record.features = [
            SeqFeature(
                CompoundLocation(
                    [
                        FeatureLocation(0, 18, strand=1),
                        FeatureLocation(60, 78, strand=1),
                    ]
                ),
                type='CDS',
                qualifiers={
                    'gene': ['split_pol'],
                    'product': ['DNA polymerase'],
                    'codon_start': ['1'],
                },
            )
        ]
        with open(gb, 'w') as handle:
            SeqIO.write([record], handle, 'genbank')

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            split_pol\ttiny_ref\t2\tA\tV\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'

        init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = open_project_db(db)
        conn.row_factory = sqlite3.Row
        feature_row = conn.execute(
            'SELECT id, start, end FROM feature WHERE name = ?',
            ('split_pol',),
        ).fetchone()
        assert feature_row is not None

        segments = conn.execute(
            'SELECT segment_index, start, end FROM feature_segment WHERE feature_id = ? ORDER BY segment_index',
            (feature_row['id'],),
        ).fetchall()
        assert [
            (row['segment_index'], row['start'], row['end'])
            for row in segments
        ] == [(0, 0, 18), (1, 60, 78)]

        ref_id = conn.execute(
            'SELECT id FROM reference WHERE name = ?',
            ('tiny_ref',),
        ).fetchone()['id']
        by_reference = load_features_for_reference(conn, ref_id)
        assert len(by_reference) == 1
        assert len(by_reference[0].segments) == 2

        with_rules = load_features_with_rules(conn, ref_id)
        assert len(with_rules) == 1
        assert len(with_rules[0].segments) == 2

        query_sequence = 'GCT' * 100
        checksum = sequence_checksum(query_sequence)
        conn.execute(
            'INSERT INTO query_reference (name, sequence, length, checksum) VALUES (?, ?, ?, ?)',
            ('query', query_sequence, len(query_sequence), checksum),
        )
        query_ref_id = conn.execute(
            'SELECT id FROM query_reference WHERE checksum = ?',
            (checksum,),
        ).fetchone()['id']
        conn.execute(
            'INSERT INTO query_feature_mapping '
            '(query_ref_id, feature_id, identity, cds_coverage, query_coverage, query_start, '
            'query_end, strand, cigar) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (query_ref_id, feature_row['id'], 1.0, 1.0, 1.0, 0, 36, '+', '36M'),
        )
        conn.commit()

        cached_matches = load_cached_mappings(conn, checksum)
        assert cached_matches is not None
        assert len(cached_matches) == 1
        assert len(cached_matches[0].feature.segments) == 2
        conn.close()

    def test_negative_strand_compound_cds_persists_segments(self, tmp_path) -> None:
        gb = tmp_path / 'compound_negative.gb'

        record = SeqRecord(Seq('GCT' * 120), id='tiny_ref', name='tiny_ref', description='')
        record.annotations['molecule_type'] = 'DNA'
        record.annotations['accessions'] = ['tiny_ref']
        record.features = [
            SeqFeature(
                CompoundLocation(
                    [
                        FeatureLocation(30, 48, strand=-1),
                        FeatureLocation(90, 108, strand=-1),
                    ]
                ),
                type='CDS',
                qualifiers={
                    'gene': ['split_neg'],
                    'product': ['DNA polymerase'],
                    'codon_start': ['1'],
                },
            )
        ]
        with open(gb, 'w') as handle:
            SeqIO.write([record], handle, 'genbank')

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            split_neg\ttiny_ref\t2\tS\tA\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'

        init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = open_project_db(db)
        conn.row_factory = sqlite3.Row
        feature_row = conn.execute(
            'SELECT id, strand, start, end FROM feature WHERE name = ?',
            ('split_neg',),
        ).fetchone()
        assert feature_row is not None
        assert feature_row['strand'] == '-'
        assert (feature_row['start'], feature_row['end']) == (30, 108)

        segments = conn.execute(
            'SELECT segment_index, start, end FROM feature_segment WHERE feature_id = ? ORDER BY segment_index',
            (feature_row['id'],),
        ).fetchall()
        conn.close()

        assert [
            (row['segment_index'], row['start'], row['end'])
            for row in segments
        ] == [(0, 30, 48), (1, 90, 108)]


class TestFeatureSegmentMigration:
    def test_open_project_db_backfills_feature_segment_rows_for_legacy_features(self, tmp_path) -> None:
        db_path = tmp_path / 'legacy_project.db'
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
            (1, 'gag', 11, 41, '+'),
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
        conn.close()

        migrated_conn = open_project_db(db_path)
        migrated_conn.row_factory = sqlite3.Row
        segments = migrated_conn.execute(
            'SELECT feature_id, segment_index, start, end FROM feature_segment WHERE feature_id = 1 '
            'ORDER BY segment_index'
        ).fetchall()
        migrated_conn.close()

        assert [
            (row['feature_id'], row['segment_index'], row['start'], row['end'])
            for row in segments
        ] == [(1, 0, 11, 41)]

    def test_product_only_cds_is_accepted_for_rule_matching(self, tmp_path) -> None:
        gb = tmp_path / 'product_only.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [
                    {
                        'product': 'DNA polymerase',
                        'start': 1,
                        'end': 87,
                        'strand': '+',
                    }
                ],
            }
        ])

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            DNA polymerase\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'

        init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        feature_name, protein_name = conn.execute(
            'SELECT name, protein FROM feature'
        ).fetchone()
        rule_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        conn.close()

        assert feature_name == 'DNA polymerase'
        assert protein_name == 'DNA polymerase'
        assert rule_count == 1

    def test_rule_feature_can_match_product_alias_when_feature_name_differs(
        self, tmp_path, caplog
    ) -> None:
        gb = tmp_path / 'alias.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [
                    {
                        'feature': 'UL30',
                        'protein': 'DNA polymerase',
                        'start': 1,
                        'end': 87,
                        'strand': '+',
                    }
                ],
            }
        ])

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            DNA polymerase\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        stored_feature = conn.execute('SELECT name FROM feature').fetchone()[0]
        rule_count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        conn.close()

        assert stored_feature == 'UL30'
        assert rule_count == 1
        assert not any('feature(s) not found in GenBank annotations' in rec.message for rec in caplog.records)

    def test_canonical_feature_name_match_wins_over_alias_match(self, tmp_path, caplog) -> None:
        sequence = TINY_REF_SEQ + TINY_REF_SEQ
        gb = tmp_path / 'canonical_vs_alias.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': sequence,
                'features': [
                    {
                        'feature': 'DNA polymerase',
                        'protein': 'Polymerase alpha',
                        'translation': 'MKAAAAAAAAAAAAAAAAAAAAAAAAAAA',
                        'start': 1,
                        'end': 87,
                        'strand': '+',
                    },
                    {
                        'feature': 'UL30',
                        'protein': 'DNA polymerase',
                        'translation': 'MKAAAAAAAAAAAAAAAAAAAAAAAAAAA',
                        'start': 88,
                        'end': 174,
                        'strand': '+',
                    },
                ],
            }
        ])

        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            DNA polymerase\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'

        with caplog.at_level(logging.WARNING, logger='respro.db.rules_import'):
            init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        matched_feature = conn.execute(
            'SELECT g.name FROM resistance_rule rr JOIN feature g ON g.id = rr.feature_id'
        ).fetchone()[0]
        conn.close()

        assert matched_feature == 'DNA polymerase'
        assert not any('feature(s) not found in GenBank annotations' in rec.message for rec in caplog.records)


class TestGenbankTranslationQuality:
    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
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
                'features': [
                    {
                        'feature': 'gag',
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
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))

        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='internal stop codon'):
            init_project(db_path=db, name='test', genbank_paths=[gb], rules_tsv=tsv, additional_info=False)

    def test_raises_when_reference_identifier_is_missing(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\tposition\treference\tmutation\tantiviral
            gag\t2\tK\tE\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='missing required field reference_identifier'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)

    def test_raises_when_reference_aa_is_missing(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\tmutation\tantiviral
            gag\ttiny_ref\t2\tE\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='missing required field reference'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank], rules_tsv=tsv, additional_info=False)


# ── TINY_REF_SEQ AA sequence (1-based) ────────────────────────────────────────
# Translated from TINY_REF_SEQ (feature positions 1-87, + strand, 28 AAs + stop):
#   pos: 1   2  3  4  5  6  7  8  9  10 11 12 13 14 15 16 17 18 19 20 ...
#   AA:  M   K  A  F  G  P  K  F  G  P  K  A  F  G  P  K  F  G  P  K  ...
# (repeating MKAFGP / KFGP pattern)


class TestResolveAnchorlessDeletion:
    """Unit tests for _resolve_anchorless_deletion."""

    def test_single_aa_deletion_returns_canonical(self) -> None:
        # pos 3 (1-based) = A; anchor at pos 2 = K → canonical KA2K
        aa_seq = 'MKAFGP'
        result = _resolve_anchorless_deletion('A', 2, aa_seq)  # 0-based pos 2 = 1-based 3
        assert result is not None
        anchor_idx, anchor_aa, canonical = result
        assert anchor_idx == 1
        assert anchor_aa == 'K'
        assert canonical == 'KA2K'

    def test_multi_aa_deletion_returns_canonical(self) -> None:
        # pos 4-5 (1-based) = FG; anchor at pos 3 = A → canonical AFG3A
        aa_seq = 'MKAFGP'
        result = _resolve_anchorless_deletion('FG', 3, aa_seq)  # 0-based pos 3 = 1-based 4
        assert result is not None
        anchor_idx, anchor_aa, canonical = result
        assert anchor_idx == 2
        assert anchor_aa == 'A'
        assert canonical == 'AFG3A'

    def test_returns_none_at_first_position(self) -> None:
        # No preceding residue when deleting at position 0 (0-based)
        aa_seq = 'MKAFGP'
        assert _resolve_anchorless_deletion('M', 0, aa_seq) is None

    def test_returns_none_on_block_mismatch(self) -> None:
        # pos 3 (0-based) = F, but we claim the deleted block is 'A'
        aa_seq = 'MKAFGP'
        assert _resolve_anchorless_deletion('A', 3, aa_seq) is None

    def test_returns_none_when_block_overruns_sequence(self) -> None:
        aa_seq = 'MKAFGP'
        # Trying to delete 10 AAs starting at pos 2 (only 4 remain)
        assert _resolve_anchorless_deletion('AFXXXXXXXXX', 2, aa_seq) is None


class TestAnchorlessDeletion:
    """Integration tests: anchor-less deletion tokens are resolved at init time."""

    @pytest.fixture()
    def tiny_genbank(self, tmp_path):
        gb = tmp_path / 'tiny.gb'
        write_genbank(gb, [
            {
                'id': 'tiny_ref',
                'accession': 'tiny_ref',
                'sequence': TINY_REF_SEQ,
                'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
            }
        ])
        return gb

    def test_single_aa_deletion_loads(self, tmp_path, tiny_genbank) -> None:
        # A at pos 3 (1-based); anchor K at pos 2; stored as reference=KA, mutation=K.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t3\tA\tA3del\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT position, reference, mutation FROM resistance_rule'
        ).fetchone()
        conn.close()

        assert row is not None
        # Stored position is the anchor's 0-based index (1-based pos 2 → 0-based 1)
        assert row[0] == 1
        assert row[1] == 'KA'
        assert row[2] == 'K'

    def test_multi_aa_deletion_loads(self, tmp_path, tiny_genbank) -> None:
        # FG at pos 4-5 (1-based); anchor A at pos 3; stored as reference=AFG, mutation=A.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t4\tF\tFG4del\tDrugA\tresistant
        """))
        db = tmp_path / 'proj.db'
        init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                     rules_tsv=tsv, additional_info=False)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            'SELECT position, reference, mutation FROM resistance_rule'
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 2   # anchor A at 0-based index 2
        assert row[1] == 'AFG'
        assert row[2] == 'A'

    def test_accepts_direct_indel_columns_and_keeps_storage(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t4\tF\tFDDD\tDrugIns
            gag\ttiny_ref\t6\tPK\tP\tDrugDel
        """))
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT position, reference, mutation FROM resistance_rule ORDER BY id'
        ).fetchall()
        conn.close()

        assert rows[0]['position'] == 3
        assert rows[0]['reference'] == 'F'
        assert rows[0]['mutation'] == 'FDDD'
        assert rows[1]['position'] == 5
        assert rows[1]['reference'] == 'PK'
        assert rows[1]['mutation'] == 'P'

    def test_legacy_indel_rewrite_is_stored_as_split_columns(self, tmp_path, tiny_genbank) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t4\tF\tF4FDDD\tDrugIns
            gag\ttiny_ref\t6\tP\tPK6P\tDrugDel
        """))
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db,
            name='test',
            genbank_paths=[tiny_genbank],
            rules_tsv=tsv,
            additional_info=False,
        )

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT position, reference, mutation FROM resistance_rule ORDER BY id'
        ).fetchall()
        conn.close()

        assert rows[0]['position'] == 3
        assert rows[0]['reference'] == 'F'
        assert rows[0]['mutation'] == 'FDDD'
        assert rows[1]['position'] == 5
        assert rows[1]['reference'] == 'PK'
        assert rows[1]['mutation'] == 'P'

    def test_block_mismatch_raises(self, tmp_path, tiny_genbank) -> None:
        # reference=A is correct at pos 3, but mutation token claims 'Q' is deleted —
        # Q does not match the feature sequence at pos 3 (which has A), so resolution fails.
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t3\tA\tQ3del\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='cannot resolve anchor'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)

    def test_deletion_at_position_1_raises(self, tmp_path, tiny_genbank) -> None:
        # Deleting M at pos 1 (1-based) — no preceding anchor, must fail
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral
            gag\ttiny_ref\t1\tM\tM1del\tDrugA
        """))
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='cannot resolve anchor'):
            init_project(db_path=db, name='test', genbank_paths=[tiny_genbank],
                         rules_tsv=tsv, additional_info=False)


class TestProjectMetadataInit:
    def test_init_project_stores_metadata_and_resolves_doi(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\n'
            'gag\tMYREF001\t2\tK\tE\tTestDrug\n'
        )
        metadata_path = tmp_path / 'metadata.json'
        metadata_path.write_text(
            '{\n'
            '  "Maintainers": ["A Curator", "B Curator"],\n'
            '  "Contact": "team@example.org",\n'
            '  "Publication": "12345678",\n'
            '  "Website": "https://example.org/db",\n'
            '  "Description": "Curated antiviral resistance database.",\n'
            '  "Maintainer update": "2026-04-21",\n'
            '  "License": "CC-BY-4.0",\n'
            '  "TSV checksum": "sha256:abc123"\n'
            '}\n'
        )

        db_path = tmp_path / 'project.db'
        with patch('respro.db.project_metadata.fetch_pubmed_metadata', return_value={'title': 'x', 'doi': '10.1000/test'}):
            init_project(
                db_path=db_path,
                name='Meta Project',
                genbank_paths=[genbank_path],
                rules_tsv=rules_path,
                metadata_json=metadata_path,
                overwrite=False,
                additional_info=False,
            )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT metadata_maintainers, metadata_contact, metadata_publication_pmid, '
            'metadata_publication_doi, metadata_website, metadata_description, '
            'metadata_maintainer_update, metadata_license, metadata_tsv_checksum '
            'FROM project LIMIT 1'
        ).fetchone()
        conn.close()

        assert row is not None
        assert row['metadata_maintainers'] == 'A Curator; B Curator'
        assert row['metadata_contact'] == 'team@example.org'
        assert row['metadata_publication_pmid'] == '12345678'
        assert row['metadata_publication_doi'] == '10.1000/test'
        assert row['metadata_website'] == 'https://example.org/db'
        assert row['metadata_description'] == 'Curated antiviral resistance database.'
        assert row['metadata_maintainer_update'] == '2026-04-21'
        assert row['metadata_license'] == 'CC-BY-4.0'
        assert row['metadata_tsv_checksum'] == 'sha256:abc123'

    def test_init_project_rejects_unknown_metadata_key(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC',
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 30, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            'feature\treference_identifier\tposition\treference\tmutation\tantiviral\n'
            'gag\tMYREF001\t2\tK\tE\tTestDrug\n'
        )
        metadata_path = tmp_path / 'metadata.json'
        metadata_path.write_text('{"UnknownField": "value"}\n')

        with pytest.raises(ValueError, match='Invalid metadata key'):
            init_project(
                db_path=tmp_path / 'project.db',
                name='Meta Project',
                genbank_paths=[genbank_path],
                rules_tsv=rules_path,
                metadata_json=metadata_path,
                overwrite=False,
                additional_info=False,
            )


class TestFormulaRuleImport:
    def test_init_project_rejects_grouped_rule_without_member_id(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                group_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral
                group_1\tgag\tmyref\t2\tK\tE\tDrugA
                """
            )
        )
        formula_path = tmp_path / 'formula_rules.tsv'
        formula_path.write_text(
            textwrap.dedent(
                """\
                group_id\tantiviral\texpression\tphenotype
                group_1\tDrugA\tmut_k2e\tresistant
                """
            )
        )

        with pytest.raises(ValueError, match='missing required field member_id'):
            init_project(
                db_path=tmp_path / 'project.db',
                name='Formula Project',
                genbank_paths=[genbank_path],
                rules_tsv=rules_path,
                formula_rules_tsv=formula_path,
                additional_info=False,
            )

    def test_init_project_imports_formula_rules_from_second_tsv(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                member_id\tgroup_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
                mut_k2e\tgroup_1\tgag\tmyref\t2\tK\tE\tDrugA\tunknown
                mut_f4l\tgroup_1\tgag\tmyref\t4\tF\tL\tDrugA\tunknown
                mut_a6v\tgroup_1\tgag\tmyref\t6\tP\tV\tDrugA\tunknown
                """
            )
        )
        formula_path = tmp_path / 'formula_rules.tsv'
        formula_path.write_text(
            textwrap.dedent(
                """\
                group_id\tantiviral\texpression\tphenotype\tclinical_phenotype\tic50\tfold_ic50\tsource\tcomment
                group_1\tDrugA\t(mut_k2e XOR mut_a6v) AND NOT mut_f4l\tresistant\tunknown\t\t3\tliterature\tExample formula
                """
            )
        )

        db_path = tmp_path / 'project.db'
        init_project(
            db_path=db_path,
            name='Formula Project',
            genbank_paths=[genbank_path],
            rules_tsv=rules_path,
            formula_rules_tsv=formula_path,
            additional_info=False,
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rule_rows = conn.execute(
            'SELECT external_id FROM resistance_rule ORDER BY external_id'
        ).fetchall()
        formula_row = conn.execute(
            'SELECT formula_id, normalized_expression, fold_ic50, source, comment '
            'FROM resistance_formula_rule'
        ).fetchone()
        link_rows = conn.execute(
            'SELECT r.external_id '
            'FROM resistance_formula_rule_member frm '
            'JOIN resistance_rule r ON r.id = frm.rule_id '
            'ORDER BY r.external_id'
        ).fetchall()
        conn.close()

        assert [row['external_id'] for row in rule_rows] == ['mut_a6v', 'mut_f4l', 'mut_k2e']
        assert formula_row is not None
        assert formula_row['formula_id'] == 'group_1'
        assert formula_row['normalized_expression'] == '((mut_a6v XOR mut_k2e) AND (NOT mut_f4l))'
        assert formula_row['fold_ic50'] == '3'
        assert formula_row['source'] == 'literature'
        assert formula_row['comment'] == 'Example formula'
        assert [row['external_id'] for row in link_rows] == ['mut_a6v', 'mut_f4l', 'mut_k2e']

    def test_init_project_imports_shared_atomic_rule_once_for_multiple_groups(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                member_id\tgroup_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
                mut_shared\tgroup_a,group_b\tgag\tmyref\t2\tK\tE\tDrugSingle\tresistant
                mut_only_a\tgroup_a\tgag\tmyref\t4\tF\tL\tDrugSingle\tunknown
                mut_only_b\tgroup_b\tgag\tmyref\t6\tP\tV\tDrugSingle\tunknown
                """
            )
        )
        formula_path = tmp_path / 'formula_rules.tsv'
        formula_path.write_text(
            textwrap.dedent(
                """\
                group_id\tantiviral\texpression\tphenotype\tcomment
                group_a\tDrugA\tmut_shared AND mut_only_a\tresistant\tFormula A
                group_b\tDrugB\tmut_shared AND mut_only_b\tintermediate\tFormula B
                """
            )
        )

        db_path = tmp_path / 'project.db'
        init_project(
            db_path=db_path,
            name='Formula Project',
            genbank_paths=[genbank_path],
            rules_tsv=rules_path,
            formula_rules_tsv=formula_path,
            additional_info=False,
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        shared_rows = conn.execute(
            'SELECT external_id, drug_id FROM resistance_rule WHERE external_id = ?',
            ('mut_shared',),
        ).fetchall()
        atomic_row = conn.execute(
            'SELECT rr.external_id, d.name AS drug_name, rr.phenotype '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'WHERE rr.external_id = ?',
            ('mut_shared',),
        ).fetchone()
        formula_rows = conn.execute(
            'SELECT fr.formula_id, d.name AS drug_name, fr.phenotype, fr.comment '
            'FROM resistance_formula_rule fr JOIN drug d ON d.id = fr.drug_id '
            'ORDER BY fr.formula_id'
        ).fetchall()
        formula_links = conn.execute(
            'SELECT fr.formula_id, r.external_id '
            'FROM resistance_formula_rule_member frm '
            'JOIN resistance_formula_rule fr ON fr.id = frm.formula_rule_id '
            'JOIN resistance_rule r ON r.id = frm.rule_id '
            'ORDER BY fr.formula_id, r.external_id'
        ).fetchall()
        conn.close()

        assert len(shared_rows) == 1
        assert atomic_row is not None
        assert atomic_row['drug_name'] == 'drugsingle'
        assert atomic_row['phenotype'] == 'resistant'
        assert [(row['formula_id'], row['drug_name'], row['phenotype'], row['comment']) for row in formula_rows] == [
            ('group_a', 'druga', 'resistant', 'Formula A'),
            ('group_b', 'drugb', 'intermediate', 'Formula B'),
        ]
        assert [(row['formula_id'], row['external_id']) for row in formula_links] == [
            ('group_a', 'mut_only_a'),
            ('group_a', 'mut_shared'),
            ('group_b', 'mut_only_b'),
            ('group_b', 'mut_shared'),
        ]

    def test_contradictory_atomic_rule_gets_auto_comment(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
                gag\tmyref\t2\tK\tE\tDrugA\tcontradictory
                """
            )
        )

        db_path = tmp_path / 'project.db'
        init_project(
            db_path=db_path,
            name='Contradictory Project',
            genbank_paths=[genbank_path],
            rules_tsv=rules_path,
            additional_info=False,
        )

        conn = sqlite3.connect(db_path)
        row = conn.execute('SELECT comment FROM resistance_rule LIMIT 1').fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 'Publications have contradictory phenotype associations.'

    def test_contradictory_formula_rule_gets_auto_comment(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                member_id\tgroup_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
                mut_k2e\tgroup_1\tgag\tmyref\t2\tK\tE\tDrugA\tunknown
                mut_p6v\tgroup_1\tgag\tmyref\t6\tP\tV\tDrugA\tunknown
                """
            )
        )
        formula_path = tmp_path / 'formula_rules.tsv'
        formula_path.write_text(
            textwrap.dedent(
                """\
                group_id\tantiviral\texpression\tphenotype
                group_1\tDrugA\tmut_k2e AND mut_p6v\tcontradictory
                """
            )
        )

        db_path = tmp_path / 'project.db'
        init_project(
            db_path=db_path,
            name='Contradictory Formula Project',
            genbank_paths=[genbank_path],
            rules_tsv=rules_path,
            formula_rules_tsv=formula_path,
            additional_info=False,
        )

        conn = sqlite3.connect(db_path)
        row = conn.execute('SELECT comment FROM resistance_formula_rule LIMIT 1').fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 'Publications have contradictory phenotype associations.'

    def test_init_project_rejects_duplicate_atomic_rule_ids(self, tmp_path) -> None:
        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        rules_path = tmp_path / 'rules.tsv'
        rules_path.write_text(
            textwrap.dedent(
                """\
                member_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral
                mut_dup\tgag\tmyref\t2\tK\tE\tDrugA
                mut_dup\tgag\tmyref\t4\tF\tL\tDrugA
                """
            )
        )

        with pytest.raises(ValueError, match='duplicate atomic rule ids'):
            init_project(
                db_path=tmp_path / 'project.db',
                name='Formula Project',
                genbank_paths=[genbank_path],
                rules_tsv=rules_path,
                additional_info=False,
            )

    def test_add_to_project_soft_skips_formula_with_unknown_atomic_rule_id(self, tmp_path) -> None:
        from respro.cli.init import add_to_project

        genbank_path = write_genbank(
            tmp_path / 'ref.gb',
            [
                {
                    'id': 'myref',
                    'accession': 'MYREF001',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'},
                    ],
                },
            ],
        )
        db_path = tmp_path / 'project.db'
        initial_rules = tmp_path / 'initial_rules.tsv'
        initial_rules.write_text(
            textwrap.dedent(
                """\
                member_id\tgroup_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral
                mut_k2e\tgroup_1\tgag\tmyref\t2\tK\tE\tDrugA
                """
            )
        )
        init_project(
            db_path=db_path,
            name='Formula Project',
            genbank_paths=[genbank_path],
            rules_tsv=initial_rules,
            additional_info=False,
        )

        additional_rules = tmp_path / 'additional_rules.tsv'
        additional_rules.write_text(
            textwrap.dedent(
                """\
                member_id\tgroup_id\tfeature\treference_identifier\tposition\treference\tmutation\tantiviral
                mut_a6v\tgroup_1\tgag\tmyref\t6\tP\tV\tDrugA
                """
            )
        )
        formula_path = tmp_path / 'formula_rules.tsv'
        formula_path.write_text(
            textwrap.dedent(
                """\
                group_id\tantiviral\texpression\tphenotype
                group_1\tDrugA\tmut_a6v AND missing_rule\tresistant
                """
            )
        )

        # Should not raise; instead skip the formula rule with unknown member
        add_to_project(
            db_path=db_path,
            rules_tsv=additional_rules,
            formula_rules_tsv=formula_path,
            additional_info=False,
            validate_only=True,
        )

        # Verify atomic rules were imported but formula rule was skipped
        conn = sqlite3.connect(str(db_path))
        formula_count = conn.execute(
            'SELECT COUNT(*) FROM resistance_formula_rule WHERE formula_id = ?',
            ('group_1',)
        ).fetchone()[0]
        conn.close()
        assert formula_count == 0  # Formula rule should be skipped


