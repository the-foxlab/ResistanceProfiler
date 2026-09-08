"""Tests for rank-based phenotype import: verbatim labels, bare-rank input, metadata (T2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ, write_genbank

from respro.cli.init import init_project
from respro.db.phenotype_ranks import label_to_rank
from respro.db.schema import open_project_db


def _tiny_genbank(tmp_path: Path) -> Path:
    return write_genbank(tmp_path / 'tiny.gb', [
        {
            'id': 'tiny_ref',
            'accession': 'tiny_ref',
            'sequence': TINY_REF_SEQ,
            'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
        }
    ])


def _rules_tsv(tmp_path: Path, phenotype_column: str, rows: list[tuple]) -> Path:
    header = (
        'feature\treference_identifier\tposition\treference\tmutation\t'
        f'antiviral\t{phenotype_column}\n'
    )
    lines = [header]
    for row in rows:
        lines.append('\t'.join(str(c) for c in row) + '\n')
    tsv = tmp_path / 'rules.tsv'
    tsv.write_text(''.join(lines))
    return tsv


def _stored_phenotypes(db: Path) -> list[tuple[str, str]]:
    conn = open_project_db(db)
    rows = conn.execute(
        'SELECT phenotype, clinical_phenotype FROM resistance_rule ORDER BY id'
    ).fetchall()
    conn.close()
    return [(r['phenotype'], r['clinical_phenotype']) for r in rows]


def _inferred_ranks(db: Path) -> set[int]:
    """Derive the rank set from stored rule labels via the vocabulary."""
    conn = open_project_db(db)
    rows = conn.execute(
        'SELECT phenotype, clinical_phenotype FROM resistance_rule'
    ).fetchall()
    conn.close()
    ranks: set[int] = set()
    for row in rows:
        for col in ('phenotype', 'clinical_phenotype'):
            label = (row[col] or '').strip()
            if label:
                rank = label_to_rank(label)
                if rank is not None:
                    ranks.add(rank)
    return ranks


class TestVerbatimLabelStorage:
    """Imported phenotype labels are stored lowercased + whitespace-stripped, verbatim."""

    def test_multi_word_label_stored_verbatim_lowercased(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'Low-level resistance'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('low-level resistance', '')]

    def test_label_whitespace_and_case_normalized(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', '  Low-Level Resistance  '),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('low-level resistance', '')]

    def test_hri_label_stored_verbatim(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'HRI'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('hri', '')]


class TestBareRankInput:
    """A bare integer rank resolves to the canonical fallback label."""

    def test_bare_rank_3_resolves_to_fallback(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', '3'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('low-level resistance', '')]

    def test_bare_rank_2_resolves_to_fallback(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', '2'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('potential low-level resistance', '')]


class TestUnknownLabelHardFails:
    """An unknown non-empty phenotype label raises during import."""

    def test_unknown_label_raises(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'foobar'),
        ])
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            init_project(
                db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
                rules_tsv=tsv, additional_info=False,
            )

    def test_old_shorthand_rejected(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'res'),
        ])
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError, match='Unknown phenotype label'):
            init_project(
                db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
                rules_tsv=tsv, additional_info=False,
            )

    def test_multiple_unknown_labels_all_reported(self, tmp_path: Path) -> None:
        """AUD-003: all invalid phenotype rows are collected, not just the first.

        The importer accumulates per-row errors and raises once at the end so a
        user sees every bad row in one pass. Raising on the first bad row
        forces an iterative fix-rerun cycle and is a regression of the
        pre-rank-refactor behaviour.
        """
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'foobar'),
            ('gag', 'tiny_ref', 3, 'A', 'E', 'DrugA', 'bazqux'),
        ])
        db = tmp_path / 'proj.db'
        with pytest.raises(ValueError) as exc_info:
            init_project(
                db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
                rules_tsv=tsv, additional_info=False,
            )
        message = str(exc_info.value)
        assert 'foobar' in message
        assert 'bazqux' in message


class TestEmptyPhenotype:
    """An empty phenotype cell stores '' (rank 0 / unknown)."""

    def test_empty_phenotype_stores_empty_string(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', ''),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _stored_phenotypes(db) == [('', '')]


class TestPhenotypeRanksInference:
    """Ranks are inferable from imported labels via the vocabulary at read time."""

    def test_three_tier_ranks_inferred(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'Susceptible'),
            ('gag', 'tiny_ref', 6, 'P', 'V', 'DrugA', 'Intermediate'),
            ('gag', 'tiny_ref', 4, 'F', 'L', 'DrugA', 'Resistant'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _inferred_ranks(db) == {1, 4, 5}

    def test_two_tier_ranks_inferred(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', 'Susceptible'),
            ('gag', 'tiny_ref', 6, 'P', 'V', 'DrugA', 'Resistant'),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        assert _inferred_ranks(db) == {1, 5}

    def test_empty_phenotypes_infer_no_severity_ranks(self, tmp_path: Path) -> None:
        tsv = _rules_tsv(tmp_path, 'phenotype', [
            ('gag', 'tiny_ref', 2, 'K', 'E', 'DrugA', ''),
        ])
        db = tmp_path / 'proj.db'
        init_project(
            db_path=db, name='t', genbank_paths=[_tiny_genbank(tmp_path)],
            rules_tsv=tsv, additional_info=False,
        )
        # No severity labels stored, so no severity ranks are inferred.
        assert _inferred_ranks(db) == set()
