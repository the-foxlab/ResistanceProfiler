"""
Results synchronization helpers for re-annotating stored runs against a project database.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from respro.cli.profile_helpers import (
    _finalize_and_export,
    _load_reference_data,
    _ProfilingRunContext,
)
from respro.core.query import resolve_cached_query_reference
from respro.db.models import AnnotatedVariant, GeneMatch, VariantCall
from respro.db.results import (
    list_runs,
    load_coverage_gaps,
    load_run,
    project_updated_at,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.utils.logging import err_console

logger = logging.getLogger(__name__)


def sync_results_database(
    *,
    results_db_path: Path,
    project_db_path: Path,
) -> None:
    """
    Re-annotate a stored run in a local database against the current project database and update stored results.

    Loads raw variant calls from the results database, re-runs annotation and rule matching
    against the live project DB, replaces stored variant_result and formula-hit rows, and
    updates resistance_hits and formula_hits counters. Requires a project fingerprint match.
    This is useful if the project database has been updated with new rules since the original run.

    All runs in the results database are attempted; runs with a fingerprint mismatch
    are skipped and reported.
    """
    logger = logging.getLogger('respro')
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
        run_ids = [int(run['id']) for run in runs]

        synced = 0
        skipped = 0
        for current_run_id in run_ids:
            try:
                hits, formula_hits = _sync_single_run(
                    run_id=current_run_id,
                    project_path=project_db_path,
                    project_conn=project_conn,
                    results_conn=results_conn,
                    logger=logger,
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
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()
        if project_conn is not None:
            project_conn.close()


def _sync_single_run(
    *,
    run_id: int,
    project_path: Path,
    project_conn,
    results_conn,
    logger: logging.Logger,
) -> tuple[int, int]:
    """Sync one stored run and return (resistance_hits, formula_hits)."""
    run_dict, variant_rows = load_run(results_conn, run_id)

    stored_fp = run_dict.get('project_fingerprint', '')
    if stored_fp:
        current_fp = compute_project_fingerprint(project_conn)
        if stored_fp != current_fp:
            raise click.ClickException(
                f'Project database fingerprint mismatch for run #{run_id}.\n'
                'The provided --project database does not match the one used for this run.\n'
                'Sync requires the same project database used during the original profiling run.'
            )
    else:
        logger.warning('Run #%d has no stored fingerprint — skipping project validation.', run_id)

    ref_row = project_conn.execute(
        'SELECT id FROM reference WHERE name = ?',
        (run_dict['reference_name'],),
    ).fetchone()
    if ref_row is None:
        raise click.ClickException(
            f"Reference {run_dict['reference_name']!r} not found in project database."
        )
    ref_id = int(ref_row['id'])

    genes, rules, formula_rules, rule_gene_names = _load_reference_data(project_conn, ref_id)
    coverage_gaps = load_coverage_gaps(results_conn, run_id)

    # Reconstruct raw AnnotatedVariant objects without rule matches so re-annotation is clean.
    raw_annotations: list[AnnotatedVariant] = []
    for row in variant_rows:
        v = VariantCall(
            chrom=row['chrom'],
            pos=row['pos'],
            ref=row['ref'],
            alt=row['alt'],
            allele_freq=row.get('allele_freq') or 0.0,
            depth=row.get('depth') or 0,
        )
        raw_annotations.append(AnnotatedVariant(
            variant=v,
            gene_name=row.get('gene_name', ''),
            codon_pos=row.get('codon_pos') or 0,
            ref_codon=row.get('ref_codon', ''),
            alt_codon=row.get('alt_codon', ''),
            ref_aa=row.get('ref_aa', ''),
            alt_aa=row.get('alt_aa', ''),
            consequence=row.get('consequence', ''),
            af_bin=row.get('af_bin', ''),
        ))

    # Try to recover query sequence and gene matches for FASTA-mode runs.
    query_sequence = ''
    gene_matches: list[GeneMatch] = []
    sample_name = run_dict.get('sample_name', '')
    if sample_name:
        try:
            _, query_sequence, gene_matches = resolve_cached_query_reference(
                project_conn, sample_name,
            )
        except ValueError as exc:
            logger.debug(
                'Skipping cached query-reference recovery for sample %r: %s',
                sample_name,
                exc,
            )

    with err_console.status(f'[dim]Re-annotating run #{run_id}…[/dim]'):
        ctx = _ProfilingRunContext(
            annotations=raw_annotations,
            formula_rules=formula_rules,
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            total_variants=run_dict.get('total_variants', 0),
            variants_in_cds=run_dict.get('variants_in_cds', 0),
            coverage_gaps=coverage_gaps or [],
            query_sequence=query_sequence,
            gene_matches=gene_matches or [],
            af_bins=None,
        )
        result, _outputs = _finalize_and_export(
            ctx=ctx,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=run_dict['project_name'],
            ref_name=run_dict['reference_name'],
            sample=sample_name,
            input_basename=run_dict['vcf_path'],
            output_target=Path('.'),
            results_conn=None,
            project_path=project_path,
            logger=logger,
        )

    # Replace stored variant rows and re-write formula hits.
    results_conn.execute('DELETE FROM variant_result WHERE run_id = ?', (run_id,))
    results_conn.execute('DELETE FROM formula_rule_hit WHERE run_id = ?', (run_id,))

    for ann in result.annotations:
        vv = ann.variant
        results_conn.execute(
            'INSERT INTO variant_result '
            '(run_id, chrom, pos, ref, alt, allele_freq, depth, '
            'gene_name, codon_pos, ref_codon, alt_codon, ref_aa, alt_aa, '
            'consequence, af_bin, rule_match, drug_hits) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                run_id,
                vv.chrom,
                vv.pos,
                vv.ref,
                vv.alt,
                vv.allele_freq,
                vv.depth,
                ann.gene_name,
                ann.codon_pos,
                ann.ref_codon,
                ann.alt_codon,
                ann.ref_aa,
                ann.alt_aa,
                ann.consequence,
                ann.af_bin,
                int(ann.is_resistance_hit),
                json.dumps(ann.drug_hits_json()),
            ),
        )

    for formula_hit in result.formula_hits:
        results_conn.execute(
            'INSERT INTO formula_rule_hit (run_id, hit_json) VALUES (?, ?)',
            (run_id, json.dumps(formula_hit.to_dict())),
        )

    current_updated_at = project_updated_at(project_conn)
    results_conn.execute(
        'UPDATE run SET resistance_hits = ?, formula_hits = ?, project_updated_at = ? WHERE id = ?',
        (result.resistance_hits, len(result.formula_hits), current_updated_at, run_id),
    )
    results_conn.commit()
    return result.resistance_hits, len(result.formula_hits)
