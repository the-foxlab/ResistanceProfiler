"""Multi-sample comparison service assembling the mutation-vs-sample heatmap matrix."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from web.backend.artifact_kinds import strip_artifact_timestamp
from web.backend.models import CompareCell, CompareMutationKey, CompareResponse

_MutationTuple = tuple[str, int, str, str]
_SampleEntry = tuple[Path, dict, dict]


def _derive_sample_name(path: Path) -> str:
    """Strip the timestamp token and ``.results.json`` suffix from a results artifact path."""
    name = strip_artifact_timestamp(path.name)
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
    """Build the comparison heatmap matrix from validated ``.results.json`` files."""
    _validate_paths(result_json_paths, results_dir, is_path_within_allowed_roots, is_allowed_artifact_path)
    deduped_paths, deduplicated = _deduplicate_paths(result_json_paths)

    sample_data = _load_all_samples(deduped_paths)
    _validate_uniform_run_field(sample_data, 'project_name', 'Cannot compare results from different databases.')
    _validate_uniform_run_field(sample_data, 'reference_name', 'Cannot compare results from different references.')

    all_mutations = _collect_sorted_mutations(sample_data)
    first_run = sample_data[0][2] if sample_data else {}
    feature_display_names = _build_feature_display_names(
        first_run.get('project_db_path', ''),
        first_run.get('reference_name', ''),
    )
    samples, disambiguation_note = _disambiguate_sample_labels(sample_data, deduplicated)

    references: list[str] = []
    matrix: list[list[CompareCell]] = []
    for _path, payload, run_payload in sample_data:
        references.append(run_payload.get('reference_name', ''))
        variant_lookup, db_hit_keys = _index_sample_variants(payload)
        coverage_gaps = _parse_coverage_gaps(payload)
        matrix.append(_build_matrix_row(all_mutations, variant_lookup, db_hit_keys, coverage_gaps))

    consequences = _collect_consequences(all_mutations, sample_data)
    db_hit_map = [
        any(row[mi].db_hit for row in matrix)
        for mi in range(len(all_mutations))
    ]

    keep_indices = _compute_kept_column_indices(
        consequences, db_hit_map,
        non_synonymous_only=non_synonymous_only,
        db_hits_only=db_hits_only,
    )
    if keep_indices is not None:
        all_mutations = [all_mutations[i] for i in keep_indices]
        consequences = [consequences[i] for i in keep_indices]
        db_hit_map = [db_hit_map[i] for i in keep_indices]
        matrix = [[row[i] for i in keep_indices] for row in matrix]

    features, feature_map = _build_feature_annotation(all_mutations)
    return CompareResponse(
        samples=samples,
        references=references,
        mutations=all_mutations,
        mutation_labels=[m.label for m in all_mutations],
        mutation_tick_labels=[f'{m.ref_aa}{m.position}{m.alt_aa}' for m in all_mutations],
        features=features,
        feature_map=feature_map,
        feature_display_names=feature_display_names,
        consequences=consequences,
        db_hit_map=db_hit_map,
        sample_disambiguation_note=disambiguation_note,
        matrix=matrix,
    )


def _deduplicate_paths(result_json_paths: list[Path]) -> tuple[list[Path], bool]:
    """Keep the first occurrence of each resolved path; return ``(paths, had_duplicates)``."""
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(path)
    return deduped, len(deduped) < len(result_json_paths)


def _disambiguate_sample_labels(sample_data: list[_SampleEntry], deduplicated: bool) -> tuple[list[str], str]:
    """Append the derived filename in parentheses only for colliding sample names."""
    name_pairs = [
        (run_payload.get('sample_name') or _derive_sample_name(path), _derive_sample_name(path))
        for path, _payload, run_payload in sample_data
    ]
    counts = Counter(name for name, _ in name_pairs)
    disambiguated = any(count > 1 for count in counts.values())
    labels = [f'{name} ({derived})' if counts[name] > 1 else name for name, derived in name_pairs]

    notes: list[str] = []
    if deduplicated:
        notes.append('Duplicate result files were merged.')
    if disambiguated:
        notes.append('Sample names with collisions show the source filename in parentheses.')
    return labels, ' '.join(notes)


def _compute_kept_column_indices(
    consequences: list[str],
    db_hit_map: list[bool],
    *,
    non_synonymous_only: bool,
    db_hits_only: bool,
) -> list[int] | None:
    """Return filtered column indices when any filter flag is enabled, else ``None``."""
    if not non_synonymous_only and not db_hits_only:
        return None
    return [
        i for i in range(len(consequences))
        if (not non_synonymous_only or consequences[i].lower() != 'synonymous')
        and (not db_hits_only or db_hit_map[i])
    ]


def _build_feature_display_names(project_db_path: str, reference_name: str) -> dict[str, str]:
    """Map internal feature names to display names (protein name for ``mat_peptide``, else name)."""
    db_path = Path(project_db_path)
    if not db_path.is_file():
        return {}
    try:
        with contextlib.closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT feature.name, feature.protein, feature.feature_type FROM feature '
                'JOIN reference r ON r.id = feature.reference_id WHERE r.name = ?',
                (reference_name,),
            ).fetchall()
    except sqlite3.Error:
        return {}

    return {
        row['name']: (row['protein'] if (row['feature_type'] or 'CDS') == 'mat_peptide' and row['protein'] else row['name'])
        for row in rows
    }


def _collect_consequences(
    all_mutations: list[CompareMutationKey],
    sample_data: list[_SampleEntry],
) -> list[str]:
    """Resolve each mutation's consequence from the first sample carrying it (default ``'unknown'``)."""
    lookup: dict[_MutationTuple, str] = {}
    for _path, payload, _run in sample_data:
        for variant in payload.get('variant_result', []):
            key = _variant_key(variant)
            if key is not None and key not in lookup:
                lookup[key] = variant.get('consequence', '') or 'unknown'
    return [lookup.get((m.feature, m.position, m.ref_aa, m.alt_aa), 'unknown') for m in all_mutations]


def _validate_paths(
    result_json_paths: list[Path],
    results_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
) -> None:
    """Validate suffix, path confinement, artifact allowlist, and file existence."""
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        if not str(resolved).endswith('.results.json'):
            raise ValueError('Path must end with .results.json.')
        if not is_path_within_allowed_roots(resolved, (results_dir,)):
            raise ValueError('Path is outside allowed results directory.')
        if not is_allowed_artifact_path(resolved):
            raise ValueError('Unsupported artifact type. Allowed: .results.json.')
        if not resolved.is_file():
            raise ValueError('Result file not found.')


def _load_all_samples(result_json_paths: list[Path]) -> list[_SampleEntry]:
    """Parse each ``.results.json`` file once and extract its validated ``run`` object."""
    samples: list[_SampleEntry] = []
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError('Expected report artifact not found.')
        try:
            payload = json.loads(resolved.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid report JSON artifact: {exc.msg}') from exc
        if not isinstance(payload, dict):
            raise ValueError('Invalid report JSON artifact: payload must be an object')
        run_payload = payload.get('run')
        if not isinstance(run_payload, dict):
            raise ValueError("Invalid report JSON artifact generated by respro: missing object key 'run'")
        samples.append((resolved, payload, run_payload))
    return samples


def _validate_uniform_run_field(sample_data: list[_SampleEntry], field: str, error_message: str) -> None:
    """Reject comparison when non-empty values of *field* disagree across samples."""
    values = {val for _, _, run in sample_data if (val := run.get(field, ''))}
    if len(values) > 1:
        raise ValueError(error_message)


def _collect_sorted_mutations(sample_data: list[_SampleEntry]) -> list[CompareMutationKey]:
    """Collect unique ``variant_result`` mutations sorted by ``(feature, position, ref_aa, alt_aa)``.

    Formula-only hits are intentionally omitted from column creation so they do not
    produce all-zero columns with unknown consequence; they still mark ``db_hit``.
    """
    seen = {
        key
        for _path, payload, _run in sample_data
        for variant in payload.get('variant_result', [])
        if (key := _variant_key(variant)) is not None
    }
    sorted_keys = sorted(seen)
    label_counts = Counter(f'{ref_aa}{pos}{alt_aa}' for _, pos, ref_aa, alt_aa in sorted_keys)

    return [
        CompareMutationKey(
            feature=feature,
            position=pos,
            ref_aa=ref_aa,
            alt_aa=alt_aa,
            label=f'{feature}:{base}' if label_counts[base] > 1 else base,
        )
        for feature, pos, ref_aa, alt_aa in sorted_keys
        for base in (f'{ref_aa}{pos}{alt_aa}',)
    ]


def _build_feature_annotation(mutations: list[CompareMutationKey]) -> tuple[list[str], list[int]]:
    """Return ordered unique features and per-mutation feature indices."""
    features: list[str] = []
    feature_index: dict[str, int] = {}
    feature_map: list[int] = []
    for mutation in mutations:
        idx = feature_index.get(mutation.feature)
        if idx is None:
            idx = len(features)
            feature_index[mutation.feature] = idx
            features.append(mutation.feature)
        feature_map.append(idx)
    return features, feature_map


def _variant_key(variant: dict) -> _MutationTuple | None:
    """Build ``(feature, 1-based codon_pos, ref_aa, alt_aa)`` from a ``variant_result`` row."""
    feature = variant.get('feature_name') or variant.get('chrom', '')
    codon_pos = variant.get('codon_pos')
    if codon_pos is None or not feature:
        return None
    return (feature, codon_pos + 1, variant.get('ref_aa', ''), variant.get('alt_aa', ''))


def _formula_variant_key(mv: dict) -> _MutationTuple | None:
    """Build ``(feature, 1-based codon_pos, ref_aa, alt_aa)`` from a formula hit entry (already 1-based)."""
    feature = mv.get('feature', '')
    codon_pos = mv.get('codon_pos')
    if codon_pos is None or not feature:
        return None
    return (feature, codon_pos, mv.get('ref_aa', ''), mv.get('alt_aa', ''))


def _index_sample_variants(payload: dict) -> tuple[dict[_MutationTuple, dict], set[_MutationTuple]]:
    """Index one sample's variants and collect its atomic + formula database-hit keys in one pass."""
    lookup: dict[_MutationTuple, dict] = {}
    hits: set[_MutationTuple] = set()
    for variant in payload.get('variant_result', []):
        key = _variant_key(variant)
        if key is None:
            continue
        lookup[key] = variant
        if variant.get('rule_match') == 1:
            hits.add(key)

    for hit in payload.get('formula_rule_hit', []):
        for mv in _parse_formula_hit_variants(hit):
            key = _formula_variant_key(mv)
            if key is not None:
                hits.add(key)

    return lookup, hits


def _parse_formula_hit_variants(hit: dict) -> list[dict]:
    """Return ``matched_variants`` parsed from a ``formula_rule_hit.hit_json`` payload."""
    raw = hit.get('hit_json', '')
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed.get('matched_variants', []) if isinstance(parsed, dict) else []


def _parse_coverage_gaps(payload: dict) -> dict[str, set[int]]:
    """Map each feature to its 1-based inclusive uncovered codon positions (JSON stores 0-based inclusive)."""
    gaps: dict[str, set[int]] = {}
    for gap in payload.get('coverage_gap', []):
        feature = gap.get('feature_name', '')
        codon_start = gap.get('codon_start')
        codon_end = gap.get('codon_end')
        if not feature or codon_start is None or codon_end is None:
            continue
        gaps.setdefault(feature, set()).update(range(codon_start + 1, codon_end + 2))
    return gaps


def _build_matrix_row(
    all_mutations: list[CompareMutationKey],
    variant_lookup: dict[_MutationTuple, dict],
    db_hit_keys: set[_MutationTuple],
    coverage_gaps: dict[str, set[int]],
) -> list[CompareCell]:
    """Build one sample's matrix row: detected AF, ``None`` in a coverage gap, or ``0.0``."""
    row: list[CompareCell] = []
    for mutation in all_mutations:
        key = (mutation.feature, mutation.position, mutation.ref_aa, mutation.alt_aa)
        variant = variant_lookup.get(key)
        if variant is not None:
            row.append(CompareCell(allele_freq=variant.get('allele_freq', 0.0), db_hit=key in db_hit_keys))
        elif mutation.position in coverage_gaps.get(mutation.feature, ()):
            row.append(CompareCell(allele_freq=None, db_hit=False))
        else:
            row.append(CompareCell(allele_freq=0.0, db_hit=False))
    return row
