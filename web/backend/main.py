"""FastAPI application for the web backend."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from web.backend.config import (
    WEB_BACKEND_CONFIG,
    WEB_ENV,
)
from web.backend.jobs import run_profile_fasta, run_profile_vcf, run_regenerate_json
from web.backend.models import (
    ApiEnvelope,
    JobStatusResponse,
    JobSubmitResponse,
    ProfileFastaPayload,
    ProfileVcfPayload,
    RegenerateJsonPayload,
    SessionCleanupPayload,
    SessionCleanupResponse,
    UploadResponse,
)
from web.backend.queue import get_queue
from web.backend.services.browse import list_databases, list_rules
from web.backend.services.upload import cleanup_session_files, save_upload_stream
from web.backend.startup_config import (
    StartupConfig,
    is_path_within_allowed_roots,
    list_project_db_paths,
    load_startup_config,
    resolve_project_db_path,
)


def create_app(startup_config: StartupConfig | None = None) -> FastAPI:
    """Create the FastAPI app instance."""
    app = FastAPI(title='ResistanceProfiler Web API', version='0.1.0')
    config = startup_config or load_startup_config()
    app.state.startup_config = config
    cors_origins = _resolve_cors_origins(config.api_token)
    limiter = _create_rate_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)

    upload_rate_limit = _resolve_upload_rate_limit()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    frontend_dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    if frontend_dist.is_dir():
        app.mount(
            WEB_BACKEND_CONFIG.defaults.frontend_base_path,
            StaticFiles(directory=str(frontend_dist), html=True),
            name='frontend',
        )

    branding_dir = Path(__file__).resolve().parents[2] / 'respro' / 'report' / 'static'

    @app.get('/api/health', response_model=ApiEnvelope)
    def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'service': WEB_BACKEND_CONFIG.defaults.service_name,
                'project_databases_dir': str(config.project_databases_dir),
                'project_database_count': len(list_project_db_paths(config.project_databases_dir)),
                'results_db': str(config.results_db),
                'uploads_dir': str(config.uploads_dir),
                'results_dir': str(config.results_dir),
            },
            status='ok',
        )

    @app.post('/api/upload/fasta', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_fasta(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        try:
            saved_path, size_bytes = await save_upload_stream(file, 'fasta', config.uploads_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='fasta',
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc
        finally:
            await file.close()

    @app.post('/api/upload/vcf', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_vcf(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        try:
            saved_path, size_bytes = await save_upload_stream(file, 'vcf', config.uploads_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='vcf',
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc
        finally:
            await file.close()

    @app.post('/api/upload/bam', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_bam(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        try:
            saved_path, size_bytes = await save_upload_stream(file, 'bam', config.uploads_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='bam',
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc
        finally:
            await file.close()

    @app.post('/api/upload/json', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_json(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        try:
            saved_path, size_bytes = await save_upload_stream(file, 'json', config.uploads_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='json',
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc
        finally:
            await file.close()

    @app.get('/api/rules', response_model=ApiEnvelope)
    def rules(
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
        try:
            data = list_rules(
                config.project_databases_dir,
                database_id,
                reference_filter=reference,
            )
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/mutations', response_model=ApiEnvelope)
    def mutations(
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
        # Alias for /api/rules — delegates to the same handler.
        return rules(database_id=database_id, reference=reference, _auth=_auth)

    @app.get('/api/databases', response_model=ApiEnvelope)
    def databases(_auth: None = Depends(require_api_token)) -> ApiEnvelope:
        try:
            data = list_databases(config.project_databases_dir)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/session/cleanup', response_model=SessionCleanupResponse)
    def cleanup_session_uploads(
        payload: SessionCleanupPayload,
        _auth: None = Depends(require_api_token),
    ) -> SessionCleanupResponse:
        deleted_count = cleanup_session_files(
            payload.upload_paths,
            payload.report_paths,
            config.uploads_dir,
            config.results_dir,
        )
        return SessionCleanupResponse(deleted_count=deleted_count)

    @app.post('/api/profile/fasta', response_model=JobSubmitResponse)
    def profile_fasta_route(
        payload: ProfileFastaPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        fasta_path = Path(payload.fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(fasta_path, (config.uploads_dir,)):
            raise HTTPException(status_code=400, detail='FASTA path is outside allowed upload directory.')
        if not fasta_path.is_file():
            raise HTTPException(status_code=404, detail='FASTA file not found.')
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        profile_defaults = WEB_BACKEND_CONFIG.defaults.profile
        job = queue.enqueue(
            run_profile_fasta,
            project_db=str(project_db),
            results_db=str(config.results_db),
            output_dir=str(config.results_dir),
            fasta_path=str(fasta_path),
            sample=payload.sample or profile_defaults.sample_name,
            threads=payload.threads if payload.threads is not None else profile_defaults.threads,
            aligner=payload.aligner or profile_defaults.aligner,
        )
        return JobSubmitResponse(job_id=job.id)

    @app.post('/api/profile/vcf', response_model=JobSubmitResponse)
    def profile_vcf_route(
        payload: ProfileVcfPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        vcf_path = Path(payload.vcf_path).expanduser().resolve()
        if not is_path_within_allowed_roots(vcf_path, (config.uploads_dir,)):
            raise HTTPException(status_code=400, detail='VCF path is outside allowed upload directory.')
        if not vcf_path.is_file():
            raise HTTPException(status_code=404, detail='VCF file not found.')
        ref_fasta_path = Path(payload.ref_fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(ref_fasta_path, (config.uploads_dir,)):
            raise HTTPException(status_code=400, detail='Reference FASTA path is outside allowed upload directory.')
        if not ref_fasta_path.is_file():
            raise HTTPException(status_code=404, detail='Reference FASTA file not found.')
        bam_path: str | None = None
        if payload.bam_path:
            resolved_bam = Path(payload.bam_path).expanduser().resolve()
            if not is_path_within_allowed_roots(resolved_bam, (config.uploads_dir,)):
                raise HTTPException(status_code=400, detail='BAM path is outside allowed upload directory.')
            if not resolved_bam.is_file():
                raise HTTPException(status_code=404, detail='BAM file not found.')
            bam_path = str(resolved_bam)
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        profile_defaults = WEB_BACKEND_CONFIG.defaults.profile
        job = queue.enqueue(
            run_profile_vcf,
            project_db=str(project_db),
            results_db=str(config.results_db),
            output_dir=str(config.results_dir),
            vcf_path=str(vcf_path),
            ref_fasta_path=str(ref_fasta_path),
            sample=payload.sample or profile_defaults.sample_name,
            min_af=payload.min_af if payload.min_af is not None else profile_defaults.min_af,
            min_depth=payload.min_depth if payload.min_depth is not None else profile_defaults.min_depth,
            bam_path=bam_path,
            threads=payload.threads if payload.threads is not None else profile_defaults.threads,
            aligner=payload.aligner or profile_defaults.aligner,
        )
        return JobSubmitResponse(job_id=job.id)

    @app.post('/api/regenerate/json', response_model=JobSubmitResponse)
    def regenerate_json_route(
        payload: RegenerateJsonPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        json_path = Path(payload.json_path).expanduser().resolve()
        if not is_path_within_allowed_roots(json_path, (config.uploads_dir, config.results_dir)):
            raise HTTPException(status_code=400, detail='JSON path is outside allowed upload/output directory.')
        if not json_path.is_file():
            raise HTTPException(status_code=404, detail='JSON file not found.')

        job = queue.enqueue(
            run_regenerate_json,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            json_path=str(json_path),
        )
        return JobSubmitResponse(job_id=job.id)

    @app.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
    def job_status(
        job_id: str,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobStatusResponse:
        try:
            job = Job.fetch(job_id, connection=queue.connection)
        except NoSuchJobError:
            raise HTTPException(status_code=404, detail='Job not found.')

        rq_status = job.get_status()
        status = _map_job_status(rq_status)
        result = job.return_value() if status == 'succeeded' else None
        error = _user_facing_error_message(job.exc_info) if status == 'failed' and job.exc_info else None
        if status == 'failed' and error is None and rq_status in ('stopped', 'canceled'):
            error = 'Job canceled by user.'
        return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)

    @app.delete('/api/jobs/{job_id}', status_code=204)
    def cancel_job(
        job_id: str,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> Response:
        try:
            job = Job.fetch(job_id, connection=queue.connection)
        except NoSuchJobError:
            raise HTTPException(status_code=404, detail='Job not found.')

        rq_status = job.get_status()
        if rq_status in ('queued', 'scheduled', 'deferred'):
            job.cancel()
            return Response(status_code=204)

        if rq_status == 'started':
            kill_worker = getattr(job, 'kill_worker', None)
            if callable(kill_worker):
                kill_worker()
                return Response(status_code=204)

            # Fallback when worker-kill support is unavailable in the installed RQ version.
            job.exc_info = 'Job canceled by user.'
            job.set_status('failed')
            job.save()
            return Response(status_code=204)

        return Response(status_code=204)

    @app.get('/api/report')
    def open_report(
        path: str = Query(...),
        _auth: None = Depends(require_api_token),
    ) -> FileResponse:
        report_path = Path(path).expanduser().resolve()
        if not is_path_within_allowed_roots(report_path, (config.results_dir,)):
            raise HTTPException(status_code=400, detail='Report path is outside allowed output directory.')
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail='Report not found.')
        return FileResponse(str(report_path), media_type='text/html')

    @app.get('/api/artifact')
    def download_artifact(
        path: str = Query(...),
        _auth: None = Depends(require_api_token),
    ) -> FileResponse:
        artifact_path = Path(path).expanduser().resolve()
        if not is_path_within_allowed_roots(artifact_path, (config.results_dir,)):
            raise HTTPException(status_code=400, detail='Artifact path is outside allowed output directory.')
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail='Artifact not found.')

        media_type = mimetypes.guess_type(str(artifact_path))[0] or 'application/octet-stream'
        return FileResponse(
            str(artifact_path),
            media_type=media_type,
            filename=artifact_path.name,
        )

    @app.get('/api/branding/logo.svg')
    def branding_logo() -> FileResponse:
        logo_path = branding_dir / 'logo.svg'
        if not logo_path.is_file():
            raise HTTPException(status_code=404, detail='Logo not found.')
        return FileResponse(str(logo_path), media_type='image/svg+xml')

    @app.get('/api/branding/favicon.svg')
    def branding_favicon() -> FileResponse:
        favicon_path = branding_dir / 'favicon.svg'
        if not favicon_path.is_file():
            raise HTTPException(status_code=404, detail='Favicon not found.')
        return FileResponse(str(favicon_path), media_type='image/svg+xml')

    return app


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


def _user_facing_error_message(raw_message: str | None) -> str:
    """Return a short user-facing message for API and job failures."""
    if not raw_message:
        return 'The operation failed on the server.'

    lines = [line.strip() for line in raw_message.splitlines() if line.strip()]
    message = lines[-1] if lines else raw_message.strip()
    for prefix in ('ValueError: ', 'RuntimeError: ', 'Exception: ', 'OSError: '):
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
    if 'unknown aligner' in lowered:
        return 'Unsupported aligner selection.'
    if message.startswith('Upload failed:'):
        return 'The upload failed on the server.'
    return message


def _resolve_cors_origins(api_token: str) -> list[str]:
    """Resolve CORS origins from env with secure defaults for local development."""
    configured = os.getenv(WEB_ENV.cors_origins, '').strip()
    if configured:
        origins = [value.strip() for value in configured.split(',') if value.strip()]
        if origins:
            return origins

    if api_token:
        return ['*']

    return list(WEB_BACKEND_CONFIG.defaults.cors_local_origins)


def _resolve_upload_rate_limit() -> str:
    """Return the configured upload rate limit string."""
    default = WEB_BACKEND_CONFIG.defaults.upload_rate_limit
    return os.getenv(WEB_ENV.upload_rate_limit, default).strip() or default


def _rate_limit_key(request: Request) -> str:
    """Use token identity when present, otherwise fall back to client IP."""
    authorization = request.headers.get('Authorization', '').strip()
    if authorization:
        return f'token:{authorization}'

    token = request.query_params.get('token', '').strip()
    if token:
        return f'token:{token}'

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


def get_startup_config(request: Request) -> StartupConfig:
    """Return startup config from FastAPI application state."""
    return request.app.state.startup_config


def require_api_token(
    config: StartupConfig = Depends(get_startup_config),
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Require bearer token auth when RESPRO_WEB_API_TOKEN is configured."""
    if not config.api_token:
        return
    if token == config.api_token:
        return
    expected = f'Bearer {config.api_token}'
    if authorization != expected:
        raise HTTPException(status_code=401, detail='Unauthorized')


def run() -> None:
    """Run the web API with uvicorn."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(name)s: %(message)s')
    host = os.getenv(WEB_ENV.host, WEB_BACKEND_CONFIG.defaults.web_host)
    port = int(os.getenv(WEB_ENV.port, str(WEB_BACKEND_CONFIG.defaults.web_port)))
    uvicorn.run(create_app(), host=host, port=port, reload=False)


if __name__ == '__main__':
    run()
