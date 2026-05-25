"""HTML report generation with tabbed layout."""

from __future__ import annotations

import base64
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from jinja2 import BaseLoader, Environment

from respro.db.models import FeatureRecord, ProfilingResult, ResistanceRule

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
    _ = rules

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
        'header': {
            'title': f'Report: {title_source} resistance profile',
            'badge_label': 'Database hits found' if has_database_hit else 'No database hits found',
            'badge_icon': 'tick' if has_database_hit else 'x',
            'badge_class': 'is-hit' if has_database_hit else 'is-no-hit',
            'meta_primary': ' · '.join([part for part in primary_parts if part]),
            'meta_secondary': ' · '.join([part for part in secondary_parts if part]),
            'picture_in_picture_icon': _load_svg_data_url('graph.svg'),
        },
        'tabs': ['Summary', 'Database hits', 'All Mutations', 'Sequence Features', 'Drugs'],
        'sequence_features': {
            'cards': feature_cards,
            'count': len(feature_cards),
            'sequence_icon': _load_svg_data_url('dna.svg'),
        },
        'drugs': {
            'cards': drug_cards,
            'count': len(drug_cards),
            'structure_icon': _load_svg_data_url('structure.svg'),
            'pubchem_icon': _load_svg_data_url('icon-database.svg'),
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
        feature_name = (ann.feature_name or '').strip()
        if not feature_name:
            continue
        if ann.is_resistance_hit:
            direct_counter[feature_name] += 1

    for formula_hit in result.formula_hits:
        for ann in formula_hit.matched_variants:
            feature_name = (ann.feature_name or '').strip()
            if not feature_name:
                continue
            formula_counter[feature_name] += 1

    stats: dict[str, dict] = {}
    # include only features with observed variants
    feature_names = set(observed_counter)
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


def _build_potential_effects_rows(result: ProfilingResult) -> list:
    """
    Build rows for potential effects table.

    Phase 2 stub - not yet implemented.

    :param result: profiling result
    :return: list of effect rows
    """
    # TODO: Phase 2 - implement potential effects rows
    return []


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
