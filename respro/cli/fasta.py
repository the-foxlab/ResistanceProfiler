"""
FASTA-based resistance profiling command — respro fasta.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console

from respro.cli.profile_helpers import (
    _finalize_and_export,
    _init_results_db_connection,
    _load_reference_data,
    _parse_export_formats,
    _print_completion_panel,
    _ProfilingRunContext,
    _resolve_reference,
)
from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import annotate_variants
from respro.core.fasta_to_vcf import fasta_to_vcf
from respro.core.query import resolve_fasta_query
from respro.db.rules_queries import get_project_example_fasta
from respro.db.schema import open_project_db
from respro.utils.cli_errors import cli_error, render_click_exception
from respro.utils.logging import err_console


def _profile_fasta_command(
    project: Annotated[
        Path,
        typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    consensus_fasta: Annotated[
        Path | None,
        typer.Option('--fasta', '-f', exists=True, help='Input consensus FASTA sequence.')
    ] = None,
    use_example: Annotated[
        bool,
        typer.Option(
            '--example',
            help='Profile the example consensus FASTA stored in the project database.',
        )
    ] = False,
    sample: Annotated[
        str,
        typer.Option('--sample', '-s', help='Sample name for the report.')
    ] = 'sample',
    output: Annotated[
        Path,
        typer.Option('--output', '-o', help='Output path (directory or HTML file path).')
    ] = Path('output'),
    results_db: Annotated[
        Path | None,
        typer.Option(
            '--results-db', '-d',
            help='Optional results database path. Creates or appends to an existing SQLite results database.',
        )
    ] = None,
    threads: Annotated[
        int, typer.Option('--threads', '-th', help='Thread count for alignment calculations.')
    ] = 1,
    cache: Annotated[
        bool, typer.Option(
            '--cache/--no-cache',
            help='Cache FASTA reference mapping in the project database for report regeneration (default: off).',
        )
    ] = False,
    export: Annotated[
        list[str] | None,
        typer.Option(
            '--export', '-e',
            help='Optional extra export format in addition to HTML (pdf, json, tsv). Pdfs are summaries only. Can be provided multiple times.',
        ),
    ] = None,
    input_display_name: Annotated[
        str | None,
        typer.Option(
            '--input-display-name',
            hidden=True,
            help='Optional display filename shown in exported reports.',
        ),
    ] = None,
) -> None:
    """
    Run resistance profiling on a consensus FASTA sequence.
    """
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    project_conn = None
    results_conn = None

    if use_example and consensus_fasta is not None:
        cli_error('Provide either --fasta or --example, not both.')
    if not use_example and consensus_fasta is None:
        cli_error('Provide --fasta or --example to specify the input consensus sequence.')

    example_temp_path: Path | None = None
    try:
        export_formats = _parse_export_formats(export)

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            cli_error('No project found in the database')

        # When --example is used, materialise the stored example FASTA text into a temp file so the
        # existing read/align pipeline is reused unchanged.
        if use_example:
            example_text = get_project_example_fasta(project_conn)
            if example_text is None:
                cli_error(
                    f'No example FASTA is stored in project database {project!s}. '
                    'Add one with `respro init --example <fasta>`.'
                )
            assert example_text is not None
            temp_fd, temp_name = tempfile.mkstemp(prefix='respro_example_', suffix='.fasta')
            example_temp_path = Path(temp_name)
            os.close(temp_fd)
            example_temp_path.write_text(example_text)
            consensus_fasta = example_temp_path

        # cli_error raises typer.Exit, but mypy cannot infer that; narrow explicitly.
        assert consensus_fasta is not None

        results_conn = _init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        with err_console.status('[dim]Aligning fasta sequence to internal references…[/dim]'):
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, consensus_fasta, use_cache=cache, threads=threads,
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        features, rules, formula_rules, rule_feature_names = _load_reference_data(project_conn, ref_id)

        variants, coverage_gaps = fasta_to_vcf(query_seq, fasta_matches)
        annotations = annotate_variants(variants, features, is_fasta_mode=True)

        if coverage_gaps:
            total_non_covered = sum(gap.codon_end - gap.codon_start + 1 for gap in coverage_gaps)
            logger.warning(
                '%d codon position(s) could not be assessed due to missing coverage '
                '(%d stretch(es): N-stretches and/or missing terminal sequence)',
                total_non_covered, len(coverage_gaps),
            )

        # FASTA mode frequencies are discrete (1.0, 0.5, 0.33, 0.25) from IUPAC expansion.
        # Bin thresholds are adjusted to reflect these values cleanly.
        fasta_af_bins = CLI_CONFIG.af_bins_fasta.as_dict()

        ctx = _ProfilingRunContext(
            annotations=annotations,
            formula_rules=formula_rules,
            features=features,
            rule_feature_names=rule_feature_names,
            rules=rules,
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            coverage_gaps=coverage_gaps or [],
            query_sequence=query_seq,
            feature_matches=fasta_matches or [],
            af_bins=fasta_af_bins,
        )
        result, outputs = _finalize_and_export(
            ctx=ctx,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_row['name'],
            ref_name=ref_name,
            sample=sample,
            input_basename=input_display_name or consensus_fasta.name,
            output_target=output,
            results_conn=results_conn,
            project_path=project,
            logger=logger,
            extra_export_formats=export_formats,
        )

        _print_completion_panel(console, '✓ Profiling complete', result, outputs)

    except click.ClickException as exc:
        render_click_exception(exc)
    except (FileNotFoundError, ValueError) as exc:
        cli_error(str(exc))
    finally:
        if project_conn is not None:
            project_conn.close()
        if results_conn is not None:
            results_conn.close()
        if example_temp_path is not None:
            example_temp_path.unlink(missing_ok=True)


def register(app: typer.Typer) -> None:
    """Register the fasta command on the given Typer app."""
    app.command('fasta')(_profile_fasta_command)
