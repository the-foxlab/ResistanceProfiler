"""HTML report context assembly and Jinja2 rendering: main entry point."""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

from jinja2 import BaseLoader, Environment

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.db.models import FeatureRecord, ProfilingResult, ResistanceRule
from respro.db.report_queries import (
    load_drug_alias_map,
    load_drug_cards,
    load_drug_class_map,
    load_feature_cards,
    load_numeric_metric_thresholds,
)
from respro.report.alignment_visualization import FeatureAlignment, build_feature_alignments
from respro.report.html.cards import (
    build_drug_cards,
    build_drug_stats,
    build_feature_cards,
    build_feature_stats,
    collect_detected_drug_names,
)
from respro.report.html.formatting import (
    load_html_css_text,
    load_html_js_text,
    load_html_template_text,
    load_svg_data_url,
)
from respro.report.html.summary import build_summary_context
from respro.report.html.tables import (
    build_all_mutations_rows,
    build_database_hits_rows,
    build_potential_effects_rows,
)
from respro.report.row_helpers import (
    build_feature_display_names,
    build_reference_name_by_chrom,
    collect_formula_hit_annotation_ids,
)


def _build_all_feature_alignments(result: ProfilingResult) -> dict[tuple[str, str] | str, FeatureAlignment]:
    """Build gapped feature alignments across every ReferenceGroup, keyed by ``(chrom, feature_name)``."""
    alignments: dict[tuple[str, str] | str, FeatureAlignment] = {}
    single_ref = len(result.references) == 1
    for rg in result.references:
        if not rg.query_sequence or not rg.feature_matches:
            continue
        for fname, aln in build_feature_alignments(rg.query_sequence, rg.feature_matches).items():
            alignments[(rg.query_name, fname)] = aln
            if single_ref:
                alignments.setdefault(fname, aln)
    return alignments


def build_report_context(
    result: ProfilingResult,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    features: list[FeatureRecord] | None = None,
    af_high_pct_source_threshold: float | None = None,
    af_intermediate_pct_source_threshold: float | None = None,
    af_low_min_pct_source_threshold: float | None = None,
    combination_fraction_source_threshold: float | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> dict:
    """Assemble the complete Jinja2 template context for an HTML or PDF report."""
    af_bins = cfg.af_bins_fasta if result.is_fasta_mode else cfg.af_bins
    af_high = af_bins.high[0] if af_high_pct_source_threshold is None else af_high_pct_source_threshold
    af_inter = (
        af_bins.intermediate[0]
        if af_intermediate_pct_source_threshold is None
        else af_intermediate_pct_source_threshold
    )
    af_low = af_bins.low[0] if af_low_min_pct_source_threshold is None else af_low_min_pct_source_threshold
    combo_frac = (
        cfg.matching.min_cooccurrence_combination_fraction
        if combination_fraction_source_threshold is None
        else combination_fraction_source_threshold
    )

    summary = result.summary_dict()
    project_name = summary.get('project_name', '')
    organism = summary.get('organism', '')
    reference = summary.get('reference', '')
    sample = summary.get('sample', '')
    vcf = summary.get('vcf', '')
    timestamp = summary.get('timestamp', '')
    title_source = sample or vcf or reference or project_name or 'Resistance profile'

    distinct_organisms = {rg.organism for rg in result.references if rg.organism}
    is_multi_species = len(distinct_organisms) > 1
    reference_name_by_chrom = build_reference_name_by_chrom(result)
    reference_name_by_ref_id = {rg.reference_id: rg.reference_name for rg in result.references}

    if is_multi_species:
        ref_names_by_org: dict[str, list[str]] = {}
        for rg in result.references:
            ref_names_by_org.setdefault(rg.organism, []).append(rg.reference_name)
        org_lines = [
            f'{org} ({", ".join(sorted(set(ref_names_by_org[org])))})'
            for org in sorted(ref_names_by_org)
        ]
        primary_parts = [
            f'Multiple references: {"; ".join(org_lines)}',
            f'Database: {project_name}' if project_name else '',
        ]
    else:
        primary_parts = [
            organism,
            f'Reference: {reference}' if reference else '',
            f'Database: {project_name}' if project_name else '',
        ]
    secondary_parts = [f'File: {vcf}' if vcf else '', f'Generated {timestamp}' if timestamp else '']

    display_names = build_feature_display_names(features)
    feature_lookup = {(f.reference_id, f.name): f for f in features} if features else {}
    detected_drug_names = collect_detected_drug_names(result)
    drug_stats = build_drug_stats(result)
    feature_stats = build_feature_stats(result, features)
    feature_alignments = _build_all_feature_alignments(result)
    formula_hit_annotation_ids = collect_formula_hit_annotation_ids(result)

    all_mutations_rows = build_all_mutations_rows(
        result, feature_alignments, formula_hit_annotation_ids, display_names,
        reference_name_by_chrom=reference_name_by_chrom,
    )
    metric_thresholds = load_numeric_metric_thresholds(project_conn)
    drug_class_map = load_drug_class_map(project_conn)
    drug_alias_map = load_drug_alias_map(project_conn)
    database_hits = build_database_hits_rows(
        result, project_conn, display_names, metric_thresholds, drug_class_map, drug_alias_map,
        reference_name_by_chrom=reference_name_by_chrom, cfg=cfg,
    )
    similarity_entries = build_potential_effects_rows(
        result,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        rules=rules or [],
        display_names=display_names,
        metric_thresholds=metric_thresholds,
        drug_class_map=drug_class_map,
        drug_alias_map=drug_alias_map,
        cfg=cfg,
    )
    summary_context = build_summary_context(
        result,
        display_names=display_names,
        database_hits=database_hits,
        similarity_entries=similarity_entries,
        project_conn=project_conn,
        drug_alias_map=drug_alias_map,
        formula_hit_annotation_ids=formula_hit_annotation_ids,
        is_multi_species=is_multi_species,
        reference_name_by_chrom=reference_name_by_chrom,
    )

    db_drug_cards_by_name = {
        name.lower(): card
        for card in load_drug_cards(project_conn, detected_drug_names)
        if (name := (card.get('name') or '').strip())
    }
    drug_cards = build_drug_cards(drug_stats, db_drug_cards_by_name, drug_alias_map)

    all_ref_names = list(dict.fromkeys(rg.reference_name for rg in result.references if rg.reference_name))
    if not all_ref_names and reference:
        all_ref_names = [reference]
    detected_feature_names = {fname for _rid, fname in feature_stats}
    db_feature_cards_by_key = {
        (card.get('reference_name') or '', name): card
        for card in load_feature_cards(project_conn, all_ref_names, detected_feature_names)
        if (name := (card.get('name') or '').strip())
    }
    feature_cards = build_feature_cards(
        feature_stats, db_feature_cards_by_key, display_names, feature_lookup,
        reference_name_by_ref_id=reference_name_by_ref_id,
    )
    has_database_hit = database_hits['count'] > 0

    return {
        'title': f'Report: {title_source} resistance profile',
        'favicon': load_svg_data_url('favicon.svg'),
        'is_multi_species': is_multi_species,
        'header': {
            'title': f'Report: {title_source} resistance profile',
            'version': __version__,
            'badge_label': 'Database hits found' if has_database_hit else 'No database hits found',
            'badge_icon': 'tick' if has_database_hit else 'x',
            'badge_class': 'is-hit' if has_database_hit else 'is-no-hit',
            'meta_primary': ' · '.join(part for part in primary_parts if part),
            'meta_secondary': ' · '.join(part for part in secondary_parts if part),
        },
        'tabs': [
            'Summary',
            'Database Hits',
            *(['Similarity to Database Entries'] if similarity_entries['count'] else []),
            'All Mutations',
            'Sequence Feature Information',
            'Drug Information',
        ],
        'thresholds': {
            'similarity_high': similarity_high,
            'similarity_moderate': similarity_moderate,
            'af_high_pct': int(af_high * 100),
            'af_intermediate_pct': int(af_inter * 100),
            'af_low_min_pct': int(af_low * 100),
            'combination_fraction_pct': int(combo_frac * 100),
        },
        'database_hits': {**database_hits, 'is_multi_species': is_multi_species},
        'similarity_entries': similarity_entries,
        'summary': summary_context,
        'all_mutations': {
            'rows': all_mutations_rows,
            'count': len(all_mutations_rows),
            'has_database_hits': any(r['is_database_hit'] for r in all_mutations_rows),
            'has_user_ref_column': not result.is_fasta_mode,
            'has_variant_source_column': not result.is_fasta_mode,
            'has_combinatorial': any(r['is_combined_codon_event'] for r in all_mutations_rows),
            'is_multi_species': is_multi_species,
            'search_icon': load_svg_data_url('search.svg'),
            'reset_icon': load_svg_data_url('reset_filter.svg'),
            'info_icon': load_svg_data_url('info.svg'),
        },
        'sequence_features': {
            'cards': feature_cards,
            'count': len(feature_cards),
            'is_multi_species': is_multi_species,
            'sequence_icon': load_svg_data_url('dna.svg'),
            'link_icon': load_svg_data_url('link.svg'),
        },
        'drugs': {
            'cards': drug_cards,
            'count': len(drug_cards),
            'structure_icon': load_svg_data_url('structure.svg'),
            'pubchem_icon': load_svg_data_url('link.svg'),
        },
    }


def render_html(
    result: ProfilingResult,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    features: list[FeatureRecord] | None = None,
    af_high_pct_source_threshold: float | None = None,
    af_intermediate_pct_source_threshold: float | None = None,
    af_low_min_pct_source_threshold: float | None = None,
    combination_fraction_source_threshold: float | None = None,
    cfg: CliConfig = CLI_CONFIG,
    context: dict | None = None,
) -> str:
    """Render the complete standalone HTML report document."""
    plot_data_url = (
        f"data:image/svg+xml;base64,{base64.b64encode(plot_svg_data).decode('ascii')}"
        if plot_svg_data
        else ''
    )
    report_context = (
        dict(context)
        if context is not None
        else build_report_context(
            result,
            project_conn=project_conn,
            rules=rules,
            features=features,
            similarity_high=similarity_high,
            similarity_moderate=similarity_moderate,
            af_high_pct_source_threshold=af_high_pct_source_threshold,
            af_intermediate_pct_source_threshold=af_intermediate_pct_source_threshold,
            af_low_min_pct_source_threshold=af_low_min_pct_source_threshold,
            combination_fraction_source_threshold=combination_fraction_source_threshold,
            cfg=cfg,
        )
    )
    report_context['plot'] = {'has_plot': bool(plot_data_url), 'data_url': plot_data_url}

    # autoescape=True ensures all user-controlled fields are HTML-escaped by default.
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(load_html_template_text())
    return template.render(
        context=report_context,
        css=load_html_css_text(),
        js=load_html_js_text(),
    )


def write_html(
    result: ProfilingResult,
    output_path: Path,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    features: list[FeatureRecord] | None = None,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    af_high_pct_source_threshold: float | None = None,
    af_intermediate_pct_source_threshold: float | None = None,
    af_low_min_pct_source_threshold: float | None = None,
    combination_fraction_source_threshold: float | None = None,
    cfg: CliConfig = CLI_CONFIG,
    context: dict | None = None,
) -> Path:
    """Render and write the HTML report to *output_path*."""
    html_content = render_html(
        result,
        plot_svg_data=plot_svg_data,
        project_conn=project_conn,
        rules=rules,
        features=features,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        af_high_pct_source_threshold=af_high_pct_source_threshold,
        af_intermediate_pct_source_threshold=af_intermediate_pct_source_threshold,
        af_low_min_pct_source_threshold=af_low_min_pct_source_threshold,
        combination_fraction_source_threshold=combination_fraction_source_threshold,
        cfg=cfg,
        context=context,
    )
    output_path.write_text(html_content, encoding='utf-8')
    return output_path
