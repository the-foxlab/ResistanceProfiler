"""
Persistence helpers for profiling results.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from respro.db.models import (
    AnnotatedVariant,
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


def save_run(
    results_conn: sqlite3.Connection,
    project_db_path: Path,
    project_conn: sqlite3.Connection,
    result: ProfilingResult,
) -> int:
    """
    Persist a profiling run and its variant annotations to the results database.

    :param results_conn: open results DB connection
    :param project_db_path: resolved path to the project DB used for this run
    :param project_conn: open project DB connection (used to compute fingerprint)
    :param result: ProfilingResult to store
    :return: the new run id
    """
    fingerprint = project_fingerprint(project_conn)
    updated_at = project_updated_at(project_conn)

    cursor = results_conn.execute(
        'INSERT INTO run '
        '(project_name, project_db_path, project_fingerprint, project_updated_at, reference_name, '
        'sample_name, vcf_path, total_variants, variants_in_cds, '
        'resistance_hits, formula_hits, status) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
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
        ),
    )
    run_id = cursor.lastrowid

    for ann in result.annotations:
        v = ann.variant
        results_conn.execute(
            'INSERT INTO variant_result '
            '(run_id, chrom, pos, ref, alt, allele_freq, depth, '
            'feature_name, codon_pos, ref_codon, alt_codon, ref_aa, alt_aa, '
            'consequence, af_bin, rule_match, drug_hits, '
            'is_combined_codon_event, combined_member_count) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                run_id,
                v.chrom,
                v.pos,
                v.ref,
                v.alt,
                v.allele_freq,
                v.depth,
                ann.feature_name,
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
            ),
        )

    for gap in result.coverage_gaps:
        results_conn.execute(
            'INSERT INTO coverage_gap (run_id, feature_name, codon_start, codon_end) VALUES (?, ?, ?, ?)',
            (run_id, gap.feature_name, gap.codon_start, gap.codon_end),
        )

    for formula_hit in result.formula_hits:
        results_conn.execute(
            'INSERT INTO formula_rule_hit (run_id, hit_json) VALUES (?, ?)',
            (run_id, json.dumps(formula_hit.to_dict())),
        )

    results_conn.commit()
    return run_id


def project_fingerprint(project_conn: sqlite3.Connection) -> str:
    """
    Return the stable UUID that identifies a project database.

    The UUID is assigned once at project creation and never changes, so it
    remains valid even after rules are added via ``respro init-add``.

    :param project_conn: open project DB connection
    :return: UUID string
    """
    row = project_conn.execute('SELECT uuid FROM project LIMIT 1').fetchone()
    if row is None:
        raise ValueError('No project found in the database')
    return row['uuid']


def project_updated_at(project_conn: sqlite3.Connection) -> str:
    """
    Return the last-updated timestamp of the project database.

    :param project_conn: open project DB connection
    :return: ISO timestamp string or empty string
    """
    row = project_conn.execute('SELECT updated_at FROM project LIMIT 1').fetchone()
    return row['updated_at'] or '' if row else ''


def list_runs(results_conn: sqlite3.Connection) -> list[dict]:
    """
    Return a summary list of all stored runs ordered by id.

    :param results_conn: open results DB connection
    :return: list of run summary dicts
    """
    rows = results_conn.execute(
        'SELECT id, sample_name, reference_name, vcf_path, '
        'total_variants, variants_in_cds, resistance_hits, formula_hits, created_at '
        'FROM run ORDER BY id'
    ).fetchall()
    return [dict(row) for row in rows]


def delete_run(results_conn: sqlite3.Connection, run_identifier: str | int) -> dict[str, str | int]:
    """
    Delete one stored run and all dependent rows.

    :param results_conn: open results DB connection
    :param run_identifier: numeric run id (or string form)
    :return: summary dict with deleted id and sample name
    :raises ValueError: when run id is missing or ambiguous
    """
    run_id = _resolve_run_id(results_conn, run_identifier)
    run_row = results_conn.execute(
        'SELECT id, sample_name FROM run WHERE id = ?',
        (run_id,),
    ).fetchone()
    if run_row is None:
        raise ValueError(f'No run found for identifier {run_identifier!r}')

    results_conn.execute('DELETE FROM variant_result WHERE run_id = ?', (run_id,))
    results_conn.execute('DELETE FROM coverage_gap WHERE run_id = ?', (run_id,))
    results_conn.execute('DELETE FROM formula_rule_hit WHERE run_id = ?', (run_id,))
    results_conn.execute('DELETE FROM sample_classification WHERE run_id = ?', (run_id,))
    results_conn.execute('DELETE FROM run WHERE id = ?', (run_id,))
    results_conn.commit()

    return {
        'id': int(run_row['id']),
        'sample_name': run_row['sample_name'] or '',
    }


def load_run(
    results_conn: sqlite3.Connection,
    run_id: int,
) -> tuple[dict, list[dict]]:
    """
    Load a run and its variant results from the results database.

    :param results_conn: open results DB connection
    :param run_id: id of the run to load
    :return: (run_dict, list of variant_result dicts)
    :raises ValueError: if no run with that id exists
    """
    run_row = results_conn.execute(
        'SELECT * FROM run WHERE id = ?', (run_id,)
    ).fetchone()
    if run_row is None:
        raise ValueError(f'No run found with id {run_id}')

    variant_rows = results_conn.execute(
        'SELECT * FROM variant_result WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return dict(run_row), [dict(row) for row in variant_rows]


def load_coverage_gaps(
    results_conn: sqlite3.Connection,
    run_id: int,
) -> list[CoverageGap]:
    """
    Load persisted coverage gaps for a run.

    :param results_conn: open results DB connection
    :param run_id: id of the run to load gaps for
    :return: list of CoverageGap objects ordered by feature_name, codon_start
    """
    tables = {
        row['name']
        for row in results_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if 'coverage_gap' not in tables:
        return []

    rows = results_conn.execute(
        'SELECT feature_name, codon_start, codon_end FROM coverage_gap '
        'WHERE run_id = ? ORDER BY feature_name, codon_start',
        (run_id,),
    ).fetchall()
    return [
        CoverageGap(feature_name=row['feature_name'], codon_start=row['codon_start'], codon_end=row['codon_end'])
        for row in rows
    ]


def load_formula_rule_hits(results_conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """
    Load persisted formula-rule hits for a run.

    :param results_conn: open results DB connection
    :param run_id: id of the run to load formula hits for
    :return: list of formula_rule_hit row dicts ordered by insertion
    """
    tables = {
        row['name']
        for row in results_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if 'formula_rule_hit' not in tables:
        return []

    rows = results_conn.execute(
        'SELECT id, run_id, hit_json FROM formula_rule_hit WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def reconstruct_annotations(variant_rows: list[dict]) -> list[AnnotatedVariant]:
    """
    Reconstruct AnnotatedVariant objects from stored variant_result rows.

    Rule matches are rebuilt from the stored drug_hits JSON, which contains
    enough information to regenerate the report display without re-running rule matching.

    :param variant_rows: list of variant_result row dicts from the results DB
    :return: list of AnnotatedVariant objects
    """
    annotations = []
    for row in variant_rows:
        drug_hits = json.loads(row.get('drug_hits') or '[]')
        v = VariantCall(
            chrom=row['chrom'],
            pos=row['pos'],
            ref=row['ref'],
            alt=row['alt'],
            allele_freq=row.get('allele_freq') or 0.0,
            depth=row.get('depth') or 0,
        )
        rule_matches = [_rule_from_hit(hit, row.get('feature_name', '')) for hit in drug_hits]
        ann = AnnotatedVariant(
            variant=v,
            feature_name=row.get('feature_name', ''),
            codon_pos=row.get('codon_pos') or 0,
            ref_codon=row.get('ref_codon', ''),
            alt_codon=row.get('alt_codon', ''),
            ref_aa=row.get('ref_aa', ''),
            alt_aa=row.get('alt_aa', ''),
            consequence=row.get('consequence', ''),
            af_bin=row.get('af_bin', ''),
            is_combined_codon_event=bool(row.get('is_combined_codon_event', 0)),
            combined_member_count=row.get('combined_member_count', 1) or 1,
            rule_matches=rule_matches,
        )
        annotations.append(ann)
    return annotations


def reconstruct_formula_rule_hits(
    formula_rows: list[dict],
    annotations: list[AnnotatedVariant],
) -> list[FormulaRuleHit]:
    """
    Reconstruct FormulaRuleHit objects from persisted formula-hit payload rows.

    :param formula_rows: list of formula-hit row dicts
    :param annotations: reconstructed annotations for the same run
    :return: list of FormulaRuleHit objects
    """
    if not formula_rows:
        return []

    hits: list[FormulaRuleHit] = []
    for row in formula_rows:
        payload = json.loads(row.get('hit_json') or '{}')
        rule_set = _rule_set_from_formula_hit(payload)
        matched_variants = _match_formula_variants(payload.get('matched_variants', []), annotations)
        hits.append(
            FormulaRuleHit(
                rule_set=rule_set,
                matched_variants=matched_variants,
                matched_member_ids=list(payload.get('matched_member_ids', [])),
            )
        )
    return hits


def _rule_from_hit(hit: dict, feature_name: str) -> ResistanceRule:
    """Reconstruct a ResistanceRule shell from a stored drug_hits JSON entry."""
    publications = [
        Publication(
            id=0,
            doi=p.get('doi', ''),
            title=p.get('title', ''),
            pubmed_id=p.get('pubmed_id', ''),
            raw_input=p.get('raw_input', ''),
        )
        for p in hit.get('publications', [])
    ]
    return ResistanceRule(
        id=0,
        feature_name=feature_name,
        feature_id=0,
        drug_name=hit.get('drug', ''),
        drug_id=0,
        reference_identifier=hit.get('reference_identifier', ''),
        position=0,
        reference=hit.get('reference', ''),
        mutation=hit.get('mutation', ''),
        phenotype=hit.get('phenotype', ''),
        clinical_phenotype=hit.get('clinical_phenotype', 'unknown'),
        ic50=hit.get('ic50', ''),
        fold_ic50=hit.get('fold_ic50', ''),
        score=hit.get('score', ''),
        publications=publications,
        pubchem_url=hit.get('pubchem_url', ''),
        is_internal_formula_component=is_internal_formula_component_drug_name(hit.get('drug', '')),
    )


def _rule_set_from_formula_hit(payload: dict) -> ResistanceRuleSet:
    """Build a ResistanceRuleSet shell from a persisted formula hit payload."""
    publications = [
        Publication(
            id=0,
            doi=p.get('doi', ''),
            title=p.get('title', ''),
            pubmed_id=p.get('pubmed_id', ''),
            raw_input=p.get('raw_input', ''),
        )
        for p in payload.get('publications', [])
    ]
    rule_set = ResistanceRuleSet(
        id=0,
        drug_name=payload.get('drug', ''),
        drug_id=0,
        phenotype=payload.get('phenotype', ''),
        clinical_phenotype=payload.get('clinical_phenotype', 'unknown'),
        ic50=payload.get('ic50', ''),
        fold_ic50=payload.get('fold_ic50', ''),
        score=payload.get('score', ''),
        source=payload.get('source', ''),
        group_name=payload.get('rule_group', ''),
        pubchem_url=payload.get('pubchem_url', ''),
        logic_expression=payload.get('logic_expression', ''),
        publications=publications,
    )
    for idx, member in enumerate(payload.get('members', []), start=1):
        position_1based = int(member.get('position', 1) or 1)
        rule_set.members.append(
            ResistanceRuleSetMember(
                id=idx,
                rule_set_id=0,
                feature_name=member.get('feature', ''),
                feature_id=0,
                reference_identifier='',
                position=max(0, position_1based - 1),
                reference=member.get('reference', ''),
                mutation=member.get('mutation', ''),
                external_id=member.get('member_id', ''),
            )
        )
    return rule_set


def _match_formula_variants(
    variant_payloads: list[dict],
    annotations: list[AnnotatedVariant],
) -> list[AnnotatedVariant]:
    """Match persisted formula-variant summaries back to reconstructed annotations."""
    by_key: dict[tuple, AnnotatedVariant] = {}
    by_key_weak: dict[tuple, AnnotatedVariant] = {}
    for ann in annotations:
        key = (
            ann.feature_name,
            ann.codon_pos + 1,
            ann.ref_aa,
            ann.alt_aa,
            round(ann.variant.allele_freq, 6),
        )
        weak_key = (ann.feature_name, ann.codon_pos + 1, ann.ref_aa, ann.alt_aa)
        by_key.setdefault(key, ann)
        by_key_weak.setdefault(weak_key, ann)

    matched: list[AnnotatedVariant] = []
    for item in variant_payloads:
        af = float(item.get('allele_freq', 0.0) or 0.0)
        codon_pos_1based = int(item.get('codon_pos', 1) or 1)
        key = (
            item.get('feature', ''),
            codon_pos_1based,
            item.get('ref_aa', ''),
            item.get('alt_aa', ''),
            round(af, 6),
        )
        weak_key = (
            item.get('feature', ''),
            codon_pos_1based,
            item.get('ref_aa', ''),
            item.get('alt_aa', ''),
        )
        ann = by_key.get(key) or by_key_weak.get(weak_key)
        if ann is not None:
            matched.append(ann)
            continue

        # Fall back to a synthetic shell if exact annotation matching is unavailable.
        matched.append(
            AnnotatedVariant(
                variant=VariantCall(chrom='', pos=0, ref='', alt='', allele_freq=af, depth=0),
                feature_name=item.get('feature', ''),
                codon_pos=max(0, codon_pos_1based - 1),
                ref_aa=item.get('ref_aa', ''),
                alt_aa=item.get('alt_aa', ''),
                consequence='',
            )
        )
    return matched


def save_classification(
    results_conn: sqlite3.Connection,
    run_id: int,
    *,
    drug: str = '',
    phenotype: str = 'unknown',
    clinical_phenotype: str = 'unknown',
    ic50: str = '',
    fold_ic50: str = '',
    note: str = '',
    source: str = '',
) -> int:
    """
    Save one manual sample classification row per stored run.

    If a classification already exists for the run, it is updated in place and
    any legacy duplicate rows are removed.

    :param results_conn: open results DB connection
    :param run_id: id of the run to classify
    :param drug: optional drug name this classification applies to
    :param phenotype: clinical resistance phenotype
    :param clinical_phenotype: externally verified clinical phenotype
    :param ic50: IC50 value string
    :param fold_ic50: fold-IC50 value string
    :param note: free-text note
    :param source: source or reference for this classification
    :return: id of the stored classification row
    """
    existing = results_conn.execute(
        'SELECT id FROM sample_classification WHERE run_id = ? ORDER BY id LIMIT 1',
        (run_id,),
    ).fetchone()
    if existing is None:
        cursor = results_conn.execute(
            'INSERT INTO sample_classification '
            '(run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source),
        )
        row_id = cursor.lastrowid  # type: ignore[assignment]
    else:
        row_id = int(existing['id'])
        results_conn.execute(
            'UPDATE sample_classification '
            'SET drug = ?, phenotype = ?, clinical_phenotype = ?, ic50 = ?, fold_ic50 = ?, '
            'note = ?, source = ?, created_at = datetime(\'now\') '
            'WHERE id = ?',
            (drug, phenotype, clinical_phenotype, ic50, fold_ic50, note, source, row_id),
        )
        results_conn.execute(
            'DELETE FROM sample_classification WHERE run_id = ? AND id != ?',
            (run_id, row_id),
        )
    results_conn.commit()
    return int(row_id)


def load_classifications(
    results_conn: sqlite3.Connection,
    run_id: int,
) -> list[dict]:
    """
    Load all manual sample classifications for a run.

    :param results_conn: open results DB connection
    :param run_id: id of the run
    :return: list of classification row dicts ordered by id
    """
    tables = {
        row['name']
        for row in results_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if 'sample_classification' not in tables:
        return []

    rows = results_conn.execute(
        'SELECT id, run_id, drug, phenotype, clinical_phenotype, ic50, fold_ic50, '
        'note, source, created_at '
        'FROM sample_classification WHERE run_id = ? ORDER BY id',
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_run_from_json(
    json_path: Path,
) -> tuple[dict, list[dict], list[CoverageGap], list[dict], list[dict]]:
    """
    Load a stored run payload from a JSON export.

    :param json_path: path to one `*.results.json` export file
    :return: run metadata, variant rows, coverage gaps, formula rows, classification rows
    :raises ValueError: if JSON is malformed or misses required keys
    """
    payload = _read_json_payload(json_path)
    run_dict = _require_dict(payload, 'run')
    variant_rows = _require_list_of_dicts(payload, 'variant_result')
    coverage_rows = _require_list_of_dicts(payload, 'coverage_gap')
    if 'formula_rule_hit' not in payload:
        raise ValueError("Invalid results JSON: missing key 'formula_rule_hit'")
    formula_rows = _require_list_of_dicts(payload, 'formula_rule_hit')
    classification_rows = _require_list_of_dicts(payload, 'sample_classification')

    required_run_keys = {
        'project_name',
        'reference_name',
        'sample_name',
        'vcf_path',
        'total_variants',
        'variants_in_cds',
        'resistance_hits',
        'created_at',
    }
    missing_run_keys = sorted(key for key in required_run_keys if key not in run_dict)
    if missing_run_keys:
        raise ValueError(
            f'Invalid results JSON: missing run field(s): {", ".join(missing_run_keys)}'
        )

    run_dict = dict(run_dict)
    run_dict.setdefault('project_db_path', '')
    run_dict.setdefault('project_fingerprint', '')
    run_dict.setdefault('formula_hits', len(formula_rows))
    run_dict.setdefault('status', 'complete')

    coverage_gaps: list[CoverageGap] = []
    for idx, row in enumerate(coverage_rows, start=1):
        for key in ('feature_name', 'codon_start', 'codon_end'):
            if key not in row:
                raise ValueError(f'Invalid results JSON: coverage_gap[{idx}] missing {key!r}')
        coverage_gaps.append(
            CoverageGap(
                feature_name=str(row.get('feature_name', '')),
                codon_start=int(row.get('codon_start', 0)),
                codon_end=int(row.get('codon_end', 0)),
            )
        )

    for idx, row in enumerate(variant_rows, start=1):
        if 'drug_hits' not in row:
            raise ValueError(f'Invalid results JSON: variant_result[{idx}] missing \'drug_hits\'')

    for idx, row in enumerate(formula_rows, start=1):
        if 'hit_json' not in row:
            raise ValueError(f'Invalid results JSON: formula_rule_hit[{idx}] missing \'hit_json\'')

    return run_dict, variant_rows, coverage_gaps, formula_rows, classification_rows


def validate_project_fingerprint_match(
    *,
    stored_fingerprint: str,
    current_fingerprint: str,
    source_label: str,
) -> None:
    """
    Validate that stored and active project UUID fingerprints match.

    :param stored_fingerprint: UUID persisted with run/json payload
    :param current_fingerprint: UUID of the active project database
    :param source_label: short label for error context
    :raises ValueError: if fingerprints do not match
    """
    if not stored_fingerprint:
        return
    if stored_fingerprint == current_fingerprint:
        return

    raise ValueError(
        f'Project database UUID mismatch for {source_label}. '
        'The provided project database does not match the database used to create this result JSON/run. '
        'Database updates currently do not allow regeneration of reports from older database versions.'
    )


def _read_json_payload(json_path: Path) -> dict:
    """Load and validate a result JSON payload root object."""
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
    """Return one required object field from a JSON payload."""
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f'Invalid results JSON: key {key!r} must be an object')
    return value


def _require_list_of_dicts(payload: dict, key: str) -> list[dict]:
    """Return one required list[object] field from a JSON payload."""
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f'Invalid results JSON: key {key!r} must be an array')
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Invalid results JSON: {key}[{idx}] must be an object')
    return value


def _resolve_run_id(results_conn: sqlite3.Connection, run_identifier: str | int) -> int:
    """Resolve an id-like run identifier to one run row id."""
    token = str(run_identifier).strip().lstrip('#')
    if token == '':
        raise ValueError('Run identifier must not be empty')

    if token.isdigit():
        run_id = int(token)
        exists = results_conn.execute('SELECT 1 FROM run WHERE id = ?', (run_id,)).fetchone()
        if exists is None:
            raise ValueError(f'No run found with id {token}')
        return run_id

    # Fallback: allow string-prefix matching on textual run ids for interactive workflows.
    rows = results_conn.execute(
        'SELECT id FROM run WHERE CAST(id AS TEXT) LIKE ? ORDER BY id',
        (f'{token}%',),
    ).fetchall()
    if not rows:
        raise ValueError(f'No run found for identifier {run_identifier!r}')
    if len(rows) > 1:
        raise ValueError(f'Ambiguous run identifier {run_identifier!r} matched {len(rows)} runs')
    return int(rows[0]['id'])
