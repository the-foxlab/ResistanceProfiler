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
    load_coverage_gaps,
    load_formula_rule_hits,
    load_run,
    load_run_from_json,
    reconstruct_annotations,
    reconstruct_formula_rule_hits,
    validate_project_fingerprint_match,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.io.reference import load_features_for_reference
from respro.report.non_html_exports import export_results
from respro.utils.files import resolve_output_file
from respro.utils.logging import err_console


def regenerate(
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
            '--output',
            '-o',
            help='Output path (directory or HTML file path).',
        ),
    ],
    result_db: Annotated[
        Path | None,
        typer.Option(
            '--results-db',
            '-d',
            exists=True,
            help='Results database.',
        ),
    ] = None,
    run_id: Annotated[
        int | None,
        typer.Option(
            '--run-id',
            '-i',
            help='Run ID to regenerate.',
        ),
    ] = None,
    json_input: Annotated[
        Path | None,
        typer.Option(
            '--json',
            '-j',
            exists=True,
            help='Results JSON export to regenerate from.',
        ),
    ] = None,
    export: Annotated[
        list[str] | None,
        typer.Option(
            '--export',
            help='Optional extra export format in addition to HTML (pdf, json, tabular). Pdfs are summaries only. Can be provided multiple times.',
        ),
    ] = None,
) -> None:
    """Regenerate a report from a stored run."""
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    results_conn = None
    project_conn = None

    try:
        extra_export_formats: set[str] = set()
        for raw_export in export or []:
            export_value = raw_export.strip().lower()
            if export_value not in ('json', 'tabular', 'pdf'):
                raise click.ClickException(
                    'Invalid --export value. Choose one of: json, tabular, pdf.'
                )
            extra_export_formats.add(export_value)

        if json_input is not None and (result_db is not None or run_id is not None):
            raise click.ClickException(
                'Use either --json OR (--results-db with --run-id), not both modes together.'
            )

        if json_input is None and (result_db is None or run_id is None):
            raise click.ClickException(
                'Missing input mode. Use --json or provide both --results-db and --run-id.'
            )

        if json_input is not None:
            run_dict, variant_rows, coverage_gaps, formula_rows, sample_classifications = load_run_from_json(
                json_input
            )
            run_label = f'JSON {json_input.name}'
            status_label = f'Regenerating {json_input.name}…'
        else:
            assert result_db is not None
            assert run_id is not None
            results_conn = open_results_db(result_db)
            run_dict, variant_rows = load_run(results_conn, run_id)
            coverage_gaps = load_coverage_gaps(results_conn, run_id)
            formula_rows = load_formula_rule_hits(results_conn, run_id)
            sample_classifications = load_classifications(results_conn, run_id)
            run_label = f'run #{run_id}'
            status_label = f'Regenerating run #{run_id}…'

        project_conn = open_project_db(project)

        stored_fp = run_dict.get('project_fingerprint', '')
        if stored_fp:
            current_fp = compute_project_fingerprint(project_conn)
            validate_project_fingerprint_match(
                stored_fingerprint=stored_fp,
                current_fingerprint=current_fp,
                source_label=run_label,
            )
        else:
            logger.warning('%s has no stored fingerprint — skipping project validation.', run_label)

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
        formula_hits = reconstruct_formula_rule_hits(formula_rows, annotations)
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
            formula_hits=formula_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
        )

        features = []
        rules = []
        rule_feature_names: set[str] = set()
        if ref_id is not None:
            features = load_features_for_reference(project_conn, ref_id)
            rules = load_rules(project_conn, ref_id)
            rule_feature_names = {rule.feature_name for rule in rules}

        default_stem = Path(run_dict['vcf_path']).stem.strip() or 'profile'
        html_output_path = resolve_output_file(out, f'{default_stem}.report.html')

        with err_console.status(f'[dim]{status_label}[/dim]'):
            outputs = export_results(
                result,
                html_output_path.parent,
                features=features,
                rule_feature_names=rule_feature_names,
                project_conn=project_conn,
                rules=rules,
                extra_export_formats=extra_export_formats,
                output_html_path=html_output_path,
            )

        console.print(Panel(
            f'{result.resistance_hits} database hit(s)',
            title=f'[green]✓ Regenerated {run_label}[/green]',
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
