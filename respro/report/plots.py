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

from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FeatureRecord,
    ProfilingResult,
    ReferenceGroup,
)
from respro.report.palette import (
    CDS_HIGHLIGHTED_COLOUR,
    FEATURE_BASELINE_COLOUR,
    FEATURE_DEFAULT_COLOUR,
    FEATURE_DEFAULT_EDGE,
    FEATURE_HIGHLIGHTED_EDGE,
    FEATURE_INTRON_COLOUR,
    MATPEPTIDE_HIGHLIGHTED_COLOUR,
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


def _build_multi_reference_lollipop_figure(
    result: ProfilingResult,
    features: list[FeatureRecord],
    rule_feature_names: set[str] | None = None,
):
    """
    Build a lollipop figure with one genome-overview + feature-panel group per reference.

    Each :class:`ReferenceGroup` in ``result.references`` is rendered as a vertically
    stacked group: a per-reference genome overview (titled with reference name + organism)
    followed by that reference's feature tracks and lollipop panels. The single-reference
    path is handled by :func:`_build_lollipop_figure` and is unchanged.

    :param result: profiling result with >1 ReferenceGroup
    :param features: union of feature records across all references
    :param rule_feature_names: optional rule-backed feature names to focus feature panels
    :return: matplotlib Figure, or None when nothing can be plotted
    """
    features_by_ref: dict[int, list[FeatureRecord]] = {}
    for feature in features:
        features_by_ref.setdefault(feature.reference_id, []).append(feature)

    all_cds = result.cds_annotations
    coverage_gaps_by_feature = _group_coverage_gaps_by_feature(result.coverage_gaps)
    database_hit_annotation_ids = {id(ann) for ann in result.database_hit_annotations}

    # Build per-reference row plans. Iterate over DISTINCT reference_id values, not over
    # ReferenceGroups: in the targeted-sequencing case two chroms map to one internal
    # reference and produce two ReferenceGroups sharing one reference_id. Collapsing by
    # reference_id draws exactly one genome overview per internal reference (not one per
    # chrom) and merges both chroms' annotations into that reference's feature panels.
    # Use the first ReferenceGroup for each reference_id as the representative (all groups
    # sharing a reference_id carry the same reference_name/organism/length/features).
    representative_by_ref_id: dict[int, ReferenceGroup] = {}
    for rg in result.references:
        representative_by_ref_id.setdefault(rg.reference_id, rg)

    per_ref_plan: list[tuple[ReferenceGroup, list[dict], list[FeatureRecord]]] = []
    for ref_id, rg in representative_by_ref_id.items():
        ref_features = features_by_ref.get(ref_id, [])
        ref_feature_names = {f.name for f in ref_features}
        # Collect CDS annotations from ALL chroms belonging to this reference_id. In the
        # targeted case annotations span multiple chroms (ReferenceGroups) that share this
        # reference_id; scoping by feature_name (unique per reference) gathers them all.
        ref_cds = [a for a in all_cds if a.feature_name in ref_feature_names]
        ref_coverage = {
            fname: gaps for fname, gaps in coverage_gaps_by_feature.items()
            if fname in ref_feature_names
        }
        if not ref_cds and not ref_coverage:
            continue
        plot_features = _select_plot_features(
            ref_features,
            ref_cds,
            rule_feature_names,
            coverage_feature_names=set(ref_coverage),
        )
        if not plot_features:
            continue
        rows = _plan_reference_rows(ref_cds, ref_coverage, plot_features, ref_features)
        if rows:
            per_ref_plan.append((rg, rows, plot_features))

    if not per_ref_plan:
        logger.warning('No reference groups produced plottable features')
        return None

    subplot_rows: list[dict] = []
    for rg, rows, _ in per_ref_plan:
        for row in reversed(rows):
            subplot_rows.append({**row, 'reference_group': rg})
    height_ratios = [2.0 if row['kind'] == 'lollipop' else 0.5 for row in subplot_rows]
    fig_height = 2 + 1.7 * (len(height_ratios) - 1)
    fig, axes = plt.subplots(
        len(height_ratios), 1,
        figsize=(16, fig_height),
        height_ratios=height_ratios,
    )
    axes_list = list(axes if isinstance(axes, (list, tuple)) else axes.flat)
    row_axes = list(zip(subplot_rows, axes_list))

    # Draw each row, scoped to its reference group.
    # Key lollipop rows by (reference_id, feature_name) — not feature_name alone — so that
    # same-named features on distinct references (e.g. two same-species references both
    # carrying UL23) never cross-link each other's track/lollipop xlim. The reference_group
    # attached to each row carries the reference_id.
    lollipop_rows_by_ref_and_feature: dict[tuple[int, str], list[tuple[dict, object]]] = {}
    for row, ax in row_axes:
        if row['kind'] != 'lollipop':
            continue
        rg = row['reference_group']
        key = (rg.reference_id, row['feature'].name)
        lollipop_rows_by_ref_and_feature.setdefault(key, []).append((row, ax))

    shared_track_ax_by_row_id: dict[int, object] = {}
    for row, ax in row_axes:
        if row['kind'] != 'track':
            continue
        rg = row['reference_group']
        key = (rg.reference_id, row['feature'].name)
        candidates = lollipop_rows_by_ref_and_feature.get(key, [])
        if candidates:
            shared_track_ax_by_row_id[id(row)] = candidates[0][1]

    first_lollipop_ax = None
    for row, ax in row_axes:
        row_kind = row['kind']
        rg = row['reference_group']
        if row_kind == 'genome':
            ref_features = features_by_ref.get(rg.reference_id, [])
            cds_highlighted = row.get('cds_highlighted', set())
            _draw_genome_overview(
                ax, ref_features, cds_highlighted,
                reference_length_nt=rg.reference_length_nt,
            )
            title = rg.reference_name
            if rg.organism:
                title = f'{rg.reference_name} ({rg.organism})'
            ax.set_title(title, fontsize=10, loc='left', pad=6)
            continue

        feature = row['feature']
        if row_kind == 'track':
            mat_peptides = row.get('mat_peptides')
            panel_name = 'Mature Peptide' if feature.feature_type == 'mat_peptide' else 'CDS'
            _draw_feature_track(
                ax, feature,
                mat_peptide_overlays=mat_peptides,
                parent_feature=row.get('parent_feature'),
                rule_feature_names=rule_feature_names,
                panel_name=panel_name,
            )
            shared_lollipop_ax = shared_track_ax_by_row_id.get(id(row))
            if shared_lollipop_ax is not None:
                ax.set_xlim(shared_lollipop_ax.get_xlim())
            continue

        if first_lollipop_ax is None:
            first_lollipop_ax = ax
        _draw_feature_panel(
            ax,
            feature,
            row.get('annotations', []),
            coverage_gaps=row.get('coverage_gaps', []),
            database_hit_annotation_ids=database_hit_annotation_ids,
            shared_track_ax=None,
        )

    # Legend on the first lollipop axis.
    legend_handles = [
        plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
                   markeredgecolor='black', markersize=8, label='Database hit'),
    ]
    effects_for_legend = {ann.consequence for ann in all_cds}
    has_coverage_overlay = any(
        coverage_gaps_by_feature.get(row['feature'].name)
        for row in subplot_rows if row['kind'] == 'lollipop'
    )
    has_introns = any(_feature_intron_gaps(f) for _, _, plot_features in per_ref_plan for f in plot_features)
    if has_coverage_overlay:
        legend_handles.append(mpatches.Patch(facecolor=NON_COVERED_COLOUR, alpha=0.12, edgecolor='none', label='non covered'))
    if has_introns:
        legend_handles.append(mpatches.Patch(facecolor=FEATURE_INTRON_COLOUR, label='Intron (non-coding)'))
    legend_handles.extend(mutation_legend_patches(effects_for_legend))
    if first_lollipop_ax is not None:
        first_lollipop_ax.legend(
            handles=legend_handles, loc='upper right', fontsize=7,
            ncol=len(legend_handles), frameon=False,
            bbox_to_anchor=(1, 1.25), borderaxespad=0.0,
        )
    plt.tight_layout()
    return fig


def _plan_reference_rows(
    cds: list,
    coverage_gaps_by_feature: dict,
    plot_features: list[FeatureRecord],
    all_ref_features: list[FeatureRecord],
) -> list[dict]:
    """Build the row plan (genome + tracks + lollipops) for one reference group."""
    selected_feature_names = {feature.name for feature in plot_features}
    cds = [ann for ann in cds if ann.feature_name in selected_feature_names]
    coverage_gaps_by_feature = {
        fname: gaps for fname, gaps in coverage_gaps_by_feature.items()
        if fname in selected_feature_names
    }
    if not cds and not coverage_gaps_by_feature:
        return []

    feature_annotations = _group_annotations_by_feature(cds, plot_features)
    feature_by_name = {feature.name: feature for feature in all_ref_features}

    mat_peptides_by_parent: dict[str, list[FeatureRecord]] = {}
    for feature in plot_features:
        if feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
            mat_peptides_by_parent.setdefault(feature.parent_feature_name, []).append(feature)

    main_features_by_name: dict[str, FeatureRecord] = {
        feature.name: feature
        for feature in plot_features
        if feature.feature_type != 'mat_peptide'
    }
    for parent_name in mat_peptides_by_parent:
        parent_feature = feature_by_name.get(parent_name)
        if parent_feature is not None:
            main_features_by_name[parent_name] = parent_feature

    main_features = sorted(
        main_features_by_name.values(),
        key=lambda feature: (feature.start, feature.end, feature.name),
    )
    if not main_features:
        return []

    cds_highlighted: set[str] = set()
    for feature_name in feature_annotations:
        feature = feature_by_name.get(feature_name)
        if feature and feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
            cds_highlighted.add(feature.parent_feature_name)
        else:
            cds_highlighted.add(feature_name)

    rows: list[dict] = [{'kind': 'genome', 'cds_highlighted': cds_highlighted}]
    for feature in main_features:
        mat_peptides = sorted(
            mat_peptides_by_parent.get(feature.name, []),
            key=lambda mp: (mp.start, mp.end, mp.name),
        )
        rows.append({'kind': 'track', 'feature': feature, 'mat_peptides': mat_peptides, 'parent_feature': None})
        if mat_peptides:
            for mat_peptide in mat_peptides:
                rows.append({'kind': 'track', 'feature': mat_peptide, 'parent_feature': feature})
                rows.append({
                    'kind': 'lollipop', 'feature': mat_peptide,
                    'annotations': feature_annotations.get(mat_peptide.name, []),
                    'coverage_gaps': coverage_gaps_by_feature.get(mat_peptide.name, []),
                })
        else:
            rows.append({
                'kind': 'lollipop', 'feature': feature,
                'annotations': feature_annotations.get(feature.name, []),
                'coverage_gaps': coverage_gaps_by_feature.get(feature.name, []),
            })
    return rows


def _build_lollipop_figure(
    result: ProfilingResult,
    features: list[FeatureRecord],
    rule_feature_names: set[str] | None = None,
):
    """Build the matplotlib figure used by the HTML and PDF reports."""
    if len(result.references) > 1:
        return _build_multi_reference_lollipop_figure(result, features, rule_feature_names)
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

    mat_peptides_by_parent: dict[str, list[FeatureRecord]] = {}
    for feature in plot_features:
        if feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
            mat_peptides_by_parent.setdefault(feature.parent_feature_name, []).append(feature)

    main_features_by_name: dict[str, FeatureRecord] = {
        feature.name: feature
        for feature in plot_features
        if feature.feature_type != 'mat_peptide'
    }
    for parent_name in mat_peptides_by_parent:
        parent_feature = feature_by_name.get(parent_name)
        if parent_feature is not None:
            main_features_by_name[parent_name] = parent_feature

    main_features = sorted(
        main_features_by_name.values(),
        key=lambda feature: (feature.start, feature.end, feature.name),
    )
    if not main_features:
        logger.warning('No main features available for plotting')
        return None

    # Map mat_peptide variant feature names to parent CDS for genome overview highlighting.
    cds_highlighted: set[str] = set()
    for feature_name in feature_annotations:
        feature = feature_by_name.get(feature_name)
        if feature and feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
            cds_highlighted.add(feature.parent_feature_name)
        else:
            cds_highlighted.add(feature_name)

    rows: list[dict] = [{'kind': 'genome'}]
    for feature in main_features:
        mat_peptides = sorted(
            mat_peptides_by_parent.get(feature.name, []),
            key=lambda mat_peptide: (mat_peptide.start, mat_peptide.end, mat_peptide.name),
        )
        rows.append({'kind': 'track', 'feature': feature, 'mat_peptides': mat_peptides, 'parent_feature': None})
        if mat_peptides:
            for mat_peptide in mat_peptides:
                rows.append({'kind': 'track', 'feature': mat_peptide, 'parent_feature': feature})
                rows.append({'kind': 'lollipop', 'feature': mat_peptide})
        else:
            rows.append({'kind': 'lollipop', 'feature': feature})

    subplot_rows = list(reversed(rows))
    height_ratios = [
        2.0 if row['kind'] == 'lollipop' else 0.5
        for row in subplot_rows
    ]
    fig_height = 2 + 1.7*(len(height_ratios)-1)
    fig, axes = plt.subplots(
        len(height_ratios),
        1,
        figsize=(16, fig_height),
        height_ratios=height_ratios,
    )
    axes_list = list(axes if isinstance(axes, (list, tuple)) else axes.flat)

    row_axes = list(zip(subplot_rows, axes_list))
    overview_ax = next(
        ax
        for row, ax in row_axes
        if row['kind'] == 'genome'
    )

    _draw_genome_overview(
        overview_ax,
        features,
        cds_highlighted,
        reference_length_nt=result.reference_length_nt,
    )

    effects_for_legend = {ann.consequence for ann in cds}
    lollipop_feature_names = {
        row['feature'].name
        for row in rows
        if row['kind'] == 'lollipop'
    }
    has_coverage_overlay = any(
        coverage_gaps_by_feature.get(feature_name)
        for feature_name in lollipop_feature_names
    )
    has_introns = any(_feature_intron_gaps(f) for f in plot_features)
    database_hit_annotation_ids = {id(ann) for ann in result.database_hit_annotations}
    handles = [
        plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
                   markeredgecolor='black', markersize=8, label='Database hit'),
    ]
    if has_coverage_overlay:
        handles.append(mpatches.Patch(facecolor=NON_COVERED_COLOUR, alpha=0.12, edgecolor='none', label='non covered'))
    if has_introns:
        handles.append(mpatches.Patch(facecolor=FEATURE_INTRON_COLOUR, label='Intron (non-coding)'))
    handles.extend(mutation_legend_patches(effects_for_legend))

    lollipop_rows_by_feature_name: dict[str, list[tuple[dict[str, object], object]]] = {}
    for row, ax in row_axes:
        if row['kind'] != 'lollipop':
            continue
        feature = row['feature']
        lollipop_rows_by_feature_name.setdefault(feature.name, []).append((row, ax))

    shared_track_ax_by_row_id: dict[int, object] = {}
    for row, ax in row_axes:
        if row['kind'] != 'track':
            continue
        feature = row['feature']
        row_id = id(row)
        lollipop_candidates = lollipop_rows_by_feature_name.get(feature.name, [])
        if lollipop_candidates:
            shared_track_ax_by_row_id[row_id] = lollipop_candidates[0][1]

    first_lollipop_ax = None
    for row, ax in row_axes:
        row_kind = row['kind']
        if row_kind == 'genome':
            continue

        feature = row['feature']
        if row_kind == 'track':
            mat_peptides = row.get('mat_peptides')
            panel_name = 'Mature Peptide' if feature.feature_type == 'mat_peptide' else 'CDS'
            if mat_peptides:
                _draw_feature_track(
                    ax,
                    feature,
                    mat_peptide_overlays=mat_peptides,
                    rule_feature_names=rule_feature_names,
                    panel_name=panel_name,
                )
            else:
                _draw_feature_track(
                    ax,
                    feature,
                    parent_feature=row.get('parent_feature'),
                    rule_feature_names=rule_feature_names,
                    panel_name=panel_name,
                )

            shared_lollipop_ax = shared_track_ax_by_row_id.get(id(row))
            if shared_lollipop_ax is not None:
                ax.set_xlim(shared_lollipop_ax.get_xlim())
            continue

        if first_lollipop_ax is None:
            first_lollipop_ax = ax
        _draw_feature_panel(
            ax,
            feature,
            feature_annotations.get(feature.name, []),
            coverage_gaps=coverage_gaps_by_feature.get(feature.name, []),
            database_hit_annotation_ids=database_hit_annotation_ids,
            shared_track_ax=None,
        )

    if first_lollipop_ax is not None:
        first_lollipop_ax.legend(
            handles=handles, loc='upper right', fontsize=7,
            ncol=len(handles), frameon=False,
            bbox_to_anchor=(1, 1.25), borderaxespad=0.0,
        )
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

    Uses the plotted segment span from _feature_plot_segments for overlap checks,
    so that features with sparse segments do not wastefully block track space.

    :param features: feature records to place
    :return: mapping of feature name to overview track index
    """
    track_stops: list[int] = []
    tracks: dict[str, int] = {}

    for feature in sorted(features, key=lambda item: (item.start, item.end, item.name)):
        segments = _feature_plot_segments(feature)
        plot_start = segments[0][0]
        plot_end = segments[-1][1]

        track_idx = 0
        while track_idx < len(track_stops) and track_stops[track_idx] > plot_start:
            track_idx += 1

        if track_idx < len(track_stops):
            track_stops[track_idx] = plot_end
        else:
            track_stops.append(plot_end)

        tracks[feature.name] = track_idx

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
    sorted_features = [feature for feature in sorted_features if feature.feature_type == 'CDS']
    if not sorted_features:
        ax.set_axis_off()
        return

    tracks = _assign_feature_tracks(sorted_features)
    genome_start, genome_end = _resolve_overview_bounds(sorted_features, reference_length_nt)
    max_track = max(tracks.values(), default=0)
    # Baseline beneath the tracks
    ax.hlines(0.5, genome_start, genome_end, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=1)

    for feature in sorted_features:
        track = tracks[feature.name]
        y = -track
        is_highlighted = feature.name in highlighted_feature_names
        colour = CDS_HIGHLIGHTED_COLOUR if is_highlighted else FEATURE_DEFAULT_COLOUR
        edge = FEATURE_HIGHLIGHTED_EDGE if is_highlighted else FEATURE_DEFAULT_EDGE
        for segment_start, segment_end in _feature_plot_segments(feature):
            ax.add_patch(mpatches.Rectangle(
                (segment_start + 1, y),
                max(1, segment_end - segment_start),
                0.9,
                facecolor=colour,
                edgecolor=edge,
                linewidth=0.7,
                zorder=100 + y,
            ))

        if is_highlighted:
            label_y = 1
            label_x = feature.start + 1 + ((feature.end - feature.start) / 2)
            ax.text(
                label_x,
                label_y,
                feature.display_name,
                ha='center',
                va='bottom',
                fontsize=7,
                fontweight='bold',
                color=FEATURE_HIGHLIGHTED_EDGE,
                zorder=200 + y,
            )

    ax.set_title('Genome overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_xlim(genome_start, genome_end)
    lower = -(max_track + 0.2)
    ax.set_ylim(lower, 1.2)
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


def _draw_feature_track(
    ax,
    feature: FeatureRecord,
    parent_feature: FeatureRecord | None = None,
    mat_peptide_overlays: list[FeatureRecord] | None = None,
    rule_feature_names: set[str] | None = None,
    panel_name: str = 'CDS',
) -> None:
    """
    Draw a simple feature track visualization above the lollipop plot.

    :param ax: matplotlib axis
    :param feature: feature to render
    :param parent_feature: optional parent feature for mat_peptide precursor strip
    :param mat_peptide_overlays: optional mat_peptide features to mark on the track
    :param rule_feature_names: optional rule-backed names for overlay labels
    """
    feature_width = feature.end - feature.start
    pad = max(10, int((feature.end - feature.start) * 0.03))
    ax.hlines(0.5, feature.start + 1 - pad, feature.end + pad, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=-10)

    # Draw full feature extent then overlay intron regions on top
    ax.add_patch(mpatches.Rectangle(
        (feature.start + 1, 0.3),
        max(1, feature.end - feature.start),
        0.4,
        facecolor=CDS_HIGHLIGHTED_COLOUR if feature.feature_type == 'CDS' else MATPEPTIDE_HIGHLIGHTED_COLOUR,
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
        f'← {feature.display_name} ←' if feature.strand == '-' else f'→ {feature.display_name} →',
        ha='center',
        va='center',
        fontsize=7,
        fontweight='bold',
        color='white'
    )

    ax.set_title(f'{panel_name} overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_xlim(max(1, feature.start + 1 - pad), feature.end + pad)

    if mat_peptide_overlays:
        sorted_mps = sorted(mat_peptide_overlays, key=lambda f: f.start)
        # Draw dotted separator at each mat_peptide start, and at the final mat_peptide end.
        boundaries: set[float] = {mp.start + 1 for mp in sorted_mps}
        boundaries.add(sorted_mps[-1].end + 1)
        for x_boundary in sorted(boundaries):
            ax.vlines(
                x=x_boundary, ymin=0.3, ymax=0.7,
                colors=FEATURE_DEFAULT_COLOUR, linewidth=1.5, zorder=5,
            )
        for mp in sorted_mps:
            label = mp.display_name
            label_x = mp.start + 1 + max(1, mp.end - mp.start) / 2
            ax.text(
                label_x, 0.76, label,
                ha='center', va='bottom',
                fontsize=7, color=MATPEPTIDE_HIGHLIGHTED_COLOUR, fontweight='bold',
                clip_on=True,
            )
        ax.set_ylim(0, 1.4)
    elif feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
        strip_start, strip_end = _resolve_precursor_strip_bounds(ax, parent_feature)
        strip_width = max(1, strip_end - strip_start)
        ax.add_patch(mpatches.Rectangle(
            (strip_start, 0.3),
            strip_width,
            0.4,
            facecolor=CDS_HIGHLIGHTED_COLOUR,
            edgecolor=FEATURE_HIGHLIGHTED_EDGE,
            linewidth=0.6,
            zorder=-4,
        ))
        ax.set_ylim(0, 1)
    else:
        ax.set_ylim(0, 1)

    ax.set_yticks([])
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
    database_hit_annotation_ids: set[int] | None = None,
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

    database_hit_annotation_ids = database_hit_annotation_ids or set()

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
                marker_shapes['database_hit'] if id(ann) in database_hit_annotation_ids
                else marker_shapes['no_database_hit']
            ),
            s=50 if id(ann) in database_hit_annotation_ids else 40,
            zorder=4,
            edgecolors='black' if id(ann) in database_hit_annotation_ids else 'white',
            linewidths=0.5,
        )

        if id(ann) in database_hit_annotation_ids:
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
        f'{feature.display_name} variants',
        fontsize=8,
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
