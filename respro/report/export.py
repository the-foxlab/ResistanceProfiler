"""
Unified export dispatcher — write requested outputs with deterministic VCF-based filenames.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from io import StringIO
from pathlib import Path

from respro.db.models import GeneRecord, ResistanceRule
from respro.report.html import write_html
from respro.report.plots import render_lollipop_plot_bytes
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)

_variant_columns = [
    'gene', 'nt_change', 'aa_change', 'consequence',
    'allele_freq', 'af_bin', 'database_hit', 'drug_hits',
]


def _format_drug_hits(row: dict) -> str:
    """Return a stable, human-readable TSV string for matched drug hits."""
    hits = row.get('drug_hits', [])
    if not hits:
        return ''
    return '; '.join(f"{hit['drug']}({hit['phenotype']})" for hit in hits)


def _build_output_stem(result: ProfilingResult) -> str:
    """Return a safe basename derived from the profiled VCF filename."""
    raw_stem = Path(result.vcf_name).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem)
    return safe_stem or 'profile'


def write_tsv(result: ProfilingResult, output_path: Path) -> Path:
    """
    Write the variant table to a TSV file.

    :param result: ProfilingResult object
    :param output_path: path to write TSV file to
    :return: the output path
    """
    rows = result.variants_as_dicts()
    output_path = Path(output_path)

    with open(output_path, 'w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=_variant_columns, delimiter='\t', extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            row['drug_hits'] = _format_drug_hits(row)
            writer.writerow(row)

    return output_path


def to_tsv_string(result: ProfilingResult) -> str:
    """
    Return the variant table as a TSV string.

    :param result: ProfilingResult object
    :return: TSV string
    """
    buf = StringIO()
    rows = result.variants_as_dicts()
    writer = csv.DictWriter(buf, fieldnames=_variant_columns, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        row['drug_hits'] = _format_drug_hits(row)
        writer.writerow(row)
    return buf.getvalue()


def export_results(
    result: ProfilingResult,
    output_dir: Path,
    genes: list[GeneRecord] | None = None,
    rule_gene_names: set[str] | None = None,
    project_conn: sqlite3.Connection | None = None,
    rules: list[ResistanceRule] | None = None,
) -> dict[str, Path]:
    """
    Write all requested report outputs and return format-to-path mapping.

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
    outputs: dict[str, Path] = {}
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
    outputs['html'] = html_path


    logger.info('Exported %d format(s) to %s', len(outputs), output_dir)
    return outputs

