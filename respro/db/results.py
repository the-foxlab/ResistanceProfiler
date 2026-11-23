"""Persistence and reconstruction helpers for profiling results and JSON exports."""

from __future__ import annotations

import json
import math
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


def deserialize_combined_states(blob: object) -> list[CodonState]:
    """Deserialize a ``combined_states`` JSON string into ``CodonState`` objects."""
    if blob is None or blob == '':
        return []
    if not isinstance(blob, str):
        raise ValueError('Invalid combined_states payload: expected a JSON string')
    try:
        items = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid combined_states payload: {exc.msg}') from exc
    if not isinstance(items, list):
        raise ValueError('Invalid combined_states payload: expected a JSON array')

    states: list[CodonState] = []
    for idx, item in enumerate(items, start=1):
        ctx = f'combined_states[{idx}]'
        if not isinstance(item, dict):
            raise ValueError(f'Invalid results JSON: {ctx} must be an object')
        raw_members = item.get('member_indices', [])
        if not isinstance(raw_members, list):
            raise ValueError(f'Invalid results JSON: {ctx}.member_indices must be an array')
        if 'accepted' not in item or not isinstance(item['accepted'], (bool, int)):
            raise ValueError(f'Invalid results JSON: {ctx}.accepted must be a boolean')
        for key in ('lower', 'upper', 'forced_fraction'):
            if key not in item:
                raise ValueError(f'Invalid results JSON: {ctx} missing {key!r}')
        states.append(CodonState(
            alt_codon=_require_str_field(item, 'alt_codon', ctx),
            alt_aa=_require_str_field(item, 'alt_aa', ctx),
            lower=_require_finite_float(item['lower'], f'{ctx}.lower'),
            upper=_require_finite_float(item['upper'], f'{ctx}.upper'),
            forced_fraction=_require_finite_float(item['forced_fraction'], f'{ctx}.forced_fraction'),
            accepted=bool(item['accepted']),
            member_indices=tuple(
                _require_int_value(m, f'{ctx}.member_indices') for m in raw_members
            ),
        ))
    return states


def serialize_haplotype_links(links: tuple[tuple[int, int, float], ...]) -> str:
    """Serialize variant haplotype links to a JSON string."""
    return json.dumps([[int(rec_id), int(alt_id), float(af)] for rec_id, alt_id, af in links])


def deserialize_haplotype_links(blob: object) -> tuple[tuple[int, int, float], ...]:
    """Deserialize a ``haplotype_links`` JSON string or list into normalized tuples."""
    if blob is None or blob == '' or (isinstance(blob, list) and not blob):
        return ()
    if isinstance(blob, str):
        try:
            raw = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid haplotype_links payload: {exc.msg}') from exc
    elif isinstance(blob, list):
        raw = blob
    else:
        raise ValueError('Invalid haplotype_links payload: expected a JSON string or array')
    if not isinstance(raw, list):
        raise ValueError('Invalid haplotype_links payload: expected a JSON array')

    links: list[tuple[int, int, float]] = []
    for idx, item in enumerate(raw, start=1):
        ctx = f'haplotype_links[{idx}]'
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError(f'Invalid results JSON: {ctx} must be a 3-element array')
        links.append((
            _require_int_value(item[0], f'{ctx}[0]'),
            _require_int_value(item[1], f'{ctx}[1]'),
            _require_finite_float(item[2], f'{ctx}[2]'),
        ))
    return tuple(links)


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
    for idx, row in enumerate(variant_rows, start=1):
        ctx = f'variant_result[{idx}]'
        drug_hits = _parse_json_field(row.get('drug_hits') or '[]', list, f'{ctx}.drug_hits')
        if any(not isinstance(item, dict) for item in drug_hits):
            raise ValueError(f'Invalid results JSON: {ctx}.drug_hits entries must be objects')
        chrom = _require_str_field(row, 'chrom', ctx)
        ref = _require_str_field(row, 'ref', ctx)
        alt = _require_str_field(row, 'alt', ctx)
        pos = _require_int_field(row, 'pos', ctx)
        allele_freq = _optional_float(row.get('allele_freq'), 0.0, f'{ctx}.allele_freq')
        depth = _optional_int(row.get('depth'), 0, f'{ctx}.depth')
        variant = VariantCall(
            chrom=chrom,
            pos=pos,
            ref=ref,
            alt=alt,
            allele_freq=allele_freq,
            depth=depth,
            source=str(row.get('variant_source') or ''),
            haplotype_links=deserialize_haplotype_links(row.get('haplotype_links')),
        )
        feature_name = str(row.get('feature_name') or '')
        rule_matches = [_rule_from_hit(hit, feature_name, f'{ctx}.drug_hits') for hit in drug_hits]
        raw_single_af = row.get('single_exchange_aa_freq')
        single_af = (
            _optional_float(raw_single_af, 0.0, f'{ctx}.single_exchange_aa_freq')
            if raw_single_af is not None
            else _SINGLE_EXCHANGE_AA_FREQ_UNSET
        )
        raw_freq_map = _parse_json_field(
            row.get('rule_effect_aa_freq') or '{}', dict, f'{ctx}.rule_effect_aa_freq',
        )
        raw_alt_map = _parse_json_field(
            row.get('rule_effect_alt') or '{}', dict, f'{ctx}.rule_effect_alt',
        )
        try:
            rule_effect_aa_freq = {int(k): _optional_float(v, 0.0, f'{ctx}.rule_effect_aa_freq[{k}]') for k, v in raw_freq_map.items()}
            rule_effect_alt = {int(k): str(v) for k, v in raw_alt_map.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Invalid results JSON: {ctx} has invalid rule_effect map entry') from exc

        annotations.append(AnnotatedVariant(
            variant=variant,
            feature_name=feature_name,
            codon_pos=_optional_int(row.get('codon_pos'), 0, f'{ctx}.codon_pos'),
            ref_codon=str(row.get('ref_codon') or ''),
            alt_codon=str(row.get('alt_codon') or ''),
            ref_aa=str(row.get('ref_aa') or ''),
            alt_aa=str(row.get('alt_aa') or ''),
            consequence=str(row.get('consequence') or ''),
            af_bin=str(row.get('af_bin') or ''),
            is_combined_codon_event=bool(row.get('is_combined_codon_event', 0)),
            combined_member_count=_optional_int(row.get('combined_member_count'), 1, f'{ctx}.combined_member_count') or 1,
            combined_states=deserialize_combined_states(row.get('combined_states')),
            single_exchange_aa_freq=single_af,
            rule_effect_aa_freq=rule_effect_aa_freq,
            rule_effect_alt=rule_effect_alt,
            freq_method=str(row.get('freq_method') or 'observed'),
            rule_matches=rule_matches,
        ))
    return annotations


def reconstruct_formula_rule_hits(
    formula_rows: list[dict],
    annotations: list[AnnotatedVariant],
) -> list[FormulaRuleHit]:
    """Reconstruct ``FormulaRuleHit`` objects from persisted formula-hit rows."""
    hits: list[FormulaRuleHit] = []
    for idx, row in enumerate(formula_rows, start=1):
        ctx = f'formula_rule_hit[{idx}].hit_json'
        payload = _parse_json_field(row.get('hit_json') or '{}', dict, ctx)
        raw_member_ids = payload.get('matched_member_ids', [])
        if raw_member_ids is None:
            raw_member_ids = []
        if not isinstance(raw_member_ids, list) or any(not isinstance(m, str) for m in raw_member_ids):
            raise ValueError(f'Invalid results JSON: {ctx}.matched_member_ids must be an array of strings')
        matched_variants_raw = payload.get('matched_variants', [])
        if matched_variants_raw is None:
            matched_variants_raw = []
        hits.append(FormulaRuleHit(
            rule_set=_rule_set_from_formula_hit(payload, ctx),
            matched_variants=_match_formula_variants(matched_variants_raw, annotations, f'{ctx}.matched_variants'),
            matched_member_ids=list(raw_member_ids),
            frechet_lower=_optional_float(payload.get('frechet_lower'), 0.0, f'{ctx}.frechet_lower'),
            forced_fraction=_optional_float(payload.get('forced_fraction'), 0.0, f'{ctx}.forced_fraction'),
            member_count=_optional_int(payload.get('member_count'), 1, f'{ctx}.member_count'),
        ))
    return hits


def _publications_from_payload(items: object, context: str = 'publications') -> list[Publication]:
    if items is None:
        return []
    if not isinstance(items, list) or any(not isinstance(p, dict) for p in items):
        raise ValueError(f'Invalid results JSON: {context} must be an array of objects')
    return [
        Publication(
            id=0,
            doi=str(p.get('doi') or ''),
            title=str(p.get('title') or ''),
            pubmed_id=str(p.get('pubmed_id') or ''),
            raw_input=str(p.get('raw_input') or ''),
            first_author=str(p.get('first_author') or ''),
            year=str(p.get('year') or ''),
            journal=str(p.get('journal') or ''),
        )
        for p in items
    ]


def _rule_from_hit(hit: dict, feature_name: str, context: str = 'drug_hits') -> ResistanceRule:
    drug_name = str(hit.get('drug') or '')
    return ResistanceRule(
        id=_optional_int(hit.get('rule_id'), 0, f'{context}.rule_id'),
        feature_name=feature_name,
        feature_id=0,
        drug_name=drug_name,
        drug_id=0,
        external_id=str(hit.get('external_id') or ''),
        reference_identifier=str(hit.get('reference_identifier') or ''),
        position=0,
        reference=str(hit.get('reference') or ''),
        mutation=str(hit.get('mutation') or ''),
        phenotype=str(hit.get('phenotype') or ''),
        clinical_phenotype=str(hit.get('clinical_phenotype') or ''),
        ic50=str(hit.get('ic50') or ''),
        fold_ic50=str(hit.get('fold_ic50') or ''),
        score=str(hit.get('score') or ''),
        source=str(hit.get('source') or ''),
        publications=_publications_from_payload(hit.get('publications', []), f'{context}.publications'),
        pubchem_url=str(hit.get('pubchem_url') or ''),
        is_internal_formula_component=is_internal_formula_component_drug_name(drug_name),
    )


def _rule_set_from_formula_hit(payload: dict, context: str = 'hit_json') -> ResistanceRuleSet:
    raw_members = payload.get('members', [])
    if raw_members is None:
        raw_members = []
    if not isinstance(raw_members, list) or any(not isinstance(m, dict) for m in raw_members):
        raise ValueError(f'Invalid results JSON: {context}.members must be an array of objects')
    members = [
        ResistanceRuleSetMember(
            id=idx,
            rule_set_id=0,
            feature_name=str(m.get('feature') or ''),
            feature_id=0,
            reference_identifier='',
            position=max(0, _optional_int(m.get('position'), 1, f'{context}.members[{idx}].position') - 1),
            reference=str(m.get('reference') or ''),
            mutation=str(m.get('mutation') or ''),
            external_id=str(m.get('member_id') or ''),
        )
        for idx, m in enumerate(raw_members, start=1)
    ]
    return ResistanceRuleSet(
        id=0,
        drug_name=str(payload.get('drug') or ''),
        drug_id=0,
        phenotype=str(payload.get('phenotype') or ''),
        clinical_phenotype=str(payload.get('clinical_phenotype') or ''),
        ic50=str(payload.get('ic50') or ''),
        fold_ic50=str(payload.get('fold_ic50') or ''),
        score=str(payload.get('score') or ''),
        source=str(payload.get('source') or ''),
        group_name=str(payload.get('rule_group') or ''),
        pubchem_url=str(payload.get('pubchem_url') or ''),
        logic_expression=str(payload.get('logic_expression') or ''),
        publications=_publications_from_payload(payload.get('publications', []), f'{context}.publications'),
        members=members,
    )


def _match_formula_variants(
    variant_payloads: object,
    annotations: list[AnnotatedVariant],
    context: str = 'matched_variants',
) -> list[AnnotatedVariant]:
    """Pair persisted formula-variant summaries back to reconstructed ``AnnotatedVariant`` rows."""
    if not isinstance(variant_payloads, list) or any(not isinstance(item, dict) for item in variant_payloads):
        raise ValueError(f'Invalid results JSON: {context} must be an array of objects')
    by_exact: dict[tuple, AnnotatedVariant] = {}
    by_weak: dict[tuple, AnnotatedVariant] = {}
    for ann in annotations:
        weak = (ann.feature_name, ann.codon_pos + 1, ann.ref_aa, ann.alt_aa)
        by_exact.setdefault((*weak, round(ann.variant.allele_freq, 6)), ann)
        by_weak.setdefault(weak, ann)

    matched: list[AnnotatedVariant] = []
    for idx, item in enumerate(variant_payloads, start=1):
        af = _optional_float(item.get('allele_freq'), 0.0, f'{context}[{idx}].allele_freq')
        codon_1based = _optional_int(item.get('codon_pos'), 1, f'{context}[{idx}].codon_pos')
        feature = str(item.get('feature') or '')
        ref_aa = str(item.get('ref_aa') or '')
        alt_aa = str(item.get('alt_aa') or '')
        weak = (feature, codon_1based, ref_aa, alt_aa)
        found = by_exact.get((*weak, round(af, 6))) or by_weak.get(weak)
        if found is not None:
            matched.append(found)
            continue
        matched.append(AnnotatedVariant(
            variant=VariantCall(chrom='', pos=0, ref='', alt='', allele_freq=af, depth=0),
            feature_name=feature,
            codon_pos=max(0, codon_1based - 1),
            ref_aa=ref_aa,
            alt_aa=alt_aa,
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
    for key in ('project_name', 'reference_name', 'sample_name', 'vcf_path', 'created_at'):
        _require_str_field(run_dict, key, 'run')
    for key in ('total_variants', 'variants_in_cds', 'resistance_hits'):
        run_dict[key] = _require_int_field(run_dict, key, 'run')

    run_dict.setdefault('project_db_path', '')
    run_dict.setdefault('project_fingerprint', '')
    run_dict.setdefault('formula_hits', len(formula_rows))
    run_dict.setdefault('status', 'complete')
    references = _require_list_of_dicts(payload, 'references') if 'references' in payload else []
    for idx, ref in enumerate(references, start=1):
        for field in ('reference_name', 'query_name'):
            val = ref.get(field)
            if val is not None and not isinstance(val, str):
                raise ValueError(f'Invalid results JSON: references[{idx}].{field} must be a string')
        pfn = ref.get('profiled_feature_names')
        if pfn is not None and (not isinstance(pfn, list) or any(not isinstance(f, str) for f in pfn)):
            raise ValueError(
                f'Invalid results JSON: references[{idx}].profiled_feature_names must be an array of strings'
            )
    run_dict['references'] = references

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
        ref_name = row.get('reference_name')
        if ref_name is not None and not isinstance(ref_name, str):
            raise ValueError(f'Invalid results JSON: variant_result[{idx}].reference_name must be a string')
        row['pos'] = _require_int_field(row, 'pos', f'variant_result[{idx}]')
    for idx, row in enumerate(formula_rows, start=1):
        if 'hit_json' not in row:
            raise ValueError(f"Invalid results JSON: formula_rule_hit[{idx}] missing 'hit_json'")

    # Exercise the actual reconstruction helpers so upload validation and regenerate share one contract.
    reconstructed = reconstruct_annotations(variant_rows)
    reconstruct_formula_rule_hits(formula_rows, reconstructed)

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


def _require_str_field(row: dict, key: str, context: str) -> str:
    val = row.get(key)
    if not isinstance(val, str):
        raise ValueError(f'Invalid results JSON: {context}.{key} must be a string')
    return val


def _require_int_value(val: object, context: str) -> int:
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        raise ValueError(f'Invalid results JSON: {context} must be an integer')
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'Invalid results JSON: {context} must be an integer') from exc


def _require_finite_float(val: object, context: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        raise ValueError(f'Invalid results JSON: {context} must be a number')
    try:
        parsed = float(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid results JSON: {context} must be a number') from exc
    if not math.isfinite(parsed):
        raise ValueError(f'Invalid results JSON: {context} must be finite')
    return parsed


def _require_int_field(row: dict, key: str, context: str) -> int:
    return _require_int_value(row.get(key), f'{context}.{key}')


def _optional_int(val: object, default: int, context: str) -> int:
    if val is None or val == '':
        return default
    return _require_int_value(val, context)


def _optional_float(val: object, default: float, context: str) -> float:
    if val is None or val == '':
        return default
    return _require_finite_float(val, context)


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
