"""Summary tab context: sequence assessment, gene coverage, mutation profile, and narrative."""

from __future__ import annotations

import sqlite3
from collections import Counter

from markupsafe import Markup, escape

from respro.core.annotation import CONSEQUENCE_LABELS, HIGH_IMPACT_CONSEQUENCES
from respro.core.phenotype_ranks import RANK_UNKNOWN, label_to_rank, rank_to_colour
from respro.db.models import ProfilingResult
from respro.db.report_queries import has_interpretation_algorithm
from respro.report.html.drug_interpretation import build_drug_interpretation_table
from respro.report.html.formatting import join_english_list, load_svg_data_url

_SYNONYMOUS_CONSEQUENCES: frozenset[str] = frozenset({'synonymous_variant', 'synonymous'})


def coverage_was_assessed(result: ProfilingResult) -> bool:
    """Return True when sequence coverage was measured (always in FASTA mode, or with a BAM)."""
    return result.coverage_assessed or result.is_fasta_mode


def build_summary_context(
    result: ProfilingResult,
    display_names: dict[str, str],
    database_hits: dict,
    similarity_entries: dict,
    project_conn: sqlite3.Connection | None,
    drug_alias_map: dict[str, str] | None = None,
    formula_hit_annotation_ids: set[int] | None = None,
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> dict:
    """Build the complete context dict for the Summary tab."""
    del similarity_entries  # Kept in signature for caller symmetry.
    formula_ids = formula_hit_annotation_ids or set()
    ref_by_chrom = reference_name_by_chrom or {}
    gene_coverage = compute_gene_coverage(result, display_names) if coverage_was_assessed(result) else None
    drug_table = build_drug_interpretation_table(
        result, database_hits, project_conn, drug_alias_map=drug_alias_map,
    )
    single_rule_hit_count = sum(len(ann.non_formula_component_rule_matches) for ann in result.cds_annotations)
    return {
        'sequence_assessment': build_sequence_assessment(result, display_names),
        'mutation_profile': build_mutation_profile(
            result, display_names, gene_coverage, formula_ids,
            is_multi_species=is_multi_species, reference_name_by_chrom=ref_by_chrom,
        ),
        'has_coverage': gene_coverage is not None,
        'coverage_not_assessed': gene_coverage is None and any(rg.feature_matches for rg in result.references),
        'has_narrative': has_interpretation_algorithm(project_conn),
        'narrative': str(build_summary_narrative(
            result, display_names, drug_table, formula_ids,
            is_multi_species=is_multi_species, reference_name_by_chrom=ref_by_chrom,
        )),
        'drug_table': drug_table,
        'db_hits_summary': {
            'total': database_hits.get('count', 0),
            'single_rule_hits': single_rule_hit_count,
            'formula_rule_hits': len(result.formula_hits),
        },
        'dna_icon': load_svg_data_url('dna.svg'),
        'db_icon': load_svg_data_url('icon-database.svg'),
        'info_icon': load_svg_data_url('info.svg'),
        'report_icon': load_svg_data_url('report.svg'),
        'drug_icon': load_svg_data_url('drug.svg'),
    }


def build_sequence_assessment(
    result: ProfilingResult,
    display_names: dict[str, str],
) -> dict:
    """Summarise mutation counts and high-impact consequences across CDS annotations."""
    feature_seen: set[str] = set()
    high_impact_features: set[str] = set()
    high_impact_by_consequence: Counter[str] = Counter()
    non_synonymous_count = 0

    for ann in result.cds_annotations:
        feature = display_names.get(ann.feature_name, ann.feature_name)
        if ann.consequence not in _SYNONYMOUS_CONSEQUENCES:
            non_synonymous_count += 1
            feature_seen.add(feature)
        if ann.consequence in HIGH_IMPACT_CONSEQUENCES:
            high_impact_features.add(feature)
            high_impact_by_consequence[ann.consequence] += 1

    high_impact_parts = [
        f"{cnt} {CONSEQUENCE_LABELS[c]}{'s' if cnt != 1 else ''}"
        for c in CONSEQUENCE_LABELS
        if (cnt := high_impact_by_consequence.get(c, 0))
    ]
    return {
        'total_mutations': len(result.cds_annotations),
        'non_synonymous_count': non_synonymous_count,
        'features_with_mutations': sorted(feature_seen),
        'features_count': len(feature_seen),
        'high_impact_count': sum(high_impact_by_consequence.values()),
        'high_impact_types': join_english_list(high_impact_parts) if high_impact_parts else '',
        'high_impact_features': sorted(high_impact_features),
    }


def compute_gene_coverage(
    result: ProfilingResult,
    display_names: dict[str, str],
) -> dict[tuple[str, str], int] | None:
    """Compute covered percentage per ``(reference_name, feature_display_name)``."""
    group_matches = [(rg.reference_name, rg.feature_matches) for rg in result.references if rg.feature_matches]
    if not group_matches:
        return None

    total_codons: dict[tuple[str, str], int] = {}
    for ref_name, matches in group_matches:
        for match in matches:
            feature = match.feature
            count = max(0, (len(feature.nt_sequence) - feature.codon_start) // 3)
            key = (ref_name, feature.name)
            total_codons[key] = max(total_codons.get(key, 0), count)

    chrom_to_ref = {rg.query_name: rg.reference_name for rg in result.references}
    non_covered: dict[tuple[str, str], int] = {}
    unattributed: dict[str, int] = {}
    for gap in result.coverage_gaps:
        codons = gap.codon_end - gap.codon_start + 1
        gap_ref = chrom_to_ref.get(gap.chrom)
        if gap_ref is None:
            unattributed[gap.feature_name] = unattributed.get(gap.feature_name, 0) + codons
        else:
            key = (gap_ref, gap.feature_name)
            non_covered[key] = non_covered.get(key, 0) + codons

    coverage: dict[tuple[str, str], int] = {}
    for (ref_name, feature_name), total in total_codons.items():
        if not total:
            continue
        display = display_names.get(feature_name, feature_name)
        nc = min(non_covered.get((ref_name, feature_name), 0) + unattributed.get(feature_name, 0), total)
        coverage[(ref_name, display)] = round(100 * (total - nc) / total)
    return coverage


def build_mutation_profile(
    result: ProfilingResult,
    display_names: dict[str, str],
    gene_coverage: dict[tuple[str, str], int] | None,
    formula_hit_annotation_ids: set[int],
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> list[dict]:
    """Group non-synonymous amino-acid changes by feature (and reference in multi-species runs)."""
    ref_by_chrom = reference_name_by_chrom or {}
    seen: dict[tuple[str | None, str, int, str], dict] = {}
    for ann in result.cds_annotations:
        if ann.consequence in _SYNONYMOUS_CONSEQUENCES:
            continue
        feature = display_names.get(ann.feature_name, ann.feature_name)
        ref_name = ref_by_chrom.get(ann.variant.chrom) if is_multi_species else None
        label = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}' if ann.ref_aa and ann.alt_aa else ann.consequence
        key = (ref_name, feature, ann.codon_pos, label)
        is_hit = ann.is_resistance_hit or id(ann) in formula_hit_annotation_ids
        is_high = ann.consequence in HIGH_IMPACT_CONSEQUENCES
        if key in seen:
            seen[key]['is_db_hit'] = seen[key]['is_db_hit'] or is_hit
            seen[key]['is_high_impact'] = seen[key]['is_high_impact'] or is_high
        else:
            seen[key] = {'label': label, 'is_db_hit': is_hit, 'is_high_impact': is_high}

    # Seed every profiled feature so zero-mutation features still report their coverage.
    feature_mutations: dict[tuple[str | None, str], list[tuple[int, dict]]] = {}
    for rg in result.references:
        seed_ref = rg.reference_name if is_multi_species else None
        for match in rg.feature_matches:
            feature_mutations.setdefault((seed_ref, display_names.get(match.feature.name, match.feature.name)), [])
    for (ref_name, feature, codon_pos, _label), entry in seen.items():
        feature_mutations.setdefault((ref_name, feature), []).append((codon_pos, entry))

    rows: list[dict] = []
    for (ref_name, feature), entries in sorted(feature_mutations.items(), key=lambda kv: (kv[0][1], kv[0][0] or '')):
        if gene_coverage is None:
            covered_pct = None
        elif is_multi_species:
            covered_pct = gene_coverage.get((ref_name or '', feature))
        else:
            candidates = [pct for (_ref, disp), pct in gene_coverage.items() if disp == feature]
            covered_pct = min(candidates) if candidates else None
        row: dict = {
            'feature': feature,
            'mutations': [m for _, m in sorted(entries, key=lambda x: x[0])],
            'covered_pct': covered_pct,
        }
        if is_multi_species:
            row['reference_name'] = ref_name
        rows.append(row)
    return rows


def build_summary_narrative(
    result: ProfilingResult,
    display_names: dict[str, str],
    drug_table: dict,
    formula_hit_annotation_ids: set[int],
    is_multi_species: bool = False,
    reference_name_by_chrom: dict[str, str] | None = None,
) -> Markup:
    """Build the clinician-facing summary narrative with coverage and high-impact caveats."""
    del reference_name_by_chrom
    paragraphs: list[str] = []
    drug_rows = drug_table.get('rows', [])
    has_assessment = bool(drug_table.get('has_assessment'))
    assessed_rows = [row for row in drug_rows if (row.get('assessment') or '').strip()]

    label_drugs: dict[str, list[str]] = {}
    label_ranks: dict[str, int] = {}

    def _register_label(label: str, name: str) -> None:
        cleaned = label.strip()
        rank = label_to_rank(cleaned)
        if rank is None or rank == RANK_UNKNOWN:
            return
        drugs = label_drugs.setdefault(cleaned, [])
        if name not in drugs:
            drugs.append(name)
        label_ranks.setdefault(cleaned, rank)

    for row in assessed_rows:
        name = row.get('summary_name') or row.get('name') or 'Unknown'
        if assessment := (row.get('assessment') or '').strip():
            _register_label(assessment, name)
        for labels in (row.get('rank_label_counts') or {}).values():
            for label, count in labels.items():
                if count > 0:
                    _register_label(label, name)

    all_feature_matches = [match for rg in result.references for match in rg.feature_matches]
    profiled_features = sorted({display_names.get(m.feature.name, m.feature.name) for m in all_feature_matches})
    organism_by_ref_id = {rg.reference_id: rg.organism for rg in result.references if rg.organism}

    if is_multi_species and profiled_features:
        features_by_org: dict[str, list[str]] = {}
        seen_org_feature: set[tuple[str, str]] = set()
        for match in sorted(
            all_feature_matches,
            key=lambda m: (display_names.get(m.feature.name, m.feature.name), m.feature.reference_id),
        ):
            feat_label = display_names.get(match.feature.name, match.feature.name)
            org = organism_by_ref_id.get(match.feature.reference_id) or result.organism or 'Unknown organism'
            if (org, feat_label) not in seen_org_feature:
                seen_org_feature.add((org, feat_label))
                features_by_org.setdefault(org, []).append(feat_label)
        clauses = [
            f'{join_english_list([escape(f) for f in feats])} of <strong>{escape(org)}</strong>'
            for org, feats in features_by_org.items()
        ]
        feature_clause = 'The tested sequence information mapped to ' + join_english_list(clauses)
    elif profiled_features:
        org_html = escape(result.organism) if result.organism else 'Unknown organism'
        feature_clause = (
            f'The tested sequence information mapped to '
            f'{join_english_list([escape(f) for f in profiled_features])} of <strong>{org_html}</strong>'
        )
    else:
        org_html = escape(result.organism) if result.organism else 'Unknown organism'
        feature_clause = f'The tested sequence information of <strong>{org_html}</strong> mapped to no profiled features'

    n_drugs = len(assessed_rows) if assessed_rows else len(drug_rows)
    if has_assessment and n_drugs:
        drug_word = 'drug' if n_drugs == 1 else 'drugs'
        paragraphs.append(
            f'{feature_clause} and was evaluated against known resistance-associated mutations '
            f'for {n_drugs} {drug_word}.'
        )
    elif drug_rows:
        paragraphs.append(
            f'{feature_clause} and was evaluated against known resistance-associated mutations, '
            'but no final drug interpretation algorithm is configured.'
        )
    else:
        paragraphs.append(
            f'{feature_clause} and was evaluated, but no in-scope drugs were available for interpretation.'
        )

    uncovered_positions = sum(max(0, gap.codon_end - gap.codon_start + 1) for gap in result.coverage_gaps)
    coverage_gap_features = sorted({display_names.get(gap.feature_name, gap.feature_name) for gap in result.coverage_gaps})
    if uncovered_positions > 0 and coverage_gap_features:
        paragraphs.append(
            f"However, {uncovered_positions} position{'s' if uncovered_positions != 1 else ''} of "
            f'{join_english_list([escape(f) for f in coverage_gap_features])} could not be assessed '
            'due to incomplete sequence data.'
        )
    elif not coverage_was_assessed(result) and any(rg.feature_matches for rg in result.references):
        paragraphs.append(
            'However, sequence coverage was not assessed: no read-depth (BAM) data was '
            'provided, so the completeness of the analysed regions is unknown.'
        )

    high_impact_without_db = [
        ann
        for ann in result.cds_annotations
        if ann.consequence in HIGH_IMPACT_CONSEQUENCES
        and not ann.is_resistance_hit
        and id(ann) not in formula_hit_annotation_ids
    ]
    if high_impact_without_db:
        paragraphs.append(
            f'Importantly, {len(high_impact_without_db)} high-impact variant'
            f"{'s were' if len(high_impact_without_db) != 1 else ' was'} detected and require"
            ' manual interpretation.'
        )

    narrative_text = ' '.join(paragraphs)
    if has_assessment and (assessed_rows or drug_rows):
        ordered_labels = sorted(label_drugs, key=lambda lbl: (-label_ranks[lbl], lbl))
        list_sections = [
            f'<strong style="color: {rank_to_colour(label_ranks[label])};">'
            f'{escape(f"Drugs assessed as {label}")}:</strong> '
            f'{join_english_list([escape(n) for n in sorted(label_drugs[label], key=str.lower)])}.'
            for label in ordered_labels
        ]
        if list_sections:
            narrative_text += '<br><br>' + '<br>'.join(list_sections)
    return Markup(narrative_text)
