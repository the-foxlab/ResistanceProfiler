"""Startup bootstrap and checksum-based auto-update for maintained project databases."""

from __future__ import annotations

import contextlib
import logging
import os
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


def _db_path_for_name(project_databases_dir: Path, name: str) -> Path:
    return project_databases_dir / f'{name}.db'


def check_and_update_maintained_databases(project_databases_dir: Path) -> None:
    """Refresh existing maintained databases whose ``tsv_checksum`` changed in the manifest."""
    try:
        remote_entries = list_maintained_databases_with_checksums()
    except Exception:  # noqa: BLE001 — manifest unavailable: fail-soft
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
    db_path = _db_path_for_name(project_databases_dir, name)
    if not db_path.is_file():
        return
    if not remote_checksum:
        logger.warning('Maintained database %s: manifest lacks a tsv_checksum — skipping update check', name)
        return

    local_checksum = _read_local_tsv_checksum(db_path)
    if local_checksum == remote_checksum:
        logger.debug('Maintained database %s is up to date (checksum unchanged)', name)
        return

    logger.info('Maintained database %s: checksum changed (%s -> %s) — refreshing', name, local_checksum, remote_checksum)
    _rebuild_database_files(name, db_path)


def bootstrap_missing_maintained_databases(project_databases_dir: Path) -> None:
    """Download and initialize missing maintained databases without overwriting valid existing files."""
    database_names = list_maintained_databases()
    logger.info('Found %d maintained database(s): %s', len(database_names), database_names)

    for name in database_names:
        db_path = _db_path_for_name(project_databases_dir, name)
        if db_path.is_file():
            if _is_valid_project_db(db_path):
                logger.info('Skipping %s — already exists at %s', name, db_path)
                continue
            logger.warning('Removing incomplete database stub at %s and re-initializing', db_path)
            db_path.unlink()

        logger.info('Bootstrapping %s...', name)
        _rebuild_database_files(name, db_path)
        logger.info('Successfully initialized %s at %s', name, db_path)


def _rebuild_database_files(name: str, dest_db_path: Path) -> None:
    """Download maintained database files, build into ``<dest>.db.tmp``, and atomically replace."""
    with tempfile.TemporaryDirectory() as tmp_dir_raw:
        files = download_database_files(name, Path(tmp_dir_raw))
        genbank_paths: list[Path] = files['genbank']  # type: ignore[assignment]
        if not genbank_paths:
            raise RuntimeError(f'No GenBank records could be fetched for maintained database {name!r}.')

        tmp_db = dest_db_path.with_suffix('.db.tmp')
        try:
            init_project(
                db_path=tmp_db,
                name=name,
                genbank_paths=genbank_paths,
                rules_tsv=files['rules'],  # type: ignore[arg-type]
                formula_rules_tsv=files['formula_rules'],  # type: ignore[arg-type]
                metadata_json=files['metadata'],  # type: ignore[arg-type]
                example_fasta=files['example'],  # type: ignore[arg-type]
                overwrite=False,
                additional_info=True,
            )
            os.replace(tmp_db, dest_db_path)
        except Exception:
            tmp_db.unlink(missing_ok=True)
            raise


def _read_local_tsv_checksum(db_path: Path) -> str:
    """Return the stored ``metadata_tsv_checksum`` for *db_path*, or ``''`` if unset/missing."""
    with contextlib.closing(open_project_db(db_path)) as conn:
        row = conn.execute('SELECT metadata_tsv_checksum FROM project ORDER BY id LIMIT 1').fetchone()
    return str(row['metadata_tsv_checksum'] or '') if row is not None else ''


def _is_valid_project_db(db_path: Path) -> bool:
    """Return True if *db_path* opens cleanly and has project metadata."""
    try:
        with contextlib.closing(open_project_db(db_path)) as conn:
            get_project_summary_for_display(conn)
            return True
    except Exception as exc:
        logger.debug('Project DB validation failed for %s: %s', db_path, exc)
        return False
