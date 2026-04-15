"""
CLI entry point for ResistanceProfiler.

Commands:
- respro init          — initialise a GenBank-backed project database
- respro init-add      — add rules and optional GenBank annotations to an existing project
- respro profile-vcf   — run resistance profiling on a VCF file
- respro profile-fasta — run resistance profiling on a consensus FASTA
- respro regenerate    — list stored results or regenerate a report from a results database
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import click
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from respro import __version__
from respro.cli_helpers import (
    _finalize_and_export,
    _init_results_db_connection,
    _load_reference_data,
    _resolve_reference,
)
from respro.core.annotation import annotate_variants
from respro.core.fasta_profile import profile_fasta_consensus
from respro.core.query import resolve_cached_query_reference, resolve_fasta_query
from respro.core.rules import load_rules
from respro.core.vcf_coverage import compute_coverage_gaps_from_bam
from respro.core.vcf_remap import remap_variants
from respro.db.models import ProfilingResult
from respro.db.project import add_to_project, init_project
from respro.db.results import (
    list_runs,
    load_combo_rule_hits,
    load_coverage_gaps,
    load_run,
    reconstruct_annotations,
    reconstruct_combo_rule_hits,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db, open_results_db
from respro.io.reference import load_genes_for_reference
from respro.io.vcf import parse_vcf
from respro.report.html import export_results
from respro.utils.logging import err_console, setup_logging

app = typer.Typer(
    help='ResistanceProfiler — agnostic antiviral resistance profiling framework.',
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


# ──────────────────────────────────────────────────────────────────────
# Shared output helpers
# ──────────────────────────────────────────────────────────────────────

def _print_completion_panel(console: Console, title: str, result, outputs: dict) -> None:
    """Render a summary panel after a profiling run."""
    hit_line = f'{result.resistance_hits} database hit(s)'
    if hasattr(result, 'combo_hits') and result.combo_hits:
        hit_line += f'  ·  {len(result.combo_hits)} combo rule hit(s)'
    lines = [hit_line, '']
    for fmt, path in outputs.items():
        lines.append(f'[dim]{fmt}[/dim]   {path}')
    console.print(Panel('\n'.join(lines), title=f'[green]{title}[/green]', border_style='green'))


def _print_runs_table(console: Console, runs: list) -> None:
    """Render stored runs as a Rich table."""
    table = Table(box=box.SIMPLE, header_style='bold cyan', show_edge=False)
    table.add_column('ID', justify='right', style='dim', no_wrap=True)
    table.add_column('Sample')
    table.add_column('Reference')
    table.add_column('Input')
    table.add_column('Hits', justify='right')
    table.add_column('Created', style='dim')
    for run in runs:
        table.add_row(
            str(run['id']),
            run['sample_name'] or '',
            run['reference_name'],
            Path(run['vcf_path']).name,
            str(run['resistance_hits']),
            run['created_at'],
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────
# Global callback — version + verbosity
# ──────────────────────────────────────────────────────────────────────

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f'respro {__version__}')
        raise typer.Exit()


@app.callback()
def _callback(
    verbose: Annotated[
        int, typer.Option(
            '--verbose',
            '-v',
            count=True,
            metavar='',
        show_default=False,
        help='Increase verbosity (-v info, -vv debug).'
        )
    ] = 0,

    version: Annotated[
        bool | None, typer.Option(
        '--version',
            callback=_version_callback,
            is_eager=True,
            help='Show version and exit.'
        )
    ] = None

) -> None:
    setup_logging(verbose)


# ──────────────────────────────────────────────────────────────────────
# init
# ──────────────────────────────────────────────────────────────────────

@app.command()
def init(
    name: Annotated[
        str, typer.Option(
            '--name',
            '-n',
            help='Project name.'
        )
    ],

    rules: Annotated[
        Path, typer.Option(
            '--rules',
            '-r',
            help='Resistance rules TSV.'
        )
    ],

    output: Annotated[
        Path, typer.Option(
            '--output',
            '-o',
            help='Output SQLite database path.'
        )
    ],

    genbank_paths: Annotated[
        list[Path] | None, typer.Option(
            '--genbank',
            '-g',
            exists=True,
            help='GenBank file(s). Repeat for multiple files.',
        )
    ] = None,

    overwrite: Annotated[
        bool, typer.Option(
            '--overwrite',
            help='Overwrite existing database.'
        )
    ] = False,

    additional_info: Annotated[
        bool, typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.'
        )
    ] = True,

) -> None:
    """
    Initialise a project database from one or more GenBank reference records and resistance rules provided in TSV.
    """
    if not genbank_paths:
        raise click.UsageError('At least one --genbank file is required.')

    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Initialising project database…[/dim]'):
            db_path = init_project(
                db_path=output,
                name=name,
                genbank_paths=list(genbank_paths),
                rules_tsv=rules,
                overwrite=overwrite,
                additional_info=additional_info,
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f'[green]✓[/green] Project initialised: [cyan]{db_path}[/cyan]')


# ──────────────────────────────────────────────────────────────────────
# init-add
# ──────────────────────────────────────────────────────────────────────

@app.command('init-add')
def init_add(
    project: Annotated[
        Path,
        typer.Option(
            '--project',
            '-p',
            exists=True,
            help='Existing project SQLite database.'
        )
    ],

    rules: Annotated[
        Path,
        typer.Option(
            '--rules',
            '-r',
            exists=True,
            help='Resistance rules TSV to add.'
        )
    ],

    genbank_paths: Annotated[
        list[Path] | None,
        typer.Option(
            '--genbank',
            '-g',
            exists=True,
            help='Optional GenBank file(s) with additional references/genes.',
        )
    ] = None,

    additional_info: Annotated[
        bool,
        typer.Option(
            '--additional-info/--no-additional-info',
            help='Query PubChem for drug metadata and resolve publications via NCBI/CrossRef.',
        )
    ] = True
) -> None:
    """
    Add curated rules and optional GenBank annotations to an existing project database.
    """
    console = Console(highlight=False)
    try:
        with err_console.status('[dim]Updating project database…[/dim]'):
            db_path = add_to_project(
                db_path=project,
                genbank_paths=list(genbank_paths or []),
                rules_tsv=rules,
                additional_info=additional_info,
            )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f'[green]✓[/green] Project updated: [cyan]{db_path}[/cyan]')


# ──────────────────────────────────────────────────────────────────────
# profile-vcf
# ──────────────────────────────────────────────────────────────────────

@app.command('profile-vcf')
def profile_vcf(
    project: Annotated[
        Path,
        typer.Option(
            '--project',
            '-p',
            exists=True,
            help='Project database.'
        )
    ],

    vcf: Annotated[
        Path,
        typer.Option(
            '--vcf',
            '-f',
            exists=True,
            help='Input VCF file.'
        )
    ],

    ref_fasta: Annotated[
        Path | None,
        typer.Option(
            '--ref-fasta',
            '-r',
            exists=True,
            help='Reference FASTA the VCF was called against (mutually exclusive with --query-ref-header).'
        )
    ] = None,

    query_ref_header: Annotated[
        str | None,
        typer.Option(
            '--query-ref-header',
            '-q',
            help='Reuse a previously cached query reference by its stored FASTA header (mutually exclusive with --ref-fasta).'
        )
    ] = None,

    sample: Annotated[
        str,
        typer.Option(
            '--sample',
            '-s',
            help='Sample name for the report.'
        )
    ] = 'sample',

    output: Annotated[
        Path,
        typer.Option(
            '--output',
            '-o',
            help='Output directory.'
        )
    ] = Path('output'),

    results_db: Annotated[
        Path | None,
        typer.Option(
            '--results-db',
            '-d',
            help='Optional results database path. Creates or appends to an existing SQLite results database.'
        )
    ] = None,

    min_af: Annotated[
        float, typer.Option(
            '--min-af',
            '-ma',
            help='Minimum allele frequency filter.'
        )
    ] = 0.01,

    min_depth: Annotated[
        int,
        typer.Option(
            '--min-depth',
            '-md',
            help='Minimum read depth filter.')
    ] = 10,

    bam: Annotated[
        Path | None,
        typer.Option(
            '--bam',
            exists=True,
            help='Optional BAM aligned against the same query reference as the VCF. '
                 'Used to mark non-covered codon stretches below --min-depth.',
        ),
    ] = None,

    threads: Annotated[
        int,
        typer.Option(
            '--threads',
            '-th',
            help='Used number of threads for alignment (default: 1).'
        )
    ] = 1,

    use_cache: Annotated[
        bool,
        typer.Option(
            '--cache/--no-cache',
            help='Reuse/store FASTA reference mapping cache in the project database (default: on).'
        )
    ] = True,

    aligner: Annotated[
        str,
        typer.Option(
            '--aligner',
            '-a',
            help="Alignment backend for query FASTA matching: 'pairwise' (Biopython, default) or 'mappy' (minimap2). Mappy is faster for long references.",
        )
    ] = 'pairwise',

) -> None:
    """
    Run resistance profiling on a VCF file.

    Provide exactly one of --ref-fasta or --query-ref-header to specify the query reference.
    """
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    project_conn = None
    results_conn = None

    try:
        if bool(ref_fasta) == bool(query_ref_header):
            raise click.ClickException(
                'Provide exactly one of --ref-fasta or --query-ref-header.'
            )

        if aligner not in ('pairwise', 'mappy'):
            raise click.ClickException(f"Unknown aligner {aligner!r}; choose 'pairwise' or 'mappy'.")

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        results_conn = _init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        if ref_fasta is not None:
            with err_console.status('[dim]Aligning reference to internal references…[/dim]'):
                query_name, query_seq, fasta_matches = resolve_fasta_query(
                    project_conn, ref_fasta, use_cache=use_cache, cores=threads,
                    aligner=aligner,  # type: ignore[arg-type]
                )
        else:
            query_name, query_seq, fasta_matches = resolve_cached_query_reference(
                project_conn, query_ref_header or '',
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)

        variants = parse_vcf(vcf)
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
            rule_sets=rule_sets,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_row['name'],
            ref_name=ref_name,
            sample=sample,
            input_basename=vcf.name,
            total_variants=total_variants,
            variants_in_cds=variants_in_cds,
            output_dir=output,
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            results_conn=results_conn,
            project_path=project,
            logger=logger,
            query_sequence=query_seq,
            gene_matches=fasta_matches,
            coverage_gaps=coverage_gaps,
        )

        _print_completion_panel(console, '✓ Profiling complete', result, outputs)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()
        if results_conn is not None:
            results_conn.close()


# ──────────────────────────────────────────────────────────────────────
# profile-fasta
# ──────────────────────────────────────────────────────────────────────

@app.command('profile-fasta')
def profile_fasta(
    project: Annotated[
        Path,
        typer.Option(
            '--project',
            '-p',
            exists=True,
            help='Project database.'
        )
    ],

    consensus_fasta: Annotated[
        Path,
        typer.Option(
            '--fasta',
            '-f',
            exists=True,
            help='Input consensus FASTA sequence.'
        )
    ],

    sample: Annotated[
        str,
        typer.Option(
            '--sample',
            '-s',
            help='Sample name for the report.'
        )
    ] = 'sample',

    output: Annotated[
        Path,
        typer.Option(
            '--output',
            '-o',
            help='Output directory.'
        )
    ] = Path('output'),

    results_db: Annotated[
        Path | None,
        typer.Option(
            '--results-db',
            '-d',
            help='Optional results database path. Creates or appends to an existing SQLite results database.'
        )
    ] = None,

    cores: Annotated[
        int,
        typer.Option(
            '--cores',
            '-c',
            help='Alignment parallelism: process count for pairwise, thread count for mappy.'
        )
    ] = 1,

    aligner: Annotated[
        str,
        typer.Option(
            '--aligner',
            '-a',
            help="Alignment backend for query FASTA matching: 'pairwise' (Biopython, default) or 'mappy' (minimap2).",
        )
    ] = 'pairwise',

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

        project_conn = open_project_db(project)
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        results_conn = _init_results_db_connection(
            str(results_db) if results_db else None, project_conn, logger,
        )

        with err_console.status('[dim]Aligning fasta sequence to internal references…[/dim]'):
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, consensus_fasta, use_cache=False, cores=cores,
                aligner=aligner,  # type: ignore[arg-type]
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)

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
            rule_sets=rule_sets,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_row['name'],
            ref_name=ref_name,
            sample=sample,
            input_basename=consensus_fasta.name,
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            output_dir=output,
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
        )

        _print_completion_panel(console, '✓ Profiling complete', result, outputs)

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if project_conn is not None:
            project_conn.close()
        if results_conn is not None:
            results_conn.close()


# ──────────────────────────────────────────────────────────────────────
# regenerate
# ──────────────────────────────────────────────────────────────────────

@app.command('regenerate')
def regenerate(
    result_db: Annotated[
        Path,
        typer.Option(
            '--result-db',
            '-d',
            exists=True,
            help='Results database.'
        )
    ],

    run_id: Annotated[
        int | None,
        typer.Option(
            '--identifier',
            '-i',
            help='Run ID to regenerate.'
        )
    ] = None,

    project: Annotated[
        Path | None,
        typer.Option(
            '--project',
            '-p',
            exists=True,
            help='Project database (required with --identifier).'
        )
    ] = None,

    out: Annotated[
        Path | None,
        typer.Option(
            '--out',
            '-o',
            help='Output directory (required with --identifier).',
        )
    ] = None,

    list_flag: Annotated[
        bool,
        typer.Option(
            '--list',
            '-l',
            help='List all stored results.'
        )
    ] = False,

) -> None:
    """
    List stored profiling results or regenerate a report from a results database.

    Use --list to display all stored runs, or --identifier with --project and
    --out to regenerate the full report for a specific run.
    """
    logger = logging.getLogger('respro')
    console = Console(highlight=False)
    results_conn = None
    project_conn = None

    try:
        results_conn = open_results_db(result_db)

        if list_flag and run_id is not None:
            raise click.UsageError('Use either --list or --identifier, not both.')

        if not list_flag and run_id is None:
            raise click.UsageError(
                'Provide --list to show stored results, or --identifier to regenerate one.'
            )

        if list_flag:
            runs = list_runs(results_conn)
            if not runs:
                console.print('No stored results found.')
                return
            _print_runs_table(console, runs)
            return

        if project is None:
            raise click.UsageError('--project is required with --identifier.')
        if out is None:
            raise click.UsageError('--out is required with --identifier.')

        if run_id is None:
            raise click.UsageError('--identifier is required when not using --list.')

        run_dict, variant_rows = load_run(results_conn, run_id)
        coverage_gaps = load_coverage_gaps(results_conn, run_id)
        combo_rows = load_combo_rule_hits(results_conn, run_id)

        project_conn = open_project_db(project)

        # Validate that the provided project DB matches the one used for this run.
        stored_fp = run_dict.get('project_fingerprint', '')
        if stored_fp:
            current_fp = compute_project_fingerprint(project_conn)
            if stored_fp != current_fp:
                raise click.ClickException(
                    f'Project database fingerprint mismatch for run #{run_id}.\n'
                    'The provided --project database does not match the one used for this run.\n'
                    'Ensure you are using the same project database that was active during profiling.'
                )
        else:
            logger.warning(
                'Run #%d has no stored fingerprint — skipping project validation.', run_id
            )

        # Load reference metadata for report context.
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
        combo_hits = reconstruct_combo_rule_hits(combo_rows, annotations)
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
            combo_hits=combo_hits,
            coverage_gaps=coverage_gaps,
        )

        genes = []
        rules = []
        rule_gene_names: set[str] = set()
        if ref_id is not None:
            genes = load_genes_for_reference(project_conn, ref_id)
            rules = load_rules(project_conn, ref_id)
            rule_gene_names = {rule.gene_name for rule in rules}

        with err_console.status(f'[dim]Regenerating run #{run_id}…[/dim]'):
            output_dir = out
            outputs = export_results(
                result,
                output_dir,
                genes=genes,
                rule_gene_names=rule_gene_names,
                project_conn=project_conn,
                rules=rules,
            )

        console.print(Panel(
            f'{result.resistance_hits} database hit(s)',
            title=f'[green]✓ Regenerated run #{run_id}[/green]',
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



if __name__ == '__main__':
    app()
