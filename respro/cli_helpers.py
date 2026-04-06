"""
Shared profiling orchestration helpers for the CLI layer.

These functions depend on Click, DB wiring, persistence, and report export —
they belong to the CLI layer and must not be moved into respro/core/.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import click

from respro.core.profile import pick_best_reference_id, select_matches_for_reference
from respro.core.resistance_rules import load_rule_sets, load_rules, match_rule_sets, match_rules
from respro.core.vcf_annotation import assign_af_bins
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.results import save_run
from respro.db.schema import init_results_db
from respro.io.reference import load_genes_for_reference
from respro.report.html import export_results
from respro.report.results_model import ProfilingResult


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
            'gene=%s identity=%.2f%% coverage=%.2f%% strand=%s cigar=%s',
            match.gene.name, match.identity * 100, match.coverage * 100,
            match.strand, match.cigar,
        )

    return ref_id, ref_name, fasta_matches


def _load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str]]:
    """
    Load genes, rules, rule_sets, and the union of gene names covered by any rule.

    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :return: (genes, rules, rule_sets, rule_gene_names)
    """
    genes = load_genes_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    rule_sets = load_rule_sets(project_conn, ref_id)
    rule_gene_names: set[str] = {rule.gene_name for rule in rules}
    for rule_set in rule_sets:
        for member in rule_set.members:
            rule_gene_names.add(member.gene_name)
    return genes, rules, rule_sets, rule_gene_names


def _finalize_and_export(
    annotations: list,
    rule_sets: list,
    project_conn: sqlite3.Connection,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    total_variants: int,
    variants_in_cds: int,
    output_dir: Path,
    genes: list,
    rule_gene_names: set[str],
    rules: list,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    af_bins: dict[str, tuple[float, float]] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Apply rule matching and AF binning, build the result object, export, and optionally persist.

    :param annotations: list of annotated variants
    :param rule_sets: combo rule sets
    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :param project_name: project name for the report
    :param ref_name: resolved reference name
    :param sample: sample name
    :param input_basename: filename of the input VCF or FASTA
    :param total_variants: total variant count
    :param variants_in_cds: variant count within CDS regions
    :param output_dir: output directory path
    :param genes: gene list for the reference
    :param rule_gene_names: set of gene names covered by any rule
    :param rules: resistance rules for the reference
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param af_bins: optional custom AF bin thresholds; defaults to VCF-mode bins
    :return: (ProfilingResult, export path dict)
    """
    annotations = match_rules(annotations, rules)
    combo_hits = match_rule_sets(annotations, rule_sets)
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
        combo_hits=combo_hits,
    )

    outputs = export_results(
        result,
        output_dir,
        genes=genes,
        rule_gene_names=rule_gene_names,
        project_conn=project_conn,
        rules=rules,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs

