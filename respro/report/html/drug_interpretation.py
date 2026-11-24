"""Per-drug interpretation table aggregation and algorithm evaluation."""

from __future__ import annotations

import sqlite3

from respro.core.algorithms import (
    METHOD_LABEL,
    assessment_strength,
    compute_drug_assessment,
    resolve_thresholds_dict,
    resolve_thresholds_with_source,
)
from respro.core.phenotype_ranks import (
    RANK_CONTRADICTORY,
    label_to_rank,
    most_frequent_label,
    rank_to_label,
)
from respro.db.models import ProfilingResult
from respro.db.report_queries import (
    load_drug_class_map,
    load_drug_interpretation_configs,
    load_in_scope_drug_names,
    load_tier1_phenotype_rows,
    parse_numeric_value,
)
from respro.report.html.formatting import format_drug_name_with_alias, phenotype_badge_class


def _resolve_tier1_labels(
    project_conn: sqlite3.Connection | None,
    reference_ids: set[int],
) -> tuple[dict[str, str], str | None]:
    """Resolve per-drug and scope-wide most frequent rank-1 phenotype labels."""
    rows = load_tier1_phenotype_rows(project_conn, reference_ids)
    counts: dict[str, dict[str, int]] = {}
    scope_totals: dict[str, int] = {}
    for name, phenotype, clinical_phenotype, count in rows:
        chosen = ''
        for value in (phenotype, clinical_phenotype):
            candidate = (value or '').strip().lower()
            if candidate and candidate != 'unknown':
                chosen = candidate
                break
        if label_to_rank(chosen) != 1:
            continue
        bucket = counts.setdefault((name or '').strip().lower(), {})
        bucket[chosen] = bucket.get(chosen, 0) + count
        scope_totals[chosen] = scope_totals.get(chosen, 0) + count
    per_drug = {
        name: winning
        for name, bucket in counts.items()
        if (winning := most_frequent_label(bucket)) is not None
    }
    return per_drug, most_frequent_label(scope_totals)


def _config_rank1_labels(
    config: dict,
    drug_reference_names: dict[str, set[str | None]],
) -> set[str]:
    """Collect every rank-1 threshold label a numeric interpretation method can resolve to."""
    if config.get('method') == 'by_phenotype':
        return set()
    labels: set[str] = set()
    for drug_name, ref_names in drug_reference_names.items():
        for ref in sorted(ref_names, key=lambda r: (r is None, r or '')):
            resolved = resolve_thresholds_dict(config, ref, drug_name)
            labels.update(str(label) for label in resolved if label_to_rank(label) == 1)
    return labels


def _assessment_description(method: str, thresholds: dict, tier1_label: str) -> str:
    """Build a human-readable tooltip describing one interpretation method's rules."""
    ranked = sorted(
        (rank, lbl, val)
        for lbl, val in thresholds.items()
        if (rank := label_to_rank(lbl)) is not None and rank > 0
    )
    if method == 'by_phenotype':
        return ' '.join([
            'Highest-rank phenotype label among the drug\u2019s hits wins.',
            f'Contradictory: wins over {tier1_label} but loses to any higher-tier severity.',
            f'Otherwise: {tier1_label.title()}.',
        ])
    unit_by_method = {
        'by_score': 'total score',
        'by_ic50': 'any IC50 value',
        'by_fold_ic50': 'any fold IC50 value',
    }
    if (unit := unit_by_method.get(method)) is not None:
        return ' '.join(f'{lbl.title()}: {unit} \u2265 {val}.' for _rank, lbl, val in ranked)
    return ''


def _init_drug_entry(name: str, drug_class: str, alias_map: dict[str, str]) -> dict:
    """Initialise one per-drug aggregation record."""
    alias = alias_map.get(name.strip().lower(), '')
    return {
        'name': format_drug_name_with_alias(name, alias_map),
        'summary_name': alias if alias and len(alias) < len(name) else name,
        'drug_class': drug_class,
        'hit_count': 0,
        'rank_counts': {},
        'rank_label_counts': {},
        'score_total': 0.0,
        'score_display': '0',
        'ic50_display': '\u2014',
        'fold_ic50_display': '\u2014',
        'ic50_values': [],
        'fold_ic50_values': [],
        'assessment': '',
        'assessment_badge_class': '',
        'method_assessments': [],
        'tier1_label': None,
        'reference_names': set(),
    }


def _accumulate_drug_hits(by_drug: dict[str, dict], hit_rows: list[dict]) -> None:
    """Accumulate hit counts, phenotype ranks, scores, and IC50 metrics per drug."""
    for row in hit_rows:
        drug = (row.get('drug_key') or row.get('drug') or 'Unknown').strip().lower()
        entry = by_drug.get(drug)
        if entry is None:
            continue
        entry['hit_count'] += 1
        if ref_name := (row.get('reference_name') or '').strip():
            entry['reference_names'].add(ref_name)

        metrics = row.get('metrics', [])
        metrics_by_label: dict[str, dict] = {}
        for metric in metrics:
            metrics_by_label.setdefault(metric.get('label'), metric)

        pheno = ''
        for metric in metrics:
            if metric.get('label') in ('Phenotype', 'Clinical phenotype'):
                pheno = (metric.get('value') or '').strip().lower()
                if pheno and pheno != 'unknown':
                    break
        if pheno and pheno != 'unknown':
            rank = label_to_rank(pheno)
            if rank is not None:
                entry['rank_counts'][rank] = entry['rank_counts'].get(rank, 0) + 1
                label_counts = entry['rank_label_counts'].setdefault(rank, {})
                label_counts[pheno] = label_counts.get(pheno, 0) + 1

        if (score_m := metrics_by_label.get('Score')) is not None:
            if (val := parse_numeric_value((score_m.get('value') or '').strip())) is not None:
                entry['score_total'] += val
        if (ic50_m := metrics_by_label.get('IC50')) is not None:
            if (val := parse_numeric_value((ic50_m.get('value') or '').strip())) is not None:
                entry['ic50_values'].append(val)
        if (fold_m := metrics_by_label.get('Fold IC50')) is not None:
            if (val := parse_numeric_value((fold_m.get('value') or '').strip())) is not None:
                entry['fold_ic50_values'].append(val)


def _evaluate_drug_assessments(
    by_drug: dict[str, dict],
    drug_interp_configs: list[dict],
    default_reference_name: str,
    has_drug_thresholds: bool,
) -> None:
    """Compute per-method and final assessments (with strongest-wins across references)."""
    config_by_method = {c.get('method'): c for c in drug_interp_configs}
    for drug_name, drug_data in by_drug.items():
        ref_names = drug_data.get('reference_names') or (
            {default_reference_name} if default_reference_name else set()
        )
        # Sort references deterministically so override resolution never depends on set hash order.
        sorted_refs: list[str | None] = sorted(ref_names) if ref_names else [None]

        if len(sorted_refs) > 1:
            best_assessment = ''
            best_methods: list[dict] = []
            selected_reference = sorted_refs[0]
            best_strength = assessment_strength('')
            for candidate_ref in sorted_refs:
                cand_assessment, cand_methods = compute_drug_assessment(
                    drug_data, drug_interp_configs,
                    reference_name=candidate_ref,
                    drug_name=drug_name or None,
                )
                strength = assessment_strength(cand_assessment)
                if strength > best_strength:
                    best_strength = strength
                    best_assessment = cand_assessment
                    best_methods = cand_methods
                    selected_reference = candidate_ref
            final_assessment, method_assessments = best_assessment, best_methods
        else:
            selected_reference = sorted_refs[0]
            final_assessment, method_assessments = compute_drug_assessment(
                drug_data, drug_interp_configs,
                reference_name=selected_reference,
                drug_name=drug_name or None,
            )

        drug_data['assessment'] = final_assessment
        drug_data['method_assessments'] = method_assessments
        for ma in method_assessments:
            ma['assessment_badge_class'] = phenotype_badge_class(ma['assessment'])
            if has_drug_thresholds and drug_name and ma['method'] != 'by_phenotype':
                config = config_by_method.get(ma['method'], {})
                resolved, source = resolve_thresholds_with_source(config, selected_reference, drug_name)
                ma['resolved_thresholds'] = resolved
                ma['threshold_source'] = source


def _build_phenotype_columns(drug_rows: list[dict]) -> list[dict]:
    """Build ordered phenotype count columns preserving the database's verbatim labels."""
    present_ranks: set[int] = set()
    rank_labels: dict[int, dict[str, int]] = {}
    for row in drug_rows:
        present_ranks.update(row['rank_counts'].keys())
        for rank, labels in row['rank_label_counts'].items():
            bucket = rank_labels.setdefault(rank, {})
            for label, count in labels.items():
                bucket[label] = bucket.get(label, 0) + count

    ordered_ranks = sorted((r for r in present_ranks if r != RANK_CONTRADICTORY), reverse=True)
    if RANK_CONTRADICTORY in present_ranks:
        ordered_ranks.append(RANK_CONTRADICTORY)

    return [
        {
            'rank': rank,
            'label': (most_frequent_label(rank_labels.get(rank, {})) or rank_to_label(rank)).title(),
            'badge_class': phenotype_badge_class(rank_to_label(rank)),
        }
        for rank in ordered_ranks
    ]


def build_drug_interpretation_table(
    result: ProfilingResult,
    database_hits: dict,
    project_conn: sqlite3.Connection | None,
    drug_alias_map: dict[str, str] | None = None,
) -> dict:
    """Build the per-drug summary table covering all in-scope drugs across profiled references."""
    alias_map = drug_alias_map or {}
    hit_rows = database_hits.get('rows', [])

    # Scope across ALL profiled ReferenceGroups, not only the primary reference.
    profiled_feature_ids = {m.feature.id for rg in result.references for m in rg.feature_matches}
    profiled_reference_ids = {m.feature.reference_id for rg in result.references for m in rg.feature_matches}
    drug_class_map = load_drug_class_map(project_conn)

    by_drug: dict[str, dict] = {}
    for name in load_in_scope_drug_names(project_conn, profiled_feature_ids):
        key = name.strip().lower()
        if key not in by_drug:
            by_drug[key] = _init_drug_entry(name, drug_class_map.get(key, ''), alias_map)

    for row in hit_rows:
        drug = (row.get('drug_key') or row.get('drug') or 'Unknown').strip()
        key = drug.lower()
        if key not in by_drug and key != '__formula_component__':
            dc = row.get('drug_class') or drug_class_map.get(key, '')
            by_drug[key] = _init_drug_entry(drug, dc, alias_map)

    if not by_drug:
        return {
            'rows': [],
            'groups': {},
            'has_groups': False,
            'has_phenotypes': False,
            'has_scores': False,
            'has_assessment': False,
            'has_method_assessments': False,
            'has_final_assessment': False,
            'has_drug_thresholds': False,
            'method_labels': [],
            'assessment_description': '',
            'col_count': 2,
            'phenotype_columns': [],
        }

    tier1_by_drug, scope_tier1_label = _resolve_tier1_labels(project_conn, profiled_reference_ids)
    for name, entry in by_drug.items():
        entry['tier1_label'] = tier1_by_drug.get(name) or scope_tier1_label

    _accumulate_drug_hits(by_drug, hit_rows)

    has_phenotypes = any(sum(d['rank_counts'].values()) > 0 for d in by_drug.values())
    has_scores = any(
        any(m.get('label') == 'Score' and (m.get('value') or '').strip() for m in row.get('metrics', []))
        for row in hit_rows
    )
    has_groups = any(d['drug_class'] for d in by_drug.values())

    drug_interp_configs = load_drug_interpretation_configs(project_conn)
    has_assessment = len(drug_interp_configs) > 0
    has_final_assessment = len(drug_interp_configs) > 1
    has_drug_thresholds = any(bool(config.get('drug_thresholds')) for config in drug_interp_configs)

    tier1_labels = {entry['tier1_label'] for entry in by_drug.values() if entry.get('tier1_label')}
    if any(not entry.get('tier1_label') for entry in by_drug.values()):
        tier1_labels.add(rank_to_label(1))

    drug_reference_names: dict[str, set[str | None]] = {
        name: entry['reference_names'] or ({result.reference_name} if result.reference_name else {None})
        for name, entry in by_drug.items()
    }
    numeric_tier1_by_method = {
        config.get('method', ''): _config_rank1_labels(config, drug_reference_names)
        for config in drug_interp_configs
        if config.get('method') != 'by_phenotype'
    }
    phenotype_tier1_display = ' or '.join(sorted(tier1_labels))
    all_tier1_display = ' or '.join(sorted(
        tier1_labels | {lbl for labels in numeric_tier1_by_method.values() for lbl in labels}
    ))

    method_labels: list[dict] = []
    assessment_description = ''
    if has_assessment:
        value_col_by_method = {
            'by_ic50': ('Highest IC50', 'ic50_display'),
            'by_fold_ic50': ('Highest Fold IC50', 'fold_ic50_display'),
        }
        for config in drug_interp_configs:
            method = config.get('method', '')
            value_header, value_field = value_col_by_method.get(method, (None, None))
            method_tier1 = (
                phenotype_tier1_display
                if method == 'by_phenotype'
                else (' or '.join(sorted(numeric_tier1_by_method.get(method, set()))) or phenotype_tier1_display)
            )
            method_labels.append({
                'method': method,
                'label': METHOD_LABEL.get(method, method),
                'description': _assessment_description(method, config.get('thresholds', {}), method_tier1),
                'value_header': value_header,
                'value_field': value_field,
            })
        assessment_description = (
            'Final assessment: most severe result across all methods '
            f'(resistant > \u2026 > potential low-level resistance > contradictory > {all_tier1_display}).'
        )
        _evaluate_drug_assessments(by_drug, drug_interp_configs, result.reference_name, has_drug_thresholds)

    for drug_data in by_drug.values():
        drug_data['assessment_badge_class'] = phenotype_badge_class(drug_data['assessment'])
        score = drug_data['score_total']
        drug_data['score_display'] = str(int(score)) if score == int(score) else f'{score:.2g}'
        if drug_data['ic50_values']:
            drug_data['ic50_display'] = f"{max(drug_data['ic50_values']):g}"
        if drug_data['fold_ic50_values']:
            drug_data['fold_ic50_display'] = f"{max(drug_data['fold_ic50_values']):g}"

    drug_rows = sorted(by_drug.values(), key=lambda d: d['name'].lower())
    groups: dict[str, list[dict]] = {}
    for drug_data in drug_rows:
        groups.setdefault(drug_data['drug_class'], []).append(drug_data)

    phenotype_columns = _build_phenotype_columns(drug_rows)
    num_value_columns = sum(1 for ml in method_labels if ml['value_header'])
    col_count = (
        2
        + (len(phenotype_columns) if has_phenotypes else 0)
        + (1 if has_scores else 0)
        + (len(method_labels) + num_value_columns if has_assessment else 0)
        + (1 if has_final_assessment else 0)
    )
    return {
        'rows': drug_rows,
        'groups': groups,
        'has_groups': has_groups,
        'has_phenotypes': has_phenotypes,
        'has_scores': has_scores,
        'has_assessment': has_assessment,
        'has_method_assessments': has_assessment,
        'has_final_assessment': has_final_assessment,
        'has_drug_thresholds': has_drug_thresholds,
        'method_labels': method_labels,
        'assessment_description': assessment_description,
        'col_count': col_count,
        'phenotype_columns': phenotype_columns,
    }
