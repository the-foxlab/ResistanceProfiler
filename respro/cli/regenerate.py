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

from respro.config.cli_settings import CLI_CONFIG
from respro.db.features import load_features_for_reference
from respro.db.models import ProfilingResult, ReferenceGroup
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
from respro.db.rules_queries import load_rules
from respro.db.schema import open_project_db, open_results_db
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
            help='Optional extra export format in addition to HTML (pdf, json). Pdfs are summaries only. Can be provided multiple times.',
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
            if export_value not in ('json', 'pdf'):
                raise click.ClickException(
                    'Invalid --export value. Choose one of: json, pdf.'
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

        annotations = reconstruct_annotations(variant_rows)
        formula_hits = reconstruct_formula_rule_hits(formula_rows, annotations)

        # Determine the distinct reference names stored for this run. Multi-reference
        # runs persist one reference_name per variant_result row (results DB schema v2)
        # or a top-level `references` list (JSON export). Single-reference and legacy
        # runs fall back to run_dict['reference_name'].
        #
        # Also recover each reference's query_name (the original VCF CHROM / FASTA header).
        # The live VCF path sets ReferenceGroup.query_name to the CHROM, and the report
        # attributes per-row reference_name and scopes the multi-reference lollipop plot
        # by mapping annotation.variant.chrom -> reference_name via query_name. Regenerate
        # must restore the same query_name, otherwise the multi-species Reference column
        # renders '—' for every row and the multi-reference lollipop plot is dropped
        # (its chrom scoping finds no annotations). The stored query_name comes from the
        # JSON `references` payload; for the results-DB path it is derived from the chrom
        # stored on each variant_result row, grouped by reference_name. Legacy runs without
        # a stored chrom fall back to the reference_name (single-reference reports do not
        # render the Reference column or use chrom scoping, so the fallback is safe).
        reference_names: list[str] = []
        seen: set[str] = set()
        query_name_by_reference_name: dict[str, str] = {}
        json_references = run_dict.get('references') or []
        if json_references:
            for ref in json_references:
                name = ref.get('reference_name', '')
                if name and name not in seen:
                    seen.add(name)
                    reference_names.append(name)
                    query_name_by_reference_name[name] = ref.get('query_name', '') or name
        else:
            for row in variant_rows:
                name = row.get('reference_name', '') or run_dict.get('reference_name', '')
                chrom = row.get('chrom', '') or ''
                if name and name not in seen:
                    seen.add(name)
                    reference_names.append(name)
                    query_name_by_reference_name[name] = chrom or name
                elif name and chrom and not query_name_by_reference_name.get(name):
                    query_name_by_reference_name[name] = chrom
        if not reference_names:
            fallback = run_dict.get('reference_name', '')
            reference_names = [fallback]
            query_name_by_reference_name[fallback] = fallback

        # Build one ReferenceGroup per distinct reference, accumulating the union of
        # features/rules/rule_feature_names for export_results (matches the live
        # multi-reference assembly path in profile_helpers._finalize_and_export_multi).
        references: list[ReferenceGroup] = []
        all_features: list = []
        all_rules: list = []
        all_rule_feature_names: set[str] = set()
        primary_organism = ''
        for ref_name in reference_names:
            ref_row = project_conn.execute(
                'SELECT id, organism, length FROM reference WHERE name = ?',
                (ref_name,),
            ).fetchone()
            organism = ''
            reference_length_nt = 0
            ref_id = None
            if ref_row is not None:
                ref_id = int(ref_row['id'])
                organism = ref_row['organism'] or ''
                reference_length_nt = int(ref_row['length'] or 0)
            if not primary_organism:
                primary_organism = organism

            ref_features: list = []
            ref_rules: list = []
            ref_rule_feature_names: set[str] = set()
            if ref_id is not None:
                ref_features = load_features_for_reference(project_conn, ref_id)
                ref_rules = load_rules(project_conn, ref_id)
                ref_rule_feature_names = {rule.feature_name for rule in ref_rules}
            all_features.extend(ref_features)
            all_rules.extend(ref_rules)
            all_rule_feature_names |= ref_rule_feature_names

            references.append(ReferenceGroup(
                reference_name=ref_name,
                reference_id=ref_id if ref_id is not None else 0,
                organism=organism,
                reference_length_nt=reference_length_nt,
                query_name=query_name_by_reference_name.get(ref_name, ref_name),
                query_sequence='',
                feature_matches=[],
                features=ref_features,
                rules=ref_rules,
                rule_feature_names=ref_rule_feature_names,
            ))

        result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=primary_organism,
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
            references=references,
        )

        default_stem = Path(run_dict['vcf_path']).stem.strip() or 'profile'
        html_output_path = resolve_output_file(out, f'{default_stem}.report.html')

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
                similarity_high=CLI_CONFIG.similarity.high,
                similarity_moderate=CLI_CONFIG.similarity.moderate,
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
