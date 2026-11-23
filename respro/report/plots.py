"""Publication-ready plots for genome overview and feature-level mutation tracks."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from io import BytesIO

import matplotlib
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

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


def render_lollipop_plot_bytes(
    result: ProfilingResult,
    features: list[FeatureRecord],
    fmt: str = 'svg',
    rule_feature_names: set[str] | None = None,
) -> bytes | None:
    """Render the genome overview and feature lollipop figure to in-memory bytes."""
    fig = _build_lollipop_figure(result, features, rule_feature_names=rule_feature_names)
    if fig is None:
        return None
    buf = BytesIO()
    metadata = {'Date': None} if fmt == 'svg' else None
    with matplotlib.rc_context({'svg.hashsalt': 'respro'}):
        fig.savefig(buf, format=fmt, dpi=150, metadata=metadata)
    return buf.getvalue()


def _plan_all_references(
    result: ProfilingResult,
    features: list[FeatureRecord],
    rule_feature_names: set[str] | None,
) -> tuple[list[tuple[ReferenceGroup | None, list[dict], list[FeatureRecord], list[FeatureRecord]]], list[AnnotatedVariant]]:
    """Build per-reference row plans for both single- and multi-reference runs."""
    all_cds = result.cds_annotations
    coverage_gaps_by_feature = _group_coverage_gaps_by_feature(result.coverage_gaps)

    if len(result.references) <= 1:
        if not all_cds and not coverage_gaps_by_feature:
            logger.warning('No CDS variants to plot')
            return [], []
        plot_features = _select_plot_features(
            features, all_cds, rule_feature_names, coverage_feature_names=set(coverage_gaps_by_feature),
        )
        if not plot_features:
            logger.warning('No features selected for plotting')
            return [], []
        rows = _plan_reference_rows(all_cds, coverage_gaps_by_feature, plot_features, features)
        if not rows:
            logger.warning('No CDS variants in selected plot features')
            return [], []
        selected_names = {f.name for f in plot_features}
        plotted_cds = [a for a in all_cds if a.feature_name in selected_names]
        return [(None, rows, plot_features, features)], plotted_cds

    features_by_ref: dict[int, list[FeatureRecord]] = {}
    for feature in features:
        features_by_ref.setdefault(feature.reference_id, []).append(feature)

    # Collapse ReferenceGroups by reference_id so targeted multi-chrom runs on one
    # reference render one overview, while chrom scoping keeps same-named features distinct.
    representative_by_ref: dict[int, ReferenceGroup] = {}
    chroms_by_ref: dict[int, set[str]] = {}
    for rg in result.references:
        representative_by_ref.setdefault(rg.reference_id, rg)
        chroms_by_ref.setdefault(rg.reference_id, set()).add(rg.query_name)

    plans: list[tuple[ReferenceGroup | None, list[dict], list[FeatureRecord], list[FeatureRecord]]] = []
    multi_plotted_cds: list[AnnotatedVariant] = []
    for ref_id, rg in representative_by_ref.items():
        ref_features = features_by_ref.get(ref_id, [])
        ref_feature_names = {f.name for f in ref_features}
        ref_chroms = chroms_by_ref.get(ref_id, set())
        ref_cds = [a for a in all_cds if a.variant.chrom in ref_chroms]
        # Filter gaps per reference rather than all(...) so shared feature names keep their own gaps.
        ref_coverage = {
            fname: matched_gaps
            for fname, gaps in coverage_gaps_by_feature.items()
            if fname in ref_feature_names
            and (matched_gaps := [gap for gap in gaps if gap.chrom in ref_chroms])
        }
        if not ref_cds and not ref_coverage:
            continue
        plot_features = _select_plot_features(
            ref_features, ref_cds, rule_feature_names, coverage_feature_names=set(ref_coverage),
        )
        if not plot_features:
            continue
        rows = _plan_reference_rows(ref_cds, ref_coverage, plot_features, ref_features)
        if rows:
            selected_names = {f.name for f in plot_features}
            plans.append((rg, rows, plot_features, ref_features))
            multi_plotted_cds.extend(a for a in ref_cds if a.feature_name in selected_names)

    if not plans:
        logger.warning('No reference groups produced plottable features')
    return plans, multi_plotted_cds


def _build_lollipop_figure(
    result: ProfilingResult,
    features: list[FeatureRecord],
    rule_feature_names: set[str] | None = None,
):
    """Build the matplotlib figure used by the HTML and PDF reports."""
    plans, plotted_cds = _plan_all_references(result, features, rule_feature_names)
    if not plans:
        return None

    subplot_rows: list[dict] = []
    for rg, rows, _plot_features, ref_features in plans:
        for row in reversed(rows):
            subplot_rows.append({**row, 'reference_group': rg, 'ref_features': ref_features})

    height_ratios = [2.0 if row['kind'] == 'lollipop' else 0.5 for row in subplot_rows]
    fig = Figure(figsize=(16, 2 + 1.7 * (len(height_ratios) - 1)))
    FigureCanvasAgg(fig)
    axes = fig.subplots(len(height_ratios), 1, height_ratios=height_ratios)
    axes_list = list(axes if isinstance(axes, (list, tuple)) else axes.flat)
    row_axes = list(zip(subplot_rows, axes_list))

    # Key by (reference_id, feature_name) so same-named features across references never share xlim.
    lollipop_ax_by_key: dict[tuple[int, str], matplotlib.axes.Axes] = {}
    for row, ax in row_axes:
        if row['kind'] == 'lollipop':
            ref_id = row['reference_group'].reference_id if row['reference_group'] is not None else 0
            lollipop_ax_by_key.setdefault((ref_id, row['feature'].name), ax)

    database_hit_annotation_ids = {id(ann) for ann in result.database_hit_annotations}
    first_lollipop_ax = None

    for row, ax in row_axes:
        row_kind = row['kind']
        row_rg: ReferenceGroup | None = row['reference_group']
        if row_kind == 'genome':
            ref_length = row_rg.reference_length_nt if row_rg is not None else result.reference_length_nt
            _draw_genome_overview(
                ax, row['ref_features'], row.get('cds_highlighted', set()),
                reference_length_nt=ref_length,
            )
            if row_rg is not None:
                title = f'{row_rg.reference_name} ({row_rg.organism})' if row_rg.organism else row_rg.reference_name
                ax.set_title(title, fontsize=10, loc='left', pad=6)
            continue

        feature = row['feature']
        if row_kind == 'track':
            panel_name = 'Mature Peptide' if feature.feature_type == 'mat_peptide' else 'CDS'
            _draw_feature_track(
                ax,
                feature,
                parent_feature=row.get('parent_feature'),
                mat_peptide_overlays=row.get('mat_peptides') or None,
                rule_feature_names=rule_feature_names,
                panel_name=panel_name,
            )
            ref_id = row_rg.reference_id if row_rg is not None else 0
            shared_lollipop_ax = lollipop_ax_by_key.get((ref_id, feature.name))
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
        )

    legend_handles: list[object] = [
        Line2D([0], [0], marker='s', color='w', markerfacecolor='white',
               markeredgecolor='black', markersize=8, label='Database hit'),
    ]
    has_coverage_overlay = any(
        row.get('coverage_gaps') for row in subplot_rows if row['kind'] == 'lollipop'
    )
    has_introns = any(_feature_intron_gaps(f) for _, _, plot_features, _ in plans for f in plot_features)
    if has_coverage_overlay:
        legend_handles.append(
            mpatches.Patch(facecolor=NON_COVERED_COLOUR, alpha=0.12, edgecolor='none', label='non covered')
        )
    if has_introns:
        legend_handles.append(mpatches.Patch(facecolor=FEATURE_INTRON_COLOUR, label='Intron (non-coding)'))
    legend_handles.extend(mutation_legend_patches({ann.consequence for ann in plotted_cds}))

    if first_lollipop_ax is not None:
        first_lollipop_ax.legend(
            handles=legend_handles, loc='upper right', fontsize=7,
            ncol=len(legend_handles), frameon=False,
            bbox_to_anchor=(1, 1.25), borderaxespad=0.0,
        )
    fig.tight_layout()
    return fig


def _plan_reference_rows(
    cds: list[AnnotatedVariant],
    coverage_gaps_by_feature: dict[str, list[CoverageGap]],
    plot_features: list[FeatureRecord],
    all_ref_features: list[FeatureRecord],
) -> list[dict]:
    """Build the ordered row plan (genome overview, tracks, lollipops) for one reference."""
    selected_names = {feature.name for feature in plot_features}
    cds = [ann for ann in cds if ann.feature_name in selected_names]
    coverage_gaps_by_feature = {
        fname: gaps for fname, gaps in coverage_gaps_by_feature.items() if fname in selected_names
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
        feature.name: feature for feature in plot_features if feature.feature_type != 'mat_peptide'
    }
    for parent_name in mat_peptides_by_parent:
        if (parent := feature_by_name.get(parent_name)) is not None:
            main_features_by_name[parent_name] = parent

    main_features = sorted(main_features_by_name.values(), key=lambda f: (f.start, f.end, f.name))
    if not main_features:
        return []

    cds_highlighted: set[str] = set()
    for feature_name in feature_annotations:
        feat = feature_by_name.get(feature_name)
        if feat and feat.feature_type == 'mat_peptide' and feat.parent_feature_name:
            cds_highlighted.add(feat.parent_feature_name)
        else:
            cds_highlighted.add(feature_name)

    rows: list[dict] = [{'kind': 'genome', 'cds_highlighted': cds_highlighted}]
    for feature in main_features:
        mat_peptides = sorted(
            mat_peptides_by_parent.get(feature.name, []),
            key=lambda mp: (mp.start, mp.end, mp.name),
        )
        rows.append({'kind': 'track', 'feature': feature, 'mat_peptides': mat_peptides, 'parent_feature': None})
        targets = [(mp, feature) for mp in mat_peptides] if mat_peptides else [(feature, None)]
        for target, parent in targets:
            if parent is not None:
                rows.append({'kind': 'track', 'feature': target, 'parent_feature': parent})
            rows.append({
                'kind': 'lollipop',
                'feature': target,
                'annotations': feature_annotations.get(target.name, []),
                'coverage_gaps': coverage_gaps_by_feature.get(target.name, []),
            })
    return rows


def _select_plot_features(
    features: list[FeatureRecord],
    annotations: list[AnnotatedVariant],
    rule_feature_names: set[str] | None,
    coverage_feature_names: set[str] | None = None,
) -> list[FeatureRecord]:
    """Select features with variants or coverage gaps, preferring rule-backed features."""
    observed = {ann.feature_name for ann in annotations if ann.feature_name} | (coverage_feature_names or set())
    if rule_feature_names is None:
        selected = observed
    else:
        selected = (observed & set(rule_feature_names)) or observed
    return sorted(
        (feature for feature in features if feature.name in selected),
        key=lambda g: (g.start, g.end, g.name),
    )


def _group_annotations_by_feature(
    annotations: list[AnnotatedVariant],
    features: list[FeatureRecord],
) -> dict[str, list[AnnotatedVariant]]:
    """Group annotations by feature name in deterministic genomic order."""
    grouped: dict[str, list[AnnotatedVariant]] = {feature.name: [] for feature in features}
    for ann in annotations:
        if ann.feature_name in grouped:
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
    """Assign features to non-overlapping horizontal overview tracks."""
    track_stops: list[int] = []
    tracks: dict[str, int] = {}
    for feature in sorted(features, key=lambda item: (item.start, item.end, item.name)):
        segments = _feature_plot_segments(feature)
        plot_start, plot_end = segments[0][0], segments[-1][1]
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
    """Return genomic CDS segments ordered left-to-right."""
    if not feature.segments:
        return [(feature.start, feature.end)]
    return sorted((s.start, s.end) for s in feature.segments)


def _feature_intron_gaps(feature: FeatureRecord) -> list[tuple[int, int]]:
    """Return non-coding intron intervals for a split feature in genomic order."""
    if not feature.segments:
        return []
    sorted_segs = sorted(feature.segments, key=lambda s: s.start)
    return [
        (sorted_segs[i].end, sorted_segs[i + 1].start)
        for i in range(len(sorted_segs) - 1)
        if sorted_segs[i + 1].start > sorted_segs[i].end
    ]


def _draw_genome_overview(
    ax,
    features: list[FeatureRecord],
    highlighted_feature_names: set[str],
    reference_length_nt: int | None = None,
) -> None:
    """Draw a whole-genome CDS overview highlighting mutated features."""
    cds_features = sorted(
        (f for f in features if f.feature_type == 'CDS'),
        key=lambda f: (f.start, f.end, f.name),
    )
    if not cds_features:
        ax.set_axis_off()
        return

    tracks = _assign_feature_tracks(cds_features)
    genome_start, genome_end = _resolve_overview_bounds(cds_features, reference_length_nt)
    max_track = max(tracks.values(), default=0)
    ax.hlines(0.5, genome_start, genome_end, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=1)

    for feature in cds_features:
        y = -tracks[feature.name]
        is_highlighted = feature.name in highlighted_feature_names
        colour = CDS_HIGHLIGHTED_COLOUR if is_highlighted else FEATURE_DEFAULT_COLOUR
        edge = FEATURE_HIGHLIGHTED_EDGE if is_highlighted else FEATURE_DEFAULT_EDGE
        for seg_start, seg_end in _feature_plot_segments(feature):
            ax.add_patch(mpatches.Rectangle(
                (seg_start + 1, y), max(1, seg_end - seg_start), 0.9,
                facecolor=colour, edgecolor=edge, linewidth=0.7, zorder=100 + y,
            ))
        if is_highlighted:
            ax.text(
                feature.start + 1 + ((feature.end - feature.start) / 2), 1,
                feature.display_name,
                ha='left', va='baseline', fontsize=7, fontweight='bold',
                color=FEATURE_HIGHLIGHTED_EDGE, zorder=200 + y,
                rotation_mode='anchor', rotation=25,
            )

    ax.set_title('Genome overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_xlim(genome_start, genome_end)
    ax.set_ylim(-(max_track + 0.2), 1.2)
    ax.set_yticks([])
    ax.set_xlabel('Genomic position')
    for spine in ('top', 'left', 'right'):
        ax.spines[spine].set_visible(False)


def _resolve_overview_bounds(
    features: list[FeatureRecord],
    reference_length_nt: int | None,
) -> tuple[int, int]:
    """Return 1-based overview x-limits from reference length or max CDS end."""
    if reference_length_nt is not None and reference_length_nt > 0:
        return 1, reference_length_nt
    if not features:
        return 1, 1
    logger.warning('Reference length missing; falling back to CDS-derived overview bounds.')
    return 1, max(feature.end for feature in features)


def _draw_feature_track(
    ax,
    feature: FeatureRecord,
    parent_feature: FeatureRecord | None = None,
    mat_peptide_overlays: list[FeatureRecord] | None = None,
    rule_feature_names: set[str] | None = None,
    panel_name: str = 'CDS',
) -> None:
    """Draw a feature track header above its lollipop panel."""
    feature_width = feature.end - feature.start
    pad = max(10, int(feature_width * 0.03))
    ax.hlines(0.5, feature.start + 1 - pad, feature.end + pad, color=FEATURE_BASELINE_COLOUR, linewidth=1.0, zorder=-10)

    ax.add_patch(mpatches.Rectangle(
        (feature.start + 1, 0.3), max(1, feature_width), 0.4,
        facecolor=CDS_HIGHLIGHTED_COLOUR if feature.feature_type == 'CDS' else MATPEPTIDE_HIGHLIGHTED_COLOUR,
        edgecolor=FEATURE_HIGHLIGHTED_EDGE, alpha=1, linewidth=1.0, zorder=2,
    ))
    for gap_start, gap_end in _feature_intron_gaps(feature):
        ax.add_patch(mpatches.Rectangle(
            (gap_start + 1, 0.3), max(1, gap_end - gap_start), 0.4,
            facecolor=FEATURE_INTRON_COLOUR, edgecolor=FEATURE_INTRON_COLOUR, linewidth=0, zorder=3,
        ))

    ax.text(
        feature.start + 1 + (feature_width / 2), 0.5,
        f'← {feature.display_name} ←' if feature.strand == '-' else f'→ {feature.display_name} →',
        ha='center', va='center', fontsize=7, fontweight='bold', color='white',
    )
    ax.set_title(f'{panel_name} overview', fontsize=8, loc='left', fontweight='bold')
    ax.set_xlim(max(1, feature.start + 1 - pad), feature.end + pad)

    if mat_peptide_overlays:
        sorted_mps = sorted(mat_peptide_overlays, key=lambda f: f.start)
        boundaries: set[float] = {mp.start + 1 for mp in sorted_mps} | {sorted_mps[-1].end + 1}
        for x_boundary in sorted(boundaries):
            ax.vlines(x=x_boundary, ymin=0.3, ymax=0.7, colors=FEATURE_DEFAULT_COLOUR, linewidth=1.5, zorder=5)
        for mp in sorted_mps:
            ax.text(
                mp.start + 1 + max(1, mp.end - mp.start) / 2, 0.76, mp.display_name,
                ha='center', va='bottom', fontsize=7,
                color=MATPEPTIDE_HIGHLIGHTED_COLOUR, fontweight='bold', clip_on=True,
            )
        ax.set_ylim(0, 1.4)
    elif feature.feature_type == 'mat_peptide' and feature.parent_feature_name:
        strip_start, strip_end = _resolve_precursor_strip_bounds(ax, parent_feature)
        ax.add_patch(mpatches.Rectangle(
            (strip_start, 0.3), max(1, strip_end - strip_start), 0.4,
            facecolor=CDS_HIGHLIGHTED_COLOUR, edgecolor=FEATURE_HIGHLIGHTED_EDGE, linewidth=0.6, zorder=-4,
        ))
        ax.set_ylim(0, 1)
    else:
        ax.set_ylim(0, 1)

    ax.set_yticks([])
    for spine in ('top', 'left', 'right'):
        ax.spines[spine].set_visible(False)
    ax.set_xlabel('')
    ax.set_ylabel('')


def _resolve_precursor_strip_bounds(ax, parent_feature: FeatureRecord | None) -> tuple[float, float]:
    """Return precursor strip x-bounds clipped to the current axis limits."""
    x_left, x_right = ax.get_xlim()
    if parent_feature is None:
        return x_left, x_right
    visible_left = max(x_left, parent_feature.start + 1)
    visible_right = min(x_right, parent_feature.end)
    return (visible_left, visible_right) if visible_right > visible_left else (x_left, x_right)


def _draw_feature_panel(
    ax,
    feature: FeatureRecord,
    annotations: list[AnnotatedVariant],
    coverage_gaps: list[CoverageGap] | None = None,
    database_hit_annotation_ids: set[int] | None = None,
    shared_track_ax=None,
) -> None:
    """Draw one feature-focused bent lollipop mutation panel."""
    hit_ids = database_hit_annotation_ids or set()
    _draw_non_covered_regions(ax, feature, coverage_gaps or [])

    for ann, x_top in _apply_top_jitter(annotations, feature_length=feature.end - feature.start):
        x_base = ann.variant.pos + 1
        y_top = ann.variant.allele_freq
        colour = MUTATION_COLOURS.get(ann.consequence, MUTATION_COLOURS['unknown'])
        is_hit = id(ann) in hit_ids
        _draw_bent_lollipop(ax, x_base, x_top, y_top, colour)
        ax.scatter(
            x_top, y_top,
            color=colour,
            marker='s' if is_hit else 'o',
            s=50 if is_hit else 40,
            zorder=4,
            edgecolors='black' if is_hit else 'white',
            linewidths=0.5,
        )
        if is_hit:
            ax.annotate(
                f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}',
                (x_top, y_top),
                textcoords='offset points', xytext=(0, 7),
                fontsize=6, color=colour, fontweight='bold', rotation=25, ha='left',
            )

    pad = max(10, int((feature.end - feature.start) * 0.03))
    ax.hlines(0.0, feature.start + 1 - pad, feature.end + pad, color='black', linewidth=1.0, zorder=1, linestyle='--')
    ax.set_xlim(max(1, feature.start + 1 - pad), feature.end + pad)
    ax.set_ylim(-0.19, 1.05)
    ax.set_ylabel('variant frequency')
    ax.set_title(f'{feature.display_name} variants', fontsize=8, loc='left', pad=25, fontweight='bold')
    ax.grid(axis='y', color='#eef2f6', linewidth=0.8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#aab4be')
    ax.spines['bottom'].set_color('#aab4be')
    if shared_track_ax is not None:
        shared_track_ax.set_xlim(ax.get_xlim())


def _apply_top_jitter(
    annotations: list[AnnotatedVariant],
    feature_length: int,
) -> list[tuple[AnnotatedVariant, float]]:
    """Compute deterministic horizontal jitter for lollipop heads while keeping stems anchored."""
    sorted_anns = sorted(
        annotations,
        key=lambda ann: (ann.variant.pos, ann.variant.allele_freq, ann.alt_aa, ann.consequence),
    )
    x_values = [ann.variant.pos + 1 for ann in sorted_anns]
    jittered = adjust_array_min_distance(x_values, min_distance=feature_length / 75)
    return list(zip(sorted_anns, jittered))


def _group_coverage_gaps_by_feature(coverage_gaps: list[CoverageGap]) -> dict[str, list[CoverageGap]]:
    """Group coverage gaps by feature name ordered by ``codon_start``."""
    grouped: dict[str, list[CoverageGap]] = {}
    for gap in coverage_gaps:
        grouped.setdefault(gap.feature_name, []).append(gap)
    for gaps in grouped.values():
        gaps.sort(key=lambda gap: gap.codon_start)
    return grouped


def _draw_non_covered_regions(ax, feature: FeatureRecord, coverage_gaps: list[CoverageGap]) -> None:
    """Draw non-covered codon stretches as a translucent background overlay."""
    for gap in coverage_gaps:
        left_start, right_end = _coverage_gap_nt_bounds(feature, gap)
        ax.axvspan(
            left_start + 0.5, right_end + 0.5, ymin=0.0, ymax=1.0,
            facecolor=NON_COVERED_COLOUR, alpha=0.12, linewidth=0, zorder=0,
        )


def _codon_nt_span(feature: FeatureRecord, codon_pos: int) -> tuple[int, int]:
    """Return 0-based half-open genomic nt interval ``[start, end)`` for one codon index."""
    cds_nt_start = feature.codon_start + codon_pos * 3
    # Multi-exon features must map through exon segments so introns are skipped.
    if len(feature.segments) > 1:
        positions = [
            pos
            for offset in range(3)
            if (pos := feature.cds_to_genomic_position(cds_nt_start + offset)) is not None
        ]
        if positions:
            return min(positions), max(positions) + 1
    if feature.strand == '+':
        genomic_start = feature.start + cds_nt_start
        return genomic_start, genomic_start + 3
    genomic_high = (feature.end - 1) - cds_nt_start
    return genomic_high - 2, genomic_high + 1


def _coverage_gap_nt_bounds(feature: FeatureRecord, gap: CoverageGap) -> tuple[int, int]:
    """Return 0-based half-open genomic bounds spanning a coverage gap's codons."""
    start_a, end_a = _codon_nt_span(feature, gap.codon_start)
    start_b, end_b = _codon_nt_span(feature, gap.codon_end)
    return min(start_a, start_b), max(end_a, end_b)


def adjust_array_min_distance(
    values: Sequence[float],
    min_distance: float,
    max_iterations: int = 1000,
    tolerance: float = 1e-6,
) -> list[float]:
    """Iteratively separate 1D positions so adjacent values are at least *min_distance* apart."""
    values_arr = np.array(values, dtype=float)
    n = len(values_arr)
    if n <= 1:
        return [float(v) for v in values_arr.tolist()]

    for _ in range(max_iterations):
        sorted_indices = np.argsort(values_arr, kind='stable')
        max_adjustment = 0.0
        for i in range(1, n):
            idx1, idx2 = sorted_indices[i - 1], sorted_indices[i]
            gap = values_arr[idx2] - values_arr[idx1]
            if gap < min_distance:
                push = (min_distance - gap) / 2.0
                values_arr[idx1] -= push
                values_arr[idx2] += push
                max_adjustment = max(max_adjustment, push)
        if max_adjustment < tolerance:
            break
    return [float(v) for v in values_arr.tolist()]


def _draw_bent_lollipop(ax, x_base: float, x_top: float, y_top: float, colour: str) -> None:
    """Draw a three-segment bent lollipop stem anchored at *x_base* and ending at (*x_top*, *y_top*)."""
    ax.plot([x_base, x_base], [-0.2, -0.1], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_base, x_top], [-0.1, 0], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
    ax.plot([x_top, x_top], [0, y_top], color=colour, linewidth=1.0, alpha=0.85, zorder=2)
