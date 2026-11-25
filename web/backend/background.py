"""Background daemon threads for TTL file sweeping and maintained-DB auto-updates."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, parse_int_env
from web.backend.project_dbs import build_project_db_uuid_index
from web.backend.services.maintained_bootstrap import check_and_update_maintained_databases

logger = logging.getLogger(__name__)


def _sweep_directory(directory: Path, *, now: float, ttl_seconds: int) -> int:
    """Delete files inside *directory* older than *ttl_seconds*; return count removed."""
    if not directory.is_dir():
        return 0
    deleted = 0
    try:
        items = list(directory.rglob('*'))
    except OSError as exc:
        logger.debug('Error scanning directory %s: %s', directory, exc)
        return 0

    for item in items:
        if not item.is_file():
            continue
        try:
            if now - item.stat().st_mtime <= ttl_seconds:
                continue
            item.unlink(missing_ok=True)
            logger.debug('Deleted expired file: %s', item)
            deleted += 1
        except OSError as exc:
            logger.debug('Error processing file %s: %s', item, exc)
    return deleted


def _sweep_expired_files(results_dir: Path, uploads_dir: Path, ttl_seconds: int) -> None:
    """Delete expired files from *results_dir* and *uploads_dir*."""
    now = time.time()
    total_deleted = sum(_sweep_directory(d, now=now, ttl_seconds=ttl_seconds) for d in (results_dir, uploads_dir))
    if total_deleted > 0:
        logger.info('TTL sweep deleted %d expired files from results and uploads directories', total_deleted)


def _resolve_sweep_ttl_seconds() -> int:
    """Resolve ``RESPRO_WEB_RESULT_TTL`` (>= 1), logging a warning fallback on invalid values."""
    default_ttl = WEB_BACKEND_CONFIG.defaults.result_ttl_seconds
    try:
        return parse_int_env(WEB_ENV.result_ttl, default_ttl, min_value=1)
    except ValueError as exc:
        logger.warning('Invalid %s (%s); falling back to default %s seconds', WEB_ENV.result_ttl, exc, default_ttl)
        return default_ttl


def start_ttl_sweep_thread(
    results_dir: Path,
    uploads_dir: Path,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Start a daemon thread that periodically deletes expired uploads and results."""
    stop = stop_event if stop_event is not None else threading.Event()
    thread = threading.Thread(
        target=_ttl_sweep_loop,
        args=(results_dir, uploads_dir, WEB_BACKEND_CONFIG.defaults.sweep_frequency_seconds, stop),
        daemon=True,
    )
    thread.start()
    return thread


def _ttl_sweep_loop(
    results_dir: Path,
    uploads_dir: Path,
    sweep_frequency_seconds: float,
    stop_event: threading.Event | None = None,
) -> None:
    stop = stop_event if stop_event is not None else threading.Event()
    while not stop.is_set():
        try:
            ttl_seconds = _resolve_sweep_ttl_seconds()
            _sweep_expired_files(results_dir, uploads_dir, ttl_seconds)
        except Exception as exc:
            logger.debug('Error in TTL sweep: %s', exc)
        if stop.wait(sweep_frequency_seconds):
            break


def resolve_maintained_db_update_interval() -> int:
    """Resolve the maintained-DB update interval, falling back to default on invalid values."""
    default = WEB_BACKEND_CONFIG.defaults.maintained_db_update_interval_seconds
    raw = os.getenv(WEB_ENV.maintained_db_update_interval, str(default)).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning('%s must be an integer; falling back to default %s', WEB_ENV.maintained_db_update_interval, default)
        return default
    if parsed < 0:
        logger.warning('%s must be >= 0; falling back to default %s', WEB_ENV.maintained_db_update_interval, default)
        return default
    return parsed


def start_maintained_db_update_thread(
    project_databases_dir: Path,
    interval_seconds: float,
    app_state: Any,
    stop_event: threading.Event | None = None,
) -> threading.Thread | None:
    """Start a daemon thread refreshing maintained DBs and UUID index (``0`` disables)."""
    if interval_seconds <= 0:
        logger.debug('Maintained database auto-update thread disabled (interval=0)')
        return None
    stop = stop_event if stop_event is not None else threading.Event()
    thread = threading.Thread(
        target=_maintained_db_update_loop,
        args=(project_databases_dir, interval_seconds, app_state, stop),
        daemon=True,
    )
    thread.start()
    return thread


def _maintained_db_update_loop(
    project_databases_dir: Path,
    interval_seconds: float,
    app_state: Any,
    stop_event: threading.Event | None = None,
) -> None:
    stop = stop_event if stop_event is not None else threading.Event()
    while not stop.wait(interval_seconds):
        try:
            check_and_update_maintained_databases(project_databases_dir)
            new_index = build_project_db_uuid_index(project_databases_dir)
            app_state.project_db_uuid_index = new_index
        except Exception:  # noqa: BLE001 — failed update pass must not kill daemon
            logger.exception('Maintained database weekly update pass failed')
