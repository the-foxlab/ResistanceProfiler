"""FastAPI application for the prototype web backend."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from web.backend.jobs import run_profile_fasta, run_profile_vcf
from web.backend.models import (
    ApiEnvelope,
    JobStatusResponse,
    JobSubmitResponse,
    ProfileFastaPayload,
    ProfileVcfPayload,
    SessionCleanupPayload,
    SessionCleanupResponse,
    UploadResponse,
)
from web.backend.queue import get_queue
from web.backend.services.browse import list_databases, list_rules
from web.backend.services.upload import cleanup_session_files, save_upload
from web.backend.startup_config import (
    StartupConfig,
    is_path_within_allowed_roots,
    load_startup_config,
)


def create_app(startup_config: StartupConfig | None = None) -> FastAPI:
    """Create the FastAPI app instance."""
    app = FastAPI(title='ResistanceProfiler Web API', version='0.1.0')
    config = startup_config or load_startup_config()
    app.state.startup_config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'],
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    _env_dist = os.environ.get('RESPRO_FRONTEND_DIST')
    frontend_dist = Path(_env_dist) if _env_dist else Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    if frontend_dist.is_dir():
        app.mount('/app', StaticFiles(directory=str(frontend_dist), html=True), name='frontend')

    branding_dir = Path(__file__).resolve().parents[2] / 'respro' / 'report' / 'static'

    @app.get('/api/health', response_model=ApiEnvelope)
    def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'service': 'respro-web-api',
                'project_db': str(config.project_db),
                'results_db': str(config.results_db),
                'output_dir': str(config.output_dir),
            },
            status='ok',
        )

    @app.post('/api/upload/fasta', response_model=UploadResponse)
    async def upload_fasta(
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        try:
            file_data = await file.read()
            upload_dir = config.output_dir / '.uploads'
            saved_path = save_upload(file_data, 'fasta', upload_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='fasta',
                size_bytes=len(file_data),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc

    @app.post('/api/upload/vcf', response_model=UploadResponse)
    async def upload_vcf(
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        try:
            file_data = await file.read()
            upload_dir = config.output_dir / '.uploads'
            saved_path = save_upload(file_data, 'vcf', upload_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='vcf',
                size_bytes=len(file_data),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc

    @app.post('/api/upload/bam', response_model=UploadResponse)
    async def upload_bam(
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        try:
            file_data = await file.read()
            upload_dir = config.output_dir / '.uploads'
            saved_path = save_upload(file_data, 'bam', upload_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type='bam',
                size_bytes=len(file_data),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc

    @app.get('/api/rules', response_model=ApiEnvelope)
    def rules(
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
        try:
            data = list_rules(config.project_db, reference_filter=reference)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/mutations', response_model=ApiEnvelope)
    def mutations(
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
        try:
            data = list_rules(config.project_db, reference_filter=reference)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/databases', response_model=ApiEnvelope)
    def databases(_auth: None = Depends(require_api_token)) -> ApiEnvelope:
        try:
            data = list_databases(config.project_db)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/session/cleanup', response_model=SessionCleanupResponse)
    def cleanup_session_uploads(
        payload: SessionCleanupPayload,
        _auth: None = Depends(require_api_token),
    ) -> SessionCleanupResponse:
        upload_dir = config.output_dir / '.uploads'
        deleted_count = cleanup_session_files(
            payload.upload_paths,
            payload.report_paths,
            upload_dir,
            config.output_dir,
        )
        return SessionCleanupResponse(deleted_count=deleted_count)

    @app.post('/api/profile/fasta', response_model=JobSubmitResponse)
    def profile_fasta_route(
        payload: ProfileFastaPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        job = queue.enqueue(
            run_profile_fasta,
            project_db=str(config.project_db),
            results_db=str(config.results_db),
            output_dir=str(config.output_dir),
            fasta_path=payload.fasta_path,
            sample=payload.sample,
            threads=payload.threads,
            aligner=payload.aligner,
        )
        return JobSubmitResponse(job_id=job.id)

    @app.post('/api/profile/vcf', response_model=JobSubmitResponse)
    def profile_vcf_route(
        payload: ProfileVcfPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        job = queue.enqueue(
            run_profile_vcf,
            project_db=str(config.project_db),
            results_db=str(config.results_db),
            output_dir=str(config.output_dir),
            vcf_path=payload.vcf_path,
            ref_fasta_path=payload.ref_fasta_path,
            sample=payload.sample,
            min_af=payload.min_af,
            min_depth=payload.min_depth,
            bam_path=payload.bam_path,
            threads=payload.threads,
            aligner=payload.aligner,
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
        return JobStatusResponse(job_id=job_id, status=status, result=result, error=error)

    @app.get('/api/report')
    def open_report(
        path: str = Query(...),
        _auth: None = Depends(require_api_token),
    ) -> FileResponse:
        report_path = Path(path).expanduser().resolve()
        if not is_path_within_allowed_roots(report_path, (config.output_dir,)):
            raise HTTPException(status_code=400, detail='Report path is outside allowed output directory.')
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail='Report not found.')
        return FileResponse(str(report_path), media_type='text/html')

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
    if 'vcf file does not appear to have valid vcf headers' in lowered:
        return 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
    if 'bam file does not have valid bgzf/gzip magic signature' in lowered or 'bam file is too small' in lowered:
        return 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'
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
    host = os.getenv('RESPRO_WEB_HOST', '127.0.0.1')
    port = int(os.getenv('RESPRO_WEB_PORT', '8000'))
    uvicorn.run(create_app(), host=host, port=port, reload=False)


if __name__ == '__main__':
    run()
