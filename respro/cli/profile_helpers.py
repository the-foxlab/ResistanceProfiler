"""
Shared profiling orchestration helpers and output utilities for the CLI layer.

These functions depend on Click, DB wiring, persistence, and report export —
they belong to the CLI layer and must not be moved into respro/core/.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import assign_af_bins
from respro.core.query import pick_best_reference_id, select_matches_for_reference
from respro.core.rules import load_formula_rules, load_rules, match_formula_rules, match_rules
from respro.db.models import AnnotatedVariant, CoverageGap, FeatureMatch, ProfilingResult
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.results import save_run
from respro.db.schema import init_results_db
from respro.io.reference import load_features_for_reference
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
    Pick the best reference, filter fasta_matches, and log matched features.

    :param project_conn: open project database connection
    :param fasta_matches: list of feature alignment matches
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
    matched_feature_names = sorted({match.feature.name for match in fasta_matches})
    logger.info('Matched %d feature(s): %s', len(matched_feature_names), ', '.join(matched_feature_names))
    for match in fasta_matches:
        logger.debug(
            'feature=%s identity=%.2f%% cds_coverage=%.2f%% query_coverage=%.2f%% strand=%s cigar=%s',
            match.feature.name, match.identity * 100, match.cds_coverage * 100,
            match.query_coverage * 100, match.strand, match.cigar,
        )

    return ref_id, ref_name, fasta_matches


def _load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str]]:
    """
    Load features, atomic rules, formula rules, and the union of rule-covered features.

    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :return: (features, rules, formula_rules, rule_feature_names)
    """
    features = load_features_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    formula_rules = load_formula_rules(project_conn, ref_id)
    rule_feature_names: set[str] = {rule.feature_name for rule in rules}
    for formula_rule in formula_rules:
        for member_rule in formula_rule.member_rules.values():
            rule_feature_names.add(member_rule.feature_name)
    return features, rules, formula_rules, rule_feature_names


def _suppress_ruleless_overlap_annotations(
    annotations: list[AnnotatedVariant],
    rule_feature_names: set[str],
) -> list[AnnotatedVariant]:
    """
    Filter out annotations for features that have no rules when multiple features overlap a variant.

    Groups annotations by the underlying variant object identity (same VariantCall).
    For groups with more than one annotation (overlapping features):
    - If at least one feature_name is in rule_feature_names, keep only those annotations.
    - If no feature_name is in rule_feature_names, keep all (variants in ruleless features alone).

    Single-annotation groups (common case) are always left unchanged.

    :param annotations: list of annotated variants
    :param rule_feature_names: set of feature names that have at least one rule
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

        # Multi-annotation group: check if any feature_name is in rule_feature_names.
        has_ruled_feature = any(ann.feature_name in rule_feature_names for ann in group)

        if has_ruled_feature:
            filtered.extend(ann for ann in group if ann.feature_name in rule_feature_names)
        else:
            filtered.extend(group)

    return filtered


@dataclass
class _ProfilingRunContext:
    """Pipeline data assembled before finalization and export."""

    annotations: list[AnnotatedVariant]
    formula_rules: list
    features: list
    rule_feature_names: set[str]
    rules: list
    total_variants: int
    variants_in_cds: int
    coverage_gaps: list[CoverageGap]
    query_sequence: str
    feature_matches: list[FeatureMatch]
    af_bins: dict[str, tuple[float, float]] | None


def _finalize_and_export(
    ctx: _ProfilingRunContext,
    project_conn: sqlite3.Connection,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    output_target: Path,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    extra_export_formats: set[str] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Apply rule matching and AF binning, build the result object, export, and optionally persist.

    :param ctx: pipeline data assembled from the profiling run
    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :param project_name: project name for the report
    :param ref_name: resolved reference name
    :param sample: sample name
    :param input_basename: filename of the input VCF or FASTA
    :param output_target: output path option; interpreted as directory or explicit HTML file
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param extra_export_formats: optional additional output formats ('json', 'tabular', 'pdf')
    :return: (ProfilingResult, export path dict)
    """
    annotations = _suppress_ruleless_overlap_annotations(ctx.annotations, ctx.rule_feature_names)
    annotations = match_rules(annotations, ctx.rules)
    formula_hits = match_formula_rules(
        annotations,
        ctx.formula_rules,
        member_af_threshold=float(CLI_CONFIG.matching.combination_member_af_threshold),
    )
    annotations = assign_af_bins(annotations, bins=ctx.af_bins)

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
        total_variants=ctx.total_variants,
        variants_in_cds=ctx.variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        annotations=annotations,
        formula_hits=formula_hits,
        coverage_gaps=ctx.coverage_gaps,
        query_sequence=ctx.query_sequence,
        feature_matches=ctx.feature_matches,
    )

    raw_stem = Path(input_basename).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem) or 'profile'
    html_output_path = resolve_output_file(output_target, f'{safe_stem}.report.html')

    outputs = export_results(
        result,
        html_output_path.parent,
        features=ctx.features,
        rule_feature_names=ctx.rule_feature_names,
        project_conn=project_conn,
        rules=ctx.rules,
        extra_export_formats=extra_export_formats,
        project_db_path=project_path.resolve(),
        output_html_path=html_output_path,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs

def _print_completion_panel(console: Console, title: str, result: ProfilingResult, outputs: dict) -> None:
    """Render a summary panel after a profiling run."""
    hit_line = f'{result.database_hit_count} unique rule hit(s)'
    if hasattr(result, 'formula_hits') and result.formula_hits:
        hit_line += f'  ·  {len(result.formula_hits)} unique formula rule hit(s)'
    direct_rule_hit_total = sum(len(ann.non_formula_component_rule_matches) for ann in result.annotations)
    total_database_hits = direct_rule_hit_total + len(result.formula_hits)
    total_hit_line = f'{total_database_hits} total database hits'

    lines = [hit_line, total_hit_line, '']
    for fmt, path in outputs.items():
        lines.append(f'[dim]{fmt}[/dim]   {path}')
    console.print(Panel('\n'.join(lines), title=f'[green]{title}[/green]', border_style='green'))
