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
from respro.db._rules_formula import _FORMULA_OPERATORS as _LOGIC_OPERATORS, _RE_FORMULA_TOKEN as _RE_LOGIC_TOKEN
from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    GeneRecord,
    ProfilingResult,
    ResistanceRule,
)
from respro.db.results import project_fingerprint
from respro.report.alignment_visualization import (
    GeneAlignment,
    build_alignment_html,
    build_gene_alignments,
)
from respro.report.palette import (
    AF_BIN_COLOURS,
    MUTATION_COLOURS,
    PHENOTYPE_COLOURS,
    SIMILARITY_COLOURS,
    badge_text_colour,
)

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
        return _format_positioned_change(ref, pos, alt)

    # FASTA mode — ref is always the 3-base reference codon
    if ann.consequence == 'frameshift':
        return _format_positioned_change(ref, pos, alt)

    if ann.consequence == 'insertion':
        return _format_positioned_change(ref, pos, alt)

    if ann.consequence == 'deletion':
        # ref = 3-base codon, alt = remaining bases after deletion
        alt_html = f'<u><strong>{escape(alt)}</strong></u>' if alt and alt != '-' else '<u><strong><em>del</em></strong></u>'
        return Markup(f'{escape(ref)}{pos}{alt_html}')

    # SNP / synonymous / stop changes — highlight positions that differ
    if len(ref) == 3 and len(alt) == 3:
        ref_html = str(escape(ref))
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


def _split_change_blocks(ref: str, alt: str) -> tuple[str, str, str, str]:
    """
    Split two strings into shared prefix/suffix and differing cores.

    :param ref: reference token
    :param alt: alternate token
    :return: (prefix, ref_core, alt_core, suffix)
    """
    prefix_len = 0
    max_prefix = min(len(ref), len(alt))
    while prefix_len < max_prefix and ref[prefix_len] == alt[prefix_len]:
        prefix_len += 1

    suffix_len = 0
    max_suffix = min(len(ref) - prefix_len, len(alt) - prefix_len)
    while suffix_len < max_suffix and ref[-(suffix_len + 1)] == alt[-(suffix_len + 1)]:
        suffix_len += 1

    if suffix_len > 0:
        prefix = ref[:prefix_len]
        ref_core = ref[prefix_len:len(ref) - suffix_len]
        alt_core = alt[prefix_len:len(alt) - suffix_len]
        suffix = ref[len(ref) - suffix_len:]
    else:
        prefix = ref[:prefix_len]
        ref_core = ref[prefix_len:]
        alt_core = alt[prefix_len:]
        suffix = ''

    return prefix, ref_core, alt_core, suffix


def _highlight_change_token(ref: str, alt: str) -> tuple[str, str]:
    """
    Highlight changed substring(s) between ref and alt tokens.

    :param ref: reference token
    :param alt: alternate token
    :return: (reference_html_without_highlighting, highlighted_alt_html)
    """
    ref_html = str(escape(ref))
    if ref == alt:
        return ref_html, str(escape(alt))

    prefix, ref_core, alt_core, suffix = _split_change_blocks(ref, alt)

    def _fmt(core: str) -> str:
        if not core:
            return ''
        return f'<u><strong>{escape(core)}</strong></u>'

    if ref_core and not alt_core:
        ref_html = f'{escape(prefix)}{_fmt(ref_core)}{escape(suffix)}'
        alt_html = str(escape(alt))
        return ref_html, alt_html

    alt_html = f'{escape(prefix)}{_fmt(alt_core)}{escape(suffix)}'
    return ref_html, alt_html


def _format_positioned_change(ref: str, pos_1based: int, alt: str) -> Markup:
    """Format one change token as ref{pos}alt with highlighted changed segments."""
    ref_html, alt_html = _highlight_change_token(ref, alt)
    return Markup(f'{ref_html}{pos_1based}{alt_html}')


def _format_aa_change(ann: AnnotatedVariant) -> Markup:
    """
    Format AA change with highlighted changed residue(s).

    :param ann: annotated variant
    :return: Markup-safe AA change string
    """
    if not ann.ref_aa or not ann.alt_aa:
        return Markup('')
    return _format_positioned_change(ann.ref_aa, ann.codon_pos + 1, ann.alt_aa)

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


def _phenotype_badge_class(value: str) -> str:
    """Map a phenotype string to a CSS badge class suffix."""
    if value in ('resistant', 'intermediate', 'sensitive', 'contradictory'):
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

        aa_change = _format_aa_change(ann)
        nt_change = _format_nt_change(ann)
        alignment_html = None
        if ann.gene_name in gene_alignments:
            alignment_html = build_alignment_html(ann, gene_alignments[ann.gene_name])

        for rule in ann.non_formula_component_rule_matches:
            rows.append({
                'gene': ann.gene_name,
                'aa_change': aa_change,
                'consequence': ann.consequence,
                'af_bin': ann.af_bin,
                'nt_change': nt_change,
                'drug': rule.drug_name,
                'ic50': rule.ic50,
                'fold_ic50': rule.fold_ic50,
                'score': rule.score,
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
    return _sort_db_hit_rows(rows)


def _extract_numeric_sort_value(value: str) -> float:
    """Extract the highest numeric token from a free-text quantitative field."""
    matches = re.findall(r'-?\d+(?:\.\d+)?', value or '')
    if not matches:
        return float('-inf')
    return max(float(token) for token in matches)


def _sort_db_hit_rows(rows: list[dict]) -> list[dict]:
    """Sort mutation hits by drug and then by resistance/IC50 relevance."""
    phenotype_order = {
        'resistant': 0,
        'intermediate': 1,
        'sensitive': 2,
        'contradictory': 3,
        'unknown': 4,
    }

    def _key(row: dict) -> tuple:
        phenotype = _effective_phenotype(row)
        phenotype_rank = phenotype_order.get(phenotype, 5)
        # Prefer fold-IC50 where present, then IC50; larger values should rank higher.
        ic50_sort_value = max(
            _extract_numeric_sort_value(str(row.get('fold_ic50', '') or '')),
            _extract_numeric_sort_value(str(row.get('ic50', '') or '')),
        )
        return (
            (row.get('drug') or '').lower(),
            phenotype_rank,
            -ic50_sort_value,
            (row.get('gene') or '').lower(),
            Markup(str(row.get('aa_change', ''))).striptags().lower(),
        )

    return sorted(rows, key=_key)


def _build_combo_hit_rows(result: ProfilingResult) -> list[dict]:
    """
    Build rows for combination rule hits.

    :param result: profiling result
    :return: list of dicts for the combo hits table
    """
    rows: list[dict] = []
    for combo in result.formula_hits:
        rs = combo.rule_set
        member_by_id = {
            member.external_id: f'{member.gene_name}:{member.reference}{member.position + 1}{member.mutation}'
            for member in rs.members
            if member.external_id
        }
        logic_text = rs.logic_expression or ''
        if logic_text and member_by_id:
            member_labels = _render_formula_logic(
                logic_text,
                member_by_id,
                set(combo.matched_member_ids),
            )
        else:
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
            'score': rs.score,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'phenotype_class': _phenotype_badge_class(rs.phenotype),
            'clinical_class': _phenotype_badge_class(rs.clinical_phenotype),
            'comment': rs.comment,
            'publications': rs.publications,
            'pub_citations': [],
        })
    return rows


def _render_formula_logic(
    expression: str,
    members_by_id: dict[str, str],
    matched_member_ids: set[str],
) -> Markup:
    """Render one formula logic expression with mutation labels and highlight classes."""
    rendered: list[str] = []
    last_index = 0
    for match in _RE_LOGIC_TOKEN.finditer(expression):
        rendered.append(str(escape(expression[last_index:match.start()])))
        token = match.group(0)
        upper_token = token.upper()

        if token in {'(', ')'}:
            rendered.append(f"<span class='formula-paren'>{escape(token)}</span>")
        elif upper_token in _LOGIC_OPERATORS:
            rendered.append(f"<span class='formula-operator'>{escape(upper_token.lower())}</span>")
        else:
            label = members_by_id.get(token, token)
            member_class = 'formula-member-detected' if token in matched_member_ids else 'formula-member-missing'
            gene_name, mutation_label = _split_formula_member_label(label)
            rendered.append(
                f"<span class='formula-member {member_class}'>"
                f"<span class='formula-member-gene'>{escape(gene_name)}</span>"
                f"<span class='formula-member-mutation'>{escape(mutation_label)}</span>"
                f"</span>"
            )
        last_index = match.end()

    rendered.append(str(escape(expression[last_index:])))
    return Markup("<div class='formula-logic'>" + ''.join(rendered) + '</div>')


def _split_formula_member_label(label: str) -> tuple[str, str]:
    """Split one formula member display label into gene and mutation chunks."""
    if ':' not in label:
        return label, ''
    gene_name, mutation_label = label.split(':', 1)
    return gene_name, mutation_label


def _build_sample_classification(result: ProfilingResult) -> dict | None:
    """
    Build a single manual sample classification payload.

    :param result: profiling result
    :return: classification dict or None
    """
    if not result.sample_classifications:
        return None

    # Prefer the newest entry in case legacy runs contain multiple rows.
    row = result.sample_classifications[-1]
    return {
        'drug': row.get('drug', ''),
        'phenotype': row.get('phenotype', 'unknown'),
        'clinical_phenotype': row.get('clinical_phenotype', 'unknown'),
        'ic50': row.get('ic50', ''),
        'fold_ic50': row.get('fold_ic50', ''),
        'note': row.get('note', ''),
        'source': row.get('source', ''),
        'created_at': row.get('created_at', ''),
    }


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
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug badge colours from project DB: %s', exc)
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
            aa_change = Markup('?')
            display_consequence = 'complex'
        else:
            aa_change = _format_aa_change(ann)
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

            observed_change = _format_positioned_change(ann.ref_aa, ann.codon_pos + 1, ann.alt_aa)
            rule_change = _format_positioned_change(rule.reference, rule.position + 1, rule.mutation)

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
                'score': rule.score,
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
) -> tuple[list[dict], dict[tuple[str, str, str, str, str], int]]:
    """
    Collect unique publications across all row sets and assign sequential citation numbers.

    Publications appear in order of first encounter (db_hits → formula_hits → potential).

    :param row_sets: one or more row-dict lists to collect publications from
    :return: (ordered bibliography dicts, mapping of normalized publication key → citation number)
    """
    seen: dict[tuple[str, str, str, str, str], int] = {}
    ordered: list[dict] = []
    num = 1
    for rows in row_sets:
        for row in rows:
            for pub in row.get('publications', []):
                key = _publication_identity(pub)
                if key not in seen:
                    seen[key] = num
                    ordered.append({
                        'citation_num': num,
                        'doi': getattr(pub, 'doi', ''),
                        'title': getattr(pub, 'title', ''),
                        'pubmed_id': getattr(pub, 'pubmed_id', ''),
                        'raw_input': getattr(pub, 'raw_input', ''),
                    })
                    num += 1
    return ordered, seen


def _publication_identity(pub) -> tuple[str, str, str, str, str]:
    """Return a stable publication identity key for citation deduplication."""
    pub_id = int(getattr(pub, 'id', 0) or 0)
    if pub_id > 0:
        return ('id', str(pub_id), '', '', '')

    doi = (getattr(pub, 'doi', '') or '').strip().lower()
    pubmed_id = (getattr(pub, 'pubmed_id', '') or '').strip()
    raw_input = (getattr(pub, 'raw_input', '') or '').strip().lower()
    title = (getattr(pub, 'title', '') or '').strip().lower()
    return ('meta', doi, pubmed_id, raw_input, title)


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
    except sqlite3.Error as exc:
        logger.debug('Failed to load drug cards from project DB: %s', exc)
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
    except sqlite3.Error as exc:
        logger.debug('Failed to load gene cards from project DB for %r: %s', reference_name, exc)
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


def _build_summary_text(
    db_hit_rows: list[dict],
    combo_hit_rows: list[dict],
    potential_rows: list[dict],
    summary: dict,
    organism: str = '',
    affected_genes: list[str] | None = None,
    high_impact_genes: list[str] | None = None,
    coverage_gap_genes: list[str] | None = None,
) -> Markup:
    """
    Build a concise, grammatically natural English narrative for the report header.

    The text reads like a clinical interpretation note: it describes resistance evidence
    per drug, phenotype distribution, clinical verification status, quantitative ranges,
    similarity hints, structural-impact warnings, and any unassessed coverage gaps.

    :param db_hit_rows: direct database hit rows
    :param combo_hit_rows: combination rule hit rows
    :param potential_rows: similarity-based potential effect rows
    :param summary: report summary metrics
    :param organism: organism name from the profiling result
    :param affected_genes: gene names with resistance hits
    :param high_impact_genes: gene names that carry at least one high-impact variant
    :param coverage_gap_genes: gene names with coverage gaps
    :return: HTML Markup narrative string
    """
    evidence_rows = [*db_hit_rows, *combo_hit_rows]
    by_drug: dict[str, dict] = {}
    for row in evidence_rows:
        drug = row.get('drug') or 'Unknown'
        entry = by_drug.setdefault(drug, {
            'total': 0,
            'resistant': 0,
            'intermediate': 0,
            'sensitive': 0,
            'unknown': 0,
            'clinical': 0,
            'ranges': {},
        })
        entry['total'] += 1
        phenotype = _effective_phenotype(row)
        if phenotype in ('resistant', 'intermediate', 'sensitive'):
            entry[phenotype] += 1
        else:
            entry['unknown'] += 1
        if row.get('clinical_phenotype', 'unknown') != 'unknown':
            entry['clinical'] += 1

        fold_value = (row.get('fold_ic50') or '').strip()
        ic50_value = (row.get('ic50') or '').strip()
        if fold_value:
            label = f'fold-IC50 {fold_value}'
        elif ic50_value:
            label = f'IC50 {ic50_value}'
        else:
            label = ''
        if label:
            entry['ranges'][label] = entry['ranges'].get(label, 0) + 1

    sentences: list[str] = []

    if by_drug:
        drug_names = sorted(by_drug)
        n_drugs = len(drug_names)
        n_hits = len(evidence_rows)

        # Opening sentence — qualifier depends on the overall phenotype picture.
        total_resistant = sum(s['resistant'] for s in by_drug.values())
        total_intermediate = sum(s['intermediate'] for s in by_drug.values())
        total_sensitive = sum(s['sensitive'] for s in by_drug.values())

        drug_list = _join_english_list(drug_names)
        hit_noun = 'database hit' if n_hits == 1 else 'database hits'
        drug_noun = 'drug' if n_drugs == 1 else 'drugs'

        # Build opening sentence: "Resistance-associated mutations in gene UL23 of Human alphaherpesvirus 1 were detected ..."
        if total_resistant or total_intermediate:
            subject = 'Resistance-associated mutations'
        elif total_sensitive:
            subject = 'Sensitivity-associated mutations'
        else:
            subject = 'Mutations with no resistance phenotype classification'

        location_parts: list[str] = []
        if affected_genes:
            gene_list = _join_english_list([f'<em>{escape(g.upper())}</em>' for g in sorted(affected_genes)])
            location_parts.append(f'in {gene_list}')
        if organism:
            location_parts.append(f'of {escape(organism)}')
        location_clause = (' ' + ' '.join(location_parts)) if location_parts else ''

        sentences.append(
            f"{subject}{location_clause} were detected "
            f"for {n_drugs} {drug_noun} ({drug_list}), with {n_hits} {hit_noun} in total."
        )

        # Per-drug sentences — split into two short sentences to avoid chained relative clauses.
        for drug_name in drug_names:
            stats = by_drug[drug_name]
            n = stats['total']
            pheno = _phenotype_sentence(stats)
            range_note = _range_sentence(stats['ranges'])

            sentence = f"For {drug_name}, {n} database {'hit was' if n == 1 else 'hits were'} identified"
            if pheno:
                sentence += f", including {pheno}"
            sentence += '.'
            sentences.append(sentence)

            if stats['clinical'] or range_note:
                if stats['clinical'] and range_note:
                    c = stats['clinical']
                    followup = (
                        f"For {drug_name}, clinical phenotype data are available for "
                        f"{c} of {'this hit' if n == 1 else 'these hits'} ({range_note})."
                    )
                elif stats['clinical']:
                    c = stats['clinical']
                    followup = (
                        f"For {drug_name}, clinical phenotype data are available for "
                        f"{c} of {'this hit' if n == 1 else 'these hits'}."
                    )
                else:
                    followup = f"Reported quantitative data for {drug_name}: {range_note}."
                sentences.append(followup)
    else:
        sentences.append(
            'No resistance mutations matching database entries were identified in this sample.'
        )

    high_sim = sum(1 for row in potential_rows if row.get('similarity') == 'high')
    moderate_sim = sum(1 for row in potential_rows if row.get('similarity') == 'moderate')
    similar_total = high_sim + moderate_sim
    if similar_total:
        sim_parts: list[str] = []
        if high_sim:
            sim_parts.append(f"{high_sim} with high amino acid similarity")
        if moderate_sim:
            sim_parts.append(f"{moderate_sim} with moderate amino acid similarity")
        sentences.append(
            f"In addition, {similar_total} substitution{'s' if similar_total != 1 else ''} "
            f"at known resistance positions {'were' if similar_total != 1 else 'was'} detected "
            f"that do not exactly match a database entry but show biochemical similarity "
            f"to a known resistance mutation ({_join_english_list(sim_parts)}). "
            f"Further evaluation of {'these variants' if similar_total != 1 else 'this variant'} is recommended."
        )

    high_impact_count = int(summary.get('high_impact_count', 0) or 0)
    if high_impact_count:
        n = high_impact_count
        # Build a list of which types were actually observed and how many.
        _consequence_labels = {
            'frameshift': 'frameshift',
            'stop_gained': 'premature stop',
            'stop_lost': 'stop loss',
            'start_lost': 'start loss',
            'insertion': 'in-frame insertion',
            'deletion': 'in-frame deletion',
        }
        by_consequence = summary.get('high_impact_by_consequence', {})
        type_parts = [
            f"{cnt} {_consequence_labels[c]}{'s' if cnt != 1 else ''}"
            for c in _consequence_labels
            if (cnt := by_consequence.get(c, 0))
        ]
        type_list = _join_english_list(type_parts) if type_parts else 'high-impact'
        gene_clause = ''
        if high_impact_genes:
            hi_gene_list = _join_english_list([f'<em>{escape(g.upper())}</em>' for g in sorted(high_impact_genes)])
            gene_clause = f' in {hi_gene_list}'
        sentences.append(
            f"Moreover, {'one' if n == 1 else n} high-impact variant{'s were' if n != 1 else ' was'} "
            f"identified{gene_clause} ({type_list}) that may disrupt protein structure or function "
            f"and should be interpreted with caution."
        )

    if coverage_gap_genes:
        gene_list = _join_english_list([f'<em>{escape(g.upper())}</em>' for g in sorted(coverage_gap_genes)])
        n_genes = len(coverage_gap_genes)
        part = 'part' if n_genes == 1 else 'parts'
        sentences.append(
            f"Due to coverage gaps, {part} of {gene_list} could not be fully assessed for resistance mutations."
        )

    return Markup(' '.join(sentences))


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
    project_uuid = ''
    if project_conn is not None:
        try:
            project_uuid = project_fingerprint(project_conn)
        except (ValueError, sqlite3.Error) as exc:
            logger.debug('Could not resolve project UUID for report context: %s', exc)
            project_uuid = ''
    summary['project_uuid'] = project_uuid

    gene_alignments = build_gene_alignments(result.query_sequence, result.gene_matches)

    db_hit_rows = _build_db_hit_rows(result, gene_alignments)
    summary['db_hit_rules'] = len(db_hit_rows)
    combo_hit_rows = _build_combo_hit_rows(result)
    summary['combination_rule_hits'] = len(combo_hit_rows)

    summary['single_hit_positions'] = int(summary['database_hits'])
    summary['single_hit_rules'] = len(db_hit_rows)
    summary['single_total_hits'] = summary['single_hit_rules']
    summary['single_hit_drugs'] = len({(row.get('drug') or '').lower() for row in db_hit_rows})

    summary['combo_hit_positions'] = len({
        (variant.gene_name, variant.codon_pos)
        for hit in result.formula_hits
        for variant in hit.matched_variants
    })
    summary['combo_hit_rules'] = len(combo_hit_rows)
    summary['combo_total_hits'] = summary['combo_hit_rules']
    summary['combo_hit_drugs'] = len({(row.get('drug') or '').lower() for row in combo_hit_rows})
    sample_classification = _build_sample_classification(result)
    cds_rows = _build_cds_rows(result, gene_alignments)
    potential_rows = _build_potential_effects_rows(result, rules or [], gene_alignments)
    coverage_assessment_available = bool(rules) or bool(result.coverage_gaps) or any(
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

    summary['combo_resistant_hits'] = sum(1 for r in combo_hit_rows if _effective_phenotype(r) == 'resistant')
    summary['combo_intermediate_hits'] = sum(1 for r in combo_hit_rows if _effective_phenotype(r) == 'intermediate')
    summary['combo_sensitive_hits'] = sum(1 for r in combo_hit_rows if _effective_phenotype(r) == 'sensitive')

    summary['similarity_resistant'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'resistant')
    summary['similarity_intermediate'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'intermediate')
    summary['similarity_sensitive'] = sum(1 for r in potential_rows if _effective_phenotype(r) == 'sensitive')

    _high_impact = {'frameshift', 'stop_gained', 'stop_lost', 'start_lost', 'insertion', 'deletion'}
    summary['high_impact_count'] = sum(
        1 for ann in result.cds_annotations if ann.consequence in _high_impact
    )
    summary['high_impact_by_consequence'] = {
        c: sum(1 for ann in result.cds_annotations if ann.consequence == c)
        for c in _high_impact
    }
    summary['missense_count'] = sum(
        1 for ann in result.cds_annotations if ann.consequence == 'missense'
    )
    summary['rule_positions_total'] = total_rule_positions
    summary['unassessed_rule_positions'] = unassessed_rule_positions

    bibliography, pub_to_num = _build_bibliography(db_hit_rows, combo_hit_rows, potential_rows)
    for row in [*db_hit_rows, *combo_hit_rows, *potential_rows]:
        row_citations: list[int] = []
        seen_citations: set[int] = set()
        for pub in row.get('publications', []):
            citation_num = pub_to_num.get(_publication_identity(pub))
            if citation_num is None or citation_num in seen_citations:
                continue
            seen_citations.add(citation_num)
            row_citations.append(citation_num)
        row['pub_citations'] = row_citations

    _optional_cols = ['ic50', 'fold_ic50', 'score', 'clinical_phenotype', 'source', 'comment', 'publication']
    db_cols = _col_visibility(db_hit_rows, _optional_cols)
    combo_cols = _col_visibility(combo_hit_rows, ['ic50', 'fold_ic50', 'score', 'clinical_phenotype', 'comment', 'publication'])
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
        for rule in ann.non_formula_component_rule_matches:
            detected_drug_names.add(rule.drug_name.lower())
    for combo in result.formula_hits:
        if combo.rule_set.drug_name:
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

    # Genes that carry at least one direct resistance hit.
    hit_gene_names = {
        ann.gene_name
        for ann in result.cds_annotations
        if ann.is_resistance_hit and ann.gene_name
    }
    high_impact_gene_names = {
        ann.gene_name
        for ann in result.cds_annotations
        if ann.consequence in _high_impact and ann.gene_name
    }
    coverage_gap_gene_names = {
        gap.gene_name
        for gap in result.coverage_gaps
    }
    summary_text = _build_summary_text(
        db_hit_rows, combo_hit_rows, potential_rows, summary,
        organism=summary.get('organism') or '',
        affected_genes=sorted(hit_gene_names),
        high_impact_genes=sorted(high_impact_gene_names),
        coverage_gap_genes=sorted(coverage_gap_gene_names),
    )

    return {
        'summary': summary,
        'summary_text_en': summary_text,
        'sample_classification': sample_classification,
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

    context = build_report_context(result, project_conn=project_conn, rules=rules)

    plot_data = ''
    if plot_svg_data:
        plot_data = base64.b64encode(plot_svg_data).decode('ascii')

    return template.render(
        **context,
        plot_data=plot_data,
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


