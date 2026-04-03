"""
Unified export dispatcher — write all requested output formats and tabular helpers.
"""

from __future__ import annotations

import csv
import logging
from io import StringIO
from pathlib import Path

from respro.db.models import GeneRecord
from respro.report.html import write_html
from respro.report.plots import lollipop_plot
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)

# Output column order for TSV exports — defines the report schema
_variant_columns = [
    'chrom', 'pos', 'ref', 'alt', 'allele_freq', 'depth',
    'gene', 'codon_pos', 'ref_codon', 'alt_codon',
    'ref_aa', 'alt_aa', 'consequence', 'af_bin',
    'resistance_hit', 'drug_hits',
]


def _format_drug_hits(row: dict) -> str:
    """Return a stable, human-readable TSV string for matched drug hits."""
    hits = row.get('drug_hits', [])
    if not hits:
        return ''
    return '; '.join(f"{hit['drug']}({hit['phenotype']})" for hit in hits)


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
    formats: tuple[str, ...] = ('html', 'json'),
) -> dict[str, Path]:
    """
    Write all requested report outputs and return format-to-path mapping.

    :param result: ProfilingResult object
    :param output_dir: directory to write outputs to
    :param genes: optional list of genes for plotting
    :param rule_gene_names: optional set of rule-backed gene names for focused plotting
    :param formats: tuple of output formats to generate
    :return: dict mapping format names to output file paths
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    # Plot (needed for HTML embedding)
    svg_path: Path | None = None
    if genes and ('svg' in formats or 'pdf' in formats or 'html' in formats):
        generated_svg_path = output_dir / 'mutations.svg'
        lollipop_plot(result, genes, generated_svg_path, fmt='svg', rule_gene_names=rule_gene_names)
        outputs['svg'] = generated_svg_path
        svg_path = generated_svg_path

    if 'pdf' in formats and genes:
        pdf_path = output_dir / 'mutations.pdf'
        lollipop_plot(result, genes, pdf_path, fmt='pdf', rule_gene_names=rule_gene_names)
        outputs['pdf'] = pdf_path

    if 'html' in formats:
        html_path = output_dir / 'report.html'
        write_html(result, html_path, genes=genes, plot_svg_path=svg_path)
        outputs['html'] = html_path

    if 'json' in formats:
        json_path = output_dir / 'results.json'
        json_path.write_text(result.to_json(), encoding='utf-8')
        outputs['json'] = json_path

    if 'tsv' in formats:
        tsv_path = output_dir / 'variants.tsv'
        write_tsv(result, tsv_path)
        outputs['tsv'] = tsv_path

    logger.info('Exported %d format(s) to %s', len(outputs), output_dir)
    return outputs

