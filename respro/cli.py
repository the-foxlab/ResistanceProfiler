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
import sqlite3
from pathlib import Path

import click

from respro import __version__
from respro.core.annotation import annotate_variants, assign_af_bins
from respro.core.fasta_profile import profile_fasta_consensus
from respro.core.profile import (
    pick_best_reference_id,
    remap_variants,
    resolve_cached_query_reference,
    resolve_fasta_query,
    select_matches_for_reference,
)
from respro.core.resistance_rules import load_rule_sets, load_rules, match_rule_sets, match_rules
from respro.db.init_project import add_to_project, init_project
from respro.db.results import (
    list_runs,
    load_run,
    reconstruct_annotations,
    save_run,
)
from respro.db.results import (
    project_fingerprint as compute_project_fingerprint,
)
from respro.db.schema import init_results_db, open_project_db, open_results_db
from respro.io.reference import load_genes_for_reference
from respro.io.vcf import parse_vcf
from respro.report.html import export_results
from respro.report.results_model import ProfilingResult
from respro.utils.logging import setup_logging


@click.group()
@click.version_option(version=__version__, prog_name='respro')
@click.option('-v', '--verbose', count=True, help='Increase verbosity (-v info, -vv debug).')
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """
    ResistanceProfiler — pathogen-agnostic antiviral resistance profiling.
    """
    ctx.ensure_object(dict)
    ctx.obj['logger'] = setup_logging(verbose)


# ──────────────────────────────────────────────────────────────────────
# init
# ──────────────────────────────────────────────────────────────────────

@main.command()
@click.option('--name', required=True, help='Project name.')
@click.option(
    '--genbank', 'genbank_paths', required=True, multiple=True,
    type=click.Path(exists=True),
    help='One or more GenBank files. Can be repeated; each file may itself contain multiple records.',
)
@click.option('--rules', required=True, type=click.Path(exists=True), help='Resistance rules TSV.')
@click.option('--output', '-o', required=True, type=click.Path(), help='Output SQLite database path.')
@click.option('--overwrite', is_flag=True, default=False, help='Overwrite existing database.')
@click.option('--drug-info/--no-drug-info', 'drug_info', default=True,
    help='Query PubChem to attach CID, URL and description to each drug (default: on).',
)
def init(
    name: str,
    genbank_paths: tuple[str, ...],
    rules: str,
    output: str,
    overwrite: bool,
    drug_info: bool,
) -> None:
    """
    Initialise a project database from one or more GenBank reference records and resistance rules provided in TSV.
    """

    try:
        db_path = init_project(
            db_path=Path(output),
            name=name,
            genbank_paths=[Path(path) for path in genbank_paths],
            rules_tsv=Path(rules),
            overwrite=overwrite,
            drug_info=drug_info,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f'✓ Project initialised: {db_path}')


@main.command('init-add')
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Existing project SQLite database.')
@click.option('--genbank', 'genbank_paths', required=False, multiple=True, type=click.Path(exists=True), help='Optional GenBank file(s) with additional references/genes.')
@click.option('--rules', required=True, type=click.Path(exists=True), help='Resistance rules TSV to add.')
@click.option('--drug-info/--no-drug-info', 'drug_info', default=True,
    help='Query PubChem to attach CID, URL and description to each new drug (default: on).',
)
def init_add(
    project: str,
    genbank_paths: tuple[str, ...],
    rules: str,
    drug_info: bool,
) -> None:
    """
    Add curated rules and optional GenBank annotations to an existing project database.
    """
    try:
        db_path = add_to_project(
            db_path=Path(project),
            genbank_paths=[Path(path) for path in genbank_paths],
            rules_tsv=Path(rules),
            drug_info=drug_info,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f'✓ Project updated: {db_path}')


# ──────────────────────────────────────────────────────────────────────
# Shared profiling helpers
# ──────────────────────────────────────────────────────────────────────

def _init_results_db_connection(
    results_db: str | None,
    project_conn: sqlite3.Connection,
    logger: logging.Logger,
) -> sqlite3.Connection | None:
    """
    Open or initialise a results database and validate project fingerprint compatibility.

    :param results_db: path to results database, or None to skip
    :param project_conn: open project database connection
    :param logger: logger instance
    :return: open results database connection, or None
    """
    if not results_db:
        return None

    results_db_path = Path(results_db)
    existed = results_db_path.is_file()
    try:
        results_conn = init_results_db(results_db_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    if existed:
        logger.info('Results database validated: %s', results_db_path)
        current_fp = compute_project_fingerprint(project_conn)
        existing_run = results_conn.execute(
            "SELECT project_fingerprint FROM run WHERE project_fingerprint != '' LIMIT 1"
        ).fetchone()
        if existing_run and existing_run['project_fingerprint'] != current_fp:
            results_conn.close()
            raise click.ClickException(
                'Project fingerprint mismatch: the provided --project database does not match '
                'the project used for existing runs in this results database.\n'
                'Ensure you use the same project database for all runs in this results file.'
            )
    else:
        logger.info('Results database initialised: %s', results_db_path)

    return results_conn


def _resolve_reference(
    project_conn: sqlite3.Connection,
    fasta_matches: list,
    query_name: str,
    logger: logging.Logger,
) -> tuple[int, str, list]:
    """
    Pick the best reference, filter fasta_matches, and log matched genes.

    :param project_conn: open project database connection
    :param fasta_matches: list of gene alignment matches
    :param query_name: query sequence name for logging
    :param logger: logger instance
    :return: (ref_id, ref_name, filtered fasta_matches)
    """
    ref_id = pick_best_reference_id(fasta_matches)
    fasta_matches = select_matches_for_reference(fasta_matches, ref_id)

    ref_name_row = project_conn.execute(
        'SELECT name FROM reference WHERE id = ?', (ref_id,)
    ).fetchone()
    if ref_name_row is None:
        raise click.ClickException(f'Reference id {ref_id} not found in project database')
    ref_name = ref_name_row['name']

    logger.info('Matched query reference %r to internal reference %r', query_name, ref_name)
    matched_gene_names = sorted({match.gene.name for match in fasta_matches})
    logger.info('Matched %d gene(s): %s', len(matched_gene_names), ', '.join(matched_gene_names))
    for match in fasta_matches:
        logger.debug(
            'gene=%s identity=%.2f%% coverage=%.2f%% strand=%s cigar=%s',
            match.gene.name, match.identity * 100, match.coverage * 100,
            match.strand, match.cigar,
        )

    return ref_id, ref_name, fasta_matches


def _load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str]]:
    """
    Load genes, rules, rule_sets, and the union of gene names covered by any rule.

    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :return: (genes, rules, rule_sets, rule_gene_names)
    """
    genes = load_genes_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    rule_sets = load_rule_sets(project_conn, ref_id)
    rule_gene_names: set[str] = {rule.gene_name for rule in rules}
    for rule_set in rule_sets:
        for member in rule_set.members:
            rule_gene_names.add(member.gene_name)
    return genes, rules, rule_sets, rule_gene_names


def _finalize_and_export(
    annotations: list,
    rule_sets: list,
    project_conn: sqlite3.Connection,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    total_variants: int,
    variants_in_cds: int,
    output_dir: Path,
    genes: list,
    rule_gene_names: set[str],
    rules: list,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    af_bins: dict[str, tuple[float, float]] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Apply rule matching and AF binning, build the result object, export, and optionally persist.

    :param annotations: list of annotated variants
    :param rule_sets: combo rule sets
    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :param project_name: project name for the report
    :param ref_name: resolved reference name
    :param sample: sample name
    :param input_basename: filename of the input VCF or FASTA
    :param total_variants: total variant count
    :param variants_in_cds: variant count within CDS regions
    :param output_dir: output directory path
    :param genes: gene list for the reference
    :param rule_gene_names: set of gene names covered by any rule
    :param rules: resistance rules for the reference
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param af_bins: optional custom AF bin thresholds; defaults to VCF-mode bins
    :return: (ProfilingResult, export path dict)
    """
    annotations = match_rules(annotations, rules)
    combo_hits = match_rule_sets(annotations, rule_sets)
    annotations = assign_af_bins(annotations, bins=af_bins)

    reference_row = project_conn.execute(
        'SELECT organism, length FROM reference WHERE id = ?', (ref_id,)
    ).fetchone()
    organism = reference_row['organism'] or '' if reference_row else ''
    reference_length_nt = int(reference_row['length'] or 0) if reference_row else 0

    result = ProfilingResult(
        project_name=project_name,
        organism=organism,
        reference_name=ref_name,
        reference_length_nt=reference_length_nt,
        sample_name=sample,
        vcf_name=input_basename,
        total_variants=total_variants,
        variants_in_cds=variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        annotations=annotations,
        combo_hits=combo_hits,
    )

    outputs = export_results(
        result,
        output_dir,
        genes=genes,
        rule_gene_names=rule_gene_names,
        project_conn=project_conn,
        rules=rules,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs


# ──────────────────────────────────────────────────────────────────────
# profile-vcf
# ──────────────────────────────────────────────────────────────────────

@main.command('profile-vcf')
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Project database.')
@click.option('--vcf', required=True, type=click.Path(exists=True), help='Input VCF file.')
@click.option('--ref-fasta', required=False, type=click.Path(exists=True),
    help='Reference FASTA the VCF was called against (mutually exclusive with --query-ref-header).',
)
@click.option(
    '--query-ref-header',
    required=False,
    help='Reuse a previously cached query reference by its stored FASTA header (mutually exclusive with --ref-fasta).',
)
@click.option('--sample', default='sample', help='Sample name for the report. Default: sample')
@click.option('--output', '-o', default='output', type=click.Path(), help='Output directory.')
@click.option(
    '--results-db',
    default=None,
    type=click.Path(),
    help='Optional results database path. Creates or appends to an existing SQLite results database.',
)
@click.option('--cache/--no-cache', 'use_cache', default=True,
    help='Reuse/store FASTA reference mapping cache in the project database (default: on).',
)
@click.option('--min-af', default=0.01, type=float, help='Minimum allele frequency filter. Default: 0.01')
@click.option('--min-depth', default=10, type=int, help='Minimum read depth filter. Default: 10')
def profile_vcf(
    project: str,
    vcf: str,
    ref_fasta: str | None,
    query_ref_header: str | None,
    sample: str,
    output: str,
    results_db: str | None,
    use_cache: bool,
    min_af: float,
    min_depth: int,
) -> None:
    """
    Run resistance profiling on a VCF file.

    Provide exactly one of --ref-fasta or --query-ref-header to specify the query reference.
    """
    logger = logging.getLogger('respro')
    project_conn = None
    results_conn = None

    try:
        if bool(ref_fasta) == bool(query_ref_header):
            raise click.ClickException(
                'Provide exactly one of --ref-fasta or --query-ref-header.'
            )

        project_conn = open_project_db(Path(project))
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        results_conn = _init_results_db_connection(results_db, project_conn, logger)

        if ref_fasta is not None:
            query_name, query_seq, fasta_matches = resolve_fasta_query(
                project_conn, Path(ref_fasta), use_cache=use_cache,
            )
        else:
            query_name, query_seq, fasta_matches = resolve_cached_query_reference(
                project_conn, query_ref_header or '',
            )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)

        variants = parse_vcf(Path(vcf))
        logger.info('Parsed %d variant(s)', len(variants))
        variants = [v for v in variants if v.allele_freq >= min_af and v.depth >= min_depth]
        logger.info('%d variant(s) after AF/depth filtering', len(variants))

        variants, remap_warnings = remap_variants(variants, fasta_matches, query_seq)
        for warning in remap_warnings:
            logger.warning(warning)
        logger.info('%d variant(s) after FASTA remapping', len(variants))

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
            input_basename=Path(vcf).name,
            total_variants=total_variants,
            variants_in_cds=variants_in_cds,
            output_dir=Path(output),
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            results_conn=results_conn,
            project_path=Path(project),
            logger=logger,
        )

        click.echo(
            '✓ Profiling complete — '
            f'{result.resistance_hits} database hit(s), '
            f'{len(result.combo_hits)} combo rule hit(s)'
        )
        for fmt, path in outputs.items():
            click.echo(f'  {fmt}: {path}')
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

@main.command('profile-fasta')
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Project database.')
@click.option('--fasta', 'consensus_fasta', required=True, type=click.Path(exists=True),
    help='Input consensus FASTA sequence.',
)
@click.option('--sample', default='sample', help='Sample name for the report. Default: sample')
@click.option('--output', '-o', default='output', type=click.Path(), help='Output directory.')
@click.option(
    '--results-db',
    default=None,
    type=click.Path(),
    help='Optional results database path. Creates or appends to an existing SQLite results database.',
)
def profile_fasta(
    project: str,
    consensus_fasta: str,
    sample: str,
    output: str,
    results_db: str | None,
) -> None:
    """
    Run resistance profiling on a consensus FASTA sequence.
    """
    logger = logging.getLogger('respro')
    project_conn = None
    results_conn = None

    try:
        project_conn = open_project_db(Path(project))
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        results_conn = _init_results_db_connection(results_db, project_conn, logger)

        query_name, query_seq, fasta_matches = resolve_fasta_query(
            project_conn, Path(consensus_fasta), use_cache=False,
        )

        ref_id, ref_name, fasta_matches = _resolve_reference(
            project_conn, fasta_matches, query_name, logger,
        )
        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)

        annotations = profile_fasta_consensus(query_seq, fasta_matches)

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
            input_basename=Path(consensus_fasta).name,
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            output_dir=Path(output),
            genes=genes,
            rule_gene_names=rule_gene_names,
            rules=rules,
            results_conn=results_conn,
            project_path=Path(project),
            logger=logger,
            af_bins=fasta_af_bins,
        )

        click.echo(
            '✓ Profiling complete — '
            f'{result.resistance_hits} database hit(s), '
            f'{len(result.combo_hits)} combo rule hit(s)'
        )
        for fmt, path in outputs.items():
            click.echo(f'  {fmt}: {path}')
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

@main.command('regenerate')
@click.option('--result-db', required=True, type=click.Path(exists=True), help='Results database.')
@click.option('--list', 'list_flag', is_flag=True, default=False, help='List all stored results.')
@click.option('--identifier', 'run_id', type=int, default=None, help='Run ID to regenerate.')
@click.option('--project', '-p', type=click.Path(exists=True), default=None,
    help='Project database (required with --identifier).',
)
@click.option('--out', '-o', type=click.Path(), default=None,
    help='Output directory (required with --identifier).',
)
def regenerate(
    result_db: str,
    list_flag: bool,
    run_id: int | None,
    project: str | None,
    out: str | None,
) -> None:
    """
    List stored profiling results or regenerate a report from a results database.

    Use --list to display all stored runs, or --identifier with --project and
    --out to regenerate the full report for a specific run.
    """
    logger = logging.getLogger('respro')
    results_conn = None
    project_conn = None

    try:
        results_conn = open_results_db(Path(result_db))

        if list_flag and run_id is not None:
            raise click.UsageError('Use either --list or --identifier, not both.')

        if not list_flag and run_id is None:
            raise click.UsageError(
                'Provide --list to show stored results, or --identifier to regenerate one.'
            )

        if list_flag:
            runs = list_runs(results_conn)
            if not runs:
                click.echo('No stored results found.')
                return
            click.echo(f'{"ID":>4}  {"Sample":<16}  {"Reference":<20}  {"VCF":<30}  {"Hits":>4}  Created')
            click.echo('─' * 95)
            for run in runs:
                click.echo(
                    f'{run["id"]:>4}  {(run["sample_name"] or ""):<16}  '
                    f'{run["reference_name"]:<20}  {Path(run["vcf_path"]).name:<30}  '
                    f'{run["resistance_hits"]:>4}  {run["created_at"]}'
                )
            return

        if project is None:
            raise click.UsageError('--project is required with --identifier.')
        if out is None:
            raise click.UsageError('--out is required with --identifier.')

        run_dict, variant_rows = load_run(results_conn, run_id)

        project_conn = open_project_db(Path(project))

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
            ref_id = ref_row['id']
            organism = ref_row['organism'] or ''
            reference_length_nt = int(ref_row['length'] or 0)

        annotations = reconstruct_annotations(variant_rows)
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
        )

        genes = []
        rules = []
        rule_gene_names: set[str] = set()
        if ref_id is not None:
            genes = load_genes_for_reference(project_conn, ref_id)
            rules = load_rules(project_conn, ref_id)
            rule_gene_names = {rule.gene_name for rule in rules}

        output_dir = Path(out)
        outputs = export_results(
            result,
            output_dir,
            genes=genes,
            rule_gene_names=rule_gene_names,
            project_conn=project_conn,
            rules=rules,
        )

        click.echo(f'✓ Regenerated run #{run_id} — {result.resistance_hits} database hit(s)')
        for fmt, path in outputs.items():
            click.echo(f'  {fmt}: {path}')

    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if results_conn is not None:
            results_conn.close()
        if project_conn is not None:
            project_conn.close()


if __name__ == '__main__':
    main()

