"""
Publication-ready plots for genome overview and feature-level mutation tracks.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from respro.db.models import AnnotatedVariant, CoverageGap, FeatureRecord, ProfilingResult
from respro.report.palette import (
    FEATURE_BASELINE_COLOUR,
    FEATURE_DEFAULT_COLOUR,
    FEATURE_DEFAULT_EDGE,
    FEATURE_HIGHLIGHTED_COLOUR,
    FEATURE_HIGHLIGHTED_EDGE,
    FEATURE_INTRON_COLOUR,
    MUTATION_COLOURS,
    NON_COVERED_COLOUR,
    mutation_legend_patches,
)

matplotlib.use('Agg')
logger = logging.getLogger(__name__)


def lollipop_plot(
    result: ProfilingResult,
    features: list[FeatureRecord],
    output_path: Path,
    fmt: str = 'svg',
    rule_feature_names: set[str] | None = None,
) -> Path:
    """
    Create a report plot with genome overview and feature-level mutation panels.

    :param result: profiling result with annotated variants
    :param features: feature records for drawing the overview and feature panels
    :param output_path: file path for the saved figure
    :param fmt: output format (svg, png)
    :param rule_feature_names: optional rule-backed feature names to focus feature panels
    :return: path to saved figure
    """
    output_path = Path(output_path)
    fig = _build_lollipop_figure(result, features, rule_feature_names=rule_feature_names)
    if fig is None:
        return output_path

    fig.savefig(output_path, format=fmt, dpi=150)
    plt.close(fig)

    logger.info('Lollipop plot saved to %s', output_path)
    return output_path


def render_lollipop_plot_bytes(
    result: ProfilingResult,
    features: list[FeatureRecord],
    fmt: str = 'svg',
    rule_feature_names: set[str] | None = None,
) -> bytes | None:
    """
    Render the report plot to an in-memory byte string.

    :param result: profiling result with annotated variants
    :param features: feature records for drawing the overview and feature panels
    :param fmt: output format (svg, png)
    :param rule_feature_names: optional rule-backed feature names to focus feature panels
    :return: plot bytes or None when no plot could be generated
    """
    fig = _build_lollipop_figure(result, features, rule_feature_names=rule_feature_names)
    if fig is None:
        return None

    buf = BytesIO()
    fig.savefig(buf, format=fmt, dpi=150)
    plt.close(fig)
    return buf.getvalue()


def _build_lollipop_figure(
    result: ProfilingResult,
    features: list[FeatureRecord],
    rule_feature_names: set[str] | None = None,
):
    """Build the matplotlib figure used by the HTML and PDF reports."""
    cds = result.cds_annotations
    coverage_gaps_by_feature = _group_coverage_gaps_by_feature(result.coverage_gaps)
    if not cds and not coverage_gaps_by_feature:
        logger.warning('No CDS variants to plot')
        return None

    plot_features = _select_plot_features(
        features,
        cds,
        rule_feature_names,
        coverage_feature_names=set(coverage_gaps_by_feature),
    )
    if not plot_features:
        logger.warning('No features selected for plotting')
        return None

    selected_feature_names = {feature.name for feature in plot_features}
    cds = [ann for ann in cds if ann.feature_name in selected_feature_names]
    coverage_gaps_by_feature = {
        feature_name: gaps
        for feature_name, gaps in coverage_gaps_by_feature.items()
        if feature_name in selected_feature_names
    }
    if not cds and not coverage_gaps_by_feature:
        logger.warning('No CDS variants in selected plot features')
        return None

    feature_annotations = _group_annotations_by_feature(cds, plot_features)
    feature_by_name = {feature.name: feature for feature in features}

    # One row for overview, then two rows per feature (track + lollipop)
    height_ratios = [2.0, 0.5] * len(plot_features) + [0.5]
    # Cap height so the overview typically fits a full-size 1080p browser window.
    fig_height = 2 + 3.4 * len(plot_features)
    fig, axes = plt.subplots(
        len(height_ratios),
        1,
        figsize=(16, fig_height),
        height_ratios=height_ratios
    )
    axes_list = list(axes if isinstance(axes, (list, tuple)) else axes.flat)
    overview_ax = axes_list[-1]
    feature_pair_axes = axes_list[0:-1]

    highlighted_feature_names = set(feature_annotations)
    _draw_genome_overview(
        overview_ax,
        features,
        highlighted_feature_names,
        reference_length_nt=result.reference_length_nt,
    )
    # Legend handles
    effects_for_legend = {ann.consequence for ann in cds}
    has_coverage_overlay = any(coverage_gaps_by_feature.get(feature.name) for feature in plot_features)
    has_introns = any(_feature_intron_gaps(feature) for feature in plot_features)
    handles = [
        plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
                   markeredgecolor='black', markersize=8, label='Database hit'),
    ]
    if has_coverage_overlay:
        handles.append(mpatches.Patch(facecolor=NON_COVERED_COLOUR, alpha=0.12, edgecolor='none', label='non covered'))
    if has_introns:
        handles.append(mpatches.Patch(facecolor=FEATURE_INTRON_COLOUR, label='Intron (non-coding)'))
    handles.extend(mutation_legend_patches(effects_for_legend))

    for i, feature in enumerate(plot_features):
        lollipop_ax = feature_pair_axes[2 * i]
        track_ax = feature_pair_axes[2 * i + 1]
        annotations = feature_annotations.get(feature.name, [])
        _draw_feature_panel(
            lollipop_ax,
            feature,
            annotations,
            coverage_gaps=coverage_gaps_by_feature.get(feature.name, []),
            shared_track_ax=track_ax,
        )
        parent_feature = None
        if feature.parent_feature_name:
            parent_feature = feature_by_name.get(feature.parent_feature_name)
        _draw_feature_track(track_ax, feature, parent_feature=parent_feature)

    feature_pair_axes[0].legend(handles=handles, loc='upper right', fontsize=7, ncol=len(handles), frameon=False, bbox_to_anchor=(1, 1.25), borderaxespad=0.0)
    plt.tight_layout()
    return fig


def _select_plot_features(
    features: list[FeatureRecord],
    annotations: list,
    rule_feature_names: set[str] | None,
    coverage_feature_names: set[str] | None = None,
) -> list[FeatureRecord]:
    """
    Keep features that have detected variants and resistance rules.

    :param features: all feature records on the selected reference
    :param annotations: CDS annotations from profiling result
    :param rule_feature_names: features with loaded resistance rules
    :return: ordered list of features to render in the plot
    """
    variant_feature_names = {ann.feature_name for ann in annotations if ann.feature_name}
    covered_gap_feature_names = coverage_feature_names or set()
    if rule_feature_names is None:
        selected_names = variant_feature_names | covered_gap_feature_names
    else:
        selected_names = (variant_feature_names | covered_gap_feature_names) & set(rule_feature_names)
        if not selected_names:
            selected_names = variant_feature_names | covered_gap_feature_names

    return sorted(
        [feature for feature in features if feature.name in selected_names],
        key=lambda g: (g.start, g.end, g.name),
    )


def _group_annotations_by_feature(
    annotations: list[AnnotatedVariant],
    features: list[FeatureRecord],
) -> dict[str, list[AnnotatedVariant]]:
    """
    Return annotations grouped by feature name in genomic order.

    :param annotations: CDS annotations
    :param features: feature records that should be represented in the figure
    :return: mapping of feature name to sorted annotations
    """
    feature_names = {feature.name for feature in features}
    grouped: dict[str, list[AnnotatedVariant]] = {feature.name: [] for feature in features}
    for ann in annotations:
        if ann.feature_name in feature_names:
            grouped[ann.feature_name].append(ann)

    return {
        feature.name: sorted(
            grouped[feature.name],
            key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
        )
        for feature in features
        if grouped[feature.name]
    }


def _assign_feature_tracks(features: list[FeatureRecord]) -> dict[str, int]:
    """
    Assign features to non-overlapping overview tracks.

    :param features: feature records to place
    :return: mapping of feature name to overview track index
    """
    last_end_by_track: list[int] = []
    tracks: dict[str, int] = {}

    for feature in sorted(features, key=lambda item: (item.start, item.end, item.name)):
        assigned_track = None
        for track_idx, last_end in enumerate(last_end_by_track):
            if feature.start >= last_end:
                assigned_track = track_idx
                last_end_by_track[track_idx] = feature.end
                break
        if assigned_track is None:
            assigned_track = len(last_end_by_track)
            last_end_by_track.append(feature.end)
        tracks[feature.name] = assigned_track

    return tracks


def _feature_plot_segments(feature: FeatureRecord) -> list[tuple[int, int]]:
    """Return genomic CDS segments ordered left-to-right for plotting."""
    if not feature.segments:
        return [(feature.start, feature.end)]
    return sorted(
        [(segment.start, segment.end) for segment in feature.segments],
        key=lambda item: (item[0], item[1]),
    )


def _feature_intron_gaps(feature: FeatureRecord) -> list[tuple[int, int]]:
    """Return non-coding intron gap intervals for a split feature, ordered by position."""
    if not feature.segments:
        return []
    sorted_segs = sorted(feature.segments, key=lambda s: s.start)
    gaps = []
    for i in range(len(sorted_segs) - 1):
        gap_start = sorted_segs[i].end
        gap_end = sorted_segs[i + 1].start
        if gap_end > gap_start:
            gaps.append((gap_start, gap_end))
    return gaps


def _draw_genome_overview(
    ax,
    features: list[FeatureRecord],
    highlighted_feature_names: set[str],
    reference_length_nt: int | None = None,
) -> None:
    """
    Draw a whole-genome feature overview and highlight affected resistance features.

    :param ax: matplotlib axis
    :param features: all feature records on the resolved reference
    :param highlighted_feature_names: features with plotted mutations
    """
    sorted_features = sorted(features, key=lambda feature: (feature.start, feature.end, feature.name))
    if not sorted_features:
        ax.set_axis_off()
        return

    tracks = _assign_feature_tracks(sorted_features)
    genome_start, genome_end = _resolve_overview_bounds(sorted_features, reference_length_nt)
    max_track = max(tracks.values(), default=0)
    track_height = 0.44
    track_step = track_height * 0.2
    # Baseline beneath the tracks
    ax.hlines(0.0, genome_start, genome_end, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=1)

    for feature in sorted_features:
        track = tracks[feature.name]
        y = -(track * track_step)
        is_highlighted = feature.name in highlighted_feature_names
        colour = FEATURE_HIGHLIGHTED_COLOUR if is_highlighted else FEATURE_DEFAULT_COLOUR
        edge = FEATURE_HIGHLIGHTED_EDGE if is_highlighted else FEATURE_DEFAULT_EDGE
        for segment_start, segment_end in _feature_plot_segments(feature):
            ax.add_patch(mpatches.Rectangle(
                (segment_start + 1, y - (track_height / 2.0)),
                max(1, segment_end - segment_start),
                track_height,
                facecolor=colour,
                edgecolor=edge,
                linewidth=0.7,
                zorder=100 + y,
            ))

        if is_highlighted:
            label_y = y + (track_height / 2.0) + 0.11
            label_x = feature.start + 1 + ((feature.end - feature.start) / 2)
            ax.text(
                label_x,
                label_y,
                feature.name,
                ha='center',
                va='bottom',
                fontsize=8,
                fontweight='bold',
                color=FEATURE_HIGHLIGHTED_EDGE,
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
    features: list[FeatureRecord],
    reference_length_nt: int | None,
) -> tuple[int, int]:
    """
    Return overview x-limits based on reference length, with safe fallback.

    :param features: feature records on the resolved reference
    :param reference_length_nt: full reference length in nucleotides
    :return: (start_1based, end_1based)
    """
    if reference_length_nt is not None and reference_length_nt > 0:
        return 1, reference_length_nt

    if not features:
        return 1, 1

    logger.warning(
        'Reference length missing in result object; falling back to CDS-derived overview bounds.'
    )
    return 1, max(feature.end for feature in features)


def _draw_feature_track(ax, feature: FeatureRecord, parent_feature: FeatureRecord | None = None) -> None:
    """
    Draw a simple feature track visualization above the lollipop plot.

    :param ax: matplotlib axis
    :param feature: feature to render
    """
    # Draw feature arrow/rectangle
    feature_width = feature.end - feature.start
    pad = max(10, int((feature.end - feature.start) * 0.03))
    ax.hlines(0.5, feature.start + 1 - pad, feature.end + pad, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=-10)

    # Draw full feature extent then overlay intron regions on top
    ax.add_patch(mpatches.Rectangle(
        (feature.start + 1, 0.3),
        max(1, feature.end - feature.start),
        0.4,
        facecolor=FEATURE_HIGHLIGHTED_COLOUR,
        edgecolor=FEATURE_HIGHLIGHTED_EDGE,
        alpha=1,
        linewidth=1.0,
        zorder=2,
    ))
    for gap_start, gap_end in _feature_intron_gaps(feature):
        ax.add_patch(mpatches.Rectangle(
            (gap_start + 1, 0.3),
            max(1, gap_end - gap_start),
            0.4,
            facecolor=FEATURE_INTRON_COLOUR,
            edgecolor=FEATURE_INTRON_COLOUR,
            linewidth=0,
            zorder=3,
        ))

    # Add feature label in the middle
    feature_x = feature.start + 1 + (feature_width / 2)
    ax.text(
        feature_x,
        0.5,
        f'← {feature.name} ←' if feature.strand == '-' else f'→ {feature.name} →',
        ha='center',
        va='center',
        fontsize=8,
        fontweight='bold',
        color='white',
    )

    ax.set_title('Gene overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlim(max(1, feature.start + 1 - pad), feature.end + pad)

    if feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
        strip_start, strip_end = _resolve_precursor_strip_bounds(ax, parent_feature)
        strip_width = max(1, strip_end - strip_start)
        ax.add_patch(mpatches.Rectangle(
            (strip_start, 0.84),
            strip_width,
            0.09,
            facecolor=FEATURE_DEFAULT_COLOUR,
            edgecolor=FEATURE_DEFAULT_EDGE,
            alpha=0.55,
            linewidth=0.6,
            zorder=4,
        ))
        label_x = strip_start + (strip_width / 2.0)
        ax.text(
            label_x,
            0.945,
            f'precursor: {feature.parent_feature_name}',
            ha='center',
            va='bottom',
            fontsize=7,
            color=FEATURE_DEFAULT_EDGE,
        )

    ax.spines['top'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlabel('')
    ax.set_ylabel('')


def _resolve_precursor_strip_bounds(
    ax,
    parent_feature: FeatureRecord | None,
) -> tuple[float, float]:
    """Return precursor strip x-bounds from parent interval or current visible limits."""
    x_left, x_right = ax.get_xlim()
    if parent_feature is None:
        return x_left, x_right

    parent_left = parent_feature.start + 1
    parent_right = parent_feature.end
    visible_left = max(x_left, parent_left)
    visible_right = min(x_right, parent_right)
    if visible_right <= visible_left:
        return x_left, x_right
    return visible_left, visible_right


def _draw_feature_panel(
    ax,
    feature: FeatureRecord,
    annotations: list[AnnotatedVariant],
    coverage_gaps: list[CoverageGap] | None = None,
    shared_track_ax=None,
) -> None:
    """
    Draw one feature-focused bent lollipop panel.

    :param ax: matplotlib axis
    :param feature: feature to render
    :param annotations: annotations belonging to the feature
    :param shared_track_ax: optional track axis to share x-limits with
    """

    marker_shapes = {
        'database_hit': 's',
        'no_database_hit': 'o',
    }

    _draw_non_covered_regions(ax, feature, coverage_gaps or [])

    jittered = _apply_top_jitter(annotations, feature_length=feature.end - feature.start)
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

    pad = max(10, int((feature.end - feature.start) * 0.03))
    ax.hlines(0.0, feature.start + 1 - pad, feature.end + pad, color='black', linewidth=1.0, zorder=1, linestyle='--')
    ax.set_xlim(max(1, feature.start + 1 - pad), feature.end + pad)
    ax.set_ylim(-0.19, 1.05)
    ax.set_ylabel('variant frequency')
    ax.set_title(
        f'{feature.name} variants',
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
    feature_length: int,
) -> list[tuple[AnnotatedVariant, float]]:
    """
    Add deterministic horizontal jitter enforcing minimum separation.

    The stem stays anchored at the true genomic position. Only the top dot is
    shifted slightly to separate overlapping points.

    The minimum separation is 1/100 of the feature length so jitter scales
    consistently across features of different sizes.

    :param annotations: annotations within one feature panel
    :param feature_length: length of the feature in nucleotides (end - start)
    :return: list of (annotation, jittered_x)
    """
    sorted_anns = sorted(
        annotations,
        key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
    )
    x_values = [ann.variant.pos + 1 for ann in sorted_anns]
    min_distance = feature_length / 100
    x_values_jittered = adjust_array_min_distance(x_values, min_distance=min_distance)
    return list(zip(sorted_anns, x_values_jittered))


def _group_coverage_gaps_by_feature(coverage_gaps: list[CoverageGap]) -> dict[str, list[CoverageGap]]:
    """Group coverage gaps by feature name ordered by codon_start."""
    grouped: dict[str, list[CoverageGap]] = {}
    for gap in coverage_gaps:
        grouped.setdefault(gap.feature_name, []).append(gap)
    for feature_name in grouped:
        grouped[feature_name].sort(key=lambda gap: gap.codon_start)
    return grouped


def _draw_non_covered_regions(ax, feature: FeatureRecord, coverage_gaps: list[CoverageGap]) -> None:
    """Draw pre-merged non-covered codon stretches as a low-alpha background overlay."""
    for gap in coverage_gaps:
        left_start, right_end = _coverage_gap_nt_bounds(feature, gap)
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


def _codon_nt_span(feature: FeatureRecord, codon_pos: int) -> tuple[int, int]:
    """Return 0-based genomic nt interval [start, end) for one codon index."""
    cds_nt_start = feature.codon_start + codon_pos * 3
    if feature.strand == '+':
        genomic_start = feature.start + cds_nt_start
        return genomic_start, genomic_start + 3

    genomic_high = (feature.end - 1) - cds_nt_start
    genomic_start = genomic_high - 2
    return genomic_start, genomic_high + 1


def _coverage_gap_nt_bounds(feature: FeatureRecord, gap: CoverageGap) -> tuple[int, int]:
    """Return genomic 0-based half-open bounds for one codon gap across both strands."""
    start_a, end_a = _codon_nt_span(feature, gap.codon_start)
    start_b, end_b = _codon_nt_span(feature, gap.codon_end)
    return min(start_a, start_b), max(end_a, end_b)


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
