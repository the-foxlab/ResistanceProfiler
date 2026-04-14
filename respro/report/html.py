"""HTML report generation using external Jinja template + CSS resources."""

from __future__ import annotations

import base64
import logging
import re
import sqlite3
from pathlib import Path

from jinja2 import BaseLoader, Environment
from markupsafe import Markup, escape

from respro import __version__
from respro.core.annotation import classify_similarity
from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    GeneRecord,
    ProfilingResult,
    ResistanceRule,
)
from respro.report.palette import (
    AF_BIN_COLOURS,
    MUTATION_COLOURS,
    PHENOTYPE_COLOURS,
    SIMILARITY_COLOURS,
    badge_text_colour,
)
from respro.report.alignment_visualization import GeneAlignment, build_alignment_html, build_gene_alignments
from respro.report.plots import render_lollipop_plot_bytes

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# NT change formatting helpers
# ──────────────────────────────────────────────────────────────────────

def _format_nt_change(ann: AnnotatedVariant) -> Markup:
    """
    Format the nucleotide change for HTML display.

    In FASTA mode, both ref and alt are codon-level (3 bases). Changed positions
    within SNP codons are highlighted with bold+underline. Insertions display
    as anchor_codon{pos}anchor_codon<u><strong>inserted</strong></u>.

    :param ann: annotated variant
    :return: Markup-safe HTML string for the NT change cell
    """
    ref = ann.variant.ref
    alt = ann.variant.alt
    pos = ann.variant.pos + 1  # 1-based for display

    if not ann.is_fasta_mode:
        return Markup(f'{escape(ref)}{pos}{escape(alt)}')

    # FASTA mode — ref is always the 3-base reference codon
    if ann.consequence == 'frameshift':
        return Markup(f'{escape(ref)}{pos}fsX')

    if ann.consequence == 'insertion':
        # alt = ref_codon + inserted (anchor style set in profile_fasta.py)
        anchor = str(escape(ref))
        inserted = str(escape(alt[len(ref):] if len(alt) > len(ref) else alt))
        return Markup(f'{anchor}{pos}{anchor}<u><strong>{inserted}</strong></u>')

    if ann.consequence == 'deletion':
        # ref = 3-base codon, alt = remaining bases after deletion
        alt_html = str(escape(alt)) if alt and alt != '-' else '<em>del</em>'
        return Markup(f'{escape(ref)}{pos}{alt_html}')

    # SNP / synonymous / stop changes — highlight positions that differ
    if len(ref) == 3 and len(alt) == 3:
        ref_html = _highlight_codon_diff(ref, alt)
        alt_html = _highlight_codon_diff(alt, ref)
        return Markup(f'{ref_html}{pos}{alt_html}')

    return Markup(f'{escape(ref)}{pos}{escape(alt)}')


def _highlight_codon_diff(seq: str, other: str) -> str:
    """
    Render a 3-base codon as HTML, bold+underlining positions that differ from other.

    :param seq: codon to render
    :param other: reference codon to compare against
    :return: HTML string (not Markup-wrapped; safe to embed in Markup context)
    """
    parts = []
    for i, base in enumerate(seq):
        if i < len(other) and base != other[i]:
            parts.append(f'<u><strong>{escape(base)}</strong></u>')
        else:
            parts.append(str(escape(base)))
    return ''.join(parts)

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


def _effective_phenotype(row: dict) -> str:
    """
    Return the single effective phenotype for a hit row.

    Prefers ``phenotype`` when it carries a meaningful value; falls back to
    ``clinical_phenotype`` otherwise. This ensures each row is counted in
    exactly one phenotype bucket.

    :param row: a db_hit or potential_effects row dict
    :return: one of 'resistant', 'intermediate', 'sensitive', or 'unknown'
    """
    p = row.get('phenotype', '')
    return p if p and p != 'unknown' else row.get('clinical_phenotype', 'unknown')


def _alignment_title(ann: AnnotatedVariant) -> str:
    """Return report label for row-level alignment visualization."""
    return 'Alignment' if ann.is_fasta_mode else 'Pseudo alignment'


def _build_db_hit_rows(
    result: ProfilingResult,
    gene_alignments: dict[str, GeneAlignment],
) -> list[dict]:
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
        nt_change = _format_nt_change(ann)
        alignment_html = None
        if ann.gene_name in gene_alignments:
            alignment_html = build_alignment_html(ann, gene_alignments[ann.gene_name])

        for rule in ann.rule_matches:
            rows.append({
                'gene': ann.gene_name,
                'aa_change': aa_change,
                'consequence': ann.consequence,
                'af_bin': ann.af_bin,
                'nt_change': nt_change,
                'drug': rule.drug_name,
                'ic50': rule.ic50,
                'fold_ic50': rule.fold_ic50,
                'phenotype': rule.phenotype,
                'clinical_phenotype': rule.clinical_phenotype,
                'source': rule.source,
                'comment': rule.comment,
                'publications': rule.publications,
                'pub_citations': [],
                'alignment_html': alignment_html,
                'has_alignment': alignment_html is not None,
                'alignment_title': _alignment_title(ann),
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
            'ic50': rs.ic50,
            'fold_ic50': rs.fold_ic50,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'phenotype_class': _phenotype_badge_class(rs.phenotype),
            'clinical_class': _phenotype_badge_class(rs.clinical_phenotype),
            'comment': rs.comment,
            'publications': rs.publications,
            'pub_citations': [],
        })
    return rows


def _load_drug_badge_colours(
    project_conn: sqlite3.Connection | None,
    detected_drug_names: set[str],
) -> dict[str, str]:
    """Load persisted drug badge colours from the project DB for detected drugs."""
    if project_conn is None or not detected_drug_names:
        return {}

    try:
        rows = project_conn.execute(
            'SELECT name, badge_color FROM drug ORDER BY name'
        ).fetchall()
    except Exception:
        return {}

    colours: dict[str, str] = {}
    for row in rows:
        name = (row['name'] or '').lower()
        colour = (row['badge_color'] or '').strip().lower()
        if name in detected_drug_names and re.fullmatch(r'#[0-9a-f]{6}', colour):
            colours[name] = colour
    return colours


def _attach_drug_badges(rows: list[dict], drug_colours: dict[str, str]) -> None:
    """Attach per-row badge colors for drug labels in report tables."""
    fallback_colour = '#475569'
    for row in rows:
        key = (row.get('drug') or '').lower()
        bg = drug_colours.get(key, fallback_colour)
        row['drug_badge_bg'] = bg
        row['drug_badge_fg'] = badge_text_colour(bg)


def _build_cds_rows(
    result: ProfilingResult,
    gene_alignments: dict[str, GeneAlignment],
) -> list[dict]:
    """
    Build rows for the all-CDS-variants table.

    :param result: profiling result
    :return: list of dicts for the variant table
    """
    rows: list[dict] = []
    for ann in result.cds_annotations:
        nt_change = _format_nt_change(ann)
        alignment_html = None
        if ann.gene_name in gene_alignments:
            alignment_html = build_alignment_html(ann, gene_alignments[ann.gene_name])
        if ann.consequence == 'inframe_complex':
            aa_change = '?'
            display_consequence = 'complex'
        else:
            aa_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}' if ann.ref_aa and ann.alt_aa else ''
            display_consequence = ann.consequence

        rows.append({
            'gene': ann.gene_name,
            'nt_change': nt_change,
            'aa_change': aa_change,
            'consequence': display_consequence,
            'allele_freq': ann.variant.allele_freq,
            'af_bin': ann.af_bin,
            'database_hit': ann.is_resistance_hit,
            'alignment_html': alignment_html,
            'has_alignment': alignment_html is not None,
            'alignment_title': _alignment_title(ann),
        })
    return rows


def _build_potential_effects_rows(
    result: ProfilingResult,
    rules: list[ResistanceRule],
    gene_alignments: dict[str, GeneAlignment] | None = None,
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

    if gene_alignments is None:
        gene_alignments = {}

    # Index rules by (gene_name, position) for position-based lookup
    rules_by_pos: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        rules_by_pos.setdefault((rule.gene_name, rule.position), []).append(rule)

    excluded_consequences = {'frameshift', 'stop_gained', 'synonymous', 'inframe_complex'}
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

            potential_alignment = (
                build_alignment_html(ann, gene_alignments[ann.gene_name])
                if ann.gene_name in gene_alignments else None
            )

            rows.append({
                'gene': ann.gene_name,
                'codon_pos': ann.codon_pos,
                'observed_change': observed_change,
                'rule_change': rule_change,
                'similarity': similarity,
                'drug': rule.drug_name,
                'ic50': rule.ic50,
                'fold_ic50': rule.fold_ic50,
                'phenotype': rule.phenotype,
                'clinical_phenotype': rule.clinical_phenotype,
                'source': rule.source,
                'comment': rule.comment,
                'allele_freq': ann.variant.allele_freq,
                'publications': rule.publications,
                'pub_citations': [],
                'alignment_html': potential_alignment,
                'has_alignment': potential_alignment is not None,
                'alignment_title': _alignment_title(ann),
            })

    return rows


def _count_unassessed_rule_positions(
    rules: list[ResistanceRule],
    coverage_gaps: list[CoverageGap],
) -> tuple[int, int]:
    """
    Count unique rule positions and how many are not assessable due to missing coverage.

    :param rules: loaded resistance rules for the active reference
    :param coverage_gaps: non-covered codon stretches emitted by the profiler
    :return: (total unique rule positions, unassessed unique rule positions)
    """
    rule_positions = {(rule.gene_name, rule.position) for rule in rules}
    if not rule_positions:
        return 0, 0

    gene_gaps: dict[str, list[CoverageGap]] = {}
    for gap in coverage_gaps:
        gene_gaps.setdefault(gap.gene_name, []).append(gap)

    unassessed_total = sum(
        1 for gene, pos in rule_positions if any(
            gap.codon_start <= pos <= gap.codon_end for gap in gene_gaps.get(gene, [])
        )
    )
    return len(rule_positions), unassessed_total


def _col_visibility(rows: list[dict], columns: list[str]) -> dict[str, bool]:
    """
    Return a visibility flag for each column — True when at least one row has a meaningful value.

    For 'clinical_phenotype', meaningful means not 'unknown'.
    For 'publication', checks 'pub_citations'. For all others, any truthy value counts.

    :param rows: list of row dicts
    :param columns: column names to check
    :return: dict mapping column name to bool
    """
    result: dict[str, bool] = {}
    for col in columns:
        if col == 'clinical_phenotype':
            result[col] = any(r.get(col, 'unknown') != 'unknown' for r in rows)
        elif col == 'publication':
            result[col] = any(r.get('pub_citations') for r in rows)
        else:
            result[col] = any(bool(r.get(col)) for r in rows)
    return result


def _build_bibliography(
    *row_sets: list[dict],
) -> tuple[list[dict], dict[int, int]]:
    """
    Collect unique publications across all row sets and assign sequential citation numbers.

    Publications appear in order of first encounter (db_hits → combo_hits → potential).

    :param row_sets: one or more row-dict lists to collect publications from
    :return: (ordered bibliography dicts, mapping of publication id → citation number)
    """
    seen: dict[int, int] = {}  # pub.id → citation number
    ordered: list[dict] = []
    num = 1
    for rows in row_sets:
        for row in rows:
            for pub in row.get('publications', []):
                if pub.id not in seen:
                    seen[pub.id] = num
                    ordered.append({
                        'citation_num': num,
                        'doi': pub.doi,
                        'title': pub.title,
                        'pubmed_id': pub.pubmed_id,
                        'raw_input': pub.raw_input,
                    })
                    num += 1
    return ordered, seen


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
            'SELECT name, badge_color, pubchem_cid, pubchem_url, description, structure_url '
            'FROM drug ORDER BY name'
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
            'badge_color': r['badge_color'] or '',
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

    gene_alignments = build_gene_alignments(result.query_sequence, result.gene_matches)

    db_hit_rows = _build_db_hit_rows(result, gene_alignments)
    summary['db_hit_rules'] = len(db_hit_rows)
    combo_hit_rows = _build_combo_hit_rows(result)
    cds_rows = _build_cds_rows(result, gene_alignments)
    potential_rows = _build_potential_effects_rows(result, rules or [], gene_alignments)
    coverage_assessment_available = bool(result.coverage_gaps) or any(
        ann.is_fasta_mode for ann in result.annotations
    )
    if coverage_assessment_available:
        total_rule_positions, unassessed_rule_positions = _count_unassessed_rule_positions(
            rules or [], result.coverage_gaps,
        )
    else:
        total_rule_positions = 0
        unassessed_rule_positions = 0
    summary['similarity_hits'] = len({(r['gene'], r['codon_pos']) for r in potential_rows})
    summary['similarity_rules'] = len({(r['gene'], r['observed_change']) for r in potential_rows})

    summary['resistant_hits'] = sum(1 for r in db_hit_rows if _effective_phenotype(r) == 'resistant')
    summary['intermediate_hits'] = sum(1 for r in db_hit_rows if _effective_phenotype(r) == 'intermediate')
    summary['sensitive_hits'] = sum(1 for r in db_hit_rows if _effective_phenotype(r) == 'sensitive')

    summary['similarity_resistant'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'resistant')
    summary['similarity_intermediate'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'intermediate')
    summary['similarity_sensitive'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'sensitive')

    _high_impact = {'frameshift', 'stop_gained', 'stop_lost', 'start_lost', 'insertion', 'deletion'}
    summary['high_impact_count'] = sum(
        1 for ann in result.cds_annotations if ann.consequence in _high_impact
    )
    summary['missense_count'] = sum(
        1 for ann in result.cds_annotations if ann.consequence == 'missense'
    )
    summary['rule_positions_total'] = total_rule_positions
    summary['unassessed_rule_positions'] = unassessed_rule_positions

    bibliography, pub_id_to_num = _build_bibliography(db_hit_rows, combo_hit_rows, potential_rows)
    for row in [*db_hit_rows, *combo_hit_rows, *potential_rows]:
        row['pub_citations'] = [
            pub_id_to_num[pub.id]
            for pub in row.get('publications', [])
            if pub.id in pub_id_to_num
        ]

    _optional_cols = ['ic50', 'fold_ic50', 'clinical_phenotype', 'source', 'comment', 'publication']
    db_cols = _col_visibility(db_hit_rows, _optional_cols)
    combo_cols = _col_visibility(combo_hit_rows, ['ic50', 'fold_ic50', 'clinical_phenotype', 'comment', 'publication'])
    pot_cols = _col_visibility(potential_rows, _optional_cols)

    # Unify clinical_phenotype visibility across all hit sections: if any section has
    # meaningful values, all sections show the column so the report is consistent.
    any_clinical = (
        db_cols['clinical_phenotype']
        or combo_cols.get('clinical_phenotype', False)
        or pot_cols['clinical_phenotype']
    )
    db_cols['clinical_phenotype'] = any_clinical
    combo_cols['clinical_phenotype'] = any_clinical
    pot_cols['clinical_phenotype'] = any_clinical

    detected_drug_names: set[str] = set()
    for ann in result.cds_annotations:
        for rule in ann.rule_matches:
            detected_drug_names.add(rule.drug_name.lower())
    for combo in result.combo_hits:
        detected_drug_names.add(combo.rule_set.drug_name.lower())

    drug_colours = _load_drug_badge_colours(project_conn, detected_drug_names)
    _attach_drug_badges(db_hit_rows, drug_colours)
    _attach_drug_badges(combo_hit_rows, drug_colours)
    _attach_drug_badges(potential_rows, drug_colours)

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
        'db_cols': db_cols,
        'combo_cols': combo_cols,
        'pot_cols': pot_cols,
        'bibliography': bibliography,
    }


def render_html(
    result: ProfilingResult,
    plot_svg_data: bytes | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> str:
    """
    Render the profiling result to an HTML string.

    :param result: ProfilingResult object
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
        phenotype_colours=PHENOTYPE_COLOURS,
        af_bin_colours=AF_BIN_COLOURS,
        similarity_colours=SIMILARITY_COLOURS,
        badge_text_colour=badge_text_colour,
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
        result, plot_svg_data=plot_svg_data,
        project_conn=project_conn, rules=rules,
    )
    output_path = Path(output_path)
    output_path.write_text(html, encoding='utf-8')
    logger.info('HTML report written to %s', output_path)
    return output_path


def _build_output_stem(result: ProfilingResult) -> str:
    """Return a safe basename derived from the profiled VCF/FASTA filename."""
    raw_stem = Path(result.vcf_name).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem)
    return safe_stem or 'profile'


def export_results(
    result: ProfilingResult,
    output_dir: Path,
    genes: list[GeneRecord] | None = None,
    rule_gene_names: set[str] | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> dict[str, Path]:
    """
    Write all report outputs to a directory and return a format-to-path mapping.

    :param result: ProfilingResult object
    :param output_dir: directory to write outputs to
    :param genes: optional list of genes for plotting
    :param rule_gene_names: optional set of rule-backed gene names for focused plotting
    :param project_conn: optional project DB connection for drug overview in HTML
    :param rules: optional resistance rules for potential effects table in HTML
    :return: dict mapping format names to output file paths
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _build_output_stem(result)

    plot_svg_data: bytes | None = None
    if genes:
        plot_svg_data = render_lollipop_plot_bytes(
            result,
            genes,
            fmt='svg',
            rule_gene_names=rule_gene_names,
        )

    html_path = output_dir / f'{stem}.report.html'
    write_html(
        result,
        html_path,
        genes=genes,
        plot_svg_data=plot_svg_data,
        project_conn=project_conn,
        rules=rules,
    )

    outputs: dict[str, Path] = {'html': html_path}
    logger.info('Exported %d format(s) to %s', len(outputs), output_dir)
    return outputs


