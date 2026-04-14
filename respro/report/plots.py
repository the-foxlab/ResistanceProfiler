"""
Publication-ready plots for genome overview and gene-level mutation tracks.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from respro.db.models import AnnotatedVariant, CoverageGap, GeneRecord, ProfilingResult
from respro.report.palette import (
    GENE_BASELINE_COLOUR,
    GENE_DEFAULT_COLOUR,
    GENE_DEFAULT_EDGE,
    GENE_HIGHLIGHTED_COLOUR,
    GENE_HIGHLIGHTED_EDGE,
    MUTATION_COLOURS,
    NON_COVERED_COLOUR,
    mutation_legend_patches,
)

matplotlib.use('Agg')
logger = logging.getLogger(__name__)


def lollipop_plot(
    result: ProfilingResult,
    genes: list[GeneRecord],
    output_path: Path,
    fmt: str = 'svg',
    rule_gene_names: set[str] | None = None,
) -> Path:
    """
    Create a report plot with genome overview and gene-level mutation panels.

    :param result: profiling result with annotated variants
    :param genes: gene records for drawing the overview and gene panels
    :param output_path: file path for the saved figure
    :param fmt: output format (svg, png)
    :param rule_gene_names: optional rule-backed gene names to focus gene panels
    :return: path to saved figure
    """
    output_path = Path(output_path)
    fig = _build_lollipop_figure(result, genes, rule_gene_names=rule_gene_names)
    if fig is None:
        return output_path

    fig.savefig(output_path, format=fmt, dpi=150)
    plt.close(fig)

    logger.info('Lollipop plot saved to %s', output_path)
    return output_path


def render_lollipop_plot_bytes(
    result: ProfilingResult,
    genes: list[GeneRecord],
    fmt: str = 'svg',
    rule_gene_names: set[str] | None = None,
) -> bytes | None:
    """
    Render the report plot to an in-memory byte string.

    :param result: profiling result with annotated variants
    :param genes: gene records for drawing the overview and gene panels
    :param fmt: output format (svg, png)
    :param rule_gene_names: optional rule-backed gene names to focus gene panels
    :return: plot bytes or None when no plot could be generated
    """
    fig = _build_lollipop_figure(result, genes, rule_gene_names=rule_gene_names)
    if fig is None:
        return None

    buf = BytesIO()
    fig.savefig(buf, format=fmt, dpi=150)
    plt.close(fig)
    return buf.getvalue()


def _build_lollipop_figure(
    result: ProfilingResult,
    genes: list[GeneRecord],
    rule_gene_names: set[str] | None = None,
):
    """Build the matplotlib figure used by the HTML and PDF reports."""
    cds = result.cds_annotations
    coverage_gaps_by_gene = _group_coverage_gaps_by_gene(result.coverage_gaps)
    if not cds and not coverage_gaps_by_gene:
        logger.warning('No CDS variants to plot')
        return None

    plot_genes = _select_plot_genes(
        genes,
        cds,
        rule_gene_names,
        coverage_gene_names=set(coverage_gaps_by_gene),
    )
    if not plot_genes:
        logger.warning('No genes selected for plotting')
        return None

    selected_gene_names = {gene.name for gene in plot_genes}
    cds = [ann for ann in cds if ann.gene_name in selected_gene_names]
    coverage_gaps_by_gene = {
        gene_name: gaps
        for gene_name, gaps in coverage_gaps_by_gene.items()
        if gene_name in selected_gene_names
    }
    if not cds and not coverage_gaps_by_gene:
        logger.warning('No CDS variants in selected plot genes')
        return None

    gene_annotations = _group_annotations_by_gene(cds, plot_genes)

    # One row for overview, then two rows per gene (track + lollipop)
    height_ratios = [2.0, 0.5] * len(plot_genes) + [0.5]
    # Cap height so the overview typically fits a full-size 1080p browser window.
    fig_height = 2 + 3.4 * len(plot_genes)
    fig, axes = plt.subplots(
        len(height_ratios),
        1,
        figsize=(16, fig_height),
        height_ratios=height_ratios
    )
    axes_list = list(axes if isinstance(axes, (list, tuple)) else axes.flat)
    overview_ax = axes_list[-1]
    gene_pair_axes = axes_list[0:-1]

    highlighted_gene_names = set(gene_annotations)
    _draw_genome_overview(
        overview_ax,
        genes,
        highlighted_gene_names,
        reference_length_nt=result.reference_length_nt,
    )
    # Legend handles
    effects_for_legend = {ann.consequence for ann in cds}
    handles = [
        plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
                   markeredgecolor='black', markersize=8, label='Database hit'),
        mpatches.Patch(facecolor=NON_COVERED_COLOUR, alpha=0.12, edgecolor='none', label='non covered'),
        *mutation_legend_patches(effects_for_legend),
    ]

    for i, gene in enumerate(plot_genes):
        lollipop_ax = gene_pair_axes[2 * i]
        track_ax = gene_pair_axes[2 * i + 1]
        annotations = gene_annotations.get(gene.name, [])
        _draw_gene_panel(
            lollipop_ax,
            gene,
            annotations,
            coverage_gaps=coverage_gaps_by_gene.get(gene.name, []),
            shared_track_ax=track_ax,
        )
        _draw_gene_track(track_ax, gene)

    gene_pair_axes[0].legend(handles=handles, loc='upper right', fontsize=7, ncol=len(handles), frameon=False, bbox_to_anchor=(1, 1.25), borderaxespad=0.0)
    plt.tight_layout()
    return fig


def _select_plot_genes(
    genes: list[GeneRecord],
    annotations: list,
    rule_gene_names: set[str] | None,
    coverage_gene_names: set[str] | None = None,
) -> list[GeneRecord]:
    """
    Keep genes that have detected variants and resistance rules.

    :param genes: all genes on the selected reference
    :param annotations: CDS annotations from profiling result
    :param rule_gene_names: genes with loaded resistance rules
    :return: ordered list of genes to render in the plot
    """
    variant_gene_names = {ann.gene_name for ann in annotations if ann.gene_name}
    covered_gap_gene_names = coverage_gene_names or set()
    if rule_gene_names is None:
        selected_names = variant_gene_names | covered_gap_gene_names
    else:
        selected_names = (variant_gene_names | covered_gap_gene_names) & set(rule_gene_names)
        if not selected_names:
            selected_names = variant_gene_names | covered_gap_gene_names

    return sorted(
        [gene for gene in genes if gene.name in selected_names],
        key=lambda g: (g.start, g.end, g.name),
    )


def _group_annotations_by_gene(
    annotations: list[AnnotatedVariant],
    genes: list[GeneRecord],
) -> dict[str, list[AnnotatedVariant]]:
    """
    Return annotations grouped by gene name in genomic order.

    :param annotations: CDS annotations
    :param genes: genes that should be represented in the figure
    :return: mapping of gene name to sorted annotations
    """
    gene_names = {gene.name for gene in genes}
    grouped: dict[str, list[AnnotatedVariant]] = {gene.name: [] for gene in genes}
    for ann in annotations:
        if ann.gene_name in gene_names:
            grouped[ann.gene_name].append(ann)

    return {
        gene.name: sorted(
            grouped[gene.name],
            key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
        )
        for gene in genes
        if grouped[gene.name]
    }


def _assign_gene_tracks(genes: list[GeneRecord]) -> dict[str, int]:
    """
    Assign genes to non-overlapping overview tracks.

    :param genes: genes to place
    :return: mapping of gene name to overview track index
    """
    last_end_by_track: list[int] = []
    tracks: dict[str, int] = {}

    for gene in sorted(genes, key=lambda item: (item.start, item.end, item.name)):
        assigned_track = None
        for track_idx, last_end in enumerate(last_end_by_track):
            if gene.start >= last_end:
                assigned_track = track_idx
                last_end_by_track[track_idx] = gene.end
                break
        if assigned_track is None:
            assigned_track = len(last_end_by_track)
            last_end_by_track.append(gene.end)
        tracks[gene.name] = assigned_track

    return tracks


def _draw_genome_overview(
    ax,
    genes: list[GeneRecord],
    highlighted_gene_names: set[str],
    reference_length_nt: int | None = None,
) -> None:
    """
    Draw a whole-genome gene overview and highlight affected resistance genes.

    :param ax: matplotlib axis
    :param genes: all genes on the resolved reference
    :param highlighted_gene_names: genes with plotted mutations
    """
    sorted_genes = sorted(genes, key=lambda gene: (gene.start, gene.end, gene.name))
    if not sorted_genes:
        ax.set_axis_off()
        return

    tracks = _assign_gene_tracks(sorted_genes)
    genome_start, genome_end = _resolve_overview_bounds(sorted_genes, reference_length_nt)
    max_track = max(tracks.values(), default=0)
    track_height = 0.44
    track_step = track_height * 0.2
    # Baseline beneath the tracks
    ax.hlines(0.0, genome_start, genome_end, color=GENE_BASELINE_COLOUR, linewidth=1.0, zorder=1)

    for gene in sorted_genes:
        track = tracks[gene.name]
        y = -(track * track_step)
        left = gene.start + 1
        width = max(1, gene.end - gene.start)
        is_highlighted = gene.name in highlighted_gene_names
        colour = GENE_HIGHLIGHTED_COLOUR if is_highlighted else GENE_DEFAULT_COLOUR
        edge = GENE_HIGHLIGHTED_EDGE if is_highlighted else GENE_DEFAULT_EDGE
        rect = mpatches.Rectangle(
            (left, y - (track_height / 2.0)),
            width,
            track_height,
            facecolor=colour,
            edgecolor=edge,
            linewidth=0.7,
            zorder=100+y,
        )
        ax.add_patch(rect)

        if is_highlighted:
            label_y = y + (track_height / 2.0) + 0.11
            label_x = left + (width / 2)
            ax.text(
                label_x,
                label_y,
                gene.name,
                ha='center',
                va='bottom',
                fontsize=8,
                fontweight='bold',
                color=GENE_HIGHLIGHTED_EDGE,
            )

    ax.set_title('Genome overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_xlim(genome_start, genome_end)
    lower = -(max_track * track_step) - (track_height / 2.0) - 0.2
    ax.set_ylim(lower, 0.9)
    ax.set_yticks([])
    ax.set_xlabel('Genomic position')
    ax.spines['top'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['right'].set_visible(False)


def _resolve_overview_bounds(
    genes: list[GeneRecord],
    reference_length_nt: int | None,
) -> tuple[int, int]:
    """
    Return overview x-limits based on reference length, with safe fallback.

    :param genes: genes on the resolved reference
    :param reference_length_nt: full reference length in nucleotides
    :return: (start_1based, end_1based)
    """
    if reference_length_nt is not None and reference_length_nt > 0:
        return 1, reference_length_nt

    if not genes:
        return 1, 1

    logger.warning(
        'Reference length missing in result object; falling back to CDS-derived overview bounds.'
    )
    return 1, max(gene.end for gene in genes)


def _draw_gene_track(ax, gene: GeneRecord) -> None:
    """
    Draw a simple gene track visualization above the lollipop plot.

    :param ax: matplotlib axis
    :param gene: gene to render
    """
    # Draw gene arrow/rectangle
    gene_width = gene.end - gene.start
    pad = max(10, int((gene.end - gene.start) * 0.03))
    ax.hlines(0.5, gene.start + 1 - pad, gene.end + pad, color=GENE_BASELINE_COLOUR, linewidth=1.0, zorder=-10)

    rect = mpatches.Rectangle(
        (gene.start + 1, 0.3),
        gene_width,
        0.4,
        facecolor=GENE_HIGHLIGHTED_COLOUR,
        edgecolor=GENE_HIGHLIGHTED_EDGE,
        alpha=1,
        linewidth=1.0,
        zorder=2,
    )
    ax.add_patch(rect)

    # Add gene label in the middle
    gene_x = gene.start + 1 + (gene_width / 2)
    ax.text(
        gene_x,
        0.5,
        f'← {gene.name} ←' if gene.strand == '-' else f'→ {gene.name} →',
        ha='center',
        va='center',
        fontsize=8,
        fontweight='bold',
        color='white',
    )

    ax.set_title('Gene overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlim(max(1, gene.start + 1 - pad), gene.end + pad)
    ax.spines['top'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlabel('')
    ax.set_ylabel('')


def _draw_gene_panel(
    ax,
    gene: GeneRecord,
    annotations: list[AnnotatedVariant],
    coverage_gaps: list[CoverageGap] | None = None,
    shared_track_ax=None,
) -> None:
    """
    Draw one gene-focused bent lollipop panel.

    :param ax: matplotlib axis
    :param gene: gene to render
    :param annotations: annotations belonging to the gene
    :param shared_track_ax: optional track axis to share x-limits with
    """

    marker_shapes = {
        'database_hit': 's',
        'no_database_hit': 'o',
    }

    _draw_non_covered_regions(ax, gene, coverage_gaps or [])

    jittered = _apply_top_jitter(annotations, gene_length=gene.end - gene.start)
    for ann, x_top in jittered:
        x_base = ann.variant.pos + 1
        y_top = ann.variant.allele_freq
        colour = MUTATION_COLOURS.get(ann.consequence, MUTATION_COLOURS['unknown'])
        _draw_bent_lollipop(ax, x_base, x_top, y_top, colour)
        ax.scatter(
            x_top,
            y_top,
            color=colour,
            marker=(
                marker_shapes['database_hit'] if ann.is_resistance_hit
                else marker_shapes['no_database_hit']
            ),
            s=50 if ann.is_resistance_hit else 40,
            zorder=4,
            edgecolors='black' if ann.is_resistance_hit else  'white',
            linewidths=0.5,
        )

        if ann.is_resistance_hit:
            label = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            # Alternate text alignment left/right to reduce label overlap
            ax.annotate(
                label,
                (x_top, y_top),
                textcoords='offset points',
                xytext=(0, 7),
                fontsize=7,
                color=colour,
                fontweight='bold',
                rotation=90,
                ha='center',
            )

    pad = max(10, int((gene.end - gene.start) * 0.03))
    ax.hlines(0.0, gene.start + 1 - pad, gene.end + pad, color='black', linewidth=1.0, zorder=1, linestyle='--')
    ax.set_xlim(max(1, gene.start + 1 - pad), gene.end + pad)
    ax.set_ylim(-0.19, 1.05)
    ax.set_ylabel('variant frequency')
    ax.set_title(
        f'{gene.name} variants',
        fontsize=9,
        loc='left',
        pad=25,
        fontweight='bold',
    )
    ax.grid(axis='y', color='#eef2f6', linewidth=0.8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#aab4be')
    ax.spines['bottom'].set_color('#aab4be')

    # Share x-axis with track if provided
    if shared_track_ax is not None:
        shared_track_ax.set_xlim(ax.get_xlim())


def _apply_top_jitter(
    annotations: list[AnnotatedVariant],
    gene_length: int,
) -> list[tuple[AnnotatedVariant, float]]:
    """
    Add deterministic horizontal jitter enforcing minimum separation.

    The stem stays anchored at the true genomic position. Only the top dot is
    shifted slightly to separate overlapping points.

    The minimum separation is 1/100 of the gene length so jitter scales
    consistently across genes of different sizes.

    :param annotations: annotations within one gene panel
    :param gene_length: length of the gene in nucleotides (end - start)
    :return: list of (annotation, jittered_x)
    """
    sorted_anns = sorted(
        annotations,
        key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
    )
    x_values = [ann.variant.pos + 1 for ann in sorted_anns]
    min_distance = gene_length / 100
    x_values_jittered = adjust_array_min_distance(x_values, min_distance=min_distance)
    return list(zip(sorted_anns, x_values_jittered))


def _group_coverage_gaps_by_gene(coverage_gaps: list[CoverageGap]) -> dict[str, list[CoverageGap]]:
    """Group coverage gaps by gene name ordered by codon_start."""
    grouped: dict[str, list[CoverageGap]] = {}
    for gap in coverage_gaps:
        grouped.setdefault(gap.gene_name, []).append(gap)
    for gene_name in grouped:
        grouped[gene_name].sort(key=lambda gap: gap.codon_start)
    return grouped


def _draw_non_covered_regions(ax, gene: GeneRecord, coverage_gaps: list[CoverageGap]) -> None:
    """Draw pre-merged non-covered codon stretches as a low-alpha background overlay."""
    for gap in coverage_gaps:
        left_start, _ = _codon_nt_span(gene, gap.codon_start)
        _, right_end = _codon_nt_span(gene, gap.codon_end)
        ax.axvspan(
            left_start + 0.5,
            right_end + 0.5,
            ymin=0.0,
            ymax=1.0,
            facecolor=NON_COVERED_COLOUR,
            alpha=0.12,
            linewidth=0,
            zorder=0,
        )


def _codon_nt_span(gene: GeneRecord, codon_pos: int) -> tuple[int, int]:
    """Return 0-based genomic nt interval [start, end) for one codon index."""
    cds_nt_start = gene.codon_start + codon_pos * 3
    if gene.strand == '+':
        genomic_start = gene.start + cds_nt_start
        return genomic_start, genomic_start + 3

    genomic_high = (gene.end - 1) - cds_nt_start
    genomic_start = genomic_high - 2
    return genomic_start, genomic_high + 1



def adjust_array_min_distance(
    values: list[float],
    min_distance: float,
    max_iterations: int = 1000,
    tolerance: float = 1e-6,
) -> list[float]:
    """
    Adjust 1D values to keep a minimum distance with minimal deviation.

    The algorithm iteratively walks sorted values and separates neighboring
    entries when they are closer than ``min_distance``.

    :param values: values to adjust
    :param min_distance: required minimum distance between values
    :param max_iterations: upper bound on refinement iterations
    :param tolerance: convergence threshold for max per-iteration adjustment
    :return: adjusted values with same ordering as input
    """
    values_arr = np.array(values, dtype=float)
    n = len(values_arr)
    if n == 0:
        return []
    if n == 1:
        return values_arr.tolist()

    for _ in range(max_iterations):
        # Re-sort each iteration: value mutations from previous passes change the ordering.
        sorted_indices = np.argsort(values_arr, kind='stable')
        max_adjustment = 0.0
        for i in range(1, n):
            idx1 = sorted_indices[i - 1]
            idx2 = sorted_indices[i]
            gap = values_arr[idx2] - values_arr[idx1]
            if gap < min_distance:
                push = (min_distance - gap) / 2.0
                values_arr[idx1] -= push
                values_arr[idx2] += push
                max_adjustment = max(max_adjustment, push)

        if max_adjustment < tolerance:
            break

    return values_arr.tolist()


def _draw_bent_lollipop(ax, x_base: float, x_top: float, y_top: float, colour: str) -> None:
    """
    Draw a lollipop with three segments: vertical-diagonal-vertical structure.

    The lollipop consists of:
    1. Vertical segment from baseline to 0.33*y_top
    2. Diagonal segment to jittered position at 0.66*y_top
    3. Vertical segment from 0.66*y_top to final y_top

    :param ax: matplotlib axis
    :param x_base: true genomic position (1-based display)
    :param x_top: jittered x-position of the top dot
    :param y_top: allele frequency of the point
    :param colour: line and marker color
    """
    y_start = -0.2
    y_segment1_end = -0.1
    y_segment2_end = 0

    ax.plot([x_base, x_base], [y_start, y_segment1_end],
            color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_base, x_top], [y_segment1_end, y_segment2_end],
            color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_top, x_top], [y_segment2_end, y_top],
            color=colour, linewidth=1.0, alpha=0.85, zorder=2)
