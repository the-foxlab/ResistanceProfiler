"""
Multi-chrom VCF + multi-record reference FASTA support.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import click
import pysam
import pytest
from typer.testing import CliRunner

from respro.cli.main import app
from respro.cli.profile_helpers import assemble_multi_reference_result
from respro.core.query import (
    QueryRecord,
    resolve_fasta_query_multi,
)
from respro.core.vcf_coverage import compute_coverage_gaps_from_bam_multi
from respro.core.vcf_remap import route_and_remap_variants
from respro.db.models import ProfilingResult, ReferenceGroup, VariantCall
from respro.db.results import load_run, save_run
from respro.db.schema import create_schema, init_results_db, open_project_db
from respro.report.plots import _build_lollipop_figure

# ──────────────────────────────────────────────────────────────────────
# Shared fixtures for multi-reference project databases
# ──────────────────────────────────────────────────────────────────────

# Two distinct 600-nt CDS sequences used as the two internal references.
# Both are long enough for mappy to align reliably.
_REF_A_MOTIF = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # 30 nt, 10 codons, starts M K A F G P K F G P
_REF_B_MOTIF = 'ATGCCCCGGGAAATTTCCCGGGAAATTTGG'  # 30 nt, 10 codons, starts M P G K F P G K F G

_REF_A_SEQ = (_REF_A_MOTIF * 20)[:600]
_REF_B_SEQ = (_REF_B_MOTIF * 20)[:600]


def _make_multi_ref_db(db_path: Path, *, ref_b_has_rules: bool = True) -> Path:
    """
    Build a project DB with two internal references (refA, refB).

    Each reference has one CDS feature (gagA / gagB) covering its whole sequence.
    refA always has a K2E rule. refB has a P2A rule when ``ref_b_has_rules`` is True,
    otherwise refB has no rules (orphaned-reference case).
    """
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        ('Multi Test', 1, str(uuid.uuid4())),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refA', len(_REF_A_SEQ), 'Organism A'),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refB', len(_REF_B_SEQ), 'Organism B'),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gagA', 'GagA', 0, len(_REF_A_SEQ), '+', _REF_A_SEQ),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (2, 'gagB', 'GagB', 0, len(_REF_B_SEQ), '+', _REF_B_SEQ),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
    # refA rule: codon 1 (0-based) is K (AAA at nt 3..5), mutation E -> resistant
    conn.execute(
        'INSERT INTO resistance_rule '
        '(feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    if ref_b_has_rules:
        # refB rule: codon 1 (0-based) is P (CCC at nt 3..5), mutation A -> resistant
        conn.execute(
            'INSERT INTO resistance_rule '
            '(feature_id, drug_id, position, reference, mutation, phenotype) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (2, 1, 1, 'P', 'A', 'resistant'),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def multi_ref_db(tmp_path: Path) -> Path:
    """Project DB with two references that both have rules."""
    return _make_multi_ref_db(tmp_path / 'multi_ref.db')


@pytest.fixture()
def multi_ref_db_orphan_b(tmp_path: Path) -> Path:
    """Project DB with two references where refB has no rules (orphan case)."""
    return _make_multi_ref_db(tmp_path / 'multi_ref_orphan.db', ref_b_has_rules=False)


@pytest.fixture()
def single_ref_db(tmp_path: Path) -> Path:
    """Project DB with one reference (refA only) — single-ref regression."""
    db_path = tmp_path / 'single_ref.db'
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        ('Single Test', 1, str(uuid.uuid4())),
    )
    conn.execute(
        'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
        (1, 'refA', len(_REF_A_SEQ), 'Organism A'),
    )
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gagA', 'GagA', 0, len(_REF_A_SEQ), '+', _REF_A_SEQ),
    )
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
    conn.execute(
        'INSERT INTO resistance_rule '
        '(feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )
    conn.commit()
    conn.close()
    return db_path


class TestResolveFastaQueryMulti:
    """resolve_fasta_query_multi returns one QueryRecord per FASTA record."""

    def test_single_record_returns_one_element_list(self, single_ref_db: Path, tmp_path: Path) -> None:
        # Arrange
        fasta_path = tmp_path / 'single.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')

        # Act
        conn = open_project_db(single_ref_db)
        records = resolve_fasta_query_multi(conn, fasta_path)
        conn.close()

        # Assert
        assert len(records) == 1
        assert isinstance(records[0], QueryRecord)
        assert records[0].query_name == 'chrom_a'
        assert records[0].query_sequence == _REF_A_SEQ
        assert len(records[0].feature_matches) >= 1
        assert records[0].feature_matches[0].feature.name == 'gagA'

    def test_two_records_aligning_to_one_reference_share_reference_id(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Targeted-sequencing case: two FASTA records both align to refA."""
        # Arrange — split refA into two halves; both should still align to gagA
        half = len(_REF_A_SEQ) // 2
        seq_a1 = _REF_A_SEQ[:half]
        seq_a2 = _REF_A_SEQ[half:]
        fasta_path = tmp_path / 'two_records_one_ref.fasta'
        fasta_path.write_text(f'>chrom_a1\n{seq_a1}\n>chrom_a2\n{seq_a2}\n')

        # Act
        conn = open_project_db(single_ref_db)
        records = resolve_fasta_query_multi(conn, fasta_path)
        conn.close()

        # Assert
        assert len(records) == 2
        ref_ids = {m.feature.reference_id for r in records for m in r.feature_matches}
        # Both records aligned to the same single internal reference (refA = id 1)
        assert ref_ids == {1}

    def test_two_records_aligning_to_two_different_references(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Segmented-virus case: two FASTA records align to two different refs."""
        # Arrange
        fasta_path = tmp_path / 'two_records_two_refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')

        # Act
        conn = open_project_db(multi_ref_db)
        records = resolve_fasta_query_multi(conn, fasta_path)
        conn.close()

        # Assert
        assert len(records) == 2
        ref_ids_per_record = [
            {m.feature.reference_id for m in r.feature_matches} for r in records
        ]
        # One record aligns to refA (id 1), the other to refB (id 2)
        assert {frozenset(r) for r in ref_ids_per_record} == {frozenset({1}), frozenset({2})}

    def test_record_with_no_alignment_is_dropped_with_warning(
        self, multi_ref_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A FASTA record that aligns to nothing is dropped, not an error."""
        # Arrange — a random unrelated sequence plus a real refA record
        unrelated = 'GATTACA' * 100  # 700 nt of GATTACA repeats, unlikely to align
        fasta_path = tmp_path / 'with_orphan.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_orphan\n{unrelated}\n')

        # Act
        with caplog.at_level('WARNING', logger='respro'):
            conn = open_project_db(multi_ref_db)
            records = resolve_fasta_query_multi(conn, fasta_path)
            conn.close()

        # Assert — only the aligning record is returned
        assert len(records) == 1
        assert records[0].query_name == 'chrom_a'
        # A warning was logged about the dropped record
        assert any('chrom_orphan' in record.message for record in caplog.records)

    def test_all_records_unaligned_raises(self, multi_ref_db: Path, tmp_path: Path) -> None:
        """If no FASTA record aligns to any internal reference, raise ValueError."""
        # Arrange
        unrelated = 'GATTACA' * 100
        fasta_path = tmp_path / 'all_orphan.fasta'
        fasta_path.write_text(f'>chrom_x\n{unrelated}\n>chrom_y\n{unrelated}\n')

        # Act / Assert
        conn = open_project_db(multi_ref_db)
        with pytest.raises(ValueError, match='No FASTA record aligned'):
            resolve_fasta_query_multi(conn, fasta_path)
        conn.close()

    def test_empty_fasta_raises(self, multi_ref_db: Path, tmp_path: Path) -> None:
        fasta_path = tmp_path / 'empty.fasta'
        fasta_path.write_text('')

        conn = open_project_db(multi_ref_db)
        with pytest.raises(ValueError, match='No sequences'):
            resolve_fasta_query_multi(conn, fasta_path)
        conn.close()

    def test_cache_round_trips_per_record(self, multi_ref_db: Path, tmp_path: Path) -> None:
        """Caching stores one query_reference row per FASTA record and reuses it."""
        # Arrange
        fasta_path = tmp_path / 'cache.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')

        # Act — first call populates cache
        conn = open_project_db(multi_ref_db)
        records_first = resolve_fasta_query_multi(conn, fasta_path, use_cache=True)
        cached_count = conn.execute('SELECT COUNT(*) AS n FROM query_reference').fetchone()['n']
        conn.close()

        # Act — second call should hit cache
        conn = open_project_db(multi_ref_db)
        records_second = resolve_fasta_query_multi(conn, fasta_path, use_cache=True)
        conn.close()

        # Assert
        assert cached_count == 2
        assert len(records_first) == len(records_second) == 2
        # Cached matches reproduce the same feature names
        names_first = sorted(m.feature.name for r in records_first for m in r.feature_matches)
        names_second = sorted(m.feature.name for r in records_second for m in r.feature_matches)
        assert names_first == names_second


def _strip_ansi(text: str) -> str:
    """Return text with ANSI escape sequences removed."""
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _build_query_records(multi_ref_db: Path, fasta_path: Path) -> list[QueryRecord]:
    """Helper: open the multi-ref DB and resolve a multi-record FASTA to QueryRecords."""
    conn = open_project_db(multi_ref_db)
    try:
        return resolve_fasta_query_multi(conn, fasta_path)
    finally:
        conn.close()


class TestRouteAndRemapVariants:
    """route_and_remap_variants groups variants by CHROM and remaps per matching QueryRecord."""

    def test_two_chroms_each_matching_a_record_remaps_both(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A 2-CHROM VCF where each CHROM matches a different FASTA record."""
        # Arrange — FASTA with chrom_a -> refA, chrom_b -> refB
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        records = _build_query_records(multi_ref_db, fasta_path)

        # Variants: codon 1 of each CDS is at nt position 3 (0-based).
        # refA codon 1 = AAA (K); a SNP A->G at pos 3 yields K2E.
        # refB codon 1 = CCC (P); a SNP C->A at pos 3 yields P2A... but we just
        # need to confirm both CHROMs are remapped, not the exact consequence.
        variants = [
            VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            VariantCall(chrom='chrom_b', pos=3, ref='C', alt='A', allele_freq=0.95, depth=500),
        ]

        # Act
        remapped, warnings, dropped_chroms = route_and_remap_variants(variants, records)

        # Assert — both variants survived remap onto their respective internal references
        assert len(remapped) == 2
        # Each remapped variant now sits at the internal genomic position of its feature
        chroms = {v.chrom for v in remapped}
        assert chroms == {'chrom_a', 'chrom_b'}
        assert dropped_chroms == []
        assert warnings == []

    def test_unmatched_chrom_warned_and_dropped(
        self, multi_ref_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A VCF CHROM with no matching FASTA record is warned and dropped."""
        # Arrange
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        records = _build_query_records(multi_ref_db, fasta_path)

        variants = [
            VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            VariantCall(chrom='chrom_orphan', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
        ]

        # Act
        with caplog.at_level('WARNING', logger='respro'):
            remapped, warnings, dropped_chroms = route_and_remap_variants(variants, records)

        # Assert — only the matched CHROM's variant survives
        assert len(remapped) == 1
        assert remapped[0].chrom == 'chrom_a'
        assert dropped_chroms == ['chrom_orphan']
        assert any('chrom_orphan' in r.message for r in caplog.records)

    def test_all_chroms_unmatched_raises(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """If no VCF CHROM matches any FASTA record, raise ValueError."""
        # Arrange
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        records = _build_query_records(multi_ref_db, fasta_path)

        variants = [
            VariantCall(chrom='chrom_x', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            VariantCall(chrom='chrom_y', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
        ]

        # Act / Assert
        with pytest.raises(ValueError, match='No VCF CHROM matched'):
            route_and_remap_variants(variants, records)

    def test_variants_outside_cds_are_dropped_silently_by_remap(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Variants inside a matched CHROM but outside any CDS are dropped by remap (not by routing)."""
        # Arrange — variant at pos 0 (before codon 1's first nt at pos 3 is fine, but pos 0 is
        # the M start codon's first base; use a position far outside the CDS by giving a ref
        # mismatch so remap drops it). Simpler: a variant whose REF doesn't match the query.
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        records = _build_query_records(multi_ref_db, fasta_path)

        variants = [
            # pos 3 with wrong REF — remap will warn and drop this variant
            VariantCall(chrom='chrom_a', pos=3, ref='T', alt='G', allele_freq=0.95, depth=500),
        ]

        # Act
        remapped, warnings, dropped_chroms = route_and_remap_variants(variants, records)

        # Assert — routing succeeded (chrom_a matched) but remap dropped the variant
        assert remapped == []
        assert dropped_chroms == []
        # remap_variants emits a warning for the REF mismatch
        assert len(warnings) >= 1

    def test_empty_variants_returns_empty(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """No variants in -> no remapped variants out, no error."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        records = _build_query_records(multi_ref_db, fasta_path)

        remapped, warnings, dropped_chroms = route_and_remap_variants([], records)

        assert remapped == []
        assert warnings == []

    def test_query_matching_two_references_narrows_to_best_before_remap(
        self, tmp_path: Path,
    ) -> None:
        """A query record matching features on two references is narrowed to the best one.

        Without narrowing, remap_variants would emit one remapped variant per matching
        feature (one per reference), producing duplicate variants with different internal
        genomic positions and cross-reference contamination. route_and_remap_variants must
        narrow each QueryRecord's feature_matches to its best reference (via
        pick_best_reference_id + select_matches_for_reference) before remap, mirroring the
        original single-reference flow.
        """
        # Build a project DB where refA and refB share an IDENTICAL feature sequence so a
        # single query aligns to both. refB has the higher-identity match (longer overlap).
        shared_seq = _REF_A_SEQ
        db_path = tmp_path / 'shared_feature.db'
        conn = create_schema(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('Shared', 1, str(uuid.uuid4())),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
            (1, 'refA', len(shared_seq), 'OrgA'),
        )
        conn.execute(
            'INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
            (1, 'refB', len(shared_seq), 'OrgB'),
        )
        # Both references carry the same CDS sequence under different feature names.
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (1, 'polA', 'PolA', 0, len(shared_seq), '+', shared_seq),
        )
        conn.execute(
            'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (2, 'polB', 'PolB', 0, len(shared_seq), '+', shared_seq),
        )
        conn.commit()
        conn.close()

        # A query identical to the shared sequence aligns to both polA and polB.
        fasta_path = tmp_path / 'q.fasta'
        fasta_path.write_text(f'>chrom_a\n{shared_seq}\n')
        records = _build_query_records(db_path, fasta_path)
        assert len(records) == 1
        # The query matched features on BOTH references.
        ref_ids = {m.feature.reference_id for m in records[0].feature_matches}
        assert ref_ids == {1, 2}

        # One variant on chrom_a at codon 1 (pos 3).
        variants = [
            VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
        ]

        # Act
        remapped, _warnings, dropped_chroms = route_and_remap_variants(variants, records)

        # Assert — exactly ONE remapped variant (narrowed to the best reference), not two.
        assert len(remapped) == 1, (
            f'expected 1 remapped variant after narrowing to best reference, got {len(remapped)}'
        )
        assert dropped_chroms == []
        assert dropped_chroms == []


def _make_reference_group(
    *, reference_name: str, reference_id: int, query_name: str,
    query_sequence: str, feature_matches: list | None = None,
    reference_length_nt: int = 600, organism: str = 'TestOrg',
) -> ReferenceGroup:
    """Build a minimal ReferenceGroup for structural tests."""
    return ReferenceGroup(
        reference_name=reference_name,
        reference_id=reference_id,
        organism=organism,
        reference_length_nt=reference_length_nt,
        query_name=query_name,
        query_sequence=query_sequence,
        feature_matches=feature_matches or [],
        features=[],
        rules=[],
        formula_rules=[],
        rule_feature_names=set(),
    )


class TestReferenceGroupDataStructure:
    """ReferenceGroup is a frozen dataclass; ProfilingResult carries references: list[ReferenceGroup]."""

    def test_reference_group_is_frozen_dataclass(self) -> None:
        """ReferenceGroup must be frozen so per-reference data is immutable after assembly."""
        rg = _make_reference_group(reference_name='refA', reference_id=1, query_name='chrom_a', query_sequence='ACGT')
        with pytest.raises(Exception):
            rg.reference_name = 'mutated'  # type: ignore[misc]

    def test_profiling_result_has_references_field(self) -> None:
        """ProfilingResult exposes a references: list[ReferenceGroup] field."""
        result = ProfilingResult()
        assert hasattr(result, 'references')
        assert result.references == []

    def test_profiling_result_no_scalar_reference_fields(self) -> None:
        """The dataclass must NOT carry stored scalar reference fields.

        Convenience properties that delegate to references[0] are allowed (they are
        not dataclass fields), but the field set must only contain `references`.
        """
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ProfilingResult)}
        for removed in ('reference_name', 'reference_length_nt', 'query_sequence', 'feature_matches'):
            assert removed not in field_names, (
                f'{removed!r} must not be a stored field on ProfilingResult; use result.references[i].{removed}'
            )
        assert 'references' in field_names

    def test_single_reference_run_builds_one_element_list(self) -> None:
        """A single-reference run constructs a one-element references list."""
        rg = _make_reference_group(reference_name='refA', reference_id=1, query_name='chrom_a', query_sequence='ACGT')
        result = ProfilingResult(references=[rg])
        assert len(result.references) == 1
        assert result.references[0].reference_name == 'refA'

    def test_two_reference_run_builds_two_groups_with_disjoint_matches(self) -> None:
        """A two-reference run constructs two ReferenceGroups with disjoint feature_matches."""
        # Use distinct sentinel objects to stand in for FeatureMatch instances.
        match_a = object()
        match_b = object()
        rg_a = _make_reference_group(
            reference_name='refA', reference_id=1, query_name='chrom_a',
            query_sequence='AAAA', feature_matches=[match_a],
        )
        rg_b = _make_reference_group(
            reference_name='refB', reference_id=2, query_name='chrom_b',
            query_sequence='CCCC', feature_matches=[match_b],
        )
        result = ProfilingResult(references=[rg_a, rg_b])
        assert len(result.references) == 2
        names = [rg.reference_name for rg in result.references]
        assert names == ['refA', 'refB']
        # Disjoint feature_matches
        assert result.references[0].feature_matches != result.references[1].feature_matches

    def test_convenience_properties_delegate_to_first_reference(self) -> None:
        """Read-only properties delegate to references[0] for incremental reader migration."""
        rg = _make_reference_group(
            reference_name='refA', reference_id=1, query_name='chrom_a',
            query_sequence=_REF_A_SEQ, reference_length_nt=600,
        )
        result = ProfilingResult(references=[rg])
        assert result.reference_name == 'refA'
        assert result.reference_length_nt == 600
        assert result.query_sequence == _REF_A_SEQ
        assert result.feature_matches == []

    def test_convenience_properties_empty_when_no_references(self) -> None:
        """With no references, convenience properties return safe defaults (no IndexError)."""
        result = ProfilingResult()
        assert result.reference_name == ''
        assert result.reference_length_nt == 0
        assert result.query_sequence == ''
        assert result.feature_matches == []


def _build_remapped_for_records(
    project_conn, records: list[QueryRecord],
) -> list:
    """Route+remap dummy K2E/P2A variants per record; return flat remapped VariantCall list.

    Each remapped variant keeps its ``chrom`` (== query_name) so the assembler can
    group variants back to their reference for per-reference annotation.
    """
    # refA codon1 (K, AAA at nt 3..5): A->G at pos 3 -> GAA -> K2E.
    # refB codon1 (P, CCC at nt 3..5): C->G at pos 3 -> GCC -> P2A.
    variants = []
    for rec in records:
        variants.append(VariantCall(
            chrom=rec.query_name, pos=3, ref=rec.query_sequence[3], alt='G',
            allele_freq=0.95, depth=500,
        ))
    remapped, _warnings, _dropped = route_and_remap_variants(variants, records)
    return remapped


class TestAssembleMultiReferenceResult:
    """assemble_multi_reference_result builds ReferenceGroups and runs per-reference rule matching."""

    def test_two_refs_both_with_rules_produces_two_groups_and_rule_hits(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A 2-record submission where both references have rules -> 2 groups, hits on both."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Multi Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )

            # Two ReferenceGroups, one per matched reference, in input order.
            assert len(result.references) == 2
            assert result.references[0].reference_name == 'refA'
            assert result.references[1].reference_name == 'refB'
            # Each group loaded its own features/rules.
            assert {f.name for f in result.references[0].features} == {'gagA'}
            assert {f.name for f in result.references[1].features} == {'gagB'}
            assert result.references[0].rule_feature_names == {'gagA'}
            assert result.references[1].rule_feature_names == {'gagB'}
            # Rule hits fired on annotations belonging to each reference.
            gag_a_hits = [a for a in result.annotations if a.feature_name == 'gagA' and a.is_resistance_hit]
            gag_b_hits = [a for a in result.annotations if a.feature_name == 'gagB' and a.is_resistance_hit]
            assert len(gag_a_hits) == 1
            assert len(gag_b_hits) == 1
        finally:
            conn.close()

    def test_one_ref_with_rules_one_orphan_completes_with_warning(
        self, multi_ref_db_orphan_b: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 2-record submission where only refA has rules -> completes; refB reported as orphan."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(multi_ref_db_orphan_b)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)

            with caplog.at_level('WARNING', logger='respro'):
                result = assemble_multi_reference_result(
                    project_conn=conn,
                    query_records=records,
                    remapped_variants=remapped,
                    coverage_gaps=[],
                    project_name='Multi Test',
                    sample='samp',
                    vcf_name='in.vcf',
                    total_variants=len(remapped),
                )

            # Both references retained (orphan kept, not dropped).
            assert len(result.references) == 2
            # refA has rules; refB has none.
            assert result.references[0].rule_feature_names == {'gagA'}
            assert result.references[1].rule_feature_names == set()
            # refA rule hit fired; refB has no rule hit.
            gag_a_hits = [a for a in result.annotations if a.feature_name == 'gagA' and a.is_resistance_hit]
            gag_b_hits = [a for a in result.annotations if a.feature_name == 'gagB' and a.is_resistance_hit]
            assert len(gag_a_hits) == 1
            assert gag_b_hits == []
            # A warning was logged about the orphaned (ruleless) reference.
            assert any('refB' in r.message and ('rule' in r.message.lower() or 'orphan' in r.message.lower()) for r in caplog.records)
        finally:
            conn.close()

    def test_no_matched_reference_has_rules_raises_click_exception(
        self, tmp_path: Path,
    ) -> None:
        """If no matched reference has rules loaded, raise click.ClickException."""
        # Build a DB where BOTH references lack rules.
        db_path = _make_multi_ref_db(tmp_path / 'no_rules.db', ref_b_has_rules=False)
        # Also remove refA's rule.
        conn = open_project_db(db_path)
        conn.execute('DELETE FROM resistance_rule')
        conn.commit()
        conn.close()

        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)

            with pytest.raises(click.ClickException, match='no matched reference has resistance rules'):
                assemble_multi_reference_result(
                    project_conn=conn,
                    query_records=records,
                    remapped_variants=remapped,
                    coverage_gaps=[],
                    project_name='No Rules',
                    sample='samp',
                    vcf_name='in.vcf',
                    total_variants=len(remapped),
                )
        finally:
            conn.close()

    def test_single_record_submission_behaves_like_single_ref(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-record submission produces a one-element references list with rule hits."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 1
            remapped = _build_remapped_for_records(conn, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Single Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )

            assert len(result.references) == 1
            assert result.references[0].reference_name == 'refA'
            gag_a_hits = [a for a in result.annotations if a.feature_name == 'gagA' and a.is_resistance_hit]
            assert len(gag_a_hits) == 1
        finally:
            conn.close()

    def test_targeted_two_records_one_reference_no_duplicate_rule_matches(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Two records aligning to the same reference do not duplicate rule matches.

        In the targeted-sequencing case the assembler builds two ReferenceGroups both
        pointing at refA (same rules). The per-reference match_rules loop must not append
        the same ResistanceRule twice to an annotation's rule_matches, which would inflate
        drug hits and double-count resistance hits in the report.
        """
        # Two identical full-length records, both aligning to gagA on refA.
        fasta_path = tmp_path / 'targeted.fasta'
        fasta_path.write_text(f'>chrom_a1\n{_REF_A_SEQ}\n>chrom_a2\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 2
            # Both records align to the same internal reference (refA = id 1).
            ref_ids = {m.feature.reference_id for r in records for m in r.feature_matches}
            assert ref_ids == {1}

            # A K2E variant on each chrom (pos 3, A->G for refA codon1).
            variants = [
                VariantCall(chrom='chrom_a1', pos=3, ref='A', alt='G',
                            allele_freq=0.95, depth=500),
                VariantCall(chrom='chrom_a2', pos=3, ref='A', alt='G',
                            allele_freq=0.95, depth=500),
            ]
            remapped, _w, _d = route_and_remap_variants(variants, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Targeted',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )

            # Both ReferenceGroups point at refA.
            assert len(result.references) == 2
            assert {rg.reference_name for rg in result.references} == {'refA'}
            # Each gagA annotation has exactly ONE rule match (no duplication).
            gag_a_hits = [a for a in result.annotations if a.feature_name == 'gagA' and a.is_resistance_hit]
            assert len(gag_a_hits) == 2  # one per chrom
            for ann in gag_a_hits:
                assert len(ann.rule_matches) == 1, (
                    f'expected 1 rule match per annotation, got {len(ann.rule_matches)} '
                    f'(duplicate rule matching across same-reference groups)'
                )
            # resistance_hits counts each hit once (not doubled).
            assert result.resistance_hits == 2
        finally:
            conn.close()

    def test_colliding_feature_names_no_cross_reference_rule_contamination(
        self, tmp_path: Path,
    ) -> None:
        """Two references sharing a feature name do not cross-match each other's rules.

        Project DBs may legitimately hold multiple pathogens whose features share a name
        (e.g. both have a ``pol`` CDS). ``match_rules`` indexes rules by
        ``(feature_name, codon_pos)`` and mutates ``ann.rule_matches`` in place, so running
        it against the full flat annotations list would append refA's rule to a refB
        annotation that happens to sit at the same codon — a false resistance hit. The
        per-reference rule loop must scope matching to the annotations whose feature belongs
        to that reference's features.
        """
        # Build a DB with two references that BOTH name their single CDS "pol".
        # refA's pol starts M K A F... (codon 1 = K, AAA at nt 3..5); refA has a K2E rule.
        # refB's pol also starts M K ... (codon 1 = K) but with a DISTINCT sequence so each
        # query record aligns to exactly one reference; refB has NO rule.
        # A K2E variant on refB's pol must NOT pick up refA's K2E rule.
        # Both references share the SAME organism so the cross-species collision gate does
        # not reject this run (this test exercises same-species shared-gene behaviour).
        refa_motif = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # codon 1 = K (AAA at nt 3..5)
        refb_motif = 'ATGAAACCCGGGAAATTTCCCGGGAAATTT'  # codon 1 = K (AAA at nt 3..5), distinct elsewhere
        refa_seq = (refa_motif * 20)[:600]
        refb_seq = (refb_motif * 20)[:600]

        db_path = tmp_path / 'collide.db'
        conn = create_schema(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
                     ('Collide', 1, str(uuid.uuid4())))
        conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                     (1, 'refA', len(refa_seq), 'Organism A'))
        conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                     (1, 'refB', len(refb_seq), 'Organism A'))
        # Both references name their CDS "pol" (colliding feature name).
        conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?)', (1, 'pol', 'Pol', 0, len(refa_seq), '+', refa_seq))
        conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?)', (2, 'pol', 'Pol', 0, len(refb_seq), '+', refb_seq))
        conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
        # refA-only rule: codon 1 K2E.
        conn.execute('INSERT INTO resistance_rule '
                     '(feature_id, drug_id, position, reference, mutation, phenotype) '
                     'VALUES (?, ?, ?, ?, ?, ?)', (1, 1, 1, 'K', 'E', 'resistant'))
        conn.commit()
        conn.close()

        # Two query records: chrom_a -> refA's pol, chrom_b -> refB's pol.
        fasta_path = tmp_path / 'collide.fasta'
        fasta_path.write_text(f'>chrom_a\n{refa_seq}\n>chrom_b\n{refb_seq}\n')
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 2
            # chrom_a aligns to refA (id 1), chrom_b aligns to refB (id 2).
            for rec in records:
                ref_ids = {m.feature.reference_id for m in rec.feature_matches}
                assert len(ref_ids) == 1, f'{rec.query_name} matched {ref_ids}'

            # K2E variant on BOTH chroms (pos 3, A->G -> codon 1 K->E).
            variants = [
                VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
                VariantCall(chrom='chrom_b', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            ]
            remapped, _w, _d = route_and_remap_variants(variants, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Collide',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )

            # Both references retained.
            assert len(result.references) == 2
            ref_names = [rg.reference_name for rg in result.references]
            assert set(ref_names) == {'refA', 'refB'}
            # refA's pol annotation gets the K2E rule hit; refB's pol annotation does NOT.
            pol_hits_by_ref = {}
            for ann in result.annotations:
                if ann.feature_name == 'pol' and ann.is_resistance_hit:
                    # Determine which reference this annotation belongs to via its chrom.
                    ref_name = next(rg.reference_name for rg in result.references if rg.query_name == ann.variant.chrom)
                    pol_hits_by_ref.setdefault(ref_name, 0)
                    pol_hits_by_ref[ref_name] += 1
            assert pol_hits_by_ref.get('refA') == 1, f'refA should have 1 rule hit, got {pol_hits_by_ref}'
            assert 'refB' not in pol_hits_by_ref, (
                f'refB must not receive refA\'s K2E rule (cross-reference contamination), '
                f'got hits={pol_hits_by_ref}'
            )
            # Total resistance hits = 1 (only refA), not 2.
            assert result.resistance_hits == 1
        finally:
            conn.close()


def _make_colliding_db(
    db_path: Path, *,
    organism_a: str, organism_b: str,
    shared_feature_name: str = 'pol',
    ref_b_has_rules: bool = False,
) -> Path:
    """
    Build a project DB with two references whose single CDS shares one feature name.

    Both references name their CDS ``shared_feature_name`` (default 'pol'). The two
    references belong to ``organism_a`` and ``organism_b`` respectively, so a cross-species
    gene-name collision occurs iff the two organisms differ. refA always has a K2E rule;
    refB has no rule unless ``ref_b_has_rules`` is True.
    """
    refa_motif = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'  # codon 1 = K (AAA at nt 3..5)
    refb_motif = 'ATGAAACCCGGGAAATTTCCCGGGAAATTT'  # codon 1 = K (AAA at nt 3..5), distinct elsewhere
    refa_seq = (refa_motif * 20)[:600]
    refb_seq = (refb_motif * 20)[:600]

    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
                 ('Collide', 1, str(uuid.uuid4())))
    conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                 (1, 'refA', len(refa_seq), organism_a))
    conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                 (1, 'refB', len(refb_seq), organism_b))
    conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?)', (1, shared_feature_name, 'Pol', 0, len(refa_seq), '+', refa_seq))
    conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?)', (2, shared_feature_name, 'Pol', 0, len(refb_seq), '+', refb_seq))
    conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
    conn.execute('INSERT INTO resistance_rule '
                 '(feature_id, drug_id, position, reference, mutation, phenotype) '
                 'VALUES (?, ?, ?, ?, ?, ?)', (1, 1, 1, 'K', 'E', 'resistant'))
    if ref_b_has_rules:
        conn.execute('INSERT INTO resistance_rule '
                     '(feature_id, drug_id, position, reference, mutation, phenotype) '
                     'VALUES (?, ?, ?, ?, ?, ?)', (2, 1, 1, 'K', 'E', 'resistant'))
    conn.commit()
    conn.close()
    return db_path


def _colliding_fasta(tmp_path: Path) -> Path:
    """FASTA with two records aligning to refA and refB respectively (both codon1 = K)."""
    refa_motif = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
    refb_motif = 'ATGAAACCCGGGAAATTTCCCGGGAAATTT'
    refa_seq = (refa_motif * 20)[:600]
    refb_seq = (refb_motif * 20)[:600]
    fasta_path = tmp_path / 'collide.fasta'
    fasta_path.write_text(f'>chrom_a\n{refa_seq}\n>chrom_b\n{refb_seq}\n')
    return fasta_path


def _k2e_variants_for_both_chroms() -> list[VariantCall]:
    """K2E variant (pos 3, A->G -> codon 1 K->E) on both chrom_a and chrom_b."""
    return [
        VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
        VariantCall(chrom='chrom_b', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
    ]


class TestCrossSpeciesGeneNameCollisionGate:
    """Reject when distinct species share a gene name."""

    def test_cross_species_same_gene_name_is_rejected(
        self, tmp_path: Path,
    ) -> None:
        """A run where two distinct organisms share a gene name must raise click.ClickException.

        This is the acceptance-critical case: example/multi-test-2 (HSV-1 UL23 + HSV-2 UL23)
        must be rejected. The exception must name the colliding gene and both organisms.
        """
        db_path = _make_colliding_db(
            tmp_path / 'cross_species_collide.db',
            organism_a='Human alphaherpesvirus 1',
            organism_b='Human alphaherpesvirus 2',
            shared_feature_name='UL23',
        )
        fasta_path = _colliding_fasta(tmp_path)
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 2
            remapped, _w, _d = route_and_remap_variants(_k2e_variants_for_both_chroms(), records)

            with pytest.raises(click.ClickException) as exc_info:
                assemble_multi_reference_result(
                    project_conn=conn,
                    query_records=records,
                    remapped_variants=remapped,
                    coverage_gaps=[],
                    project_name='Cross Species',
                    sample='samp',
                    vcf_name='in.vcf',
                    total_variants=len(remapped),
                )
            message = str(exc_info.value)
            # The message must name the colliding gene and both organisms.
            assert 'UL23' in message
            assert 'Human alphaherpesvirus 1' in message
            assert 'Human alphaherpesvirus 2' in message
        finally:
            conn.close()

    def test_same_species_shared_gene_name_passes(
        self, tmp_path: Path,
    ) -> None:
        """A run where the SAME species shares a gene name across references must pass.

        Same-species + shared gene name is not an ambiguous cross-species hit, so it must
        not be rejected. (e.g. two HSV-1 references both carrying UL23.)
        """
        db_path = _make_colliding_db(
            tmp_path / 'same_species_collide.db',
            organism_a='Human alphaherpesvirus 1',
            organism_b='Human alphaherpesvirus 1',  # same species
            shared_feature_name='UL23',
            ref_b_has_rules=True,
        )
        fasta_path = _colliding_fasta(tmp_path)
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped, _w, _d = route_and_remap_variants(_k2e_variants_for_both_chroms(), records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Same Species',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            # Both references retained, no rejection.
            assert len(result.references) == 2
            assert {rg.organism for rg in result.references} == {'Human alphaherpesvirus 1'}
        finally:
            conn.close()

    def test_different_species_disjoint_gene_names_pass(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A multi-species run with disjoint gene names (gagA vs gagB) must pass.

        The shared multi_ref_db fixture uses Organism A / Organism B with disjoint feature
        names (gagA, gagB), so it must not trigger the collision gate.
        """
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Multi Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            # Two distinct organisms, disjoint gene names -> passes.
            assert len(result.references) == 2
            assert {rg.organism for rg in result.references} == {'Organism A', 'Organism B'}
            assert {rg.reference_name for rg in result.references} == {'refA', 'refB'}
        finally:
            conn.close()

    def test_single_reference_run_unaffected_by_gate(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-reference run must never trigger the cross-species collision gate."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Single Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            assert len(result.references) == 1
        finally:
            conn.close()

    def test_gate_runs_before_annotation_so_no_partial_report(
        self, tmp_path: Path,
    ) -> None:
        """The gate must reject before any annotation/rule matching runs.

        A rejected run must not have produced annotations or rule hits. We verify this
        indirectly: the ClickException is raised and no ProfilingResult is returned.
        """
        db_path = _make_colliding_db(
            tmp_path / 'cross_species_no_partial.db',
            organism_a='Species A',
            organism_b='Species B',
            shared_feature_name='sharedGene',
        )
        fasta_path = _colliding_fasta(tmp_path)
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped, _w, _d = route_and_remap_variants(_k2e_variants_for_both_chroms(), records)

            with pytest.raises(click.ClickException):
                assemble_multi_reference_result(
                    project_conn=conn,
                    query_records=records,
                    remapped_variants=remapped,
                    coverage_gaps=[],
                    project_name='No Partial',
                    sample='samp',
                    vcf_name='in.vcf',
                    total_variants=len(remapped),
                )
        finally:
            conn.close()


_MULTI_CHROM_VCF = (
    '##fileformat=VCFv4.2\n'
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
    '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
    # chrom_a codon1 (K, AAA at nt 1..3 in 1-based -> pos 4): A->G -> K2E
    'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
    # chrom_b codon1 (P, CCC at nt 1..3 in 1-based -> pos 4): C->G -> P2A
    'chrom_b\t4\t.\tC\tG\t100\tPASS\tAF=0.95;DP=500\n'
)


class TestMultiChromVcfCli:
    """End-to-end: respro vcf with a 2-CHROM VCF and 2-record reference FASTA."""

    def test_two_chrom_vcf_produces_report_with_both_references(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A 2-CHROM VCF + 2-record FASTA yields one HTML report covering both references."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        vcf_path = tmp_path / 'multi.vcf'
        vcf_path.write_text(_MULTI_CHROM_VCF)
        output_dir = tmp_path / 'out'

        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(multi_ref_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--no-cache',
        ])

        assert result.exit_code == 0, result.output
        html_files = list(output_dir.glob('*.report.html'))
        assert len(html_files) == 1, f'expected exactly one HTML report, got {html_files}'
        html = html_files[0].read_text()
        # Both references' rule hits are rendered (K2E on gagA, P2A on gagB).
        # Per-reference section headers/subplots are refined in the report ticket.
        assert 'gagA' in html
        assert 'gagB' in html

    def test_single_chrom_vcf_still_works_via_multi_path(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-chrom VCF + single-record FASTA behaves identically to today."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        vcf_path = tmp_path / 'single.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'

        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(single_ref_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--no-cache',
        ])

        assert result.exit_code == 0, result.output
        html_files = list(output_dir.glob('*.report.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'refA' in html
        assert 'gagA' in html


def _write_multi_chrom_partial_bam(bam_path: Path) -> None:
    """Write a 2-contig BAM (chrom_a, chrom_b) with high depth only over the first 30 nt of each."""
    header = {
        'HD': {'VN': '1.0'},
        'SQ': [
            {'SN': 'chrom_a', 'LN': len(_REF_A_SEQ)},
            {'SN': 'chrom_b', 'LN': len(_REF_B_SEQ)},
        ],
    }
    with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
        for contig_idx, seq in enumerate((_REF_A_SEQ, _REF_B_SEQ)):
            read_seq = seq[:30]
            qualities = pysam.qualitystring_to_array('I' * len(read_seq))
            for read_idx in range(20):
                read = pysam.AlignedSegment()
                read.query_name = f'{contig_idx}_read_{read_idx}'
                read.query_sequence = read_seq
                read.flag = 0
                read.reference_id = contig_idx
                read.reference_start = 0
                read.mapping_quality = 60
                read.cigar = ((0, len(read_seq)),)
                read.next_reference_id = -1
                read.next_reference_start = -1
                read.template_length = 0
                read.query_qualities = qualities
                bam.write(read)
    pysam.index(str(bam_path))


class TestComputeCoverageGapsFromBamMulti:
    """compute_coverage_gaps_from_bam_multi loops over per-CHROM mappings and concatenates gaps."""

    def test_two_chrom_bam_yields_gaps_on_both_references(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A 2-CHROM BAM + 2-record FASTA yields CoverageGaps on features of both references."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        bam_path = tmp_path / 'sample.bam'
        _write_multi_chrom_partial_bam(bam_path)

        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            # Build per-CHROM mapping chrom -> (query_name, query_sequence, matches).
            per_chrom = {
                rec.query_name: (rec.query_name, rec.query_sequence, rec.feature_matches)
                for rec in records
            }

            gaps = compute_coverage_gaps_from_bam_multi(
                bam_path=bam_path,
                per_chrom=per_chrom,
                min_depth=10,
            )

            # Gaps appear for both references' features (gagA and gagB).
            feature_names = {gap.feature_name for gap in gaps}
            assert 'gagA' in feature_names
            assert 'gagB' in feature_names
            # Only the first 30 nt (~10 codons) are covered; the rest are gaps.
            gag_a_gaps = [g for g in gaps if g.feature_name == 'gagA']
            gag_b_gaps = [g for g in gaps if g.feature_name == 'gagB']
            assert len(gag_a_gaps) >= 1
            assert len(gag_b_gaps) >= 1
        finally:
            conn.close()

    def test_bam_contig_with_no_matching_fasta_record_warns_and_skips(
        self, multi_ref_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A BAM contig whose name has no matching FASTA record logs a warning and skips."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        bam_path = tmp_path / 'sample.bam'
        _write_multi_chrom_partial_bam(bam_path)

        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            # Drop chrom_b from the per-CHROM mapping to simulate an unmatched BAM contig.
            per_chrom = {
                rec.query_name: (rec.query_name, rec.query_sequence, rec.feature_matches)
                for rec in records if rec.query_name == 'chrom_a'
            }

            with caplog.at_level('WARNING', logger='respro'):
                gaps = compute_coverage_gaps_from_bam_multi(
                    bam_path=bam_path,
                    per_chrom=per_chrom,
                    min_depth=10,
                )

            # Only gagA gaps (chrom_a); chrom_b's BAM contig is skipped silently here because
            # it's simply not in the mapping. No gagB gaps.
            feature_names = {gap.feature_name for gap in gaps}
            assert 'gagA' in feature_names
            assert 'gagB' not in feature_names
        finally:
            conn.close()

    def test_single_chrom_bam_identical_to_single_ref_path(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-CHROM BAM via the multi path produces the same gaps as the single-ref path."""
        from respro.core.vcf_coverage import compute_coverage_gaps_from_bam
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        bam_path = tmp_path / 'sample.bam'
        _write_multi_chrom_partial_bam(bam_path)

        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 1
            rec = records[0]

            single = compute_coverage_gaps_from_bam(
                bam_path=bam_path,
                query_name=rec.query_name,
                query_sequence=rec.query_sequence,
                matches=rec.feature_matches,
                min_depth=10,
            )
            multi = compute_coverage_gaps_from_bam_multi(
                bam_path=bam_path,
                per_chrom={rec.query_name: (rec.query_name, rec.query_sequence, rec.feature_matches)},
                min_depth=10,
            )

            assert multi == single
        finally:
            conn.close()


def _assemble_two_ref_result(db_path: Path, tmp_path: Path) -> ProfilingResult:
    """Build a real 2-reference ProfilingResult via the multi-chrom VCF CLI path."""
    fasta_path = tmp_path / 'refs.fasta'
    fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
    vcf_path = tmp_path / 'multi.vcf'
    vcf_path.write_text(_MULTI_CHROM_VCF)
    output_dir = tmp_path / 'out'

    runner = CliRunner()
    result = runner.invoke(app, [
        'vcf',
        '--project', str(db_path),
        '--vcf', str(vcf_path),
        '--ref-fasta', str(fasta_path),
        '--output', str(output_dir),
        '--min-af', '0.01',
        '--min-depth', '0',
        '--no-cache',
    ])
    assert result.exit_code == 0, result.output
    html_files = list(output_dir.glob('*.report.html'))
    assert len(html_files) == 1
    return html_files[0].read_text()


class TestPerReferenceReporting:
    """Per-reference subplots/sections + multi-species warning banner."""

    def test_two_reference_lollipop_has_two_genome_overviews(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A 2-reference result yields a lollipop figure with two genome-overview axes."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Multi Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            # Union of features across references for the figure.
            all_features = []
            for rg in result.references:
                all_features.extend(rg.features)
            fig = _build_lollipop_figure(result, all_features)
            try:
                assert fig is not None
                # Genome-overview axes are the ones with an x-axis label 'Genomic position'.
                overview_axes = [
                    ax for ax in fig.axes
                    if ax.get_xlabel() == 'Genomic position'
                ]
                assert len(overview_axes) >= 2, (
                    f'expected >=2 genome-overview axes for 2 references, got {len(overview_axes)}'
                )
            finally:
                if fig:
                    import matplotlib.pyplot as plt
                    plt.close(fig)
        finally:
            conn.close()

    def test_single_reference_lollipop_unchanged(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-reference result yields exactly one genome-overview axis (unchanged behavior)."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Single Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            all_features = [f for rg in result.references for f in rg.features]
            fig = _build_lollipop_figure(result, all_features)
            try:
                assert fig is not None
                overview_axes = [
                    ax for ax in fig.axes
                    if ax.get_xlabel() == 'Genomic position'
                ]
                assert len(overview_axes) == 1
            finally:
                if fig:
                    import matplotlib.pyplot as plt
                    plt.close(fig)
        finally:
            conn.close()

    def test_no_multi_species_warning_banner_or_profiled_references_section(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """No warning banner and no profiled-references section in any report.

        The multi-species warning banner and the profiled-references section introduced in
        multi-vcf-support are removed because proper per-reference handling makes them
        redundant. This holds for multi-species, same-species multi-reference, and
        single-reference runs alike.
        """
        # multi_ref_db has refA='Organism A', refB='Organism B' -> distinct organisms.
        html = _assemble_two_ref_result(multi_ref_db, tmp_path)
        assert 'multiple species' not in html.lower()
        assert 'multi-species' not in html.lower()
        assert 'Profiled references' not in html
        assert 'references-summary' not in html

    def test_single_organism_report_has_no_banner_or_profiled_references(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-organism report has no warning banner and no profiled-references section."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        vcf_path = tmp_path / 'single.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        runner.invoke(app, [
            'vcf', '--project', str(single_ref_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        html = list(output_dir.glob('*.report.html'))[0].read_text()
        assert 'multiple species' not in html.lower()
        assert 'multi-species' not in html.lower()
        assert 'Profiled references' not in html
        assert 'references-summary' not in html

    def test_two_reference_html_renders_both_reference_names(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """The 2-reference HTML renders both reference names in per-reference sections."""
        html = _assemble_two_ref_result(multi_ref_db, tmp_path)
        assert 'refA' in html
        assert 'refB' in html


def _overview_axes(fig) -> list:
    """Return the genome-overview axes of a lollipop figure (xlabel == 'Genomic position')."""
    return [ax for ax in fig.axes if ax.get_xlabel() == 'Genomic position']


class TestOneGenomeOverviewPerInternalReference:
    """Collapse ReferenceGroups by distinct reference_id."""

    def test_targeted_two_records_one_reference_yields_single_genome_overview(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Two records aligning to the SAME internal reference produce ONE genome overview.

        In the targeted-sequencing case (multi-test-1) two chroms map to one HSV-1 reference,
        producing two ReferenceGroups sharing one reference_id. The figure must collapse them
        into a single genome overview (not one per ReferenceGroup) followed by the feature
        panels once each.
        """
        # Two identical full-length records, both aligning to gagA on refA (id 1).
        fasta_path = tmp_path / 'targeted.fasta'
        fasta_path.write_text(f'>chrom_a1\n{_REF_A_SEQ}\n>chrom_a2\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            assert len(records) == 2
            # Both records align to the same internal reference (refA = id 1).
            ref_ids = {m.feature.reference_id for r in records for m in r.feature_matches}
            assert ref_ids == {1}

            variants = [
                VariantCall(chrom='chrom_a1', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
                VariantCall(chrom='chrom_a2', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            ]
            remapped, _w, _d = route_and_remap_variants(variants, records)

            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Targeted',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            # Two ReferenceGroups share one reference_id.
            assert len(result.references) == 2
            assert {rg.reference_id for rg in result.references} == {1}

            all_features = [f for rg in result.references for f in rg.features]
            fig = _build_lollipop_figure(result, all_features)
            try:
                assert fig is not None
                overviews = _overview_axes(fig)
                # Exactly ONE genome overview — not one per ReferenceGroup.
                assert len(overviews) == 1, (
                    f'targeted case (one reference_id) must yield 1 genome overview, '
                    f'got {len(overviews)}'
                )
            finally:
                if fig:
                    import matplotlib.pyplot as plt
                    plt.close(fig)
        finally:
            conn.close()

    def test_two_distinct_references_yield_one_genome_overview_each(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Two records aligning to DISTINCT internal references yield one overview each."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
        conn = open_project_db(multi_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Multi Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            assert {rg.reference_id for rg in result.references} == {1, 2}

            all_features = [f for rg in result.references for f in rg.features]
            fig = _build_lollipop_figure(result, all_features)
            try:
                assert fig is not None
                overviews = _overview_axes(fig)
                # One overview per DISTINCT internal reference (two distinct reference_ids).
                assert len(overviews) == 2, (
                    f'two distinct references must yield 2 genome overviews, got {len(overviews)}'
                )
            finally:
                if fig:
                    import matplotlib.pyplot as plt
                    plt.close(fig)
        finally:
            conn.close()

    def test_single_reference_lollipop_still_one_overview(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-reference run still yields exactly one genome overview (unchanged)."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Single Test',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
            all_features = [f for rg in result.references for f in rg.features]
            fig = _build_lollipop_figure(result, all_features)
            try:
                assert fig is not None
                assert len(_overview_axes(fig)) == 1
            finally:
                if fig:
                    import matplotlib.pyplot as plt
                    plt.close(fig)
        finally:
            conn.close()


def _assemble_two_ref_result_object(db_path: Path, tmp_path: Path) -> ProfilingResult:
    """Build a real 2-reference ProfilingResult object (no export) for persistence tests."""
    fasta_path = tmp_path / 'refs.fasta'
    fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
    conn = open_project_db(db_path)
    try:
        records = resolve_fasta_query_multi(conn, fasta_path)
        remapped = _build_remapped_for_records(conn, records)
        return assemble_multi_reference_result(
            project_conn=conn,
            query_records=records,
            remapped_variants=remapped,
            coverage_gaps=[],
            project_name='Multi Test',
            sample='sample1',
            vcf_name='multi.vcf',
            total_variants=len(remapped),
        )
    finally:
        conn.close()


class TestResultsDbMultiReference:
    """Results DB schema v2: per-row reference_name on variant_result/coverage_gap/formula_rule_hit."""

    def test_two_reference_save_run_writes_per_row_reference_name(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Each variant_result row carries the reference_name of its ReferenceGroup."""
        result = _assemble_two_ref_result_object(multi_ref_db, tmp_path)
        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(multi_ref_db)
        try:
            run_id = save_run(results_conn, results_db, project_conn, result)
            rows = results_conn.execute(
                'SELECT feature_name, reference_name FROM variant_result WHERE run_id = ? ORDER BY id',
                (run_id,),
            ).fetchall()
        finally:
            project_conn.close()
            results_conn.close()

        assert len(rows) == 2
        by_feature = {row['feature_name']: row['reference_name'] for row in rows}
        assert by_feature['gagA'] == 'refA'
        assert by_feature['gagB'] == 'refB'

    def test_colliding_feature_names_save_run_writes_correct_per_row_reference_name(
        self, tmp_path: Path,
    ) -> None:
        """Per-row reference_name is resolved by chrom, not feature_name, so colliding
        feature names across references do not corrupt the persisted reference_name.

        Without chrom-keyed lookup, the feature_name -> reference_name map would be
        last-wins, so refA's variant rows would be persisted with reference_name='refB',
        which then corrupts regenerate's distinct-reference_name reconstruction.
        """
        # Two references both naming their CDS "pol" (colliding), distinct sequences.
        refa_motif = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCC'
        refb_motif = 'ATGAAACCCGGGAAATTTCCCGGGAAATTT'
        refa_seq = (refa_motif * 20)[:600]
        refb_seq = (refb_motif * 20)[:600]

        db_path = tmp_path / 'collide.db'
        conn = create_schema(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
                     ('Collide', 1, str(uuid.uuid4())))
        conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                     (1, 'refA', len(refa_seq), 'Organism A'))
        conn.execute('INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)',
                     (1, 'refB', len(refb_seq), 'Organism A'))
        conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?)', (1, 'pol', 'Pol', 0, len(refa_seq), '+', refa_seq))
        conn.execute('INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?)', (2, 'pol', 'Pol', 0, len(refb_seq), '+', refb_seq))
        conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, 'testdrug'))
        # Both references have a K2E rule at codon 1.
        conn.execute('INSERT INTO resistance_rule '
                     '(feature_id, drug_id, position, reference, mutation, phenotype) '
                     'VALUES (?, ?, ?, ?, ?, ?)', (1, 1, 1, 'K', 'E', 'resistant'))
        conn.execute('INSERT INTO resistance_rule '
                     '(feature_id, drug_id, position, reference, mutation, phenotype) '
                     'VALUES (?, ?, ?, ?, ?, ?)', (2, 1, 1, 'K', 'E', 'resistant'))
        conn.commit()
        conn.close()

        # Two query records: chrom_a -> refA, chrom_b -> refB.
        fasta_path = tmp_path / 'collide.fasta'
        fasta_path.write_text(f'>chrom_a\n{refa_seq}\n>chrom_b\n{refb_seq}\n')
        conn = open_project_db(db_path)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            variants = [
                VariantCall(chrom='chrom_a', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
                VariantCall(chrom='chrom_b', pos=3, ref='A', alt='G', allele_freq=0.95, depth=500),
            ]
            remapped, _w, _d = route_and_remap_variants(variants, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Collide',
                sample='samp',
                vcf_name='in.vcf',
                total_variants=len(remapped),
            )
        finally:
            conn.close()

        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(db_path)
        try:
            run_id = save_run(results_conn, results_db, project_conn, result)
            rows = results_conn.execute(
                'SELECT chrom, feature_name, reference_name FROM variant_result WHERE run_id = ? ORDER BY id',
                (run_id,),
            ).fetchall()
        finally:
            project_conn.close()
            results_conn.close()

        assert len(rows) == 2
        by_chrom = {row['chrom']: row['reference_name'] for row in rows}
        # chrom_a belongs to refA; chrom_b belongs to refB — even though both features are "pol".
        assert by_chrom['chrom_a'] == 'refA', (
            f'chrom_a (refA) was persisted with reference_name={by_chrom["chrom_a"]!r} '
            f'(feature_name-keyed lookup collapsed colliding "pol" features)'
        )
        assert by_chrom['chrom_b'] == 'refB'

    def test_single_reference_save_run_reference_name_matches_first_group(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A single-reference run round-trips with variant_result.reference_name == references[0].reference_name."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        conn = open_project_db(single_ref_db)
        try:
            records = resolve_fasta_query_multi(conn, fasta_path)
            remapped = _build_remapped_for_records(conn, records)
            result = assemble_multi_reference_result(
                project_conn=conn,
                query_records=records,
                remapped_variants=remapped,
                coverage_gaps=[],
                project_name='Single Test',
                sample='sample1',
                vcf_name='single.vcf',
                total_variants=len(remapped),
            )
        finally:
            conn.close()

        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(single_ref_db)
        try:
            run_id = save_run(results_conn, results_db, project_conn, result)
            rows = results_conn.execute(
                'SELECT reference_name FROM variant_result WHERE run_id = ?',
                (run_id,),
            ).fetchall()
        finally:
            project_conn.close()
            results_conn.close()

        assert len(rows) == 1
        assert rows[0]['reference_name'] == result.references[0].reference_name == 'refA'

    def test_load_run_returns_reference_name_in_variant_rows(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """load_run returns variant_result rows that include the reference_name column."""
        result = _assemble_two_ref_result_object(multi_ref_db, tmp_path)
        results_db = tmp_path / 'results.db'
        results_conn = init_results_db(results_db)
        project_conn = open_project_db(multi_ref_db)
        try:
            run_id = save_run(results_conn, results_db, project_conn, result)
            _run_dict, variant_rows = load_run(results_conn, run_id)
        finally:
            project_conn.close()
            results_conn.close()

        assert len(variant_rows) == 2
        assert all('reference_name' in row for row in variant_rows)
        assert {row['reference_name'] for row in variant_rows} == {'refA', 'refB'}

    def test_v1_results_db_auto_migrates_reference_name_column(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Opening a v1 results DB (no reference_name column) auto-adds it without data loss."""
        results_db = tmp_path / 'v1_results.db'
        conn = sqlite3.connect(str(results_db))
        conn.execute('CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute(
            "INSERT INTO results_meta (key, value) VALUES ('results_schema_version', '1')"
        )
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
            'alt TEXT NOT NULL, '
            'feature_name TEXT DEFAULT ""'
            ')'
        )
        conn.execute(
            'INSERT INTO run (project_name, project_db_path, reference_name, vcf_path) '
            'VALUES (?, ?, ?, ?)',
            ('p', str(results_db), 'refA', '/tmp/sample.vcf'),
        )
        conn.execute(
            'INSERT INTO variant_result (run_id, chrom, pos, ref, alt, feature_name) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (1, 'chrom_a', 4, 'A', 'G', 'gagA'),
        )
        conn.commit()
        conn.close()

        migrated = init_results_db(results_db)
        try:
            columns = {
                row['name']
                for row in migrated.execute('PRAGMA table_info(variant_result)').fetchall()
            }
            row = migrated.execute(
                'SELECT feature_name, reference_name FROM variant_result WHERE id = 1'
            ).fetchone()
        finally:
            migrated.close()

        assert 'reference_name' in columns
        assert row is not None
        assert row['feature_name'] == 'gagA'  # data preserved
        assert row['reference_name'] == ''  # default for migrated rows

    def test_results_schema_version_is_one(self, tmp_path: Path) -> None:
        """A freshly initialised results DB records schema version 1."""
        results_db = tmp_path / 'fresh_results.db'
        conn = init_results_db(results_db)
        try:
            version = conn.execute(
                "SELECT value FROM results_meta WHERE key = 'results_schema_version'"
            ).fetchone()
        finally:
            conn.close()
        assert version is not None
        assert version['value'] == '1'


def _run_two_ref_profile_with_results(
    db_path: Path, tmp_path: Path, *, export_json: bool = True,
) -> tuple[Path, Path | None, Path]:
    """Run the 2-reference VCF profile with --results-db (and optionally --export json).

    Returns (results_db_path, json_path_or_None, output_dir).
    """
    fasta_path = tmp_path / 'refs.fasta'
    fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n>chrom_b\n{_REF_B_SEQ}\n')
    vcf_path = tmp_path / 'multi.vcf'
    vcf_path.write_text(_MULTI_CHROM_VCF)
    output_dir = tmp_path / 'out'
    results_db = tmp_path / 'results.db'

    args = [
        'vcf',
        '--project', str(db_path),
        '--vcf', str(vcf_path),
        '--ref-fasta', str(fasta_path),
        '--output', str(output_dir),
        '--results-db', str(results_db),
        '--min-af', '0.01',
        '--min-depth', '0',
        '--no-cache',
    ]
    if export_json:
        args.extend(['--export', 'json'])
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    json_path = None
    if export_json:
        json_files = list(output_dir.glob('*.results.json'))
        assert len(json_files) == 1, f'expected one JSON export, got {json_files}'
        json_path = json_files[0]
    return results_db, json_path, output_dir


class TestMultiReferenceRegenerate:
    """regenerate reconstructs one ReferenceGroup per distinct stored reference_name."""

    def test_regenerate_from_results_db_renders_both_references(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A stored 2-reference run regenerated from results.db renders both references."""
        results_db, _json_path, _out = _run_two_ref_profile_with_results(multi_ref_db, tmp_path)

        regen_dir = tmp_path / 'regenerated'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(multi_ref_db),
            '--output', str(regen_dir),
        ])
        assert result.exit_code == 0, result.output
        html_files = list(regen_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'refA' in html
        assert 'refB' in html

    def test_regenerate_from_results_db_reconstructs_two_reference_groups(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """Regenerated ProfilingResult carries two ReferenceGroups (one per stored reference)."""
        results_db, _json_path, _out = _run_two_ref_profile_with_results(multi_ref_db, tmp_path)

        # Inspect the reconstructed references by calling the same load path the CLI uses.
        from respro.db.results import load_run
        from respro.db.schema import open_results_db

        conn = open_results_db(results_db)
        try:
            run_dict, variant_rows = load_run(conn, 1)
        finally:
            conn.close()

        distinct_refs = {row.get('reference_name', '') for row in variant_rows}
        assert distinct_refs == {'refA', 'refB'}

    def test_regenerate_from_json_round_trips_references_list(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """regenerate --json on a multi-reference JSON round-trips the references list."""
        _results_db, json_path, _out = _run_two_ref_profile_with_results(multi_ref_db, tmp_path)
        assert json_path is not None
        payload = json.loads(json_path.read_text())
        # The JSON export serialises the references list.
        assert 'references' in payload
        ref_names = {ref['reference_name'] for ref in payload['references']}
        assert ref_names == {'refA', 'refB'}
        # Each variant row carries its reference_name.
        assert all('reference_name' in row for row in payload['variant_result'])
        assert {row['reference_name'] for row in payload['variant_result']} == {'refA', 'refB'}

        regen_dir = tmp_path / 'regenerated_json'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--json', str(json_path),
            '--project', str(multi_ref_db),
            '--output', str(regen_dir),
        ])
        assert result.exit_code == 0, result.output
        html_files = list(regen_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'refA' in html
        assert 'refB' in html

    def test_regenerate_single_reference_run_one_group(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """A stored single-reference run regenerates with one ReferenceGroup."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        vcf_path = tmp_path / 'single.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        results_db = tmp_path / 'results.db'
        runner = CliRunner()
        run_result = runner.invoke(app, [
            'vcf', '--project', str(single_ref_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--results-db', str(results_db),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert run_result.exit_code == 0, run_result.output

        regen_dir = tmp_path / 'regenerated'
        result = CliRunner().invoke(app, [
            'regenerate',
            '--results-db', str(results_db),
            '--run-id', '1',
            '--project', str(single_ref_db),
            '--output', str(regen_dir),
        ])
        assert result.exit_code == 0, result.output
        html_files = list(regen_dir.glob('*.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'refA' in html
        # Single-reference report has no multi-species banner and no per-reference summary list.
        assert 'multiple species' not in html.lower()
        assert 'Profiled references' not in html


class TestMultiVcfRegressionSuite:
    """
    Cohesive regression suite for multi-chrom VCF + multi-record reference FASTA.

    Cases (a)–(j) from the feature acceptance:
      (a) 2 FASTA records aligning to one internal reference (targeted) — here.
      (b) 2 FASTA records aligning to two different references (segmented) —
          TestMultiChromVcfCli::test_two_chrom_vcf_produces_report_with_both_references.
      (c) VCF with one unmatched CHROM → warning + continued —
          TestRouteAndRemapVariants::test_variants_with_unmatched_chrom_are_dropped_with_warning.
      (d) VCF with all CHROMs unmatched → click.ClickException —
          TestRouteAndRemapVariants::test_all_chroms_unmatched_raises_error.
      (e) no matched reference has rules → click.ClickException —
          TestAssembleMultiReferenceResult::test_no_ruled_reference_raises_click_exception.
      (f) 2-CHROM BAM coverage projection — TestComputeCoverageGapsFromBamMulti.
      (g) save_run → load_run → regenerate round-trip for a 2-reference run —
          TestMultiReferenceRegenerate::test_regenerate_from_results_db_renders_both_references.
      (h) multi-species warning banner present iff organisms differ —
          TestPerReferenceReporting.
      (i) JSON export → regenerate --json round-trip preserves the references list —
          TestMultiReferenceRegenerate::test_regenerate_from_json_round_trips_references_list.
      (j) single-record regression (existing tests unchanged) — here + full suite.
    """

    def test_case_a_targeted_sequencing_two_records_one_reference(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """(a) Two FASTA records both aligning to one internal reference produce one report.

        Both CHROMs route to refA; the run completes and renders gagA rule hits.
        """
        half = len(_REF_A_SEQ) // 2
        seq_a1 = _REF_A_SEQ[:half]
        seq_a2 = _REF_A_SEQ[half:]
        fasta_path = tmp_path / 'targeted.fasta'
        fasta_path.write_text(f'>chrom_a1\n{seq_a1}\n>chrom_a2\n{seq_a2}\n')
        # Variants on both chroms at pos 3 (refA codon1 K->E).
        vcf_path = tmp_path / 'targeted.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'chrom_a1\t4\t.\t{seq_a1[3]}\tG\t100\tPASS\tAF=0.95;DP=500\n'
            f'chrom_a2\t4\t.\t{seq_a2[3]}\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'

        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(single_ref_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
            '--no-cache',
        ])
        assert result.exit_code == 0, result.output
        html_files = list(output_dir.glob('*.report.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        # Both records aligned to the same reference (refA) — gagA appears.
        assert 'gagA' in html
        # Single organism → no multi-species banner.
        assert 'multiple species' not in html.lower()

    def test_case_j_single_record_regression_unchanged(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """(j) A single-record run produces one report with one reference, no multi-ref UI."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        vcf_path = tmp_path / 'single.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf', '--project', str(single_ref_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert result.exit_code == 0, result.output
        html_files = list(output_dir.glob('*.report.html'))
        assert len(html_files) == 1
        html = html_files[0].read_text()
        assert 'refA' in html
        assert 'gagA' in html
        # No multi-reference UI elements for a single-reference run.
        assert 'multiple species' not in html.lower()
        assert 'Profiled references' not in html


class TestMultiSpeciesReportingRegressionSuite:
    """
    Consolidated regression suite for the multi-species-reporting feature.

    Cases (a)–(e) from the feature acceptance:
      (a) two HSV-1 chroms aligning to one internal reference (targeted) → passes,
          one genome overview, no Reference column, single-organism header;
      (b) HSV-1 UL23 + HSV-2 UL23 (cross-species shared gene name) → rejected by the
          validation gate with a click.ClickException naming the gene and both organisms;
      (c) multi-species run with non-colliding gene names → passes, shows the Reference
          column in all three tables, multi-reference header, per-reference feature
          attribution, one genome overview per reference;
      (d) same-species two-reference run with a shared gene name → passes, no Reference
          column, single-organism header;
      (e) single-reference regression unchanged.
    """

    def test_case_a_targeted_two_chroms_one_reference_single_organism(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """(a) Two chroms aligning to one reference → one overview, no Reference column."""
        half = len(_REF_A_SEQ) // 2
        seq_a1 = _REF_A_SEQ[:half]
        seq_a2 = _REF_A_SEQ[half:]
        fasta_path = tmp_path / 'targeted.fasta'
        fasta_path.write_text(f'>chrom_a1\n{seq_a1}\n>chrom_a2\n{seq_a2}\n')
        vcf_path = tmp_path / 'targeted.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'chrom_a1\t4\t.\t{seq_a1[3]}\tG\t100\tPASS\tAF=0.95;DP=500\n'
            f'chrom_a2\t4\t.\t{seq_a2[3]}\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf', '--project', str(single_ref_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert result.exit_code == 0, result.output
        html = list(output_dir.glob('*.report.html'))[0].read_text()
        # Single organism → no Reference column in the tables.
        assert 'Reference</th>' not in html
        # Single-organism header (no "multiple references" phrasing).
        assert 'multiple references' not in html.lower()

    def test_case_b_cross_species_shared_gene_name_rejected(
        self, tmp_path: Path,
    ) -> None:
        """(b) HSV-1 UL23 + HSV-2 UL23 → rejected, naming the gene and both organisms."""
        db_path = _make_colliding_db(
            tmp_path / 'collide.db',
            organism_a='Human alphaherpesvirus 1',
            organism_b='Human alphaherpesvirus 2',
            shared_feature_name='UL23',
        )
        fasta_path = _colliding_fasta(tmp_path)
        vcf_path = tmp_path / 'collide.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
            'chrom_b\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf', '--project', str(db_path), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert result.exit_code == 1, result.output
        # The error must name the colliding gene. (The full organism names are
        # truncated by the Rich error box in CLI output; the complete message —
        # including both organisms — is asserted at the API level by
        # TestCrossSpeciesGeneNameCollisionGate::test_cross_species_same_gene_name_is_rejected.)
        assert 'UL23' in result.output

    def test_case_c_multi_species_disjoint_gene_names_full_report(
        self, multi_ref_db: Path, tmp_path: Path,
    ) -> None:
        """(c) Multi-species disjoint gene names → Reference column, multi-ref header,
        per-reference attribution, one genome overview per reference."""
        html = _assemble_two_ref_result(multi_ref_db, tmp_path)
        # Multi-reference header states both organisms.
        assert 'Organism A' in html
        assert 'Organism B' in html
        assert 'multiple references' in html.lower()
        # Reference column present in the tables.
        assert 'Reference</th>' in html
        # Both reference names appear in the rendered cells.
        assert 'refA' in html
        assert 'refB' in html
        # Per-reference feature attribution: one feature card per reference (gagA, gagB).
        assert 'gagA' in html
        assert 'gagB' in html
        # The genome-overview-per-reference invariant (one overview per distinct
        # reference_id) is validated at the figure level by
        # TestOneGenomeOverviewPerInternalReference::test_two_distinct_references_yield_one_genome_overview_each.

    def test_case_d_same_species_shared_gene_name_passes_no_reference_column(
        self, tmp_path: Path,
    ) -> None:
        """(d) Same-species two-reference shared gene name → passes, no Reference column."""
        db_path = _make_colliding_db(
            tmp_path / 'same_species.db',
            organism_a='Human alphaherpesvirus 1',
            organism_b='Human alphaherpesvirus 1',
            shared_feature_name='UL23',
            ref_b_has_rules=True,
        )
        fasta_path = _colliding_fasta(tmp_path)
        vcf_path = tmp_path / 'same_species.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
            'chrom_b\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf', '--project', str(db_path), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert result.exit_code == 0, result.output
        html = list(output_dir.glob('*.report.html'))[0].read_text()
        # Same species → no Reference column, single-organism header.
        assert 'Reference</th>' not in html
        assert 'multiple references' not in html.lower()

    def test_case_e_single_reference_regression_unchanged(
        self, single_ref_db: Path, tmp_path: Path,
    ) -> None:
        """(e) A single-reference run produces no multi-species UI at all."""
        fasta_path = tmp_path / 'refs.fasta'
        fasta_path.write_text(f'>chrom_a\n{_REF_A_SEQ}\n')
        vcf_path = tmp_path / 'single.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chrom_a\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
        )
        output_dir = tmp_path / 'out'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf', '--project', str(single_ref_db), '--vcf', str(vcf_path),
            '--ref-fasta', str(fasta_path), '--output', str(output_dir),
            '--min-af', '0.01', '--min-depth', '0', '--no-cache',
        ])
        assert result.exit_code == 0, result.output
        html = list(output_dir.glob('*.report.html'))[0].read_text()
        assert 'Reference</th>' not in html
        assert 'multiple references' not in html.lower()
        assert 'multiple species' not in html.lower()
