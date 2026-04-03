"""
Tests for report plotting helpers and focused lollipop rendering.
"""

from pathlib import Path

from respro.db.models import AnnotatedVariant, GeneRecord, VariantCall
from respro.report.plots import _apply_position_jitter, _select_plot_genes, lollipop_plot
from respro.report.results_model import ProfilingResult


def _genes() -> list[GeneRecord]:
    return [
        GeneRecord(
            id=1,
            reference_id=1,
            name='UL23',
            protein='UL23',
            start=100,
            end=400,
            strand='+',
            nt_sequence='A' * 300,
        ),
        GeneRecord(
            id=2,
            reference_id=1,
            name='UL30',
            protein='UL30',
            start=1000,
            end=1800,
            strand='+',
            nt_sequence='A' * 800,
        ),
    ]


def _result() -> ProfilingResult:
    ann_1 = AnnotatedVariant(
        variant=VariantCall(chrom='ref', pos=150, ref='A', alt='G', allele_freq=0.9, depth=100),
        gene_name='UL23',
        codon_pos=10,
        ref_aa='K',
        alt_aa='E',
        consequence='missense',
    )
    ann_2 = AnnotatedVariant(
        variant=VariantCall(chrom='ref', pos=150, ref='A', alt='T', allele_freq=0.9, depth=100),
        gene_name='UL23',
        codon_pos=10,
        ref_aa='K',
        alt_aa='N',
        consequence='missense',
    )
    ann_3 = AnnotatedVariant(
        variant=VariantCall(chrom='ref', pos=1200, ref='A', alt='G', allele_freq=0.8, depth=90),
        gene_name='UL30',
        codon_pos=20,
        ref_aa='A',
        alt_aa='V',
        consequence='missense',
    )
    return ProfilingResult(project_name='Test', annotations=[ann_1, ann_2, ann_3])


class TestPlotSelection:
    def test_select_plot_genes_intersection(self) -> None:
        selected = _select_plot_genes(_genes(), _result().cds_annotations, {'UL23'})
        assert [gene.name for gene in selected] == ['UL23']

    def test_select_plot_genes_fallback_to_variant_genes(self) -> None:
        selected = _select_plot_genes(_genes(), _result().cds_annotations, {'UL52'})
        assert [gene.name for gene in selected] == ['UL23', 'UL30']


class TestPlotJitter:
    def test_jitter_splits_overlapping_points(self) -> None:
        annotations = _result().cds_annotations
        jittered = _apply_position_jitter(annotations)
        y_by_pos_150 = [y for ann, y in jittered if ann.variant.pos == 150]

        assert len(y_by_pos_150) == 2
        assert y_by_pos_150[0] != y_by_pos_150[1]


class TestLollipopPlot:
    def test_lollipop_plot_writes_svg(self, tmp_path: Path) -> None:
        output = tmp_path / 'mutations.svg'
        lollipop_plot(_result(), _genes(), output, fmt='svg', rule_gene_names={'UL23'})

        assert output.exists()
        content = output.read_text(encoding='utf-8')
        assert '<svg' in content

