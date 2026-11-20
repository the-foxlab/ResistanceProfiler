"""``respro regenerate`` command — rebuild reports from a stored run or results JSON export."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.panel import Panel

from respro.cli.profile_helpers import (
    parse_export_formats,
    reconstruct_reference_groups,
    safe_report_stem,
)
from respro.config.cli_settings import load_config_with_overrides
from respro.db.models import CoverageGap, ProfilingResult
from respro.db.results import (
    load_classifications,
    load_coverage_gaps,
    load_formula_rule_hits,
    load_profiled_features,
    load_run,
    load_run_from_json,
    reconstruct_annotations,
    reconstruct_formula_rule_hits,
    validate_project_fingerprint_match,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.report.non_html_exports import export_results
from respro.utils.cli_errors import cli_error, render_click_exception
from respro.utils.files import resolve_output_file
from respro.utils.logging import err_console


def _collect_profiled_features_from_json(json_references: list[dict]) -> dict[str, list[str]]:
    """Aggregate ``profiled_feature_names`` per reference name from a JSON export."""
    by_ref: dict[str, list[str]] = {}
    for ref in json_references:
        name = ref.get('reference_name', '')
        names = ref.get('profiled_feature_names') or []
        if not name or not names:
            continue
        existing = by_ref.setdefault(name, [])
        for fname in names:
            if fname not in existing:
                existing.append(fname)
    return by_ref


def _load_stored_run_payload(
    *,
    json_input: Path | None,
    result_db: Path | None,
    run_id: int | None,
) -> tuple[
    sqlite3.Connection | None,
    dict,
    list[dict],
    list[CoverageGap],
    list[dict],
    list[dict],
    dict[str, list[str]],
    str,
    str,
]:
    """Load run metadata, rows, coverage gaps, formula hits, classifications, and profiled features."""
    if json_input is not None:
        run_dict, variant_rows, coverage_gaps, formula_rows, classifications = load_run_from_json(
            json_input,
        )
        profiled = _collect_profiled_features_from_json(run_dict.get('references') or [])
        return (
            None, run_dict, variant_rows, coverage_gaps, formula_rows,
            classifications, profiled, f'JSON {json_input.name}', f'Regenerating {json_input.name}…',
        )

    assert result_db is not None and run_id is not None
    results_conn = open_results_db(result_db)
    try:
        run_dict, variant_rows = load_run(results_conn, run_id)
        coverage_gaps = load_coverage_gaps(results_conn, run_id)
        formula_rows = load_formula_rule_hits(results_conn, run_id)
        classifications = load_classifications(results_conn, run_id)
        profiled = load_profiled_features(results_conn, run_id)
    except Exception:
        results_conn.close()
        raise
    return (
        results_conn, run_dict, variant_rows, coverage_gaps, formula_rows,
        classifications, profiled, f'run #{run_id}', f'Regenerating run #{run_id}…',
    )


def regenerate(
    project: Annotated[
        Path, typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    out: Annotated[
        Path, typer.Option('--output', '-o', help='Output path (directory or HTML file path).')
    ],
    result_db: Annotated[
        Path | None, typer.Option('--results-db', '-d', exists=True, help='Results database.')
    ] = None,
    run_id: Annotated[
        int | None, typer.Option('--run-id', '-i', help='Run ID to regenerate.')
    ] = None,
    json_input: Annotated[
        Path | None, typer.Option('--json', '-j', exists=True, help='Results JSON export to regenerate from.')
    ] = None,
    export: Annotated[
        list[str] | None,
        typer.Option(
            '--export', '-e',
            help='Optional extra export format in addition to HTML (pdf, json, tsv). Pdfs are summaries only. Can be provided multiple times.',
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            '--config', '-c', exists=True,
            help='User TOML overriding bundled defaults (scientific thresholds, alignment, AF bins, timeouts). '
                 'Only report-stage keys (AF bins, similarity, AF-threshold labels) affect regenerate; '
                 'alignment/codon overrides are accepted but have no effect.',
        ),
    ] = None,
) -> None:
    """Regenerate a report from a stored run."""
    log = logging.getLogger('respro')
    console = Console(highlight=False)
    results_conn = None
    project_conn = None

    try:
        extra_export_formats = parse_export_formats(export) or set()
        cfg = load_config_with_overrides(config)
        if config is not None:
            log.info('Loaded configuration overrides from %s', config)

        if json_input is not None and (result_db is not None or run_id is not None):
            cli_error('Use either --json OR (--results-db with --run-id), not both modes together.')
        if json_input is None and (result_db is None or run_id is None):
            cli_error('Missing input mode. Use --json or provide both --results-db and --run-id.')

        (
            results_conn, run_dict, variant_rows, coverage_gaps, formula_rows,
            sample_classifications, profiled_by_ref, run_label, status_label,
        ) = _load_stored_run_payload(json_input=json_input, result_db=result_db, run_id=run_id)

        project_conn = open_project_db(project)
        stored_fp = run_dict.get('project_fingerprint', '')
        if stored_fp:
            validate_project_fingerprint_match(
                stored_fingerprint=stored_fp,
                current_fingerprint=compute_project_fingerprint(project_conn),
                source_label=run_label,
            )
        else:
            log.warning('%s has no stored fingerprint — skipping project validation.', run_label)

        annotations = reconstruct_annotations(variant_rows)
        formula_hits = reconstruct_formula_rule_hits(formula_rows, annotations)
        references, all_features, all_rules, all_rule_feature_names, primary_organism = (
            reconstruct_reference_groups(project_conn, run_dict, variant_rows, profiled_by_ref)
        )

        is_fasta_mode = bool(run_dict.get('is_fasta_mode', 0))
        result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=primary_organism,
            sample_name=run_dict.get('sample_name', ''),
            vcf_name=run_dict['vcf_path'],
            run_timestamp=run_dict.get('created_at', ''),
            total_variants=run_dict.get('total_variants', 0),
            variants_in_cds=run_dict.get('variants_in_cds', 0),
            resistance_hits=run_dict.get('resistance_hits', 0),
            is_fasta_mode=is_fasta_mode,
            coverage_assessed=bool(run_dict.get('coverage_assessed', 0)) or is_fasta_mode,
            annotations=annotations,
            formula_hits=formula_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
            references=references,
        )

        html_output_path = resolve_output_file(out, f"{safe_report_stem(run_dict['vcf_path'])}.report.html")
        with err_console.status(f'[dim]{status_label}[/dim]'):
            outputs = export_results(
                result,
                html_output_path.parent,
                features=all_features,
                rule_feature_names=all_rule_feature_names,
                project_conn=project_conn,
                rules=all_rules,
                extra_export_formats=extra_export_formats,
                output_html_path=html_output_path,
                similarity_high=cfg.similarity.high,
                similarity_moderate=cfg.similarity.moderate,
                cfg=cfg,
            )

        console.print(Panel(
            f'{result.resistance_hits} database hit(s)',
            title=f'[green]✓ Regenerated {run_label}[/green]',
            border_style='green',
        ))
        for fmt, path in outputs.items():
            console.print(f'  [dim]{fmt}[/dim]   {path}')

    except click.ClickException as exc:
        render_click_exception(exc)
    except (FileNotFoundError, ValueError) as exc:
        cli_error(str(exc))
    finally:
        if results_conn is not None:
            results_conn.close()
        if project_conn is not None:
            project_conn.close()


def register(app: typer.Typer) -> None:
    """Register the ``regenerate`` command on *app*."""
    app.command()(regenerate)
