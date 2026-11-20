"""Non-HTML report exports (JSON, TSV, PDF) and export orchestration."""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from jinja2 import BaseLoader, Environment

try:
    from weasyprint import HTML
except ImportError:
    HTML = None

from respro import __version__
from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.phenotype_ranks import label_to_rank
from respro.db.models import (
    AnnotatedVariant,
    FeatureRecord,
    ProfilingResult,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
)
from respro.db.results import (
    project_fingerprint,
    project_updated_at,
    serialize_combined_states,
    serialize_haplotype_links,
)
from respro.report.html.main import build_report_context, write_html
from respro.report.plots import render_lollipop_plot_bytes
from respro.report.row_helpers import (
    build_feature_display_names,
    build_reference_name_by_chrom,
    display_consequence,
    format_aa_effects,
    nt_change_stored,
    nt_change_user,
    select_effect_as_resistant_rules,
    variant_source_label,
)

logger = logging.getLogger(__name__)

_REPORT_DIR = Path(__file__).resolve().parent

TSV_COLUMNS: tuple[str, ...] = (
    'reference', 'gene', 'nt_mut', 'nt_mut_user', 'strand', 'variant_source',
    'aa_frequency', 'depth', 'consequence', 'aa_effects',
    'in_database', 'rule_type',
    'drug', 'phenotype', 'clinical_phenotype', 'ic50', 'fold_ic50', 'score',
    'source', 'publications',
)

_TSV_PLACEHOLDER = 'n/a'
_TSV_VCF_ONLY_COLUMNS: frozenset[str] = frozenset({'reference', 'nt_mut_user', 'depth', 'variant_source'})
_TSV_CONDITIONAL_COLUMNS: frozenset[str] = frozenset({
    'phenotype', 'clinical_phenotype', 'ic50', 'fold_ic50', 'score', 'publications',
})
_TSV_UNKNOWN_IS_ABSENT_COLUMNS: frozenset[str] = frozenset({'phenotype', 'clinical_phenotype'})

_PDF_VALID_BADGE_CLASSES: frozenset[str] = frozenset({
    'is-susceptible', 'is-potential', 'is-low-level',
    'is-intermediate', 'is-resistant', 'is-unknown', 'is-contradictory',
})
_PDF_BADGE_BY_RANK: dict[int, str] = {
    1: 'is-susceptible',
    2: 'is-potential',
    3: 'is-low-level',
    4: 'is-intermediate',
    5: 'is-resistant',
    0: 'is-unknown',
    -1: 'is-contradictory',
}


def export_results(
    result: ProfilingResult,
    output_dir: Path,
    features: list[FeatureRecord] | None = None,
    rule_feature_names: set[str] | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    extra_export_formats: set[str] | None = None,
    project_db_path: Path | None = None,
    output_html_path: Path | None = None,
    similarity_high: int = 1,
    similarity_moderate: int = 0,
    cfg: CliConfig = CLI_CONFIG,
) -> dict[str, Path]:
    """Write HTML and any requested extra outputs (JSON, PDF, TSV), returning format -> path."""
    if output_html_path is None:
        raise ValueError('output_html_path is required for export_results().')

    html_path = Path(output_html_path)
    output_dir = html_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    if html_path.name.endswith('.report.html'):
        stem = html_path.name[:-12]
    elif html_path.suffix == '.html':
        stem = html_path.stem
    else:
        stem = html_path.name

    requested_formats = set(extra_export_formats or set())
    if unknown_formats := requested_formats - {'json', 'pdf', 'tsv'}:
        raise ValueError(f'Unsupported export format(s): {", ".join(sorted(unknown_formats))}')

    plot_svg_data = (
        render_lollipop_plot_bytes(result, features, fmt='svg', rule_feature_names=rule_feature_names)
        if features
        else None
    )

    # Build report context once so HTML and PDF share identical data without recomputing.
    report_context = build_report_context(
        result,
        project_conn=project_conn,
        rules=rules,
        features=features,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        cfg=cfg,
    )

    write_html(
        result,
        html_path,
        features=features,
        plot_svg_data=plot_svg_data,
        project_conn=project_conn,
        rules=rules,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        cfg=cfg,
        context=report_context,
    )
    outputs: dict[str, Path] = {'html': html_path}

    if 'json' in requested_formats:
        outputs['json'] = write_json(
            result, output_dir / f'{stem}.results.json',
            project_conn=project_conn, project_db_path=project_db_path,
        )

    if 'pdf' in requested_formats:
        outputs['pdf'] = write_pdf(
            result,
            output_dir / f'{stem}.report.pdf',
            project_conn=project_conn,
            rules=rules,
            context=report_context,
            plot_svg_data=plot_svg_data,
            features=features,
            similarity_high=similarity_high,
            similarity_moderate=similarity_moderate,
            cfg=cfg,
        )

    if 'tsv' in requested_formats:
        outputs['tsv'] = write_tsv(
            result,
            output_dir / f'{stem}.results.tsv',
            project_conn=project_conn,
            display_names=build_feature_display_names(features),
            reference_name_by_chrom=build_reference_name_by_chrom(result),
        )

    logger.info('Exported report to %s', html_path)
    return outputs


def write_json(
    result: ProfilingResult,
    output_path: Path,
    project_conn: sqlite3.Connection | None = None,
    project_db_path: Path | None = None,
) -> Path:
    """Write a single-run JSON document mirroring the ``results.db`` schema."""
    ref_by_chrom = build_reference_name_by_chrom(result)

    run_payload = {
        'project_name': result.project_name,
        'project_db_path': str(project_db_path) if project_db_path else '',
        'project_fingerprint': project_fingerprint(project_conn) if project_conn is not None else '',
        'project_updated_at': project_updated_at(project_conn) if project_conn is not None else '',
        'reference_name': result.reference_name,
        'sample_name': result.sample_name,
        'vcf_path': result.vcf_name,
        'total_variants': result.total_variants,
        'variants_in_cds': result.variants_in_cds,
        'resistance_hits': result.resistance_hits,
        'formula_hits': len(result.formula_hits),
        'status': 'complete',
        'created_at': result.run_timestamp,
        'is_fasta_mode': int(result.is_fasta_mode),
        'coverage_assessed': int(result.coverage_assessed),
    }

    variant_rows = []
    for ann in result.annotations:
        v = ann.variant
        row = {
            'id': None,
            'chrom': v.chrom,
            'pos': v.pos,
            'ref': v.ref,
            'alt': v.alt,
            'allele_freq': v.allele_freq,
            'depth': v.depth,
            'feature_name': ann.feature_name,
            'reference_name': ref_by_chrom.get(v.chrom, ''),
            'codon_pos': ann.codon_pos,
            'ref_codon': ann.ref_codon,
            'alt_codon': ann.alt_codon,
            'ref_aa': ann.ref_aa,
            'alt_aa': ann.alt_aa,
            'consequence': ann.consequence,
            'af_bin': ann.af_bin,
            'rule_match': int(ann.is_resistance_hit),
            'drug_hits': json.dumps(ann.drug_hits_json()),
            'is_combined_codon_event': ann.is_combined_codon_event,
            'combined_member_count': ann.combined_member_count,
            'single_exchange_aa_freq': ann.single_exchange_aa_freq,
            'rule_effect_aa_freq': json.dumps(ann.rule_effect_aa_freq),
            'rule_effect_alt': json.dumps(ann.rule_effect_alt),
            'freq_method': ann.freq_method,
            'haplotype_links': serialize_haplotype_links(v.haplotype_links),
            'combined_states': serialize_combined_states(ann.combined_states),
        }
        if not result.is_fasta_mode:
            row['variant_source'] = v.source
        variant_rows.append(row)

    coverage_rows = [
        {
            'id': None,
            'feature_name': gap.feature_name,
            'reference_name': ref_by_chrom.get(gap.chrom, ''),
            'chrom': gap.chrom,
            'codon_start': gap.codon_start,
            'codon_end': gap.codon_end,
        }
        for gap in result.coverage_gaps
    ]
    combo_rows = [
        {
            'id': None,
            'reference_name': ref_by_chrom.get(
                hit.matched_variants[0].variant.chrom if hit.matched_variants else '', ''
            ),
            'hit_json': json.dumps(hit.to_dict()),
        }
        for hit in result.formula_hits
    ]
    classification_rows = [
        {
            'id': row.get('id'),
            'drug': row.get('drug', ''),
            'phenotype': row.get('phenotype', ''),
            'clinical_phenotype': row.get('clinical_phenotype', ''),
            'ic50': row.get('ic50', ''),
            'fold_ic50': row.get('fold_ic50', ''),
            'note': row.get('note', ''),
            'source': row.get('source', ''),
            'created_at': row.get('created_at', ''),
        }
        for row in result.sample_classifications
    ]
    references_payload = [
        {
            'reference_name': rg.reference_name,
            'reference_id': rg.reference_id,
            'organism': rg.organism,
            'reference_length_nt': rg.reference_length_nt,
            'query_name': rg.query_name,
            'profiled_feature_names': sorted({m.feature.name for m in rg.feature_matches}),
        }
        for rg in result.references
    ]

    payload = {
        'run': run_payload,
        'variant_result': variant_rows,
        'coverage_gap': coverage_rows,
        'formula_rule_hit': combo_rows,
        'sample_classification': classification_rows,
        'references': references_payload,
    }
    output_path = Path(output_path)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    logger.info('JSON report written to %s', output_path)
    return output_path


def _is_real_tsv_value(column: str, value: str) -> bool:
    """Return True when a TSV cell carries a non-placeholder displayable value."""
    if not value or value == _TSV_PLACEHOLDER:
        return False
    return not (column in _TSV_UNKNOWN_IS_ABSENT_COLUMNS and value.lower() == 'unknown')


def _present_tsv_columns(rows: list[dict[str, str]], *, is_fasta_mode: bool = False) -> tuple[str, ...]:
    """Return TSV columns to emit, omitting empty conditional columns and VCF-only columns in FASTA mode."""
    dropped = set(_TSV_CONDITIONAL_COLUMNS) | (_TSV_VCF_ONLY_COLUMNS if is_fasta_mode else set())
    present = {
        column
        for row in rows
        for column in _TSV_CONDITIONAL_COLUMNS
        if _is_real_tsv_value(column, row.get(column, ''))
    }
    return tuple(col for col in TSV_COLUMNS if col not in dropped or col in present)


def _format_publications_tsv(publications: list[Publication]) -> str:
    """Join publication identifiers with ``|``, preferring DOI then PubMed ID."""
    return '|'.join(pub.doi or pub.pubmed_id for pub in publications if pub.doi or pub.pubmed_id)


def _build_strand_by_feature(result: ProfilingResult) -> dict[str, str]:
    """Map feature_name -> strand from all ReferenceGroup features."""
    strand_by_feature: dict[str, str] = {}
    for rg in result.references:
        for feature in rg.features:
            strand_by_feature.setdefault(feature.name, feature.strand)
    return strand_by_feature


def _base_tsv_row(
    ann: AnnotatedVariant,
    ref_by_chrom: dict[str, str],
    strand_by_feature: dict[str, str],
    display_names: dict[str, str],
) -> dict[str, str]:
    """Build the per-annotation structural columns shared by all TSV row types."""
    return {
        'reference': ref_by_chrom.get(ann.variant.chrom, ''),
        'gene': display_names.get(ann.feature_name, ann.feature_name),
        'nt_mut': nt_change_stored(ann),
        'nt_mut_user': nt_change_user(ann),
        'strand': strand_by_feature.get(ann.feature_name, ''),
        'variant_source': variant_source_label(ann),
        'aa_frequency': repr(round(ann.variant.allele_freq, 3)),
        'depth': str(ann.variant.depth),
        'consequence': display_consequence(ann),
        'aa_effects': format_aa_effects(ann),
    }


def _empty_rule_fields(in_database: str, rule_type: str) -> dict[str, str]:
    """Return placeholder rule columns for non-hit or formula-member rows."""
    return {
        'in_database': in_database,
        'rule_type': rule_type,
        'drug': _TSV_PLACEHOLDER,
        'phenotype': _TSV_PLACEHOLDER,
        'clinical_phenotype': _TSV_PLACEHOLDER,
        'ic50': '',
        'fold_ic50': '',
        'score': '',
        'source': _TSV_PLACEHOLDER,
        'publications': '',
    }


def write_tsv(
    result: ProfilingResult,
    output_path: Path,
    project_conn: sqlite3.Connection | None = None,
    display_names: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> Path:
    """Write a denormalized TSV of all CDS annotations and their matched resistance rules."""
    names = display_names or {}
    ref_by_chrom = reference_name_by_chrom or build_reference_name_by_chrom(result)
    strand_by_feature = _build_strand_by_feature(result)
    formula_member_ids = {id(ann) for hit in result.formula_hits for ann in hit.matched_variants}

    rows: list[dict[str, str]] = []
    for ann in result.cds_annotations:
        base = _base_tsv_row(ann, ref_by_chrom, strand_by_feature, names)
        matches = ann.non_formula_component_rule_matches
        if matches:
            for rule in matches:
                rows.append({
                    **base,
                    'in_database': 'yes',
                    'rule_type': 'single',
                    'drug': rule.drug_name,
                    'phenotype': rule.phenotype,
                    'clinical_phenotype': rule.clinical_phenotype,
                    'ic50': rule.ic50,
                    'fold_ic50': rule.fold_ic50,
                    'score': rule.score,
                    'source': rule.source,
                    'publications': _format_publications_tsv(rule.publications),
                })
        elif id(ann) in formula_member_ids:
            rows.append({**base, **_empty_rule_fields('yes', 'formula-member')})
        else:
            rows.append({**base, **_empty_rule_fields('no', _TSV_PLACEHOLDER)})

    for hit in result.formula_hits:
        rs: ResistanceRuleSet = hit.rule_set
        members = hit.matched_variants
        if not members:
            continue
        rows.append({
            'reference': ref_by_chrom.get(members[0].variant.chrom, ''),
            'gene': ';'.join(names.get(a.feature_name, a.feature_name) for a in members),
            'nt_mut': ';'.join(nt_change_stored(a) for a in members),
            'nt_mut_user': ';'.join(nt_change_user(a) for a in members),
            'strand': ';'.join(strand_by_feature.get(a.feature_name, '') for a in members),
            'variant_source': ';'.join(variant_source_label(a) for a in members),
            'aa_frequency': repr(round(hit.frechet_lower, 3)),
            'depth': '',
            'consequence': ';'.join(a.consequence for a in members),
            'aa_effects': ';'.join(format_aa_effects(a) for a in members),
            'in_database': 'yes',
            'rule_type': 'formula',
            'drug': rs.drug_name,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'ic50': rs.ic50,
            'fold_ic50': rs.fold_ic50,
            'score': rs.score,
            'source': rs.source,
            'publications': _format_publications_tsv(rs.publications),
        })

    for match in select_effect_as_resistant_rules(result, project_conn):
        base = _base_tsv_row(match.annotation, ref_by_chrom, strand_by_feature, names)
        rows.append({
            **base,
            'in_database': 'yes',
            'rule_type': 'single',
            'drug': (match.rule.get('drug') or '').strip(),
            'phenotype': 'resistant',
            'clinical_phenotype': _TSV_PLACEHOLDER,
            'ic50': '',
            'fold_ic50': '',
            'score': '',
            'source': 'Metadata algorithm',
            'publications': '',
        })

    output_path = Path(output_path)
    fieldnames = _present_tsv_columns(rows, is_fasta_mode=result.is_fasta_mode)
    with output_path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fieldnames, delimiter='\t', lineterminator='\n', extrasaction='ignore',
        )
        writer.writeheader()
        writer.writerows(rows)
    logger.info('TSV report written to %s', output_path)
    return output_path


@lru_cache(maxsize=1)
def _load_pdf_template_text() -> str:
    """Load the PDF Jinja template from package resources."""
    return (_REPORT_DIR / 'templates' / 'report_pdf.j2').read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def _load_pdf_css_text() -> str:
    """Load the PDF stylesheet from package resources."""
    return (_REPORT_DIR / 'static' / 'report_pdf.css').read_text(encoding='utf-8')


def write_pdf(
    result: ProfilingResult,
    output_path: Path,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    context: dict | None = None,
    plot_svg_data: bytes | None = None,
    features: list[FeatureRecord] | None = None,
    similarity_high: int = 1,
    similarity_moderate: int = 0,
    cfg: CliConfig = CLI_CONFIG,
) -> Path:
    """Render and write a summary PDF report via WeasyPrint."""
    del plot_svg_data
    if HTML is None:
        raise ValueError('PDF export requested but WeasyPrint is not installed.')

    report_context = _build_pdf_summary_context(
        result,
        context=context,
        project_conn=project_conn,
        rules=rules,
        features=features,
        similarity_high=similarity_high,
        similarity_moderate=similarity_moderate,
        cfg=cfg,
    )
    env = Environment(loader=BaseLoader(), autoescape=True)
    pdf_html = env.from_string(_load_pdf_template_text()).render(
        context=report_context,
        print_css=_load_pdf_css_text(),
    )
    output_path = Path(output_path)
    HTML(string=pdf_html, base_url=str(_REPORT_DIR)).write_pdf(str(output_path))
    logger.info('PDF report written to %s', output_path)
    return output_path


def _build_pdf_summary_context(
    result: ProfilingResult,
    context: dict | None,
    project_conn: sqlite3.Connection | None,
    rules: list[ResistanceRule] | None,
    features: list[FeatureRecord] | None,
    similarity_high: int = 1,
    similarity_moderate: int = 0,
    cfg: CliConfig = CLI_CONFIG,
) -> dict:
    """Project the full report context into the summary-focused PDF layout."""
    if context is None:
        context = build_report_context(
            result,
            project_conn=project_conn,
            rules=rules,
            features=features,
            similarity_high=similarity_high,
            similarity_moderate=similarity_moderate,
            cfg=cfg,
        )

    summary = context.get('summary', {})
    header = context.get('header', {})
    drug_table = summary.get('drug_table', {})
    drug_rows = _build_pdf_drug_rows(drug_table)

    return {
        'title': context.get('title', 'Resistance profile summary'),
        'header': {
            'title': header.get('title', 'Resistance profile summary'),
            'meta_primary': header.get('meta_primary', ''),
            'meta_secondary': header.get('meta_secondary', ''),
            'version': __version__,
        },
        'summary': {
            'sequence_assessment': summary.get('sequence_assessment', {}),
            'db_hits_summary': summary.get('db_hits_summary', {}),
            'sequence_items': _build_pdf_sequence_items(summary.get('sequence_assessment', {})),
            'database_items': _build_pdf_database_items(summary.get('db_hits_summary', {})),
            'mutation_profile': summary.get('mutation_profile', []),
            'has_coverage': bool(summary.get('has_coverage')),
            'coverage_not_assessed': bool(summary.get('coverage_not_assessed')),
            'drug_rows': drug_rows,
            'has_drug_class': any((row.get('drug_class', '') or '').strip() for row in drug_rows),
            'has_assessment': bool(drug_table.get('has_assessment')),
            'has_final_assessment': bool(drug_table.get('has_final_assessment')),
            'method_labels': drug_table.get('method_labels', []),
            'narrative': _condense_pdf_narrative(summary.get('narrative', '')),
        },
    }


def _build_pdf_sequence_items(sequence_assessment: dict) -> list[dict]:
    """Build compact sequence-summary key/value rows for the PDF layout."""
    items = [
        {'label': 'Total mutations', 'value': str(int(sequence_assessment.get('total_mutations', 0) or 0))},
        {'label': 'Non-synonymous variants', 'value': str(int(sequence_assessment.get('non_synonymous_count', 0) or 0))},
        {'label': 'High-impact variants', 'value': str(int(sequence_assessment.get('high_impact_count', 0) or 0))},
    ]
    if high_impact_types := (sequence_assessment.get('high_impact_types', '') or '').strip():
        items.append({'label': 'High-impact composition', 'value': high_impact_types})
    return items


def _build_pdf_database_items(db_hits_summary: dict) -> list[dict]:
    """Build compact database-hit summary key/value rows for the PDF layout."""
    return [
        {'label': 'Total database hits', 'value': str(int(db_hits_summary.get('total', 0) or 0))},
        {'label': 'Single-rule hits', 'value': str(int(db_hits_summary.get('single_rule_hits', 0) or 0))},
        {'label': 'Formula-rule hits', 'value': str(int(db_hits_summary.get('formula_rule_hits', 0) or 0))},
    ]


def _build_pdf_drug_rows(drug_table: dict) -> list[dict]:
    """Flatten grouped or ungrouped summary drug-table rows for the PDF table."""
    grouped_rows = drug_table.get('groups', {})
    source_pairs = (
        [(class_name or '', row) for class_name, rows in grouped_rows.items() for row in rows]
        if grouped_rows
        else [('', row) for row in drug_table.get('rows', [])]
    )
    return [
        {
            'drug_class': drug_class,
            'name': row.get('name', ''),
            'assessment': row.get('assessment', ''),
            'assessment_badge_class': _normalize_assessment_badge_class(
                row.get('assessment_badge_class', ''), row.get('assessment', ''),
            ),
            'hit_count': int(row.get('hit_count', 0)),
            'method_assessments_by_method': {
                ma['method']: ma['assessment'] for ma in row.get('method_assessments', [])
            },
            'method_badge_classes_by_method': {
                ma['method']: _normalize_assessment_badge_class(
                    ma.get('assessment_badge_class', ''), ma.get('assessment', ''),
                )
                for ma in row.get('method_assessments', [])
            },
            'ic50_display': row.get('ic50_display', '—'),
            'fold_ic50_display': row.get('fold_ic50_display', '—'),
        }
        for drug_class, row in source_pairs
    ]


def _normalize_assessment_badge_class(existing_class: str, assessment: str) -> str:
    """Convert an HTML ``phenotype--<rank>`` class or assessment label to a PDF ``is-<rank>`` class."""
    normalized = (existing_class or '').strip().lower()
    if normalized.startswith('phenotype--'):
        pdf_class = f"is-{normalized.removeprefix('phenotype--')}"
        if pdf_class in _PDF_VALID_BADGE_CLASSES:
            return pdf_class
    rank = label_to_rank((assessment or '').strip())
    return _PDF_BADGE_BY_RANK.get(rank, 'is-unknown') if rank is not None else 'is-unknown'


def _condense_pdf_narrative(narrative: str) -> str:
    """Strip per-drug list sentences from the HTML narrative for compact PDF output."""
    text = str(narrative or '')
    text = re.sub(r'\s*List of drugs with[^.]*\.', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*List of drugs without[^.]*\.', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s{2,}', ' ', text).strip()
