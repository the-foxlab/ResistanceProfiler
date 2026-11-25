"""Multi-sample comparison service assembling the mutation-vs-sample heatmap matrix."""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import sqlite3
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeGuard

from web.backend.artifact_kinds import strip_artifact_timestamp
from web.backend.config import WEB_ENV
from web.backend.models import CompareCell, CompareMutationKey, CompareResponse
from web.backend.project_dbs import open_project_db_readonly

logger = logging.getLogger(__name__)

MAX_AGGREGATE_JSON_BYTES = 32 * 1024 * 1024
MAX_COVERAGE_GAP_END = 1_000_000
MAX_COVERAGE_GAP_SPAN_PER_FEATURE = 1_000_001
MAX_COVERAGE_GAP_INTERVALS_PER_SAMPLE = 50_000
MAX_UNIQUE_MUTATIONS = 50_000
MAX_MATRIX_CELLS = 500_000

_MutationTuple = tuple[str, int, str, str]
_SampleEntry = tuple[Path, dict, dict]
_CoverageIntervals = dict[str, tuple[list[int], list[int]]]


def _is_strict_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_dict_list(payload: dict, key: str) -> list[dict]:
    """Return a list of dicts for *key*, treating ``None``/missing as ``[]`` and rejecting invalid types."""
    raw = payload.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError(f'Invalid report JSON artifact: {key} must be an array of objects.')
    return raw


def _derive_sample_name(path: Path) -> str:
    """Strip the timestamp token and ``.results.json`` suffix from a results artifact path."""
    name = strip_artifact_timestamp(path.name)
    if name.endswith('.results.json'):
        name = name[:-13]
    return name or path.stem


def _resolve_project_databases_dir(results_dir: Path, project_databases_dir: Path | None) -> Path:
    """Resolve the confined project databases directory with sibling and env fallbacks."""
    if project_databases_dir is not None:
        return project_databases_dir.expanduser().resolve()
    sibling = results_dir.expanduser().resolve().parent / 'project_databases'
    if sibling.is_dir():
        return sibling.resolve()
    env_data_dir = os.getenv(WEB_ENV.data_dir, '').strip()
    if env_data_dir:
        return (Path(env_data_dir).expanduser().resolve() / 'project_databases').resolve()
    repo_data = Path('/data') if Path('/data').is_dir() else Path(__file__).resolve().parents[3] / 'data'
    return (repo_data / 'project_databases').resolve()


def build_comparison_matrix(
    result_json_paths: list[Path],
    results_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
    non_synonymous_only: bool = False,
    db_hits_only: bool = False,
    project_databases_dir: Path | None = None,
) -> CompareResponse:
    """Build the comparison heatmap matrix from validated ``.results.json`` files."""
    if not result_json_paths:
        raise ValueError('At least one result file is required.')
    _validate_paths(result_json_paths, results_dir, is_path_within_allowed_roots, is_allowed_artifact_path)
    deduped_paths, deduplicated = _deduplicate_paths(result_json_paths)

    sample_data = _load_all_samples(deduped_paths)
    _validate_uniform_run_field(
        sample_data,
        'project_name',
        'Result JSON is missing required project_name.',
        'Cannot compare results from different databases.',
    )
    _validate_uniform_run_field(
        sample_data,
        'reference_name',
        'Result JSON is missing required reference_name.',
        'Cannot compare results from different references.',
    )
    matched_references = _validate_uniform_references(sample_data)

    all_mutations = _collect_sorted_mutations(sample_data)
    if len(sample_data) * len(all_mutations) > MAX_MATRIX_CELLS:
        raise ValueError('Comparison matrix size exceeds the allowed limit.')

    resolved_db_dir = _resolve_project_databases_dir(results_dir, project_databases_dir)
    first_run = sample_data[0][2]
    feature_display_names = _build_feature_display_names(
        first_run.get('project_db_path'),
        matched_references,
        resolved_db_dir,
        is_path_within_allowed_roots,
    )
    samples, disambiguation_note = _disambiguate_sample_labels(sample_data, deduplicated)

    references: list[str] = []
    matrix: list[list[CompareCell]] = []
    for _path, payload, run_payload in sample_data:
        references.append(run_payload['reference_name'].strip())
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
    """Ensure every sample label is uniquely disambiguated via source stem and ordinal suffix."""
    name_pairs: list[tuple[str, str]] = []
    for path, _payload, run_payload in sample_data:
        derived = _derive_sample_name(path)
        raw_name = run_payload.get('sample_name')
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else derived
        name_pairs.append((name, derived))

    name_counts = Counter(name for name, _ in name_pairs)
    candidates = [f'{name} ({derived})' if name_counts[name] > 1 else name for name, derived in name_pairs]

    label_counts = Counter(candidates)
    if any(count > 1 for count in label_counts.values()):
        used = {label for label, count in label_counts.items() if count == 1}
        occurrences: Counter[str] = Counter()
        resolved: list[str] = []
        for label in candidates:
            if label_counts[label] == 1:
                resolved.append(label)
                continue
            occurrences[label] += 1
            suffix_num = occurrences[label]
            while (candidate := f'{label} #{suffix_num}') in used:
                suffix_num += 1
            used.add(candidate)
            resolved.append(candidate)
        labels = resolved
    else:
        labels = candidates

    disambiguated = any(count > 1 for count in name_counts.values()) or any(
        count > 1 for count in label_counts.values()
    )
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


def _build_feature_display_names(
    project_db_path: object,
    reference_names: Sequence[str],
    project_databases_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
) -> dict[str, str]:
    """Map internal feature names to display names (protein name for ``mat_peptide``, else name)."""
    if not isinstance(project_db_path, str) or not project_db_path.strip() or '\x00' in project_db_path:
        return {}
    if not reference_names:
        return {}
    db_path = Path(project_db_path).expanduser().resolve()
    if db_path == project_databases_dir or not is_path_within_allowed_roots(db_path, (project_databases_dir,)):
        return {}
    if db_path.suffix != '.db' or not db_path.is_file():
        return {}
    try:
        with contextlib.closing(open_project_db_readonly(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ', '.join('?' for _ in reference_names)
            rows = conn.execute(
                'SELECT feature.name, feature.protein, feature.feature_type FROM feature '
                f'JOIN reference r ON r.id = feature.reference_id WHERE r.name IN ({placeholders}) '
                'ORDER BY r.id, feature.id',
                tuple(reference_names),
            ).fetchall()
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        logger.warning('Skipping project database display names for %s: %s', db_path.name, exc)
        return {}

    return {
        row['name']: (row['protein'] if (row['feature_type'] or 'CDS') == 'mat_peptide' and row['protein'] else row['name'])
        for row in rows
    }


def _collect_consequences(
    all_mutations: list[CompareMutationKey],
    sample_data: list[_SampleEntry],
) -> list[str]:
    """Resolve each mutation's consequence deterministically (default ``'unknown'``)."""
    lookup: dict[_MutationTuple, str] = {}
    for _path, payload, _run in sample_data:
        for variant in _optional_dict_list(payload, 'variant_result'):
            key = _variant_key(variant)
            if key is None:
                continue
            raw_conseq = variant.get('consequence')
            if raw_conseq is None:
                conseq = 'unknown'
            elif isinstance(raw_conseq, str):
                conseq = raw_conseq or 'unknown'
            else:
                raise ValueError('Invalid report JSON artifact: consequence must be a string.')
            if key not in lookup or (lookup[key] == 'unknown' and conseq != 'unknown'):
                lookup[key] = conseq
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
    """Parse each ``.results.json`` file once within aggregate byte caps and extract its ``run`` object."""
    samples: list[_SampleEntry] = []
    total_bytes = 0
    for path in result_json_paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError('Expected report artifact not found.')
        total_bytes += resolved.stat().st_size
        if total_bytes > MAX_AGGREGATE_JSON_BYTES:
            raise ValueError('Total comparison input size exceeds the allowed limit.')
        try:
            payload = json.loads(resolved.read_text(encoding='utf-8'))
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid report JSON artifact: file must be valid UTF-8.') from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid report JSON artifact: {exc.msg}') from exc
        if not isinstance(payload, dict):
            raise ValueError('Invalid report JSON artifact: payload must be an object')
        run_payload = payload.get('run')
        if not isinstance(run_payload, dict):
            raise ValueError("Invalid report JSON artifact generated by respro: missing object key 'run'")
        samples.append((resolved, payload, run_payload))
    return samples


def _validate_uniform_run_field(
    sample_data: list[_SampleEntry],
    field: str,
    missing_error_message: str,
    mismatch_error_message: str,
) -> None:
    """Require non-empty string values of *field* and reject disagreement across samples."""
    if not sample_data:
        raise ValueError('At least one result file is required.')
    values: set[str] = set()
    for _path, _payload, run in sample_data:
        raw = run.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(missing_error_message)
        values.add(raw.strip())
    if len(values) > 1:
        raise ValueError(mismatch_error_message)


def _extract_sample_references(payload: dict, run_payload: dict) -> tuple[str, ...]:
    """Extract ordered unique reference names from top-level or ``run`` ``references`` metadata."""
    raw_refs = payload.get('references')
    if raw_refs is None or raw_refs == []:
        raw_refs = run_payload.get('references')
    primary = run_payload['reference_name'].strip()
    if raw_refs is None or raw_refs == []:
        return (primary,)
    if not isinstance(raw_refs, list):
        raise ValueError('Invalid report JSON artifact: references must be a list.')

    ordered: list[str] = []
    seen: set[str] = set()
    for entry in raw_refs:
        if not isinstance(entry, dict):
            raise ValueError('Invalid report JSON artifact: each reference entry must be an object.')
        ref_name = entry.get('reference_name')
        if not isinstance(ref_name, str) or not ref_name.strip():
            raise ValueError('Invalid report JSON artifact: reference entry is missing reference_name.')
        normalized = ref_name.strip()
        if normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    if primary not in seen:
        ordered.insert(0, primary)
    return tuple(ordered)


def _validate_uniform_references(sample_data: list[_SampleEntry]) -> tuple[str, ...]:
    """Ensure all samples share the same full set of references and return the ordered reference names."""
    per_sample_refs = [_extract_sample_references(payload, run_payload) for _path, payload, run_payload in sample_data]
    if len({frozenset(refs) for refs in per_sample_refs}) > 1:
        raise ValueError('Cannot compare results from different references.')
    return per_sample_refs[0]


def _collect_sorted_mutations(sample_data: list[_SampleEntry]) -> list[CompareMutationKey]:
    """Collect unique ``variant_result`` mutations sorted by ``(feature, position, ref_aa, alt_aa)``.

    Formula-only hits are intentionally omitted from column creation so they do not
    produce all-zero columns with unknown consequence; they still mark ``db_hit``.
    """
    seen: set[_MutationTuple] = set()
    for _path, payload, _run in sample_data:
        for variant in _optional_dict_list(payload, 'variant_result'):
            key = _variant_key(variant)
            if key is None:
                continue
            if key not in seen:
                if len(seen) >= MAX_UNIQUE_MUTATIONS:
                    raise ValueError('Unique mutation count exceeds the allowed comparison limit.')
                seen.add(key)

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
    raw_feature = variant.get('feature_name')
    if raw_feature is None or raw_feature == '':
        raw_feature = variant.get('chrom')
    if raw_feature is None or raw_feature == '':
        return None
    if not isinstance(raw_feature, str):
        raise ValueError('Invalid report JSON artifact: variant feature_name must be a string.')
    codon_pos = variant.get('codon_pos')
    if codon_pos is None:
        return None
    if not _is_strict_int(codon_pos) or codon_pos < 0:
        raise ValueError('Invalid report JSON artifact: variant codon_pos must be a non-negative integer.')
    ref_aa = variant.get('ref_aa')
    alt_aa = variant.get('alt_aa')
    ref_aa_str = '' if ref_aa is None else ref_aa
    alt_aa_str = '' if alt_aa is None else alt_aa
    if not isinstance(ref_aa_str, str) or not isinstance(alt_aa_str, str):
        raise ValueError('Invalid report JSON artifact: variant amino acid fields must be strings.')
    return (raw_feature, codon_pos + 1, ref_aa_str, alt_aa_str)


def _formula_variant_key(mv: dict) -> _MutationTuple | None:
    """Build ``(feature, 1-based codon_pos, ref_aa, alt_aa)`` from a formula hit entry (already 1-based)."""
    feature = mv.get('feature')
    if feature is None or feature == '':
        return None
    if not isinstance(feature, str):
        raise ValueError('Invalid report JSON artifact: matched variant feature must be a string.')
    codon_pos = mv.get('codon_pos')
    if codon_pos is None:
        return None
    if not _is_strict_int(codon_pos) or codon_pos < 1:
        raise ValueError('Invalid report JSON artifact: matched variant codon_pos must be a positive integer.')
    ref_aa = mv.get('ref_aa')
    alt_aa = mv.get('alt_aa')
    ref_aa_str = '' if ref_aa is None else ref_aa
    alt_aa_str = '' if alt_aa is None else alt_aa
    if not isinstance(ref_aa_str, str) or not isinstance(alt_aa_str, str):
        raise ValueError('Invalid report JSON artifact: matched variant amino acid fields must be strings.')
    return (feature, codon_pos, ref_aa_str, alt_aa_str)


def _parse_allele_freq(variant: dict) -> float | None:
    """Parse ``allele_freq`` strictly as a finite float or ``None`` (defaulting missing key to ``0.0``)."""
    if 'allele_freq' not in variant:
        return 0.0
    raw_af = variant['allele_freq']
    if raw_af is None:
        return None
    if isinstance(raw_af, bool) or not isinstance(raw_af, (int, float)):
        raise ValueError('Invalid report JSON artifact: allele_freq must be numeric or null.')
    af = float(raw_af)
    if not math.isfinite(af):
        raise ValueError('Invalid report JSON artifact: allele_freq must be finite.')
    return af


def _index_sample_variants(payload: dict) -> tuple[dict[_MutationTuple, float | None], set[_MutationTuple]]:
    """Index one sample's variants with deterministic duplicate-AA aggregation and collect db-hit keys."""
    lookup: dict[_MutationTuple, float | None] = {}
    hits: set[_MutationTuple] = set()
    for variant in _optional_dict_list(payload, 'variant_result'):
        key = _variant_key(variant)
        if key is None:
            continue
        af_val = _parse_allele_freq(variant)
        if key not in lookup:
            lookup[key] = af_val
        else:
            prev = lookup[key]
            if prev is None:
                lookup[key] = af_val
            elif af_val is not None:
                lookup[key] = min(1.0, prev + af_val)
        if variant.get('rule_match') == 1:
            hits.add(key)

    for hit in _optional_dict_list(payload, 'formula_rule_hit'):
        for mv in _parse_formula_hit_variants(hit):
            key = _formula_variant_key(mv)
            if key is not None:
                hits.add(key)

    return lookup, hits


def _parse_formula_hit_variants(hit: dict) -> list[dict]:
    """Return ``matched_variants`` parsed from a ``formula_rule_hit.hit_json`` payload."""
    raw = hit.get('hit_json')
    if raw is None or raw == '':
        return []
    if not isinstance(raw, str):
        raise ValueError('Invalid report JSON artifact: hit_json must be a string.')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    matched = parsed.get('matched_variants')
    if matched is None:
        return []
    if not isinstance(matched, list) or any(not isinstance(item, dict) for item in matched):
        raise ValueError('Invalid report JSON artifact: matched_variants must be an array of objects.')
    return matched


def _parse_coverage_gaps(payload: dict) -> _CoverageIntervals:
    """Map each feature to sorted merged 1-based inclusive ``(starts, ends)`` interval lists."""
    raw_gaps = _optional_dict_list(payload, 'coverage_gap')
    if len(raw_gaps) > MAX_COVERAGE_GAP_INTERVALS_PER_SAMPLE:
        raise ValueError('Coverage gap interval count exceeds the allowed limit.')

    per_feature: dict[str, list[tuple[int, int]]] = {}
    for gap in raw_gaps:
        feature = gap.get('feature_name')
        if feature is None or feature == '':
            continue
        if not isinstance(feature, str):
            raise ValueError('Invalid coverage gap: feature_name must be a string.')
        codon_start = gap.get('codon_start')
        codon_end = gap.get('codon_end')
        if codon_start is None or codon_end is None:
            continue
        if (
            not _is_strict_int(codon_start)
            or not _is_strict_int(codon_end)
            or codon_start < 0
            or codon_start > codon_end
            or codon_end > MAX_COVERAGE_GAP_END
        ):
            raise ValueError('Invalid coverage gap coordinates.')
        per_feature.setdefault(feature, []).append((codon_start + 1, codon_end + 1))

    merged_gaps: _CoverageIntervals = {}
    for feature, intervals in per_feature.items():
        intervals.sort()
        merged: list[list[int]] = []
        for start, end in intervals:
            if not merged or start > merged[-1][1] + 1:
                merged.append([start, end])
            elif end > merged[-1][1]:
                merged[-1][1] = end
        total_span = sum(end - start + 1 for start, end in merged)
        if total_span > MAX_COVERAGE_GAP_SPAN_PER_FEATURE:
            raise ValueError('Coverage gap span exceeds the allowed limit.')
        merged_gaps[feature] = ([s for s, _ in merged], [e for _, e in merged])
    return merged_gaps


def _is_in_coverage_gap(coverage_gaps: _CoverageIntervals, feature: str, position: int) -> bool:
    """Return whether 1-based *position* falls within any merged coverage-gap interval for *feature*."""
    entry = coverage_gaps.get(feature)
    if entry is None:
        return False
    starts, ends = entry
    idx = bisect_right(starts, position) - 1
    return idx >= 0 and position <= ends[idx]


def _build_matrix_row(
    all_mutations: list[CompareMutationKey],
    variant_lookup: dict[_MutationTuple, float | None],
    db_hit_keys: set[_MutationTuple],
    coverage_gaps: _CoverageIntervals,
) -> list[CompareCell]:
    """Build one sample's matrix row: detected AF, ``coverage_gap=True`` in a gap, or ``0.0``."""
    row: list[CompareCell] = []
    for mutation in all_mutations:
        key = (mutation.feature, mutation.position, mutation.ref_aa, mutation.alt_aa)
        if key in variant_lookup:
            row.append(
                CompareCell(
                    allele_freq=variant_lookup[key],
                    db_hit=key in db_hit_keys,
                    coverage_gap=False,
                )
            )
        elif _is_in_coverage_gap(coverage_gaps, mutation.feature, mutation.position):
            row.append(CompareCell(allele_freq=None, db_hit=False, coverage_gap=True))
        else:
            row.append(CompareCell(allele_freq=0.0, db_hit=False, coverage_gap=False))
    return row
