"""HTML report generation using external Jinja template + CSS resources."""

from __future__ import annotations

import base64
import logging
import sqlite3
from pathlib import Path

from jinja2 import Environment, BaseLoader

from respro import __version__
from respro.core.similarity import classify_similarity
from respro.db.models import GeneRecord, ResistanceRule
from respro.report.palette import MUTATION_COLOURS
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)


def _load_template_text() -> str:
    """Load the HTML Jinja template text from the package template file."""
    template_path = Path(__file__).resolve().parent / 'templates' / 'report.html.j2'
    return template_path.read_text(encoding='utf-8')


def _load_css_text() -> str:
    """Load report CSS text from the package static file."""
    css_path = Path(__file__).resolve().parent / 'static' / 'report.css'
    return css_path.read_text(encoding='utf-8')


def _load_js_text() -> str:
    """Load report JavaScript text from the package static file."""
    js_path = Path(__file__).resolve().parent / 'static' / 'report.js'
    return js_path.read_text(encoding='utf-8')


def _load_logo_svg_text() -> str:
    """Load report logo SVG markup from the package static file."""
    logo_path = Path(__file__).resolve().parent / 'static' / 'logo.svg'
    return logo_path.read_text(encoding='utf-8')


def _load_favicon_svg_text() -> str:
    """Load report favicon SVG markup from the package static file."""
    favicon_path = Path(__file__).resolve().parent / 'static' / 'favicon.svg'
    return favicon_path.read_text(encoding='utf-8')


def _phenotype_badge_class(value: str) -> str:
    """Map a phenotype string to a CSS badge class suffix."""
    if value in ('resistant', 'intermediate', 'sensitive'):
        return value if value != 'intermediate' else 'intermediate-p'
    return 'unknown'


def _build_db_hit_rows(result: ProfilingResult) -> list[dict]:
    """
    Build one row per drug per annotated variant that matched a resistance rule.

    :param result: profiling result
    :return: list of dicts for the database hits table
    """
    rows: list[dict] = []
    for ann in result.cds_annotations:
        if not ann.is_resistance_hit:
            continue

        aa_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
        nt_change = f'{ann.variant.ref}{ann.variant.pos + 1}{ann.variant.alt}'

        # One row per drug
        for rule in ann.rule_matches:
            rows.append({
                'gene': ann.gene_name,
                'aa_change': aa_change,
                'consequence': ann.consequence,
                'af_bin': ann.af_bin,
                'nt_change': nt_change,
                'drug': rule.drug_name,
                'ic50': rule.ic50 or '—',
                'phenotype': rule.phenotype,
                'clinical_phenotype': rule.clinical_phenotype,
                'source': rule.source or '—',
                'publication': rule.publication or '',
            })
    return rows


def _build_combo_hit_rows(result: ProfilingResult) -> list[dict]:
    """
    Build rows for combination rule hits.

    :param result: profiling result
    :return: list of dicts for the combo hits table
    """
    rows: list[dict] = []
    for combo in result.combo_hits:
        rs = combo.rule_set
        member_labels = ', '.join(
            f'{m.gene_name}:{m.reference}{m.position + 1}{m.mutation}'
            for m in rs.members
        )
        rows.append({
            'group_name': rs.group_name or '—',
            'members': member_labels,
            'drug': rs.drug_name,
            'ic50': rs.ic50 or '—',
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'phenotype_class': _phenotype_badge_class(rs.phenotype),
            'clinical_class': _phenotype_badge_class(rs.clinical_phenotype),
            'publication': rs.publication or '',
        })
    return rows


def _build_cds_rows(result: ProfilingResult) -> list[dict]:
    """
    Build rows for the all-CDS-variants table.

    :param result: profiling result
    :return: list of dicts for the variant table
    """
    rows: list[dict] = []
    for ann in result.cds_annotations:
        nt_change = f'{ann.variant.ref}{ann.variant.pos + 1}{ann.variant.alt}'
        aa_change = ''
        if ann.ref_aa and ann.alt_aa:
            aa_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'

        rows.append({
            'gene': ann.gene_name,
            'nt_change': nt_change,
            'aa_change': aa_change,
            'consequence': ann.consequence,
            'allele_freq': ann.variant.allele_freq,
            'af_bin': ann.af_bin,
            'database_hit': ann.is_resistance_hit,
        })
    return rows


def _build_potential_effects_rows(
    result: ProfilingResult,
    rules: list[ResistanceRule],
) -> list[dict]:
    """
    Find detected mutations at known resistance positions with a different AA change.

    For missense variants that are not direct DB hits, check if any rule exists
    at the same gene + position and score similarity via BLOSUM62.
    For indels, report if any indel-type rule exists at that position.
    Frameshifts and stop gains are excluded (reported elsewhere).

    :param result: profiling result with annotated variants
    :param rules: all loaded resistance rules
    :return: list of dicts for the potential effects table
    """
    if not rules:
        return []

    # Index rules by (gene_name, position) for position-based lookup
    rules_by_pos: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        rules_by_pos.setdefault((rule.gene_name, rule.position), []).append(rule)

    excluded_consequences = {'frameshift', 'stop_gained', 'synonymous'}
    rows: list[dict] = []
    seen: set[tuple[str, int, str, str]] = set()

    for ann in result.cds_annotations:
        # Skip direct hits, excluded consequences, and variants without AA info
        if ann.is_resistance_hit:
            continue
        if ann.consequence in excluded_consequences:
            continue
        if not ann.gene_name or not ann.alt_aa:
            continue

        pos_key = (ann.gene_name, ann.codon_pos)
        if pos_key not in rules_by_pos:
            continue

        ann_is_indel = ann.consequence in ('insertion', 'deletion') or len(ann.alt_aa) != 1

        for rule in rules_by_pos[pos_key]:
            # Skip wildcard rules (already matched as direct hits)
            if rule.mutation.lower() == 'any':
                continue

            # Indel observations should only be compared to indel-like rule tokens.
            rule_is_indel = rule.mutation.lower() == 'fsx' or any(ch.isdigit() for ch in rule.mutation)
            if ann_is_indel and not rule_is_indel:
                continue

            # Deduplicate by (gene, position, observed_aa, drug)
            dedup_key = (ann.gene_name, ann.codon_pos, ann.alt_aa, rule.drug_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            observed_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            rule_change = f'{rule.reference}{rule.position + 1}{rule.mutation}'

            # For indels: report presence without BLOSUM scoring
            if ann_is_indel:
                similarity = 'moderate'
            else:
                similarity = classify_similarity(ann.alt_aa, rule.mutation)

            rows.append({
                'gene': ann.gene_name,
                'observed_change': observed_change,
                'rule_change': rule_change,
                'similarity': similarity,
                'drug': rule.drug_name,
                'ic50': rule.ic50 or '—',
                'phenotype': rule.phenotype,
                'clinical_phenotype': rule.clinical_phenotype,
                'source': rule.source or '—',
                'allele_freq': ann.variant.allele_freq,
                'publication': rule.publication or '',
            })

    return rows


def _load_drug_cards(
    project_conn: sqlite3.Connection | None,
    detected_drug_names: set[str] | None = None,
) -> list[dict]:
    """
    Load drug metadata for drugs with detected rule hits.

    Structure image URLs are pre-stored in the database and rendered as
    external image references (not embedded as base64).

    :param project_conn: open project database connection (or None)
    :param detected_drug_names: drug names with matched rules (lowercase)
    :return: list of drug info dicts
    """
    if project_conn is None or not detected_drug_names:
        return []
    try:
        rows = project_conn.execute(
            'SELECT name, pubchem_cid, pubchem_url, description, structure_url FROM drug ORDER BY name'
        ).fetchall()
    except Exception:
        return []

    cards: list[dict] = []
    for r in rows:
        if r['name'].lower() not in detected_drug_names:
            continue
        # Show drug details only when we actually have a PubChem hit.
        if not (r['pubchem_cid'] or '').strip():
            continue

        cards.append({
            'name': r['name'],
            'pubchem_url': r['pubchem_url'] or '',
            'description': r['description'] or '',
            'structure_url': r['structure_url'] or '',
        })
    return cards


def _load_gene_cards(
    project_conn: sqlite3.Connection | None,
    reference_name: str,
    detected_gene_names: set[str] | None = None,
) -> list[dict]:
    """
    Load gene metadata for detected genes in the active reference.

    :param project_conn: open project database connection (or None)
    :param reference_name: active reference name from profiling result
    :param detected_gene_names: genes observed in this profiling run
    :return: list of gene info dicts
    """
    if project_conn is None or not detected_gene_names:
        return []

    try:
        rows = project_conn.execute(
            'SELECT g.name, g.protein, g.protein_id, g.ncbi_protein_url, g.locus_tag, g.note, g.aa_sequence '
            'FROM gene g '
            'JOIN reference r ON r.id = g.reference_id '
            'WHERE r.name = ? '
            'ORDER BY g.start',
            (reference_name,),
        ).fetchall()
    except Exception:
        return []

    cards: list[dict] = []
    for row in rows:
        if row['name'] not in detected_gene_names:
            continue
        cards.append({
            'name': row['name'],
            'protein': row['protein'] or '',
            'protein_id': row['protein_id'] or '',
            'ncbi_protein_url': row['ncbi_protein_url'] or '',
            'locus_tag': row['locus_tag'] or '',
            'note': row['note'] or '',
            'aa_sequence': row['aa_sequence'] or '',
        })
    return cards


def build_report_context(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> dict:
    """
    Build the shared report context used by HTML and PDF exports.

    :param result: ProfilingResult object
    :param project_conn: optional project DB connection for overview sections
    :param rules: optional list of resistance rules for potential effects analysis
    :return: dict with summary and section rows
    """
    summary = result.summary_dict()
    summary['database_hits'] = summary.pop('resistance_hits', 0)

    db_hit_rows = _build_db_hit_rows(result)
    combo_hit_rows = _build_combo_hit_rows(result)
    cds_rows = _build_cds_rows(result)
    potential_rows = _build_potential_effects_rows(result, rules or [])
    summary['similarity_hits'] = len(potential_rows)

    detected_drug_names: set[str] = set()
    for ann in result.cds_annotations:
        for rule in ann.rule_matches:
            detected_drug_names.add(rule.drug_name.lower())
    for combo in result.combo_hits:
        detected_drug_names.add(combo.rule_set.drug_name.lower())

    drug_cards = _load_drug_cards(project_conn, detected_drug_names)
    detected_gene_names = {
        ann.gene_name
        for ann in result.cds_annotations
        if ann.gene_name
    }
    gene_cards = _load_gene_cards(project_conn, result.reference_name, detected_gene_names)

    return {
        'summary': summary,
        'db_hit_rows': db_hit_rows,
        'combo_hit_rows': combo_hit_rows,
        'cds_rows': cds_rows,
        'potential_rows': potential_rows,
        'drug_cards': drug_cards,
        'gene_cards': gene_cards,
    }


def render_html(
    result: ProfilingResult,
    genes: list[GeneRecord] | None = None,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> str:
    """
    Render the profiling result to an HTML string.

    :param result: ProfilingResult object
    :param genes: optional list of genes for context
    :param plot_svg_data: optional SVG bytes of the embedded plot
    :param project_conn: optional project DB connection for drug overview
    :param rules: optional list of resistance rules for potential effects analysis
    :return: HTML string
    """

    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_load_template_text())
    css_text = _load_css_text()
    js_text = _load_js_text()
    logo_svg = _load_logo_svg_text()
    favicon_svg = _load_favicon_svg_text()
    favicon_data_uri = 'data:image/svg+xml;base64,' + base64.b64encode(
        favicon_svg.encode('utf-8')
    ).decode('ascii')

    context = build_report_context(result, project_conn=project_conn, rules=rules)

    plot_data = ''
    if plot_svg_data:
        plot_data = base64.b64encode(plot_svg_data).decode('ascii')

    return template.render(
        **context,
        plot_data=plot_data,
        logo_svg=logo_svg,
        favicon_data_uri=favicon_data_uri,
        css=css_text,
        js=js_text,
        mutation_colours=MUTATION_COLOURS,
        version=__version__,
    )


def write_html(
    result: ProfilingResult,
    output_path: Path,
    genes: list[GeneRecord] | None = None,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> Path:
    """
    Render and write the HTML report to a file.

    :param result: ProfilingResult object
    :param output_path: path to write HTML file to
    :param genes: optional list of genes for context
    :param plot_svg_data: optional SVG bytes of the embedded plot
    :param project_conn: optional project DB connection for drug overview
    :param rules: optional list of resistance rules for potential effects analysis
    :return: path to written HTML file
    """
    html = render_html(
        result, genes=genes, plot_svg_data=plot_svg_data,
        project_conn=project_conn, rules=rules,
    )
    output_path = Path(output_path)
    output_path.write_text(html, encoding='utf-8')
    logger.info('HTML report written to %s', output_path)
    return output_path

