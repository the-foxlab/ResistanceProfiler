"""Startup bootstrap and checksum-based auto-update for maintained project databases."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

from respro.cli.init import init_project
from respro.db.rules_queries import get_project_summary_for_display
from respro.db.schema import open_project_db
from respro.io.maintained_db import (
    download_database_files,
    list_maintained_databases,
    list_maintained_databases_with_checksums,
    validate_maintained_db_name,
)

logger = logging.getLogger(__name__)
_SQLITE_HEADER = b'SQLite format 3\x00'


def _ensure_no_symlinks(db_path: Path, *, root: Path | None = None) -> None:
    """Reject symlinked database targets, sidecars, lockfiles, or escapes outside *root*."""
    resolved_root = (root if root is not None else db_path.parent).resolve()
    candidates = (
        db_path,
        Path(f'{db_path}-wal'),
        Path(f'{db_path}-shm'),
        db_path.with_name(f'.{db_path.name}.lock'),
    )
    for candidate in candidates:
        if candidate.is_symlink():
            raise ValueError(f'Refusing symlinked maintained database path: {candidate.name}')
        if candidate.resolve(strict=False).parent != resolved_root:
            raise ValueError(f'Maintained database path escapes project_databases_dir: {candidate.name}')


def _resolve_confined_db_path(project_databases_dir: Path, name: str) -> Path:
    """Validate *name* as a safe single component and return a symlink-free path inside *project_databases_dir*."""
    validate_maintained_db_name(name)
    if project_databases_dir.is_symlink():
        raise ValueError('Refusing symlinked project_databases_dir')
    root = project_databases_dir.resolve()
    db_path = project_databases_dir / f'{name}.db'
    _ensure_no_symlinks(db_path, root=root)
    return db_path


def _db_path_for_name(project_databases_dir: Path, name: str) -> Path:
    return _resolve_confined_db_path(project_databases_dir, name)


@contextlib.contextmanager
def _database_lock(db_path: Path) -> Iterator[None]:
    """Acquire an exclusive blocking ``fcntl.flock`` on the hidden per-DB lockfile."""
    _ensure_no_symlinks(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.with_name(f'.{db_path.name}.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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
    db_path = _resolve_confined_db_path(project_databases_dir, name)
    if not db_path.is_file():
        return
    if not remote_checksum:
        logger.warning('Maintained database %s: manifest lacks a tsv_checksum — skipping update check', name)
        return

    with _database_lock(db_path):
        db_path = _resolve_confined_db_path(project_databases_dir, name)
        if not db_path.is_file():
            return
        local_checksum = _read_local_tsv_checksum(db_path)
        if local_checksum == remote_checksum:
            logger.debug('Maintained database %s is up to date (checksum unchanged)', name)
            return

        logger.info('Maintained database %s: checksum changed (%s -> %s) — refreshing', name, local_checksum, remote_checksum)
        _rebuild_database_files(name, db_path)


def bootstrap_missing_maintained_databases(project_databases_dir: Path) -> None:
    """Download and initialize missing maintained databases without overwriting valid existing files."""
    project_databases_dir.mkdir(parents=True, exist_ok=True)
    database_names = list_maintained_databases()
    for name in database_names:
        validate_maintained_db_name(name)
    logger.info('Found %d maintained database(s): %s', len(database_names), database_names)

    errors: list[tuple[str, Exception]] = []
    for name in database_names:
        try:
            db_path = _resolve_confined_db_path(project_databases_dir, name)
            with _database_lock(db_path):
                db_path = _resolve_confined_db_path(project_databases_dir, name)
                if db_path.is_file():
                    if _is_valid_project_db(db_path):
                        logger.info('Skipping %s — already exists at %s', name, db_path)
                        continue
                    logger.warning(
                        'Existing database stub at %s is incomplete or invalid — rebuilding before replacement',
                        db_path,
                    )

                logger.info('Bootstrapping %s...', name)
                _rebuild_database_files(name, db_path)
                logger.info('Successfully initialized %s at %s', name, db_path)
        except Exception as exc:  # noqa: BLE001 — isolate per-DB failures so others still bootstrap
            logger.exception('Failed to bootstrap maintained database %s', name)
            errors.append((name, exc))

    if errors:
        raise errors[0][1]


def _is_sqlite_file(db_path: Path) -> bool:
    if not db_path.is_file():
        return False
    try:
        with db_path.open('rb') as fh:
            return fh.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _finalize_delete_journal_mode(db_path: Path) -> None:
    """Checkpoint WAL frames and switch *db_path* to ``DELETE`` journal mode before atomic swap."""
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.commit()


def _unlink_non_symlink_wal_sidecars(db_path: Path) -> None:
    for suffix in ('-wal', '-shm'):
        sidecar = Path(f'{db_path}{suffix}')
        if sidecar.is_symlink():
            raise ValueError(f'Refusing symlinked database sidecar: {sidecar.name}')
        sidecar.unlink(missing_ok=True)


def _rebuild_database_files(name: str, dest_db_path: Path) -> None:
    """Download maintained database files, build into an isolated temp dir, and atomically replace."""
    validate_maintained_db_name(name)
    dest_db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_no_symlinks(dest_db_path)

    with tempfile.TemporaryDirectory() as tmp_dir_raw:
        files = download_database_files(name, Path(tmp_dir_raw))
        genbank_paths: list[Path] = files['genbank']  # type: ignore[assignment]
        if not genbank_paths:
            raise RuntimeError(f'No GenBank records could be fetched for maintained database {name!r}.')

        with tempfile.TemporaryDirectory(
            dir=dest_db_path.parent,
            prefix=f'.{dest_db_path.name}.',
        ) as tmp_build_dir:
            tmp_db = Path(tmp_build_dir) / f'{dest_db_path.name}.tmp'
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
            if _is_sqlite_file(tmp_db):
                _finalize_delete_journal_mode(tmp_db)
            _unlink_non_symlink_wal_sidecars(tmp_db)
            _ensure_no_symlinks(dest_db_path)
            _unlink_non_symlink_wal_sidecars(dest_db_path)
            os.replace(tmp_db, dest_db_path)


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
