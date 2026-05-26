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
    return {
        'rows': rows,
        'count': len(rows),
        'has_publications': has_publications,
        'has_drug_class': bool(drug_class_map),
        'has_comments': has_comments,
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
                'badge_color': metadata.get('badge_color', ''),
                'pubchem_url': metadata.get('pubchem_url', ''),
                'description': metadata.get('description', ''),
                'structure_url': metadata.get('structure_url', ''),
                'has_metadata': True,
            })
        else:
            stats.update({
                'badge_color': '',
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
    return {
        'rows': rows,
        'count': len(rows),
        'has_drug_class': has_drug_class,
        'has_publications': has_publications,
        'bibliography': bibliography,
        'info_icon': _load_svg_data_url('info.svg'),
        'search_icon': _load_svg_data_url('search.svg'),
        'reset_icon': _load_svg_data_url('reset_filter.svg'),
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
            'SELECT name, badge_color, pubchem_cid, pubchem_url, description, structure_url '
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
            'badge_color': row['badge_color'] or '',
            'pubchem_url': row['pubchem_url'] or '',
            'description': row['description'] or '',
            'structure_url': row['structure_url'] or '',
            'pubchem_cid': row['pubchem_cid'] or '',
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
