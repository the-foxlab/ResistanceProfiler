"""Non-HTML report exports (JSON, TSV, PDF) and export orchestration."""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from pathlib import Path

from jinja2 import BaseLoader, Environment

try:
    from weasyprint import HTML
except ImportError:
    HTML = None

from respro import __version__
from respro.db.models import FeatureRecord, ProfilingResult, ResistanceRule
from respro.db.results import project_fingerprint, project_updated_at
from respro.report.html import build_report_context, write_html
from respro.report.plots import render_lollipop_plot_bytes

logger = logging.getLogger(__name__)


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
) -> dict[str, Path]:
    """
    Write all report outputs to a directory and return a format-to-path mapping.

    :param result: ProfilingResult object
    :param output_dir: directory to write outputs to
    :param features: optional list of features for plotting
    :param rule_feature_names: optional set of rule-backed feature names for focused plotting
    :param project_conn: optional project DB connection for drug overview in reports
    :param rules: optional resistance rules for potential effects analysis
    :param extra_export_formats: optional set of additional output formats ('json', 'tabular', 'pdf')
    :param project_db_path: optional path to project database used for this run
    :param output_html_path: optional explicit HTML output file path; when set, HTML is written
        exactly to this path and JSON/tabular files use its basename stem
    :return: dict mapping format names to output file paths
    """
    if output_html_path is None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = _build_output_stem(result)
        html_path = output_dir / f'{stem}.report.html'
    else:
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
    unknown_formats = requested_formats - {'json', 'tabular', 'pdf'}
    if unknown_formats:
        raise ValueError(f'Unsupported export format(s): {", ".join(sorted(unknown_formats))}')

    plot_svg_data: bytes | None = None
    if features:
        plot_svg_data = render_lollipop_plot_bytes(
            result,
            features,
            fmt='svg',
            rule_feature_names=rule_feature_names,
        )

    write_html(
        result,
        html_path,
        features=features,
        plot_svg_data=plot_svg_data,
        project_conn=project_conn,
        rules=rules,
    )

    outputs: dict[str, Path] = {'html': html_path}

    context: dict | None = None
    if 'json' in requested_formats:
        json_path = output_dir / f'{stem}.results.json'
        write_json(
            result,
            json_path,
            project_conn=project_conn,
            project_db_path=project_db_path,
        )
        outputs['json'] = json_path

    if 'tabular' in requested_formats:
        context = build_report_context(result, project_conn=project_conn, rules=rules, features=features)
        tabular_path = output_dir / f'{stem}.mutations.tsv'
        write_tabular(tabular_path, context['db_hit_rows'], context['db_cols'])
        outputs['tabular'] = tabular_path

    if 'pdf' in requested_formats:
        pdf_path = output_dir / f'{stem}.report.pdf'
        write_pdf(
            result,
            pdf_path,
            project_conn=project_conn,
            rules=rules,
            context=context,
            plot_svg_data=plot_svg_data,
            features=features,
        )
        outputs['pdf'] = pdf_path

    logger.info('Exported report to %s', html_path)
    return outputs


def write_json(
    result: ProfilingResult,
    output_path: Path,
    project_conn: sqlite3.Connection | None = None,
    project_db_path: Path | None = None,
) -> Path:
    """Write one-run JSON with the same information model as results.db rows."""
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
    }

    variant_rows = []
    for ann in result.annotations:
        v = ann.variant
        variant_rows.append({
            'id': None,
            'chrom': v.chrom,
            'pos': v.pos,
            'ref': v.ref,
            'alt': v.alt,
            'allele_freq': v.allele_freq,
            'depth': v.depth,
            'feature_name': ann.feature_name,
            'codon_pos': ann.codon_pos,
            'ref_codon': ann.ref_codon,
            'alt_codon': ann.alt_codon,
            'ref_aa': ann.ref_aa,
            'alt_aa': ann.alt_aa,
            'consequence': ann.consequence,
            'af_bin': ann.af_bin,
            'rule_match': int(ann.is_resistance_hit),
            'drug_hits': json.dumps(ann.drug_hits_json()),
        })

    coverage_rows = [
        {
            'id': None,
            'feature_name': gap.feature_name,
            'codon_start': gap.codon_start,
            'codon_end': gap.codon_end,
        }
        for gap in result.coverage_gaps
    ]

    combo_rows = [
        {
            'id': None,
            'hit_json': json.dumps(hit.to_dict()),
        }
        for hit in result.formula_hits
    ]

    classification_rows = [
        {
            'id': row.get('id'),
            'drug': row.get('drug', ''),
            'phenotype': row.get('phenotype', 'unknown'),
            'clinical_phenotype': row.get('clinical_phenotype', 'unknown'),
            'ic50': row.get('ic50', ''),
            'fold_ic50': row.get('fold_ic50', ''),
            'note': row.get('note', ''),
            'source': row.get('source', ''),
            'created_at': row.get('created_at', ''),
        }
        for row in result.sample_classifications
    ]

    payload = {
        'run': run_payload,
        'variant_result': variant_rows,
        'coverage_gap': coverage_rows,
        'formula_rule_hit': combo_rows,
        'sample_classification': classification_rows,
    }

    output_path = Path(output_path)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    logger.info('JSON report written to %s', output_path)
    return output_path


def write_tabular(output_path: Path, db_hit_rows: list[dict], db_cols: dict[str, bool]) -> Path:
    """Write database-hit rows in a tab-separated table format."""
    output_path = Path(output_path)
    headers = ['Feature', 'AA change', 'Drug']
    if db_cols.get('ic50', False):
        headers.append('IC50')
    if db_cols.get('fold_ic50', False):
        headers.append('Fold-IC50')
    headers.append('Phenotype')
    if db_cols.get('clinical_phenotype', False):
        headers.append('Clinical phenotype')
    headers.extend(['Underlying nt change', 'Consequence', 'Frequency classification'])
    if db_cols.get('source', False):
        headers.append('Source')
    if db_cols.get('comment', False):
        headers.append('Comment')
    if db_cols.get('publication', False):
        headers.append('Publications')

    with output_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for row in db_hit_rows:
            publications = '; '.join(
                filter(None, (_format_publication(pub) for pub in row.get('publications', [])))
            )
            output_row = [
                row.get('feature', ''),
                _strip_html(str(row.get('aa_change', ''))),
                row.get('drug', ''),
            ]
            if db_cols.get('ic50', False):
                output_row.append(row.get('ic50', ''))
            if db_cols.get('fold_ic50', False):
                output_row.append(row.get('fold_ic50', ''))
            output_row.append(row.get('phenotype', ''))
            if db_cols.get('clinical_phenotype', False):
                output_row.append(row.get('clinical_phenotype', ''))

            output_row.extend([
                _strip_html(str(row.get('nt_change', ''))),
                row.get('consequence', ''),
                row.get('af_bin', ''),
            ])
            if db_cols.get('source', False):
                output_row.append(row.get('source', ''))
            if db_cols.get('comment', False):
                output_row.append(row.get('comment', ''))
            if db_cols.get('publication', False):
                output_row.append(publications)
            writer.writerow(output_row)
    logger.info('Tabular report written to %s', output_path)
    return output_path


def write_pdf(
    result: ProfilingResult,
    output_path: Path,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
    context: dict | None = None,
    plot_svg_data: bytes | None = None,
    features: list[FeatureRecord] | None = None,
) -> Path:
    """
    Render and write a dedicated PDF report to a file.

    :param result: ProfilingResult object
    :param output_path: path to write PDF file to
    :param project_conn: optional project DB connection for report context
    :param rules: optional list of resistance rules for potential effects analysis
    :param context: optional prebuilt report context
    :param plot_svg_data: optional SVG bytes for the mutation overview plot
    :param features: optional list of features for context
    :return: path to written PDF file
    """
    if HTML is None:
        raise ValueError('PDF export requested but WeasyPrint is not installed.')

    report_context = _build_pdf_summary_context(
        result,
        context=context,
        project_conn=project_conn,
        rules=rules,
        features=features,
    )
    env = Environment(loader=BaseLoader())
    template = env.from_string(_load_pdf_template_text())

    pdf_html = template.render(
        context=report_context,
        print_css=_load_pdf_css_text(),
    )

    output_path = Path(output_path)
    HTML(
        string=pdf_html,
        base_url=str(Path(__file__).resolve().parent),
    ).write_pdf(str(output_path))
    logger.info('PDF report written to %s', output_path)
    return output_path


def _load_pdf_template_text() -> str:
    """Load the PDF-specific Jinja template text from package resources."""
    template_path = Path(__file__).resolve().parent / 'templates' / 'report_pdf.j2'
    return template_path.read_text(encoding='utf-8')


def _load_pdf_css_text() -> str:
    """Load the PDF-specific CSS text from package resources."""
    css_path = Path(__file__).resolve().parent / 'static' / 'report_pdf.css'
    return css_path.read_text(encoding='utf-8')


def _build_pdf_summary_context(
    result: ProfilingResult,
    context: dict | None,
    project_conn: sqlite3.Connection | None,
    rules: list[ResistanceRule] | None,
    features: list[FeatureRecord] | None,
) -> dict:
    """
    Build PDF context from the existing HTML report context.

    :param result: profiling result
    :param context: optional full HTML context
    :param project_conn: optional project DB connection
    :param rules: optional rules for compatibility with existing call sites
    :param features: optional feature records for compatibility with existing call sites
    :return: summary-focused context for the PDF template
    """
    if context is None:
        context = build_report_context(
            result,
            project_conn=project_conn,
            rules=rules,
            features=features,
        )

    summary = context.get('summary', {})
    header = context.get('header', {})
    sequence_items = _build_pdf_sequence_items(summary.get('sequence_assessment', {}))
    database_items = _build_pdf_database_items(summary.get('db_hits_summary', {}))
    drug_table = summary.get('drug_table', {})
    drug_rows = _build_pdf_drug_rows(drug_table)
    has_drug_class = any((row.get('drug_class', '') or '').strip() for row in drug_rows)
    has_assessment = bool(drug_table.get('has_assessment'))

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
            'sequence_items': sequence_items,
            'database_items': database_items,
            'mutation_profile': summary.get('mutation_profile', []),
            'has_coverage': bool(summary.get('has_coverage')),
            'drug_rows': drug_rows,
            'has_drug_class': has_drug_class,
            'has_assessment': has_assessment,
            'narrative': _condense_pdf_narrative(summary.get('narrative', '')),
        },
    }


def _build_pdf_sequence_items(sequence_assessment: dict) -> list[dict]:
    """
    Build compact sequence summary items for the PDF layout.

    :param sequence_assessment: sequence assessment from summary context
    :return: ordered list of sequence summary key/value rows
    """
    total = int(sequence_assessment.get('total_mutations', 0) or 0)
    non_synonymous = int(sequence_assessment.get('non_synonymous_count', 0) or 0)
    high_impact = int(sequence_assessment.get('high_impact_count', 0) or 0)
    high_impact_types = (sequence_assessment.get('high_impact_types', '') or '').strip()

    items = [
        {'label': 'Total mutations', 'value': str(total)},
        {'label': 'Non-synonymous variants', 'value': str(non_synonymous)},
        {'label': 'High-impact variants', 'value': str(high_impact)},
    ]
    if high_impact_types:
        items.append({'label': 'High-impact composition', 'value': high_impact_types})
    return items


def _build_pdf_database_items(db_hits_summary: dict) -> list[dict]:
    """
    Build compact database-hit summary items for the PDF layout.

    :param db_hits_summary: database hits summary from summary context
    :return: ordered list of database summary key/value rows
    """
    total = int(db_hits_summary.get('total', 0) or 0)
    single_rule = int(db_hits_summary.get('single_rule_hits', 0) or 0)
    formula_rule = int(db_hits_summary.get('formula_rule_hits', 0) or 0)

    return [
        {'label': 'Total database hits', 'value': str(total)},
        {'label': 'Single-rule hits', 'value': str(single_rule)},
        {'label': 'Formula-rule hits', 'value': str(formula_rule)},
    ]


def _build_pdf_drug_rows(drug_table: dict) -> list[dict]:
    """
    Build clinician-facing drug rows from the existing summary drug table.

    :param drug_table: summary drug table context from HTML builder
    :return: ordered rows with drug, count and assessment
    """
    flattened_rows: list[dict] = []
    grouped_rows = drug_table.get('groups', {})

    if grouped_rows:
        for class_name, rows in grouped_rows.items():
            for row in rows:
                flattened_rows.append(
                    {
                        'drug_class': class_name or '',
                        'name': row.get('name', ''),
                        'assessment': row.get('assessment', ''),
                        'assessment_badge_class': _normalize_assessment_badge_class(
                            row.get('assessment_badge_class', ''),
                            row.get('assessment', ''),
                        ),
                        'hit_count': int(row.get('hit_count', 0)),
                    }
                )
    else:
        for row in drug_table.get('rows', []):
            flattened_rows.append(
                {
                    'drug_class': '',
                    'name': row.get('name', ''),
                    'assessment': row.get('assessment', ''),
                    'assessment_badge_class': _normalize_assessment_badge_class(
                        row.get('assessment_badge_class', ''),
                        row.get('assessment', ''),
                    ),
                    'hit_count': int(row.get('hit_count', 0)),
                }
            )
    return flattened_rows


def _normalize_assessment_badge_class(existing_class: str, assessment: str) -> str:
    """Normalize assessment class names to the PDF CSS variant names."""
    normalized_class = (existing_class or '').strip().lower()
    if normalized_class.startswith('phenotype--'):
        normalized_class = normalized_class.replace('phenotype--', 'is-', 1)
    if normalized_class in {'is-resistant', 'is-intermediate', 'is-sensitive', 'is-unknown'}:
        return normalized_class
    return _assessment_label_to_class(assessment)


def _assessment_label_to_class(assessment: str) -> str:
    """Map assessment label text to PDF CSS variant names."""
    normalized = (assessment or '').strip().lower()
    if normalized == 'resistant':
        return 'is-resistant'
    if normalized == 'intermediate':
        return 'is-intermediate'
    if normalized == 'sensitive':
        return 'is-sensitive'
    return 'is-unknown'


def _condense_pdf_narrative(narrative: str) -> str:
    """
    Remove detailed per-drug list sentences from the summary narrative for compact PDF output.

    :param narrative: full narrative text from summary context
    :return: condensed narrative text
    """
    text = str(narrative or '')
    text = re.sub(r'\s*List of drugs with[^.]*\.', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*List of drugs without[^.]*\.', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _build_output_stem(result: ProfilingResult) -> str:
    """Return a safe basename derived from the profiled VCF/FASTA filename."""
    raw_stem = Path(result.vcf_name).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem)
    return safe_stem or 'profile'


def _strip_html(value: str) -> str:
    """Collapse simple inline HTML markup used in report cells to plain text."""
    return re.sub(r'<[^>]+>', '', value)


def _format_publication(pub) -> str:
    """Render one publication object into a compact TSV cell string."""
    doi = (getattr(pub, 'doi', '') or '').strip()
    pubmed_id = (getattr(pub, 'pubmed_id', '') or '').strip()
    raw_input = (getattr(pub, 'raw_input', '') or '').strip()
    if doi:
        return f'DOI:{doi}'
    if pubmed_id:
        return f'PMID:{pubmed_id}'
    return raw_input
