"""Stats aggregation and info-card builders for sequence features and drugs."""

from __future__ import annotations

from collections import Counter

from respro.db.models import FeatureRecord, ProfilingResult
from respro.report.html.formatting import format_drug_name_with_alias


def collect_detected_drug_names(result: ProfilingResult) -> set[str]:
    """Collect lowercase drug names from single-rule and formula hits."""
    detected: set[str] = set()
    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            if drug_name := (rule.drug_name or '').strip():
                detected.add(drug_name.lower())
    for combo_hit in result.formula_hits:
        if drug_name := (combo_hit.rule_set.drug_name or '').strip():
            detected.add(drug_name.lower())
    return detected


def build_drug_stats(result: ProfilingResult) -> dict[str, dict]:
    """Build per-drug single-rule and formula hit counts."""
    direct_counter: Counter[str] = Counter()
    formula_counter: Counter[str] = Counter()
    display_names: dict[str, str] = {}

    for ann in result.cds_annotations:
        for rule in ann.non_formula_component_rule_matches:
            if drug_name := (rule.drug_name or '').strip():
                key = drug_name.lower()
                direct_counter[key] += 1
                display_names.setdefault(key, drug_name)

    for combo_hit in result.formula_hits:
        if drug_name := (combo_hit.rule_set.drug_name or '').strip():
            key = drug_name.lower()
            formula_counter[key] += 1
            display_names.setdefault(key, drug_name)

    return {
        key: {
            'name': display_names.get(key, key),
            'single_rule_hits': direct_counter.get(key, 0),
            'formula_hits': formula_counter.get(key, 0),
            'total_hits': direct_counter.get(key, 0) + formula_counter.get(key, 0),
        }
        for key in sorted(set(direct_counter) | set(formula_counter))
    }


def build_feature_stats(
    result: ProfilingResult,
    features: list[FeatureRecord] | None = None,
) -> dict[tuple[int, str], dict]:
    """Build observed-variant and database-hit counts per detected ``(reference_id, feature)``."""
    unassigned = 'Unassigned'
    ref_id_by_chrom = {rg.query_name: rg.reference_id for rg in result.references if rg.query_name}
    distinct_ref_ids = {rg.reference_id for rg in result.references}
    multi_ref = len(distinct_ref_ids) > 1
    default_ref_id = next(iter(distinct_ref_ids), 0) if len(distinct_ref_ids) == 1 else 0

    def _key(ref_id: int | None, feature_name: str) -> tuple[int, str]:
        return (ref_id if (multi_ref and ref_id is not None) else default_ref_id, feature_name)

    observed_counter: Counter[tuple[int, str]] = Counter()
    direct_counter: Counter[tuple[int, str]] = Counter()
    formula_counter: Counter[tuple[int, str]] = Counter()

    if features:
        seen_keys_by_feature: dict[tuple[int, str], set[tuple[str, int, str, str]]] = {
            _key(f.reference_id, f.name): set() for f in features
        }
        for ann in result.annotations:
            ann_ref_id = ref_id_by_chrom.get(ann.variant.chrom)
            variant_key = (ann.variant.chrom, ann.variant.pos, ann.variant.ref, ann.variant.alt)
            for feature in features:
                # Scope containment checks by reference_id in multi-reference runs so
                # coordinates on one genome never increment features on another.
                if multi_ref and ann_ref_id is not None and feature.reference_id != ann_ref_id:
                    continue
                if not feature.contains(ann.variant.pos):
                    continue
                fkey = _key(feature.reference_id, feature.name)
                seen_keys = seen_keys_by_feature.setdefault(fkey, set())
                if variant_key not in seen_keys:
                    seen_keys.add(variant_key)
                    observed_counter[fkey] += 1
    else:
        for ann in result.cds_annotations:
            if feature_name := (ann.feature_name or '').strip():
                observed_counter[_key(ref_id_by_chrom.get(ann.variant.chrom), feature_name)] += 1

    for ann in result.cds_annotations:
        feature_name = (ann.feature_name or '').strip() or unassigned
        fkey = _key(ref_id_by_chrom.get(ann.variant.chrom), feature_name)
        direct_counter[fkey] += len(ann.non_formula_component_rule_matches)

    for formula_hit in result.formula_hits:
        for fkey in {
            _key(ref_id_by_chrom.get(ann.variant.chrom), (ann.feature_name or '').strip() or unassigned)
            for ann in formula_hit.matched_variants
        }:
            formula_counter[fkey] += 1

    all_keys = set(observed_counter) | set(direct_counter) | set(formula_counter)
    return {
        fkey: {
            'name': fkey[1],
            'reference_id': fkey[0],
            'observed_variants': observed_counter.get(fkey, 0),
            'database_hits': direct_counter.get(fkey, 0) + formula_counter.get(fkey, 0),
        }
        for fkey in sorted(all_keys, key=lambda item: (item[1].lower(), item[0]))
    }


def build_drug_cards(
    drug_stats: dict[str, dict],
    db_drug_cards_by_name: dict[str, dict],
    drug_alias_map: dict[str, str] | None = None,
) -> list[dict]:
    """Merge per-drug hit counts with optional DB metadata into card payloads."""
    alias_map = drug_alias_map or {}
    cards: list[dict] = []
    for key in sorted(drug_stats, key=lambda name: drug_stats[name]['name'].lower()):
        stats = dict(drug_stats[key])
        stats['name'] = format_drug_name_with_alias(stats['name'], alias_map)
        metadata = db_drug_cards_by_name.get(key)
        stats.update({
            'pubchem_url': metadata.get('pubchem_url', '') if metadata else '',
            'description': metadata.get('description', '') if metadata else '',
            'structure_url': metadata.get('structure_url', '') if metadata else '',
            'has_metadata': bool(metadata),
        })
        cards.append(stats)
    return cards


def build_feature_cards(
    feature_stats: dict[tuple[int, str], dict],
    db_feature_cards_by_key: dict[tuple[str, str], dict],
    display_names: dict[str, str],
    feature_lookup: dict[tuple[int, str], FeatureRecord],
    reference_name_by_ref_id: dict[int, str] | None = None,
) -> list[dict]:
    """Merge per-feature stats with optional DB metadata into card payloads."""
    ref_by_id = reference_name_by_ref_id or {}
    fallback_feature_by_name = {name: feat for (_rid, name), feat in feature_lookup.items()}
    fallback_card_by_name = {name: card for (_ref_name, name), card in db_feature_cards_by_key.items()}
    cards: list[dict] = []
    for ref_id, feature_name in sorted(
        feature_stats,
        key=lambda item: (str(display_names.get(item[1], item[1])).lower(), ref_by_id.get(item[0], '').lower()),
    ):
        stats = dict(feature_stats[(ref_id, feature_name)])
        feature = feature_lookup.get((ref_id, feature_name)) or fallback_feature_by_name.get(feature_name)
        ref_name = ref_by_id.get(ref_id, '') or (ref_by_id.get(feature.reference_id, '') if feature is not None else '')
        metadata = db_feature_cards_by_key.get((ref_name, feature_name)) or fallback_card_by_name.get(feature_name)
        stats['name'] = feature.display_name if feature is not None else display_names.get(feature_name, feature_name)
        nt_seq = feature.nt_sequence or '' if feature is not None else ''
        aa_seq = feature.aa_sequence or '' if feature is not None else ''
        stats['reference_name'] = ref_name

        if metadata:
            stats.update({
                'protein': metadata.get('protein', ''),
                'protein_id': metadata.get('protein_id', ''),
                'ncbi_protein_url': metadata.get('ncbi_protein_url', ''),
                'locus_tag': metadata.get('locus_tag', ''),
                'note': metadata.get('note', ''),
                'nt_sequence': nt_seq or metadata.get('nt_sequence', ''),
                'aa_sequence': aa_seq or metadata.get('aa_sequence', ''),
                'has_metadata': True,
            })
        else:
            stats.update({
                'protein': '',
                'protein_id': '',
                'ncbi_protein_url': '',
                'locus_tag': '',
                'note': '',
                'nt_sequence': nt_seq,
                'aa_sequence': aa_seq,
                'has_metadata': False,
            })
        stats['has_sequence'] = bool(stats['nt_sequence'] or stats['aa_sequence'])
        cards.append(stats)
    return cards
