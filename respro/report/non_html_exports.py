"""Non-HTML report exports (JSON, TSV, PDF) and export orchestration."""

from __future__ import annotations

import base64
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
    plot_png_data: bytes | None = None
    if features:
        plot_svg_data = render_lollipop_plot_bytes(
            result,
            features,
            fmt='svg',
            rule_feature_names=rule_feature_names,
        )
        plot_png_data = render_lollipop_plot_bytes(
            result,
            features,
            fmt='png',
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
            plot_png_data=plot_png_data,
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
    plot_png_data: bytes | None = None,
    features: list[FeatureRecord] | None = None,
) -> Path:
    """
    Render and write a dedicated PDF report to a file.

    :param result: ProfilingResult object
    :param output_path: path to write PDF file to
    :param project_conn: optional project DB connection for report context
    :param rules: optional list of resistance rules for potential effects analysis
    :param context: optional prebuilt report context
    :param features: optional list of features for display name resolution
    :return: path to written PDF file
    """
    if HTML is None:
        raise ValueError('PDF export requested but WeasyPrint is not installed.')

    report_context = context or build_report_context(result, project_conn=project_conn, rules=rules, features=features)
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_load_pdf_template_text())
    plot_data = base64.b64encode(plot_png_data).decode('ascii') if plot_png_data else None

    pdf_html = template.render(
        summary=report_context['summary'],
        summary_text_en=report_context['summary_text_en'],
        mutation_groups=_build_pdf_mutation_entries(result, report_context),
        bibliography=report_context['bibliography'],
        plot_data=plot_data,
        css=_load_pdf_css_text(),
        version=__version__,
    )

    output_path = Path(output_path)
    HTML(
        string=pdf_html,
        base_url=str(Path(__file__).resolve().parent),
    ).write_pdf(str(output_path))
    logger.info('PDF report written to %s', output_path)
    return output_path


def _build_pdf_mutation_entries(result: ProfilingResult, report_context: dict) -> list[dict]:
    """Build grouped mutation cards for non-synonymous annotations."""
    bibliography_lookup = _build_pdf_bibliography_lookup(report_context.get('bibliography', []))
    display_names = report_context.get('display_names', {})
    potential_rows_by_key: dict[tuple[str, int, str], list[dict]] = {}
    for potential_row in report_context.get('potential_rows', []):
        key = (
            potential_row.get('feature', ''),
            int(potential_row.get('codon_pos', -1) or -1),
            potential_row.get('observed_change', ''),
        )
        potential_rows_by_key.setdefault(key, []).append(potential_row)

    rows: list[tuple[str, int, int, dict]] = []
    for ann in result.annotations:
        if ann.consequence == 'synonymous':
            continue
        variant = ann.variant
        feature_name = display_names.get(ann.feature_name, ann.feature_name) or 'Intergenic'
        aa_change = ''
        if ann.ref_aa and ann.alt_aa:
            aa_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
        nt_change = f'{variant.ref}{variant.pos + 1}{variant.alt}'
        hits = []
        for rule in ann.non_formula_component_rule_matches:
            hits.append({
                'drug': rule.drug_name,
                'phenotype': rule.phenotype,
                'clinical_phenotype': rule.clinical_phenotype,
                'ic50': rule.ic50,
                'fold_ic50': rule.fold_ic50,
                'source': rule.source,
                'citation_numbers': _collect_pdf_rule_citations(rule, bibliography_lookup),
                'phenotype_badge_class': _phenotype_badge_class(rule.phenotype),
            })

        similarity_hits: list[dict] = []
        if not hits:
            key = (feature_name, ann.codon_pos if ann.codon_pos is not None else -1, aa_change)
            for potential_row in potential_rows_by_key.get(key, []):
                similarity = str(potential_row.get('similarity', '') or '')
                similarity_hits.append({
                    'drug': str(potential_row.get('drug', '') or ''),
                    'rule_change': str(potential_row.get('rule_change', '') or ''),
                    'similarity': similarity,
                    'similarity_badge_class': _similarity_badge_class(similarity),
                    'phenotype': str(potential_row.get('phenotype', '') or ''),
                    'phenotype_badge_class': _phenotype_badge_class(
                        str(potential_row.get('phenotype', '') or ''),
                    ),
                    'citation_numbers': list(potential_row.get('pub_citations', [])),
                })

        codon_pos = ann.codon_pos if ann.codon_pos is not None else -1
        row = {
            'consequence': ann.consequence or 'unknown',
            'effect_badge_class': _effect_badge_class(ann.consequence),
            'aa_change': aa_change,
            'nt_change': nt_change,
            'allele_freq': variant.allele_freq,
            'af_bin': ann.af_bin or 'unknown',
            'db_hits': hits,
            'similarity_hits': similarity_hits,
        }
        rows.append((feature_name, codon_pos, variant.pos, row))

    rows.sort(key=lambda item: (item[0].lower(), item[1], item[2]))
    grouped: list[dict] = []
    current_feature: str | None = None
    current_group: dict | None = None
    for feature_name, _, _, row in rows:
        if feature_name != current_feature:
            current_group = {'feature': feature_name, 'mutations': []}
            grouped.append(current_group)
            current_feature = feature_name
        current_group['mutations'].append(row)
    return grouped


def _phenotype_badge_class(phenotype: str) -> str:
    """Map a phenotype value to a PDF badge CSS class."""
    normalized = (phenotype or '').strip().lower()
    if normalized == 'resistant':
        return 'badge-resistant'
    if normalized == 'intermediate':
        return 'badge-intermediate'
    if normalized == 'sensitive':
        return 'badge-sensitive'
    if normalized == 'contradictory':
        return 'badge-contradictory'
    return 'badge-unknown'


def _effect_badge_class(consequence: str) -> str:
    """Map a consequence value to a PDF badge CSS class."""
    normalized = (consequence or '').strip().lower()
    if normalized == 'missense':
        return 'badge-missense'
    if normalized == 'frameshift':
        return 'badge-frameshift'
    if normalized in {'insertion', 'deletion', 'inframe_complex'}:
        return 'badge-indel'
    return 'badge-unknown'


def _similarity_badge_class(similarity: str) -> str:
    """Map a similarity label to a PDF badge CSS class."""
    normalized = (similarity or '').strip().lower()
    if normalized == 'high':
        return 'badge-sim-high'
    if normalized == 'moderate':
        return 'badge-sim-moderate'
    if normalized == 'low':
        return 'badge-sim-low'
    return 'badge-unknown'


def _build_pdf_bibliography_lookup(bibliography: list[dict]) -> dict[tuple[str, str, str, str], int]:
    """Map normalized bibliography metadata to citation numbers for PDF mutation rows."""
    lookup: dict[tuple[str, str, str, str], int] = {}
    for citation in bibliography:
        citation_num = int(citation.get('citation_num', 0) or 0)
        if citation_num <= 0:
            continue
        key = _pdf_bibliography_key(
            doi=citation.get('doi', ''),
            pubmed_id=citation.get('pubmed_id', ''),
            raw_input=citation.get('raw_input', ''),
            title=citation.get('title', ''),
        )
        lookup[key] = citation_num
    return lookup


def _collect_pdf_rule_citations(
    rule: ResistanceRule,
    bibliography_lookup: dict[tuple[str, str, str, str], int],
) -> list[int]:
    """Resolve and deduplicate bibliography numbers for one resistance rule."""
    citation_numbers: list[int] = []
    seen: set[int] = set()
    for pub in rule.publications:
        key = _pdf_bibliography_key(
            doi=getattr(pub, 'doi', ''),
            pubmed_id=getattr(pub, 'pubmed_id', ''),
            raw_input=getattr(pub, 'raw_input', ''),
            title=getattr(pub, 'title', ''),
        )
        citation_num = bibliography_lookup.get(key)
        if citation_num is None or citation_num in seen:
            continue
        seen.add(citation_num)
        citation_numbers.append(citation_num)
    return citation_numbers


def _pdf_bibliography_key(
    doi: str,
    pubmed_id: str,
    raw_input: str,
    title: str,
) -> tuple[str, str, str, str]:
    """Build a stable key used for PDF bibliography and mutation-hit citation lookup."""
    return (
        (doi or '').strip().lower(),
        (pubmed_id or '').strip(),
        (raw_input or '').strip().lower(),
        (title or '').strip().lower(),
    )


def _load_pdf_template_text() -> str:
    """Load the PDF-specific Jinja template text from package resources."""
    template_path = Path(__file__).resolve().parent / 'templates' / 'report_pdf.j2'
    return template_path.read_text(encoding='utf-8')


def _load_pdf_css_text() -> str:
    """Load the PDF-specific CSS text from package resources."""
    css_path = Path(__file__).resolve().parent / 'static' / 'report_pdf.css'
    return css_path.read_text(encoding='utf-8')


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
