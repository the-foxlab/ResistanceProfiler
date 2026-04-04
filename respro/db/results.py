"""
Persistence helpers for profiling results.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from respro.db.models import AnnotatedVariant, ResistanceRule, VariantCall
from respro.report.results_model import ProfilingResult


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

    cursor = results_conn.execute(
        'INSERT INTO run '
        '(project_name, project_db_path, project_fingerprint, reference_name, '
        'sample_name, vcf_path, total_variants, variants_in_cds, '
        'resistance_hits, combo_hits, status) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            result.project_name,
            str(project_db_path),
            fingerprint,
            result.reference_name,
            result.sample_name,
            result.vcf_name,
            result.total_variants,
            result.variants_in_cds,
            result.resistance_hits,
            len(result.combo_hits),
            'complete',
        ),
    )
    run_id = cursor.lastrowid

    for ann in result.annotations:
        v = ann.variant
        results_conn.execute(
            'INSERT INTO variant_result '
            '(run_id, chrom, pos, ref, alt, allele_freq, depth, '
            'gene_name, codon_pos, ref_codon, alt_codon, ref_aa, alt_aa, '
            'consequence, af_bin, rule_match, drug_hits) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                run_id,
                v.chrom,
                v.pos,
                v.ref,
                v.alt,
                v.allele_freq,
                v.depth,
                ann.gene_name,
                ann.codon_pos,
                ann.ref_codon,
                ann.alt_codon,
                ann.ref_aa,
                ann.alt_aa,
                ann.consequence,
                ann.af_bin,
                int(ann.is_resistance_hit),
                json.dumps(ann.drug_hits_json()),
            ),
        )

    results_conn.commit()
    return run_id


def list_runs(results_conn: sqlite3.Connection) -> list[dict]:
    """
    Return a summary list of all stored runs ordered by id.

    :param results_conn: open results DB connection
    :return: list of run summary dicts
    """
    rows = results_conn.execute(
        'SELECT id, sample_name, reference_name, vcf_path, '
        'total_variants, variants_in_cds, resistance_hits, combo_hits, created_at '
        'FROM run ORDER BY id'
    ).fetchall()
    return [dict(row) for row in rows]


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
        rule_matches = [_rule_from_hit(hit, row.get('gene_name', '')) for hit in drug_hits]
        ann = AnnotatedVariant(
            variant=v,
            gene_name=row.get('gene_name', ''),
            codon_pos=row.get('codon_pos') or 0,
            ref_codon=row.get('ref_codon', ''),
            alt_codon=row.get('alt_codon', ''),
            ref_aa=row.get('ref_aa', ''),
            alt_aa=row.get('alt_aa', ''),
            consequence=row.get('consequence', ''),
            af_bin=row.get('af_bin', ''),
            rule_matches=rule_matches,
        )
        annotations.append(ann)
    return annotations


def _rule_from_hit(hit: dict, gene_name: str) -> ResistanceRule:
    """Reconstruct a ResistanceRule shell from a stored drug_hits JSON entry."""
    return ResistanceRule(
        id=0,
        gene_name=gene_name,
        gene_id=0,
        drug_name=hit.get('drug', ''),
        drug_id=0,
        reference_identifier=hit.get('reference_identifier', ''),
        position=0,
        reference=hit.get('reference', ''),
        mutation=hit.get('mutation', ''),
        phenotype=hit.get('phenotype', ''),
        clinical_phenotype=hit.get('clinical_phenotype', 'unknown'),
        ic50=hit.get('ic50', ''),
        publication=hit.get('publication', ''),
        pubchem_url=hit.get('pubchem_url', ''),
    )

