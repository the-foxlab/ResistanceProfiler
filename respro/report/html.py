"""HTML report generation with tabbed layout."""

from __future__ import annotations

import base64
import logging
import sqlite3
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


def build_report_context(result: ProfilingResult) -> dict:
    """
    Build all data structures needed to render the report.

    :param result: profiling result to report on
    :return: dictionary of context variables for Jinja2 template
    """
    summary = result.summary_dict()
    resistance_hits = int(summary.get('resistance_hits', 0) or 0)
    has_database_hit = resistance_hits > 0

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

    return {
        'title': f'Report: {title_source} resistance profile',
        'header': {
            'title': f'Report: {title_source} resistance profile',
            'badge_label': 'Database hit' if has_database_hit else 'No database hits',
            'badge_icon': 'tick' if has_database_hit else 'x',
            'badge_class': 'is-hit' if has_database_hit else 'is-no-hit',
            'meta_primary': ' · '.join([part for part in primary_parts if part]),
            'meta_secondary': ' · '.join([part for part in secondary_parts if part]),
            'picture_in_picture_icon': _load_svg_data_url('picture_in_picture.svg'),
        },
        'tabs': ['Summary', 'Database hits', 'All Mutations', 'Sequence Features', 'Drugs'],
    }


def render_html(result: ProfilingResult, plot_svg_data: bytes | None = None) -> str:
    """
    Render the complete HTML report.

    :param result: profiling result to report on
    :param plot_svg_data: optional SVG bytes of the embedded plot
    :return: complete HTML document as string
    """
    plot_data_url = ''
    if plot_svg_data:
        encoded_svg = base64.b64encode(plot_svg_data).decode('ascii')
        plot_data_url = f'data:image/svg+xml;base64,{encoded_svg}'

    context = build_report_context(result)
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
    # TODO: Phase 2 - implement write_html
    html_content = render_html(result, plot_svg_data=plot_svg_data)
    output_path.write_text(html_content, encoding='utf-8')
    return output_path


def _build_potential_effects_rows(result: ProfilingResult) -> list:
    """
    Build rows for potential effects table.

    Phase 2 stub - not yet implemented.

    :param result: profiling result
    :return: list of effect rows
    """
    # TODO: Phase 2 - implement potential effects rows
    return []


def _load_feature_cards(result: ProfilingResult) -> list:
    """
    Load feature cards for the report.

    Phase 2 stub - not yet implemented.

    :param result: profiling result
    :return: list of feature cards
    """
    # TODO: Phase 2 - implement feature cards
    return []
