"""Table row builders for All Mutations, Database Hits, and Similarity to Database Entries."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.annotation import CONSEQUENCE_LABELS, classify_similarity
from respro.db.models import ProfilingResult, ResistanceRule
from respro.report.alignment_visualization import FeatureAlignment, build_alignment_html
from respro.report.html.formatting import (
    attach_citations,
    bin_for_af,
    build_rule_metrics,
    format_drug_name_with_alias,
    load_svg_data_url,
    metric_flags_for_rows,
)
from respro.report.row_helpers import (
    display_consequence,
    format_aa_effects,
    nt_change_stored,
    nt_change_user,
    select_effect_as_resistant_rules,
    variant_source_label,
)


def build_all_mutations_rows(
    result: ProfilingResult,
    feature_alignments: Mapping[tuple[str, str] | str, FeatureAlignment],
    formula_hit_annotation_ids: set[int],
    display_names: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> list[dict]:
    """Build one row per CDS annotation for the All Mutations tab."""
    names = display_names or {}
    ref_by_chrom = reference_name_by_chrom or {}
    rows: list[dict] = []

    for ann in result.cds_annotations:
        alignment = (
            feature_alignments.get((ann.variant.chrom, ann.feature_name))
            or feature_alignments.get(ann.feature_name)
        )
        alignment_html = build_alignment_html(ann, alignment) if alignment is not None else None
        is_single_hit = ann.is_resistance_hit
        is_formula_hit = id(ann) in formula_hit_annotation_ids
        aa_change = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}' if ann.ref_aa and ann.alt_aa else ''

        rows.append({
            'feature': names.get(ann.feature_name, ann.feature_name),
            'nt_change_stored': nt_change_stored(ann),
            'nt_change_user': nt_change_user(ann),
            'variant_source': variant_source_label(ann),
            'nt_pos': ann.variant.pos + 1,
            'aa_effects': format_aa_effects(ann),
            'aa_change': aa_change,
            'consequence': display_consequence(ann),
            'variant_freq': ann.variant.allele_freq,
            'af_bin': ann.af_bin,
            'is_combined_codon_event': ann.is_combined_codon_event,
            'combined_member_count': ann.combined_member_count,
            'is_single_hit': is_single_hit,
            'is_formula_hit': is_formula_hit,
            'is_database_hit': is_single_hit or is_formula_hit,
            'alignment_html': str(alignment_html) if alignment_html is not None else '',
            'has_alignment': alignment_html is not None,
            'reference_name': ref_by_chrom.get(ann.variant.chrom, ''),
        })
    return rows


def build_database_hits_rows(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None = None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
    drug_alias_map: dict[str, str] | None = None,
    reference_name_by_chrom: dict[str, str] | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> dict:
    """Build rows, visibility flags, and deduplicated bibliography for the Database Hits tab."""
    names = display_names or {}
    class_map = drug_class_map or {}
    alias_map = drug_alias_map or {}
    ref_by_chrom = reference_name_by_chrom or {}
    rows: list[dict] = []

    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            feature = names.get(ann.feature_name, ann.feature_name)
            eff_alt = ann.rule_effect_alt.get(rule.id, ann.alt_aa)
            aa_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{eff_alt}'
                if ann.ref_aa and eff_alt
                else ann.feature_name
            )
            if rule.mutation == 'INS_any':
                aa_change = f'INS_any ({aa_change})'
            # Combined-state hits bin at their Fréchet lower bound rather than nucleotide AF.
            effect_lower = ann.rule_effect_aa_freq.get(rule.id)
            if effect_lower is not None and effect_lower != ann.variant.allele_freq:
                hit_af_bin = bin_for_af(effect_lower, result.is_fasta_mode, cfg=cfg)
            else:
                hit_af_bin = ann.af_bin
            rows.append({
                'drug_key': rule.drug_name,
                'drug': format_drug_name_with_alias(rule.drug_name, alias_map),
                'drug_class': class_map.get(rule.drug_name.strip().lower(), ''),
                'mutation_groups': [{'feature': feature, 'muts': [aa_change]}],
                'metrics': build_rule_metrics(
                    rule.phenotype, rule.clinical_phenotype,
                    rule.ic50, rule.fold_ic50, rule.score,
                    thresholds=metric_thresholds,
                ),
                'af_bin': hit_af_bin,
                'source': rule.source,
                'comment': rule.comment,
                'reference_name': ref_by_chrom.get(ann.variant.chrom, ''),
                '_raw_pubs': list(rule.publications),
            })

    for formula_hit in result.formula_hits:
        rs = formula_hit.rule_set
        member_ids = set(formula_hit.matched_member_ids)
        feature_to_muts: dict[str, list[str]] = {}
        first_chrom = formula_hit.matched_variants[0].variant.chrom if formula_hit.matched_variants else ''
        for ann in formula_hit.matched_variants:
            feature = names.get(ann.feature_name, ann.feature_name)
            eff_alt = next(
                (
                    ann.rule_effect_alt[r.id]
                    for r in ann.rule_matches
                    if r.external_id in member_ids and r.id in ann.rule_effect_alt
                ),
                ann.alt_aa,
            )
            aa_change = (
                f'{ann.ref_aa}{ann.codon_pos + 1}{eff_alt}'
                if ann.ref_aa and eff_alt
                else ann.feature_name
            )
            feature_to_muts.setdefault(feature, []).append(aa_change)
        rows.append({
            'drug_key': rs.drug_name,
            'drug': format_drug_name_with_alias(rs.drug_name, alias_map),
            'drug_class': class_map.get(rs.drug_name.strip().lower(), ''),
            'mutation_groups': [{'feature': f, 'muts': muts} for f, muts in feature_to_muts.items()],
            'metrics': build_rule_metrics(
                rs.phenotype, rs.clinical_phenotype,
                rs.ic50, rs.fold_ic50, rs.score,
                thresholds=metric_thresholds,
            ),
            'af_bin': bin_for_af(formula_hit.frechet_lower, result.is_fasta_mode, cfg=cfg),
            'source': rs.source,
            'comment': rs.comment,
            'reference_name': ref_by_chrom.get(first_chrom, ''),
            '_raw_pubs': list(rs.publications),
        })

    rows.extend(_build_effect_as_resistant_rows(
        result, project_conn, names, metric_thresholds, class_map, alias_map, ref_by_chrom,
    ))
    rows.sort(key=lambda r: (
        r['drug'].lower(),
        [(g['feature'], g['muts']) for g in r['mutation_groups']],
    ))
    bibliography = attach_citations(rows)

    return {
        'rows': rows,
        'count': len(rows),
        'has_publications': any(row['pub_citations'] for row in rows),
        'has_drug_class': bool(drug_class_map) and any(row.get('drug_class') for row in rows),
        'has_comments': any(row.get('comment') for row in rows),
        **metric_flags_for_rows(rows),
        'bibliography': bibliography,
        'info_icon': load_svg_data_url('info.svg'),
        'search_icon': load_svg_data_url('search.svg'),
        'reset_icon': load_svg_data_url('reset_filter.svg'),
    }


def _build_effect_as_resistant_rows(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None,
    display_names: dict[str, str],
    metric_thresholds: dict[str, tuple[float, float] | None] | None,
    drug_class_map: dict[str, str],
    drug_alias_map: dict[str, str],
    reference_name_by_chrom: dict[str, str],
) -> list[dict]:
    """Shape metadata-only ``effect_as_resistant`` matches into Database Hits rows."""
    rows: list[dict] = []
    for match in select_effect_as_resistant_rules(result, project_conn):
        ann = match.annotation
        drug_name = (match.rule.get('drug') or '').strip()
        aa_change = (
            f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            if ann.ref_aa and ann.alt_aa
            else ann.feature_name
        )
        label = CONSEQUENCE_LABELS.get(ann.consequence, ann.consequence)
        ref_label = match.reference_name or result.reference_name
        rows.append({
            'drug_key': drug_name,
            'drug': format_drug_name_with_alias(drug_name, drug_alias_map),
            'drug_class': drug_class_map.get(drug_name.lower(), ''),
            'mutation_groups': [{'feature': display_names.get(ann.feature_name, ann.feature_name), 'muts': [aa_change]}],
            'metrics': build_rule_metrics('resistant', '', '', '', '', thresholds=metric_thresholds),
            'af_bin': ann.af_bin,
            'source': 'Metadata algorithm',
            'comment': f'{label} interpreted as resistant by metadata algorithm ({ann.feature_name}, {ref_label}).',
            'reference_name': reference_name_by_chrom.get(ann.variant.chrom, ''),
            '_raw_pubs': [],
        })
    return rows


def build_potential_effects_rows(
    result: ProfilingResult,
    similarity_high: int = CLI_CONFIG.similarity.high,
    similarity_moderate: int = CLI_CONFIG.similarity.moderate,
    rules: list[ResistanceRule] | None = None,
    display_names: dict[str, str] | None = None,
    metric_thresholds: dict[str, tuple[float, float] | None] | None = None,
    drug_class_map: dict[str, str] | None = None,
    drug_alias_map: dict[str, str] | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> dict:
    """Build BLOSUM62 similarity rows for non-hit CDS variants at rule-covered codons."""
    if not rules:
        return {
            'rows': [],
            'count': 0,
            'has_drug_class': False,
            'has_publications': False,
            **metric_flags_for_rows([]),
            'bibliography': [],
            'info_icon': load_svg_data_url('info.svg'),
            'search_icon': load_svg_data_url('search.svg'),
            'reset_icon': load_svg_data_url('reset_filter.svg'),
        }

    # Scope rules per reference when multiple references carry distinct per-group rule lists.
    chrom_to_ref_id = {rg.query_name: rg.reference_id for rg in result.references if rg.query_name}
    distinct_ref_ids = {rg.reference_id for rg in result.references}
    scoped_rules_by_pos: dict[tuple[int, str, int], list[ResistanceRule]] = {}
    if len(distinct_ref_ids) > 1 and any(rg.rules for rg in result.references):
        for rg in result.references:
            for rule in rg.rules:
                scoped_rules_by_pos.setdefault((rg.reference_id, rule.feature_name, rule.position), []).append(rule)

    rules_by_pos: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        rules_by_pos.setdefault((rule.feature_name, rule.position), []).append(rule)

    names = display_names or {}
    class_map = drug_class_map or {}
    alias_map = drug_alias_map or {}
    excluded_consequences = {'frameshift', 'stop_gained', 'synonymous'}
    rows: list[dict] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    multi_ref = len(distinct_ref_ids) > 1

    for ann in result.cds_annotations:
        if ann.is_resistance_hit or ann.consequence in excluded_consequences:
            continue
        if not ann.feature_name or not ann.alt_aa:
            continue

        if scoped_rules_by_pos and (ref_id := chrom_to_ref_id.get(ann.variant.chrom)) is not None:
            candidate_rules = scoped_rules_by_pos.get((ref_id, ann.feature_name, ann.codon_pos))
        else:
            candidate_rules = rules_by_pos.get((ann.feature_name, ann.codon_pos))
        if not candidate_rules:
            continue

        ann_is_indel = ann.consequence in ('insertion', 'deletion') or len(ann.alt_aa) != 1
        effect_variants = _dedup_effect_variants(ann)
        scope_chrom = ann.variant.chrom if multi_ref else ''

        for eff_alt, eff_af in effect_variants:
            if len(eff_alt) != 1 and not ann_is_indel:
                continue
            for rule in candidate_rules:
                if rule.drug_name == '__formula_component__':
                    continue
                rule_is_indel = (
                    rule.mutation.lower() == 'fsx'
                    or rule.mutation == 'INS_any'
                    or any(ch.isdigit() for ch in rule.mutation)
                )
                if ann_is_indel and not rule_is_indel:
                    continue
                dedup_key = (scope_chrom, ann.feature_name, ann.codon_pos, eff_alt, rule.drug_name)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                observed_change = f'{ann.ref_aa}{ann.codon_pos + 1}{eff_alt}' if ann.ref_aa and eff_alt else (eff_alt or '')
                rule_change = (
                    f'{rule.reference}{rule.position + 1}{rule.mutation}'
                    if rule.reference and rule.mutation
                    else (rule.mutation or '')
                )
                similarity = (
                    'moderate'
                    if ann_is_indel
                    else classify_similarity(
                        eff_alt, rule.mutation,
                        high_threshold=similarity_high,
                        moderate_threshold=similarity_moderate,
                    )
                )
                sim_af_bin = (
                    bin_for_af(eff_af, result.is_fasta_mode, cfg=cfg)
                    if eff_af != ann.variant.allele_freq
                    else ann.af_bin
                )
                rows.append({
                    'feature': names.get(ann.feature_name, ann.feature_name),
                    'drug': format_drug_name_with_alias(rule.drug_name, alias_map),
                    'drug_class': class_map.get(rule.drug_name.strip().lower(), ''),
                    'mutation': observed_change,
                    'rule_change': rule_change,
                    'similarity': similarity,
                    'metrics': build_rule_metrics(
                        rule.phenotype, rule.clinical_phenotype,
                        rule.ic50, rule.fold_ic50, rule.score,
                        thresholds=metric_thresholds,
                    ),
                    'af_bin': sim_af_bin,
                    'source': rule.source or '',
                    '_raw_pubs': list(rule.publications),
                })

    rows.sort(key=lambda r: (r['drug'].lower(), r['mutation']))
    bibliography = attach_citations(rows)

    return {
        'rows': rows,
        'count': len(rows),
        'has_drug_class': bool(drug_class_map) and any(r.get('drug_class') for r in rows),
        'has_publications': any(row['pub_citations'] for row in rows),
        **metric_flags_for_rows(rows),
        'bibliography': bibliography,
        'info_icon': load_svg_data_url('info.svg'),
        'search_icon': load_svg_data_url('search.svg'),
        'reset_icon': load_svg_data_url('reset_filter.svg'),
    }


def _dedup_effect_variants(ann) -> list[tuple[str, float]]:
    """Return deduplicated ``(alt_aa, frequency)`` pairs for similarity evaluation."""
    raw = (
        [(s.alt_aa, s.lower) for s in ann.combined_states if s.accepted]
        if ann.combined_states
        else [(ann.alt_aa, ann.single_exchange_aa_freq)]
    )
    seen_alts: set[str] = set()
    result: list[tuple[str, float]] = []
    for alt, af in raw:
        if alt not in seen_alts:
            seen_alts.add(alt)
            result.append((alt, af))
    return result
