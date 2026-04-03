"""
CLI entry point for ResistanceProfiler.

Commands:
- respro init    — initialise a GenBank-backed project database
- respro init-add — add rules and optional GenBank annotations to an existing project
- respro profile — run the resistance profiling pipeline
- respro export  — package a project into a portable bundle
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from respro import __version__
from respro.utils.logging import setup_logging
from respro.core.annotation import assign_af_bins
from respro.core.annotation import annotate_variants
from respro.core.resistance_rules import load_rules, load_rule_sets, match_rules, match_rule_sets
from respro.core.profile import (
    pick_best_reference_id,
    remap_variants,
    resolve_fasta_reference,
    select_matches_for_reference,
)
from respro.db.bundle import export_bundle
from respro.db.init_project import add_to_project
from respro.db.init_project import init_project
from respro.db.schema import init_results_db
from respro.db.schema import open_project_db
from respro.io.reference import load_genes_for_reference
from respro.io.vcf import parse_vcf
from respro.report.export import export_results
from respro.report.results_model import ProfilingResult


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
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Existing project database.')
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
# profile
# ──────────────────────────────────────────────────────────────────────

@main.command()
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Project database.')
@click.option('--vcf', required=True, type=click.Path(exists=True), help='Input VCF file.')
@click.option('--ref-fasta', required=True, type=click.Path(exists=True),
    help='Reference FASTA the VCF was called against.')
@click.option('--sample', default='', help='Sample name for the report.')
@click.option('--output', '-o', default='output', type=click.Path(), help='Output directory.')
@click.option(
    '--results-db',
    default=None,
    type=click.Path(),
    help='Optional results database path. Creates new DB or validates existing DB.',
)
@click.option(
    '--format', 'formats', multiple=True, default=['html', 'json'],
    type=click.Choice(['html', 'json', 'tsv', 'svg', 'pdf']),
    help='Output formats (can be repeated).',
)
@click.option('--min-af', default=0.01, type=float, help='Minimum allele frequency.')
@click.option('--min-depth', default=10, type=int, help='Minimum read depth.')
def profile(
    project: str,
    vcf: str,
    ref_fasta: str,
    sample: str,
    output: str,
    results_db: str | None,
    formats: tuple[str, ...],
    min_af: float,
    min_depth: int
) -> None:
    """
    Run the resistance profiling pipeline on a VCF file plus reference FASTA.
    """

    logger = logging.getLogger('respro')

    project_conn = None
    results_conn = None

    try:
        if results_db:
            results_db_path = Path(results_db)
            existed = results_db_path.is_file()
            try:
                results_conn = init_results_db(results_db_path)
            except (FileNotFoundError, ValueError, OSError) as exc:
                raise click.ClickException(str(exc)) from exc

            if existed:
                logger.info('Results database validated: %s', results_db_path)
            else:
                logger.info('Results database initialised: %s', results_db_path)

        # 1. Open project DB
        project_conn = open_project_db(Path(project))
        project_row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
        if project_row is None:
            raise click.ClickException('No project found in the database')

        # 2. Match the provided reference FASTA to internal rule-relevant genes.
        query_name, query_seq, fasta_matches = resolve_fasta_reference(
            project_conn,
            Path(ref_fasta),
        )
        ref_id = pick_best_reference_id(fasta_matches)
        fasta_matches = select_matches_for_reference(fasta_matches, ref_id)

        ref_name_row = project_conn.execute(
            'SELECT name FROM reference WHERE id = ?',
            (ref_id,),
        ).fetchone()
        if ref_name_row is None:
            raise click.ClickException(
                f'Reference id {ref_id} not found in project database'
            )
        ref_name = ref_name_row['name']

        logger.info(
            'Matched query reference %r to internal reference %r',
            query_name,
            ref_name,
        )

        matched_gene_names = sorted({match.gene.name for match in fasta_matches})
        logger.info(
            'VCF remapping matched %d gene(s): %s',
            len(matched_gene_names),
            ', '.join(matched_gene_names),
        )
        for match in fasta_matches:
            logger.debug(
                'gene=%s identity=%.2f%% coverage=%.2f%% strand=%s cigar=%s',
                match.gene.name,
                match.identity * 100,
                match.coverage * 100,
                match.strand,
                match.cigar,
            )

        # 3. Load genes and rules for the resolved reference.
        genes = load_genes_for_reference(project_conn, ref_id)
        rules = load_rules(project_conn, ref_id)
        rule_sets = load_rule_sets(project_conn, ref_id)

        # 4. Read and filter the VCF before coordinate remapping.
        variants = parse_vcf(Path(vcf))
        logger.info('Parsed %d variant(s)', len(variants))
        variants = [
            variant for variant in variants
            if variant.allele_freq >= min_af and variant.depth >= min_depth
        ]
        logger.info('%d variant(s) after AF/depth filtering', len(variants))

        # 5. Remap the VCF to internal reference coordinates.
        variants, remap_warnings = remap_variants(
            variants,
            fasta_matches,
            query_seq,
        )
        for warning in remap_warnings:
            logger.warning(warning)
        logger.info('%d variant(s) after FASTA remapping', len(variants))

        # 6. Annotate amino acid effects on the matched internal reference.
        annotations = annotate_variants(variants, genes)

        # 7. Match resistance rules
        annotations = match_rules(annotations, rules)
        combo_hits = match_rule_sets(annotations, rule_sets)

        # 8. Assign AF bins
        annotations = assign_af_bins(annotations)

        organism_row = project_conn.execute(
            'SELECT organism FROM reference WHERE id = ?',
            (ref_id,),
        ).fetchone()
        organism = ''
        if organism_row is not None:
            organism = organism_row['organism'] or ''

        # 9. Build result object
        result = ProfilingResult(
            project_name=project_row['name'],
            organism=organism,
            reference_name=ref_name,
            sample_name=sample,
            vcf_path=vcf,
            total_variants=len(variants),
            variants_in_cds=sum(1 for a in annotations if a.gene_name),
            resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
            annotations=annotations,
            combo_hits=combo_hits,
        )

        # 10. Export
        output_dir = Path(output)
        outputs = export_results(result, output_dir, genes=genes, formats=formats)

        click.echo(
            '✓ Profiling complete — '
            f'{result.resistance_hits} resistance hit(s), '
            f'{len(combo_hits)} combo rule hit(s)'
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
# export
# ──────────────────────────────────────────────────────────────────────

@main.command('export')
@click.option('--project', '-p', required=True, type=click.Path(exists=True), help='Project database.')
@click.option('--output', '-o', required=True, type=click.Path(), help='Output ZIP path.')
def export_cmd(project: str, output: str) -> None:
    """
    Package a project into a portable bundle (ZIP).
    """
    export_bundle(Path(project), Path(output))
    click.echo(f'✓ Bundle exported: {output}')


if __name__ == '__main__':
    main()

