"""Shared profiling orchestration, reconstruction, and export helpers for the CLI layer."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.panel import Panel

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.annotation import annotate_variants, assign_af_bins
from respro.core.query import (
    QueryRecord,
    pick_best_reference_id,
    select_matches_for_reference,
)
from respro.core.rules import match_formula_rules, match_rules
from respro.db.features import (
    load_all_features_for_reference,
    load_features_for_reference,
    load_reference_metadata,
    load_reference_name,
)
from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FeatureMatch,
    ProfilingResult,
    ReferenceGroup,
    VariantCall,
)
from respro.db.results import (
    load_existing_run_project_fingerprint,
    save_run,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.rules_queries import load_formula_rules, load_rules
from respro.db.schema import init_results_db
from respro.report.non_html_exports import export_results
from respro.utils.files import resolve_output_file

if TYPE_CHECKING:
    from respro.core.combined_snp import BamCooccurrence

logger = logging.getLogger('respro')


def safe_report_stem(input_basename: str) -> str:
    """Derive a filesystem-safe filename stem from an input path or basename."""
    raw_stem = Path(input_basename).stem.strip() or 'profile'
    return re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem) or 'profile'


def parse_export_formats(export_values: list[str] | None) -> set[str] | None:
    """Normalize and validate repeated ``--export`` format options."""
    if export_values is None:
        return None
    formats: set[str] = set()
    for value in export_values:
        normalized = value.strip().lower()
        if normalized not in ('json', 'pdf', 'tsv'):
            raise click.ClickException('Invalid --export value. Choose one of: json, pdf, tsv.')
        formats.add(normalized)
    return formats or None


def init_results_db_connection(
    results_db: str | None,
    project_conn: sqlite3.Connection,
    log: logging.Logger,
) -> sqlite3.Connection | None:
    """Open or create a results DB and verify its project fingerprint matches *project_conn*."""
    if not results_db:
        return None

    results_db_path = Path(results_db)
    existed = results_db_path.is_file()
    try:
        results_conn = init_results_db(results_db_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    if existed:
        log.info('Results database validated: %s', results_db_path)
        current_fp = compute_project_fingerprint(project_conn)
        existing_fp = load_existing_run_project_fingerprint(results_conn)
        if existing_fp is not None and existing_fp != current_fp:
            results_conn.close()
            raise click.ClickException(
                'Project fingerprint mismatch: the provided --project database does not match '
                'the project used for existing runs in this results database.\n'
                'Ensure you use the same project database for all runs in this results file.'
            )
    else:
        log.info('Results database initialised: %s', results_db_path)
    return results_conn


def load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str], list]:
    """Return ``(rule_backed_features, rules, formula_rules, rule_feature_names, all_features)``."""
    features = load_features_for_reference(project_conn, ref_id)
    report_features = load_all_features_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    formula_rules = load_formula_rules(project_conn, ref_id)
    rule_feature_names: set[str] = {r.feature_name for r in rules}
    for formula in formula_rules:
        for member in formula.members:
            rule_feature_names.add(member.feature_name)
    return features, rules, formula_rules, rule_feature_names, report_features


def bam_excluded_positions(
    rg: ReferenceGroup,
    coverage_gaps: list[CoverageGap],
) -> set[tuple[str, int]]:
    """Return ``(chrom, genomic_pos)`` pairs falling inside low-coverage BAM codons."""
    features_by_name = {f.name: f for f in rg.features}
    excluded: set[tuple[str, int]] = set()
    for gap in coverage_gaps:
        if gap.chrom != rg.query_name:
            continue
        feature = features_by_name.get(gap.feature_name)
        if feature is None:
            continue
        for codon_idx in range(gap.codon_start, gap.codon_end + 1):
            for nt_offset in range(3):
                genomic_pos = feature.cds_to_genomic_position(
                    feature.codon_start + codon_idx * 3 + nt_offset,
                )
                if genomic_pos is not None:
                    excluded.add((gap.chrom, genomic_pos))
    return excluded


def variant_touches_excluded_positions(
    var: VariantCall,
    excluded: set[tuple[str, int]],
) -> bool:
    """Return True if any genomic base covered by ``var.ref`` is in *excluded*."""
    return any((var.chrom, var.pos + offset) in excluded for offset in range(len(var.ref)))


def _reject_cross_species_gene_name_collisions(references: list[ReferenceGroup]) -> None:
    """Abort if the same matched feature name appears across multiple organisms."""
    organisms_by_feature: dict[str, set[str]] = {}
    refs_by_feature: dict[str, set[str]] = {}
    for rg in references:
        for match in rg.feature_matches:
            fname = match.feature.name
            organisms_by_feature.setdefault(fname, set()).add(rg.organism)
            refs_by_feature.setdefault(fname, set()).add(rg.reference_name)

    for fname, organisms in sorted(organisms_by_feature.items()):
        if len(organisms) > 1:
            raise click.ClickException(
                f"Cannot create an HTML report: matched gene {fname!r} occurs in "
                f"multiple species ({', '.join(sorted(organisms))}; references: "
                f"{', '.join(sorted(refs_by_feature[fname]))}). "
                'Resistance-relevant mutations cannot be attributed to a single species '
                'unambiguously. Restrict the reference FASTA to one species or remove the '
                'overlapping gene.'
            )


def match_rules_across_references(
    annotations: list[AnnotatedVariant],
    references: list[ReferenceGroup],
    *,
    is_fasta_mode: bool = False,
    cfg: CliConfig = CLI_CONFIG,
) -> list:
    """Run atomic and formula rule matching once per distinct reference, scoped by CHROM."""
    chroms_by_ref_id: dict[int, set[str]] = {}
    for rg in references:
        chroms_by_ref_id.setdefault(rg.reference_id, set()).add(rg.query_name)

    single_fasta = is_fasta_mode and len(references) == 1
    all_formula_hits: list = []
    seen_ref_ids: set[int] = set()
    for rg in references:
        if rg.reference_id in seen_ref_ids:
            continue
        seen_ref_ids.add(rg.reference_id)
        ref_annotations = (
            annotations
            if single_fasta
            else [a for a in annotations if a.variant.chrom in chroms_by_ref_id[rg.reference_id]]
        )
        if not ref_annotations:
            continue
        match_rules(ref_annotations, rg.rules)
        all_formula_hits.extend(match_formula_rules(
            ref_annotations,
            rg.formula_rules,
            min_fraction=float(cfg.matching.min_cooccurrence_combination_fraction),
            eps=float(cfg.matching.frechet_epsilon),
        ))
    return all_formula_hits


def assemble_multi_reference_result(
    *,
    project_conn: sqlite3.Connection,
    query_records: list[QueryRecord],
    remapped_variants: list[VariantCall],
    coverage_gaps: list[CoverageGap],
    project_name: str,
    sample: str,
    vcf_name: str,
    total_variants: int | None = None,
    af_bins: dict[str, tuple[float, float]] | None = None,
    is_fasta_mode: bool = False,
    coverage_assessed: bool = False,
    bam_cooccurrence_by_chrom: dict[str, BamCooccurrence] | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> ProfilingResult:
    """Build ``ReferenceGroup``s per query record, annotate per reference, and match rules."""
    bins = af_bins if af_bins is not None else (
        cfg.af_bins_fasta.as_dict() if is_fasta_mode else cfg.af_bins.as_dict()
    )

    references: list[ReferenceGroup] = []
    for record in query_records:
        ref_id = pick_best_reference_id(record.feature_matches)
        selected_matches = select_matches_for_reference(record.feature_matches, ref_id)
        ref_name = load_reference_name(project_conn, ref_id)
        if ref_name is None:
            raise click.ClickException(f'Reference id {ref_id} not found in project database')
        organism, reference_length_nt = load_reference_metadata(project_conn, ref_id)
        features, rules, formula_rules, rule_feature_names, _ = load_reference_data(project_conn, ref_id)
        references.append(ReferenceGroup(
            reference_name=ref_name,
            reference_id=ref_id,
            organism=organism,
            reference_length_nt=reference_length_nt,
            query_name=ref_name if is_fasta_mode and len(query_records) == 1 else record.query_name,
            query_sequence=record.query_sequence,
            feature_matches=selected_matches,
            features=features,
            rules=rules,
            formula_rules=formula_rules,
            rule_feature_names=rule_feature_names,
        ))

    _reject_cross_species_gene_name_collisions(references)

    if not any(rg.rule_feature_names for rg in references):
        ref_names = ', '.join(rg.reference_name for rg in references) or '(none)'
        raise click.ClickException(
            f'no matched reference has resistance rules in the project database '
            f'(matched references: {ref_names})'
        )

    variants_by_chrom: dict[str, list[VariantCall]] = {}
    for var in remapped_variants:
        variants_by_chrom.setdefault(var.chrom, []).append(var)

    single_fasta = is_fasta_mode and len(references) == 1
    annotations: list[AnnotatedVariant] = []
    for rg in references:
        group_variants = remapped_variants if single_fasta else variants_by_chrom.get(rg.query_name, [])
        bam_ctx = bam_cooccurrence_by_chrom.get(rg.query_name) if bam_cooccurrence_by_chrom else None
        if bam_cooccurrence_by_chrom is not None:
            excluded = bam_excluded_positions(rg, coverage_gaps)
            if excluded:
                group_variants = [
                    v for v in group_variants if not variant_touches_excluded_positions(v, excluded)
                ]
        annotations.extend(annotate_variants(
            group_variants, rg.features, is_fasta_mode=is_fasta_mode,
            bam_cooccurrence=bam_ctx, cfg=cfg,
        ))

    formula_hits = match_rules_across_references(
        annotations, references, is_fasta_mode=is_fasta_mode, cfg=cfg,
    )
    annotations = assign_af_bins(annotations, bins=bins)
    variants_in_cds = sum(1 for a in annotations if a.feature_name)

    return ProfilingResult(
        project_name=project_name,
        organism=references[0].organism,
        sample_name=sample,
        vcf_name=vcf_name,
        total_variants=len(annotations) if total_variants is None else total_variants,
        variants_in_cds=variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        is_fasta_mode=is_fasta_mode,
        coverage_assessed=coverage_assessed,
        annotations=annotations,
        formula_hits=formula_hits,
        coverage_gaps=coverage_gaps,
        references=references,
    )


def reconstruct_reference_groups(
    project_conn: sqlite3.Connection,
    run_dict: dict,
    variant_rows: list[dict],
    profiled_by_ref: dict[str, list[str]],
) -> tuple[list[ReferenceGroup], list, list, set[str], str]:
    """Reconstruct ``ReferenceGroup``s plus reporting features/rules for stored runs (regenerate/sync)."""
    # Collect ordered (reference_name, query_name) pairs without collapsing multi-chrom targeted refs.
    ref_query_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    json_references = run_dict.get('references') or []

    def _add_pair(ref_name: str, query_name: str) -> None:
        if not ref_name:
            return
        pair = (ref_name, query_name or ref_name)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            ref_query_pairs.append(pair)

    is_fasta_mode = bool(run_dict.get('is_fasta_mode', 0))
    if json_references:
        for ref in json_references:
            _add_pair(ref.get('reference_name', ''), ref.get('query_name', ''))
    else:
        for row in variant_rows:
            ref_name = row.get('reference_name', '') or run_dict.get('reference_name', '')
            qname = ref_name if is_fasta_mode else (row.get('chrom', '') or ref_name)
            _add_pair(ref_name, qname)
        # Include zero-variant profiled references persisted in profiled_feature.
        known_refs = {ref for ref, _ in ref_query_pairs}
        for ref_name in profiled_by_ref:
            if ref_name and ref_name not in known_refs:
                _add_pair(ref_name, ref_name)

    if not ref_query_pairs:
        fallback = run_dict.get('reference_name', '')
        _add_pair(fallback, fallback)

    # Cache loaded reference metadata/features/rules per distinct reference_name.
    ref_cache: dict[str, tuple[int, str, int, list, list, list, set[str]]] = {}
    all_features: list = []
    all_rules: list = []
    all_rule_feature_names: set[str] = set()
    primary_organism = ''
    references: list[ReferenceGroup] = []

    for ref_name, query_name in ref_query_pairs:
        if ref_name not in ref_cache:
            ref_row = project_conn.execute(
                'SELECT id, organism, length FROM reference WHERE name = ?',
                (ref_name,),
            ).fetchone()
            if ref_row is not None:
                ref_id = int(ref_row['id'])
                organism = ref_row['organism'] or ''
                ref_len = int(ref_row['length'] or 0)
                _, ref_rules, ref_formulas, ref_rule_names, ref_features = load_reference_data(
                    project_conn, ref_id,
                )
            else:
                ref_id, organism, ref_len = 0, '', 0
                ref_features, ref_rules, ref_formulas, ref_rule_names = [], [], [], set()

            if not primary_organism:
                primary_organism = organism
            ref_cache[ref_name] = (
                ref_id, organism, ref_len, ref_features, ref_rules, ref_formulas, ref_rule_names,
            )
            all_features.extend(ref_features)
            all_rules.extend(ref_rules)
            all_rule_feature_names |= ref_rule_names

        ref_id, organism, ref_len, ref_features, ref_rules, ref_formulas, ref_rule_names = ref_cache[ref_name]
        profiled_names = profiled_by_ref.get(ref_name) or sorted(ref_rule_names)
        feature_by_name = {f.name: f for f in ref_features}
        feature_matches = [
            FeatureMatch(
                feature=feature_by_name[fname],
                identity=0.0,
                cds_coverage=0.0,
                query_coverage=0.0,
                query_start=0,
                query_end=0,
                strand='+',
                cigar='',
            )
            for fname in profiled_names
            if fname in feature_by_name
        ]
        references.append(ReferenceGroup(
            reference_name=ref_name,
            reference_id=ref_id,
            organism=organism,
            reference_length_nt=ref_len,
            query_name=query_name,
            query_sequence='',
            feature_matches=feature_matches,
            features=ref_features,
            rules=ref_rules,
            formula_rules=ref_formulas,
            rule_feature_names=ref_rule_names,
        ))

    return references, all_features, all_rules, all_rule_feature_names, primary_organism


def collect_reporting_scope(
    result: ProfilingResult,
    project_conn: sqlite3.Connection,
) -> tuple[list, list, set[str]]:
    """Collect deduplicated full features, rules, and rule feature names across all references."""
    features: list = []
    rules: list = []
    rule_feature_names: set[str] = set()
    seen_feature_ids: set[int] = set()
    seen_ref_ids: set[int] = set()

    for rg in result.references:
        if rg.reference_id in seen_ref_ids:
            continue
        seen_ref_ids.add(rg.reference_id)
        rules.extend(rg.rules)
        rule_feature_names |= rg.rule_feature_names
        for feature in load_all_features_for_reference(project_conn, rg.reference_id):
            if feature.id not in seen_feature_ids:
                seen_feature_ids.add(feature.id)
                features.append(feature)
    return features, rules, rule_feature_names


def export_and_persist_result(
    result: ProfilingResult,
    *,
    project_conn: sqlite3.Connection,
    input_basename: str,
    output_target: Path,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    log: logging.Logger,
    extra_export_formats: set[str] | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> tuple[ProfilingResult, dict]:
    """Export an assembled ``ProfilingResult`` to disk and optionally persist to results DB."""
    features, rules, rule_feature_names = collect_reporting_scope(result, project_conn)
    html_output_path = resolve_output_file(output_target, f'{safe_report_stem(input_basename)}.report.html')

    outputs = export_results(
        result,
        html_output_path.parent,
        features=features,
        rule_feature_names=rule_feature_names,
        project_conn=project_conn,
        rules=rules,
        extra_export_formats=extra_export_formats,
        project_db_path=project_path.resolve(),
        output_html_path=html_output_path,
        similarity_high=cfg.similarity.high,
        similarity_moderate=cfg.similarity.moderate,
        cfg=cfg,
    )
    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        log.info('Run saved to results database with id %d', run_id)
    return result, outputs


def print_completion_panel(console: Console, title: str, result: ProfilingResult, outputs: dict) -> None:
    """Render a summary panel after a profiling run."""
    direct_hits = sum(len(ann.non_formula_component_rule_matches) for ann in result.annotations)
    hit_line = f'{direct_hits} rule hit(s)'
    if result.formula_hits:
        hit_line += f'  ·  {len(result.formula_hits)} formula rule hit(s)'
    lines = [hit_line, f'{direct_hits + len(result.formula_hits)} total database hits', '']
    for fmt, path in outputs.items():
        lines.append(f'[dim]{fmt}[/dim]   {path}')
    console.print(Panel('\n'.join(lines), title=f'[green]{title}[/green]', border_style='green'))
