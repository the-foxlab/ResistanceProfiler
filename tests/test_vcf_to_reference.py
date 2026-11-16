"""
Reference reconciliation tests — baseline emission, call overlay, source labels.

Expectations are derived independently: each case states the supplied reference,
the VCF calls, and the expected internal-reference alleles/frequencies from the
TODO reconciliation tables (built by applying the calls to the supplied sequence
and diffing against the internal reference), never from the implementation.
Positions in comments are 1-based VCF-style; VariantCall positions are 0-based.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
from Bio.Seq import Seq
from conftest import TINY_REF_NAME, TINY_REF_SEQ, make_profiling_result

from respro.core.vcf_to_reference import (
    REFERENCE_DERIVED,
    VCF_DERIVED,
    reconcile_variants,
    reconcile_variants_for_matches,
)
from respro.db.models import (
    AnnotatedVariant,
    FeatureMatch,
    FeatureRecord,
    VariantCall,
)
from respro.db.results import load_run, reconstruct_annotations
from respro.db.schema import create_schema, init_results_db


def _feature(internal: str, *, strand: str = '+', feature_id: int = 1) -> FeatureRecord:
    return FeatureRecord(
        id=feature_id,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=len(internal),
        strand=strand,
        codon_start=0,
        nt_sequence=internal,
    )


def _match(
    feature: FeatureRecord,
    supplied: str,
    cigar: str,
    *,
    strand: str = '+',
) -> FeatureMatch:
    return FeatureMatch(
        feature=feature,
        identity=0.99,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=len(supplied),
        strand=strand,
        cigar=cigar,
    )


def _snp(pos: int, ref: str, alt: str, af: float = 1.0) -> VariantCall:
    return VariantCall(chrom='ref', pos=pos, ref=ref, alt=alt, allele_freq=af, depth=50)


def _tokens(out: list[VariantCall]) -> list[tuple]:
    return sorted(
        (v.pos, v.ref, v.alt, round(v.allele_freq, 3), v.source) for v in out
    )


def _run_plus(
    internal: str,
    supplied: str,
    cigar: str,
    calls: list[VariantCall],
) -> tuple[list[tuple], list[str]]:
    """Reconcile on a '+'-strand feature and return (tokens, warnings)."""
    out, warnings = reconcile_variants_for_matches(
        calls, [_match(_feature(internal), supplied, cigar)], supplied,
    )
    return _tokens(out), warnings


class TestTier0IdenticalReferences:
    """Identical supplied/internal references: reconciliation is a no-op baseline."""

    def test_snp_call_emits_legacy_remap_output(self) -> None:
        out, warnings = _run_plus('ACGTACGT', 'ACGTACGT', '8M', [_snp(1, 'C', 'G')])
        assert out == [(1, 'C', 'G', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_no_calls_emit_no_events(self) -> None:
        out, warnings = _run_plus('ACGTACGT', 'ACGTACGT', '8M', [])
        assert out == []
        assert warnings == []


class TestMainReconciliationTable:
    """The main TODO examples table (single-nucleotide position)."""

    def test_baseline_only_emits_reference_derived_at_af_1(self) -> None:
        # internal A, supplied G, no call -> A>G at 1.0 (assumption)
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_full_reversion_emits_no_variant(self) -> None:
        # internal A, supplied G, G>A at 1.0 -> no variant
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'A')])
        assert out == []
        assert warnings == []

    def test_partial_reversion_keeps_call_frequency(self) -> None:
        # internal A, supplied G, G>A at 0.5 -> A>G at 0.5
        out, _ = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'A', af=0.5)])
        assert out == [(0, 'A', 'G', 0.5, VCF_DERIVED)]

    def test_multi_allelic_residual_frequencies(self) -> None:
        # internal A, supplied G, G>C at 0.3 -> A>G at 0.7 and A>C at 0.3
        out, _ = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'G', 'C', af=0.3)])
        assert out == [(0, 'A', 'C', 0.3, VCF_DERIVED), (0, 'A', 'G', 0.7, VCF_DERIVED)]

    def test_below_cutoff_call_reverts_to_pure_baseline(self) -> None:
        # The CLI removes calls below min-AF before reconciliation; the position
        # then reverts to the pure baseline at AF 1.0.
        # internal A, supplied G, G>A at 0.1 (below cutoff, removed) -> A>G at 1.0
        out, _ = _run_plus('ACGT', 'GCGT', '4M', [])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]


class TestIndelInteractions:
    """The non-canceling indel interaction table (Tier 2 local windows)."""

    def test_exact_cancellation_at_full_frequency(self) -> None:
        # internal ACGT, supplied ACGGT (G inserted before T), call deletes one G
        out, warnings = _run_plus(
            'ACGT', 'ACGGT', '3M1I1M', [_snp(2, 'GG', 'G')],
        )
        assert out == []
        assert warnings == []

    def test_partial_cancellation_emits_residual_state(self) -> None:
        # same at AF 0.5: per-allele state cancels, residual keeps the insertion
        out, _ = _run_plus('ACGT', 'ACGGT', '3M1I1M', [_snp(2, 'GG', 'G', af=0.5)])
        assert out == [(2, 'G', 'GG', 0.5, VCF_DERIVED)]

    def test_adjacent_deletions_merge(self) -> None:
        # internal ACGT, supplied ACT (G deleted), call deletes supplied T:
        # sample ACT - T = AC -> net deletion of internal GT (anchored C2>CGT)
        out, _ = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(1, 'CT', 'C')])
        assert out == [(1, 'CGT', 'C', 1.0, VCF_DERIVED)]

    def test_insertions_merge_into_one(self) -> None:
        # internal AT, supplied ACGT (CG inserted), call inserts G after C:
        # sample ACGGT -> net insertion of CGG after A (anchored A1>ACGG)
        out, _ = _run_plus('AT', 'ACGT', '1M2I1M', [_snp(1, 'C', 'CG')])
        assert out == [(0, 'A', 'ACGG', 1.0, VCF_DERIVED)]

    def test_baseline_deletion_plus_call_deletion_merge(self) -> None:
        # internal ACGT, supplied ACT (G deleted), call deletes supplied C:
        # sample AT -> net deletion of internal CG (anchored A1>ACG)
        out, _ = _run_plus('ACGT', 'ACT', '2M1D1M', [_snp(0, 'AC', 'A')])
        assert out == [(0, 'ACG', 'A', 1.0, VCF_DERIVED)]

    def test_non_overlapping_separate_records_emit_independently(self) -> None:
        # identical references, C2del and C6del: two independent events
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'CG', 'C'), _snp(5, 'CG', 'C')],
        )
        assert out == [(1, 'CG', 'C', 1.0, VCF_DERIVED), (5, 'CG', 'C', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_incompatible_alleles_at_one_position_are_unverified(self) -> None:
        # identical references, C>G and C>A at the same position (AF mass 2.0)
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'C', 'G'), _snp(1, 'C', 'A')],
        )
        assert out == []
        assert any('cannot be jointly phased' in w for w in warnings)

    def test_partially_overlapping_separate_records_are_unverified(self) -> None:
        # identical references: record A spans cols 1-2 (CG>CA), record B col 2
        # (G>A); spans overlap with combined AF 1.2 -> unverified, baseline kept
        out, warnings = _run_plus(
            'ACGTACGT', 'ACGTACGT', '8M',
            [_snp(1, 'CG', 'CA', af=0.6), _snp(2, 'G', 'A', af=0.6)],
        )
        assert out == []
        assert any('Overlapping VCF records' in w for w in warnings)

    def test_ref_mismatch_skips_call_and_keeps_baseline(self) -> None:
        # REF block does not match the supplied reference
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(0, 'T', 'A')])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert any('does not match supplied' in w for w in warnings)

    def test_call_anchored_on_inserted_base_applies_to_pending(self) -> None:
        # internal AT, supplied ACGT (CG inserted); the call substitutes the
        # second inserted base (a pending slot): sample coding A[CA]T -> A>ACA
        out, warnings = _run_plus('AT', 'ACGT', '1M2I1M', [_snp(2, 'G', 'A')])
        assert out == [(0, 'A', 'ACA', 1.0, VCF_DERIVED)]
        assert warnings == []

    def test_call_outside_aligned_region_is_skipped(self) -> None:
        # call anchored beyond the match's aligned region: the REF block cannot
        # match the (empty) supplied slice, so the call is skipped and the
        # baseline is retained
        out, warnings = _run_plus('ACGT', 'GCGT', '4M', [_snp(10, 'A', 'G')])
        assert out == [(0, 'A', 'G', 1.0, REFERENCE_DERIVED)]
        assert any('skipped' in w for w in warnings)


class TestReverseStrandOrientation:
    """Minus-strand features: both coding effect and genomic allele orientation.

    The supplied reference maps reverse complement against the feature; each
    expectation is derived by applying the edit to the supplied sequence and
    reverse-complementing into coding orientation.
    """

    @staticmethod
    def _minus_feature(internal_coding: str) -> FeatureRecord:
        return _feature(internal_coding, strand='-')

    def test_identical_rc_pair_yields_no_baseline_events(self) -> None:
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), genomic, '8M', strand='-')],
            genomic,
        )
        assert _tokens(out) == []
        assert warnings == []

    def test_minus_feature_baseline_snp_genomic_orientation(self) -> None:
        # internal coding ACGGTACG, genomic CGTACCGT; supplied genomic pos 2
        # T>A -> coding col 6 G>T; emitted genomic allele T>A at pos 2
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:2] + ('A' if genomic[2] != 'A' else 'C') + genomic[3:]
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), supplied, '8M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(2, genomic[2], supplied[2], 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_minus_feature_partial_reversion(self) -> None:
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:2] + ('A' if genomic[2] != 'A' else 'C') + genomic[3:]
        call = _snp(2, supplied[2], genomic[2], af=0.5)
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '8M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(2, genomic[2], supplied[2], 0.5, VCF_DERIVED)]

    def test_minus_feature_terminal_deletion_lands_at_coding_start(self) -> None:
        # deleting the forward-LAST supplied base deletes the coding-FIRST base;
        # the emitted genomic deletion anchors at the correct end (guards the
        # past reversed-CIGAR indel bug)
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:-1]
        out, warnings = reconcile_variants_for_matches(
            [], [_match(self._minus_feature(internal), supplied, '1D7M', strand='-')],
            supplied,
        )
        # genomic ref CGTACCGT: deleted base is pos 7 (T); anchored at pos 6
        assert _tokens(out) == [(6, 'GT', 'G', 1.0, REFERENCE_DERIVED)]
        assert warnings == []

    def test_minus_feature_merged_insertion(self) -> None:
        # baseline insertion (forward C after genomic pos 3) + call inserting
        # another C -> one merged insertion A>ACC
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + 'C' + genomic[4:]
        call = _snp(4, 'C', 'CC')
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '4M1I4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(3, 'A', 'ACC', 1.0, VCF_DERIVED)]

    def test_minus_feature_full_cancellation(self) -> None:
        # baseline deletion of genomic pos 4 fully reverted by the call at 1.0
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + genomic[5:]
        call = _snp(3, 'A', 'AC')
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '3M1D4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == []

    def test_minus_feature_partial_reversion_of_deletion(self) -> None:
        # same at AF 0.5: the deletion survives at residual 0.5
        internal = 'ACGGTACG'
        genomic = str(Seq(internal).reverse_complement())
        supplied = genomic[:4] + genomic[5:]
        call = _snp(3, 'A', 'AC', af=0.5)
        out, _ = reconcile_variants_for_matches(
            [call], [_match(self._minus_feature(internal), supplied, '3M1D4M', strand='-')],
            supplied,
        )
        assert _tokens(out) == [(3, 'AC', 'A', 0.5, VCF_DERIVED)]


class TestRouting:
    """reconcile_variants routes per CHROM and restores the VCF CHROM."""

    def test_chrom_restored_and_dropped_chroms_reported(self) -> None:
        from respro.core.query import QueryRecord

        internal = 'ACGT'
        feature = _feature(internal)
        supplied = 'GCGT'
        match = _match(feature, supplied, '4M')
        record = QueryRecord(query_name='ref', query_sequence=supplied, feature_matches=[match])

        out, warnings, dropped = reconcile_variants([_snp(0, 'G', 'T')], [record])
        assert [(v.pos, v.ref, v.alt, v.source) for v in out] == [(0, 'A', 'T', VCF_DERIVED)]
        assert out[0].chrom == 'ref'
        assert dropped == []

        ghost = dc_replace(_snp(0, 'G', 'T'), chrom='ghost')
        out2, _, dropped2 = reconcile_variants([_snp(0, 'G', 'T'), ghost], [record])
        assert [(v.pos, v.ref, v.alt, v.source) for v in out2] == [(0, 'A', 'T', VCF_DERIVED)]
        assert dropped2 == ['ghost']

    def test_no_usable_chrom_raises(self) -> None:
        from respro.core.query import QueryRecord

        with pytest.raises(ValueError, match='No VCF CHROM could be reconciled'):
            reconcile_variants(
                [_snp(0, 'G', 'A')],
                [QueryRecord(query_name='other', query_sequence='ACGT', feature_matches=[])],
            )


class TestSourcePersistence:
    """The source label survives the results-DB round trip (R2)."""

    def _result(self, source: str):
        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='ref', pos=4, ref='A', alt='T', allele_freq=1.0, depth=100,
                source=source,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense',
        )
        return make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.vcf',
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )

    def test_variant_source_round_trips_through_results_db(self, tmp_path: Path) -> None:
        import uuid

        from respro.db.results import save_run

        project_db = tmp_path / 'project.db'
        proj_conn = create_schema(project_db)
        proj_conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            ('T', 6, str(uuid.uuid4())),
        )
        proj_conn.commit()

        results_db = tmp_path / 'results.db'
        conn = init_results_db(results_db)
        run_id = save_run(conn, project_db, proj_conn, self._result(VCF_DERIVED))
        run_row, variant_rows = load_run(conn, run_id)
        anns = reconstruct_annotations(variant_rows)
        assert anns[0].variant.source == VCF_DERIVED
        conn.close()
        proj_conn.close()

    def test_legacy_results_db_gains_variant_source_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / 'legacy.db'
        # Create a results DB without the variant_source column, then reopen it:
        # the optional-column migration must add it.
        conn = create_schema(db_path)  # project schema, not results — build manually
        conn.close()

        raw = sqlite3.connect(str(db_path))
        raw.executescript(
            """
            CREATE TABLE results_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                project_db_path TEXT DEFAULT '',
                reference_name TEXT DEFAULT '',
                vcf_path TEXT DEFAULT ''
            );
            CREATE TABLE variant_result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                chrom TEXT NOT NULL,
                pos INTEGER NOT NULL,
                ref TEXT NOT NULL,
                alt TEXT NOT NULL
            );
            """
        )
        raw.commit()
        raw.close()

        conn = init_results_db(db_path)
        columns = {
            row['name']
            for row in conn.execute('PRAGMA table_info(variant_result)').fetchall()
        }
        assert 'variant_source' in columns
        conn.close()


class TestVariantSourceSurfacing:
    """The source label surfaces in report rows (R5)."""

    def test_source_label_mapping(self) -> None:
        from respro.report._row_helpers import variant_source_label

        def ann_with(source: str) -> AnnotatedVariant:
            return AnnotatedVariant(
                variant=VariantCall(chrom='c', pos=0, ref='A', alt='G', source=source),
            )

        assert variant_source_label(ann_with(VCF_DERIVED)) == 'VCF derived'
        assert variant_source_label(ann_with(REFERENCE_DERIVED)) == 'Reference derived'
        assert variant_source_label(ann_with('')) == ''

    def test_all_mutations_rows_carry_source_and_flag(self) -> None:
        from respro.report.html import build_report_context

        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(
                chrom='ref', pos=4, ref='A', alt='T', allele_freq=1.0, depth=100,
                source=REFERENCE_DERIVED,
            ),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense', af_bin='high',
        )
        result = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.vcf', is_fasta_mode=False,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx['all_mutations']['has_variant_source_column'] is True
        assert ctx['all_mutations']['rows'][0]['variant_source'] == 'Reference derived'

    def test_fasta_mode_hides_variant_source(self) -> None:
        from respro.report.html import build_report_context

        feature = _feature('ATGAAGAAA')
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='gag', pos=4, ref='A', alt='T', allele_freq=1.0),
            feature_name='gag', codon_pos=1,
            ref_codon='AAG', alt_codon='ATG', ref_aa='K', alt_aa='M',
            consequence='missense', af_bin='high', is_fasta_mode=True,
        )
        result = make_profiling_result(
            project_name='T', reference_name='ref', reference_length_nt=9,
            sample_name='S', vcf_name='s.fasta', is_fasta_mode=True,
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx['all_mutations']['has_variant_source_column'] is False
        assert ctx['all_mutations']['rows'][0]['variant_source'] == ''


class TestBamAssessabilityMask:
    """Deferred BAM coverage gate (R4): annotate everything, mask, then match."""

    def test_excluded_positions_cover_gap_codons(self) -> None:
        from respro.cli.profile_helpers import _bam_excluded_positions
        from respro.db.models import CoverageGap, ReferenceGroup

        feature = _feature('ATGAAGAAA')
        rg = ReferenceGroup(
            reference_name='ref', reference_id=1, organism='',
            reference_length_nt=9, query_name='ref', query_sequence='ATGAAGAAA',
            feature_matches=[_match(feature, 'ATGAAGAAA', '9M')],
            features=[feature], rules=[], formula_rules=[], rule_feature_names=set(),
        )
        # codon 1 (0-based) = nt positions 3-5
        gaps = [CoverageGap(feature_name='gag', codon_start=1, codon_end=1, chrom='ref')]
        excluded = _bam_excluded_positions(rg, gaps)
        assert excluded == {('ref', 3), ('ref', 4), ('ref', 5)}

    def test_uncovered_variant_excluded_from_report_covered_retained(
        self,
        project_db: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        """A variant inside a non-assessable BAM codon is emitted and annotated
        first, then removed before rule matching; a covered variant is retained."""
        import re

        import pysam
        from typer.testing import CliRunner

        from respro.cli.main import app

        bam_path = tmp_path / 'query.bam'
        header = {'HD': {'VN': '1.0'}, 'SQ': [{'SN': TINY_REF_NAME, 'LN': len(TINY_REF_SEQ)}]}
        with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
            read_seq = TINY_REF_SEQ[:30]
            qualities = pysam.qualitystring_to_array('I' * len(read_seq))
            for idx in range(20):
                read = pysam.AlignedSegment()
                read.query_name = f'read_{idx}'
                read.query_sequence = read_seq
                read.flag = 0
                read.reference_id = 0
                read.reference_start = 0
                read.mapping_quality = 60
                read.cigar = ((0, len(read_seq)),)
                read.next_reference_id = -1
                read.template_length = 0
                read.query_qualities = qualities
                bam.write(read)
        pysam.index(str(bam_path))

        # covered: 0-based 3 (codon 1, K>E rule fires); uncovered: 0-based 33
        # (codon 11, nt 33-35, zero BAM depth)
        vcf_path = tmp_path / 'masked.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="DP">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
            f'{TINY_REF_NAME}\t34\t.\tG\tT\t100\tPASS\tAF=0.90;DP=500\n',
            encoding='utf-8',
        )

        def _run(with_bam: bool, name: str) -> str:
            out_dir = tmp_path / name
            args = [
                'vcf', '--project', str(project_db), '--vcf', str(vcf_path),
                '--ref-fasta', str(sample_ref_fasta), '--output', str(out_dir),
                '--min-af', '0.01', '--min-depth', '10',
            ]
            if with_bam:
                args += ['--bam', str(bam_path)]
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, result.output
            return list(out_dir.glob('*.html'))[0].read_text()

        html_without_bam = _run(False, 'out_no_bam')
        # Control: without a BAM nothing is masked, both variants are reported.
        assert 'K2E' in html_without_bam
        assert 'A12S' in html_without_bam

        html_with_bam = _run(True, 'out_bam')
        # The covered resistance hit is retained; the uncovered variant is
        # excluded from final annotations and cannot appear (or fire rules).
        assert 'K2E' in html_with_bam
        assert 'A12S' not in html_with_bam
        # Coverage gaps are preserved for reporting.
        assert re.search(r'(?i)coverage', html_with_bam)
