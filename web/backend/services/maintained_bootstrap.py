"""Startup bootstrap and auto-update for maintained project databases."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from respro.cli.init import init_project
from respro.db.rules_queries import get_project_summary_for_display
from respro.db.schema import open_project_db
from respro.io.maintained_db import (
    download_database_files,
    list_maintained_databases,
    list_maintained_databases_with_checksums,
)

logger = logging.getLogger(__name__)


def check_and_update_maintained_databases(project_databases_dir: Path) -> None:
    """
    Refresh maintained databases whose ``tsv_checksum`` changed in the companion manifest.

    Only databases whose filename (sans ``.db``) appear in the maintained manifest are
    considered; user-created databases are never touched. Each database is rebuilt
    independently into ``<name>.db.tmp`` and atomically swapped via ``os.replace``; a
    failure in one database or in the manifest fetch is logged and does not abort the
    others.

    :param project_databases_dir: directory containing project ``.db`` files
    """
    try:
        remote_entries = list_maintained_databases_with_checksums()
    except Exception:  # noqa: BLE001 — manifest unavailable: nothing to update, fail-soft
        logger.exception('Maintained database manifest fetch failed — skipping update check')
        return

    for name, remote_checksum in remote_entries:
        try:
            _maybe_update_one_database(project_databases_dir, name, remote_checksum)
        except Exception:  # noqa: BLE001 — per-DB failure must not abort the others
            logger.exception('Maintained database update failed for %s — keeping existing copy', name)


def _maybe_update_one_database(
    project_databases_dir: Path,
    name: str,
    remote_checksum: str,
) -> None:
    """Check one maintained database against the manifest and rebuild it on mismatch."""
    db_path = project_databases_dir / f'{name}.db'
    if not db_path.is_file():
        # Bootstrap owns creating missing databases; the update path only refreshes existing ones.
        return

    if not remote_checksum:
        logger.warning(
            'Maintained database %s: manifest lacks a tsv_checksum — skipping update check', name
        )
        return

    local_checksum = _read_local_tsv_checksum(db_path)
    if local_checksum == remote_checksum:
        logger.debug('Maintained database %s is up to date (checksum unchanged)', name)
        return

    logger.info(
        'Maintained database %s: checksum changed (%s -> %s) — refreshing',
        name,
        local_checksum,
        remote_checksum,
    )
    _rebuild_database_files(name, db_path)


def bootstrap_missing_maintained_databases(project_databases_dir: Path) -> None:
    """
    Download and initialize all maintained databases that are not yet present.

    Startup intentionally never overwrites existing ``.db`` files. Existing files are
    treated as user-managed artifacts and skipped; an incomplete stub (present but not a
    valid project DB) is removed and re-initialized.
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
        _rebuild_database_files(database_name, db_path)
        logger.info(f'Successfully initialized {database_name} at {db_path}')


def _rebuild_database_files(name: str, dest_db_path: Path) -> None:
    """
    Download files for a maintained database and (re)initialize it atomically.

    Builds into ``<dest>.db.tmp`` then swaps via :func:`os.replace` so a failed init
    never leaves a stub ``.db`` file. Any exception cleans up the temp file and
    re-raises.

    :param name: maintained database source name
    :param dest_db_path: final destination ``.db`` path
    """
    with tempfile.TemporaryDirectory() as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        files = download_database_files(name, tmp_dir)
        genbank_paths: list[Path] = files['genbank']  # type: ignore[assignment]
        if not genbank_paths:
            raise RuntimeError(
                f'No GenBank records could be fetched for maintained database {name!r}.'
            )

        tmp_db = dest_db_path.with_suffix('.db.tmp')
        try:
            init_project(
                db_path=tmp_db,
                name=name,
                genbank_paths=genbank_paths,
                rules_tsv=files['rules'],  # type: ignore[arg-type]
                formula_rules_tsv=files['formula_rules'],  # type: ignore[arg-type]
                metadata_json=files['metadata'],  # type: ignore[arg-type]
                overwrite=False,
                additional_info=True,
            )
            os.replace(tmp_db, dest_db_path)
        except Exception:
            tmp_db.unlink(missing_ok=True)
            raise


def _read_local_tsv_checksum(db_path: Path) -> str:
    """
    Return the stored ``metadata_tsv_checksum`` for a project database.

    Returns an empty string when the column is unset or the project row is absent.
    Unexpected database errors propagate (fail-fast); only a missing column or empty
    value is treated as ``''``.

    :param db_path: path to a project database
    :return: stored tsv checksum, or ``''`` if unset
    """
    conn = open_project_db(db_path)
    try:
        try:
            row = conn.execute(
                'SELECT metadata_tsv_checksum FROM project ORDER BY id LIMIT 1'
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such column' in str(exc).lower():
                return ''
            raise
    finally:
        conn.close()

    if row is None:
        return ''
    return str(row['metadata_tsv_checksum'] or '')


def _is_valid_project_db(db_path: Path) -> bool:
    """Return True if the database can be opened and has project metadata."""
    try:
        conn = open_project_db(db_path)
        try:
            get_project_summary_for_display(conn)
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.debug('Project DB validation failed for %s: %s', db_path, exc)
        return False
