"""Profiling service wrappers for FASTA and VCF web endpoints."""

from __future__ import annotations

from pathlib import Path

from respro.core.annotation import annotate_variants
from respro.core.fasta_profile import profile_fasta_consensus
from respro.core.query import (
    pick_best_reference_id,
    resolve_fasta_query,
    select_matches_for_reference,
)
from respro.core.rules import load_rule_sets, load_rules, match_rule_sets, match_rules
from respro.core.vcf_coverage import compute_coverage_gaps_from_bam
from respro.core.vcf_remap import remap_variants
from respro.db.models import AnnotatedVariant, CoverageGap, ProfilingResult
from respro.db.results import save_run
from respro.db.schema import init_results_db, open_project_db
from respro.io.reference import load_genes_for_reference
from respro.io.vcf import parse_vcf
from respro.report.html import export_results


def profile_fasta(
    *,
    project_db: Path,
    results_db: Path,
    output_dir: Path,
    fasta_path: Path,
    sample: str,
    threads: int,
    aligner: str,
) -> dict:
    """Run FASTA profiling and persist the run in results.db."""
    _validate_aligner(aligner)

    project_conn = open_project_db(project_db)
    results_conn = init_results_db(results_db)
    try:
        project_name = _project_name(project_conn)
        query_name, query_seq, fasta_matches = resolve_fasta_query(
            project_conn,
            fasta_path,
            use_cache=False,
            threads=threads,
            aligner=aligner,  # type: ignore[arg-type]
        )
        ref_id = pick_best_reference_id(fasta_matches)
        selected_matches = select_matches_for_reference(fasta_matches, ref_id)
        ref_name = _reference_name(project_conn, ref_id)

        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)
        annotations, coverage_gaps = profile_fasta_consensus(query_seq, selected_matches)

        af_bins = {
            'high': (0.75, 1.0),
            'intermediate': (0.35, 0.74),
            'low': (0.01, 0.34),
        }

        result = _build_result(
            annotations=annotations,
            rule_sets=rule_sets,
            rules=rules,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_name,
            ref_name=ref_name,
            sample=sample,
            input_basename=fasta_path.name,
            total_variants=len(annotations),
            variants_in_cds=len(annotations),
            af_bins=af_bins,
            coverage_gaps=coverage_gaps,
            query_sequence=query_seq,
            gene_matches=selected_matches,
        )

        outputs = export_results(
            result,
            output_dir,
            genes=genes,
            rule_gene_names=rule_gene_names,
            project_conn=project_conn,
            rules=rules,
        )
        run_id = save_run(results_conn, project_db.resolve(), project_conn, result)

        return {
            'mode': 'fasta',
            'run_id': run_id,
            'sample_name': result.sample_name,
            'reference_name': result.reference_name,
            'query_name': query_name,
            'report_html_path': str(outputs['html']),
            'resistance_hits': result.resistance_hits,
            'total_variants': result.total_variants,
        }
    finally:
        project_conn.close()
        results_conn.close()


def profile_vcf(
    *,
    project_db: Path,
    results_db: Path,
    output_dir: Path,
    vcf_path: Path,
    ref_fasta_path: Path,
    sample: str,
    min_af: float,
    min_depth: int,
    bam_path: Path | None,
    threads: int,
    aligner: str,
) -> dict:
    """Run VCF profiling and persist the run in results.db."""
    _validate_aligner(aligner)

    project_conn = open_project_db(project_db)
    results_conn = init_results_db(results_db)
    try:
        project_name = _project_name(project_conn)
        query_name, query_seq, fasta_matches = resolve_fasta_query(
            project_conn,
            ref_fasta_path,
            use_cache=False,
            threads=threads,
            aligner=aligner,  # type: ignore[arg-type]
        )
        ref_id = pick_best_reference_id(fasta_matches)
        selected_matches = select_matches_for_reference(fasta_matches, ref_id)
        ref_name = _reference_name(project_conn, ref_id)

        genes, rules, rule_sets, rule_gene_names = _load_reference_data(project_conn, ref_id)

        variants = parse_vcf(vcf_path, expected_query_name=query_name)
        variants = [
            variant for variant in variants
            if variant.allele_freq >= min_af and (variant.depth < 0 or variant.depth >= min_depth)
        ]
        variants, _warnings = remap_variants(variants, selected_matches, query_seq)

        coverage_gaps: list[CoverageGap] = []
        if bam_path is not None:
            coverage_gaps = compute_coverage_gaps_from_bam(
                bam_path=bam_path,
                query_name=query_name,
                query_sequence=query_seq,
                matches=selected_matches,
                min_depth=min_depth,
            )

        annotations = annotate_variants(variants, genes)
        result = _build_result(
            annotations=annotations,
            rule_sets=rule_sets,
            rules=rules,
            project_conn=project_conn,
            ref_id=ref_id,
            project_name=project_name,
            ref_name=ref_name,
            sample=sample,
            input_basename=vcf_path.name,
            total_variants=len(variants),
            variants_in_cds=sum(1 for ann in annotations if ann.gene_name),
            coverage_gaps=coverage_gaps,
            query_sequence=query_seq,
            gene_matches=selected_matches,
        )

        outputs = export_results(
            result,
            output_dir,
            genes=genes,
            rule_gene_names=rule_gene_names,
            project_conn=project_conn,
            rules=rules,
        )
        run_id = save_run(results_conn, project_db.resolve(), project_conn, result)

        return {
            'mode': 'vcf',
            'run_id': run_id,
            'sample_name': result.sample_name,
            'reference_name': result.reference_name,
            'query_name': query_name,
            'report_html_path': str(outputs['html']),
            'resistance_hits': result.resistance_hits,
            'total_variants': result.total_variants,
        }
    finally:
        project_conn.close()
        results_conn.close()


def _build_result(
    *,
    annotations: list[AnnotatedVariant],
    rule_sets: list,
    rules: list,
    project_conn,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    total_variants: int,
    variants_in_cds: int,
    af_bins: dict[str, tuple[float, float]] | None = None,
    coverage_gaps: list[CoverageGap] | None = None,
    query_sequence: str = '',
    gene_matches: list | None = None,
) -> ProfilingResult:
    """Apply rule matching and build the result dataclass."""
    matched_annotations = match_rules(annotations, rules)
    combo_hits = match_rule_sets(matched_annotations, rule_sets)
    _assign_af_bins(matched_annotations, bins=af_bins)

    reference_row = project_conn.execute(
        'SELECT organism, length FROM reference WHERE id = ?', (ref_id,)
    ).fetchone()
    organism = reference_row['organism'] or '' if reference_row else ''
    reference_length_nt = int(reference_row['length'] or 0) if reference_row else 0

    return ProfilingResult(
        project_name=project_name,
        organism=organism,
        reference_name=ref_name,
        reference_length_nt=reference_length_nt,
        sample_name=sample,
        vcf_name=input_basename,
        total_variants=total_variants,
        variants_in_cds=variants_in_cds,
        resistance_hits=sum(1 for ann in matched_annotations if ann.is_resistance_hit),
        annotations=matched_annotations,
        combo_hits=combo_hits,
        coverage_gaps=coverage_gaps or [],
        query_sequence=query_sequence,
        gene_matches=gene_matches or [],
    )


def _load_reference_data(project_conn, ref_id: int) -> tuple[list, list, list, set[str]]:
    """Load genes/rules/rule sets for one internal reference."""
    genes = load_genes_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    rule_sets = load_rule_sets(project_conn, ref_id)
    rule_gene_names: set[str] = {rule.gene_name for rule in rules}
    for rule_set in rule_sets:
        for member in rule_set.members:
            rule_gene_names.add(member.gene_name)
    return genes, rules, rule_sets, rule_gene_names


def _project_name(project_conn) -> str:
    """Return project name from project DB."""
    row = project_conn.execute('SELECT name FROM project LIMIT 1').fetchone()
    if row is None:
        raise ValueError('No project found in the project database.')
    return row['name']


def _reference_name(project_conn, ref_id: int) -> str:
    """Return reference name for one internal reference id."""
    row = project_conn.execute('SELECT name FROM reference WHERE id = ?', (ref_id,)).fetchone()
    if row is None:
        raise ValueError(f'Reference id {ref_id} not found in project database.')
    return row['name']


def _validate_aligner(aligner: str) -> None:
    """Allow only supported aligner values."""
    if aligner not in ('pairwise', 'mappy'):
        raise ValueError(f"Unknown aligner {aligner!r}; choose 'pairwise' or 'mappy'.")


def _assign_af_bins(
    annotations: list[AnnotatedVariant],
    bins: dict[str, tuple[float, float]] | None = None,
) -> None:
    """Assign AF bins in-place."""
    if bins is None:
        bins = {
            'high': (0.75, 1.0),
            'intermediate': (0.25, 0.7499),
            'low': (0.01, 0.2499),
        }

    sorted_bins = sorted(bins.items(), key=lambda item: -item[1][0])
    for ann in annotations:
        for label, (lower, upper) in sorted_bins:
            if lower <= ann.variant.allele_freq <= upper:
                ann.af_bin = label
                break
