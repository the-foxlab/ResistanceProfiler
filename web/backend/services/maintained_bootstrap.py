"""Startup bootstrap for maintained project databases."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from respro.cli.init import init_project
from respro.db.rules_queries import get_project_summary_for_display
from respro.db.schema import open_project_db
from respro.io.maintained_db import download_database_files, list_maintained_databases

logger = logging.getLogger(__name__)


def bootstrap_missing_maintained_databases(project_databases_dir: Path) -> None:
    """
    Download and initialize all maintained databases that are not yet present.

    Startup intentionally never overwrites existing .db files. Existing files are
    treated as user-managed artifacts and skipped.
    """
    database_names = list_maintained_databases()
    logger.info(f'Found {len(database_names)} maintained database(s): {database_names}')
    
    for database_name in database_names:
        db_path = project_databases_dir / f'{database_name}.db'
        if db_path.is_file():
            if _is_valid_project_db(db_path):
                logger.info(f'Skipping {database_name} — already exists at {db_path}')
                continue
            logger.warning(f'Removing incomplete database stub at {db_path} and re-initializing')
            db_path.unlink()

        logger.info(f'Bootstrapping {database_name}...')
        with tempfile.TemporaryDirectory() as tmp_dir_raw:
            tmp_dir = Path(tmp_dir_raw)
            files = download_database_files(database_name, tmp_dir)
            genbank_paths: list[Path] = files['genbank']  # type: ignore[assignment]
            if not genbank_paths:
                raise RuntimeError(
                    f'No GenBank records could be fetched for maintained database {database_name!r}.'
                )

            # Write to a temp path first so a failed init never leaves a stub .db file.
            tmp_db = db_path.with_suffix('.db.tmp')
            try:
                init_project(
                    db_path=tmp_db,
                    name=database_name,
                    genbank_paths=genbank_paths,
                    rules_tsv=files['rules'],  # type: ignore[arg-type]
                    formula_rules_tsv=files['formula_rules'],  # type: ignore[arg-type]
                    metadata_json=files['metadata'],  # type: ignore[arg-type]
                    overwrite=False,
                    additional_info=True,
                )
                tmp_db.rename(db_path)
            except Exception:
                tmp_db.unlink(missing_ok=True)
                raise
            logger.info(f'Successfully initialized {database_name} at {db_path}')


def _is_valid_project_db(db_path: Path) -> bool:
    """Return True if the database can be opened and has project metadata."""
    try:
        conn = open_project_db(db_path)
        try:
            get_project_summary_for_display(conn)
            return True
        finally:
            conn.close()
    except Exception:
        return False
