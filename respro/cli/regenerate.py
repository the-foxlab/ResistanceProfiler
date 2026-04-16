"""
`respro regenerate` command — regenerate a report from a stored run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.panel import Panel

from respro.core.rules import load_rules
from respro.db.models import ProfilingResult
from respro.db.results import (
    load_classifications,
    load_combo_rule_hits,
    load_coverage_gaps,
    load_run,
    reconstruct_annotations,
    reconstruct_combo_rule_hits,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.io.reference import load_genes_for_reference
from respro.report.html import export_results
from respro.utils.logging import err_console


def regenerate(
    result_db: Annotated[
        Path,
        typer.Option(
            '--result-db',
            '-d',
            exists=True,
            help='Results database.',
        ),
    ],
    run_id: Annotated[
        int,
        typer.Option(
            '--run-id',
            '-i',
            help='Run ID to regenerate.',
        ),
    ],
    project: Annotated[
        Path,
        typer.Option(
            '--project',
            '-p',
            exists=True,
            help='Project database.',
        ),
    ],
    out: Annotated[
        Path,
        typer.Option(
            '--out',
            '-o',
            help='Output directory.',
        ),
    ],
) -> None:
    """Regenerate a report from a stored run."""
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    results_conn = None
    project_conn = None

    try:
        results_conn = open_results_db(result_db)
        run_dict, variant_rows = load_run(results_conn, run_id)
        coverage_gaps = load_coverage_gaps(results_conn, run_id)
        combo_rows = load_combo_rule_hits(results_conn, run_id)
        sample_classifications = load_classifications(results_conn, run_id)

        project_conn = open_project_db(project)

        stored_fp = run_dict.get('project_fingerprint', '')
        if stored_fp:
            current_fp = compute_project_fingerprint(project_conn)
            if stored_fp != current_fp:
                raise click.ClickException(
                    f'Project database fingerprint mismatch for run #{run_id}.\n'
                    'The provided --project database does not match the one used for this run.\n'
                    'Ensure you are using the same project database that was active during profiling.'
                )
        else:
            logger.warning('Run #%d has no stored fingerprint — skipping project validation.', run_id)

        ref_row = project_conn.execute(
            'SELECT id, organism, length FROM reference WHERE name = ?',
            (run_dict['reference_name'],),
        ).fetchone()
        organism = ''
        reference_length_nt = 0
        ref_id = None
        if ref_row is not None:
            ref_id = int(ref_row['id'])
            organism = ref_row['organism'] or ''
            reference_length_nt = int(ref_row['length'] or 0)

        annotations = reconstruct_annotations(variant_rows)
        combo_hits = reconstruct_combo_rule_hits(combo_rows, annotations)
        result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=organism,
            reference_name=run_dict['reference_name'],
            reference_length_nt=reference_length_nt,
            sample_name=run_dict.get('sample_name', ''),
            vcf_name=run_dict['vcf_path'],
            run_timestamp=run_dict.get('created_at', ''),
            total_variants=run_dict.get('total_variants', 0),
            variants_in_cds=run_dict.get('variants_in_cds', 0),
            resistance_hits=run_dict.get('resistance_hits', 0),
            annotations=annotations,
            combo_hits=combo_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
        )

        genes = []
        rules = []
        rule_gene_names: set[str] = set()
        if ref_id is not None:
            genes = load_genes_for_reference(project_conn, ref_id)
            rules = load_rules(project_conn, ref_id)
            rule_gene_names = {rule.gene_name for rule in rules}

        with err_console.status(f'[dim]Regenerating run #{run_id}…[/dim]'):
            outputs = export_results(
                result,
                out,
                genes=genes,
                rule_gene_names=rule_gene_names,
                project_conn=project_conn,
                rules=rules,
            )

        console.print(Panel(
            f'{result.resistance_hits} database hit(s)',
            title=f'[green]✓ Regenerated run #{run_id}[/green]',
            border_style='green',
        ))
        for fmt, path in outputs.items():
            console.print(f'  [dim]{fmt}[/dim]   {path}')

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()
        if project_conn is not None:
            project_conn.close()


def register(app: typer.Typer) -> None:
    """Register the regenerate command on the given Typer app."""
    app.command()(regenerate)
