"""
Shared profiling orchestration helpers and output utilities for the CLI layer.

These functions depend on Click, DB wiring, persistence, and report export —
they belong to the CLI layer and must not be moved into respro/core/.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from respro.core.query import pick_best_reference_id, select_matches_for_reference
from respro.core.rules import load_formula_rules, load_rules, match_formula_rules, match_rules
from respro.db.models import AnnotatedVariant, CoverageGap, GeneMatch, ProfilingResult
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.results import save_run
from respro.db.schema import init_results_db
from respro.io.reference import load_genes_for_reference
from respro.report.non_html_exports import export_results
from respro.utils.files import resolve_output_file


def _parse_export_formats(export_values: list[str] | None) -> set[str] | None:
    """Normalize and validate repeated ``--export`` values."""
    if export_values is None:
        return None

    normalized_formats: set[str] = set()
    for export_value in export_values:
        normalized_value = export_value.strip().lower()
        if normalized_value not in ('json', 'tabular', 'pdf'):
            raise click.ClickException('Invalid --export value. Choose one of: json, tabular, pdf.')
        normalized_formats.add(normalized_value)

    return normalized_formats if normalized_formats else None


def _init_results_db_connection(
    results_db: str | None,
    project_conn: sqlite3.Connection,
    logger: logging.Logger,
) -> sqlite3.Connection | None:
    """
    Open or initialise a results database and validate project fingerprint compatibility.

    :param results_db: path to results database, or None to skip
    :param project_conn: open project database connection
    :param logger: logger instance
    :return: open results database connection, or None
    """
    if not results_db:
        return None

    results_db_path = Path(results_db)
    existed = results_db_path.is_file()
    try:
        results_conn = init_results_db(results_db_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    if existed:
        logger.info('Results database validated: %s', results_db_path)
        current_fp = compute_project_fingerprint(project_conn)
        existing_run = results_conn.execute(
            "SELECT project_fingerprint FROM run WHERE project_fingerprint != '' LIMIT 1"
        ).fetchone()
        if existing_run and existing_run['project_fingerprint'] != current_fp:
            results_conn.close()
            raise click.ClickException(
                'Project fingerprint mismatch: the provided --project database does not match '
                'the project used for existing runs in this results database.\n'
                'Ensure you use the same project database for all runs in this results file.'
            )
    else:
        logger.info('Results database initialised: %s', results_db_path)

    return results_conn


def _resolve_reference(
    project_conn: sqlite3.Connection,
    fasta_matches: list,
    query_name: str,
    logger: logging.Logger,
) -> tuple[int, str, list]:
    """
    Pick the best reference, filter fasta_matches, and log matched genes.

    :param project_conn: open project database connection
    :param fasta_matches: list of gene alignment matches
    :param query_name: query sequence name for logging
    :param logger: logger instance
    :return: (ref_id, ref_name, filtered fasta_matches)
    """
    ref_id = pick_best_reference_id(fasta_matches)
    fasta_matches = select_matches_for_reference(fasta_matches, ref_id)

    ref_name_row = project_conn.execute(
        'SELECT name FROM reference WHERE id = ?', (ref_id,)
    ).fetchone()
    if ref_name_row is None:
        raise click.ClickException(f'Reference id {ref_id} not found in project database')
    ref_name = ref_name_row['name']

    logger.info('Matched query reference %r to internal reference %r', query_name, ref_name)
    matched_gene_names = sorted({match.gene.name for match in fasta_matches})
    logger.info('Matched %d gene(s): %s', len(matched_gene_names), ', '.join(matched_gene_names))
    for match in fasta_matches:
        logger.debug(
            'gene=%s identity=%.2f%% cds_coverage=%.2f%% query_coverage=%.2f%% strand=%s cigar=%s',
            match.gene.name, match.identity * 100, match.cds_coverage * 100,
            match.query_coverage * 100, match.strand, match.cigar,
        )

    return ref_id, ref_name, fasta_matches


def _load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str]]:
    """
    Load genes, atomic rules, formula rules, and the union of rule-covered genes.

    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :return: (genes, rules, formula_rules, rule_gene_names)
    """
    genes = load_genes_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    formula_rules = load_formula_rules(project_conn, ref_id)
    rule_gene_names: set[str] = {rule.gene_name for rule in rules}
    for formula_rule in formula_rules:
        for member_rule in formula_rule.member_rules.values():
            rule_gene_names.add(member_rule.gene_name)
    return genes, rules, formula_rules, rule_gene_names


def _suppress_ruleless_overlap_annotations(
    annotations: list[AnnotatedVariant],
    rule_gene_names: set[str],
) -> list[AnnotatedVariant]:
    """
    Filter out annotations for genes that have no rules when multiple genes overlap a variant.

    Groups annotations by the underlying variant object identity (same VariantCall).
    For groups with more than one annotation (overlapping genes):
    - If at least one gene_name is in rule_gene_names, keep only those annotations.
    - If no gene_name is in rule_gene_names, keep all (variants in ruleless genes alone).

    Single-annotation groups (common case) are always left unchanged.

    :param annotations: list of annotated variants
    :param rule_gene_names: set of gene names that have at least one rule
    :return: filtered list of annotated variants
    """
    # Group by variant object identity (each annotation for the same underlying variant
    # shares the exact same VariantCall object).
    variant_groups: dict[int, list[AnnotatedVariant]] = {}
    for ann in annotations:
        variant_id = id(ann.variant)
        if variant_id not in variant_groups:
            variant_groups[variant_id] = []
        variant_groups[variant_id].append(ann)

    filtered: list[AnnotatedVariant] = []
    for group in variant_groups.values():
        # Single-annotation groups always pass through unchanged.
        if len(group) == 1:
            filtered.extend(group)
            continue

        # Multi-annotation group: check if any gene_name is in rule_gene_names.
        has_ruled_gene = any(ann.gene_name in rule_gene_names for ann in group)

        if has_ruled_gene:
            filtered.extend(ann for ann in group if ann.gene_name in rule_gene_names)
        else:
            filtered.extend(group)

    return filtered


def _finalize_and_export(
    annotations: list,
    formula_rules: list,
    project_conn: sqlite3.Connection,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    total_variants: int,
    variants_in_cds: int,
    output_target: Path,
    genes: list,
    rule_gene_names: set[str],
    rules: list,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    af_bins: dict[str, tuple[float, float]] | None = None,
    coverage_gaps: list[CoverageGap] | None = None,
    query_sequence: str = '',
    gene_matches: list[GeneMatch] | None = None,
    extra_export_formats: set[str] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Apply rule matching and AF binning, build the result object, export, and optionally persist.

    :param annotations: list of annotated variants
    :param formula_rules: formula rules
    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :param project_name: project name for the report
    :param ref_name: resolved reference name
    :param sample: sample name
    :param input_basename: filename of the input VCF or FASTA
    :param total_variants: total variant count
    :param variants_in_cds: variant count within CDS regions
    :param output_target: output path option; interpreted as directory or explicit HTML file
    :param genes: gene list for the reference
    :param rule_gene_names: set of gene names covered by any rule
    :param rules: resistance rules for the reference
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param af_bins: optional custom AF bin thresholds; defaults to VCF-mode bins
    :param coverage_gaps: optional list of non-covered codon positions (FASTA mode)
    :param query_sequence: query FASTA sequence used during profiling
    :param gene_matches: gene alignment matches used during profiling
    :param extra_export_formats: optional additional output formats ('json', 'tabular', 'pdf')
    :return: (ProfilingResult, export path dict)
    """
    # Filter out spurious annotations for ruleless genes when overlapping with ruled genes.
    annotations = _suppress_ruleless_overlap_annotations(annotations, rule_gene_names)
    annotations = match_rules(annotations, rules)
    formula_hits = match_formula_rules(annotations, formula_rules)
    annotations = assign_af_bins(annotations, bins=af_bins)

    reference_row = project_conn.execute(
        'SELECT organism, length FROM reference WHERE id = ?', (ref_id,)
    ).fetchone()
    organism = reference_row['organism'] or '' if reference_row else ''
    reference_length_nt = int(reference_row['length'] or 0) if reference_row else 0

    result = ProfilingResult(
        project_name=project_name,
        organism=organism,
        reference_name=ref_name,
        reference_length_nt=reference_length_nt,
        sample_name=sample,
        vcf_name=input_basename,
        total_variants=total_variants,
        variants_in_cds=variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        annotations=annotations,
        formula_hits=formula_hits,
        coverage_gaps=coverage_gaps or [],
        query_sequence=query_sequence,
        gene_matches=gene_matches or [],
    )

    raw_stem = Path(input_basename).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem) or 'profile'
    html_output_path = resolve_output_file(output_target, f'{safe_stem}.report.html')

    outputs = export_results(
        result,
        html_output_path.parent,
        genes=genes,
        rule_gene_names=rule_gene_names,
        project_conn=project_conn,
        rules=rules,
        extra_export_formats=extra_export_formats,
        project_db_path=project_path.resolve(),
        output_html_path=html_output_path,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs

def assign_af_bins(
    annotations: list[AnnotatedVariant],
    bins: dict[str, tuple[float, float]] | None = None,
) -> list[AnnotatedVariant]:
    """
    Assign an allele-frequency bin label to each annotated variant.

    Mutates ``af_bin`` in place and returns the same list.

    :param annotations: annotated variants to bin
    :param bins: mapping of bin label to (lower_inclusive, upper_inclusive);
        defaults to the built-in high/intermediate/low bins
    :return: the same annotations list with af_bin populated
    """
    if bins is None:
        bins = {
            'high': (0.75, 1.0),
            'intermediate': (0.25, 0.7499),
            'low': (0.01, 0.2499),
        }

    # Sort bins by lower bound descending so higher bins are checked first
    sorted_bins = sorted(bins.items(), key=lambda x: -x[1][0])

    for ann in annotations:
        af = ann.variant.allele_freq
        for label, (lo, hi) in sorted_bins:
            if lo <= af <= hi:
                ann.af_bin = label

    return annotations


def _print_completion_panel(console: Console, title: str, result: ProfilingResult, outputs: dict) -> None:
    """Render a summary panel after a profiling run."""
    hit_line = f'{result.resistance_hits} database hit(s)'
    if hasattr(result, 'formula_hits') and result.formula_hits:
        hit_line += f'  ·  {len(result.formula_hits)} formula rule hit(s)'
    lines = [hit_line, '']
    for fmt, path in outputs.items():
        lines.append(f'[dim]{fmt}[/dim]   {path}')
    console.print(Panel('\n'.join(lines), title=f'[green]{title}[/green]', border_style='green'))
