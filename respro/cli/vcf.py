"""VCF-based resistance profiling command — ``respro vcf``."""

from __future__ import annotations

import logging
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
from respro.core.combined_snp import BamCooccurrence
from respro.core.query import (
    pick_best_reference_id,
    resolve_fasta_query_multi,
    select_matches_for_reference,
)
from respro.core.vcf_coverage import (
    build_bam_cooccurrence_by_chrom,
    close_bam_cooccurrence_handles,
    compute_coverage_gaps_from_bam_multi,
)
from respro.core.vcf_to_reference.main import REFERENCE_DERIVED, map_vcf_to_references
from respro.db.schema import open_project_db
from respro.io.reference import read_fasta
from respro.io.vcf import collect_vcf_chroms, collect_vcf_header_contigs, parse_vcf
from respro.utils.cli_errors import cli_error, render_click_exception
from respro.utils.logging import err_console


def _resolve_vcf_chroms(vcf: Path, ref_fasta: Path) -> set[str]:
    """Return observed VCF CHROMs (falling back to header/FASTA for 0-variant VCFs) and verify coverage."""
    observed = collect_vcf_chroms(vcf)
    fasta_headers = set(read_fasta(ref_fasta).keys())
    if not observed:
        header_contigs = collect_vcf_header_contigs(vcf) & fasta_headers
        if len(fasta_headers) == 1:
            observed = set(fasta_headers)
        elif header_contigs:
            observed = header_contigs
        else:
            cli_error(
                'VCF contains no variant records; nothing to profile. '
                'Provide a VCF with at least one variant record.'
            )

    missing = sorted(observed - fasta_headers)
    if missing:
        cli_error(
            f'VCF CHROM(s) have no matching reference FASTA record: {", ".join(missing)}. '
            f'VCF CHROMs={sorted(observed)}, FASTA records={sorted(fasta_headers)}. '
            'Provide a reference FASTA whose record headers cover every VCF CHROM.'
        )
    return observed


def _profile_vcf_command(
    project: Annotated[
        Path, typer.Option('--project', '-p', exists=True, help='Project database.')
    ],
    vcf: Annotated[
        Path, typer.Option('--vcf', '-f', exists=True, help='Input VCF file.')
    ],
    ref_fasta: Annotated[
        Path | None,
        typer.Option('--ref-fasta', '-r', exists=True, help='Reference FASTA the VCF was called against.')
    ],
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
    min_af: Annotated[
        float, typer.Option('--min-af', '-ma', help='Minimum allele frequency filter.')
    ] = 0.01,
    min_depth: Annotated[
        int, typer.Option('--min-depth', '-md', help='Minimum read depth filter.')
    ] = 10,
    bam: Annotated[
        Path | None,
        typer.Option(
            '--bam', '-b', exists=True,
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
    """Run resistance profiling on a VCF file."""
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    project_conn = None
    results_conn = None

    try:
        if ref_fasta is None:
            cli_error('Missing option --ref-fasta.')

        export_formats = parse_export_formats(export)
        cfg = load_config_with_overrides(config)
        if config is not None:
            logger.info('Loaded configuration overrides from %s', config)

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            cli_error('No project found in the database')

        results_conn = init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )
        observed_chroms = _resolve_vcf_chroms(vcf, ref_fasta)

        with err_console.status('[dim]Aligning reference to internal references…[/dim]'):
            query_records = resolve_fasta_query_multi(
                project_conn, ref_fasta, use_cache=use_cache, threads=threads,
                selected_query_names=observed_chroms, cfg=cfg,
            )

        variants = parse_vcf(vcf, expected_query_name=None)
        logger.info('Parsed %d variant(s)', len(variants))
        variants = [
            v for v in variants
            if v.allele_freq >= min_af and (v.depth < 0 or v.depth >= min_depth)
        ]
        logger.info('%d variant(s) after AF/depth filtering', len(variants))

        variants, map_warnings, _ = map_vcf_to_references(variants, query_records)
        for warning in map_warnings:
            logger.warning(warning)
        logger.info('%d variant(s) after VCF reference comparison', len(variants))

        ref_derived = sum(1 for v in variants if v.source == REFERENCE_DERIVED)
        if ref_derived:
            logger.warning(
                '%d variant(s) come from differences between the supplied reference and '
                "the internal project reference (marked 'Reference derived' in the output)",
                ref_derived,
            )

        coverage_gaps = []
        bam_cooccurrence_by_chrom: dict[str, BamCooccurrence] | None = None
        if bam is not None:
            with err_console.status('[dim]Projecting BAM depth to internal CDS coordinates…[/dim]'):
                per_chrom = {
                    rec.query_name: (
                        rec.query_name,
                        rec.query_sequence,
                        select_matches_for_reference(
                            rec.feature_matches, pick_best_reference_id(rec.feature_matches),
                        ),
                    )
                    for rec in query_records
                }
                coverage_gaps = compute_coverage_gaps_from_bam_multi(
                    bam_path=bam,
                    per_chrom=per_chrom,
                    min_depth=min_depth,
                    bam_base_quality_threshold=cfg.codon.bam_base_quality_threshold,
                )
            if coverage_gaps:
                total_non_covered = sum(g.codon_end - g.codon_start + 1 for g in coverage_gaps)
                logger.warning(
                    '%d codon position(s) could not be assessed due to missing/low BAM coverage '
                    '(%d stretch(es), threshold=min-depth=%d)',
                    total_non_covered, len(coverage_gaps), min_depth,
                )
            bam_cooccurrence_by_chrom = build_bam_cooccurrence_by_chrom(
                bam_path=bam,
                per_chrom=per_chrom,
                min_mapq=cfg.codon.min_read_mapping_quality,
                min_depth=min_depth,
            )

        try:
            result = assemble_multi_reference_result(
                project_conn=project_conn,
                query_records=query_records,
                remapped_variants=variants,
                coverage_gaps=coverage_gaps,
                project_name=project_row['name'],
                sample=sample,
                vcf_name=input_display_name or vcf.name,
                total_variants=len(variants),
                af_bins=cfg.af_bins.as_dict(),
                coverage_assessed=bam is not None,
                bam_cooccurrence_by_chrom=bam_cooccurrence_by_chrom,
                cfg=cfg,
            )
        finally:
            close_bam_cooccurrence_handles(bam_cooccurrence_by_chrom)

        result, outputs = export_and_persist_result(
            result,
            project_conn=project_conn,
            input_basename=input_display_name or vcf.name,
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


def register(app: typer.Typer) -> None:
    """Register the ``vcf`` command on *app*."""
    app.command('vcf')(_profile_vcf_command)
