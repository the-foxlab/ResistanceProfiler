"""HTML report generation with tabbed layout."""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from jinja2 import BaseLoader, Environment
from markupsafe import Markup, escape

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import CONSEQUENCE_LABELS, HIGH_IMPACT_CONSEQUENCES, classify_similarity
from respro.db.models import (
    FeatureRecord,
    ProfilingResult,
    Publication,
    ResistanceRule,
)
from respro.db.report_queries import (
    has_interpretation_algorithm,
    load_drug_alias_map,
    load_drug_cards,
    load_drug_class_map,
    load_feature_cards,
    load_numeric_metric_thresholds,
)
from respro.report.alignment_visualization import (
    FeatureAlignment,
    build_alignment_html,
    build_feature_alignments,
)

logger = logging.getLogger(__name__)

_SYNONYMOUS_CONSEQUENCES: frozenset[str] = frozenset({'synonymous_variant', 'synonymous'})

_ACCESSION_IDENTIFIER_RE = re.compile(
    r'^(?P<base>(?:[A-Z]{1,6}_[A-Z0-9]*\d[A-Z0-9]*|[A-Z]{1,6}\d[A-Z0-9]*))(?:\.(?P<version>\d+))?$'
)


def _load_svg_data_url(asset_name: str) -> str:
    """Load an SVG asset and return it as a data URL."""
    asset_path = Path(__file__).parent / 'static' / 'assets' / asset_name
    svg_text = asset_path.read_text(encoding='utf-8')
    return f'data:image/svg+xml,{quote(svg_text)}'


def build_report_context(
    result: ProfilingResult,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    features: list[FeatureRecord] | None = None,
    af_high_pct_source_threshold: float = 0.75,
    af_intermediate_pct_source_threshold: float = 0.25,
    af_low_min_pct_source_threshold: float = 0.01,
    combination_member_af_pct_source_threshold: float = 0.75,
) -> dict:
    """
    Build all data structures needed to render the report.

    :param result: profiling result to report on
    :param project_conn: optional project DB connection
    :param rules: optional resistance rules (kept for compatibility)
    :param features: optional feature records for display names
    :return: dictionary of context variables for Jinja2 template
    """
    summary = result.summary_dict()
    has_database_hit = result.database_hit_count > 0

    project_name = summary.get('project_name', '')
    organism = summary.get('organism', '')
    reference = summary.get('reference', '')
    sample = summary.get('sample', '')
    vcf = summary.get('vcf', '')
    timestamp = summary.get('timestamp', '')

    title_source = sample or vcf or reference or project_name or 'Resistance profile'

    # Multi-species detection: the report header states multiple references/organisms only
    # when the matched references span more than one distinct organism. Same-species
    # multi-reference runs (e.g. two HSV-1 references) keep the single-organism header so
    # the report is byte-identical to a single-reference run. The flag is also threaded
    # into the context for downstream conditional rendering (reference-id columns, etc.).
    distinct_organisms = {rg.organism for rg in result.references if rg.organism}
    is_multi_species = len(distinct_organisms) > 1

    # Map each annotation's chrom (== ReferenceGroup.query_name) to its reference_name so
    # the Database Hits / All Mutations / Sequence Feature tables can attribute rows to a
    # reference. Used only for the conditional Reference column shown in multi-species reports.
    reference_name_by_chrom: dict[str, str] = {
        rg.query_name: rg.reference_name for rg in result.references
    }
    # Map feature reference_id -> reference_name for sequence-feature cards (keyed by feature).
    reference_name_by_ref_id: dict[int, str] = {
        rg.reference_id: rg.reference_name for rg in result.references
    }

    if is_multi_species:
        # State multiple references: list the distinct organisms (and their references).
        # Group reference names by organism so each organism line names its references.
        ref_names_by_organism: dict[str, list[str]] = {}
        for rg in result.references:
            ref_names_by_organism.setdefault(rg.organism, []).append(rg.reference_name)
        organism_lines = []
        for org in sorted(ref_names_by_organism):
            ref_list = ', '.join(sorted(set(ref_names_by_organism[org])))
            organism_lines.append(f'{org} ({ref_list})')
        primary_parts = [
            f'Multiple references: {"; ".join(organism_lines)}',
            f'Database: {project_name}' if project_name else '',
        ]
    else:
        primary_parts = [
            organism,
            f'Reference: {reference}' if reference else '',
            f'Database: {project_name}' if project_name else '',
        ]
    secondary_parts = [
        f'File: {vcf}' if vcf else '',
        f'Generated {timestamp}' if timestamp else '',
    ]

    display_names = _build_feature_display_names(features)
    feature_lookup = _build_feature_lookup(features)
    detected_drug_names = _collect_detected_drug_names(result)
    drug_stats = _build_drug_stats(result)
    feature_stats = _build_feature_stats(result, features)

    feature_alignments: dict[str, FeatureAlignment] = {}
    if result.query_sequence and result.feature_matches:
        feature_alignments = build_feature_alignments(
            result.query_sequence, result.feature_matches
        )
    formula_hit_annotation_ids = _collect_formula_hit_annotation_ids(result)

    all_mutations_rows = _build_all_mutations_rows(
        result,
        feature_alignments,
        formula_hit_annotation_ids,
        display_names,
        reference_name_by_chrom=reference_name_by_chrom,
    )
    metric_thresholds = load_numeric_metric_thresholds(project_conn)
    drug_class_map = load_drug_class_map(project_conn)
    drug_alias_map = load_drug_alias_map(project_conn)
    database_hits = _build_database_hits_rows(
        result,
        project_conn,
        display_names,
        metric_thresholds,
        drug_class_map,
        drug_alias_map,
        reference_name_by_chrom=reference_name_by_chrom,
    )
    similarity_entries = _build_potential_effects_rows(
        result,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        rules=rules or [],
        display_names=display_names,
        metric_thresholds=metric_thresholds,
        drug_class_map=drug_class_map,
        drug_alias_map=drug_alias_map,
    )

    summary_context = _build_summary_context(
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

    db_drug_cards = load_drug_cards(project_conn, detected_drug_names)
    db_drug_cards_by_name = {
        (card.get('name') or '').strip().lower(): card
        for card in db_drug_cards
        if (card.get('name') or '').strip()
    }
    drug_cards = _build_drug_cards(drug_stats, db_drug_cards_by_name, drug_alias_map)

    detected_feature_names = set(feature_stats)
    db_feature_cards = load_feature_cards(
        project_conn,
        reference,
        detected_feature_names,
    )
    db_feature_cards_by_name = {
        (card.get('name') or '').strip(): card
        for card in db_feature_cards
        if (card.get('name') or '').strip()
    }
    feature_cards = _build_feature_cards(
        feature_stats,
        db_feature_cards_by_name,
        display_names,
        feature_lookup,
        reference_name_by_ref_id=reference_name_by_ref_id,
    )

    return {
        'title': f'Report: {title_source} resistance profile',
        'favicon': _load_svg_data_url('favicon.svg'),
        'is_multi_species': is_multi_species,
        'header': {
            'title': f'Report: {title_source} resistance profile',
            'version': __version__,
            'badge_label': 'Database hits found' if has_database_hit else 'No database hits found',
            'badge_icon': 'tick' if has_database_hit else 'x',
            'badge_class': 'is-hit' if has_database_hit else 'is-no-hit',
            'meta_primary': ' · '.join([part for part in primary_parts if part]),
            'meta_secondary': ' · '.join([part for part in secondary_parts if part]),
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
            'af_high_pct': int(af_high_pct_source_threshold * 100),
            'af_intermediate_pct': int(af_intermediate_pct_source_threshold * 100),
            'af_low_min_pct': int(af_low_min_pct_source_threshold * 100),
            'combination_member_af_pct': int(combination_member_af_pct_source_threshold * 100),
        },
        'database_hits': {**database_hits, 'is_multi_species': is_multi_species},
        'similarity_entries': similarity_entries,
        'summary': summary_context,
        'all_mutations': {
            'rows': all_mutations_rows,
            'count': len(all_mutations_rows),
            'has_database_hits': any(r['is_database_hit'] for r in all_mutations_rows),
            'is_multi_species': is_multi_species,
            'search_icon': _load_svg_data_url('search.svg'),
            'reset_icon': _load_svg_data_url('reset_filter.svg'),
        },
        'sequence_features': {
            'cards': feature_cards,
            'count': len(feature_cards),
            'is_multi_species': is_multi_species,
            'sequence_icon': _load_svg_data_url('dna.svg'),
            'link_icon': _load_svg_data_url('link.svg'),
        },
        'drugs': {
            'cards': drug_cards,
            'count': len(drug_cards),
            'structure_icon': _load_svg_data_url('structure.svg'),
            'pubchem_icon': _load_svg_data_url('link.svg'),
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
    af_high_pct_source_threshold: float = 0.75,
    af_intermediate_pct_source_threshold: float = 0.25,
    af_low_min_pct_source_threshold: float = 0.01,
    combination_member_af_pct_source_threshold: float = 0.75,
) -> str:
    """
    Render the complete HTML report.

    :param result: profiling result to report on
    :param plot_svg_data: optional SVG bytes of the embedded plot
    :param project_conn: optional project DB connection
    :param rules: optional list of resistance rules
    :param features: optional list of features for display name mapping
    :return: complete HTML document as string
    """
    plot_data_url = ''
    if plot_svg_data:
        encoded_svg = base64.b64encode(plot_svg_data).decode('ascii')
        plot_data_url = f'data:image/svg+xml;base64,{encoded_svg}'

    context = build_report_context(
        result,
        project_conn=project_conn,
        rules=rules,
        features=features,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        af_high_pct_source_threshold=af_high_pct_source_threshold,
        af_intermediate_pct_source_threshold=af_intermediate_pct_source_threshold,
        af_low_min_pct_source_threshold=af_low_min_pct_source_threshold,
        combination_member_af_pct_source_threshold=combination_member_af_pct_source_threshold,
    )
    context['plot'] = {
        'has_plot': bool(plot_data_url),
        'data_url': plot_data_url,
    }
    template_text = (Path(__file__).parent / 'templates' / 'report.html.j2').read_text(
        encoding='utf-8'
    )
    css_text = (Path(__file__).parent / 'static' / 'report.css').read_text(encoding='utf-8')
    js_text = (Path(__file__).parent / 'static' / 'report.js').read_text(encoding='utf-8')

    # autoescape=True ensures every {{ variable }} is HTML-escaped by default. The
    # | safe blocks below are all trusted server-bundled assets or pre-escaped markup:
    #   - css/js are static files read from respro/report/static (not user data)
    #   - context.summary.narrative and r.alignment_html are built with escape()
    # User-controlled values (context.title, header.*, sample/filename, drug/mutation
    # names) flow through plain {{ }} and are autoescaped, closing the stored-XSS vector.
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(template_text)

    return template.render(
        context=context,
        css=css_text,
        js=js_text,
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
    af_high_pct_source_threshold: float = 0.75,
    af_intermediate_pct_source_threshold: float = 0.25,
    af_low_min_pct_source_threshold: float = 0.01,
    combination_member_af_pct_source_threshold: float = 0.75,
) -> Path:
    """
    Render and write the HTML report to a file.

    Phase 2 stub - not yet implemented.

    :param result: profiling result to report on
    :param output_path: path to write HTML file to
    :param features: optional list of features for context
    :param plot_svg_data: optional SVG bytes of the embedded plot
    :param project_conn: optional database connection for additional data
    :param rules: optional list of resistance rules
    :return: path to the written HTML file
    """
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
        combination_member_af_pct_source_threshold=combination_member_af_pct_source_threshold,
    )
    output_path.write_text(html_content, encoding='utf-8')
    return output_path


def _build_all_mutations_rows(
    result: ProfilingResult,
    feature_alignments: dict[str, FeatureAlignment],
    formula_hit_annotation_ids: set[int],
    display_names: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> list[dict]:
    """
    Build one row per CDS annotation for the All Mutations tab.

    Each row carries the variant details, DB-hit status (single-rule and/or formula),
    and an optional inline alignment block. Using annotation-level rows (rather than
    variant-level) ensures overlapping features each produce their own row with the
    correct per-feature alignment.

    :param result: profiling result
    :param feature_alignments: gapped alignments keyed by feature name
    :param formula_hit_annotation_ids: annotation IDs participating in formula hits
    :param display_names: optional feature display-name overrides
    :param reference_name_by_chrom: optional chrom -> reference_name map for the
        conditional Reference column (multi-species reports)
    :return: list of row dicts for the template
    """
    rows: list[dict] = []
    for ann in result.cds_annotations:
        alignment_html = None
        if ann.feature_name in feature_alignments:
            alignment_html = build_alignment_html(ann, feature_alignments[ann.feature_name])

        is_single_hit = ann.is_resistance_hit
        is_formula_hit = id(ann) in formula_hit_annotation_ids
        display_consequence = ann.consequence

        pos_1based = ann.variant.pos + 1
        if ann.is_combined_codon_event and ann.ref_codon and ann.alt_codon:
            nt_change = f'{ann.ref_codon}{ann.codon_pos + 1}{ann.alt_codon}'
        else:
            nt_change = f'{ann.variant.ref}{pos_1based}{ann.variant.alt}'
        aa_change = (
            f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            if ann.ref_aa and ann.alt_aa
            else ''
        )

        rows.append({
            'feature': (display_names or {}).get(ann.feature_name, ann.feature_name),
            'nt_change': nt_change,
            'nt_pos': pos_1based,
            'aa_change': aa_change,
            'consequence': display_consequence,
            'allele_freq': ann.variant.allele_freq,
            'af_bin': ann.af_bin,
            'is_single_hit': is_single_hit,
            'is_formula_hit': is_formula_hit,
            'is_database_hit': is_single_hit or is_formula_hit,
            'alignment_html': str(alignment_html) if alignment_html is not None else '',
            'has_alignment': alignment_html is not None,
            'reference_name': (reference_name_by_chrom or {}).get(ann.variant.chrom, ''),
        })
    return rows


# Matches an optional qualifier (>, <, ≥, ≤, ~) followed by a leading number.
_RE_LEADING_NUM = re.compile(r'^[><=~≥≤≈\s]*(-?\d+(?:\.\d+)?)')


def _parse_numeric_value(value_str: str) -> float | None:
    """Extract a leading float from a potentially qualified metric string (e.g. '>0.5 µM' → 0.5)."""
    m = _RE_LEADING_NUM.match(value_str.strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _assign_numeric_tier(value: float, mean: float, std: float) -> str:
    """
    Map a numeric metric value to a severity tier CSS class using mean ± std thresholds.

    :param value: parsed float value
    :param mean: mean of all values for this field in the database
    :param std: standard deviation of all values for this field
    :return: CSS class string
    """
    if value <= mean:
        return 'metric--tier1'
    if value <= mean + std:
        return 'metric--tier2'
    if value <= mean + 2 * std:
        return 'metric--tier3'
    return 'metric--tier4'


def _format_drug_name_with_alias(name: str, alias_map: dict[str, str]) -> str:
    """Return canonical drug name with alias suffix (when alias exists)."""
    alias = alias_map.get(name.strip().lower(), '')
    if not alias:
        return name
    return f'{name} ({alias})'


def _build_database_hits_rows(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None = None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
    drug_alias_map: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> dict:
    """
    Build one row per database hit for the Database Hits table.

    Single rules and formula rules each produce one row. Formula-rule frequency is
    always 'high' since they only fire when allele_freq > 0.75 for every member.
    Publications are deduplicated globally and referenced by citation number.

    :param result: profiling result
    :param project_conn: optional project DB connection for metadata algorithms
    :param display_names: optional feature display-name overrides
    :param metric_thresholds: optional mean/std per numeric field for tier-badge coloring
    :param drug_class_map: optional mapping of normalized drug name to drug class/group name
    :param drug_alias_map: optional mapping of normalized drug name to alias
    :param reference_name_by_chrom: optional chrom -> reference_name map for the
        conditional Reference column (multi-species reports)
    :return: dict with 'rows', 'count', 'has_publications', 'has_drug_class', and 'bibliography'
    """
    ref_by_chrom = reference_name_by_chrom or {}
    rows: list[dict] = []
    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            feature = (display_names or {}).get(ann.feature_name, ann.feature_name)
            aa_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
                if ann.ref_aa and ann.alt_aa
                else ann.feature_name
            )
            # For wildcard insertion rules, prefix the rule label so the
            # database-hits table shows both the rule type and actual allele.
            if rule.mutation == 'INS_any':
                aa_change = f'INS_any ({aa_change})'
            rows.append({
                'drug_key': rule.drug_name,
                'drug': _format_drug_name_with_alias(rule.drug_name, drug_alias_map or {}),
                'drug_class': (drug_class_map or {}).get(rule.drug_name.strip().lower(), ''),
                'mutation_groups': [{'feature': feature, 'muts': [aa_change]}],
                'metrics': _build_rule_metrics(
                    rule.phenotype, rule.clinical_phenotype,
                    rule.ic50, rule.fold_ic50, rule.score,
                    thresholds=metric_thresholds,
                ),
                'af_bin': ann.af_bin,
                'source': rule.source,
                'comment': rule.comment,
                'reference_name': ref_by_chrom.get(ann.variant.chrom, ''),
                '_raw_pubs': list(rule.publications),
            })

    for formula_hit in result.formula_hits:
        rs = formula_hit.rule_set
        feature_to_muts: dict[str, list[str]] = {}
        # A formula hit may span multiple references; attribute the row to the first
        # matched variant's chrom (formula hits are reported as one combined row).
        first_chrom = formula_hit.matched_variants[0].variant.chrom if formula_hit.matched_variants else ''
        for ann in formula_hit.matched_variants:
            feature = (display_names or {}).get(ann.feature_name, ann.feature_name)
            aa_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
                if ann.ref_aa and ann.alt_aa
                else ann.feature_name
            )
            feature_to_muts.setdefault(feature, []).append(aa_change)
        mutation_groups = [{'feature': f, 'muts': muts} for f, muts in feature_to_muts.items()]
        rows.append({
            'drug_key': rs.drug_name,
            'drug': _format_drug_name_with_alias(rs.drug_name, drug_alias_map or {}),
            'drug_class': (drug_class_map or {}).get(rs.drug_name.strip().lower(), ''),
            'mutation_groups': mutation_groups,
            'metrics': _build_rule_metrics(
                rs.phenotype, rs.clinical_phenotype,
                rs.ic50, rs.fold_ic50, rs.score,
                thresholds=metric_thresholds,
            ),
            'af_bin': 'high',  # formula rules only fire at allele_freq > 0.75
            'source': rs.source,
            'comment': rs.comment,
            'reference_name': ref_by_chrom.get(first_chrom, ''),
            '_raw_pubs': list(rs.publications),
        })

    rows.extend(
        _build_effect_as_resistant_rows(
            result,
            project_conn,
            display_names,
            metric_thresholds,
            drug_class_map,
            drug_alias_map,
            reference_name_by_chrom=reference_name_by_chrom,
        )
    )

    rows.sort(key=lambda r: (
        r['drug'].lower(),
        [(g['feature'], g['muts']) for g in r['mutation_groups']],
    ))

    bibliography, pub_to_num = _build_bibliography(rows)
    for row in rows:
        citations: list[dict] = []
        seen_nums: set[int] = set()
        for pub in row.pop('_raw_pubs'):
            num = pub_to_num.get(_publication_key(pub))
            if num is None or num in seen_nums:
                continue
            seen_nums.add(num)
            url = ''
            if pub.doi:
                url = f'https://doi.org/{pub.doi}'
            elif pub.pubmed_id:
                url = f'https://pubmed.ncbi.nlm.nih.gov/{pub.pubmed_id}/'
            citations.append({'num': num, 'url': url})
        row['pub_citations'] = citations

    has_publications = any(row['pub_citations'] for row in rows)
    has_comments = any(row.get('comment') for row in rows)

    # Track which metric labels are actually used in any row
    metric_labels_present: set[str] = set()
    for row in rows:
        for metric in row.get('metrics', []):
            metric_labels_present.add(metric['label'])
    return {
        'rows': rows,
        'count': len(rows),
        'has_publications': has_publications,
        'has_drug_class': bool(drug_class_map),
        'has_comments': has_comments,
        'has_phenotype_metrics': 'Phenotype' in metric_labels_present,
        'has_clinical_phenotype_metrics': 'Clinical phenotype' in metric_labels_present,
        'has_ic50_metrics': 'IC50' in metric_labels_present,
        'has_fold_ic50_metrics': 'Fold IC50' in metric_labels_present,
        'has_score_metrics': 'Score' in metric_labels_present,
        'bibliography': bibliography,
        'info_icon': _load_svg_data_url('info.svg'),
        'search_icon': _load_svg_data_url('search.svg'),
        'reset_icon': _load_svg_data_url('reset_filter.svg'),
    }


def _load_algorithm_config(
    project_conn: sqlite3.Connection | None,
    algorithm_name: str,
) -> dict | None:
    """Load one interpretation algorithm config by name."""
    if project_conn is None:
        return None
    try:
        row = project_conn.execute(
            'SELECT config_json FROM interpretation_algorithm '
            'WHERE algorithm_name = ? LIMIT 1',
            (algorithm_name,),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to load %s algorithm from DB: %s', algorithm_name, exc)
        return None

    if row is None:
        return None

    try:
        config = json.loads(row['config_json'])
    except (TypeError, json.JSONDecodeError) as exc:
        logger.debug('Failed to parse %s algorithm config JSON: %s', algorithm_name, exc)
        return None
    if not isinstance(config, dict):
        return None
    return config


def _has_any_phenotype_association(project_conn: sqlite3.Connection | None) -> bool:
    """Return whether any rule row carries a known phenotype field."""
    if project_conn is None:
        return False
    known_clause = (
        "(TRIM(COALESCE(phenotype, '')) <> '' AND LOWER(TRIM(phenotype)) <> 'unknown') "
        "OR (TRIM(COALESCE(clinical_phenotype, '')) <> '' "
        "AND LOWER(TRIM(clinical_phenotype)) <> 'unknown')"
    )
    try:
        row = project_conn.execute(
            f'SELECT (EXISTS(SELECT 1 FROM resistance_rule WHERE {known_clause}) '
            f'OR EXISTS(SELECT 1 FROM resistance_formula_rule WHERE {known_clause})) AS has_rows'
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to check phenotype association rows in DB: %s', exc)
        return False

    if row is None:
        return False
    return bool(row['has_rows'])


def _references_match_with_accession_version(
    configured_reference: str,
    observed_reference: str,
) -> bool:
    """Return whether two references match exactly or by accession base plus version."""
    if configured_reference == observed_reference:
        return True

    configured_match = _ACCESSION_IDENTIFIER_RE.fullmatch(configured_reference)
    observed_match = _ACCESSION_IDENTIFIER_RE.fullmatch(observed_reference)
    if configured_match is None or observed_match is None:
        return False

    return configured_match.group('base') == observed_match.group('base')


def _build_effect_as_resistant_rows(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
    drug_alias_map: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> list[dict]:
    """Build metadata-only DB-hit rows for configured effect annotations."""
    if project_conn is None:
        return []

    effect_config = _load_algorithm_config(project_conn, 'effect_as_resistant')
    if effect_config is None:
        return []
    if not _has_any_phenotype_association(project_conn):
        return []

    config_rules = effect_config.get('rules')
    if not isinstance(config_rules, list) or not config_rules:
        return []

    rules_by_feature: dict[str, list[dict]] = {}
    for rule in config_rules:
        if not isinstance(rule, dict):
            continue
        feature = rule.get('feature')
        reference = rule.get('reference')
        drug = rule.get('drug')
        if not isinstance(feature, str) or not isinstance(reference, str) or not isinstance(drug, str):
            continue
        if not _references_match_with_accession_version(reference, result.reference_name):
            continue
        rules_by_feature.setdefault(feature, []).append(rule)

    if not rules_by_feature:
        return []

    rows: list[dict] = []
    for ann in result.cds_annotations:
        feature_rules = rules_by_feature.get(ann.feature_name, [])
        if not feature_rules:
            continue

        # Collect all effect lists from matching rules for this annotation's feature
        feature_display = (display_names or {}).get(ann.feature_name, ann.feature_name)
        aa_change = (
            f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            if ann.ref_aa and ann.alt_aa
            else ann.feature_name
        )
        for rule in feature_rules:
            rule_effects = rule.get('effect', [])
            if ann.consequence not in rule_effects:
                continue
            drug_name = (rule.get('drug') or '').strip()
            if not drug_name:
                continue
            label = CONSEQUENCE_LABELS.get(ann.consequence, ann.consequence)
            comment = (
                f'{label} interpreted as resistant by metadata algorithm '
                f'({ann.feature_name}, {result.reference_name}).'
            )
            rows.append({
                'drug_key': drug_name,
                'drug': _format_drug_name_with_alias(drug_name, drug_alias_map or {}),
                'drug_class': (drug_class_map or {}).get(drug_name.lower(), ''),
                'mutation_groups': [{'feature': feature_display, 'muts': [aa_change]}],
                'metrics': _build_rule_metrics(
                    'resistant',
                    '',
                    '',
                    '',
                    '',
                    thresholds=metric_thresholds,
                ),
                'af_bin': ann.af_bin,
                'source': 'Metadata algorithm',
                'comment': comment,
                'reference_name': (reference_name_by_chrom or {}).get(ann.variant.chrom, ''),
                '_raw_pubs': [],
            })
    return rows


def _build_bibliography(
    rows: list[dict],
) -> tuple[list[dict], dict[tuple, int]]:
    """
    Collect unique publications across all rows and assign sequential citation numbers.

    :param rows: row dicts containing '_raw_pubs' lists (not yet popped)
    :return: (ordered bibliography list, mapping of publication key → citation number)
    """
    seen: dict[tuple, int] = {}
    ordered: list[dict] = []
    num = 1
    for row in rows:
        for pub in row.get('_raw_pubs', []):
            key = _publication_key(pub)
            if key not in seen:
                seen[key] = num
                url = ''
                if pub.doi:
                    url = f'https://doi.org/{pub.doi}'
                elif pub.pubmed_id:
                    url = f'https://pubmed.ncbi.nlm.nih.gov/{pub.pubmed_id}/'
                ordered.append({
                    'num': num,
                    'url': url,
                    'label': pub.title or pub.doi or pub.pubmed_id or pub.raw_input,
                    'has_url': bool(url),
                })
                num += 1
    return ordered, seen


def _publication_key(pub: Publication) -> tuple:
    """Return a stable deduplication key for a publication."""
    pub_id = int(getattr(pub, 'id', 0) or 0)
    if pub_id > 0:
        return ('id', str(pub_id), '', '', '')
    doi = (pub.doi or '').strip().lower()
    pubmed_id = (pub.pubmed_id or '').strip()
    raw_input = (pub.raw_input or '').strip().lower()
    title = (pub.title or '').strip().lower()
    return ('meta', doi, pubmed_id, raw_input, title)


_PHENOTYPE_BADGE_CLASS = {
    'resistant': 'phenotype--resistant',
    'intermediate': 'phenotype--intermediate',
    'sensitive': 'phenotype--sensitive',
    'contradictory': 'phenotype--contradictory',
}


def _build_rule_metrics(
    phenotype: str,
    clinical_phenotype: str,
    ic50: str,
    fold_ic50: str,
    score: str,
    thresholds: dict[str, tuple[float, float] | None] | None = None,
) -> list[dict]:
    """Build metric chips for non-empty, non-unknown resistance data fields."""
    metrics: list[dict] = []
    if phenotype and phenotype.lower() != 'unknown':
        metrics.append({
            'label': 'Phenotype',
            'value': phenotype,
            'badge_class': _PHENOTYPE_BADGE_CLASS.get(phenotype.lower(), ''),
        })
    if clinical_phenotype and clinical_phenotype.lower() != 'unknown':
        metrics.append({
            'label': 'Clinical phenotype',
            'value': clinical_phenotype,
            'badge_class': _PHENOTYPE_BADGE_CLASS.get(clinical_phenotype.lower(), ''),
        })

    def _numeric_badge_class(field: str, value_str: str) -> str:
        if thresholds:
            stats = thresholds.get(field)
            if stats is not None:
                parsed = _parse_numeric_value(value_str)
                if parsed is not None:
                    return _assign_numeric_tier(parsed, *stats)
        return ''

    if ic50:
        metrics.append({'label': 'IC50', 'value': ic50, 'badge_class': _numeric_badge_class('ic50', ic50)})
    if fold_ic50:
        metrics.append({'label': 'Fold IC50', 'value': fold_ic50, 'badge_class': _numeric_badge_class('fold_ic50', fold_ic50)})
    if score:
        metrics.append({'label': 'Score', 'value': score, 'badge_class': _numeric_badge_class('score', score)})
    return metrics


def _build_feature_display_names(features: list[FeatureRecord] | None) -> dict[str, str]:
    """Build feature display-name mapping from loaded feature records."""
    if not features:
        return {}

    names: dict[str, str] = {}
    for feature in features:
        names[feature.name] = feature.display_name or feature.name
    return names


def _build_feature_lookup(features: list[FeatureRecord] | None) -> dict[str, FeatureRecord]:
    """Build a feature-name lookup from loaded feature records."""
    if not features:
        return {}

    return {feature.name: feature for feature in features}


def _collect_detected_drug_names(result: ProfilingResult) -> set[str]:
    """Collect lowercase drug names from single-rule and formula hits."""
    detected_drug_names: set[str] = set()

    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            drug_name = (rule.drug_name or '').strip()
            if drug_name:
                detected_drug_names.add(drug_name.lower())

    for combo_hit in result.formula_hits:
        drug_name = (combo_hit.rule_set.drug_name or '').strip()
        if drug_name:
            detected_drug_names.add(drug_name.lower())

    return detected_drug_names


def _collect_formula_hit_annotation_ids(result: ProfilingResult) -> set[int]:
    """Collect annotation object IDs that participate in any formula hit."""
    formula_hit_annotation_ids: set[int] = set()
    for formula_hit in result.formula_hits:
        for ann in formula_hit.matched_variants:
            # Formula hits reference annotation objects, not stable variant keys;
            # object identity preserves exact membership when overlaps share coordinates.
            formula_hit_annotation_ids.add(id(ann))
    return formula_hit_annotation_ids


def _build_drug_stats(result: ProfilingResult) -> dict[str, dict]:
    """Build per-drug counts from single-rule and formula hits."""
    direct_counter: Counter[str] = Counter()
    formula_counter: Counter[str] = Counter()
    display_names: dict[str, str] = {}

    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            drug_name = (rule.drug_name or '').strip()
            if not drug_name:
                continue
            key = drug_name.lower()
            direct_counter[key] += 1
            display_names.setdefault(key, drug_name)

    for combo_hit in result.formula_hits:
        drug_name = (combo_hit.rule_set.drug_name or '').strip()
        if not drug_name:
            continue
        key = drug_name.lower()
        formula_counter[key] += 1
        display_names.setdefault(key, drug_name)

    stats: dict[str, dict] = {}
    for key in sorted(set(direct_counter) | set(formula_counter)):
        direct_hits = direct_counter.get(key, 0)
        formula_hits = formula_counter.get(key, 0)
        stats[key] = {
            'name': display_names.get(key, key),
            'single_rule_hits': direct_hits,
            'formula_hits': formula_hits,
            'total_hits': direct_hits + formula_hits,
        }
    return stats


def _build_feature_stats(
    result: ProfilingResult,
    features: list[FeatureRecord] | None = None,
) -> dict[str, dict]:
    """Build counts per detected feature from annotation and hit data."""
    unassigned_feature_name = 'Unassigned'
    observed_counter: Counter[str] = Counter()
    direct_counter: Counter[str] = Counter()
    formula_counter: Counter[str] = Counter()

    if features:
        seen_variant_keys_by_feature: dict[str, set[tuple[int, str, str]]] = {
            feature.name: set() for feature in features
        }
        for ann in result.annotations:
            variant_key = (ann.variant.pos, ann.variant.ref, ann.variant.alt)
            for feature in features:
                if not feature.contains(ann.variant.pos):
                    continue
                seen_keys = seen_variant_keys_by_feature.setdefault(feature.name, set())
                if variant_key in seen_keys:
                    continue
                seen_keys.add(variant_key)
                observed_counter[feature.name] += 1
    else:
        for ann in result.cds_annotations:
            feature_name = (ann.feature_name or '').strip()
            if not feature_name:
                continue
            observed_counter[feature_name] += 1

    for ann in result.cds_annotations:
        feature_name = (ann.feature_name or '').strip() or unassigned_feature_name
        direct_counter[feature_name] += len(ann.non_formula_component_rule_matches)

    for formula_hit in result.formula_hits:
        # count once per unique feature the formula involves, not once per member variant
        hit_features: set[str] = set()
        for ann in formula_hit.matched_variants:
            hit_features.add((ann.feature_name or '').strip() or unassigned_feature_name)
        for feature_name in hit_features:
            formula_counter[feature_name] += 1

    stats: dict[str, dict] = {}
    # include observed features plus features that only carry hit counts.
    feature_names = set(observed_counter) | set(direct_counter) | set(formula_counter)
    for feature_name in sorted(feature_names, key=lambda item: item.lower()):
        direct_hits = direct_counter.get(feature_name, 0)
        formula_hits = formula_counter.get(feature_name, 0)
        stats[feature_name] = {
            'name': feature_name,
            'observed_variants': observed_counter.get(feature_name, 0),
            'database_hits': direct_hits + formula_hits,
        }
    return stats


def _build_drug_cards(
    drug_stats: dict[str, dict],
    db_drug_cards_by_name: dict[str, dict],
    drug_alias_map: dict[str, str] | None = None,
) -> list[dict]:
    """Merge detected-drug stats with optional DB metadata into card payloads."""
    cards: list[dict] = []
    for key in sorted(drug_stats, key=lambda name: drug_stats[name]['name'].lower()):
        stats = dict(drug_stats[key])
        stats['name'] = _format_drug_name_with_alias(stats['name'], drug_alias_map or {})
        metadata = db_drug_cards_by_name.get(key)
        if metadata:
            stats.update({
                'pubchem_url': metadata.get('pubchem_url', ''),
                'description': metadata.get('description', ''),
                'structure_url': metadata.get('structure_url', ''),
                'has_metadata': True,
            })
        else:
            stats.update({
                'pubchem_url': '',
                'description': '',
                'structure_url': '',
                'has_metadata': False,
            })
        cards.append(stats)
    return cards


def _build_feature_cards(
    feature_stats: dict[str, dict],
    db_feature_cards_by_name: dict[str, dict],
    display_names: dict[str, str],
    feature_lookup: dict[str, FeatureRecord],
    reference_name_by_ref_id: dict[int, str] | None = None,
) -> list[dict]:
    """Merge detected-feature stats with optional DB metadata into card payloads."""
    ref_by_id = reference_name_by_ref_id or {}
    cards: list[dict] = []
    for feature_name in sorted(feature_stats, key=lambda item: display_names.get(item, item).lower()):
        stats = dict(feature_stats[feature_name])
        metadata = db_feature_cards_by_name.get(feature_name)
        feature = feature_lookup.get(feature_name)
        stats['name'] = display_names.get(feature_name, feature_name)
        nt_sequence = ''
        aa_sequence = ''
        if feature is not None:
            nt_sequence = feature.nt_sequence or ''
            aa_sequence = feature.aa_sequence or ''
            stats['reference_name'] = ref_by_id.get(feature.reference_id, '')
        else:
            stats['reference_name'] = ''
        if metadata:
            stats.update({
                'protein': metadata.get('protein', ''),
                'protein_id': metadata.get('protein_id', ''),
                'ncbi_protein_url': metadata.get('ncbi_protein_url', ''),
                'locus_tag': metadata.get('locus_tag', ''),
                'note': metadata.get('note', ''),
                'nt_sequence': nt_sequence or metadata.get('nt_sequence', ''),
                'aa_sequence': aa_sequence or metadata.get('aa_sequence', ''),
                'has_metadata': True,
            })
        else:
            stats.update({
                'protein': '',
                'protein_id': '',
                'ncbi_protein_url': '',
                'locus_tag': '',
                'note': '',
                'nt_sequence': nt_sequence,
                'aa_sequence': aa_sequence,
                'has_metadata': False,
            })

        stats['has_sequence'] = bool(stats.get('nt_sequence') or stats.get('aa_sequence'))
        cards.append(stats)
    return cards


def _build_potential_effects_rows(
    result: ProfilingResult,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    rules: list[ResistanceRule] | None = None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
    drug_alias_map: dict[str, str] | None = None,
) -> dict:
    """
    Build the Similarity to Database Entries context.

    For single-rule variants that are NOT direct database hits, find resistance rules at
    the same feature + codon position and score amino acid similarity via BLOSUM62.
    Indels at indel-rule positions are reported with 'moderate' similarity.
    Frameshifts, stop gains, and synonymous changes are excluded.

    :param result: profiling result
    :param rules: loaded resistance rules for position-based lookup
    :param display_names: optional feature display-name overrides
    :param metric_thresholds: optional mean/std per numeric field for metric tier colouring
    :param drug_class_map: optional mapping of normalised drug name to drug class
    :param drug_alias_map: optional mapping of normalised drug name to alias
    :return: dict with 'rows', 'count', 'has_drug_class', 'has_publications', 'bibliography', icons
    """
    def _empty() -> dict:
        return {
            'rows': [],
            'count': 0,
            'has_drug_class': False,
            'has_publications': False,
            'bibliography': [],
            'info_icon': _load_svg_data_url('info.svg'),
            'search_icon': _load_svg_data_url('search.svg'),
            'reset_icon': _load_svg_data_url('reset_filter.svg'),
        }

    if not rules:
        return _empty()

    rules_by_pos: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        rules_by_pos.setdefault((rule.feature_name, rule.position), []).append(rule)

    excluded_consequences = {'frameshift', 'stop_gained', 'synonymous'}
    # These classes are excluded because AA-level similarity is not interpretable:
    # they are either disruptive, no-op, or not representable as one AA substitution.
    rows: list[dict] = []
    seen: set[tuple[str, int, str, str]] = set()

    for ann in result.cds_annotations:
        if ann.is_resistance_hit:
            continue
        if ann.consequence in excluded_consequences:
            continue
        if not ann.feature_name or not ann.alt_aa:
            continue

        pos_key = (ann.feature_name, ann.codon_pos)
        if pos_key not in rules_by_pos:
            continue

        ann_is_indel = ann.consequence in ('insertion', 'deletion') or len(ann.alt_aa) != 1

        for rule in rules_by_pos[pos_key]:
            if rule.drug_name == '__formula_component__':
                continue
            rule_is_indel = rule.mutation.lower() == 'fsx' or any(ch.isdigit() for ch in rule.mutation)
            if ann_is_indel and not rule_is_indel:
                continue

            dedup_key = (ann.feature_name, ann.codon_pos, ann.alt_aa, rule.drug_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            observed_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
                if ann.ref_aa and ann.alt_aa
                else ann.alt_aa or ''
            )
            rule_change = (
                f'{rule.reference}{rule.position + 1}{rule.mutation}'
                if rule.reference and rule.mutation
                else rule.mutation or ''
            )
            similarity = (
                'moderate' if ann_is_indel
                else classify_similarity(
                    ann.alt_aa,
                    rule.mutation,
                    high_threshold=similarity_high,
                    moderate_threshold=similarity_moderate,
                )
            )

            feature_name = (display_names or {}).get(ann.feature_name, ann.feature_name)
            rows.append({
                'feature': feature_name,
                'drug': _format_drug_name_with_alias(rule.drug_name, drug_alias_map or {}),
                'drug_class': (drug_class_map or {}).get(rule.drug_name.strip().lower(), ''),
                'mutation': observed_change,
                'rule_change': rule_change,
                'similarity': similarity,
                'metrics': _build_rule_metrics(
                    rule.phenotype, rule.clinical_phenotype,
                    rule.ic50, rule.fold_ic50, rule.score,
                    thresholds=metric_thresholds,
                ),
                'af_bin': ann.af_bin,
                'source': rule.source or '',
                '_raw_pubs': list(rule.publications),
            })

    rows.sort(key=lambda r: (r['drug'].lower(), r['mutation']))

    bibliography, pub_to_num = _build_bibliography(rows)
    for row in rows:
        citations: list[dict] = []
        seen_nums: set[int] = set()
        for pub in row.pop('_raw_pubs'):
            num = pub_to_num.get(_publication_key(pub))
            if num is None or num in seen_nums:
                continue
            seen_nums.add(num)
            url = ''
            if pub.doi:
                url = f'https://doi.org/{pub.doi}'
            elif pub.pubmed_id:
                url = f'https://pubmed.ncbi.nlm.nih.gov/{pub.pubmed_id}/'
            citations.append({'num': num, 'url': url})
        row['pub_citations'] = citations

    has_publications = any(row['pub_citations'] for row in rows)
    has_drug_class = bool(drug_class_map) and any(r.get('drug_class') for r in rows)

    # Track which metric labels are actually used in any row
    metric_labels_present: set[str] = set()
    for row in rows:
        for metric in row.get('metrics', []):
            metric_labels_present.add(metric['label'])
    return {
        'rows': rows,
        'count': len(rows),
        'has_drug_class': has_drug_class,
        'has_publications': has_publications,
        'has_phenotype_metrics': 'Phenotype' in metric_labels_present,
        'has_clinical_phenotype_metrics': 'Clinical phenotype' in metric_labels_present,
        'has_ic50_metrics': 'IC50' in metric_labels_present,
        'has_fold_ic50_metrics': 'Fold IC50' in metric_labels_present,
        'has_score_metrics': 'Score' in metric_labels_present,
        'bibliography': bibliography,
        'info_icon': _load_svg_data_url('info.svg'),
        'search_icon': _load_svg_data_url('search.svg'),
        'reset_icon': _load_svg_data_url('reset_filter.svg'),
    }


# ──────────────────────────────────────────────────────────────────────
# Summary tab helpers
# ──────────────────────────────────────────────────────────────────────

def _join_english_list(items: list[str]) -> str:
    """Join a list of strings with commas and a final Oxford 'and'."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f'{items[0]} and {items[1]}'
    return ', '.join(items[:-1]) + f', and {items[-1]}'


def _phenotype_sentence(stats: dict) -> str:
    """
    Build a concise phenotype-distribution sentence for one drug.

    Returns an empty string when all evidence is of unknown phenotype.
    """
    parts: list[str] = []
    if stats['resistant']:
        n = stats['resistant']
        parts.append(f"{n} {'mutation' if n == 1 else 'mutations'} associated with resistance")
    if stats['intermediate']:
        n = stats['intermediate']
        parts.append(f"{n} with an intermediate phenotype")
    if stats['sensitive']:
        n = stats['sensitive']
        parts.append(f"{n} associated with drug sensitivity")
    if not parts:
        return ''
    return _join_english_list(parts)


def _range_sentence(ranges: dict[str, int]) -> str:
    """Format IC50/fold-IC50 range data as a short parenthetical phrase."""
    if not ranges:
        return ''
    ordered = sorted(ranges.items(), key=lambda item: (-item[1], item[0].lower()))
    labels = [label for label, _ in ordered[:3]]
    return f"quantitative values: {_join_english_list(labels)}"


def _build_summary_context(
    result: ProfilingResult,
    display_names: dict[str, str],
    database_hits: dict,
    similarity_entries: dict,
    project_conn: sqlite3.Connection | None,
    drug_alias_map: dict[str, str] | None = None,
    formula_hit_annotation_ids: set[int] | None = None,
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> dict:
    """
    Build the complete context dict for the Summary tab.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :param database_hits: context dict from _build_database_hits_rows
    :param similarity_entries: context dict from _build_potential_effects_rows
    :param project_conn: optional project DB connection for algorithm lookup
    :param formula_hit_annotation_ids: annotation IDs participating in formula hits
    :param is_multi_species: whether references span more than one organism
    :param reference_name_by_chrom: chrom → reference name map (multi-species attribution)
    :return: summary context dict
    """
    formula_ids = formula_hit_annotation_ids or set()
    sequence_assessment = _build_sequence_assessment(result, display_names)
    gene_coverage = _compute_gene_coverage(result, display_names)
    mutation_profile = _build_mutation_profile(
        result,
        display_names,
        gene_coverage,
        formula_ids,
        is_multi_species=is_multi_species,
        reference_name_by_chrom=reference_name_by_chrom or {},
    )
    has_narrative = has_interpretation_algorithm(project_conn)
    drug_table = _build_drug_interpretation_table(
        result,
        database_hits,
        project_conn,
        drug_alias_map=drug_alias_map,
    )
    narrative = _build_summary_narrative(
        result,
        display_names,
        drug_table,
        formula_ids,
        is_multi_species=is_multi_species,
        reference_name_by_chrom=reference_name_by_chrom or {},
    )
    single_rule_hit_count = sum(
        len(ann.non_formula_component_rule_matches) for ann in result.cds_annotations
    )
    formula_rule_hit_count = len(result.formula_hits)
    return {
        'sequence_assessment': sequence_assessment,
        'mutation_profile': mutation_profile,
        'has_coverage': gene_coverage is not None,
        'has_narrative': has_narrative,
        'narrative': str(narrative),
        'drug_table': drug_table,
        'db_hits_summary': {
            'total': database_hits.get('count', 0),
            'single_rule_hits': single_rule_hit_count,
            'formula_rule_hits': formula_rule_hit_count,
        },
        'dna_icon': _load_svg_data_url('dna.svg'),
        'db_icon': _load_svg_data_url('icon-database.svg'),
        'info_icon': _load_svg_data_url('info.svg'),
        'report_icon': _load_svg_data_url('report.svg'),
        'drug_icon': _load_svg_data_url('drug.svg'),
    }


def _build_sequence_assessment(
    result: ProfilingResult,
    display_names: dict[str, str],
) -> dict:
    """
    Summarise variant types and high-impact consequences across all CDS annotations.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :return: dict with mutation counts, feature lists, and high-impact breakdown
    """
    feature_seen: set[str] = set()
    high_impact_features: set[str] = set()
    high_impact_by_consequence: Counter[str] = Counter()
    non_synonymous_count = 0

    for ann in result.cds_annotations:
        feature = display_names.get(ann.feature_name, ann.feature_name)
        if ann.consequence not in _SYNONYMOUS_CONSEQUENCES:
            non_synonymous_count += 1
            feature_seen.add(feature)
        if ann.consequence in HIGH_IMPACT_CONSEQUENCES:
            high_impact_features.add(feature)
            high_impact_by_consequence[ann.consequence] += 1

    high_impact_count = sum(high_impact_by_consequence.values())
    high_impact_type_parts = [
        f"{cnt} {CONSEQUENCE_LABELS[c]}{'s' if cnt != 1 else ''}"
        for c in CONSEQUENCE_LABELS
        if (cnt := high_impact_by_consequence.get(c, 0))
    ]
    return {
        'total_mutations': len(result.cds_annotations),
        'non_synonymous_count': non_synonymous_count,
        'features_with_mutations': sorted(feature_seen),
        'features_count': len(feature_seen),
        'high_impact_count': high_impact_count,
        'high_impact_types': _join_english_list(high_impact_type_parts) if high_impact_type_parts else '',
        'high_impact_features': sorted(high_impact_features),
    }


def _compute_gene_coverage(
    result: ProfilingResult,
    display_names: dict[str, str],
) -> dict[str, int] | None:
    """
    Compute covered percentage per feature from feature matches and coverage gaps.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :return: dict mapping display name to covered %, or None when no feature matches exist
    """
    if not result.feature_matches:
        return None

    total_codons: dict[str, int] = {}
    for match in result.feature_matches:
        feature = match.feature
        count = max(0, (len(feature.nt_sequence) - feature.codon_start) // 3)
        total_codons[feature.name] = count

    non_covered: dict[str, int] = {}
    for gap in result.coverage_gaps:
        non_covered[gap.feature_name] = (
            non_covered.get(gap.feature_name, 0) + gap.codon_end - gap.codon_start + 1
        )

    coverage: dict[str, int] = {}
    for feature_name, total in total_codons.items():
        display = display_names.get(feature_name, feature_name)
        nc = min(non_covered.get(feature_name, 0), total)
        coverage[display] = round(100 * (total - nc) / total) if total else 100
    return coverage


def _build_mutation_profile(
    result: ProfilingResult,
    display_names: dict[str, str],
    gene_coverage: dict[str, int] | None,
    formula_hit_annotation_ids: set[int],
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> list[dict]:
    """
    Group amino acid changes by feature with per-mutation styling flags.

    When ``is_multi_species`` is true, annotations are grouped by
    ``(reference_name, feature)`` so that same-named features from different
    references are not conflated, and each returned entry carries a
    ``reference_name``. When false, grouping is by feature only and no
    ``reference_name`` is added, keeping single-species output byte-identical.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :param formula_hit_annotation_ids: annotation IDs participating in formula hits
    :param is_multi_species: whether references span more than one organism
    :param reference_name_by_chrom: chrom → reference name map (multi-species attribution)
    :return: list of {feature, mutations: list[{label, is_db_hit, is_high_impact}} ordered by feature
    """
    ref_by_chrom = reference_name_by_chrom or {}
    # Key: (reference, feature, codon_pos, label) → merged style flags. Multiple NT variants
    # in the same codon can produce the same AA label; deduplicate and OR the flags.
    seen: dict[tuple[str | None, str, int, str], dict] = {}
    for ann in result.cds_annotations:
        if ann.consequence in _SYNONYMOUS_CONSEQUENCES:
            continue
        feature = display_names.get(ann.feature_name, ann.feature_name)
        ref_name = ref_by_chrom.get(ann.variant.chrom) if is_multi_species else None
        label = (
            f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            if ann.ref_aa and ann.alt_aa
            else ann.consequence
        )
        key = (ref_name, feature, ann.codon_pos, label)
        if key in seen:
            seen[key]['is_db_hit'] = (
                seen[key]['is_db_hit']
                or ann.is_resistance_hit
                or id(ann) in formula_hit_annotation_ids
            )
            seen[key]['is_high_impact'] = seen[key]['is_high_impact'] or ann.consequence in HIGH_IMPACT_CONSEQUENCES
        else:
            seen[key] = {
                'label': label,
                'is_db_hit': ann.is_resistance_hit or id(ann) in formula_hit_annotation_ids,
                'is_high_impact': ann.consequence in HIGH_IMPACT_CONSEQUENCES,
            }

    feature_mutations: dict[tuple[str | None, str], list[tuple[int, dict]]] = {}
    for (ref_name, feature, codon_pos, _label), entry in seen.items():
        feature_mutations.setdefault((ref_name, feature), []).append((codon_pos, entry))

    rows: list[dict] = []
    for (ref_name, feature), entries in sorted(
        feature_mutations.items(), key=lambda kv: (kv[0][1], kv[0][0] or '')
    ):
        row = {
            'feature': feature,
            'mutations': [m for _, m in sorted(entries, key=lambda x: x[0])],
            'covered_pct': gene_coverage.get(feature) if gene_coverage is not None else None,
        }
        if is_multi_species:
            row['reference_name'] = ref_name
        rows.append(row)
    return rows


def _build_summary_narrative(
    result: ProfilingResult,
    display_names: dict[str, str],
    drug_table: dict,
    formula_hit_annotation_ids: set[int],
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> Markup:
    """
    Build a concise clinician-facing narrative for the interpretation summary tile.

    Focuses on final drug assessments (ideally grouped by drug class), plus mandatory
    caveats on uncovered codon positions and high-impact variants lacking database evidence.

    When ``is_multi_species`` is true, the lead sentence attributes the profiled features
    per organism/reference instead of using a single ``result.organism``; the drug
    interpretation list remains one combined report. When false, the narrative is
    byte-identical to the single-species form.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :param drug_table: context dict from _build_drug_interpretation_table
    :param formula_hit_annotation_ids: annotation IDs participating in formula hits
    :param is_multi_species: whether references span more than one organism
    :param reference_name_by_chrom: chrom → reference name map (multi-species attribution)
    :return: HTML Markup narrative string
    """
    paragraphs: list[str] = []

    drug_rows = drug_table.get('rows', [])
    has_assessment = bool(drug_table.get('has_assessment'))
    assessed_rows = [
        row for row in drug_rows
        if (row.get('assessment') or '').strip()
    ]

    resistant_drugs = sorted([
        row.get('summary_name') or row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'resistant'
    ], key=lambda name: name.lower())
    intermediate_drugs = sorted([
        row.get('summary_name') or row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'intermediate'
    ], key=lambda name: name.lower())
    sensitive_drugs = sorted([
        row.get('summary_name') or row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'sensitive'
    ], key=lambda name: name.lower())

    # When multi-species, ProfilingResult.feature_matches only exposes the primary
    # reference's matches (it delegates to references[0]); aggregate across all
    # references so every organism's profiled features are attributed. The
    # single-species path keeps result.feature_matches for byte-identical output.
    if is_multi_species:
        all_feature_matches = [
            match for rg in result.references for match in rg.feature_matches
        ]
    else:
        all_feature_matches = result.feature_matches
    profiled_features = sorted({
        display_names.get(match.feature.name, match.feature.name)
        for match in all_feature_matches
    })
    was_were = 'were' if len(profiled_features) != 1 else 'was'

    # Multi-species attribution: group profiled features by organism so the lead
    # sentence names each organism alongside its features, instead of collapsing to
    # a single result.organism. The drug interpretation list below stays combined.
    organism_by_ref_id: dict[int, str] = {
        rg.reference_id: rg.organism for rg in result.references if rg.organism
    }
    ref_id_by_feature_name: dict[str, int] = {
        match.feature.name: match.feature.reference_id
        for match in all_feature_matches
    }

    def _organism_for_feature(feature_name: str) -> str:
        ref_id = ref_id_by_feature_name.get(feature_name)
        if ref_id is not None:
            org = organism_by_ref_id.get(ref_id)
            if org:
                return org
        return result.organism or 'Unknown organism'

    if is_multi_species and profiled_features:
        # Group features by organism, preserving sorted feature order within each.
        features_by_organism: dict[str, list[str]] = {}
        for feature in profiled_features:
            org = _organism_for_feature(feature)
            features_by_organism.setdefault(org, []).append(feature)
        per_organism_clauses = []
        for org in features_by_organism:
            feats = features_by_organism[org]
            feat_list = _join_english_list([escape(f) for f in feats])
            seq_word = 'sequence' if len(feats) == 1 else 'sequences'
            per_organism_clauses.append(
                f'the {seq_word} of {feat_list} of <strong>{escape(org)}</strong>'
            )
        feature_clause = 'The ' + _join_english_list(per_organism_clauses)
        was_were = 'were'
        # Each per-organism clause above already names its organism; do not append
        # the (single) primary organism again or it duplicates in the sentence.
        feature_organism_clause = feature_clause
    elif profiled_features:
        feature_list = _join_english_list([
            escape(feature) for feature in profiled_features
        ])
        feature_clause = f"The sequence{'s' if len(profiled_features) != 1 else ''} of {feature_list}"
        organism_name = escape(result.organism) if result.organism else 'Unknown organism'
        feature_organism_clause = f'{feature_clause} of <strong>{organism_name}</strong>'
    else:
        feature_clause = 'The input sequence'
        organism_name = escape(result.organism) if result.organism else 'Unknown organism'
        feature_organism_clause = f'{feature_clause} of <strong>{organism_name}</strong>'

    n_drugs = len(assessed_rows) if assessed_rows else len(drug_rows)
    if has_assessment and n_drugs:
        drug_word = 'drug' if n_drugs == 1 else 'drugs'
        if len(resistant_drugs) == 0 and len(intermediate_drugs) == 0:
            lead = (
                f'{feature_organism_clause} {was_were} evaluated against '
                f'known resistance-associated mutations for {n_drugs} {drug_word}. '
                'The assessment found no evidence for antiviral resistance for any drug.'
            )
        elif len(sensitive_drugs) == 0 and len(intermediate_drugs) == 0:
            lead = (
                f'{feature_organism_clause} {was_were} evaluated against '
                f'known resistance-associated mutations for {n_drugs} {drug_word}. '
                'The assessment found evidence for antiviral resistance for all analysed drugs.'
            )
        else:
            lead = (
                f'{feature_organism_clause} {was_were} evaluated against '
                f'known resistance-associated mutations for {n_drugs} {drug_word}. '
                f'The assessment found evidence for antiviral resistance against '
                f"{len(resistant_drugs)} {'drug' if len(resistant_drugs) == 1 else 'drugs'}, "
                f"intermediate resistance against {len(intermediate_drugs)} {'drug' if len(intermediate_drugs) == 1 else 'drugs'}, "
                f"and sensitivity for {len(sensitive_drugs)} {'drug' if len(sensitive_drugs) == 1 else 'drugs'}."
            )
    elif drug_rows:
        lead = (
            f'{feature_organism_clause} {was_were} evaluated against '
            f'known resistance-associated mutations, but no final drug interpretation '
            'algorithm is configured.'
        )
    else:
        lead = (
            f'{feature_organism_clause} were evaluated, '
            'but no in-scope drugs were available for interpretation.'
        )
    paragraphs.append(lead)

    uncovered_positions = sum(
        max(0, gap.codon_end - gap.codon_start + 1)
        for gap in result.coverage_gaps
    )
    coverage_gap_features = sorted({
        display_names.get(gap.feature_name, gap.feature_name)
        for gap in result.coverage_gaps
    })
    if uncovered_positions > 0 and coverage_gap_features:
        feature_list = _join_english_list([
            escape(feature) for feature in coverage_gap_features
        ])
        paragraphs.append(
            f'However, {uncovered_positions} position'
            f"{'s' if uncovered_positions != 1 else ''} of {feature_list} could not be assessed "
            'due to incomplete sequence data.'
        )

    high_impact_without_db = [
        ann for ann in result.cds_annotations
        if ann.consequence in HIGH_IMPACT_CONSEQUENCES
        and not ann.is_resistance_hit
        and id(ann) not in formula_hit_annotation_ids
    ]
    if high_impact_without_db:
        paragraphs.append(
            f'Importantly, {len(high_impact_without_db)} high-impact variant'
            f"{'s were' if len(high_impact_without_db) != 1 else ' was'} detected and require"
            ' manual interpretation.'
        )

    def _list_line(title: str, colour: str, drugs: list[str]) -> str:
        if drugs:
            values = _join_english_list([escape(name) for name in drugs])
        else:
            values = 'none'
        return f'<strong style="color: {colour};">{escape(title)}:</strong> {values}. '

    list_sections: list[str] = []
    if has_assessment and (assessed_rows or drug_rows):
        list_sections.append(_list_line(
            'List of drugs with resistance-associated mutations',
            '#991b1b',
            resistant_drugs,
        ))
        list_sections.append(_list_line(
            'List of drugs with mutations associated with intermediate resistance',
            '#c2410c',
            intermediate_drugs,
        ))
        list_sections.append(_list_line(
            'List of drugs without resistance-associated mutations',
            '#166534',
            sensitive_drugs,
        ))

    narrative_text = ' '.join(paragraphs)
    if list_sections:
        narrative_text += '<br><br>' + '<br>'.join(list_sections)

    return Markup(narrative_text)


def _threshold_source_label(
    config: dict,
    reference_name: str | None,
    drug_name: str,
    references_match,
) -> str:
    """Return a human-readable label for the resolution source of a drug's thresholds.

    Mirrors the precedence in :func:`respro.db.algorithms.resolve_thresholds`:
    ``(reference, drug)`` override > ``(drug)`` override > global ``thresholds``.

    :param config: drug_interpretation config dict
    :param reference_name: observed reference name, or ``None``
    :param drug_name: drug name
    :param references_match: accession-version tolerant reference matcher
    :return: source label for the per-cell hover
    """
    drug_lower = drug_name.strip().lower()
    for entry in (config.get('drug_thresholds') or []):
        if entry.get('drug', '').strip().lower() != drug_lower:
            continue
        ref = entry.get('reference')
        if ref is None:
            continue
        if reference_name is not None and references_match(ref, reference_name):
            return 'override (reference, drug)'
    for entry in (config.get('drug_thresholds') or []):
        if entry.get('drug', '').strip().lower() != drug_lower:
            continue
        if entry.get('reference') is None:
            return 'override (drug)'
    return 'global default'


def _build_drug_interpretation_table(
    result: ProfilingResult,
    database_hits: dict,
    project_conn: sqlite3.Connection | None,
    drug_alias_map: dict[str, str] | None = None,
) -> dict:
    """
    Build per-drug summary rows for the drug interpretation table.

    Includes all in-scope drugs (those with rules referencing profiled features),
    even with zero hits. Aggregates hit counts, phenotype breakdowns, and score sums
    per drug. Optionally computes an assessment using the stored drug_interpretation
    algorithm.

    :param result: profiling result, used to determine feature scope
    :param database_hits: context dict from _build_database_hits_rows
    :param project_conn: optional project DB connection for algorithm lookup
    :param drug_alias_map: optional mapping of normalized drug name to alias
    :return: dict with rows, groups, and capability flags
    """
    def _init_entry(name: str, drug_class: str) -> dict:
        alias = (drug_alias_map or {}).get(name.strip().lower(), '')
        return {
            'name': _format_drug_name_with_alias(name, drug_alias_map or {}),
            'summary_name': alias if alias and len(alias) < len(name) else name,
            'drug_class': drug_class,
            'hit_count': 0,
            'resistant_count': 0, 'intermediate_count': 0, 'sensitive_count': 0, 'contradictory_count': 0,
            'score_total': 0.0, 'score_display': '0',
            'ic50_display': '\u2014', 'fold_ic50_display': '\u2014',
            'ic50_values': [], 'fold_ic50_values': [],
            'assessment': '', 'assessment_badge_class': '',
            'method_assessments': [],
            'reference_names': set(),
        }

    def _assessment_description(method: str, resistant_t, intermediate_t) -> str:
        if method == 'by_phenotype':
            parts = [f'Resistant: \u2265{resistant_t} resistant phenotype hit(s).']
            if intermediate_t is not None:
                parts.append(f'Intermediate: \u2265{intermediate_t} intermediate phenotype hit(s).')
            parts.append('Contradictory: any contradictory hit(s).')
            parts.append('Otherwise: Sensitive.')
        elif method == 'by_score':
            parts = [f'Resistant: total score \u2265 {resistant_t}.']
            if intermediate_t is not None:
                parts.append(f'Intermediate: total score \u2265 {intermediate_t}.')
            parts.append('Otherwise: Sensitive.')
        elif method == 'by_ic50':
            parts = [f'Resistant: any IC50 value \u2265 {resistant_t}.']
            if intermediate_t is not None:
                parts.append(f'Intermediate: any IC50 value \u2265 {intermediate_t}.')
            parts.append('Otherwise: Sensitive.')
        elif method == 'by_fold_ic50':
            parts = [f'Resistant: any fold IC50 value \u2265 {resistant_t}.']
            if intermediate_t is not None:
                parts.append(f'Intermediate: any fold IC50 value \u2265 {intermediate_t}.')
            parts.append('Otherwise: Sensitive.')
        else:
            parts = []
        return ' '.join(parts)

    hit_rows = database_hits.get('rows', [])
    profiled_features = {m.feature.name for m in result.feature_matches}
    drug_class_map = load_drug_class_map(project_conn)

    # Pre-populate from project DB: all drugs with rules for profiled features
    by_drug: dict[str, dict] = {}
    if project_conn is not None and profiled_features:
        try:
            placeholders = ','.join('?' * len(profiled_features))
            for row in project_conn.execute(
                f'SELECT DISTINCT d.name FROM drug d '
                f'JOIN resistance_rule r ON r.drug_id = d.id '
                f'JOIN feature f ON r.feature_id = f.id '
                f'WHERE f.name IN ({placeholders})',
                tuple(profiled_features),
            ).fetchall():
                name = (row[0] or '').strip()
                if name and name != '__formula_component__':
                    by_drug[name] = _init_entry(
                        name,
                        drug_class_map.get(name.lower(), ''),
                    )
        except sqlite3.Error as exc:
            logger.debug('Failed to load in-scope drugs from DB: %s', exc)

    # Also seed any hit drugs not already present (covers no-project-conn case)
    for row in hit_rows:
        drug = (row.get('drug_key') or row.get('drug') or 'Unknown').strip()
        if drug not in by_drug and drug != '__formula_component__':
            dc = row.get('drug_class') or drug_class_map.get(drug.lower(), '')
            by_drug[drug] = _init_entry(
                drug,
                dc,
            )

    if not by_drug:
        return {
            'rows': [], 'groups': {}, 'has_groups': False,
            'has_phenotypes': False, 'has_scores': False, 'has_assessment': False,
            'assessment_description': '', 'col_count': 2,
        }

    # Accumulate counts and score sums from hit rows
    for row in hit_rows:
        drug = (row.get('drug_key') or row.get('drug') or 'Unknown').strip()
        by_drug[drug]['hit_count'] += 1
        ref_name = (row.get('reference_name') or '').strip()
        if ref_name:
            by_drug[drug]['reference_names'].add(ref_name)
        metrics = row.get('metrics', [])
        pheno = ''
        for m in metrics:
            if m.get('label') in ('Phenotype', 'Clinical phenotype'):
                pheno = (m.get('value') or '').strip().lower()
                if pheno and pheno != 'unknown':
                    break
        if pheno == 'resistant':
            by_drug[drug]['resistant_count'] += 1
        elif pheno == 'intermediate':
            by_drug[drug]['intermediate_count'] += 1
        elif pheno == 'sensitive':
            by_drug[drug]['sensitive_count'] += 1
        elif pheno == 'contradictory':
            by_drug[drug]['contradictory_count'] += 1
        for m in metrics:
            if m.get('label') == 'Score':
                val = _parse_numeric_value((m.get('value') or '').strip())
                if val is not None:
                    by_drug[drug]['score_total'] += val
                break
        for m in metrics:
            if m.get('label') == 'IC50':
                val = _parse_numeric_value((m.get('value') or '').strip())
                if val is not None:
                    by_drug[drug]['ic50_values'].append(val)
                break
        for m in metrics:
            if m.get('label') == 'Fold IC50':
                val = _parse_numeric_value((m.get('value') or '').strip())
                if val is not None:
                    by_drug[drug]['fold_ic50_values'].append(val)
                break

    # Column presence is determined by actual hit data, not zero-hit entries
    has_phenotypes = any(
        d['resistant_count'] + d['intermediate_count'] + d['sensitive_count'] + d['contradictory_count'] > 0
        for d in by_drug.values()
    )
    has_scores = any(
        any(m.get('label') == 'Score' and (m.get('value') or '').strip()
            for m in row.get('metrics', []))
        for row in hit_rows
    )
    has_groups = any(d['drug_class'] for d in by_drug.values())

    drug_interp_configs: list[dict] = []
    assessment_description = ''
    if project_conn is not None:
        try:
            interp_rows = project_conn.execute(
                "SELECT config_json FROM interpretation_algorithm "
                "WHERE algorithm_name = 'drug_interpretation' ORDER BY id"
            ).fetchall()
            for row in interp_rows:
                drug_interp_configs.append(json.loads(row['config_json']))
        except sqlite3.Error as exc:
            logger.debug('Failed to load drug_interpretation algorithm: %s', exc)

    has_assessment = len(drug_interp_configs) > 0
    has_final_assessment = len(drug_interp_configs) > 1
    # True when any configured drug_interpretation method carries per-(reference, drug)
    # overrides; gates the per-cell hover so the no-override path renders unchanged.
    has_drug_thresholds = any(
        bool(config.get('drug_thresholds')) for config in drug_interp_configs
    )
    method_labels: list[dict] = []
    if has_assessment:
        from respro.db.algorithms import (
            _METHOD_LABEL,
            compute_drug_assessment,
            resolve_thresholds,
        )
        from respro.db.algorithms import (
            _references_match as _algorithms_references_match,
        )
        for config in drug_interp_configs:
            method = config.get('method', '')
            thresholds = config.get('thresholds', {})
            resistant_threshold = thresholds.get('resistant', 1)
            intermediate_threshold = thresholds.get('intermediate')

            value_header = None
            value_field = None
            if method == 'by_ic50':
                value_header = 'Highest IC50'
                value_field = 'ic50_display'
            elif method == 'by_fold_ic50':
                value_header = 'Highest Fold IC50'
                value_field = 'fold_ic50_display'

            method_labels.append({
                'method': method,
                'label': _METHOD_LABEL.get(method, method),
                'description': _assessment_description(method, resistant_threshold, intermediate_threshold),
                'value_header': value_header,
                'value_field': value_field,
            })
        # Final assessment description
        assessment_description = (
            'Final assessment: most severe result across all methods '
            '(resistant > contradictory > intermediate > sensitive).'
        )

        for drug_data in by_drug.values():
            drug_name = next(
                (k for k, v in by_drug.items() if v is drug_data), ''
            )
            # Resolve the reference name(s) observed for this drug's hits. When a
            # drug has hits under multiple references, resolve against each and
            # take the strongest-wins assessment (most resistant). Drugs without
            # hits fall back to the profiled reference name.
            ref_names = drug_data.get('reference_names') or set()
            if not ref_names:
                ref_names = {result.reference_name} if result.reference_name else set()
            # Select the reference deterministically (sorted) so per-(reference,
            # drug) override resolution is stable across process invocations
            # (set iteration order depends on PYTHONHASHSEED).
            selected_reference = sorted(ref_names)[0] if ref_names else None
            final_assessment, method_assessments = compute_drug_assessment(
                drug_data, drug_interp_configs,
                reference_name=selected_reference,
                drug_name=drug_name or None,
            )
            drug_data['assessment'] = final_assessment
            drug_data['method_assessments'] = method_assessments
            # Add badge classes for per-method assessment styling
            for ma in method_assessments:
                ma['assessment_badge_class'] = _PHENOTYPE_BADGE_CLASS.get(
                    ma['assessment'].lower(), ''
                )
            # Attach resolved thresholds + source for the per-cell hover when
            # overrides are configured. Source labels mirror the precedence in
            # resolve_thresholds: (reference, drug) > (drug) > global.
            if has_drug_thresholds and drug_name:
                for ma in method_assessments:
                    config = next(
                        (c for c in drug_interp_configs if c.get('method') == ma['method']),
                        {},
                    )
                    ref_for_resolution = selected_reference
                    resistant_t, intermediate_t = resolve_thresholds(
                        config, ref_for_resolution, drug_name,
                    )
                    ma['resolved_thresholds'] = {
                        'resistant': resistant_t, 'intermediate': intermediate_t,
                    }
                    ma['threshold_source'] = _threshold_source_label(
                        config, ref_for_resolution, drug_name,
                        _algorithms_references_match,
                    )

    for drug_data in by_drug.values():
        drug_data['assessment_badge_class'] = _PHENOTYPE_BADGE_CLASS.get(
            drug_data['assessment'].lower(), ''
        )
        score = drug_data['score_total']
        drug_data['score_display'] = str(int(score)) if score == int(score) else f'{score:.2g}'
        ic50_vals = drug_data['ic50_values']
        if ic50_vals:
            highest = max(ic50_vals)
            drug_data['ic50_display'] = f'{highest:g}'
        fold_ic50_vals = drug_data['fold_ic50_values']
        if fold_ic50_vals:
            highest = max(fold_ic50_vals)
            drug_data['fold_ic50_display'] = f'{highest:g}'

    drug_rows = sorted(by_drug.values(), key=lambda d: d['name'].lower())
    groups: dict[str, list[dict]] = {}
    for drug_data in drug_rows:
        groups.setdefault(drug_data['drug_class'], []).append(drug_data)

    num_value_columns = sum(1 for ml in method_labels if ml['value_header'])
    col_count = (
        2
        + (3 if has_phenotypes else 0)
        + (1 if has_scores else 0)
        + (len(method_labels) if has_assessment else 0)
        + (num_value_columns if has_assessment else 0)
        + (1 if has_final_assessment else 0)
    )
    return {
        'rows': drug_rows,
        'groups': groups,
        'has_groups': has_groups,
        'has_phenotypes': has_phenotypes,
        'has_scores': has_scores,
        'has_assessment': has_assessment,
        'has_method_assessments': has_assessment,
        'has_final_assessment': has_final_assessment,
        'has_drug_thresholds': has_drug_thresholds,
        'method_labels': method_labels,
        'assessment_description': assessment_description,
        'col_count': col_count,
    }


