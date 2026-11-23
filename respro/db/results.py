"""Persistence and reconstruction helpers for profiling results and JSON exports."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TypeVar

from respro.db.models import (
    _SINGLE_EXCHANGE_AA_FREQ_UNSET,
    AnnotatedVariant,
    CodonState,
    CoverageGap,
    FormulaRuleHit,
    ProfilingResult,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
    is_internal_formula_component_drug_name,
)


def serialize_combined_states(states: list[CodonState]) -> str:
    """Serialize Fréchet combined codon states to a JSON string."""
    return json.dumps([
        {
            'alt_codon': s.alt_codon,
            'alt_aa': s.alt_aa,
            'lower': s.lower,
            'upper': s.upper,
            'forced_fraction': s.forced_fraction,
            'accepted': s.accepted,
            'member_indices': list(s.member_indices),
        }
        for s in states
    ])


def deserialize_combined_states(blob: str | None) -> list[CodonState]:
    """Deserialize a ``combined_states`` JSON string into ``CodonState`` objects."""
    if not blob:
        return []
    try:
        items = json.loads(blob)
        if not isinstance(items, list):
            raise ValueError('combined_states must be a JSON array')
        return [
            CodonState(
                alt_codon=item['alt_codon'],
                alt_aa=item['alt_aa'],
                lower=float(item['lower']),
                upper=float(item['upper']),
                forced_fraction=float(item['forced_fraction']),
                accepted=bool(item['accepted']),
                member_indices=tuple(item.get('member_indices', [])),
            )
            for item in items
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid combined_states payload: {exc}') from exc


def serialize_haplotype_links(links: tuple[tuple[int, int, float], ...]) -> str:
    """Serialize variant haplotype links to a JSON string."""
    return json.dumps([[int(rec_id), int(alt_id), float(af)] for rec_id, alt_id, af in links])


def deserialize_haplotype_links(blob: str | list | None) -> tuple[tuple[int, int, float], ...]:
    """Deserialize a ``haplotype_links`` JSON string or list into normalized tuples."""
    if not blob:
        return ()
    try:
        raw = json.loads(blob) if isinstance(blob, str) else blob
        if not isinstance(raw, list):
            raise ValueError('haplotype_links must be a JSON array')
        return tuple((int(item[0]), int(item[1]), float(item[2])) for item in raw)
    except (json.JSONDecodeError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid haplotype_links payload: {exc}') from exc


def _insert_variant_and_formula_rows(
    results_conn: sqlite3.Connection,
    run_id: int,
    result: ProfilingResult,
) -> None:
    """Insert all 27-column ``variant_result`` rows and ``formula_rule_hit`` rows for *run_id*."""
    # Key by chrom (== ReferenceGroup.query_name) so shared feature names across references never collide.
    ref_name_by_chrom = {rg.query_name: rg.reference_name for rg in result.references}
    fallback_ref = result.references[0].reference_name if len(result.references) == 1 else ''

    def _ref_for(chrom: str) -> str:
        return ref_name_by_chrom.get(chrom, fallback_ref)

    results_conn.executemany(
        'INSERT INTO variant_result '
        '(run_id, chrom, pos, ref, alt, allele_freq, depth, '
        'feature_name, reference_name, codon_pos, ref_codon, alt_codon, ref_aa, alt_aa, '
        'consequence, af_bin, rule_match, drug_hits, '
        'is_combined_codon_event, combined_member_count, combined_states, '
        'single_exchange_aa_freq, rule_effect_aa_freq, rule_effect_alt, freq_method, '
        'variant_source, haplotype_links) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                run_id,
                ann.variant.chrom,
                ann.variant.pos,
                ann.variant.ref,
                ann.variant.alt,
                ann.variant.allele_freq,
                ann.variant.depth,
                ann.feature_name,
                _ref_for(ann.variant.chrom),
                ann.codon_pos,
                ann.ref_codon,
                ann.alt_codon,
                ann.ref_aa,
                ann.alt_aa,
                ann.consequence,
                ann.af_bin,
                int(ann.is_resistance_hit),
                json.dumps(ann.drug_hits_json()),
                int(ann.is_combined_codon_event),
                ann.combined_member_count,
                serialize_combined_states(ann.combined_states),
                ann.single_exchange_aa_freq,
                json.dumps(ann.rule_effect_aa_freq),
                json.dumps(ann.rule_effect_alt),
                ann.freq_method,
                ann.variant.source,
                serialize_haplotype_links(ann.variant.haplotype_links),
            )
            for ann in result.annotations
        ],
    )

    results_conn.executemany(
        'INSERT INTO formula_rule_hit (run_id, reference_name, hit_json) VALUES (?, ?, ?)',
        [
            (
                run_id,
                _ref_for(fh.matched_variants[0].variant.chrom if fh.matched_variants else ''),
                json.dumps(fh.to_dict()),
            )
            for fh in result.formula_hits
        ],
    )


def replace_run_results(
    results_conn: sqlite3.Connection,
    run_id: int,
    result: ProfilingResult,
    project_conn: sqlite3.Connection,
) -> None:
    """Replace variant/formula rows and refresh summary counters for a synced run."""
    with results_conn:
        results_conn.execute('DELETE FROM variant_result WHERE run_id = ?', (run_id,))
        results_conn.execute('DELETE FROM formula_rule_hit WHERE run_id = ?', (run_id,))
        _insert_variant_and_formula_rows(results_conn, run_id, result)
        results_conn.execute(
            'UPDATE run SET resistance_hits = ?, formula_hits = ?, project_updated_at = ? WHERE id = ?',
            (result.resistance_hits, len(result.formula_hits), project_updated_at(project_conn), run_id),
        )


def save_run(
    results_conn: sqlite3.Connection,
    project_db_path: Path,
    project_conn: sqlite3.Connection,
    result: ProfilingResult,
) -> int:
    """Persist one ``ProfilingResult`` and all dependent rows to the results DB; return run id."""
    fingerprint = project_fingerprint(project_conn)
    updated_at = project_updated_at(project_conn)
    ref_name_by_chrom = {rg.query_name: rg.reference_name for rg in result.references}

    with results_conn:
        cursor = results_conn.execute(
            'INSERT INTO run '
            '(project_name, project_db_path, project_fingerprint, project_updated_at, reference_name, '
            'sample_name, vcf_path, total_variants, variants_in_cds, '
            'resistance_hits, formula_hits, status, is_fasta_mode, coverage_assessed) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                result.project_name,
                str(project_db_path),
                fingerprint,
                updated_at,
                result.reference_name,
                result.sample_name,
                result.vcf_name,
                result.total_variants,
                result.variants_in_cds,
                result.resistance_hits,
                len(result.formula_hits),
                'complete',
                int(result.is_fasta_mode),
                int(result.coverage_assessed),
            ),
        )
        assert cursor.lastrowid is not None
        run_id = int(cursor.lastrowid)

        _insert_variant_and_formula_rows(results_conn, run_id, result)

        results_conn.executemany(
            'INSERT INTO coverage_gap (run_id, feature_name, reference_name, chrom, codon_start, codon_end) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [
                (run_id, gap.feature_name, ref_name_by_chrom.get(gap.chrom, ''),
                 gap.chrom, gap.codon_start, gap.codon_end)
                for gap in result.coverage_gaps
            ],
        )

        profiled_rows: list[tuple[int, str, str]] = []
        for rg in result.references:
            seen: set[str] = set()
            for match in rg.feature_matches:
                name = (match.feature.name or '').strip()
                if name and name not in seen:
                    seen.add(name)
                    profiled_rows.append((run_id, rg.reference_name, name))
        if profiled_rows:
            results_conn.executemany(
                'INSERT INTO profiled_feature (run_id, reference_name, feature_name) VALUES (?, ?, ?)',
                profiled_rows,
            )

    return run_id


def project_fingerprint(project_conn: sqlite3.Connection) -> str:
    """Return the immutable UUID identifying the project database."""
    row = project_conn.execute('SELECT uuid FROM project LIMIT 1').fetchone()
    if row is None:
        raise ValueError('No project found in the database')
    return str(row['uuid'])


def project_updated_at(project_conn: sqlite3.Connection) -> str:
    """Return the last-updated ISO timestamp of the project database, or ``''``."""
    row = project_conn.execute('SELECT updated_at FROM project LIMIT 1').fetchone()
    return row['updated_at'] or '' if row else ''


def load_existing_run_project_fingerprint(results_conn: sqlite3.Connection) -> str | None:
    """Return one stored non-empty project fingerprint from the results DB, or ``None``."""
    row = results_conn.execute(
        "SELECT project_fingerprint FROM run WHERE project_fingerprint != '' LIMIT 1"
    ).fetchone()
    return str(row['project_fingerprint']) if row is not None else None


def list_runs(results_conn: sqlite3.Connection) -> list[dict]:
    """Return summary dicts for all stored runs ordered by id."""
    rows = results_conn.execute(
        'SELECT id, sample_name, reference_name, vcf_path, '
        'total_variants, variants_in_cds, resistance_hits, formula_hits, created_at '
        'FROM run ORDER BY id'
    ).fetchall()
    return [dict(row) for row in rows]


def find_run_summary(results_conn: sqlite3.Connection, run_identifier: str | int) -> dict[str, str | int]:
    """Resolve *run_identifier* and return ``{'id', 'sample_name'}`` or raise ``ValueError``."""
    run_id = _resolve_run_id(results_conn, run_identifier)
    run_row = results_conn.execute(
        'SELECT id, sample_name FROM run WHERE id = ?',
        (run_id,),
    ).fetchone()
    if run_row is None:
        raise ValueError(f'No run found for identifier {run_identifier!r}')
    return {'id': int(run_row['id']), 'sample_name': run_row['sample_name'] or ''}


def delete_run(results_conn: sqlite3.Connection, run_identifier: str | int) -> dict[str, str | int]:
    """Delete one stored run and all its dependent rows; return ``{'id', 'sample_name'}``."""
    summary = find_run_summary(results_conn, run_identifier)
    run_id = int(summary['id'])
    with results_conn:
        for table in (
            'variant_result',
            'coverage_gap',
            'formula_rule_hit',
            'sample_classification',
            'profiled_feature',
        ):
            results_conn.execute(f'DELETE FROM {table} WHERE run_id = ?', (run_id,))
        results_conn.execute('DELETE FROM run WHERE id = ?', (run_id,))
    return summary


def load_run(results_conn: sqlite3.Connection, run_id: int) -> tuple[dict, list[dict]]:
    """Load one run row and its ordered ``variant_result`` rows."""
    run_row = results_conn.execute('SELECT * FROM run WHERE id = ?', (run_id,)).fetchone()
    if run_row is None:
        raise ValueError(f'No run found with id {run_id}')
    variant_rows = results_conn.execute(
        'SELECT * FROM variant_result WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return dict(run_row), [dict(row) for row in variant_rows]


def load_coverage_gaps(results_conn: sqlite3.Connection, run_id: int) -> list[CoverageGap]:
    """Load persisted ``CoverageGap`` records for *run_id*."""
    rows = results_conn.execute(
        'SELECT feature_name, reference_name, chrom, codon_start, codon_end FROM coverage_gap '
        'WHERE run_id = ? ORDER BY feature_name, chrom, codon_start, codon_end, id',
        (run_id,),
    ).fetchall()
    return [
        CoverageGap(
            feature_name=row['feature_name'],
            codon_start=row['codon_start'],
            codon_end=row['codon_end'],
            chrom=row['chrom'] or '',
        )
        for row in rows
    ]


def load_formula_rule_hits(results_conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Load persisted formula-rule hit rows for *run_id*."""
    rows = results_conn.execute(
        'SELECT id, run_id, reference_name, hit_json FROM formula_rule_hit WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_profiled_features(results_conn: sqlite3.Connection, run_id: int) -> dict[str, list[str]]:
    """Load persisted profiled feature names for *run_id*, grouped by ``reference_name``."""
    rows = results_conn.execute(
        'SELECT reference_name, feature_name FROM profiled_feature WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    by_ref: dict[str, list[str]] = {}
    for row in rows:
        by_ref.setdefault(row['reference_name'] or '', []).append(row['feature_name'])
    return by_ref


def reconstruct_annotations(variant_rows: list[dict]) -> list[AnnotatedVariant]:
    """Reconstruct ``AnnotatedVariant`` objects from stored ``variant_result`` row dicts."""
    annotations: list[AnnotatedVariant] = []
    for row in variant_rows:
        drug_hits = json.loads(row.get('drug_hits') or '[]')
        variant = VariantCall(
            chrom=row['chrom'],
            pos=row['pos'],
            ref=row['ref'],
            alt=row['alt'],
            allele_freq=row.get('allele_freq') or 0.0,
            depth=row.get('depth') or 0,
            source=row.get('variant_source') or '',
            haplotype_links=deserialize_haplotype_links(row.get('haplotype_links')),
        )
        feature_name = row.get('feature_name') or ''
        rule_matches = [_rule_from_hit(hit, feature_name) for hit in drug_hits]
        raw_single_af = row.get('single_exchange_aa_freq')
        single_af = float(raw_single_af) if raw_single_af is not None else _SINGLE_EXCHANGE_AA_FREQ_UNSET
        annotations.append(AnnotatedVariant(
            variant=variant,
            feature_name=feature_name,
            codon_pos=row.get('codon_pos') or 0,
            ref_codon=row.get('ref_codon') or '',
            alt_codon=row.get('alt_codon') or '',
            ref_aa=row.get('ref_aa') or '',
            alt_aa=row.get('alt_aa') or '',
            consequence=row.get('consequence') or '',
            af_bin=row.get('af_bin') or '',
            is_combined_codon_event=bool(row.get('is_combined_codon_event', 0)),
            combined_member_count=row.get('combined_member_count', 1) or 1,
            combined_states=deserialize_combined_states(row.get('combined_states') or '[]'),
            single_exchange_aa_freq=single_af,
            rule_effect_aa_freq={
                int(k): float(v)
                for k, v in json.loads(row.get('rule_effect_aa_freq') or '{}').items()
            },
            rule_effect_alt={
                int(k): str(v)
                for k, v in json.loads(row.get('rule_effect_alt') or '{}').items()
            },
            freq_method=row.get('freq_method') or 'observed',
            rule_matches=rule_matches,
        ))
    return annotations


def reconstruct_formula_rule_hits(
    formula_rows: list[dict],
    annotations: list[AnnotatedVariant],
) -> list[FormulaRuleHit]:
    """Reconstruct ``FormulaRuleHit`` objects from persisted formula-hit rows."""
    hits: list[FormulaRuleHit] = []
    for row in formula_rows:
        payload = json.loads(row.get('hit_json') or '{}')
        hits.append(FormulaRuleHit(
            rule_set=_rule_set_from_formula_hit(payload),
            matched_variants=_match_formula_variants(payload.get('matched_variants', []), annotations),
            matched_member_ids=list(payload.get('matched_member_ids', [])),
            frechet_lower=float(payload.get('frechet_lower', 0.0)),
            forced_fraction=float(payload.get('forced_fraction', 0.0)),
            member_count=int(payload.get('member_count', 1)),
        ))
    return hits


def _publications_from_payload(items: list[dict]) -> list[Publication]:
    return [
        Publication(
            id=0,
            doi=p.get('doi', ''),
            title=p.get('title', ''),
            pubmed_id=p.get('pubmed_id', ''),
            raw_input=p.get('raw_input', ''),
            first_author=p.get('first_author', ''),
            year=p.get('year', ''),
            journal=p.get('journal', ''),
        )
        for p in items
    ]


def _rule_from_hit(hit: dict, feature_name: str) -> ResistanceRule:
    return ResistanceRule(
        id=int(hit.get('rule_id', 0) or 0),
        feature_name=feature_name,
        feature_id=0,
        drug_name=hit.get('drug', ''),
        drug_id=0,
        external_id=hit.get('external_id', ''),
        reference_identifier=hit.get('reference_identifier', ''),
        position=0,
        reference=hit.get('reference', ''),
        mutation=hit.get('mutation', ''),
        phenotype=hit.get('phenotype', ''),
        clinical_phenotype=hit.get('clinical_phenotype', ''),
        ic50=hit.get('ic50', ''),
        fold_ic50=hit.get('fold_ic50', ''),
        score=hit.get('score', ''),
        source=hit.get('source', ''),
        publications=_publications_from_payload(hit.get('publications', [])),
        pubchem_url=hit.get('pubchem_url', ''),
        is_internal_formula_component=is_internal_formula_component_drug_name(hit.get('drug', '')),
    )


def _rule_set_from_formula_hit(payload: dict) -> ResistanceRuleSet:
    members = [
        ResistanceRuleSetMember(
            id=idx,
            rule_set_id=0,
            feature_name=m.get('feature', ''),
            feature_id=0,
            reference_identifier='',
            position=max(0, int(m.get('position', 1) or 1) - 1),
            reference=m.get('reference', ''),
            mutation=m.get('mutation', ''),
            external_id=m.get('member_id', ''),
        )
        for idx, m in enumerate(payload.get('members', []), start=1)
    ]
    return ResistanceRuleSet(
        id=0,
        drug_name=payload.get('drug', ''),
        drug_id=0,
        phenotype=payload.get('phenotype', ''),
        clinical_phenotype=payload.get('clinical_phenotype', ''),
        ic50=payload.get('ic50', ''),
        fold_ic50=payload.get('fold_ic50', ''),
        score=payload.get('score', ''),
        source=payload.get('source', ''),
        group_name=payload.get('rule_group', ''),
        pubchem_url=payload.get('pubchem_url', ''),
        logic_expression=payload.get('logic_expression', ''),
        publications=_publications_from_payload(payload.get('publications', [])),
        members=members,
    )


def _match_formula_variants(
    variant_payloads: list[dict],
    annotations: list[AnnotatedVariant],
) -> list[AnnotatedVariant]:
    """Pair persisted formula-variant summaries back to reconstructed ``AnnotatedVariant`` rows."""
    by_exact: dict[tuple, AnnotatedVariant] = {}
    by_weak: dict[tuple, AnnotatedVariant] = {}
    for ann in annotations:
        weak = (ann.feature_name, ann.codon_pos + 1, ann.ref_aa, ann.alt_aa)
        by_exact.setdefault((*weak, round(ann.variant.allele_freq, 6)), ann)
        by_weak.setdefault(weak, ann)

    matched: list[AnnotatedVariant] = []
    for item in variant_payloads:
        af = float(item.get('allele_freq', 0.0) or 0.0)
        codon_1based = int(item.get('codon_pos', 1) or 1)
        weak = (item.get('feature', ''), codon_1based, item.get('ref_aa', ''), item.get('alt_aa', ''))
        found = by_exact.get((*weak, round(af, 6))) or by_weak.get(weak)
        if found is not None:
            matched.append(found)
            continue
        matched.append(AnnotatedVariant(
            variant=VariantCall(chrom='', pos=0, ref='', alt='', allele_freq=af, depth=0),
            feature_name=item.get('feature', ''),
            codon_pos=max(0, codon_1based - 1),
            ref_aa=item.get('ref_aa', ''),
            alt_aa=item.get('alt_aa', ''),
            consequence='',
        ))
    return matched


def save_classification(
    results_conn: sqlite3.Connection,
    run_id: int,
    *,
    drug: str = '',
    phenotype: str = '',
    clinical_phenotype: str = '',
    ic50: str = '',
    fold_ic50: str = '',
    note: str = '',
    source: str = '',
) -> int:
    """Upsert the manual sample classification row for ``(run_id, drug)``."""
    with results_conn:
        existing = results_conn.execute(
            'SELECT id FROM sample_classification '
            'WHERE run_id = ? AND LOWER(drug) = LOWER(?) ORDER BY id LIMIT 1',
            (run_id, drug),
        ).fetchone()
        if existing is None:
            cursor = results_conn.execute(
                'INSERT INTO sample_classification '
                '(run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source),
            )
            assert cursor.lastrowid is not None
            row_id = int(cursor.lastrowid)
        else:
            row_id = int(existing['id'])
            results_conn.execute(
                'UPDATE sample_classification '
                'SET drug = ?, phenotype = ?, clinical_phenotype = ?, ic50 = ?, fold_ic50 = ?, '
                "note = ?, source = ?, created_at = datetime('now') WHERE id = ?",
                (drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source, row_id),
            )
            results_conn.execute(
                'DELETE FROM sample_classification WHERE run_id = ? AND LOWER(drug) = LOWER(?) AND id != ?',
                (run_id, drug, row_id),
            )
    return row_id


def load_classifications(results_conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Load all manual sample classifications for *run_id*."""
    rows = results_conn.execute(
        'SELECT id, run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, '
        'note, source, created_at FROM sample_classification WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_run_from_json(
    json_path: Path,
) -> tuple[dict, list[dict], list[CoverageGap], list[dict], list[dict]]:
    """Load and validate a stored run payload from a ``*.results.json`` export."""
    payload = _read_json_payload(json_path)
    run_dict = dict(_require_dict(payload, 'run'))
    variant_rows = _require_list_of_dicts(payload, 'variant_result')
    coverage_rows = _require_list_of_dicts(payload, 'coverage_gap')
    if 'formula_rule_hit' not in payload:
        raise ValueError("Invalid results JSON: missing key 'formula_rule_hit'")
    formula_rows = _require_list_of_dicts(payload, 'formula_rule_hit')
    classification_rows = _require_list_of_dicts(payload, 'sample_classification')

    required_run_keys = {
        'project_name', 'reference_name', 'sample_name', 'vcf_path',
        'total_variants', 'variants_in_cds', 'resistance_hits', 'created_at',
    }
    missing = sorted(key for key in required_run_keys if key not in run_dict)
    if missing:
        raise ValueError(f'Invalid results JSON: missing run field(s): {", ".join(missing)}')

    run_dict.setdefault('project_db_path', '')
    run_dict.setdefault('project_fingerprint', '')
    run_dict.setdefault('formula_hits', len(formula_rows))
    run_dict.setdefault('status', 'complete')
    run_dict['references'] = _require_list_of_dicts(payload, 'references') if 'references' in payload else []

    coverage_gaps: list[CoverageGap] = []
    for idx, row in enumerate(coverage_rows, start=1):
        for key in ('feature_name', 'codon_start', 'codon_end'):
            if key not in row:
                raise ValueError(f'Invalid results JSON: coverage_gap[{idx}] missing {key!r}')
        coverage_gaps.append(CoverageGap(
            feature_name=str(row.get('feature_name') or ''),
            codon_start=_require_int_field(row, 'codon_start', f'coverage_gap[{idx}]'),
            codon_end=_require_int_field(row, 'codon_end', f'coverage_gap[{idx}]'),
            chrom=str(row.get('chrom') or ''),
        ))

    for idx, row in enumerate(variant_rows, start=1):
        for key in ('chrom', 'pos', 'ref', 'alt', 'drug_hits'):
            if key not in row:
                raise ValueError(f'Invalid results JSON: variant_result[{idx}] missing {key!r}')
        _require_int_field(row, 'pos', f'variant_result[{idx}]')
        _validate_variant_json_fields(row, idx)
    for idx, row in enumerate(formula_rows, start=1):
        if 'hit_json' not in row:
            raise ValueError(f"Invalid results JSON: formula_rule_hit[{idx}] missing 'hit_json'")
        _parse_json_field(row.get('hit_json') or '{}', dict, f'formula_rule_hit[{idx}].hit_json')

    return run_dict, variant_rows, coverage_gaps, formula_rows, classification_rows


def validate_project_fingerprint_match(
    *,
    stored_fingerprint: str,
    current_fingerprint: str,
    source_label: str,
) -> None:
    """Raise ``ValueError`` when a stored project UUID does not match the active project DB."""
    if not stored_fingerprint or stored_fingerprint == current_fingerprint:
        return
    raise ValueError(
        f'Project database UUID mismatch for {source_label}. '
        'The provided project database does not match the database used to create this result JSON/run. '
        'Database updates currently do not allow regeneration of reports from older database versions.'
    )


def _read_json_payload(json_path: Path) -> dict:
    path = Path(json_path)
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise ValueError(f'Results JSON file not found: {path}') from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid results JSON: {exc.msg}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Invalid results JSON: top-level payload must be an object')
    return payload


def _require_dict(payload: dict, key: str) -> dict:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f'Invalid results JSON: key {key!r} must be an object')
    return value


def _require_list_of_dicts(payload: dict, key: str) -> list[dict]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f'Invalid results JSON: key {key!r} must be an array')
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Invalid results JSON: {key}[{idx}] must be an object')
    return value


def _require_int_field(row: dict, key: str, context: str) -> int:
    val = row.get(key)
    if isinstance(val, bool) or val is None:
        raise ValueError(f'Invalid results JSON: {context}.{key} must be an integer')
    try:
        return int(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid results JSON: {context}.{key} must be an integer') from exc


_T = TypeVar('_T')


def _parse_json_field(raw: object, expected_type: type[_T], context: str) -> _T:
    if not isinstance(raw, str):
        raise ValueError(f'Invalid results JSON: {context} must be a JSON string')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid results JSON: {context} is not valid JSON: {exc.msg}') from exc
    if not isinstance(parsed, expected_type):
        raise ValueError(f'Invalid results JSON: {context} must decode to {expected_type.__name__}')
    return parsed


def _validate_variant_json_fields(row: dict, idx: int) -> None:
    ctx = f'variant_result[{idx}]'
    hits = _parse_json_field(row.get('drug_hits') or '[]', list, f'{ctx}.drug_hits')
    if any(not isinstance(item, dict) for item in hits):
        raise ValueError(f'Invalid results JSON: {ctx}.drug_hits entries must be objects')
    if row.get('combined_states'):
        deserialize_combined_states(row['combined_states'])
    if row.get('haplotype_links'):
        deserialize_haplotype_links(row['haplotype_links'])
    for map_field in ('rule_effect_aa_freq', 'rule_effect_alt'):
        if row.get(map_field):
            mapping = _parse_json_field(row[map_field], dict, f'{ctx}.{map_field}')
            try:
                for k, v in mapping.items():
                    int(k)
                    if map_field == 'rule_effect_aa_freq':
                        float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid results JSON: {ctx}.{map_field} has invalid entry') from exc


def _resolve_run_id(results_conn: sqlite3.Connection, run_identifier: str | int) -> int:
    """Resolve a numeric run ID (with optional leading ``#``) to one stored ``run.id``."""
    token = str(run_identifier).strip().lstrip('#')
    if not token:
        raise ValueError('Run identifier must not be empty')
    if not token.isdigit():
        raise ValueError(f'Invalid run identifier {run_identifier!r}: expected a numeric run id')

    run_id = int(token)
    if results_conn.execute('SELECT 1 FROM run WHERE id = ?', (run_id,)).fetchone() is None:
        raise ValueError(f'No run found with id {token}')
    return run_id
