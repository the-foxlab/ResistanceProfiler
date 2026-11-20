"""FASTA-based resistance profiling command — ``respro fasta``."""

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
    assemble_multi_reference_result,
    export_and_persist_result,
    init_results_db_connection,
    parse_export_formats,
    print_completion_panel,
)
from respro.config.cli_settings import load_config_with_overrides
from respro.core.fasta_to_vcf import fasta_to_vcf
from respro.core.query import (
    QueryRecord,
    pick_best_reference_id,
    resolve_fasta_query,
    select_matches_for_reference,
)
from respro.db.rules_queries import get_project_example_fasta
from respro.db.schema import open_project_db
from respro.utils.cli_errors import cli_error, render_click_exception
from respro.utils.logging import err_console


def _materialize_example_fasta(project_conn, project_path: Path) -> Path:
    """Write the project's stored example FASTA to a temporary file and return its path."""
    example_text = get_project_example_fasta(project_conn)
    if example_text is None:
        cli_error(
            f'No example FASTA is stored in project database {project_path!s}. '
            'Add one with `respro init --example <fasta>`.'
        )
    temp_fd, temp_name = tempfile.mkstemp(prefix='respro_example_', suffix='.fasta')
    temp_path = Path(temp_name)
    os.close(temp_fd)
    temp_path.write_text(example_text)
    return temp_path


def _profile_fasta_command(
    project: Annotated[
        Path, typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    consensus_fasta: Annotated[
        Path | None, typer.Option('--fasta', '-f', exists=True, help='Input consensus FASTA sequence.')
    ] = None,
    use_example: Annotated[
        bool,
        typer.Option('--example', help='Profile the example consensus FASTA stored in the project database.')
    ] = False,
    sample: Annotated[
        str, typer.Option('--sample', '-s', help='Sample name for the report.')
    ] = 'sample',
    output: Annotated[
        Path, typer.Option('--output', '-o', help='Output path (directory or HTML file path).')
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
        typer.Option('--input-display-name', hidden=True, help='Optional display filename shown in exported reports.'),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            '--config', '-c', exists=True,
            help='User TOML overriding bundled defaults (scientific thresholds, alignment, AF bins, timeouts).',
        ),
    ] = None,
) -> None:
    """Run resistance profiling on a consensus FASTA sequence."""
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
        export_formats = parse_export_formats(export)
        cfg = load_config_with_overrides(config)
        if config is not None:
            logger.info('Loaded configuration overrides from %s', config)

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            cli_error('No project found in the database')

        if use_example:
            example_temp_path = _materialize_example_fasta(project_conn, project)
            consensus_fasta = example_temp_path
        assert consensus_fasta is not None

        results_conn = init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        with err_console.status('[dim]Aligning fasta sequence to internal references…[/dim]'):
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, consensus_fasta, use_cache=cache, threads=threads, cfg=cfg,
            )

        best_ref_id = pick_best_reference_id(fasta_matches)
        narrowed_matches = select_matches_for_reference(fasta_matches, best_ref_id)
        variants, coverage_gaps = fasta_to_vcf(query_seq, narrowed_matches)

        if coverage_gaps:
            total_non_covered = sum(g.codon_end - g.codon_start + 1 for g in coverage_gaps)
            logger.warning(
                '%d codon position(s) could not be assessed due to missing coverage '
                '(%d stretch(es): N-stretches and/or missing terminal sequence)',
                total_non_covered, len(coverage_gaps),
            )

        result = assemble_multi_reference_result(
            project_conn=project_conn,
            query_records=[QueryRecord(query_name, query_seq, narrowed_matches)],
            remapped_variants=variants,
            coverage_gaps=coverage_gaps,
            project_name=project_row['name'],
            sample=sample,
            vcf_name=input_display_name or consensus_fasta.name,
            total_variants=None,
            af_bins=cfg.af_bins_fasta.as_dict(),
            is_fasta_mode=True,
            coverage_assessed=True,
            cfg=cfg,
        )
        result, outputs = export_and_persist_result(
            result,
            project_conn=project_conn,
            input_basename=input_display_name or consensus_fasta.name,
            output_target=output,
            results_conn=results_conn,
            project_path=project,
            log=logger,
            extra_export_formats=export_formats,
            cfg=cfg,
        )
        print_completion_panel(console, '✓ Profiling complete', result, outputs)

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
    """Register the ``fasta`` command on *app*."""
    app.command('fasta')(_profile_fasta_command)
