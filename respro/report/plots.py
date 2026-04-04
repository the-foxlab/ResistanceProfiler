"""
Publication-ready plots for genome overview and gene-level mutation tracks.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from respro.db.models import AnnotatedVariant, GeneRecord
from respro.report.palette import MUTATION_COLOURS
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)

_MARKER_SHAPES = {
    'database_hit': 's',
    'no_database_hit': 'o',
}

_COLOURS = MUTATION_COLOURS


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
    :param fmt: output format (svg, pdf, png)
    :param rule_gene_names: optional rule-backed gene names to focus gene panels
    :return: path to saved figure
    """
    output_path = Path(output_path)
    cds = result.cds_annotations
    if not cds:
        logger.warning('No CDS variants to plot')
        return output_path

    plot_genes = _select_plot_genes(genes, cds, rule_gene_names)
    if not plot_genes:
        logger.warning('No genes selected for plotting')
        return output_path

    selected_gene_names = {gene.name for gene in plot_genes}
    cds = [ann for ann in cds if ann.gene_name in selected_gene_names]
    if not cds:
        logger.warning('No CDS variants in selected plot genes')
        return output_path

    gene_annotations = _group_annotations_by_gene(cds, plot_genes)

    # One row for overview, then two rows per gene (track + lollipop)
    height_ratios = [2.0, 0.5] * len(plot_genes) + [0.5]
    # Cap height so the overview typically fits a full-size 1080p browser window.
    fig_height = min(9.0, max(5.4, 2 + 2.1 * len(plot_genes)))
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

    handles = [
        plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
                   markeredgecolor='black', markersize=8, label='Database hit'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['missense'],
                   markeredgecolor='white', markersize=8, label='Missense'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['synonymous'],
                   markeredgecolor='white', markersize=8, label='Synonymous'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['stop_gained'],
                   markeredgecolor='white', markersize=8, label='Stop gained/lost'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['frameshift'],
                   markeredgecolor='white', markersize=8, label='Frameshift'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['insertion'],
                   markeredgecolor='white', markersize=8, label='Insertion'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=_COLOURS['deletion'],
                   markeredgecolor='white', markersize=8, label='Deletion'),
    ]


    for i, gene in enumerate(plot_genes):
        lollipop_ax = gene_pair_axes[2 * i]
        track_ax = gene_pair_axes[2 * i + 1]
        annotations = gene_annotations.get(gene.name, [])
        _draw_gene_panel(lollipop_ax, gene, annotations, shared_track_ax=track_ax)
        _draw_gene_track(track_ax, gene)

    gene_pair_axes[0].legend(handles=handles, loc='upper right', fontsize=7, ncol=len(handles), frameon=False, bbox_to_anchor=(1, 1.15), borderaxespad=0.0)
    plt.tight_layout()
    fig.savefig(output_path, format=fmt, dpi=150)
    plt.close(fig)

    logger.info('Lollipop plot saved to %s', output_path)
    return output_path


def _select_plot_genes(
    genes: list[GeneRecord],
    annotations: list,
    rule_gene_names: set[str] | None,
) -> list[GeneRecord]:
    """
    Keep genes that have detected variants and resistance rules.

    :param genes: all genes on the selected reference
    :param annotations: CDS annotations from profiling result
    :param rule_gene_names: genes with loaded resistance rules
    :return: ordered list of genes to render in the plot
    """
    variant_gene_names = {ann.gene_name for ann in annotations if ann.gene_name}
    if rule_gene_names is None:
        selected_names = variant_gene_names
    else:
        selected_names = variant_gene_names & set(rule_gene_names)
        if not selected_names:
            selected_names = variant_gene_names

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
    genome_mid = genome_start + ((genome_end - genome_start) / 2)
    max_track = max(tracks.values(), default=0)
    track_height = 0.44
    track_step = track_height * 0.2

    ax.hlines(0.0, genome_start, genome_end, color='dimgrey', linewidth=1.0, zorder=1)

    for gene in sorted_genes:
        track = tracks[gene.name]
        y = -(track * track_step)
        left = gene.start + 1
        width = max(1, gene.end - gene.start)
        is_highlighted = gene.name in highlighted_gene_names
        colour = 'steelblue' if is_highlighted else '#d9dde3'
        edge = 'slategrey' if is_highlighted else '#8d99a6'
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
                color='slategrey',
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
    ax.hlines(0.5, gene.start + 1 - pad, gene.end + pad, color='dimgrey', linewidth=1.0, zorder=-10)

    rect = mpatches.Rectangle(
        (gene.start + 1, 0.3),
        gene_width,
        0.4,
        facecolor='steelblue',
        edgecolor='slategrey',
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
        gene.name,
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


def _draw_gene_panel(ax, gene: GeneRecord, annotations: list[AnnotatedVariant], shared_track_ax=None) -> None:
    """
    Draw one gene-focused bent lollipop panel.

    :param ax: matplotlib axis
    :param gene: gene to render
    :param annotations: annotations belonging to the gene
    :param shared_track_ax: optional track axis to share x-limits with
    """
    jittered = _apply_top_jitter(annotations)

    for ann, x_top in jittered:
        x_base = ann.variant.pos + 1
        y_top = ann.variant.allele_freq
        colour = _COLOURS.get(ann.consequence, _COLOURS['unknown'])
        _draw_bent_lollipop(ax, x_base, x_top, y_top, colour)
        ax.scatter(
            x_top,
            y_top,
            color=colour,
            marker=_MARKER_SHAPES['database_hit'] if ann.is_resistance_hit else _MARKER_SHAPES['no_database_hit'],
            s=50 if ann.is_resistance_hit else 40,
            zorder=4,
            edgecolors='black' if ann.is_resistance_hit else  'white',
            linewidths=0.5,
        )

        if ann.is_resistance_hit:
            label = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            ax.annotate(
                label,
                (x_top, y_top),
                textcoords='offset points',
                xytext=(4, 7),
                fontsize=7,
                color=colour,
                fontweight='bold',
            )

    pad = max(10, int((gene.end - gene.start) * 0.03))
    hit_count = sum(1 for ann in annotations if ann.is_resistance_hit)
    ax.hlines(0.0, gene.start + 1 - pad, gene.end + pad, color='black', linewidth=1.0, zorder=1, linestyle='--')
    ax.set_xlim(max(1, gene.start + 1 - pad), gene.end + pad)
    ax.set_ylim(-0.19, 1.05)
    ax.set_ylabel('Variant frequency')
    ax.set_title(
        f'{gene.name} variants',
        fontsize=9,
        loc='left',
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


def _apply_top_jitter(annotations: list[AnnotatedVariant]) -> list[tuple[AnnotatedVariant, float]]:
    """
    Add deterministic horizontal jitter enforcing minimum separation.

    The stem stays anchored at the true genomic position. Only the top dot is
    shifted slightly to separate overlapping points.

    :param annotations: annotations within one gene panel
    :return: list of (annotation, jittered_x)
    """
    sorted_anns = sorted(
        annotations,
        key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
    )
    x_values = [ann.variant.pos + 1 for ann in sorted_anns]
    x_values_jittered = adjust_array_min_distance(x_values, min_distance=25)
    return list(zip(sorted_anns, x_values_jittered))


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

    sorted_indices = np.argsort(values_arr)
    original_values = values_arr.copy()

    for _ in range(max_iterations):
        max_adjustment = 0.0
        for i in range(1, n):
            idx1 = sorted_indices[i - 1]
            idx2 = sorted_indices[i]
            current_distance = values_arr[idx2] - values_arr[idx1]
            if current_distance < min_distance:
                adjustment = (min_distance - current_distance) / 2.0

                values_arr[idx1] -= adjustment
                values_arr[idx2] += adjustment

                values_arr[idx1] = max(values_arr[idx1], original_values[idx1] - adjustment)
                values_arr[idx2] = min(values_arr[idx2], original_values[idx2] + adjustment)

                if adjustment > max_adjustment:
                    max_adjustment = adjustment

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

    ax.plot([x_base, x_base], [y_start, y_segment1_end], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_base, x_top], [y_segment1_end, y_segment2_end], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_top, x_top], [y_segment2_end, y_top], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
