"""Shared row-formatting and effect-as-resistant selection helpers for HTML and TSV exports."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from respro.core.algorithms import references_match
from respro.db.models import AnnotatedVariant, FeatureRecord, ProfilingResult
from respro.db.report_queries import has_any_phenotype_association, load_algorithm_config


def nt_change_stored(ann: AnnotatedVariant) -> str:
    """Return the internal-reference 1-based nucleotide change ``ref{pos+1}alt``."""
    return f'{ann.variant.ref}{ann.variant.pos + 1}{ann.variant.alt}'


def nt_change_user(ann: AnnotatedVariant) -> str:
    """Return the user-supplied-reference nucleotide change, or ``''`` in FASTA mode."""
    if not ann.has_user_ref_coords:
        return ''
    _user_chrom, user_pos, user_ref, user_alt = ann.user_ref_coords
    return f'{user_ref}{user_pos + 1}{user_alt}'


def variant_source_label(ann: AnnotatedVariant) -> str:
    """Return the display label for VCF variant provenance, or ``''`` when unset."""
    return {
        'vcf_derived': 'VCF derived',
        'reference_derived': 'Reference derived',
    }.get(ann.variant.source, '')


def display_consequence(ann: AnnotatedVariant) -> str:
    """Return the report consequence label, promoting combined-codon synonymous hits to ``missense``."""
    if (
        ann.is_combined_codon_event
        and ann.consequence == 'synonymous'
        and any(s.accepted and s.alt_aa and s.alt_aa != ann.ref_aa for s in ann.combined_states)
    ):
        return 'missense'
    return ann.consequence


def format_aa_effects(ann: AnnotatedVariant) -> str:
    """Format single-exchange and accepted combined-state amino-acid effects with frequencies."""
    parts: list[str] = []
    single_label = ''
    freq_tag = 'observed' if ann.freq_method == 'observed' else 'lower bound'
    # Gate on rounded frequency so sub-precision molecules never render as ``| 0.0 |``.
    if ann.alt_aa and round(ann.single_exchange_aa_freq, 3) > 0:
        single_label = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
        parts.append(f'{single_label} | {round(ann.single_exchange_aa_freq, 3)} | {freq_tag}')
    for state in ann.combined_states:
        if not state.accepted or round(state.lower, 3) <= 0:
            continue
        combined_label = f'{ann.ref_aa}{ann.codon_pos + 1}{state.alt_aa}'
        if combined_label == single_label:
            continue
        parts.append(f'{combined_label} | {round(state.lower, 3)} | {freq_tag}')
    return '; '.join(parts)


def build_reference_name_by_chrom(result: ProfilingResult) -> dict[str, str]:
    """Map each annotation chrom (``ReferenceGroup.query_name``) to its ``reference_name``."""
    return {rg.query_name: rg.reference_name for rg in result.references}


def build_feature_display_names(features: list[FeatureRecord] | None) -> dict[str, str]:
    """Map feature names to their report display names (protein for mat_peptides, else name)."""
    if not features:
        return {}
    return {feature.name: feature.display_name for feature in features}


@dataclass(frozen=True)
class EffectAsResistantMatch:
    """One metadata-only ``effect_as_resistant`` rule match against an annotated variant."""

    annotation: AnnotatedVariant
    rule: dict
    reference_name: str = ''


def select_effect_as_resistant_rules(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None,
) -> list[EffectAsResistantMatch]:
    """Select metadata-only ``effect_as_resistant`` matches scoped to each annotation's reference."""
    if project_conn is None or not has_any_phenotype_association(project_conn):
        return []

    effect_config = load_algorithm_config(project_conn, 'effect_as_resistant')
    if effect_config is None:
        return []
    config_rules = effect_config.get('rules')
    if not isinstance(config_rules, list) or not config_rules:
        return []

    rules_by_feature: dict[str, list[dict]] = {}
    for rule in config_rules:
        if not isinstance(rule, dict):
            continue
        feature = rule.get('feature')
        reference = rule.get('reference')
        drug = rule.get('drug')
        if not (isinstance(feature, str) and isinstance(reference, str) and isinstance(drug, str)):
            continue
        if not drug.strip():
            continue
        rules_by_feature.setdefault(feature, []).append(rule)
    if not rules_by_feature:
        return []

    ref_by_chrom = build_reference_name_by_chrom(result)
    matches: list[EffectAsResistantMatch] = []
    for ann in result.cds_annotations:
        feature_rules = rules_by_feature.get(ann.feature_name)
        if not feature_rules:
            continue
        # Match against the annotation's own reference so multi-reference runs scope accurately.
        observed_ref = ref_by_chrom.get(ann.variant.chrom) or result.reference_name
        for rule in feature_rules:
            if not references_match(rule['reference'], observed_ref):
                continue
            if ann.consequence not in rule.get('effect', []):
                continue
            matches.append(EffectAsResistantMatch(
                annotation=ann,
                rule=rule,
                reference_name=observed_ref,
            ))
    return matches
