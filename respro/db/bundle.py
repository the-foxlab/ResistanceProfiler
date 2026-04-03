"""
Project bundle export and import.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile

from respro.db.schema import open_project_db
from respro.io.reference import load_reference_sequence

logger = logging.getLogger(__name__)


def export_bundle(project_db: Path, output_zip: Path) -> Path:
    """
    Package the project database into a portable ZIP bundle.

    The bundle contains:
    - project.db (the SQLite file)
    - manifest.json (metadata)
    - reference sequences extracted as FASTA

    :param project_db: path to project database
    :param output_zip: path to write bundle ZIP to
    :return: path to the created bundle
    """
    conn = open_project_db(project_db)
    try:
        project = conn.execute('SELECT * FROM project LIMIT 1').fetchone()
        if project is None:
            raise ValueError('No project found in the database')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Copy DB
            db_copy = tmp / 'project.db'
            shutil.copy2(project_db, db_copy)

            # Extract references as FASTA
            refs = conn.execute(
                'SELECT id, name, accession, organism, taxonomy FROM reference'
            ).fetchall()
            fasta_path = tmp / 'reference.fasta'
            with open(fasta_path, 'w') as fh:
                for ref in refs:
                    sequence = load_reference_sequence(conn, ref['name'])
                    fh.write(f'>{ref["name"]}\n{sequence}\n')

            # Write manifest
            manifest = {
                'project_name': project['name'],
                'schema_version': project['schema_version'],
                'references': [r['name'] for r in refs],
                'organisms': sorted({r['organism'] for r in refs if r['organism']}),
                'reference_metadata': [
                    {
                        'name': r['name'],
                        'accession': r['accession'],
                        'organism': r['organism'],
                        'taxonomy': r['taxonomy'],
                    }
                    for r in refs
                ],
                'gene_count': conn.execute('SELECT COUNT(*) FROM gene').fetchone()[0],
                'rule_count': conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0],
            }
            manifest_path = tmp / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest, indent=2))

            # Create ZIP
            with ZipFile(output_zip, 'w') as zf:
                zf.write(db_copy, 'project.db')
                zf.write(fasta_path, 'reference.fasta')
                zf.write(manifest_path, 'manifest.json')

        logger.info('Bundle exported to %s', output_zip)
    finally:
        conn.close()

    return output_zip

