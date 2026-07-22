"""
Shared profiling orchestration helpers and output utilities for the CLI layer.

These functions depend on Click, DB wiring, persistence, and report export —
they belong to the CLI layer and must not be moved into respro/core/.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from respro.config.cli_settings import CLI_CONFIG
from respro.core.annotation import (
    _suppress_ruleless_overlap_annotations,
    annotate_variants,
    assign_af_bins,
)
from respro.core.query import (
    QueryRecord,
    pick_best_reference_id,
    select_matches_for_reference,
)
from respro.core.rules import match_formula_rules, match_rules
from respro.db.features import load_features_for_reference
from respro.db.models import (
    AnnotatedVariant,
    CoverageGap,
    FeatureMatch,
    ProfilingResult,
    ReferenceGroup,
    VariantCall,
)
from respro.db.profile_queries import (
    load_existing_run_project_fingerprint,
    load_reference_metadata,
    load_reference_name,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.results import save_run
from respro.db.rules_queries import load_formula_rules, load_rules
from respro.db.schema import init_results_db
from respro.report.non_html_exports import export_results
from respro.utils.files import resolve_output_file

logger = logging.getLogger('respro')


def _parse_export_formats(export_values: list[str] | None) -> set[str] | None:
    """Normalize and validate repeated ``--export`` values."""
    if export_values is None:
        return None

    normalized_formats: set[str] = set()
    for export_value in export_values:
        normalized_value = export_value.strip().lower()
        if normalized_value not in ('json', 'pdf'):
            raise click.ClickException('Invalid --export value. Choose one of: json, pdf.')
        normalized_formats.add(normalized_value)

    return normalized_formats if normalized_formats else None


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
        existing_run_fp = load_existing_run_project_fingerprint(results_conn)
        if existing_run_fp is not None and existing_run_fp != current_fp:
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
    Pick the best reference, filter fasta_matches, and log matched features.

    :param project_conn: open project database connection
    :param fasta_matches: list of feature alignment matches
    :param query_name: query sequence name for logging
    :param logger: logger instance
    :return: (ref_id, ref_name, filtered fasta_matches)
    """
    ref_id = pick_best_reference_id(fasta_matches)
    fasta_matches = select_matches_for_reference(fasta_matches, ref_id)

    ref_name = load_reference_name(project_conn, ref_id)
    if ref_name is None:
        raise click.ClickException(f'Reference id {ref_id} not found in project database')

    logger.info('Matched query reference %r to internal reference %r', query_name, ref_name)
    matched_feature_names = sorted({match.feature.name for match in fasta_matches})
    logger.info('Matched %d feature(s): %s', len(matched_feature_names), ', '.join(matched_feature_names))
    for match in fasta_matches:
        logger.debug(
            'feature=%s identity=%.2f%% cds_coverage=%.2f%% query_coverage=%.2f%% strand=%s cigar=%s',
            match.feature.name, match.identity * 100, match.cds_coverage * 100,
            match.query_coverage * 100, match.strand, match.cigar,
        )

    return ref_id, ref_name, fasta_matches


def _load_reference_data(
    project_conn: sqlite3.Connection,
    ref_id: int,
) -> tuple[list, list, list, set[str]]:
    """
    Load features, atomic rules, formula rules, and the union of rule-covered features.

    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :return: (features, rules, formula_rules, rule_feature_names)
    """
    features = load_features_for_reference(project_conn, ref_id)
    rules = load_rules(project_conn, ref_id)
    formula_rules = load_formula_rules(project_conn, ref_id)
    rule_feature_names: set[str] = {rule.feature_name for rule in rules}
    for formula_rule in formula_rules:
        for member_rule in formula_rule.member_rules.values():
            rule_feature_names.add(member_rule.feature_name)
    return features, rules, formula_rules, rule_feature_names


def _reject_cross_species_gene_name_collisions(references: list[ReferenceGroup]) -> None:
    """
    Raise :class:`click.ClickException` if a feature name appears on matched references
    belonging to more than one distinct organism.

    A cross-species gene-name collision (e.g. HSV-1 UL23 and HSV-2 UL23) is an ambiguous
    hit: the report cannot attribute resistance-relevant mutations to a single species
    unambiguously, so we refuse to report rather than produce a misleading result.
    Same-species shared gene names are allowed — the per-reference scoping in the
    rule/annotation loops disambiguates them.

    :param references: assembled :class:`ReferenceGroup` list
    :raises click.ClickException: naming the first colliding gene and the organisms involved
    """
    # Map each feature name to the set of distinct organisms whose matched references
    # carry that feature. A reference's matched features are its CDS features that the
    # query aligned to (rg.features is the full CDS list; rg.rule_feature_names is the
    # rule-covered subset). We use rg.features so the gate also catches ruleless CDS
    # collisions — a collision is ambiguous regardless of whether rules are attached.
    organisms_by_feature_name: dict[str, set[str]] = {}
    reference_names_by_feature_name: dict[str, set[str]] = {}
    for rg in references:
        for feature in rg.features:
            organisms_by_feature_name.setdefault(feature.name, set()).add(rg.organism)
            reference_names_by_feature_name.setdefault(feature.name, set()).add(rg.reference_name)

    for feature_name, organisms in sorted(organisms_by_feature_name.items()):
        if len(organisms) > 1:
            organisms_str = ', '.join(sorted(organisms))
            ref_names_str = ', '.join(sorted(reference_names_by_feature_name[feature_name]))
            raise click.ClickException(
                f'gene {feature_name!r} matched references from multiple species '
                f'({organisms_str}; references: {ref_names_str}) — ambiguous cross-species '
                f'hit; refusing to report'
            )


def assemble_multi_reference_result(
    *,
    project_conn: sqlite3.Connection,
    query_records: list[QueryRecord],
    remapped_variants: list[VariantCall],
    coverage_gaps: list[CoverageGap],
    project_name: str,
    sample: str,
    vcf_name: str,
    total_variants: int,
    af_bins: dict[str, tuple[float, float]] | None = None,
    is_fasta_mode: bool = False,
) -> ProfilingResult:
    """
    Build one ``ReferenceGroup`` per matched query record, annotate per reference,
    and run per-reference rule matching.

    Remapped variants are grouped by their ``chrom`` (the original VCF CHROM, which
    equals the matching ``QueryRecord.query_name``) and annotated against only that
    reference's features. This is required because internal references share the
    same coordinate origin (each starts at 0), so annotating a variant against the
    union of all references' features would produce spurious cross-reference hits.
    ``annotate_variants`` itself is not modified.

    For each :class:`QueryRecord` the best internal reference is selected, its
    features/rules/formula_rules loaded, and a :class:`ReferenceGroup` constructed.
    Rule matching (``match_rules`` and ``match_formula_rules``) is run per reference
    against the annotations whose ``feature_name`` belongs to that reference.

    At least one matched reference must have rules loaded (non-empty
    ``rule_feature_names``); otherwise :class:`click.ClickException` is raised.
    References that aligned but have no rules are retained and reported with a
    warning (orphan case), not an error.

    :param project_conn: open project database connection
    :param query_records: one per matched FASTA record
    :param remapped_variants: flat list of variants remapped to internal coordinates;
        each variant's ``chrom`` must match a query record's ``query_name``
    :param coverage_gaps: flat coverage gaps
    :param project_name: project name for the report
    :param sample: sample name
    :param vcf_name: input VCF basename
    :param total_variants: total variant count (pre-annotation)
    :param af_bins: AF bin thresholds; defaults to CLI config when None
    :param is_fasta_mode: mark emitted annotations as FASTA-derived
    :return: assembled :class:`ProfilingResult` with ``references`` populated
    :raises click.ClickException: if no matched reference has resistance rules
    """
    bins = af_bins if af_bins is not None else CLI_CONFIG.af_bins.as_dict()

    references: list[ReferenceGroup] = []
    for record in query_records:
        ref_id = pick_best_reference_id(record.feature_matches)
        selected_matches = select_matches_for_reference(record.feature_matches, ref_id)
        ref_name = load_reference_name(project_conn, ref_id)
        if ref_name is None:
            raise click.ClickException(f'Reference id {ref_id} not found in project database')
        organism, reference_length_nt = load_reference_metadata(project_conn, ref_id)
        features, rules, formula_rules, rule_feature_names = _load_reference_data(project_conn, ref_id)

        references.append(ReferenceGroup(
            reference_name=ref_name,
            reference_id=ref_id,
            organism=organism,
            reference_length_nt=reference_length_nt,
            query_name=record.query_name,
            query_sequence=record.query_sequence,
            feature_matches=selected_matches,
            features=features,
            rules=rules,
            formula_rules=formula_rules,
            rule_feature_names=rule_feature_names,
        ))

    # Validate: reject cross-species gene-name collisions. A feature name that appears
    # on matched references belonging to more than one distinct organism is an ambiguous
    # cross-species hit (e.g. HSV-1 UL23 and HSV-2 UL23): the report cannot attribute
    # resistance-relevant mutations to a single species unambiguously, so we refuse to
    # report rather than produce a misleading result. Same-species shared gene names are
    # allowed (the per-reference scoping in the rule/annotation loops disambiguates them).
    # The gate runs before any annotation or rule matching so a rejected run produces no
    # partial report.
    _reject_cross_species_gene_name_collisions(references)

    # Validate: at least one matched reference must carry resistance rules.
    ruled_references = [rg for rg in references if rg.rule_feature_names]
    if not ruled_references:
        ref_names = ', '.join(rg.reference_name for rg in references) or '(none)'
        raise click.ClickException(
            f'no matched reference has resistance rules in the project database '
            f'(matched references: {ref_names})'
        )

    # Warn about orphaned (ruleless) references — kept and reported, not an error.
    for rg in references:
        if not rg.rule_feature_names:
            logger.warning(
                'Matched reference %r has no resistance rules in the project database; '
                'features will appear in the report without rule hits',
                rg.reference_name,
            )

    # Annotate per reference: group remapped variants by chrom (== query_name) and
    # annotate each group against only its reference's features.
    variants_by_chrom: dict[str, list[VariantCall]] = {}
    for var in remapped_variants:
        variants_by_chrom.setdefault(var.chrom, []).append(var)

    annotations: list[AnnotatedVariant] = []
    for rg in references:
        group_variants = variants_by_chrom.get(rg.query_name, [])
        annotations.extend(annotate_variants(group_variants, rg.features, is_fasta_mode=is_fasta_mode))

    # Rule suppression and matching must run once per DISTINCT reference, not once per
    # ReferenceGroup. In the targeted-sequencing case two records align to the same
    # reference and produce two ReferenceGroups carrying the same rules; running match_rules
    # twice against the shared annotations list would append each ResistanceRule twice
    # (match_rules mutates in place via ann.rule_matches.append). Dedupe by reference_id.
    #
    # match_rules / match_formula_rules index rules by feature_name (no chrom) and mutate
    # ann.rule_matches in place, so they must be scoped to the annotations belonging to this
    # reference. Scoping by feature_name alone is insufficient when two references in the same
    # project DB share a feature name (e.g. both pathogens have a "pol" CDS): a rule on refA
    # would fire on a refB annotation at the same codon — a false resistance hit. The unique,
    # unambiguous scope key is the chrom (== query_name), which is unique per ReferenceGroup.
    # A single reference_id may span several chroms in the targeted case, so the scope for a
    # reference_id is the union of its ReferenceGroups' query_names. The matchers mutate the
    # shared annotation objects in place, so filtering the passed list is sufficient.
    # _suppress_ruleless_overlap_annotations groups by (chrom, pos, ref, alt), so annotations
    # from different references (different chroms) never share a locus; it is safe to run on
    # the full list and is idempotent under the reference_id dedup.
    chroms_by_reference_id: dict[int, set[str]] = {}
    for rg in references:
        chroms_by_reference_id.setdefault(rg.reference_id, set()).add(rg.query_name)

    seen_reference_ids: set[int] = set()
    for rg in references:
        if rg.reference_id in seen_reference_ids:
            continue
        seen_reference_ids.add(rg.reference_id)
        annotations = _suppress_ruleless_overlap_annotations(annotations, rg.rule_feature_names)
        ref_chroms = chroms_by_reference_id[rg.reference_id]
        ref_annotations = [a for a in annotations if a.variant.chrom in ref_chroms]
        if ref_annotations:
            match_rules(ref_annotations, rg.rules)

    # Per-reference formula rule matching; collect all hits into a flat list.
    # Dedupe by reference_id and scope by chrom for the same reasons as the rule loop above.
    all_formula_hits: list = []
    seen_reference_ids_formula: set[int] = set()
    for rg in references:
        if rg.reference_id in seen_reference_ids_formula:
            continue
        seen_reference_ids_formula.add(rg.reference_id)
        ref_chroms = chroms_by_reference_id[rg.reference_id]
        ref_annotations = [a for a in annotations if a.variant.chrom in ref_chroms]
        if not ref_annotations:
            continue
        formula_hits = match_formula_rules(
            ref_annotations,
            rg.formula_rules,
            member_af_threshold=float(CLI_CONFIG.matching.combination_member_af_threshold),
        )
        all_formula_hits.extend(formula_hits)

    annotations = assign_af_bins(annotations, bins=bins)
    variants_in_cds = sum(1 for a in annotations if a.feature_name)

    return ProfilingResult(
        project_name=project_name,
        organism=references[0].organism,
        sample_name=sample,
        vcf_name=vcf_name,
        total_variants=total_variants,
        variants_in_cds=variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        annotations=annotations,
        formula_hits=all_formula_hits,
        coverage_gaps=coverage_gaps,
        references=references,
    )


@dataclass
class _ProfilingRunContext:
    """Pipeline data assembled before finalization and export."""

    annotations: list[AnnotatedVariant]
    formula_rules: list
    features: list
    rule_feature_names: set[str]
    rules: list
    total_variants: int
    variants_in_cds: int
    coverage_gaps: list[CoverageGap]
    query_sequence: str
    feature_matches: list[FeatureMatch]
    af_bins: dict[str, tuple[float, float]]


def _finalize_and_export(
    ctx: _ProfilingRunContext,
    project_conn: sqlite3.Connection,
    ref_id: int,
    project_name: str,
    ref_name: str,
    sample: str,
    input_basename: str,
    output_target: Path,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    extra_export_formats: set[str] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Apply rule matching and AF binning, build the result object, export, and optionally persist.

    :param ctx: pipeline data assembled from the profiling run
    :param project_conn: open project database connection
    :param ref_id: internal reference id
    :param project_name: project name for the report
    :param ref_name: resolved reference name
    :param sample: sample name
    :param input_basename: filename of the input VCF or FASTA
    :param output_target: output path option; interpreted as directory or explicit HTML file
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param extra_export_formats: optional additional output formats ('json', 'pdf')
    :return: (ProfilingResult, export path dict)
    """
    annotations = _suppress_ruleless_overlap_annotations(ctx.annotations, ctx.rule_feature_names)
    annotations = match_rules(annotations, ctx.rules)
    formula_hits = match_formula_rules(
        annotations,
        ctx.formula_rules,
        member_af_threshold=float(CLI_CONFIG.matching.combination_member_af_threshold),
    )
    annotations = assign_af_bins(annotations, bins=ctx.af_bins)

    organism, reference_length_nt = load_reference_metadata(project_conn, ref_id)

    reference_group = ReferenceGroup(
        reference_name=ref_name,
        reference_id=ref_id,
        organism=organism,
        reference_length_nt=reference_length_nt,
        query_name=ref_name,
        query_sequence=ctx.query_sequence,
        feature_matches=ctx.feature_matches,
        features=ctx.features,
        rules=ctx.rules,
        formula_rules=ctx.formula_rules,
        rule_feature_names=ctx.rule_feature_names,
    )

    result = ProfilingResult(
        project_name=project_name,
        organism=organism,
        sample_name=sample,
        vcf_name=input_basename,
        total_variants=ctx.total_variants,
        variants_in_cds=ctx.variants_in_cds,
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        annotations=annotations,
        formula_hits=formula_hits,
        coverage_gaps=ctx.coverage_gaps,
        references=[reference_group],
    )

    raw_stem = Path(input_basename).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem) or 'profile'
    html_output_path = resolve_output_file(output_target, f'{safe_stem}.report.html')

    outputs = export_results(
        result,
        html_output_path.parent,
        features=ctx.features,
        rule_feature_names=ctx.rule_feature_names,
        project_conn=project_conn,
        rules=ctx.rules,
        extra_export_formats=extra_export_formats,
        project_db_path=project_path.resolve(),
        output_html_path=html_output_path,
        similarity_high=CLI_CONFIG.similarity.high,
        similarity_moderate=CLI_CONFIG.similarity.moderate,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs


def _finalize_and_export_multi(
    *,
    result: ProfilingResult,
    project_conn: sqlite3.Connection,
    sample: str,
    input_basename: str,
    output_target: Path,
    results_conn: sqlite3.Connection | None,
    project_path: Path,
    logger: logging.Logger,
    extra_export_formats: set[str] | None = None,
) -> tuple[ProfilingResult, dict]:
    """
    Export and persist an already-assembled multi-reference :class:`ProfilingResult`.

    Mirrors the export/save tail of :func:`_finalize_and_export` but derives the
    feature/rule/rule_feature_names arguments from the union of all
    :class:`ReferenceGroup`s on ``result`` (single-reference runs have one group,
    so the union equals that group's data and behavior is identical to today).

    :param result: assembled ProfilingResult with ``references`` populated
    :param project_conn: open project database connection
    :param sample: sample name (unused directly; kept for parity/logging)
    :param input_basename: filename of the input VCF
    :param output_target: output path option; interpreted as directory or explicit HTML file
    :param results_conn: open results database connection, or None
    :param project_path: path to the project database file
    :param logger: logger instance
    :param extra_export_formats: optional additional output formats ('json', 'pdf')
    :return: (ProfilingResult, export path dict)
    """
    features: list = []
    rules: list = []
    rule_feature_names: set[str] = set()
    for rg in result.references:
        features.extend(rg.features)
        rules.extend(rg.rules)
        rule_feature_names |= rg.rule_feature_names

    raw_stem = Path(input_basename).stem.strip() or 'profile'
    safe_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', raw_stem) or 'profile'
    html_output_path = resolve_output_file(output_target, f'{safe_stem}.report.html')

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
        similarity_high=CLI_CONFIG.similarity.high,
        similarity_moderate=CLI_CONFIG.similarity.moderate,
    )

    if results_conn is not None:
        run_id = save_run(results_conn, project_path.resolve(), project_conn, result)
        logger.info('Run saved to results database with id %d', run_id)

    return result, outputs

def _print_completion_panel(console: Console, title: str, result: ProfilingResult, outputs: dict) -> None:
    """Render a summary panel after a profiling run."""
    direct_rule_hit_total = sum(len(ann.non_formula_component_rule_matches) for ann in result.annotations)
    hit_line = f'{direct_rule_hit_total} rule hit(s)'
    if result.formula_hits:
        hit_line += f'  ·  {len(result.formula_hits)} formula rule hit(s)'
    total_database_hits = direct_rule_hit_total + len(result.formula_hits)
    total_hit_line = f'{total_database_hits} total database hits'

    lines = [hit_line, total_hit_line, '']
    for fmt, path in outputs.items():
        lines.append(f'[dim]{fmt}[/dim]   {path}')
    console.print(Panel('\n'.join(lines), title=f'[green]{title}[/green]', border_style='green'))
