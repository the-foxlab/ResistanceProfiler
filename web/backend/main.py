"""FastAPI application factory, session middleware, SPA mount, and uvicorn entrypoint."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import Headers, MutableHeaders
from starlette.formparsers import MultiPartException
from starlette.requests import cookie_parser
from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

from web.backend.artifact_kinds import ARTIFACT_KINDS
from web.backend.background import (
    resolve_maintained_db_update_interval,
    start_maintained_db_update_thread,
    start_ttl_sweep_thread,
)
from web.backend.config import WEB_BACKEND_CONFIG, WEB_ENV, parse_int_env
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
    SessionBackendError,
    resolve_or_create_session,
    set_session_cookie_header,
)
from web.backend.startup_config import (
    StartupConfig,
    load_startup_config,
    resolve_maintained_bootstrap_enabled,
)

logger = logging.getLogger(__name__)

_SPA_RESERVED_EXACT = ('/api', '/docs', '/redoc', '/openapi.json')
_SPA_RESERVED_PREFIXES = ('/api/', '/docs/', '/redoc/')
_STATELESS_API_EXACT = frozenset({'/api/health', '/api/readiness'})
_STATELESS_API_PREFIXES = ('/api/ui/', '/api/branding/')
_MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

_MAX_MULTIPART_FRAMING_BYTES = 64 * 1024
_MAX_PART_HEADER_BYTES = 8 * 1024
_MAX_PART_HEADERS = 16

_UPLOAD_LIMITS_BY_PATH: dict[str, int] = {
    '/api/upload/fasta': WEB_BACKEND_CONFIG.defaults.upload_max_fasta_size,
    '/api/upload/vcf': WEB_BACKEND_CONFIG.defaults.upload_max_vcf_size,
    '/api/upload/bam': WEB_BACKEND_CONFIG.defaults.upload_max_bam_size,
    '/api/upload/json': WEB_BACKEND_CONFIG.defaults.upload_max_json_size,
}

_SPA_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://pubchem.ncbi.nlm.nih.gov "
    "https://uni-freiburg.de https://*.uni-freiburg.de https://www.uniklinik-freiburg.de; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_LEGAL_CSP = (
    "default-src 'none'; "
    "script-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)
_REPORT_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src 'self' data: blob: https://pubchem.ncbi.nlm.nih.gov; "
    "font-src 'self' data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'self'"
)
_API_CSP = "default-src 'none'; frame-ancestors 'none'"


class _UploadAdmissionRejected(MultiPartException, HTTPException):
    """Raised during streaming upload admission so Starlette cleans spool files and returns 4xx."""

    def __init__(self, status_code: int, detail: str) -> None:
        MultiPartException.__init__(self, detail)
        HTTPException.__init__(self, status_code=status_code, detail=detail)


def _normalize_origin_parts(scheme: str, netloc: str) -> str | None:
    scheme_norm = scheme.strip().lower()
    if scheme_norm not in {'http', 'https'} or not netloc.strip():
        return None
    try:
        parsed = urlsplit(f'{scheme_norm}://{netloc.strip()}')
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    host_norm = f'[{hostname.lower()}]' if ':' in hostname else hostname.lower()
    default_port = 80 if scheme_norm == 'http' else 443
    if port is None or port == default_port:
        return f'{scheme_norm}://{host_norm}'
    return f'{scheme_norm}://{host_norm}:{port}'


def _normalize_origin_url(raw: str, *, allow_path: bool = False) -> str | None:
    stripped = raw.strip()
    if not stripped or any(ch.isspace() for ch in stripped):
        return None
    try:
        parsed = urlsplit(stripped)
    except ValueError:
        return None
    if not allow_path and (parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        return None
    return _normalize_origin_parts(parsed.scheme, parsed.netloc)


class UploadAdmissionMiddleware:
    """Pre-parse streaming multipart admission guard for ``POST /api/upload/*``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or str(scope.get('method', '')).upper() != 'POST':
            await self.app(scope, receive, send)
            return

        path = str(scope.get('path', ''))
        max_file_bytes = _UPLOAD_LIMITS_BY_PATH.get(path)
        if max_file_bytes is None:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        media_type, params = parse_options_header(headers.get('content-type', '').encode('latin-1'))
        boundary = params.get(b'boundary')
        if media_type.lower() != b'multipart/form-data' or not boundary:
            await self._send_error(scope, receive, send, 400, 'Expected multipart/form-data upload with boundary.')
            return

        declared_content_length: int | None = None
        content_length_values = headers.getlist('content-length')
        if len(content_length_values) > 1:
            await self._send_error(scope, receive, send, 400, 'Conflicting Content-Length headers.')
            return
        if len(content_length_values) == 1:
            try:
                declared_content_length = int(content_length_values[0].strip())
            except ValueError:
                await self._send_error(scope, receive, send, 400, 'Invalid Content-Length header.')
                return
            if declared_content_length < 0:
                await self._send_error(scope, receive, send, 400, 'Invalid Content-Length header.')
                return
            if declared_content_length > max_file_bytes + _MAX_MULTIPART_FRAMING_BYTES:
                await self._send_error(scope, receive, send, 413, 'Upload exceeds maximum allowed size.')
                return

        part_count = 0
        file_part_seen = False
        file_bytes = 0
        total_body_bytes = 0
        current_header_bytes = 0
        current_header_count = 0
        current_header_name = bytearray()
        current_header_value = bytearray()
        content_disposition: bytes | None = None
        rejected_exc: _UploadAdmissionRejected | None = None

        def _reject(status_code: int, detail: str) -> _UploadAdmissionRejected:
            nonlocal rejected_exc
            rejected_exc = _UploadAdmissionRejected(status_code, detail)
            return rejected_exc

        def on_part_begin() -> None:
            nonlocal part_count, current_header_bytes, current_header_count, content_disposition
            part_count += 1
            if part_count > 1:
                raise _reject(400, 'Upload must contain exactly one file part.')
            current_header_bytes = 0
            current_header_count = 0
            current_header_name.clear()
            current_header_value.clear()
            content_disposition = None

        def on_header_field(data: bytes, start: int, end: int) -> None:
            nonlocal current_header_bytes
            current_header_bytes += end - start
            if current_header_bytes > _MAX_PART_HEADER_BYTES:
                raise _reject(400, 'Multipart part headers exceed maximum allowed size.')
            current_header_name.extend(data[start:end])

        def on_header_value(data: bytes, start: int, end: int) -> None:
            nonlocal current_header_bytes
            current_header_bytes += end - start
            if current_header_bytes > _MAX_PART_HEADER_BYTES:
                raise _reject(400, 'Multipart part headers exceed maximum allowed size.')
            current_header_value.extend(data[start:end])

        def on_header_end() -> None:
            nonlocal current_header_count, content_disposition
            current_header_count += 1
            if current_header_count > _MAX_PART_HEADERS:
                raise _reject(400, 'Too many multipart part headers.')
            if bytes(current_header_name).strip().lower() == b'content-disposition':
                content_disposition = bytes(current_header_value)
            current_header_name.clear()
            current_header_value.clear()

        def on_headers_finished() -> None:
            nonlocal file_part_seen
            if content_disposition is None:
                raise _reject(400, 'Missing Content-Disposition header in multipart upload.')
            disp_type, disp_params = parse_options_header(content_disposition)
            if (
                disp_type.lower() != b'form-data'
                or disp_params.get(b'name') != b'file'
                or b'filename' not in disp_params
            ):
                raise _reject(400, 'Unexpected multipart field; expected a single "file" upload.')
            file_part_seen = True

        def on_part_data(data: bytes, start: int, end: int) -> None:
            nonlocal file_bytes
            del data
            file_bytes += end - start
            if file_bytes > max_file_bytes:
                raise _reject(413, 'Upload exceeds maximum allowed size.')

        callbacks: MultipartCallbacks = {
            'on_part_begin': on_part_begin,
            'on_header_field': on_header_field,
            'on_header_value': on_header_value,
            'on_header_end': on_header_end,
            'on_headers_finished': on_headers_finished,
            'on_part_data': on_part_data,
        }
        parser = MultipartParser(boundary, callbacks)

        async def guarded_receive() -> Message:
            nonlocal total_body_bytes
            message = await receive()
            msg_type = message.get('type')
            if msg_type == 'http.disconnect':
                raise _reject(400, 'Upload connection closed.')
            if msg_type == 'http.request':
                chunk = message.get('body', b'')
                total_body_bytes += len(chunk)
                if declared_content_length is not None and total_body_bytes > declared_content_length:
                    raise _reject(400, 'Request body exceeds declared Content-Length.')
                if total_body_bytes > max_file_bytes + _MAX_MULTIPART_FRAMING_BYTES:
                    raise _reject(413, 'Upload exceeds maximum allowed size.')
                try:
                    parser.write(chunk)
                except _UploadAdmissionRejected:
                    raise
                except FormParserError as exc:
                    raise _reject(400, 'Malformed multipart upload.') from exc
                if (total_body_bytes - file_bytes) > _MAX_MULTIPART_FRAMING_BYTES:
                    raise _reject(413, 'Multipart framing overhead exceeds limit.')
                if not message.get('more_body', False):
                    try:
                        parser.finalize()
                    except _UploadAdmissionRejected:
                        raise
                    except FormParserError as exc:
                        raise _reject(400, 'Malformed multipart upload.') from exc
                    if not file_part_seen:
                        raise _reject(400, 'Missing file part in multipart upload.')
            return message

        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected_exc is not None:
                if message['type'] == 'http.response.body' and not message.get('more_body', False):
                    response_started = True
                    await self._send_error(scope, receive, send, rejected_exc.status_code, rejected_exc.detail)
                return
            if message['type'] == 'http.response.start':
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except _UploadAdmissionRejected as exc:
            if not response_started:
                await self._send_error(scope, receive, send, exc.status_code, exc.detail)

    @staticmethod
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: Any,
    ) -> None:
        resp = JSONResponse(status_code=status_code, content={'detail': detail})
        await resp(scope, receive, send)


class SessionMiddleware:
    """Pure ASGI session middleware resolving/minting cookies only on stateful API routes."""

    def __init__(self, app: ASGIApp, *, deployment_mode: str) -> None:
        self.app = app
        self.deployment_mode = deployment_mode

    @staticmethod
    def _is_session_exempt(scope: Scope) -> bool:
        if str(scope.get('method', '')).upper() == 'OPTIONS':
            return True
        path = str(scope.get('path', ''))
        if not path.startswith('/api/'):
            return True
        if path in _STATELESS_API_EXACT or path.startswith(_STATELESS_API_PREFIXES):
            return True
        app = scope.get('app')
        if app is not None and hasattr(app, 'router'):
            for route in app.router.routes:
                if isinstance(route, Mount):
                    continue
                match, _ = route.matches(scope)
                if match == Match.FULL:
                    return False
            return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or self._is_session_exempt(scope):
            await self.app(scope, receive, send)
            return

        cookie_header = Headers(scope=scope).get('cookie', '')
        cookie_value = cookie_parser(cookie_header).get(SESSION_COOKIE_NAME) if cookie_header else None
        try:
            session = await run_in_threadpool(
                resolve_or_create_session,
                cookie_value,
                deployment_mode=self.deployment_mode,
            )
        except SessionBackendError:
            resp = JSONResponse(status_code=503, content={'detail': 'Session store unavailable.'})
            await resp(scope, receive, send)
            return

        state = scope.setdefault('state', {})
        state['session'] = session
        cookie_line = set_session_cookie_header(session, self.deployment_mode)

        async def send_with_session_cookie(message: Message) -> None:
            if message['type'] == 'http.response.start':
                MutableHeaders(scope=message).append('set-cookie', cookie_line)
            await send(message)

        await self.app(scope, receive, send_with_session_cookie)


class SecurityHeadersAndCsrfMiddleware:
    """Enforce online-mode Origin/Sec-Fetch-Site checks and attach HTTP security headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        deployment_mode: str,
        allowed_cors_origins: list[str],
        docs_enabled: bool,
    ) -> None:
        self.app = app
        self.deployment_mode = deployment_mode
        self.docs_enabled = docs_enabled
        self.normalized_cors_origins: frozenset[str] = frozenset(
            norm
            for origin in allowed_cors_origins
            if origin.strip() != '*' and (norm := _normalize_origin_url(origin)) is not None
        )

    def _is_csrf_rejected(self, scope: Scope) -> bool:
        if self.deployment_mode == 'local':
            return False
        if str(scope.get('method', '')).upper() not in _MUTATING_METHODS:
            return False

        headers = Headers(scope=scope)
        sec_fetch_site = headers.get('sec-fetch-site', '').strip().lower()
        if sec_fetch_site and sec_fetch_site != 'same-origin':
            return True

        allowed_origins = set(self.normalized_cors_origins)
        req_origin = _normalize_origin_parts(str(scope.get('scheme', 'http')), headers.get('host', ''))
        if req_origin is not None:
            allowed_origins.add(req_origin)

        origin_hdr = headers.get('origin', '').strip()
        if origin_hdr:
            norm_origin = _normalize_origin_url(origin_hdr)
            return norm_origin is None or norm_origin not in allowed_origins

        referer_hdr = headers.get('referer', '').strip()
        if referer_hdr:
            norm_referer = _normalize_origin_url(referer_hdr, allow_path=True)
            return norm_referer is None or norm_referer not in allowed_origins

        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        if self._is_csrf_rejected(scope):
            resp = JSONResponse(status_code=403, content={'detail': 'Cross-site request blocked.'})
            await resp(scope, receive, send)
            return

        path = str(scope.get('path', ''))

        async def send_with_security_headers(message: Message) -> None:
            if message['type'] == 'http.response.start':
                res_headers = MutableHeaders(scope=message)
                res_headers['x-content-type-options'] = 'nosniff'
                res_headers['referrer-policy'] = 'no-referrer'
                if path == '/api/report':
                    if 'x-frame-options' not in res_headers:
                        res_headers['x-frame-options'] = 'SAMEORIGIN'
                    if 'content-security-policy' not in res_headers:
                        res_headers['content-security-policy'] = _REPORT_CSP
                elif path == '/legal':
                    if 'x-frame-options' not in res_headers:
                        res_headers['x-frame-options'] = 'DENY'
                    if 'content-security-policy' not in res_headers:
                        res_headers['content-security-policy'] = _LEGAL_CSP
                elif self.docs_enabled and (
                    path in {'/docs', '/redoc', '/openapi.json'} or path.startswith(('/docs/', '/redoc/'))
                ):
                    if 'x-frame-options' not in res_headers:
                        res_headers['x-frame-options'] = 'DENY'
                elif path.startswith('/api/'):
                    if 'x-frame-options' not in res_headers:
                        res_headers['x-frame-options'] = 'DENY'
                    if 'content-security-policy' not in res_headers:
                        res_headers['content-security-policy'] = _API_CSP
                else:
                    if 'x-frame-options' not in res_headers:
                        res_headers['x-frame-options'] = 'DENY'
                    if 'content-security-policy' not in res_headers:
                        res_headers['content-security-policy'] = _SPA_CSP
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start background TTL-sweep and optional maintained-DB update threads with graceful shutdown."""
    config: StartupConfig = app.state.startup_config
    stop_event = threading.Event()
    threads: list[threading.Thread] = [
        start_ttl_sweep_thread(config.results_dir, config.uploads_dir, stop_event=stop_event),
    ]
    if resolve_maintained_bootstrap_enabled():
        update_thread = start_maintained_db_update_thread(
            config.project_databases_dir,
            resolve_maintained_db_update_interval(),
            app.state,
            stop_event=stop_event,
        )
        if update_thread is not None:
            threads.append(update_thread)
    try:
        yield
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5.0)


async def _handle_session_backend_error(request: Request, exc: SessionBackendError) -> JSONResponse:
    del request, exc
    return JSONResponse(status_code=503, content={'detail': 'Session store unavailable.'})


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    del request
    logger.exception('Unhandled server error: %s', exc)
    return JSONResponse(status_code=500, content={'detail': 'Internal server error.'})


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
    app.add_exception_handler(SessionBackendError, _handle_session_backend_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)

    upload_rate_limit = resolve_upload_rate_limit()
    api_rate_limit = resolve_api_rate_limit()
    max_batch_size = resolve_max_batch_size()
    artifact_bundle_max_artifacts = len(ARTIFACT_KINDS) * max_batch_size

    cors_origins = resolve_cors_origins()
    proxy_headers, forwarded_allow_ips = resolve_proxy_settings()

    # Starlette add_middleware wraps in LIFO order: register innermost first, CORSMiddleware last (outermost).
    app.add_middleware(UploadAdmissionMiddleware)
    app.add_middleware(SessionMiddleware, deployment_mode=config.deployment_mode)
    app.add_middleware(
        SecurityHeadersAndCsrfMiddleware,
        deployment_mode=config.deployment_mode,
        allowed_cors_origins=cors_origins,
        docs_enabled=docs_enabled,
    )
    if proxy_headers:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=forwarded_allow_ips)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
        allow_headers=['Content-Type', 'Accept'],
    )

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
            allowed_roots=(config.results_dir,),
            artifact_bundle_max_artifacts=artifact_bundle_max_artifacts,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            project_databases_dir=config.project_databases_dir,
        ),
        build_session_router(
            uploads_dir=config.uploads_dir,
            results_dir=config.results_dir,
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
        normalized_base = base_path.rstrip('/')
        theme_init_path = frontend_dist / 'theme-init.js'
        if theme_init_path.is_file():
            @app.get(f'{normalized_base}/theme-init.js', include_in_schema=False)
            def serve_theme_init() -> FileResponse:
                return FileResponse(theme_init_path, media_type='application/javascript')

        assets_dir = frontend_dist / 'assets'
        if assets_dir.is_dir():
            app.mount(
                f'{normalized_base}/assets',
                StaticFiles(directory=str(assets_dir)),
                name='frontend-assets',
            )
        app.mount(base_path, _build_spa_fallback_app(frontend_dist), name='frontend-spa-fallback')

    return app


def _frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'frontend' / 'dist'


def _is_reserved_spa_path(path: str) -> bool:
    return path in _SPA_RESERVED_EXACT or path.startswith(_SPA_RESERVED_PREFIXES)


def _build_spa_fallback_app(frontend_dist: Path) -> FastAPI:
    """Serve ``index.html`` for SPA routes while keeping backend-reserved prefixes 404."""
    async def spa_fallback(request: Request) -> FileResponse:
        if _is_reserved_spa_path(request.url.path):
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(frontend_dist / 'index.html')

    fallback = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    fallback.add_route('/{path:path}', spa_fallback, methods=['GET'])
    return fallback


def _resolve_web_port() -> int:
    """Resolve and validate ``RESPRO_WEB_PORT`` within ``1..65535``."""
    port = parse_int_env(
        WEB_ENV.port,
        WEB_BACKEND_CONFIG.defaults.web_port,
        min_value=1,
    )
    if port > 65535:
        raise ValueError(f'{WEB_ENV.port} must be between 1 and 65535.')
    return port


def run() -> None:
    """Run the web API with uvicorn."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(name)s: %(message)s')
    host = os.getenv(WEB_ENV.host, WEB_BACKEND_CONFIG.defaults.web_host)
    port = _resolve_web_port()
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
