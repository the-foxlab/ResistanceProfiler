"""Regeneration service wrapper for result JSON inputs."""

from __future__ import annotations

from pathlib import Path

from respro.core.rules import load_rules
from respro.db.models import ProfilingResult
from respro.db.results import (
    load_run_from_json,
    reconstruct_annotations,
    reconstruct_combo_rule_hits,
    validate_project_fingerprint_match,
)
from respro.db.results import project_fingerprint as compute_project_fingerprint
from respro.db.schema import open_project_db
from respro.io.reference import load_genes_for_reference
from respro.report.html import export_results


def regenerate_from_json(
    *,
    project_db: Path,
    output_dir: Path,
    json_path: Path,
) -> dict:
    """Regenerate HTML/JSON/TSV artifacts from a previously exported results JSON file."""
    run_dict, variant_rows, coverage_gaps, combo_rows, sample_classifications = load_run_from_json(json_path)

    project_conn = open_project_db(project_db)
    try:
        stored_fp = run_dict.get('project_fingerprint', '')
        if stored_fp:
            current_fp = compute_project_fingerprint(project_conn)
            validate_project_fingerprint_match(
                stored_fingerprint=stored_fp,
                current_fingerprint=current_fp,
                source_label=f'JSON {json_path.name}',
            )

        ref_row = project_conn.execute(
            'SELECT id, organism, length FROM reference WHERE name = ?',
            (run_dict['reference_name'],),
        ).fetchone()

        ref_id = int(ref_row['id']) if ref_row is not None else None
        organism = ref_row['organism'] or '' if ref_row is not None else ''
        reference_length_nt = int(ref_row['length'] or 0) if ref_row is not None else 0

        annotations = reconstruct_annotations(variant_rows)
        combo_hits = reconstruct_combo_rule_hits(combo_rows, annotations)

        result = ProfilingResult(
            project_name=run_dict['project_name'],
            organism=organism,
            reference_name=run_dict['reference_name'],
            reference_length_nt=reference_length_nt,
            sample_name=run_dict.get('sample_name', ''),
            vcf_name=run_dict.get('vcf_path', ''),
            run_timestamp=run_dict.get('created_at', ''),
            total_variants=int(run_dict.get('total_variants', 0) or 0),
            variants_in_cds=int(run_dict.get('variants_in_cds', 0) or 0),
            resistance_hits=int(run_dict.get('resistance_hits', 0) or 0),
            annotations=annotations,
            combo_hits=combo_hits,
            coverage_gaps=coverage_gaps,
            sample_classifications=sample_classifications,
        )

        genes = []
        rules = []
        rule_gene_names: set[str] = set()
        if ref_id is not None:
            genes = load_genes_for_reference(project_conn, ref_id)
            rules = load_rules(project_conn, ref_id)
            rule_gene_names = {rule.gene_name for rule in rules}

        outputs = export_results(
            result,
            output_dir,
            genes=genes,
            rule_gene_names=rule_gene_names,
            project_conn=project_conn,
            rules=rules,
            extra_export_formats={'json', 'tabular'},
            project_db_path=project_db.resolve(),
        )

        return {
            'mode': 'regenerate-json',
            'sample_name': result.sample_name,
            'created_at': result.run_timestamp,
            'reference_name': result.reference_name,
            'query_name': '',
            'report_html_path': str(outputs['html']),
            'report_json_path': str(outputs.get('json', '')),
            'report_tabular_path': str(outputs.get('tabular', '')),
            'resistance_hits': result.resistance_hits,
            'total_variants': result.total_variants,
        }
    finally:
        project_conn.close()
