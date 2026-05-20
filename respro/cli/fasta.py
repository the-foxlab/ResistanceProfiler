"""
FASTA-based resistance profiling command — respro fasta.
"""

from __future__ import annotations

import logging
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
from respro.core.annotation import annotate_variants
from respro.core.fasta_to_vcf import fasta_to_vcf
from respro.core.query import resolve_fasta_query
from respro.db.schema import open_project_db
from respro.utils.logging import err_console


def _profile_fasta_command(
    project: Annotated[
        Path,
        typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    consensus_fasta: Annotated[
        Path,
        typer.Option('--fasta', '-f', exists=True, help='Input consensus FASTA sequence.')
    ],
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
    min_identity: Annotated[
        float,
        typer.Option(
            '--min-identity', '-mi', min=0.0, max=1.0,
            help='Minimum nucleotide identity for FASTA-to-reference matching (0-1).',
        )
    ] = 0.9,
    export: Annotated[
        list[str] | None,
        typer.Option(
            '--export',
            help='Optional extra export format in addition to HTML (pdf, json, tabular). Can be provided multiple times.',
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

    try:
        if min_identity <= 0.75:
            logger.warning(
                'Low min-identity threshold (%.2f) may increase mismatches and false-positive mappings.',
                min_identity,
            )

        export_formats = _parse_export_formats(export)

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        results_conn = _init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        with err_console.status('[dim]Aligning fasta sequence to internal references…[/dim]'):
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, consensus_fasta, use_cache=cache, threads=threads,
                min_identity=min_identity,
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
        fasta_af_bins = {
            'high': (0.75, 1.0),
            'intermediate': (0.35, 0.74),
            'low': (0.01, 0.34),
        }

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

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()
        if results_conn is not None:
            results_conn.close()


def register(app: typer.Typer) -> None:
    """Register the fasta command on the given Typer app."""
    app.command('fasta')(_profile_fasta_command)
