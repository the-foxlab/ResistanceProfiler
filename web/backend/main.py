"""FastAPI application for the web backend."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import redis
import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from rq.exceptions import NoSuchJobError
from rq.job import Job
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
)
from web.backend.models import (
    ApiEnvelope,
)
from web.backend.routes.artifacts import build_artifacts_router
from web.backend.routes.catalog import build_catalog_router
from web.backend.routes.health import build_contact_router, build_health_router, build_legal_router
from web.backend.routes.jobs import build_jobs_router
from web.backend.routes.profile import build_profile_router
from web.backend.routes.regenerate import build_regenerate_router
from web.backend.routes.session import build_session_router
from web.backend.routes.upload import build_upload_router
from web.backend.services.maintained_bootstrap import (
    check_and_update_maintained_databases,
)
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    Session,
    resolve_or_create_session,
    set_session_cookie_header,
)
from web.backend.services.upload import save_upload_stream
from web.backend.startup_config import (
    StartupConfig,
    _resolve_maintained_bootstrap_enabled,
    is_path_within_allowed_roots,
    list_project_db_paths,
    load_startup_config,
    refresh_project_db_uuid_index,
    resolve_project_db_path,
    resolve_regenerate_project_db_path,
)

logger = logging.getLogger(__name__)
_SAMPLE_QUOTA_LOCK = threading.Lock()
_SAMPLE_QUOTA_COUNTER: dict[tuple[str, int], int] = {}


def _is_allowed_artifact_path(artifact_path: Path) -> bool:
    """Allow only known artifact file types for downloads."""
    allowed_suffixes = (
        '.report.pdf',
        '.results.json',
        '.report.html',
        '.results.tsv',
    )
    return any(str(artifact_path).endswith(suffix) for suffix in allowed_suffixes)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """FastAPI lifespan context manager."""
    config: StartupConfig = app.state.startup_config
    _start_ttl_sweep_thread(config.results_dir, config.uploads_dir)
    if _resolve_maintained_bootstrap_enabled():
        interval_seconds = _resolve_maintained_db_update_interval()
        _start_maintained_db_update_thread(
            config.project_databases_dir,
            interval_seconds,
            app.state,
        )
    yield


def create_app(startup_config: StartupConfig | None = None) -> FastAPI:
    """Create the FastAPI app instance."""
    version = importlib.metadata.version('respro')
    config = startup_config or load_startup_config()
    # Disable API docs (Swagger UI, ReDoc, OpenAPI schema) outside local mode so a
    # publicly reachable deployment does not advertise its surface area.
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
    # Seed a mutable UUID->path cache refreshed by the weekly update thread; the frozen
    # StartupConfig stays authoritative for everything else.
    app.state.project_db_uuid_index = dict(config.project_db_uuid_index)
    cors_origins = _resolve_cors_origins()
    limiter = _create_rate_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)

    upload_rate_limit = _resolve_upload_rate_limit()
    api_rate_limit = _resolve_api_rate_limit()
    sample_limit_per_minute = _resolve_max_batch_size()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    # Issue/refresh the opaque session cookie on every response.
    _set_session_cookie_middleware(app, config.deployment_mode)

    branding_dir = Path(__file__).resolve().parents[2] / 'respro' / 'report' / 'static'

    app.include_router(
        build_health_router(
            config=config,
            sample_limit_per_minute=sample_limit_per_minute,
            build_readiness_payload=_build_readiness_payload,
            version=version,
        )
    )
    app.include_router(
        build_upload_router(
            uploads_dir=config.uploads_dir,
            limiter=limiter,
            upload_rate_limit=upload_rate_limit,
            user_facing_error_message=_user_facing_error_message,
            save_upload_stream=save_upload_stream,
            get_session=get_session,
        )
    )
    app.include_router(
        build_catalog_router(
            project_databases_dir=config.project_databases_dir,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
        )
    )
    app.include_router(
        build_profile_router(
            config=config,
            sample_limit_per_minute=sample_limit_per_minute,
            consume_sample_quota=_consume_sample_quota,
            is_path_within_allowed_roots=is_path_within_allowed_roots,
            resolve_project_db_path=resolve_project_db_path,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            get_session=get_session,
        )
    )
    app.include_router(
        build_jobs_router(
            map_job_status=_map_job_status,
            user_facing_error_message=_user_facing_error_message,
            job_class=Job,
            no_such_job_error=NoSuchJobError,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            get_session=get_session,
        )
    )
    app.include_router(
        build_artifacts_router(
            results_dir=config.results_dir,
            branding_dir=branding_dir,
            allowed_roots=config.allowed_roots,
            is_path_within_allowed_roots=is_path_within_allowed_roots,
            is_allowed_artifact_path=_is_allowed_artifact_path,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            get_session=get_session,
        )
    )
    app.include_router(
        build_session_router(
            uploads_dir=config.uploads_dir,
            results_dir=config.results_dir,
            allowed_roots=config.allowed_roots,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            get_session=get_session,
        )
    )
    app.include_router(
        build_regenerate_router(
            config=config,
            sample_limit_per_minute=sample_limit_per_minute,
            consume_sample_quota=_consume_sample_quota,
            user_facing_error_message=_user_facing_error_message,
            is_path_within_allowed_roots=is_path_within_allowed_roots,
            resolve_regenerate_project_db_path=resolve_regenerate_project_db_path,
            limiter=limiter,
            api_rate_limit=api_rate_limit,
            get_session=get_session,
        )
    )

    app.include_router(build_legal_router(imprint=config.imprint))
    app.include_router(build_contact_router(contact_email=config.contact_email))

    frontend_dist = _frontend_dist_dir()
    if frontend_dist.is_dir():
        app.mount(
            f'{WEB_BACKEND_CONFIG.defaults.frontend_base_path.rstrip("/")}/assets',
            StaticFiles(directory=str(frontend_dist / 'assets')),
            name='frontend-assets',
        )
        app.mount(
            WEB_BACKEND_CONFIG.defaults.frontend_base_path,
            _build_spa_fallback_app(frontend_dist),
            name='frontend-spa-fallback',
        )

    return app


def _frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'frontend' / 'dist'


def _build_spa_fallback_app(frontend_dist: Path) -> FastAPI:
    """Serve index.html for unknown non-API paths so SPA deep links survive reloads.

    Mounted last: API, report and artifact routes are registered on the main app
    before this mount and are matched first. The fallback answers everything else
    with index.html (real static assets are covered by the assets mount above;
    other dist files, e.g. favicon, are resolved through index.html's own asset
    URLs which the bundler emits under /assets).

    Backend-reserved paths are excluded: ``/api/*`` keeps its JSON 404s, and the
    FastAPI docs URLs (``/docs``, ``/redoc``, ``/openapi.json``) must stay 404 in
    online mode (where they are disabled) instead of receiving index.html.
    """

    reserved_prefixes = ('/api', '/docs', '/redoc', '/openapi.json')

    async def spa_fallback(request: Request) -> FileResponse:
        if request.url.path.startswith(reserved_prefixes):
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(frontend_dist / 'index.html')

    fallback = FastAPI(docs=None, redoc=None, openapi_url=None)
    fallback.add_route('/{path:path}', spa_fallback, methods=['GET'])
    return fallback


def _sweep_expired_files(results_dir: Path, uploads_dir: Path, ttl_seconds: int) -> None:
    """
    Delete files in results and uploads dirs that are older than TTL.
    """
    now = time.time()
    total_deleted = 0

    for directory in (results_dir, uploads_dir):
        if not directory.is_dir():
            continue
        try:
            for item in directory.rglob('*'):
                if not item.is_file():
                    continue
                try:
                    mtime = item.stat().st_mtime
                    age_seconds = now - mtime
                    if age_seconds > ttl_seconds:
                        item.unlink(missing_ok=True)
                        logger.debug(f'Deleted expired file: {item}')
                        total_deleted += 1
                except OSError as exc:
                    logger.debug(f'Error processing file {item}: {exc}')
        except OSError as exc:
            logger.debug(f'Error scanning directory {directory}: {exc}')

    if total_deleted > 0:
        logger.info(f'TTL sweep deleted {total_deleted} expired files from results and uploads directories')


def _start_ttl_sweep_thread(results_dir: Path, uploads_dir: Path) -> None:
    """Start a background thread that periodically deletes expired files."""
    sweep_thread = threading.Thread(
        target=_ttl_sweep_loop,
        args=(results_dir, uploads_dir, WEB_BACKEND_CONFIG.defaults.sweep_frequency_seconds),
        daemon=True,
    )
    sweep_thread.start()


def _ttl_sweep_loop(results_dir: Path, uploads_dir: Path, sweep_frequency_seconds: int) -> None:
    """Continuously sweep expired upload and result files on a fixed interval."""
    while True:
        try:
            ttl_seconds = int(os.getenv(WEB_ENV.result_ttl, str(WEB_BACKEND_CONFIG.defaults.result_ttl_seconds)))
            _sweep_expired_files(results_dir, uploads_dir, ttl_seconds)
        except Exception as exc:
            logger.debug(f'Error in TTL sweep: {exc}')
        time.sleep(sweep_frequency_seconds)


def _start_maintained_db_update_thread(
    project_databases_dir: Path,
    interval_seconds: int,
    app_state,
    stop_event: threading.Event | None = None,
) -> threading.Thread | None:
    """Start a daemon thread that periodically refreshes maintained databases.

    :param project_databases_dir: directory containing project ``.db`` files
    :param interval_seconds: seconds between update checks; ``0`` disables the thread
    :param app_state: FastAPI ``app.state`` holding the mutable ``project_db_uuid_index``
    :param stop_event: optional ``threading.Event``; when set the loop exits cleanly.
        Defaults to a never-set event so production behaviour is unchanged.
    :return: the started daemon thread, or ``None`` if disabled.
    """
    if interval_seconds <= 0:
        logger.debug('Maintained database auto-update thread disabled (interval=0)')
        return None
    stop = stop_event if stop_event is not None else threading.Event()
    update_thread = threading.Thread(
        target=_maintained_db_update_loop,
        args=(project_databases_dir, interval_seconds, app_state, stop),
        daemon=True,
    )
    update_thread.start()
    return update_thread


def _maintained_db_update_loop(
    project_databases_dir: Path,
    interval_seconds: int,
    app_state,
    stop_event: threading.Event | None = None,
) -> None:
    """Periodically refresh maintained databases and rebuild the UUID index cache.

    Exits cleanly when ``stop_event`` is set so tests and shutdown can stop the daemon
    without leaving a background thread performing real network calls.
    """
    stop = stop_event if stop_event is not None else threading.Event()
    while not stop.is_set():
        try:
            check_and_update_maintained_databases(project_databases_dir)
            new_index = refresh_project_db_uuid_index(project_databases_dir)
            app_state.project_db_uuid_index.clear()
            app_state.project_db_uuid_index.update(new_index)
        except Exception:  # noqa: BLE001 — a failed update pass must not kill the daemon
            logger.exception('Maintained database weekly update pass failed')
        stop.wait(interval_seconds)


def _resolve_maintained_db_update_interval() -> int:
    """Resolve the weekly update interval, falling back to the config default on invalid input."""
    default = WEB_BACKEND_CONFIG.defaults.maintained_db_update_interval_seconds
    raw_value = os.getenv(WEB_ENV.maintained_db_update_interval, str(default)).strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning(
            '%s must be an integer; falling back to default %s',
            WEB_ENV.maintained_db_update_interval,
            default,
        )
        return default
    if parsed < 0:
        logger.warning(
            '%s must be >= 0; falling back to default %s',
            WEB_ENV.maintained_db_update_interval,
            default,
        )
        return default
    return parsed


def _map_job_status(rq_status) -> str:
    """Map an RQ job status to the stable API status string."""
    finished_statuses = ('finished',)
    running_statuses = ('started',)
    failed_statuses = ('failed', 'stopped', 'canceled')
    if rq_status in finished_statuses:
        return 'succeeded'
    if rq_status in running_statuses:
        return 'running'
    if rq_status in failed_statuses:
        return 'failed'
    return 'queued'


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

    status = 'ok' if not diagnostics else 'error'
    return ApiEnvelope(
        status=status,
        data={
            'service': WEB_BACKEND_CONFIG.defaults.service_name,
            'redis': {'connected': redis_connected},
            'project_databases': {
                'ready': project_db_ready,
                'count': project_db_count,
            },
            'workspace': workspace,
            'diagnostics': diagnostics,
        },
    )


def _is_redis_connected() -> bool:
    """Check Redis connectivity for readiness checks."""
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url)
    try:
        client = redis.Redis.from_url(redis_url)
        return bool(client.ping())
    except redis.RedisError as exc:
        logger.debug('Readiness check: Redis ping failed for %s: %s', redis_url, exc)
        return False
    except OSError as exc:
        logger.debug('Readiness check: Redis connection failed for %s: %s', redis_url, exc)
        return False
    except RuntimeError as exc:
        logger.debug('Readiness check: Redis runtime error for %s: %s', redis_url, exc)
        return False


def _project_database_catalog_readiness(project_databases_dir: Path) -> tuple[bool, int]:
    """Validate project database catalog readiness and return ready/count diagnostics."""
    try:
        db_paths = list_project_db_paths(project_databases_dir)
    except (FileNotFoundError, OSError, ValueError) as exc:
        logger.debug(
            'Readiness check: project database catalog unavailable in %s: %s',
            project_databases_dir,
            exc,
        )
        return False, 0
    return bool(db_paths), len(db_paths)


def _user_facing_error_message(raw_message: str | None) -> str:
    """Return a short user-facing message for API and job failures."""
    if not raw_message:
        return 'The operation failed on the server.'

    message = _extract_primary_error_message(raw_message)
    for prefix in ('Error: ', 'ValueError: ', 'RuntimeError: ', 'Exception: ', 'OSError: '):
        if message.startswith(prefix):
            message = message[len(prefix):]
            break

    lowered = message.lower()
    if 'fasta file does not appear to contain valid sequence data' in lowered:
        return 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.'
    if 'fasta file contains non-text/binary bytes' in lowered:
        return 'Unsupported FASTA format. Upload a plain-text FASTA file.'
    if 'fasta file contains invalid sequence characters' in lowered:
        return 'Unsupported FASTA format. Sequence lines contain unsupported characters.'
    if 'fasta file contains line' in lowered and 'longer than' in lowered:
        return 'Unsupported FASTA format. Input contains an excessively long line.'
    if 'vcf file does not appear to have valid vcf headers' in lowered:
        return 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
    if 'json upload is empty' in lowered:
        return 'Unsupported JSON format. Upload a non-empty results JSON file.'
    if 'json upload must be valid utf-8 text' in lowered:
        return 'Unsupported JSON format. Upload a UTF-8 encoded JSON file.'
    if 'invalid results json' in lowered:
        return (
            'Unsupported JSON format. Upload a valid ResistanceProfiler results JSON '
            'with run, variant_result, coverage_gap, formula_rule_hit, and sample_classification sections.'
        )
    if 'project database uuid mismatch' in lowered:
        return (
            'Project database UUID mismatch. Database updates currently do not allow '
            'regeneration of reports from older database versions.'
        )
    if 'vcf file contains non-text/binary bytes' in lowered:
        return 'Unsupported VCF format. Upload a plain-text VCF file.'
    if 'vcf file contains data rows before #chrom header' in lowered:
        return 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
    if 'vcf file contains line' in lowered and 'longer than' in lowered:
        return 'Unsupported VCF format. Input contains an excessively long line.'
    if 'vcf file exceeds maximum data row count' in lowered:
        return 'Unsupported VCF format. Input contains too many variant rows.'
    if 'bam file does not have valid bgzf/gzip magic signature' in lowered or 'bam file is too small' in lowered:
        return 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'
    if 'failed to parse fasta input' in lowered:
        return 'Unsupported FASTA format. The FASTA file could not be parsed.'
    if 'failed to parse reference fasta input' in lowered:
        return 'Unsupported FASTA reference format. The reference FASTA file could not be parsed.'
    if 'failed to parse vcf input' in lowered:
        return 'Unsupported VCF format. The VCF file could not be parsed.'
    if 'failed to parse bam coverage input' in lowered:
        return 'Unsupported BAM format. The BAM file could not be parsed for coverage analysis.'
    if 'vcf contig names do not match the uploaded reference fasta' in lowered:
        return 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'
    if 'failed to create bam index' in lowered:
        return 'Coverage annotation needs a coordinate-sorted BAM. The server could not create an index for this file.'
    if 'bam reference' in lowered and 'not found' in lowered:
        return 'BAM and reference FASTA do not match. Use files derived from the same reference sequence.'
    if 'no cds matches found' in lowered:
        return 'No matches to references in the database found.'
    if message.startswith('Upload failed:'):
        return 'The upload failed on the server.'
    return message


_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mK]')


def _extract_primary_error_message(raw_message: str) -> str:
    """Extract one meaningful error line from traceback or Rich panel output."""
    cleaned = _ANSI_ESCAPE.sub('', raw_message)
    raw_lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
    if not raw_lines:
        return raw_message.strip()

    boxed_lines: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith('│') and stripped.endswith('│'):
            inner = stripped.strip('│').strip()
            if inner:
                boxed_lines.append(inner)
    if boxed_lines:
        return ' '.join(boxed_lines)

    return raw_lines[-1].strip()


def _resolve_cors_origins() -> list[str]:
    """Resolve CORS origins from env with secure defaults for local development."""
    configured = os.getenv(WEB_ENV.cors_origins, '').strip()
    if configured:
        origins = [value.strip() for value in configured.split(',') if value.strip()]
        if origins:
            return origins
    return list(WEB_BACKEND_CONFIG.defaults.cors_local_origins)


def _resolve_upload_rate_limit() -> str:
    """Return the configured upload rate limit string."""
    default = WEB_BACKEND_CONFIG.defaults.upload_rate_limit
    return os.getenv(WEB_ENV.upload_rate_limit, default).strip() or default


def _resolve_api_rate_limit() -> str:
    """Return the configured non-upload API rate limit string (SEC-004).

    Applies to job-status, profile, regenerate, artifact, and session routes to
    resist brute-force/scraping. Defaults to a permissive 120/minute so the
    zero-config local deployment is unaffected; operators can tighten it via
    ``RESPRO_WEB_API_RATE_LIMIT``.
    """
    default = WEB_BACKEND_CONFIG.defaults.api_rate_limit
    return os.getenv(WEB_ENV.api_rate_limit, default).strip() or default


def _resolve_max_batch_size() -> int:
    """Return the configured maximum number of samples accepted per batch request."""
    default = WEB_BACKEND_CONFIG.defaults.max_batch_size
    raw_value = os.getenv(WEB_ENV.max_batch_size, str(default)).strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f'{WEB_ENV.max_batch_size} must be an integer value.') from exc
    if parsed <= 0:
        raise RuntimeError(f'{WEB_ENV.max_batch_size} must be > 0.')
    return parsed


def _current_window_minute() -> int:
    """Return the current minute window for sample quota accounting."""
    return int(time.time() // 60)


def _consume_sample_quota(request: Request, sample_count: int, sample_limit_per_minute: int) -> None:
    """Consume sample quota for the request identity in the current minute window."""
    if sample_count <= 0:
        return

    detail = (
        f'Sample rate limit exceeded. At most {sample_limit_per_minute} '
        'samples can be analyzed per minute.'
    )
    if sample_count > sample_limit_per_minute:
        raise HTTPException(status_code=429, detail=detail)

    identity = _rate_limit_key(request)
    window_minute = _current_window_minute()
    redis_url = os.getenv(WEB_ENV.redis_url, WEB_BACKEND_CONFIG.defaults.redis_url).strip()
    if redis_url:
        try:
            client = redis.Redis.from_url(redis_url)
            redis_key = f'respro:sample_quota:{identity}:{window_minute}'
            total = client.incrby(redis_key, sample_count)
            client.expire(redis_key, 120)
            if total > sample_limit_per_minute:
                raise HTTPException(status_code=429, detail=detail)
            return
        except HTTPException:
            raise
        except (redis.RedisError, OSError, RuntimeError) as exc:
            logger.debug('Sample quota Redis check failed for identity %s: %s', identity, exc)

    with _SAMPLE_QUOTA_LOCK:
        stale_before = window_minute - 1
        stale_keys = [
            counter_key
            for counter_key in _SAMPLE_QUOTA_COUNTER
            if counter_key[1] < stale_before
        ]
        for stale_key in stale_keys:
            del _SAMPLE_QUOTA_COUNTER[stale_key]

        counter_key = (identity, window_minute)
        total = _SAMPLE_QUOTA_COUNTER.get(counter_key, 0) + sample_count
        if total > sample_limit_per_minute:
            raise HTTPException(status_code=429, detail=detail)
        _SAMPLE_QUOTA_COUNTER[counter_key] = total


def _rate_limit_key(request: Request) -> str:
    """Use the client IP as the rate-limit identity."""
    client_host = request.client.host if request.client else ''
    if client_host:
        return f'ip:{client_host}'
    return f'ip:{get_remote_address(request)}'


def _create_rate_limiter() -> Limiter:
    """Create the shared upload limiter, using Redis storage when configured."""
    redis_url = os.getenv(WEB_ENV.redis_url, '').strip()
    if redis_url:
        return Limiter(key_func=_rate_limit_key, storage_uri=redis_url)
    return Limiter(key_func=_rate_limit_key)


def _handle_rate_limit_exceeded(_: Request, __: RateLimitExceeded) -> JSONResponse:
    """Return a clear rate-limit error response for upload endpoints."""
    return JSONResponse(
        status_code=429,
        content={'detail': 'Upload rate limit exceeded. Try again later.'},
    )


def get_session(request: Request) -> Session:
    """Return the session resolved by the session middleware.

    The middleware resolves/creates the session on every request and stashes it
    on ``request.state.session``. This dependency exposes it to routes so they
    can read the owner hash for ownership checks. Routes that need ownership
    enforcement declare ``session: Session = Depends(get_session)``.
    """
    return request.state.session


def _set_session_cookie_middleware(app: FastAPI, deployment_mode: str) -> None:
    """Register middleware that resolves/creates a session and sets the cookie.

    Resolving the session in middleware (rather than only via a route dependency)
    guarantees every response — including static frontend assets and routes that
    do not declare ``get_session`` — carries a session cookie, so a browser
    navigating to the app receives one on the very first request regardless of
    the entry route. The resolved session is stashed on ``request.state.session``
    so routes can read the owner hash without re-resolving.

    ``deployment_mode`` controls whether the ``Secure`` attribute is set on the
    cookie: local mode (HTTP on loopback) omits it; online mode sets it (behind
    a TLS-terminating proxy).
    """

    @app.middleware('http')
    async def _session_cookie(request: Request, call_next):  # type: ignore[no-untyped-def]
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        # resolve_or_create_session performs synchronous Redis I/O; run it in the
        # threadpool so a slow Redis cannot stall the event loop for every request.
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
    proxy_headers, forwarded_allow_ips = _resolve_proxy_settings()
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        reload=False,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
    )


def _resolve_proxy_settings() -> tuple[bool, str]:
    """Enable trusted proxy forwarding only when explicitly configured."""
    configured = os.getenv(WEB_ENV.trusted_proxies, '').strip()
    if not configured:
        return False, ''

    proxy_ips = [value.strip() for value in configured.split(',') if value.strip()]
    if not proxy_ips:
        return False, ''
    return True, ','.join(proxy_ips)


if __name__ == '__main__':
    run()
