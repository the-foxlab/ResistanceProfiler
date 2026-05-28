"""HTML report generation with tabbed layout."""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
import statistics
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from jinja2 import BaseLoader, Environment
from markupsafe import Markup, escape

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import classify_similarity
from respro.db.models import (
    FeatureRecord,
    ProfilingResult,
    Publication,
    ResistanceRule,
)
from respro.report.alignment_visualization import (
    FeatureAlignment,
    build_alignment_html,
    build_feature_alignments,
)

logger = logging.getLogger(__name__)


def _load_template_text() -> str:
    """Load the Jinja2 template from filesystem."""
    template_path = Path(__file__).parent / 'templates' / 'report.html.j2'
    return template_path.read_text(encoding='utf-8')


def _load_css_text() -> str:
    """Load the report CSS stylesheet."""
    css_path = Path(__file__).parent / 'static' / 'report.css'
    return css_path.read_text(encoding='utf-8')


def _load_js_text() -> str:
    """Load the report JavaScript."""
    js_path = Path(__file__).parent / 'static' / 'report.js'
    return js_path.read_text(encoding='utf-8')


def _load_svg_data_url(asset_name: str) -> str:
    """Load an SVG asset and return it as a data URL."""
    asset_path = Path(__file__).parent / 'static' / 'assets' / asset_name
    svg_text = asset_path.read_text(encoding='utf-8')
    return f'data:image/svg+xml,{quote(svg_text)}'


def build_report_context(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    features: list[FeatureRecord] | None = None,
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

    all_mutations_rows = _build_all_mutations_rows(result, feature_alignments, display_names)
    metric_thresholds = _load_numeric_metric_thresholds(project_conn)
    drug_class_map = _load_drug_class_map(project_conn)
    database_hits = _build_database_hits_rows(result, display_names, metric_thresholds, drug_class_map)
    similarity_entries = _build_potential_effects_rows(
        result, rules or [], display_names, metric_thresholds, drug_class_map
    )

    summary_context = _build_summary_context(
        result,
        display_names=display_names,
        database_hits=database_hits,
        similarity_entries=similarity_entries,
        project_conn=project_conn,
    )

    db_drug_cards = _load_drug_cards(project_conn, detected_drug_names)
    db_drug_cards_by_name = {
        (card.get('name') or '').strip().lower(): card
        for card in db_drug_cards
        if (card.get('name') or '').strip()
    }
    drug_cards = _build_drug_cards(drug_stats, db_drug_cards_by_name)

    detected_feature_names = set(feature_stats)
    db_feature_cards = _load_feature_cards(
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
    )

    return {
        'title': f'Report: {title_source} resistance profile',
        'favicon': _load_svg_data_url('favicon.svg'),
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
            'similarity_high': CLI_CONFIG.similarity.high,
            'similarity_moderate': CLI_CONFIG.similarity.moderate,
            'af_high_pct': int(CLI_CONFIG.af_bins.high[0] * 100),
            'af_intermediate_pct': int(CLI_CONFIG.af_bins.intermediate[0] * 100),
            'af_low_min_pct': int(CLI_CONFIG.af_bins.low[0] * 100),
            'combination_member_af_pct': int(CLI_CONFIG.matching.combination_member_af_threshold * 100),
        },
        'database_hits': database_hits,
        'similarity_entries': similarity_entries,
        'summary': summary_context,
        'all_mutations': {
            'rows': all_mutations_rows,
            'count': len(all_mutations_rows),
            'has_database_hits': any(r['is_database_hit'] for r in all_mutations_rows),
            'search_icon': _load_svg_data_url('search.svg'),
            'reset_icon': _load_svg_data_url('reset_filter.svg'),
        },
        'sequence_features': {
            'cards': feature_cards,
            'count': len(feature_cards),
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
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    features: list[FeatureRecord] | None = None,
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
    )
    context['plot'] = {
        'has_plot': bool(plot_data_url),
        'data_url': plot_data_url,
    }
    template_text = _load_template_text()
    css_text = _load_css_text()
    js_text = _load_js_text()

    env = Environment(loader=BaseLoader())
    template = env.from_string(template_text)

    return template.render(
        context=context,
        css=css_text,
        js=js_text,
    )


def write_html(
    result: ProfilingResult,
    output_path: Path,
    features: list[FeatureRecord] | None = None,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
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
    )
    output_path.write_text(html_content, encoding='utf-8')
    return output_path


def _build_all_mutations_rows(
    result: ProfilingResult,
    feature_alignments: dict[str, FeatureAlignment],
    display_names: dict[str, str] | None = None,
) -> list[dict]:
    """
    Build one row per CDS annotation for the All Mutations tab.

    Each row carries the variant details, DB-hit status (single-rule and/or formula),
    and an optional inline alignment block. Using annotation-level rows (rather than
    variant-level) ensures overlapping features each produce their own row with the
    correct per-feature alignment.

    :param result: profiling result
    :param feature_alignments: gapped alignments keyed by feature name
    :param display_names: optional feature display-name overrides
    :return: list of row dicts for the template
    """
    formula_hit_ann_ids: set[int] = set()
    for formula_hit in result.formula_hits:
        for ann in formula_hit.matched_variants:
            formula_hit_ann_ids.add(id(ann))

    rows: list[dict] = []
    for ann in result.cds_annotations:
        alignment_html = None
        if ann.feature_name in feature_alignments:
            alignment_html = build_alignment_html(ann, feature_alignments[ann.feature_name])

        is_single_hit = ann.is_resistance_hit
        is_formula_hit = id(ann) in formula_hit_ann_ids
        display_consequence = 'complex' if ann.consequence == 'inframe_complex' else ann.consequence

        pos_1based = ann.variant.pos + 1
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
        })
    return rows


# Matches an optional qualifier (>, <, ≥, ≤, ~) followed by a leading number.
_RE_LEADING_NUM = re.compile(r'^[><=~≥≤≈\s]*(-?\d+(?:\.\d+)?)')

_HIGH_IMPACT_CONSEQUENCES: frozenset[str] = frozenset({
    'frameshift', 'stop_gained', 'stop_lost', 'start_lost', 'insertion', 'deletion',
})
_CONSEQUENCE_LABELS: dict[str, str] = {
    'frameshift': 'frameshift',
    'stop_gained': 'premature stop',
    'stop_lost': 'stop loss',
    'start_lost': 'start loss',
    'insertion': 'in-frame insertion',
    'deletion': 'in-frame deletion',
}


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


def _load_numeric_metric_thresholds(
    project_conn: sqlite3.Connection | None,
) -> dict[str, tuple[float, float] | None]:
    """
    Compute mean and standard deviation for each numeric metric field across all database rules.

    Queries both single rules and formula rules to capture the full population.
    Returns None for a field when fewer than two parseable values are available.

    :param project_conn: open project DB connection
    :return: dict mapping 'ic50', 'fold_ic50', 'score' to (mean, std) or None
    """
    if project_conn is None:
        return {}
    try:
        rows = project_conn.execute(
            'SELECT ic50, fold_ic50, score FROM resistance_rule '
            'UNION ALL '
            'SELECT ic50, fold_ic50, score FROM resistance_formula_rule'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load numeric metric stats from DB: %s', exc)
        return {}

    buckets: dict[str, list[float]] = {'ic50': [], 'fold_ic50': [], 'score': []}
    for row in rows:
        for field in ('ic50', 'fold_ic50', 'score'):
            raw = row[field] if isinstance(row, dict) else row[field]
            parsed = _parse_numeric_value(raw or '')
            if parsed is not None:
                buckets[field].append(parsed)

    result: dict[str, tuple[float, float] | None] = {}
    for field, values in buckets.items():
        if len(values) < 2:
            result[field] = None
        else:
            mean = statistics.mean(values)
            std = statistics.stdev(values)
            result[field] = (mean, std) if std > 0 else None
    return result


def _load_drug_class_map(
    project_conn: sqlite3.Connection | None,
) -> dict[str, str]:
    """
    Build a lowercase-drug-name → class-name map from the drug_groups algorithm config.

    :param project_conn: open project DB connection
    :return: dict mapping normalized drug name to drug class/group name; empty if not configured
    """
    if project_conn is None:
        return {}
    try:
        row = project_conn.execute(
            "SELECT config_json FROM interpretation_algorithm "
            "WHERE algorithm_name = 'drug_groups' LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug_groups algorithm from DB: %s', exc)
        return {}
    if row is None:
        return {}
    config = json.loads(row['config_json'])
    drug_map: dict[str, str] = {}
    for group_name, members in config.get('groups', {}).items():
        for drug in members:
            drug_map[drug.strip().lower()] = group_name
    return drug_map


def _build_database_hits_rows(
    result: ProfilingResult,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
) -> dict:
    """
    Build one row per database hit for the Database Hits table.

    Single rules and formula rules each produce one row. Formula-rule frequency is
    always 'high' since they only fire when allele_freq > 0.75 for every member.
    Publications are deduplicated globally and referenced by citation number.

    :param result: profiling result
    :param display_names: optional feature display-name overrides
    :param metric_thresholds: optional mean/std per numeric field for tier-badge coloring
    :param drug_class_map: optional mapping of normalized drug name to drug class/group name
    :return: dict with 'rows', 'count', 'has_publications', 'has_drug_class', and 'bibliography'
    """
    rows: list[dict] = []
    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            feature = (display_names or {}).get(ann.feature_name, ann.feature_name)
            aa_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
                if ann.ref_aa and ann.alt_aa
                else ann.feature_name
            )
            rows.append({
                'drug': rule.drug_name,
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
                '_raw_pubs': list(rule.publications),
            })

    for formula_hit in result.formula_hits:
        rs = formula_hit.rule_set
        feature_to_muts: dict[str, list[str]] = {}
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
            'drug': rs.drug_name,
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
            '_raw_pubs': list(rs.publications),
        })

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


def _build_drug_cards(drug_stats: dict[str, dict], db_drug_cards_by_name: dict[str, dict]) -> list[dict]:
    """Merge detected-drug stats with optional DB metadata into card payloads."""
    cards: list[dict] = []
    for key in sorted(drug_stats, key=lambda name: drug_stats[name]['name'].lower()):
        stats = dict(drug_stats[key])
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
) -> list[dict]:
    """Merge detected-feature stats with optional DB metadata into card payloads."""
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
    rules: list[ResistanceRule] | None = None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
) -> dict:
    """
    Build the Similarity to Database Entries context.

    For single-rule variants that are NOT direct database hits, find resistance rules at
    the same feature + codon position and score amino acid similarity via BLOSUM62.
    Indels at indel-rule positions are reported with 'moderate' similarity.
    Frameshifts, stop gains, synonymous changes, and complex indels are excluded.

    :param result: profiling result
    :param rules: loaded resistance rules for position-based lookup
    :param display_names: optional feature display-name overrides
    :param metric_thresholds: optional mean/std per numeric field for metric tier colouring
    :param drug_class_map: optional mapping of normalised drug name to drug class
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

    excluded_consequences = {'frameshift', 'stop_gained', 'synonymous', 'inframe_complex'}
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
                else classify_similarity(ann.alt_aa, rule.mutation)
            )

            feature_name = (display_names or {}).get(ann.feature_name, ann.feature_name)
            rows.append({
                'feature': feature_name,
                'drug': rule.drug_name,
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


def _has_interpretation_algorithm(project_conn: sqlite3.Connection | None) -> bool:
    """
    Return True when the project DB has at least one interpretation algorithm registered.

    :param project_conn: optional project DB connection
    :return: True if any algorithm is configured, False otherwise
    """
    if project_conn is None:
        return False
    try:
        row = project_conn.execute(
            'SELECT 1 FROM interpretation_algorithm LIMIT 1'
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _build_summary_context(
    result: ProfilingResult,
    display_names: dict[str, str],
    database_hits: dict,
    similarity_entries: dict,
    project_conn: sqlite3.Connection | None,
) -> dict:
    """
    Build the complete context dict for the Summary tab.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :param database_hits: context dict from _build_database_hits_rows
    :param similarity_entries: context dict from _build_potential_effects_rows
    :param project_conn: optional project DB connection for algorithm lookup
    :return: summary context dict
    """
    sequence_assessment = _build_sequence_assessment(result, display_names)
    gene_coverage = _compute_gene_coverage(result, display_names)
    mutation_profile = _build_mutation_profile(result, display_names, gene_coverage)
    has_narrative = _has_interpretation_algorithm(project_conn)
    drug_table = _build_drug_interpretation_table(result, database_hits, project_conn)
    narrative = _build_summary_narrative(
        result, display_names, drug_table,
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
        feature_seen.add(feature)
        if ann.consequence not in ('synonymous_variant', 'synonymous'):
            non_synonymous_count += 1
        if ann.consequence in _HIGH_IMPACT_CONSEQUENCES:
            high_impact_features.add(feature)
            high_impact_by_consequence[ann.consequence] += 1

    high_impact_count = sum(high_impact_by_consequence.values())
    high_impact_type_parts = [
        f"{cnt} {_CONSEQUENCE_LABELS[c]}{'s' if cnt != 1 else ''}"
        for c in _CONSEQUENCE_LABELS
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
) -> list[dict]:
    """
    Group amino acid changes by feature with per-mutation styling flags.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :return: list of {feature, mutations: list[{label, is_db_hit, is_high_impact}]} ordered by feature
    """
    formula_hit_ann_ids: set[int] = set()
    for formula_hit in result.formula_hits:
        for ann in formula_hit.matched_variants:
            formula_hit_ann_ids.add(id(ann))

    # Key: (feature, codon_pos, label) → merged style flags. Multiple NT variants
    # in the same codon can produce the same AA label; deduplicate and OR the flags.
    seen: dict[tuple[str, int, str], dict] = {}
    for ann in result.cds_annotations:
        feature = display_names.get(ann.feature_name, ann.feature_name)
        label = (
            f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            if ann.ref_aa and ann.alt_aa
            else ann.consequence
        )
        key = (feature, ann.codon_pos, label)
        if key in seen:
            seen[key]['is_db_hit'] = seen[key]['is_db_hit'] or ann.is_resistance_hit or id(ann) in formula_hit_ann_ids
            seen[key]['is_high_impact'] = seen[key]['is_high_impact'] or ann.consequence in _HIGH_IMPACT_CONSEQUENCES
        else:
            seen[key] = {
                'label': label,
                'is_db_hit': ann.is_resistance_hit or id(ann) in formula_hit_ann_ids,
                'is_high_impact': ann.consequence in _HIGH_IMPACT_CONSEQUENCES,
            }

    feature_mutations: dict[str, list[tuple[int, dict]]] = {}
    for (feature, codon_pos, _label), entry in seen.items():
        feature_mutations.setdefault(feature, []).append((codon_pos, entry))

    return [
        {
            'feature': feature,
            'mutations': [m for _, m in sorted(entries, key=lambda x: x[0])],
            'covered_pct': gene_coverage.get(feature) if gene_coverage is not None else None,
        }
        for feature, entries in sorted(feature_mutations.items())
    ]


def _build_summary_narrative(
    result: ProfilingResult,
    display_names: dict[str, str],
    drug_table: dict,
) -> Markup:
    """
    Build a concise clinician-facing narrative for the interpretation summary tile.

    Focuses on final drug assessments (ideally grouped by drug class), plus mandatory
    caveats on uncovered codon positions and high-impact variants lacking database evidence.

    :param result: profiling result
    :param display_names: feature display-name overrides
    :param drug_table: context dict from _build_drug_interpretation_table
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
        row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'resistant'
    ], key=lambda name: name.lower())
    intermediate_drugs = sorted([
        row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'intermediate'
    ], key=lambda name: name.lower())
    sensitive_drugs = sorted([
        row.get('name') or 'Unknown'
        for row in assessed_rows
        if (row.get('assessment') or '').strip().lower() == 'sensitive'
    ], key=lambda name: name.lower())

    profiled_features = sorted({
        display_names.get(match.feature.name, match.feature.name)
        for match in result.feature_matches
    })
    if profiled_features:
        feature_list = _join_english_list([
            escape(feature) for feature in profiled_features
        ])
        feature_clause = f"The sequence{'s' if len(profiled_features) != 1 else ''} of {feature_list}"
    else:
        feature_clause = 'The input sequence'

    organism_name = escape(result.organism) if result.organism else 'Unknown organism'
    n_drugs = len(assessed_rows) if assessed_rows else len(drug_rows)
    if has_assessment and n_drugs:
        drug_word = 'drug' if n_drugs == 1 else 'drugs'
        lead = (
            f'{feature_clause} of <strong>{organism_name}</strong> were evaluated against '
            f'known resistance-associated mutations for {n_drugs} {drug_word}. '
            f'The assessment found evidence for antiviral resistance against '
            f"{len(resistant_drugs)} {'drug' if len(resistant_drugs) == 1 else 'drugs'}, "
            f"intermediate resistance against {len(intermediate_drugs)} {'drug' if len(intermediate_drugs) == 1 else 'drugs'}, "
            f"and sensitivity for {len(sensitive_drugs)} {'drug' if len(sensitive_drugs) == 1 else 'drugs'}."
        )
    elif drug_rows:
        lead = (
            f'{feature_clause} of <strong>{organism_name}</strong> were evaluated against '
            f'known resistance-associated mutations, but no final drug interpretation '
            'algorithm is configured.'
        )
    else:
        lead = (
            f'{feature_clause} of <strong>{organism_name}</strong> were evaluated, '
            'but no in-scope drugs were available for interpretation.'
        )
    paragraphs.append(lead)

    uncovered_positions = sum(
        max(0, gap.codon_end - gap.codon_start + 1)
        for gap in result.coverage_gaps
    )
    coverage_gap_features = sorted({
        display_names.get(gap.feature_name, gap.feature_name).lower()
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

    formula_hit_ann_ids: set[int] = set()
    for formula_hit in result.formula_hits:
        for ann in formula_hit.matched_variants:
            formula_hit_ann_ids.add(id(ann))

    high_impact_without_db = [
        ann for ann in result.cds_annotations
        if ann.consequence in _HIGH_IMPACT_CONSEQUENCES
        and not ann.is_resistance_hit
        and id(ann) not in formula_hit_ann_ids
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


def _build_drug_interpretation_table(
    result: ProfilingResult,
    database_hits: dict,
    project_conn: sqlite3.Connection | None,
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
    :return: dict with rows, groups, and capability flags
    """
    def _init_entry(name: str, drug_class: str) -> dict:
        return {
            'name': name, 'drug_class': drug_class, 'hit_count': 0,
            'resistant_count': 0, 'intermediate_count': 0, 'sensitive_count': 0,
            'score_total': 0.0, 'score_display': '0',
            'assessment': '', 'assessment_badge_class': '',
        }

    def _assessment_description(method: str, resistant_t, intermediate_t) -> str:
        if method == 'by_phenotype':
            parts = [f'Resistant: \u2265{resistant_t} resistant phenotype hit(s).']
            if intermediate_t is not None:
                parts.append(f'Intermediate: \u2265{intermediate_t} intermediate phenotype hit(s).')
        elif method == 'by_score':
            parts = [f'Resistant: total score \u2265 {resistant_t}.']
            if intermediate_t is not None:
                parts.append(f'Intermediate: total score \u2265 {intermediate_t}.')
        else:
            parts = []
        parts.append('Otherwise: Sensitive.')
        return ' '.join(parts)

    hit_rows = database_hits.get('rows', [])
    profiled_features = {m.feature.name for m in result.feature_matches}
    drug_class_map = _load_drug_class_map(project_conn)

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
                    by_drug[name] = _init_entry(name, drug_class_map.get(name.lower(), ''))
        except sqlite3.Error as exc:
            logger.debug('Failed to load in-scope drugs from DB: %s', exc)

    # Also seed any hit drugs not already present (covers no-project-conn case)
    for row in hit_rows:
        drug = (row.get('drug') or 'Unknown').strip()
        if drug not in by_drug and drug != '__formula_component__':
            dc = row.get('drug_class') or drug_class_map.get(drug.lower(), '')
            by_drug[drug] = _init_entry(drug, dc)

    if not by_drug:
        return {
            'rows': [], 'groups': {}, 'has_groups': False,
            'has_phenotypes': False, 'has_scores': False, 'has_assessment': False,
            'assessment_description': '', 'col_count': 2,
        }

    # Accumulate counts and score sums from hit rows
    for row in hit_rows:
        drug = (row.get('drug') or 'Unknown').strip()
        by_drug[drug]['hit_count'] += 1
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
        for m in metrics:
            if m.get('label') == 'Score':
                val = _parse_numeric_value((m.get('value') or '').strip())
                if val is not None:
                    by_drug[drug]['score_total'] += val
                break

    # Column presence is determined by actual hit data, not zero-hit entries
    has_phenotypes = any(
        d['resistant_count'] + d['intermediate_count'] + d['sensitive_count'] > 0
        for d in by_drug.values()
    )
    has_scores = any(
        any(m.get('label') == 'Score' and (m.get('value') or '').strip()
            for m in row.get('metrics', []))
        for row in hit_rows
    )
    has_groups = any(d['drug_class'] for d in by_drug.values())

    drug_interp_config: dict | None = None
    assessment_description = ''
    if project_conn is not None:
        try:
            interp_row = project_conn.execute(
                "SELECT config_json FROM interpretation_algorithm "
                "WHERE algorithm_name = 'drug_interpretation' LIMIT 1"
            ).fetchone()
            if interp_row:
                drug_interp_config = json.loads(interp_row['config_json'])
        except sqlite3.Error as exc:
            logger.debug('Failed to load drug_interpretation algorithm: %s', exc)

    has_assessment = drug_interp_config is not None
    if has_assessment:
        method = drug_interp_config.get('method', '')
        thresholds = drug_interp_config.get('thresholds', {})
        resistant_threshold = thresholds.get('resistant', 1)
        intermediate_threshold = thresholds.get('intermediate')
        assessment_description = _assessment_description(method, resistant_threshold, intermediate_threshold)
        for drug_data in by_drug.values():
            if method == 'by_phenotype':
                if drug_data['resistant_count'] >= resistant_threshold:
                    drug_data['assessment'] = 'resistant'
                elif (
                    intermediate_threshold is not None
                    and drug_data['intermediate_count'] >= intermediate_threshold
                ):
                    drug_data['assessment'] = 'intermediate'
                else:
                    drug_data['assessment'] = 'sensitive'
            elif method == 'by_score':
                total = drug_data['score_total']
                if total >= resistant_threshold:
                    drug_data['assessment'] = 'resistant'
                elif intermediate_threshold is not None and total >= intermediate_threshold:
                    drug_data['assessment'] = 'intermediate'
                else:
                    drug_data['assessment'] = 'sensitive'

    for drug_data in by_drug.values():
        drug_data['assessment_badge_class'] = _PHENOTYPE_BADGE_CLASS.get(
            drug_data['assessment'].lower(), ''
        )
        score = drug_data['score_total']
        drug_data['score_display'] = str(int(score)) if score == int(score) else f'{score:.2g}'

    drug_rows = sorted(by_drug.values(), key=lambda d: d['name'].lower())
    groups: dict[str, list[dict]] = {}
    for drug_data in drug_rows:
        groups.setdefault(drug_data['drug_class'], []).append(drug_data)

    col_count = (
        2
        + (3 if has_phenotypes else 0)
        + (1 if has_scores else 0)
        + (1 if has_assessment else 0)
    )
    return {
        'rows': drug_rows,
        'groups': groups,
        'has_groups': has_groups,
        'has_phenotypes': has_phenotypes,
        'has_scores': has_scores,
        'has_assessment': has_assessment,
        'assessment_description': assessment_description,
        'col_count': col_count,
    }


def _load_drug_cards(
    project_conn: sqlite3.Connection | None,
    detected_drug_names: set[str] | None = None,
) -> list[dict]:
    """Load drug metadata for detected drugs in this run."""
    if project_conn is None or not detected_drug_names:
        return []

    try:
        rows = project_conn.execute(
            'SELECT name, pubchem_url, description, structure_url '
            'FROM drug ORDER BY name'
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug cards from project DB: %s', exc)
        return []

    cards: list[dict] = []
    for row in rows:
        name = (row['name'] or '').strip()
        if not name:
            continue
        if name.lower() not in detected_drug_names:
            continue
        cards.append({
            'name': name,
            'pubchem_url': row['pubchem_url'] or '',
            'description': row['description'] or '',
            'structure_url': row['structure_url'] or '',
        })

    cards.sort(key=lambda card: (card.get('name') or '').lower())
    return cards


def _load_feature_cards(
    project_conn: sqlite3.Connection | None,
    reference_name: str,
    detected_feature_names: set[str] | None = None,
) -> list[dict]:
    """
    Load feature metadata for detected features in the active reference.

    :param project_conn: optional project DB connection
    :param reference_name: active reference name from profiling result
    :param detected_feature_names: features observed in this profiling run
    :return: list of feature cards
    """
    if project_conn is None or not detected_feature_names:
        return []

    try:
        rows = project_conn.execute(
            'SELECT g.name, g.protein, g.protein_id, g.ncbi_protein_url, g.locus_tag, g.note, '
            'g.nt_sequence, g.aa_sequence FROM feature g '
            'JOIN reference r ON r.id = g.reference_id '
            'WHERE r.name = ? ORDER BY g.start',
            (reference_name,),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug('Failed to load feature cards from project DB for %r: %s', reference_name, exc)
        return []

    cards: list[dict] = []
    for row in rows:
        name = (row['name'] or '').strip()
        if not name or name not in detected_feature_names:
            continue
        cards.append({
            'name': name,
            'protein': row['protein'] or '',
            'protein_id': row['protein_id'] or '',
            'ncbi_protein_url': row['ncbi_protein_url'] or '',
            'locus_tag': row['locus_tag'] or '',
            'note': row['note'] or '',
            'nt_sequence': row['nt_sequence'] or '',
            'aa_sequence': row['aa_sequence'] or '',
        })

    cards.sort(key=lambda card: (card.get('name') or '').lower())
    return cards
