"""
Coverage reporting in the summary mutation profile (HTML and PDF).

Two behaviours are specified here:

1. Per-reference attribution — every reference group's features receive a
   coverage value, not only the primary reference's; coverage gaps are
   attributed to the reference group whose query record produced them.
2. Assessed semantics — a coverage percentage is only reported when coverage
   was actually measured (consensus alignment in FASTA mode, or a supplied BAM
   in VCF mode). A VCF run without a BAM states "coverage not assessed"
   instead of claiming 100%.
"""

from __future__ import annotations

import sqlite3
import textwrap
import uuid
from pathlib import Path

import pysam
from jinja2 import BaseLoader, Environment

from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FeatureMatch,
    FeatureRecord,
    ProfilingResult,
    ReferenceGroup,
    VariantCall,
)
from respro.db.schema import create_schema
from respro.report.html import build_report_context, render_html
from respro.report.non_html_exports import (
    _build_pdf_summary_context,
    _load_pdf_template_text,
)

# 12-nt CDS: 4 codons (M K A F). Used for hand-built report fixtures.
_CDS_SEQ = "ATGAAAGCTTAA"


def _make_feature(name: str, reference_id: int) -> FeatureRecord:
    return FeatureRecord(
        id=reference_id,
        reference_id=reference_id,
        name=name,
        protein=name,
        start=0,
        end=12,
        strand="+",
        codon_start=0,
        nt_sequence=_CDS_SEQ,
    )


def _make_match(feature: FeatureRecord) -> FeatureMatch:
    return FeatureMatch(
        feature=feature,
        identity=1.0,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=12,
        strand="+",
        cigar="12M",
        cds_start=0,
    )


def _make_group(
    reference_name: str,
    query_name: str,
    feature_name: str,
    organism: str = "HSV-1",
    reference_id: int = 1,
) -> ReferenceGroup:
    feature = _make_feature(feature_name, reference_id)
    return ReferenceGroup(
        reference_name=reference_name,
        reference_id=reference_id,
        organism=organism,
        reference_length_nt=1000,
        query_name=query_name,
        query_sequence=_CDS_SEQ,
        features=[feature],
        feature_matches=[_make_match(feature)],
    )


def _make_annotation(chrom: str, feature_name: str) -> AnnotatedVariant:
    return AnnotatedVariant(
        variant=VariantCall(chrom=chrom, pos=4, ref="A", alt="G", allele_freq=0.95, depth=500),
        feature_name=feature_name,
        codon_pos=1,
        ref_codon="AAA",
        alt_codon="GAA",
        ref_aa="K",
        alt_aa="E",
        consequence="missense",
        af_bin="high",
    )


def _coverage_pct_by_feature(ctx: dict) -> dict[str, int | None]:
    return {e["feature"]: e["covered_pct"] for e in ctx["summary"]["mutation_profile"]}


def _render_pdf_html(result: ProfilingResult) -> str:
    context = _build_pdf_summary_context(
        result,
        context=None,
        project_conn=None,
        rules=None,
        features=None,
    )
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_load_pdf_template_text())
    return template.render(context=context, print_css="")


class TestGeneCoveragePerReferenceGroup:
    """Coverage is computed for every reference group's features."""

    def test_features_of_all_reference_groups_receive_coverage(self) -> None:
        """Two per-CDS query records on one reference: both features get coverage."""
        result = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=2,
            variants_in_cds=2,
            coverage_assessed=True,
            annotations=[
                _make_annotation("chrom_a", "UL23"),
                _make_annotation("chrom_b", "UL30"),
            ],
            references=[
                _make_group("NC_x", "chrom_a", "UL23", reference_id=1),
                _make_group("NC_x", "chrom_b", "UL30", reference_id=1),
            ],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert _coverage_pct_by_feature(ctx) == {"UL23": 100, "UL30": 100}

    def test_coverage_gaps_attributed_to_the_owning_reference_group(self) -> None:
        """A gap on the second group's feature lowers only that feature's coverage."""
        result = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=2,
            variants_in_cds=2,
            coverage_assessed=True,
            annotations=[
                _make_annotation("chrom_a", "UL23"),
                _make_annotation("chrom_b", "UL30"),
            ],
            coverage_gaps=[
                CoverageGap(feature_name="UL30", codon_start=2, codon_end=2, chrom="chrom_b"),
            ],
            references=[
                _make_group("NC_x", "chrom_a", "UL23", reference_id=1),
                _make_group("NC_x", "chrom_b", "UL30", reference_id=1),
            ],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert _coverage_pct_by_feature(ctx) == {"UL23": 100, "UL30": 75}

    def test_multi_species_same_feature_name_attributed_per_reference(self) -> None:
        """Same-named features on different organisms keep their own coverage value."""
        result = ProfilingResult(
            project_name="T",
            organism="",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=2,
            variants_in_cds=2,
            coverage_assessed=True,
            annotations=[
                _make_annotation("chrom_a", "UL23"),
                _make_annotation("chrom_b", "UL23"),
            ],
            coverage_gaps=[
                CoverageGap(feature_name="UL23", codon_start=2, codon_end=2, chrom="chrom_b"),
            ],
            references=[
                _make_group("refA", "chrom_a", "UL23", organism="Organism A", reference_id=1),
                _make_group("refB", "chrom_b", "UL23", organism="Organism B", reference_id=2),
            ],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx["is_multi_species"] is True
        pct = {
            (e["reference_name"], e["feature"]): e["covered_pct"]
            for e in ctx["summary"]["mutation_profile"]
        }
        assert pct == {("refA", "UL23"): 100, ("refB", "UL23"): 75}

    def test_single_species_merged_rows_take_conservative_minimum(self) -> None:
        """Same-species same-named features merge into one row: report the minimum."""
        result = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=2,
            variants_in_cds=2,
            coverage_assessed=True,
            annotations=[
                _make_annotation("chrom_a", "UL23"),
                _make_annotation("chrom_b", "UL23"),
            ],
            coverage_gaps=[
                CoverageGap(feature_name="UL23", codon_start=2, codon_end=2, chrom="chrom_b"),
            ],
            references=[
                _make_group("refA", "chrom_a", "UL23", reference_id=1),
                _make_group("refB", "chrom_b", "UL23", reference_id=2),
            ],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert _coverage_pct_by_feature(ctx) == {"UL23": 75}


class TestCoverageAssessedSemantics:
    """Coverage percentages are only reported when coverage was measured."""

    def _vcf_result(self, coverage_assessed: bool) -> ProfilingResult:
        return ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=1,
            variants_in_cds=1,
            coverage_assessed=coverage_assessed,
            annotations=[_make_annotation("chrom_a", "UL23")],
            references=[_make_group("NC_x", "chrom_a", "UL23", reference_id=1)],
        )

    def test_vcf_without_bam_states_coverage_not_assessed(self) -> None:
        """VCF mode without a BAM must not claim 100%: it states 'not assessed'."""
        result = self._vcf_result(coverage_assessed=False)
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx["summary"]["has_coverage"] is False
        assert ctx["summary"]["coverage_not_assessed"] is True
        assert all(e["covered_pct"] is None for e in ctx["summary"]["mutation_profile"])

        html = render_html(result, similarity_high=1, similarity_moderate=0)
        assert "n.d." in html
        assert "feature-coverage-na" not in html
        assert "100% covered" not in html
        assert "None%" not in html

    def test_vcf_with_bam_reports_covered_percentage(self) -> None:
        """VCF mode with a BAM reports the measured percentage (no invalid None%)."""
        result = self._vcf_result(coverage_assessed=True)
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx["summary"]["has_coverage"] is True
        assert ctx["summary"]["coverage_not_assessed"] is False

        html = render_html(result, similarity_high=1, similarity_moderate=0)
        assert "width: 100%" in html
        assert "None%" not in html

    def test_fasta_mode_coverage_is_always_assessed(self) -> None:
        """FASTA mode measures coverage from the alignment: assessed without a flag."""
        result = self._vcf_result(coverage_assessed=False)
        result.is_fasta_mode = True
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx["summary"]["has_coverage"] is True
        assert ctx["summary"]["coverage_not_assessed"] is False

    def test_narrative_states_unassessed_coverage_for_vcf_without_bam(self) -> None:
        """The interpretation narrative carries the not-assessed caveat."""
        result = self._vcf_result(coverage_assessed=False)
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert "coverage was not assessed" in ctx["summary"]["narrative"]

    def test_narrative_omits_unassessed_caveat_when_coverage_measured(self) -> None:
        """No not-assessed caveat when coverage was measured and no gaps exist."""
        result = self._vcf_result(coverage_assessed=True)
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert "coverage was not assessed" not in ctx["summary"]["narrative"]


class TestPdfCoverageReporting:
    """The PDF summary reports coverage for every feature and states unassessed coverage."""

    def _two_feature_result(self, coverage_assessed: bool) -> ProfilingResult:
        return ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=2,
            variants_in_cds=2,
            coverage_assessed=coverage_assessed,
            annotations=[
                _make_annotation("chrom_a", "UL23"),
                _make_annotation("chrom_b", "UL30"),
            ],
            references=[
                _make_group("NC_x", "chrom_a", "UL23", reference_id=1),
                _make_group("NC_x", "chrom_b", "UL30", reference_id=1),
            ],
        )

    def test_pdf_states_coverage_not_assessed_without_bam(self) -> None:
        """The PDF states 'coverage not assessed' instead of a percentage."""
        html = _render_pdf_html(self._two_feature_result(coverage_assessed=False))
        assert html.count("coverage not assessed") == 2
        assert "100% covered" not in html
        assert "None%" not in html

    def test_pdf_reports_coverage_for_every_feature(self) -> None:
        """Every feature row carries its coverage percentage in the PDF."""
        html = _render_pdf_html(self._two_feature_result(coverage_assessed=True))
        assert html.count("(100% covered)") == 2
        assert "None%" not in html


class TestFeatureBadgeCoverage:
    """The feature badge carries the coverage statement; clean features stay listed."""

    def test_feature_without_mutations_still_listed(self) -> None:
        """An aligned feature with no non-synonymous mutations keeps its badge row."""
        result = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=0,
            variants_in_cds=0,
            coverage_assessed=True,
            annotations=[],
            references=[_make_group("NC_x", "chrom_a", "UL23", reference_id=1)],
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        assert ctx["summary"]["mutation_profile"] == [
            {"feature": "UL23", "mutations": [], "covered_pct": 100},
        ]

    def test_badge_shows_percent_and_nd_in_html(self) -> None:
        """Badge shows the percent int when measured and n.d. when not assessed."""
        measured = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=0,
            variants_in_cds=0,
            coverage_assessed=True,
            annotations=[],
            references=[_make_group("NC_x", "chrom_a", "UL23", reference_id=1)],
        )
        html = render_html(measured, similarity_high=1, similarity_moderate=0)
        assert 'feature-mutation-coverage">100%</span>' in html
        assert 'feature-mutation-coverage">n.d.' not in html

        unassessed = ProfilingResult(
            project_name="T",
            organism="HSV-1",
            sample_name="S",
            vcf_name="t.vcf",
            total_variants=0,
            variants_in_cds=0,
            coverage_assessed=False,
            annotations=[],
            references=[_make_group("NC_x", "chrom_a", "UL23", reference_id=1)],
        )
        html = render_html(unassessed, similarity_high=1, similarity_moderate=0)
        assert 'feature-mutation-coverage">n.d.</span>' in html
        assert "feature-coverage-na" not in html


# ──────────────────────────────────────────────────────────────────────
# End-to-end: live CLI run and regeneration with real alignment/BAM data
# ──────────────────────────────────────────────────────────────────────

# Two distinct 600-nt CDS sequences used as the two rule-backed features of one
# internal reference. Per-CDS query records (one per feature) then produce two
# reference groups sharing the same reference name — the multi-test topology.
_E2E_A_MOTIF = "ATGAAAGCTTTTGGCCCCAAATTTGGGCCC"
_E2E_B_MOTIF = "ATGCCCCGGGAAATTTCCCGGGAAATTTGG"
_E2E_A_SEQ = (_E2E_A_MOTIF * 20)[:600]
_E2E_B_SEQ = (_E2E_B_MOTIF * 20)[:600]


def _make_two_feature_db(db_path: Path) -> Path:
    """Project DB with one reference carrying two rule-backed CDS features."""
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)",
        ("Coverage E2E", 1, str(uuid.uuid4())),
    )
    conn.execute(
        "INSERT INTO reference (project_id, name, length, organism) VALUES (?, ?, ?, ?)",
        (1, "refA", 1200, "HSV-1"),
    )
    conn.execute(
        "INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "gagA", "GagA", 0, len(_E2E_A_SEQ), "+", _E2E_A_SEQ),
    )
    conn.execute(
        "INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "gagB", "GagB", 0, len(_E2E_B_SEQ), "+", _E2E_B_SEQ),
    )
    conn.execute("INSERT INTO drug (project_id, name) VALUES (?, ?)", (1, "testdrug"))
    # gagA codon 1 (0-based) = AAA (K) at nt 3..5; K->E rule.
    conn.execute(
        "INSERT INTO resistance_rule "
        "(feature_id, drug_id, position, reference, mutation, phenotype) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (1, 1, 1, "K", "E", "resistant"),
    )
    # gagB codon 1 (0-based) = CCC (P) at nt 3..5; P->A rule.
    conn.execute(
        "INSERT INTO resistance_rule "
        "(feature_id, drug_id, position, reference, mutation, phenotype) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (2, 1, 1, "P", "A", "resistant"),
    )
    conn.execute("UPDATE feature SET has_rules = 1 WHERE id IN (1, 2)")
    conn.commit()
    conn.close()
    return db_path


def _write_e2e_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write per-CDS query FASTA, a one-SNP-per-CHROM VCF, and a full-depth BAM."""
    fasta_path = tmp_path / "refs.fasta"
    fasta_path.write_text(f">chrom_a\n{_E2E_A_SEQ}\n>chrom_b\n{_E2E_B_SEQ}\n")

    vcf_path = tmp_path / "variants.vcf"
    # Codon 1 (0-based) of each CDS starts at 1-based position 4.
    vcf_path.write_text(
        textwrap.dedent(f"""\
        ##fileformat=VCFv4.0
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        chrom_a\t4\t.\t{_E2E_A_SEQ[3]}\tG\t100\tPASS\tAF=0.95;DP=500
        chrom_b\t4\t.\t{_E2E_B_SEQ[3]}\tA\t100\tPASS\tAF=0.95;DP=500
        """)
    )

    bam_path = tmp_path / "reads.bam"
    header = {
        "HD": {"VN": "1.0"},
        "SQ": [
            {"SN": "chrom_a", "LN": len(_E2E_A_SEQ)},
            {"SN": "chrom_b", "LN": len(_E2E_B_SEQ)},
        ],
    }
    with pysam.AlignmentFile(str(bam_path), "wb", header=header) as bam:
        for contig_idx, seq in ((0, _E2E_A_SEQ), (1, _E2E_B_SEQ)):
            for read_idx in range(12):
                a = pysam.AlignedSegment()
                a.query_name = f"read{contig_idx}_{read_idx}"
                a.query_sequence = seq
                a.flag = 0
                a.reference_id = contig_idx
                a.reference_start = 0
                a.mapping_quality = 60
                a.cigar = ((0, len(seq)),)
                a.query_qualities = pysam.qualitystring_to_array("I" * len(seq))
                a.next_reference_id = -1
                a.next_reference_start = -1
                a.template_length = 0
                bam.write(a)
    pysam.index(str(bam_path))
    return fasta_path, vcf_path, bam_path


class TestCoverageEndToEnd:
    """Live CLI runs and regeneration report coverage for every feature."""

    def _run_profile(
        self,
        project_db: Path,
        fasta_path: Path,
        vcf_path: Path,
        bam_path: Path | None,
        tmp_path: Path,
        out_name: str,
    ) -> Path:
        from typer.testing import CliRunner

        from respro.cli.main import app

        out_dir = tmp_path / out_name
        results_db = tmp_path / f"{out_name}.results.db"
        args = [
            "vcf",
            "--project",
            str(project_db),
            "--vcf",
            str(vcf_path),
            "--ref-fasta",
            str(fasta_path),
            "--results-db",
            str(results_db),
            "--output",
            str(out_dir),
            "--min-af",
            "0.01",
            "--min-depth",
            "10",
            "--export",
            "json",
        ]
        if bam_path is not None:
            args += ["--bam", str(bam_path)]
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        return out_dir

    def test_live_report_with_bam_shows_coverage_for_both_features(self, tmp_path: Path) -> None:
        project_db = _make_two_feature_db(tmp_path / "proj.db")
        fasta_path, vcf_path, bam_path = _write_e2e_inputs(tmp_path)

        out_dir = self._run_profile(
            project_db, fasta_path, vcf_path, bam_path, tmp_path, "live_bam"
        )
        html = (out_dir / "variants.report.html").read_text(encoding="utf-8")
        # Assert on the feature-coverage bar markup itself: plain 'width: 100%'
        # also occurs in the embedded CSS and cannot discriminate covered from
        # unassessed features.
        assert html.count('class="feature-coverage-bar"') >= 2
        assert html.count("width: 100%") >= 2
        assert "coverage not assessed" not in html
        assert "None%" not in html

    def test_vcf_run_without_bam_states_not_assessed_in_live_report(self, tmp_path: Path) -> None:
        project_db = _make_two_feature_db(tmp_path / "proj.db")
        fasta_path, vcf_path, _ = _write_e2e_inputs(tmp_path)

        out_dir = self._run_profile(project_db, fasta_path, vcf_path, None, tmp_path, "live_nobam")
        html = (out_dir / "variants.report.html").read_text(encoding="utf-8")
        assert html.count('class="feature-coverage-bar"') == 0
        assert html.count("n.d.") >= 2
        assert "100% covered" not in html
        assert "None%" not in html

    def test_regenerated_run_keeps_coverage_for_both_features(self, tmp_path: Path) -> None:
        """Regeneration from the results DB must not lose the second feature's coverage."""
        from typer.testing import CliRunner

        from respro.cli.main import app

        project_db = _make_two_feature_db(tmp_path / "proj.db")
        fasta_path, vcf_path, bam_path = _write_e2e_inputs(tmp_path)
        self._run_profile(project_db, fasta_path, vcf_path, bam_path, tmp_path, "regen_in")

        out_dir = tmp_path / "regen_out"
        result = CliRunner().invoke(
            app,
            [
                "regenerate",
                "--results-db",
                str(tmp_path / "regen_in.results.db"),
                "--run-id",
                "1",
                "--project",
                str(project_db),
                "--output",
                str(out_dir),
            ],
        )
        assert result.exit_code == 0, result.output

        html_files = list(out_dir.glob("*.report.html"))
        assert len(html_files) == 1
        html = html_files[0].read_text(encoding="utf-8")
        assert html.count('class="feature-coverage-bar"') >= 2
        assert html.count("width: 100%") >= 2
        assert "coverage not assessed" not in html
        assert "None%" not in html
