"""Re-annotate stored results runs against an updated project database."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from respro.cli.profile_helpers import (
    export_and_persist_result,
    match_rules_across_references,
    reconstruct_reference_groups,
)
from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import assign_af_bins
from respro.db.models import ProfilingResult
from respro.db.results import (
    list_runs,
    load_coverage_gaps,
    load_profiled_features,
    load_run,
    reconstruct_annotations,
    replace_run_results,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.utils.cli_errors import cli_error
from respro.utils.logging import err_console


def sync_results_database(
    *,
    results_db_path: Path,
    project_db_path: Path,
) -> None:
    """Re-match every compatible stored run against the current rules in *project_db_path*."""
    log = logging.getLogger('respro')
    console = Console(highlight=False)
    results_conn = None
    project_conn = None

    try:
        results_conn = open_results_db(results_db_path)
        project_conn = open_project_db(project_db_path)

        runs = list_runs(results_conn)
        if not runs:
            console.print('No stored results found.')
            return

        synced = 0
        skipped = 0
        for run in runs:
            current_run_id = int(run['id'])
            try:
                hits, formula_hits = _sync_single_run(
                    run_id=current_run_id,
                    project_path=project_db_path,
                    project_conn=project_conn,
                    results_conn=results_conn,
                    log=log,
                )
                synced += 1
                console.print(
                    f'[green]✓[/green] Run #{current_run_id} synced '
                    f'({hits} hit(s), {formula_hits} formula rule hit(s)).'
                )
            except click.ClickException as exc:
                skipped += 1
                console.print(f'[yellow]![/yellow] Skipped run #{current_run_id}: {exc}')

        console.print(Panel(
            f'Synced: {synced} run(s)\nSkipped: {skipped} run(s)',
            title='[green]✓ Sync complete[/green]',
            border_style='green',
        ))
    except (FileNotFoundError, ValueError) as exc:
        cli_error(str(exc))
    finally:
        if results_conn is not None:
            results_conn.close()
        if project_conn is not None:
            project_conn.close()


def _sync_single_run(
    *,
    run_id: int,
    project_path: Path,
    project_conn: sqlite3.Connection,
    results_conn: sqlite3.Connection,
    log: logging.Logger,
) -> tuple[int, int]:
    """Re-match one stored run against current project rules and update all persisted columns."""
    run_dict, variant_rows = load_run(results_conn, run_id)

    stored_fp = run_dict.get('project_fingerprint', '')
    if stored_fp:
        if stored_fp != compute_project_fingerprint(project_conn):
            raise click.ClickException(
                f'Project database fingerprint mismatch for run #{run_id}.\n'
                'The provided --project database does not match the one used for this run.\n'
                'Sync requires the same project database used during the original profiling run.'
            )
    else:
        log.warning('Run #%d has no stored fingerprint — skipping project validation.', run_id)

    ref_row = project_conn.execute(
        'SELECT id FROM reference WHERE name = ?',
        (run_dict['reference_name'],),
    ).fetchone()
    if ref_row is None:
        raise click.ClickException(
            f"Reference {run_dict['reference_name']!r} not found in project database."
        )

    coverage_gaps = load_coverage_gaps(results_conn, run_id)
    profiled_by_ref = load_profiled_features(results_conn, run_id)
    references, _, _, _, primary_organism = reconstruct_reference_groups(
        project_conn, run_dict, variant_rows, profiled_by_ref,
    )

    # Preserve combined-codon states, source labels, and haplotype links; reset only rule outputs.
    annotations = reconstruct_annotations(variant_rows)
    for ann in annotations:
        ann.rule_matches = []
        ann.rule_effect_aa_freq = {}
        ann.rule_effect_alt = {}

    is_fasta_mode = bool(run_dict.get('is_fasta_mode', 0))
    af_bins = CLI_CONFIG.af_bins_fasta.as_dict() if is_fasta_mode else CLI_CONFIG.af_bins.as_dict()

    with err_console.status(f'[dim]Re-annotating run #{run_id}…[/dim]'):
        formula_hits = match_rules_across_references(
            annotations, references, is_fasta_mode=is_fasta_mode, cfg=CLI_CONFIG,
        )
        annotations = assign_af_bins(annotations, bins=af_bins)
        result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=primary_organism,
            sample_name=run_dict.get('sample_name', ''),
            vcf_name=run_dict['vcf_path'],
            total_variants=run_dict.get('total_variants', 0),
            variants_in_cds=run_dict.get('variants_in_cds', 0),
            resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
            is_fasta_mode=is_fasta_mode,
            coverage_assessed=bool(run_dict.get('coverage_assessed', 0)) or is_fasta_mode,
            annotations=annotations,
            formula_hits=formula_hits,
            coverage_gaps=coverage_gaps,
            references=references,
        )
        export_and_persist_result(
            result,
            project_conn=project_conn,
            input_basename=run_dict['vcf_path'],
            output_target=Path('.'),
            results_conn=None,
            project_path=project_path,
            log=log,
        )

    replace_run_results(results_conn, run_id, result, project_conn)
    return result.resistance_hits, len(result.formula_hits)
