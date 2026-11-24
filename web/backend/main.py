"""FastAPI application factory, session middleware, SPA mount, and uvicorn entrypoint."""

from __future__ import annotations

import importlib.metadata
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from web.backend.artifact_kinds import ARTIFACT_KINDS
from web.backend.background import (
    resolve_maintained_db_update_interval,
    start_maintained_db_update_thread,
    start_ttl_sweep_thread,
)
from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV
from web.backend.rate_limit import (
    create_rate_limiter,
    handle_rate_limit_exceeded,
    resolve_api_rate_limit,
    resolve_cors_origins,
    resolve_max_batch_size,
    resolve_proxy_settings,
    resolve_upload_rate_limit,
)
from web.backend.routes.artifacts import build_artifacts_router
from web.backend.routes.catalog import build_catalog_router
from web.backend.routes.health import build_health_router
from web.backend.routes.jobs import build_jobs_router
from web.backend.routes.profile import build_profile_router
from web.backend.routes.regenerate import build_regenerate_router
from web.backend.routes.session import build_session_router
from web.backend.routes.upload import build_upload_router
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    resolve_or_create_session,
    set_session_cookie_header,
)
from web.backend.startup_config import (
    StartupConfig,
    _resolve_maintained_bootstrap_enabled,
    load_startup_config,
)

_SPA_RESERVED_PREFIXES = ('/api', '/docs', '/redoc', '/openapi.json')


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Start background TTL-sweep and optional maintained-DB update daemon threads."""
    config: StartupConfig = app.state.startup_config
    start_ttl_sweep_thread(config.results_dir, config.uploads_dir)
    if _resolve_maintained_bootstrap_enabled():
        start_maintained_db_update_thread(
            config.project_databases_dir,
            resolve_maintained_db_update_interval(),
            app.state,
        )
    yield


def create_app(startup_config: StartupConfig | None = None) -> FastAPI:
    """Create and wire the FastAPI application."""
    version = importlib.metadata.version('respro')
    config = startup_config or load_startup_config()
    docs_enabled = config.deployment_mode != 'online'
    app = FastAPI(
        title='ResistanceProfiler Web API',
        version=version,
        lifespan=lifespan,
        docs_url='/docs' if docs_enabled else None,
        redoc_url='/redoc' if docs_enabled else None,
        openapi_url='/openapi.json' if docs_enabled else None,
    )
    app.state.startup_config = config
    app.state.project_db_uuid_index = dict(config.project_db_uuid_index)

    limiter = create_rate_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)

    upload_rate_limit = resolve_upload_rate_limit()
    api_rate_limit = resolve_api_rate_limit()
    max_batch_size = resolve_max_batch_size()
    artifact_bundle_max_artifacts = len(ARTIFACT_KINDS) * max_batch_size

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolve_cors_origins(),
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    _set_session_cookie_middleware(app, config.deployment_mode)

    branding_dir = Path(__file__).resolve().parents[2] / 'respro' / 'report' / 'static'
    for router in (
        build_health_router(
            config=config,
            sample_limit_per_minute=max_batch_size,
            artifact_bundle_max_artifacts=artifact_bundle_max_artifacts,
            version=version,
        ),
        build_upload_router(
            uploads_dir=config.uploads_dir,
            limiter=limiter,
            upload_rate_limit=upload_rate_limit,
        ),
        build_catalog_router(
            project_databases_dir=config.project_databases_dir,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        ),
        build_profile_router(
            config=config,
            sample_limit_per_minute=max_batch_size,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        ),
        build_jobs_router(limiter=limiter, api_rate_limit=api_rate_limit),
        build_artifacts_router(
            results_dir=config.results_dir,
            branding_dir=branding_dir,
            allowed_roots=config.allowed_roots,
            artifact_bundle_max_artifacts=artifact_bundle_max_artifacts,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        ),
        build_session_router(
            allowed_roots=config.allowed_roots,
            artifact_max_ids=artifact_bundle_max_artifacts,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        ),
        build_regenerate_router(
            config=config,
            sample_limit_per_minute=max_batch_size,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        ),
    ):
        app.include_router(router)

    frontend_dist = _frontend_dist_dir()
    if frontend_dist.is_dir():
        base_path = WEB_BACKEND_CONFIG.defaults.frontend_base_path
        app.mount(
            f'{base_path.rstrip("/")}/assets',
            StaticFiles(directory=str(frontend_dist / 'assets')),
            name='frontend-assets',
        )
        app.mount(base_path, _build_spa_fallback_app(frontend_dist), name='frontend-spa-fallback')

    return app


def _frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'frontend' / 'dist'


def _build_spa_fallback_app(frontend_dist: Path) -> FastAPI:
    """Serve ``index.html`` for SPA routes while keeping backend-reserved prefixes 404."""
    async def spa_fallback(request: Request) -> FileResponse:
        if request.url.path.startswith(_SPA_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(frontend_dist / 'index.html')

    fallback = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    fallback.add_route('/{path:path}', spa_fallback, methods=['GET'])
    return fallback


def _set_session_cookie_middleware(app: FastAPI, deployment_mode: str) -> None:
    """Resolve/create the session cookie on every request (in threadpool to avoid blocking)."""
    @app.middleware('http')
    async def _session_cookie(request: Request, call_next):  # type: ignore[no-untyped-def]
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        session = await run_in_threadpool(resolve_or_create_session, cookie_value)
        request.state.session = session
        response = await call_next(request)
        response.headers['Set-Cookie'] = set_session_cookie_header(session, deployment_mode)
        return response


def run() -> None:
    """Run the web API with uvicorn."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(name)s: %(message)s')
    host = os.getenv(WEB_ENV.host, WEB_BACKEND_CONFIG.defaults.web_host)
    port = int(os.getenv(WEB_ENV.port, str(WEB_BACKEND_CONFIG.defaults.web_port)))
    proxy_headers, forwarded_allow_ips = resolve_proxy_settings()
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        reload=False,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
    )


if __name__ == '__main__':
    run()
