"""
Multi-sample comparison service.

Assembles a mutation-vs-sample matrix from multiple result JSON files
for the comparison heatmap endpoint.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from web.backend.jobs import _load_run_payload
from web.backend.models import (
    CompareCell,
    CompareMutationKey,
    CompareResponse,
)

_WEB_TIMESTAMP_TOKEN = re.compile(
    r'\.(\d{20})(?=\.(?:report\.html|report\.pdf|results\.json)$)'
)


def _derive_sample_name(path: Path) -> str:
    """
    Derive a sample name from a results JSON path.

    Strips the web timestamp token and the .results.json suffix,
    e.g. 'mysample.20260601.results.json' -> 'mysample'.
    """
    name = path.name
    name = _WEB_TIMESTAMP_TOKEN.sub('', name)
    if name.endswith('.results.json'):
        name = name[:-13]
    return name or path.stem


def build_comparison_matrix(
    result_json_paths: list[Path],
    results_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
    non_synonymous_only: bool = False,
    db_hits_only: bool = False,
) -> CompareResponse:
    """
    Build the comparison heatmap matrix from multiple result JSON files.

    :param result_json_paths: resolved paths to .results.json files
    :param results_dir: allowed root directory for result artifacts
    :param is_path_within_allowed_roots: path confinement check
    :param is_allowed_artifact_path: artifact type check
    :param non_synonymous_only: if True, exclude synonymous mutations from the matrix
    :param db_hits_only: if True, exclude mutations that have no database hit across any sample
    :return: CompareResponse with samples, mutations, and allele-frequency matrix
    :raises ValueError: on validation failures (path, file, same-db checks)
    """
    _validate_paths(result_json_paths, results_dir, is_path_within_allowed_roots, is_allowed_artifact_path)

    sample_data = _load_all_samples(result_json_paths)
    _validate_same_database(sample_data)
    _validate_same_reference(sample_data)

    all_mutations = _collect_sorted_mutations(sample_data)
    features, feature_map = _build_feature_annotation(all_mutations)

    # Resolve feature display names from the project database
    first_run_payload = sample_data[0][2] if sample_data else {}
    project_db_path = first_run_payload.get('project_db_path', '')
    reference_name = first_run_payload.get('reference_name', '')
    feature_display_names = _build_feature_display_names(project_db_path, reference_name)

    samples: list[str] = []
    references: list[str] = []
    matrix: list[list[CompareCell]] = []
    for path, payload, run_payload in sample_data:
        sample_name = run_payload.get('sample_name') or _derive_sample_name(path)
        ref_name = run_payload.get('reference_name', '')
        variant_lookup = _build_variant_lookup(payload)
        db_hit_keys = _collect_db_hit_keys(payload)
        coverage_gaps = _parse_coverage_gaps(payload)

        samples.append(sample_name)
        references.append(ref_name)
        row = _build_matrix_row(all_mutations, variant_lookup, db_hit_keys, coverage_gaps)
        matrix.append(row)

    mutation_labels = [m.label for m in all_mutations]
    mutation_tick_labels = [
        f'{m.ref_aa}{m.position}{m.alt_aa}' for m in all_mutations
    ]
    consequences = _collect_consequences(all_mutations, sample_data)

    # Per-mutation db_hit: True if any sample has a db_hit for that column
    db_hit_map = [
        any(matrix[si][mi].db_hit for si in range(len(samples)))
        for mi in range(len(all_mutations))
    ]

    if non_synonymous_only:
        keep_indices = [
            i for i, c in enumerate(consequences)
            if c.lower() != 'synonymous'
        ]
        all_mutations = [all_mutations[i] for i in keep_indices]
        consequences = [consequences[i] for i in keep_indices]
        features, feature_map = _build_feature_annotation(all_mutations)
        matrix = [[row[i] for i in keep_indices] for row in matrix]
        mutation_labels = [m.label for m in all_mutations]
        mutation_tick_labels = [
            f'{m.ref_aa}{m.position}{m.alt_aa}' for m in all_mutations
        ]
        db_hit_map = [db_hit_map[i] for i in keep_indices]

    if db_hits_only:
        keep_indices = [i for i, hit in enumerate(db_hit_map) if hit]
        all_mutations = [all_mutations[i] for i in keep_indices]
        consequences = [consequences[i] for i in keep_indices]
        features, feature_map = _build_feature_annotation(all_mutations)
        matrix = [[row[i] for i in keep_indices] for row in matrix]
        mutation_labels = [m.label for m in all_mutations]
        mutation_tick_labels = [
            f'{m.ref_aa}{m.position}{m.alt_aa}' for m in all_mutations
        ]
        db_hit_map = [db_hit_map[i] for i in keep_indices]

    return CompareResponse(
        samples=samples,
        references=references,
        mutations=all_mutations,
        mutation_labels=mutation_labels,
        mutation_tick_labels=mutation_tick_labels,
        features=features,
        feature_map=feature_map,
        feature_display_names=feature_display_names,
        consequences=consequences,
        db_hit_map=db_hit_map,
        matrix=matrix,
    )


def _build_feature_display_names(
    project_db_path: str, reference_name: str,
) -> dict[str, str]:
    """
    Build feature display-name mapping from the project database.

    For mat_peptide features with a protein name, the display name is the
    protein; otherwise it falls back to the internal feature name.
    """
    db_path = Path(project_db_path)
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT feature.name, feature.protein, feature.feature_type FROM feature '
            'JOIN reference r ON r.id = feature.reference_id '
            'WHERE r.name = ?',
            (reference_name,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}

    names: dict[str, str] = {}
    for row in rows:
        name = row['name']
        protein = row['protein'] or ''
        feature_type = row['feature_type'] or 'CDS'
        if feature_type == 'mat_peptide' and protein:
            names[name] = protein
        else:
            names[name] = name
    return names


def _collect_consequences(
    all_mutations: list[CompareMutationKey],
    sample_data: list[tuple[Path, dict, dict]],
) -> list[str]:
    """
    Collect consequence string for each mutation from the first sample that has it.

    Mutations found only in formula hits (not in variant_result) default to
    ``'unknown'``.
    """
    consequence_lookup: dict[tuple[str, int, str, str], str] = {}
    for _path, payload, _run in sample_data:
        for variant in payload.get('variant_result', []):
            key = _variant_key(variant)
            if key is not None and key not in consequence_lookup:
                consequence = variant.get('consequence', '') or 'unknown'
                consequence_lookup[key] = consequence

    results: list[str] = []
    for mutation in all_mutations:
        key = (mutation.feature, mutation.position, mutation.ref_aa, mutation.alt_aa)
        results.append(consequence_lookup.get(key, 'unknown'))
    return results


def _validate_paths(
    result_json_paths: list[Path],
    results_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
) -> None:
    """Validate that all paths are allowed and exist."""
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        if not str(resolved).endswith('.results.json'):
            raise ValueError(f'Path must end with .results.json: {resolved}')
        if not is_path_within_allowed_roots(resolved, (results_dir,)):
            raise ValueError(f'Path is outside allowed results directory: {resolved}')
        if not is_allowed_artifact_path(resolved):
            raise ValueError(f'Unsupported artifact type: {resolved}')
        if not resolved.is_file():
            raise ValueError(f'Result file not found: {resolved}')


def _load_all_samples(result_json_paths: list[Path]) -> list[tuple[Path, dict, dict]]:
    """
    Load all result JSON payloads.

    :return: list of (path, full_payload, run_payload) tuples
    """
    samples: list[tuple[Path, dict, dict]] = []
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        full_payload = _load_full_payload(resolved)
        run_payload = _load_run_payload(resolved)
        samples.append((resolved, full_payload, run_payload))
    return samples


def _load_full_payload(results_json_path: Path) -> dict:
    """Load the full result JSON object from disk."""
    path = Path(results_json_path)
    if not path.is_file():
        raise ValueError(f'Expected report artifact not found: {path}')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid report JSON artifact: {exc.msg}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Invalid report JSON artifact: payload must be an object')
    return payload


def _validate_same_database(sample_data: list[tuple[Path, dict, dict]]) -> None:
    """Ensure all samples come from the same project database."""
    project_names: list[str] = []
    for _path, _payload, run_payload in sample_data:
        project_name = run_payload.get('project_name', '')
        if project_name:
            project_names.append(project_name)
    if len(set(project_names)) > 1:
        raise ValueError('Cannot compare results from different databases.')


def _validate_same_reference(sample_data: list[tuple[Path, dict, dict]]) -> None:
    """Ensure all samples use the same reference sequence."""
    reference_names: list[str] = []
    for _path, _payload, run_payload in sample_data:
        reference_name = run_payload.get('reference_name', '')
        if reference_name:
            reference_names.append(reference_name)
    if len(set(reference_names)) > 1:
        raise ValueError('Cannot compare results from different references.')


def _collect_sorted_mutations(sample_data: list[tuple[Path, dict, dict]]) -> list[CompareMutationKey]:
    """
    Collect all unique mutation keys across all samples, sorted by
    (feature, position, ref_aa, alt_aa).

    Only variant_result entries are used to build mutation columns.
    Formula hit variants are excluded from column creation (they may
    create all-zero columns with unknown consequence), but they still
    contribute to the db_hit flag via ``_collect_db_hit_keys``.

    When two mutations from different features produce the same
    ``{ref_aa}{pos}{alt_aa}`` label, the label is prefixed with the
    feature name to disambiguate.
    """
    seen: set[tuple[str, int, str, str]] = set()
    for _path, payload, _run in sample_data:
        for variant in payload.get('variant_result', []):
            key = _variant_key(variant)
            if key is not None:
                seen.add(key)

    sorted_keys = sorted(seen, key=lambda k: (k[0], k[1], k[2], k[3]))

    # Detect label collisions: same label from different features
    label_counts: dict[str, int] = {}
    for feature, pos, ref_aa, alt_aa in sorted_keys:
        base_label = f'{ref_aa}{pos}{alt_aa}'
        label_counts[base_label] = label_counts.get(base_label, 0) + 1

    results: list[CompareMutationKey] = []
    for feature, pos, ref_aa, alt_aa in sorted_keys:
        base_label = f'{ref_aa}{pos}{alt_aa}'
        label = f'{feature}:{base_label}' if label_counts[base_label] > 1 else base_label
        results.append(CompareMutationKey(
            feature=feature,
            position=pos,
            ref_aa=ref_aa,
            alt_aa=alt_aa,
            label=label,
        ))
    return results


def _build_feature_annotation(mutations: list[CompareMutationKey]) -> tuple[list[str], list[int]]:
    """Build ordered features list and feature_map index for each mutation."""
    features: list[str] = []
    feature_index: dict[str, int] = {}
    feature_map: list[int] = []

    for mutation in mutations:
        if mutation.feature not in feature_index:
            feature_index[mutation.feature] = len(features)
            features.append(mutation.feature)
        feature_map.append(feature_index[mutation.feature])

    return features, feature_map


def _variant_key(variant: dict) -> tuple[str, int, str, str] | None:
    """
    Build a unique mutation key tuple from a variant_result entry.

    Converts 0-based codon_pos to 1-based display position.
    """
    feature = variant.get('feature_name') or variant.get('chrom', '')
    codon_pos = variant.get('codon_pos')
    ref_aa = variant.get('ref_aa', '')
    alt_aa = variant.get('alt_aa', '')
    if codon_pos is None or not feature:
        return None
    return (feature, codon_pos + 1, ref_aa, alt_aa)


def _formula_variant_key(mv: dict) -> tuple[str, int, str, str] | None:
    """
    Build a unique mutation key tuple from a formula_rule_hit matched_variant.

    Converts 0-based codon_pos to 1-based display position.
    """
    feature = mv.get('feature', '')
    codon_pos = mv.get('codon_pos')
    ref_aa = mv.get('ref_aa', '')
    alt_aa = mv.get('alt_aa', '')
    if codon_pos is None or not feature:
        return None
    return (feature, codon_pos + 1, ref_aa, alt_aa)


def _build_variant_lookup(payload: dict) -> dict[tuple[str, int, str, str], dict]:
    """
    Index variant_result entries by their mutation key for fast lookup.

    :return: mapping from (feature, 1-based-position, ref_aa, alt_aa) -> variant dict
    """
    lookup: dict[tuple[str, int, str, str], dict] = {}
    for variant in payload.get('variant_result', []):
        key = _variant_key(variant)
        if key is not None:
            lookup[key] = variant
    return lookup


def _collect_db_hit_keys(payload: dict) -> set[tuple[str, int, str, str]]:
    """
    Collect mutation keys that are database hits.

    A mutation is a db_hit if:
    - rule_match == 1 in variant_result, OR
    - it appears in any formula_rule_hit's matched_variants
    """
    hits: set[tuple[str, int, str, str]] = set()

    for variant in payload.get('variant_result', []):
        if variant.get('rule_match') == 1:
            key = _variant_key(variant)
            if key is not None:
                hits.add(key)

    for hit in payload.get('formula_rule_hit', []):
        for mv in _parse_formula_hit_variants(hit):
            key = _formula_variant_key(mv)
            if key is not None:
                hits.add(key)

    return hits


def _parse_formula_hit_variants(hit: dict) -> list[dict]:
    """
    Parse matched_variants from a formula_rule_hit entry.

    hit_json is a JSON-encoded string containing matched_variants.
    """
    raw = hit.get('hit_json', '')
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    return parsed.get('matched_variants', [])


def _parse_coverage_gaps(payload: dict) -> dict[str, set[int]]:
    """
    Parse coverage gaps into a feature -> set of 1-based codon positions mapping.

    The JSON stores codon_start and codon_end as 0-based inclusive indices.
    We convert to 1-based inclusive for comparison with display positions.
    """
    gaps: dict[str, set[int]] = {}
    for gap in payload.get('coverage_gap', []):
        feature = gap.get('feature_name', '')
        codon_start = gap.get('codon_start')
        codon_end = gap.get('codon_end')
        if not feature or codon_start is None or codon_end is None:
            continue
        positions: set[int] = set()
        for p in range(codon_start, codon_end + 1):
            positions.add(p + 1)
        if feature not in gaps:
            gaps[feature] = set()
        gaps[feature].update(positions)
    return gaps


def _build_matrix_row(
    all_mutations: list[CompareMutationKey],
    variant_lookup: dict[tuple[str, int, str, str], dict],
    db_hit_keys: set[tuple[str, int, str, str]],
    coverage_gaps: dict[str, set[int]],
) -> list[CompareCell]:
    """
    Build one sample's row of the comparison matrix.

    - Detected variant: allele_freq from entry, db_hit from flags
    - Not detected, no coverage gap: allele_freq=0.0, db_hit=False
    - Not detected, in coverage gap: allele_freq=None, db_hit=False
    """
    row: list[CompareCell] = []
    for mutation in all_mutations:
        key = (mutation.feature, mutation.position, mutation.ref_aa, mutation.alt_aa)
        if key in variant_lookup:
            variant = variant_lookup[key]
            allele_freq = variant.get('allele_freq', 0.0)
            db_hit = key in db_hit_keys
            row.append(CompareCell(allele_freq=allele_freq, db_hit=db_hit))
        elif _position_in_coverage_gap(mutation.feature, mutation.position, coverage_gaps):
            row.append(CompareCell(allele_freq=None, db_hit=False))
        else:
            row.append(CompareCell(allele_freq=0.0, db_hit=False))
    return row


def _position_in_coverage_gap(
    feature: str,
    position: int,
    coverage_gaps: dict[str, set[int]],
) -> bool:
    """Check whether a 1-based codon position falls within a coverage gap for the feature."""
    gap_positions = coverage_gaps.get(feature)
    return gap_positions is not None and position in gap_positions
