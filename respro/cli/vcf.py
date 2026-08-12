"""
VCF-based resistance profiling command — respro vcf.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console

from respro.cli.profile_helpers import (
    _finalize_and_export_multi,
    _init_results_db_connection,
    _parse_export_formats,
    _print_completion_panel,
    assemble_multi_reference_result,
)
from respro.config.cli_settings import CLI_CONFIG
from respro.core.query import (
    pick_best_reference_id,
    resolve_fasta_query_multi,
    select_matches_for_reference,
)
from respro.core.vcf_coverage import compute_coverage_gaps_from_bam_multi
from respro.core.vcf_remap import route_and_remap_variants
from respro.db.schema import open_project_db
from respro.io.reference import read_fasta
from respro.io.vcf import collect_vcf_chroms, parse_vcf
from respro.utils.cli_errors import cli_error, render_click_exception
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
    export: Annotated[
        list[str] | None,
        typer.Option(
            '--export', '-e',
            help='Optional extra export format in addition to HTML (pdf, json). Pdfs are summaries only. Can be provided multiple times.',
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
    Run resistance profiling on a VCF file.
    """
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    project_conn = None
    results_conn = None

    try:
        if ref_fasta is None:
            cli_error('Missing option --ref-fasta.')
        assert ref_fasta is not None  # cli_error raises typer.Exit above

        export_formats = _parse_export_formats(export)

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            cli_error('No project found in the database')

        results_conn = _init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        # Preflight: every VCF CHROM observed in variant records must have an exact
        # FASTA record header. Extra FASTA records are allowed (ignored in VCF mode),
        # but a VCF CHROM without a supplied reference cannot be remapped and indicates
        # the wrong reference file was provided — a hard failure rather than a silent drop.
        observed_chroms = collect_vcf_chroms(vcf)
        fasta_headers = set(read_fasta(ref_fasta).keys())
        missing = sorted(observed_chroms - fasta_headers)
        if missing:
            cli_error(
                'VCF CHROM(s) have no matching reference FASTA record: '
                f'{", ".join(missing)}. '
                f'VCF CHROMs={sorted(observed_chroms)}, '
                f'FASTA records={sorted(fasta_headers)}. '
                'Provide a reference FASTA whose record headers cover every VCF CHROM.'
            )

        with err_console.status('[dim]Aligning reference to internal references…[/dim]'):
            query_records = resolve_fasta_query_multi(
                project_conn, ref_fasta, use_cache=use_cache, threads=threads,
                selected_query_names=observed_chroms,
            )

        # Parse all CHROMs (expected_query_name=None) so multi-chrom VCFs are retained;
        # route_and_remap_variants pairs each CHROM with its matching QueryRecord.
        variants = parse_vcf(vcf, expected_query_name=None)
        logger.info('Parsed %d variant(s)', len(variants))
        variants = [
            v for v in variants
            if v.allele_freq >= min_af and (v.depth < 0 or v.depth >= min_depth)
        ]
        logger.info('%d variant(s) after AF/depth filtering', len(variants))

        variants, remap_warnings, dropped_chroms = route_and_remap_variants(variants, query_records)
        for warning in remap_warnings:
            logger.warning(warning)
        for chrom in dropped_chroms:
            logger.warning(
                'Dropped VCF CHROM %r: its reference FASTA record has no usable internal '
                'feature mapping; variants were not remapped',
                chrom,
            )
        logger.info('%d variant(s) after FASTA remapping', len(variants))

        coverage_gaps = []
        if bam is not None:
            with err_console.status('[dim]Projecting BAM depth to internal CDS coordinates…[/dim]'):
                # Narrow each query record's matches to its best reference (same narrowing
                # applied in route_and_remap_variants) so coverage is projected onto only
                # the selected reference's features, avoiding cross-reference duplication.
                per_chrom = {}
                for rec in query_records:
                    ref_id = pick_best_reference_id(rec.feature_matches)
                    narrowed = select_matches_for_reference(rec.feature_matches, ref_id)
                    per_chrom[rec.query_name] = (rec.query_name, rec.query_sequence, narrowed)
                coverage_gaps = compute_coverage_gaps_from_bam_multi(
                    bam_path=bam,
                    per_chrom=per_chrom,
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

        result = assemble_multi_reference_result(
            project_conn=project_conn,
            query_records=query_records,
            remapped_variants=variants,
            coverage_gaps=coverage_gaps or [],
            project_name=project_row['name'],
            sample=sample,
            vcf_name=input_display_name or vcf.name,
            total_variants=len(variants),
            af_bins=CLI_CONFIG.af_bins.as_dict(),
        )

        result, outputs = _finalize_and_export_multi(
            result=result,
            project_conn=project_conn,
            sample=sample,
            input_basename=input_display_name or vcf.name,
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


def register(app: typer.Typer) -> None:
    """Register the vcf command on the given Typer app."""
    app.command('vcf')(_profile_vcf_command)
