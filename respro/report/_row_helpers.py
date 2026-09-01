"""Shared row-building helpers for the HTML and TSV report exporters.

Keeps the combined-codon / user-reference NT-change formatting, the
chrom -> reference_name map, the feature display-name map, the
interpretation-algorithm DB helpers, and the metadata-only effect-as-resistant
rule selector in one place so the HTML and TSV writers cannot drift apart.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from respro.db.models import AnnotatedVariant, FeatureRecord, ProfilingResult

logger = logging.getLogger(__name__)

# Matches a nucleotide accession with an optional trailing version, e.g.
# ``NC_001806`` or ``NC_001806.1``. Used to match a configured reference
# against an observed reference ignoring the version suffix.
_ACCESSION_IDENTIFIER_RE = re.compile(
    r'^(?P<base>(?:[A-Z]{1,6}_[A-Z0-9]*\d[A-Z0-9]*|[A-Z]{1,6}\d[A-Z0-9]*))(?:\.(?P<version>\d+))?$'
)


def nt_change_stored(ann: AnnotatedVariant) -> str:
    """Return the internal-reference nucleotide change for one annotation.

    Combined codon events (multiple high-AF SNPs in one codon) are reported at
    codon resolution ``ref_codon{codon_pos+1}alt_codon``; all other variants use
    the raw VCF coordinates ``ref{pos+1}alt`` (1-based position).

    :param ann: annotated variant
    :return: formatted NT change string
    """
    if ann.is_combined_codon_event and ann.ref_codon and ann.alt_codon:
        return f'{ann.ref_codon}{ann.codon_pos + 1}{ann.alt_codon}'
    return f'{ann.variant.ref}{ann.variant.pos + 1}{ann.variant.alt}'


def nt_change_user(ann: AnnotatedVariant) -> str:
    """Return the user-supplied-reference nucleotide change, or '' when absent.

    Built from the preserved user-ref coords (VCF CHROM/POS/REF/ALT before
    remap to internal coordinates). FASTA-emitted variants carry no user
    reference, so this returns an empty string for them.

    :param ann: annotated variant
    :return: formatted user-ref NT change, or '' in FASTA mode
    """
    if not ann.has_user_ref_coords:
        return ''
    _user_chrom, user_pos, user_ref, user_alt = ann.user_ref_coords
    return f'{user_ref}{user_pos + 1}{user_alt}'


def build_reference_name_by_chrom(result: ProfilingResult) -> dict[str, str]:
    """Map each annotation chrom (== ReferenceGroup.query_name) to its reference_name.

    Keying by chrom (not feature_name) is unambiguous even when two references
    share a feature name, and matches the pattern used by ``write_json`` and
    ``build_report_context``.

    :param result: profiling result
    :return: dict mapping chrom -> reference_name (empty string for unknown chroms)
    """
    return {rg.query_name: rg.reference_name for rg in result.references}


def build_feature_display_names(features: list[FeatureRecord] | None) -> dict[str, str]:
    """Build a feature-name -> display-name mapping from loaded feature records.

    ``FeatureRecord.display_name`` is the protein for mat_peptides (if present),
    else the feature name. The HTML Database Hits / All Mutations tables use this
    map; the TSV writer uses it too so the ``gene`` column matches the HTML.

    :param features: loaded feature records (may be ``None`` or empty)
    :return: dict mapping feature name -> display name (empty when no features)
    """
    if not features:
        return {}
    return {feature.name: feature.display_name for feature in features}


def load_algorithm_config(
    project_conn: sqlite3.Connection | None,
    algorithm_name: str,
) -> dict | None:
    """Load one interpretation algorithm config by name from the project DB.

    :param project_conn: open project DB connection (``None`` -> no config)
    :param algorithm_name: algorithm name to look up
    :return: parsed config dict, or ``None`` when absent / unreadable / not a dict
    """
    if project_conn is None:
        return None
    try:
        row = project_conn.execute(
            'SELECT config_json FROM interpretation_algorithm '
            'WHERE algorithm_name = ? LIMIT 1',
            (algorithm_name,),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to load %s algorithm from DB: %s', algorithm_name, exc)
        return None

    if row is None:
        return None

    try:
        config = json.loads(row['config_json'])
    except (TypeError, json.JSONDecodeError) as exc:
        logger.debug('Failed to parse %s algorithm config JSON: %s', algorithm_name, exc)
        return None
    if not isinstance(config, dict):
        return None
    return config


def has_any_phenotype_association(project_conn: sqlite3.Connection | None) -> bool:
    """Return whether any rule row carries a known phenotype field.

    Guards the effect-as-resistant synthetic rows so they are only emitted when
    the project DB actually records phenotype associations.

    :param project_conn: open project DB connection (``None`` -> False)
    :return: True when at least one rule has a non-unknown phenotype/clinical_phenotype
    """
    if project_conn is None:
        return False
    known_clause = (
        "(TRIM(COALESCE(phenotype, '')) <> '' AND LOWER(TRIM(phenotype)) <> 'unknown') "
        "OR (TRIM(COALESCE(clinical_phenotype, '')) <> '' "
        "AND LOWER(TRIM(clinical_phenotype)) <> 'unknown')"
    )
    try:
        row = project_conn.execute(
            f'SELECT (EXISTS(SELECT 1 FROM resistance_rule WHERE {known_clause}) '
            f'OR EXISTS(SELECT 1 FROM resistance_formula_rule WHERE {known_clause})) AS has_rows'
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug('Failed to check phenotype association rows in DB: %s', exc)
        return False

    if row is None:
        return False
    return bool(row['has_rows'])


def references_match_with_accession_version(
    configured_reference: str,
    observed_reference: str,
) -> bool:
    """Return whether two references match exactly or by accession base plus version.

    Two accessions match when they are equal, or when both parse as accessions
    and share the same base (ignoring the trailing ``.version``).

    :param configured_reference: reference identifier from the algorithm config
    :param observed_reference: reference name observed in the profiling result
    :return: True when the references are considered the same
    """
    if configured_reference == observed_reference:
        return True

    configured_match = _ACCESSION_IDENTIFIER_RE.fullmatch(configured_reference)
    observed_match = _ACCESSION_IDENTIFIER_RE.fullmatch(observed_reference)
    if configured_match is None or observed_match is None:
        return False

    return configured_match.group('base') == observed_match.group('base')


@dataclass(frozen=True)
class EffectAsResistantMatch:
    """One effect-as-resistant rule match against an annotated variant.

    The selector returns these pairs so the HTML and TSV row shapers only need to
    format their own column layout; the rule-selection logic is shared.
    """

    annotation: AnnotatedVariant
    rule: dict


def select_effect_as_resistant_rules(
    result: ProfilingResult,
    project_conn: sqlite3.Connection | None,
) -> list[EffectAsResistantMatch]:
    """Select metadata-only effect-as-resistant rule matches for a result.

    Loads the ``effect_as_resistant`` interpretation-algorithm config from the
    project DB, filters its rules to those whose ``reference`` matches the
    result's reference, groups them by feature, and returns one
    :class:`EffectAsResistantMatch` per (annotation × matching rule) whose
    ``effect`` list contains the annotation's consequence and whose drug is
    non-empty.

    Returns an empty list when there is no project DB, no phenotype association,
    no configured algorithm, or no matching rules.

    :param result: profiling result
    :param project_conn: optional project DB connection holding the algorithm config
    :return: ordered list of (annotation, rule) matches
    """
    if project_conn is None:
        return []
    if not has_any_phenotype_association(project_conn):
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
        if not references_match_with_accession_version(reference, result.reference_name):
            continue
        rules_by_feature.setdefault(feature, []).append(rule)
    if not rules_by_feature:
        return []

    matches: list[EffectAsResistantMatch] = []
    for ann in result.cds_annotations:
        feature_rules = rules_by_feature.get(ann.feature_name, [])
        if not feature_rules:
            continue
        for rule in feature_rules:
            rule_effects = rule.get('effect', [])
            if ann.consequence not in rule_effects:
                continue
            drug_name = (rule.get('drug') or '').strip()
            if not drug_name:
                continue
            matches.append(EffectAsResistantMatch(annotation=ann, rule=rule))
    return matches
