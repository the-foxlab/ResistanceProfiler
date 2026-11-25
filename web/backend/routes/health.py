"""Health, readiness, UI configuration, legal notice, and contact routes."""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from pathlib import Path

import redis
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
    redact_redis_url,
    sanitize_redis_client_error,
)
from web.backend.models import ApiEnvelope
from web.backend.project_dbs import list_project_db_paths
from web.backend.startup_config import StartupConfig

logger = logging.getLogger(__name__)


def _is_redis_connected() -> bool:
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    client: redis.Redis | None = None
    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        return bool(client.ping())
    except (redis.RedisError, OSError, RuntimeError) as exc:
        logger.debug(
            'Readiness check: Redis unavailable at %s: %s',
            redact_redis_url(redis_url),
            sanitize_redis_client_error(str(exc), redis_url),
        )
        return False
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
            pool = getattr(client, 'connection_pool', None)
            if pool is not None:
                with contextlib.suppress(Exception):
                    pool.disconnect()


def _project_database_catalog_readiness(project_databases_dir: Path) -> tuple[bool, int]:
    try:
        db_paths = list_project_db_paths(project_databases_dir)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        logger.debug('Readiness check: project database catalog unavailable in %s: %s', project_databases_dir, exc)
        return False, 0
    return bool(db_paths), len(db_paths)


def _build_readiness_payload(config: StartupConfig) -> ApiEnvelope:
    """Build readiness diagnostics without exposing filesystem paths or credentials."""
    diagnostics: list[str] = []
    redis_connected = _is_redis_connected()
    if not redis_connected:
        diagnostics.append('redis_unreachable')

    project_db_ready, project_db_count = _project_database_catalog_readiness(config.project_databases_dir)
    if not project_db_ready:
        diagnostics.append('project_database_catalog_unready')

    workspace = {
        'project_databases_dir_ready': config.project_databases_dir.is_dir(),
        'uploads_dir_ready': config.uploads_dir.is_dir(),
        'results_dir_ready': config.results_dir.is_dir(),
    }
    if not all(workspace.values()):
        diagnostics.append('workspace_directories_unready')

    return ApiEnvelope(
        status='ok' if not diagnostics else 'error',
        data={
            'service': WEB_BACKEND_CONFIG.defaults.service_name,
            'redis': {'connected': redis_connected},
            'project_databases': {'ready': project_db_ready, 'count': project_db_count},
            'workspace': workspace,
            'diagnostics': diagnostics,
        },
    )


def build_health_router(
    *,
    config: StartupConfig,
    sample_limit_per_minute: int,
    artifact_bundle_max_artifacts: int,
    version: str,
) -> APIRouter:
    """Build public health, readiness, UI config, legal-notice, and contact routes."""
    router = APIRouter()
    imprint = config.imprint
    contact_email = config.contact_email

    @router.get('/api/health', response_model=ApiEnvelope)
    def health() -> ApiEnvelope:
        return ApiEnvelope(data={'service': WEB_BACKEND_CONFIG.defaults.service_name}, status='ok')

    @router.get('/api/readiness', response_model=ApiEnvelope)
    def readiness() -> JSONResponse | ApiEnvelope:
        payload = _build_readiness_payload(config)
        return payload if payload.status == 'ok' else JSONResponse(status_code=503, content=payload.model_dump())

    @router.get('/api/ui/config', response_model=ApiEnvelope)
    def ui_config() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'batch_max_samples': sample_limit_per_minute,
                'sample_limit_per_minute': sample_limit_per_minute,
                'artifact_bundle_max_artifacts': artifact_bundle_max_artifacts,
                'cli_version': version,
            }
        )

    @router.get('/legal', response_class=HTMLResponse, response_model=None)
    def legal() -> HTMLResponse | RedirectResponse:
        if imprint is None:
            raise HTTPException(status_code=404)
        if imprint.kind == 'url':
            return RedirectResponse(url=imprint.url, status_code=302)  # type: ignore[arg-type]
        return HTMLResponse(content=imprint.html)

    @router.get('/api/ui/legal', response_model=ApiEnvelope)
    def legal_indicator() -> ApiEnvelope:
        if imprint is None:
            return ApiEnvelope(data={'enabled': False, 'kind': 'path'}, status='ok')
        data: dict[str, object] = {'enabled': True, 'kind': imprint.kind}
        if imprint.kind == 'url':
            data['url'] = imprint.url
        return ApiEnvelope(data=data, status='ok')

    @router.get('/api/ui/contact', response_model=ApiEnvelope)
    def contact_indicator() -> ApiEnvelope:
        if contact_email is None:
            return ApiEnvelope(data={'enabled': False, 'email': None}, status='ok')
        return ApiEnvelope(data={'enabled': True, 'email': contact_email.email}, status='ok')

    return router
