"""
VCF-based resistance profiling command — respro vcf.
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
from respro.core.annotation import annotate_variants
from respro.core.query import resolve_fasta_query
from respro.core.vcf_coverage import compute_coverage_gaps_from_bam
from respro.core.vcf_remap import remap_variants
from respro.db.schema import open_project_db
from respro.io.vcf import parse_vcf
from respro.utils.logging import err_console


def _profile_vcf_command(
    project: Annotated[
        Path,
        typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    vcf: Annotated[
        Path,
        typer.Option('--vcf', '-f', exists=True, help='Input VCF file.')
    ],
    ref_fasta: Annotated[
        Path | None,
        typer.Option('--ref-fasta', '-r', exists=True, help='Reference FASTA the VCF was called against.')
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
    min_af: Annotated[
        float, typer.Option('--min-af', '-ma', help='Minimum allele frequency filter.')
    ] = 0.01,
    min_depth: Annotated[
        int, typer.Option('--min-depth', '-md', help='Minimum read depth filter.')
    ] = 10,
    bam: Annotated[
        Path | None,
        typer.Option(
            '--bam', exists=True,
            help='Optional BAM aligned against the same query reference as the VCF. '
                 'Used to mark non-covered codon stretches below --min-depth.',
        ),
    ] = None,
    threads: Annotated[
        int, typer.Option('--threads', '-th', help='Thread count for alignment calculations.')
    ] = 1,
    use_cache: Annotated[
        bool, typer.Option(
            '--cache/--no-cache',
            help='Reuse/store FASTA reference mapping cache in the project database (default: off).',
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
    Run resistance profiling on a VCF file.
    """
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    project_conn = None
    results_conn = None

    try:
        if ref_fasta is None:
            raise click.ClickException('Missing option --ref-fasta.')

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

        with err_console.status('[dim]Aligning reference to internal references…[/dim]'):
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, ref_fasta, use_cache=use_cache, threads=threads,
                aligner=aligner,  # type: ignore[arg-type]
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, formula_rules, rule_gene_names = _load_reference_data(project_conn, ref_id)

        variants = parse_vcf(vcf, expected_query_name=query_name)
        logger.info('Parsed %d variant(s)', len(variants))
        variants = [
            v for v in variants
            if v.allele_freq >= min_af and (v.depth < 0 or v.depth >= min_depth)
        ]
        logger.info('%d variant(s) after AF/depth filtering', len(variants))

        variants, remap_warnings = remap_variants(variants, fasta_matches, query_seq)
        for warning in remap_warnings:
            logger.warning(warning)
        logger.info('%d variant(s) after FASTA remapping', len(variants))

        coverage_gaps = []
        if bam is not None:
            with err_console.status('[dim]Projecting BAM depth to internal CDS coordinates…[/dim]'):
                coverage_gaps = compute_coverage_gaps_from_bam(
                    bam_path=bam,
                    query_name=query_name,
                    query_sequence=query_seq,
                    matches=fasta_matches,
                    min_depth=min_depth,
                )
            if coverage_gaps:
                total_non_covered = sum(gap.codon_end - gap.codon_start + 1 for gap in coverage_gaps)
                logger.warning(
                    '%d codon position(s) could not be assessed due to missing/low BAM coverage '
                    '(%d stretch(es), threshold=min-depth=%d)',
                    total_non_covered,
                    len(coverage_gaps),
                    min_depth,
                )
        annotations = annotate_variants(variants, genes)
        total_variants = len(variants)
        variants_in_cds = sum(1 for a in annotations if a.gene_name)

        result, outputs = _finalize_and_export(
            annotations=annotations,
            formula_rules=formula_rules,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_row['name'],
            ref_name=ref_name,
            sample=sample,
            input_basename=vcf.name,
            total_variants=total_variants,
            variants_in_cds=variants_in_cds,
            output_target=output,
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            results_conn=results_conn,
            project_path=project,
            logger=logger,
            query_sequence=query_seq,
            gene_matches=fasta_matches,
            coverage_gaps=coverage_gaps,
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
    """Register the vcf command on the given Typer app."""
    app.command('vcf')(_profile_vcf_command)
