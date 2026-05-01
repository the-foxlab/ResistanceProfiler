"""
FASTA-based resistance profiling command — respro fasta.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

import click
import typer
from rich.console import Console

from respro.cli.profile_helpers import (
    _finalize_and_export,
    _init_results_db_connection,
    _load_reference_data,
    _print_completion_panel,
    _resolve_reference,
)
from respro.core.fasta_profile import profile_fasta_consensus
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
    aligner: Annotated[
        Literal['mappy', 'pairwise'],
        typer.Option(
            '--aligner', '-a',
            help="Alignment backend for query FASTA matching: 'pairwise' (Biopython) or 'mappy' (minimap2, default). Mappy is faster for long references.",
        )
    ] = 'mappy',
    export: Annotated[
        Literal['json', 'tabular', 'pdf'] | None,
        typer.Option(
            '--export',
            help='Optional extra export format in addition to HTML.',
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
        if aligner not in ('pairwise', 'mappy'):
            raise click.ClickException(f"Unknown aligner {aligner!r}; choose 'pairwise' or 'mappy'.")

        export_format: str | None = None
        if export is not None:
            export_format = export.strip().lower()
            if export_format not in ('json', 'tabular', 'pdf'):
                raise click.ClickException(
                    'Invalid --export value. Choose one of: json, tabular, pdf.'
                )

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
                aligner=aligner,  # type: ignore[arg-type]
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, formula_rules, rule_gene_names = _load_reference_data(project_conn, ref_id)

        annotations, coverage_gaps = profile_fasta_consensus(query_seq, fasta_matches)

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

        result, outputs = _finalize_and_export(
            annotations=annotations,
            formula_rules=formula_rules,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_row['name'],
            ref_name=ref_name,
            sample=sample,
            input_basename=consensus_fasta.name,
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            output_target=output,
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            results_conn=results_conn,
            project_path=project,
            logger=logger,
            af_bins=fasta_af_bins,
            coverage_gaps=coverage_gaps,
            query_sequence=query_seq,
            gene_matches=fasta_matches,
            extra_export_format=export_format,
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
