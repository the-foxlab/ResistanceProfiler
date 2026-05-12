"""FastAPI application for the web backend."""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import io
import logging
import mimetypes
import os
import re
import threading
import time
import zipfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import redis
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
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
    ArtifactBundlePayload,
    BatchProfileFastaPayload,
    BatchProfileVcfPayload,
    BatchSampleEntry,
    BatchSubmitResponse,
    JobStatusResponse,
    JobSubmitResponse,
    ProfileFastaPayload,
    ProfileVcfPayload,
    RegenerateJsonPayload,
    SessionCleanupPayload,
    SessionCleanupResponse,
    UploadResponse,
)
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue
from web.backend.services.browse import list_databases, list_rules
from web.backend.services.upload import cleanup_session_files, save_upload_stream
from web.backend.startup_config import (
    StartupConfig,
    is_path_within_allowed_roots,
    list_project_db_paths,
    load_startup_config,
    resolve_project_db_path,
)

logger = logging.getLogger(__name__)
_SAMPLE_QUOTA_LOCK = threading.Lock()
_SAMPLE_QUOTA_COUNTER: dict[tuple[str, int], int] = {}
_WEB_TIMESTAMP_TOKEN = re.compile(
    r'\.(\d{20})(?=\.(?:report\.html|report\.pdf|results\.json|mutations\.tsv)$)'
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """FastAPI lifespan context manager."""
    config: StartupConfig = app.state.startup_config
    _start_ttl_sweep_thread(config.results_dir, config.uploads_dir)
    yield


def create_app(startup_config: StartupConfig | None = None) -> FastAPI:
    """Create the FastAPI app instance."""
    version = importlib.metadata.version('respro')
    app = FastAPI(
        title='ResistanceProfiler Web API',
        version=version,
        lifespan=lifespan,
    )
    config = startup_config or load_startup_config()
    app.state.startup_config = config
    cors_origins = _resolve_cors_origins(config.api_token)
    limiter = _create_rate_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)

    upload_rate_limit = _resolve_upload_rate_limit()
    sample_limit_per_minute = _resolve_max_batch_size()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    branding_dir = Path(__file__).resolve().parents[2] / 'respro' / 'report' / 'static'

    def _is_allowed_artifact_path(artifact_path: Path) -> bool:
        """Allow only known artifact file types for downloads."""
        allowed_suffixes = (
            '.report.pdf',
            '.results.json',
            '.mutations.tsv',
            '.report.html',
        )
        return any(str(artifact_path).endswith(suffix) for suffix in allowed_suffixes)

    async def _handle_upload(
        *,
        file: UploadFile,
        file_type: Literal['fasta', 'vcf', 'bam', 'json'],
    ) -> UploadResponse:
        try:
            saved_path, size_bytes = await save_upload_stream(file, file_type, config.uploads_dir)
            return UploadResponse(
                file_path=str(saved_path),
                file_type=file_type,
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_user_facing_error_message(str(exc))) from exc
        finally:
            await file.close()

    @app.get('/api/health', response_model=ApiEnvelope)
    def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'service': WEB_BACKEND_CONFIG.defaults.service_name,
            },
            status='ok',
        )

    @app.get('/api/readiness', response_model=ApiEnvelope)
    def readiness() -> JSONResponse | ApiEnvelope:
        payload = _build_readiness_payload(config)
        if payload.status == 'ok':
            return payload
        return JSONResponse(status_code=503, content=payload.model_dump())

    @app.get('/api/ui/config', response_model=ApiEnvelope)
    def ui_config(_auth: None = Depends(require_api_token)) -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'batch_max_samples': sample_limit_per_minute,
                'sample_limit_per_minute': sample_limit_per_minute,
            }
        )

    @app.post('/api/upload/fasta', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_fasta(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        return await _handle_upload(file=file, file_type='fasta')

    @app.post('/api/upload/vcf', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_vcf(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        return await _handle_upload(file=file, file_type='vcf')

    @app.post('/api/upload/bam', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_bam(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        return await _handle_upload(file=file, file_type='bam')

    @app.post('/api/upload/json', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_json(
        request: Request,
        file: UploadFile = File(...),
        _auth: None = Depends(require_api_token),
    ) -> UploadResponse:
        del request
        return await _handle_upload(file=file, file_type='json')

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
        request: Request,
        payload: ProfileFastaPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        fasta_path = Path(payload.fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='FASTA path is outside allowed upload directory.')
        if not fasta_path.is_file():
            raise HTTPException(status_code=404, detail='FASTA file not found.')
        _consume_sample_quota(
            request,
            sample_count=1,
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
        enqueue_options = build_enqueue_job_options()
        job = queue.enqueue(
            run_profile_fasta,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            fasta_path=str(fasta_path),
            sample=payload.sample or defaults.profile_sample_name,
            threads=payload.threads if payload.threads is not None else defaults.profile_threads,
            input_display_name=payload.input_display_name,
            **enqueue_options,
        )
        logger.info('Queue job enqueued: job_id=%s mode=fasta database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @app.post('/api/profile/vcf', response_model=JobSubmitResponse)
    def profile_vcf_route(
        request: Request,
        payload: ProfileVcfPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        vcf_path = Path(payload.vcf_path).expanduser().resolve()
        if not is_path_within_allowed_roots(vcf_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='VCF path is outside allowed upload directory.')
        if not vcf_path.is_file():
            raise HTTPException(status_code=404, detail='VCF file not found.')
        ref_fasta_path = Path(payload.ref_fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(ref_fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='Reference FASTA path is outside allowed upload directory.')
        if not ref_fasta_path.is_file():
            raise HTTPException(status_code=404, detail='Reference FASTA file not found.')
        bam_path: str | None = None
        if payload.bam_path:
            resolved_bam = Path(payload.bam_path).expanduser().resolve()
            if not is_path_within_allowed_roots(resolved_bam, config.allowed_roots):
                raise HTTPException(status_code=400, detail='BAM path is outside allowed upload directory.')
            if not resolved_bam.is_file():
                raise HTTPException(status_code=404, detail='BAM file not found.')
            bam_path = str(resolved_bam)
        _consume_sample_quota(
            request,
            sample_count=1,
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
        enqueue_options = build_enqueue_job_options()
        job = queue.enqueue(
            run_profile_vcf,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            vcf_path=str(vcf_path),
            ref_fasta_path=str(ref_fasta_path),
            sample=payload.sample or defaults.profile_sample_name,
            min_af=payload.min_af if payload.min_af is not None else defaults.profile_min_af,
            min_depth=payload.min_depth if payload.min_depth is not None else defaults.profile_min_depth,
            bam_path=bam_path,
            threads=payload.threads if payload.threads is not None else defaults.profile_threads,
            input_display_name=payload.input_display_name,
            **enqueue_options,
        )
        logger.info('Queue job enqueued: job_id=%s mode=vcf database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @app.post('/api/profile/batch/vcf', response_model=BatchSubmitResponse)
    def profile_batch_vcf_route(
        request: Request,
        payload: BatchProfileVcfPayload,
        queue: Queue = Depends(get_batch_queue),
        _auth: None = Depends(require_api_token),
    ) -> BatchSubmitResponse:
        if len(payload.vcf_paths) != len(payload.sample_names):
            raise HTTPException(
                status_code=422,
                detail='vcf_paths and sample_names must have the same length.',
            )
        input_display_names = _resolve_batch_input_display_names(
            input_paths=payload.vcf_paths,
            input_display_names=payload.input_display_names,
            path_label='vcf_paths',
        )
        artifact_base_names = _derive_unique_artifact_base_names(input_display_names)
        max_batch = sample_limit_per_minute
        if len(payload.vcf_paths) > max_batch:
            raise HTTPException(
                status_code=422,
                detail=f'Batch size {len(payload.vcf_paths)} exceeds the maximum of {max_batch} samples per batch.',
            )
        ref_fasta_path = Path(payload.reference_fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(ref_fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='Reference FASTA path is outside allowed upload directory.')
        if not ref_fasta_path.is_file():
            raise HTTPException(status_code=404, detail='Reference FASTA file not found.')
        _consume_sample_quota(
            request,
            sample_count=len(payload.vcf_paths),
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        enqueue_options = build_enqueue_job_options()
        validated_vcf_inputs: list[tuple[Path, str]] = []
        for vcf_path_str, sample_name in zip(payload.vcf_paths, payload.sample_names):
            vcf_path = Path(vcf_path_str).expanduser().resolve()
            if not is_path_within_allowed_roots(vcf_path, config.allowed_roots):
                raise HTTPException(
                    status_code=400,
                    detail=f'VCF path for sample {sample_name!r} is outside allowed upload directory.',
                )
            if not vcf_path.is_file():
                raise HTTPException(status_code=404, detail=f'VCF file not found for sample {sample_name!r}.')
            validated_vcf_inputs.append((vcf_path, sample_name))

        samples = []
        for index, (vcf_path, sample_name) in enumerate(validated_vcf_inputs):
            job_id = str(uuid4())
            queue.enqueue(
                run_profile_vcf,
                project_db=str(project_db),
                output_dir=str(config.results_dir),
                vcf_path=str(vcf_path),
                ref_fasta_path=str(ref_fasta_path),
                sample=sample_name,
                min_af=payload.min_af,
                min_depth=payload.min_depth,
                bam_path=None,
                threads=payload.threads,
                input_display_name=input_display_names[index],
                artifact_base_name=artifact_base_names[index],
                job_id=job_id,
                **enqueue_options,
            )
            samples.append(BatchSampleEntry(job_id=job_id, sample_name=sample_name))
        logger.info(
            'Batch VCF jobs enqueued: count=%d database_id=%s',
            len(samples),
            project_db.name,
        )
        return BatchSubmitResponse(samples=samples, total=len(samples))

    @app.post('/api/profile/batch/fasta', response_model=BatchSubmitResponse)
    def profile_batch_fasta_route(
        request: Request,
        payload: BatchProfileFastaPayload,
        queue: Queue = Depends(get_batch_queue),
        _auth: None = Depends(require_api_token),
    ) -> BatchSubmitResponse:
        if len(payload.fasta_paths) != len(payload.sample_names):
            raise HTTPException(
                status_code=422,
                detail='fasta_paths and sample_names must have the same length.',
            )
        input_display_names = _resolve_batch_input_display_names(
            input_paths=payload.fasta_paths,
            input_display_names=payload.input_display_names,
            path_label='fasta_paths',
        )
        artifact_base_names = _derive_unique_artifact_base_names(input_display_names)
        max_batch = sample_limit_per_minute
        if len(payload.fasta_paths) > max_batch:
            raise HTTPException(
                status_code=422,
                detail=f'Batch size {len(payload.fasta_paths)} exceeds the maximum of {max_batch} samples per batch.',
            )
        _consume_sample_quota(
            request,
            sample_count=len(payload.fasta_paths),
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        enqueue_options = build_enqueue_job_options()
        validated_fasta_inputs: list[tuple[Path, str]] = []
        for fasta_path_str, sample_name in zip(payload.fasta_paths, payload.sample_names):
            fasta_path = Path(fasta_path_str).expanduser().resolve()
            if not is_path_within_allowed_roots(fasta_path, config.allowed_roots):
                raise HTTPException(
                    status_code=400,
                    detail=f'FASTA path for sample {sample_name!r} is outside allowed upload directory.',
                )
            if not fasta_path.is_file():
                raise HTTPException(status_code=404, detail=f'FASTA file not found for sample {sample_name!r}.')
            validated_fasta_inputs.append((fasta_path, sample_name))

        samples = []
        for index, (fasta_path, sample_name) in enumerate(validated_fasta_inputs):
            job_id = str(uuid4())
            queue.enqueue(
                run_profile_fasta,
                project_db=str(project_db),
                output_dir=str(config.results_dir),
                fasta_path=str(fasta_path),
                sample=sample_name,
                threads=payload.threads,
                input_display_name=input_display_names[index],
                artifact_base_name=artifact_base_names[index],
                job_id=job_id,
                **enqueue_options,
            )
            samples.append(BatchSampleEntry(job_id=job_id, sample_name=sample_name))
        logger.info(
            'Batch FASTA jobs enqueued: count=%d database_id=%s',
            len(samples),
            project_db.name,
        )
        return BatchSubmitResponse(samples=samples, total=len(samples))

    @app.post('/api/regenerate/json', response_model=JobSubmitResponse)
    def regenerate_json_route(
        payload: RegenerateJsonPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        json_path = Path(payload.json_path).expanduser().resolve()
        if not is_path_within_allowed_roots(json_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='JSON path is outside allowed upload/output directory.')
        if not json_path.is_file():
            raise HTTPException(status_code=404, detail='JSON file not found.')

        job = queue.enqueue(
            run_regenerate_json,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            json_path=str(json_path),
            **build_enqueue_job_options(),
        )
        logger.info(
            'Queue job enqueued: job_id=%s mode=regenerate-json database_id=%s',
            job.id,
            project_db.name,
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
        error = _user_facing_error_message(job.exc_info) if status == 'failed' else None
        if status == 'failed' and rq_status in ('stopped', 'canceled'):
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
        if not str(report_path).endswith('.report.html'):
            raise HTTPException(status_code=400, detail='Unsupported report type. Allowed: .report.html.')
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
            raise HTTPException(status_code=400, detail='Artifact path is outside allowed results directory.')
        if not _is_allowed_artifact_path(artifact_path):
            raise HTTPException(
                status_code=400,
                detail='Unsupported artifact type. Allowed: .report.pdf, .results.json, .mutations.tsv, .report.html.',
            )
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail='Artifact not found.')

        media_type = mimetypes.guess_type(str(artifact_path))[0] or 'application/octet-stream'
        return FileResponse(
            str(artifact_path),
            media_type=media_type,
            filename=_derive_download_filename(artifact_path),
        )

    @app.post('/api/artifact-bundle')
    def download_artifact_bundle(
        payload: ArtifactBundlePayload,
        _auth: None = Depends(require_api_token),
    ) -> Response:
        if not payload.paths:
            raise HTTPException(status_code=400, detail='At least one artifact path is required.')

        bundle_bytes = _build_artifact_bundle(
            payload.paths,
            config.results_dir,
            _is_allowed_artifact_path,
        )
        return Response(
            content=bundle_bytes,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="respro-batch-artifacts.zip"'},
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

    frontend_dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    if frontend_dist.is_dir():
        app.mount(
            WEB_BACKEND_CONFIG.defaults.frontend_base_path,
            StaticFiles(directory=str(frontend_dist), html=True),
            name='frontend',
        )

    return app


def _build_artifact_bundle(
    artifact_paths: list[str],
    results_dir: Path,
    is_allowed_artifact_path: Callable[[Path], bool],
) -> bytes:
    """Pack validated result artifacts into one zip archive."""
    buffer = io.BytesIO()
    used_names: set[str] = set()

    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for raw_path in artifact_paths:
            artifact_path = Path(raw_path).expanduser().resolve()
            if not is_path_within_allowed_roots(artifact_path, (results_dir,)):
                raise HTTPException(status_code=400, detail='Artifact path is outside allowed results directory.')
            if not is_allowed_artifact_path(artifact_path):
                raise HTTPException(
                    status_code=400,
                    detail='Unsupported artifact type. Allowed: .report.pdf, .results.json, .mutations.tsv, .report.html.',
                )
            if not artifact_path.is_file():
                raise HTTPException(status_code=404, detail='Artifact not found.')

            archive.write(
                artifact_path,
                arcname=_deduplicate_archive_name(_derive_download_filename(artifact_path), used_names),
            )

    return buffer.getvalue()


def _deduplicate_archive_name(file_name: str, used_names: set[str]) -> str:
    """Keep archive member names unique while preserving readable basenames."""
    if file_name not in used_names:
        used_names.add(file_name)
        return file_name

    path = Path(file_name)
    stem = path.stem
    suffix = ''.join(path.suffixes)
    counter = 1
    while True:
        candidate = f'{stem}_{counter}{suffix}'
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def _resolve_batch_input_display_names(
    *,
    input_paths: list[str],
    input_display_names: list[str] | None,
    path_label: str,
) -> list[str]:
    """Resolve batch input display names, defaulting to uploaded file basenames."""
    if input_display_names is None:
        return [Path(path).name for path in input_paths]
    if len(input_display_names) != len(input_paths):
        raise HTTPException(
            status_code=422,
            detail=f'{path_label} and input_display_names must have the same length.',
        )
    return [Path(name).name for name in input_display_names]


def _derive_unique_artifact_base_names(input_display_names: list[str]) -> list[str]:
    """Build deterministic unique artifact base names from display names."""
    seen_counts: dict[str, int] = {}
    artifact_base_names: list[str] = []
    for display_name in input_display_names:
        base_name = _sanitize_artifact_base_name(display_name)
        duplicate_count = seen_counts.get(base_name, 0)
        if duplicate_count == 0:
            artifact_base_names.append(base_name)
        else:
            artifact_base_names.append(f'{base_name}_{duplicate_count}')
        seen_counts[base_name] = duplicate_count + 1
    return artifact_base_names


def _sanitize_artifact_base_name(input_name: str) -> str:
    """Normalize one input name to a stable report-artifact base name."""
    raw_stem = Path(input_name).stem.strip() or 'profile'
    raw_stem = raw_stem.removesuffix('.results')
    safe_stem = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in raw_stem) or 'profile'
    return safe_stem


def _derive_download_filename(artifact_path: Path) -> str:
    """Map internal artifact names to user-facing download names."""
    file_name = _WEB_TIMESTAMP_TOKEN.sub('', artifact_path.name)
    if file_name.endswith('.report.html'):
        return file_name[:-12] + '.html'
    if file_name.endswith('.report.pdf'):
        return file_name[:-11] + '.pdf'
    if file_name.endswith('.results.json'):
        return file_name[:-13] + '.json'
    if file_name.endswith('.mutations.tsv'):
        return file_name[:-14] + '.tsv'
    return file_name


def _sweep_expired_files(results_dir: Path, uploads_dir: Path, ttl_seconds: int) -> None:
    """
    Delete files in results and uploads dirs that are older than TTL.
    """
    logger = logging.getLogger(__name__)
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
    logger = logging.getLogger(__name__)

    def sweep_loop() -> None:
        while True:
            try:
                ttl_seconds = int(os.getenv('RESPRO_WEB_RESULT_TTL', '86400'))
                _sweep_expired_files(results_dir, uploads_dir, ttl_seconds)
            except Exception as exc:
                logger.debug(f'Error in TTL sweep: {exc}')
            time.sleep(WEB_BACKEND_CONFIG.defaults.sweep_frequency_seconds)

    sweep_thread = threading.Thread(target=sweep_loop, daemon=True)
    sweep_thread.start()


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
    if 'no cds matches above identity threshold' in lowered:
        return 'No sequence match found. The uploaded sequence did not align to any reference CDS with sufficient identity and coverage.'
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


def _resolve_cors_origins(api_token: str) -> list[str]:
    """Resolve CORS origins from env with secure defaults for local development."""
    configured = os.getenv(WEB_ENV.cors_origins, '').strip()
    if configured:
        origins = [value.strip() for value in configured.split(',') if value.strip()]
        if origins:
            return origins

    if api_token:
        raise RuntimeError(
            'RESPRO_WEB_API_TOKEN is set but RESPRO_WEB_CORS_ORIGINS is not configured. '
            'Set explicit allowed origins for token-authenticated deployments.'
        )
    return list(WEB_BACKEND_CONFIG.defaults.cors_local_origins)


def _resolve_upload_rate_limit() -> str:
    """Return the configured upload rate limit string."""
    default = WEB_BACKEND_CONFIG.defaults.upload_rate_limit
    return os.getenv(WEB_ENV.upload_rate_limit, default).strip() or default


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


def _extract_bearer_token(authorization: str | None) -> str:
    """Extract the token value from a Bearer Authorization header."""
    if not authorization:
        return ''
    scheme, _, token = authorization.strip().partition(' ')
    if scheme.lower() != 'bearer' or not token:
        return ''
    return token.strip()


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
    """Use a validated token identity when present, otherwise fall back to client IP."""
    token_identity = _rate_limit_token_identity(request)
    if token_identity:
        return token_identity
    client_host = request.client.host if request.client else ''
    if client_host:
        return f'ip:{client_host}'
    return f'ip:{get_remote_address(request)}'


def _rate_limit_token_identity(request: Request) -> str:
    """Return a hashed limiter identity only for valid configured bearer tokens."""
    configured_token = request.app.state.startup_config.api_token
    if not configured_token:
        return ''
    provided_token = _extract_bearer_token(request.headers.get('Authorization'))
    if not provided_token:
        return ''
    if not hmac.compare_digest(configured_token, provided_token):
        return ''
    digest = hashlib.sha256(provided_token.encode('utf-8')).hexdigest()
    return f'token:{digest}'


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
) -> None:
    """Require bearer token auth when RESPRO_WEB_API_TOKEN is configured."""
    if not config.api_token:
        return
    provided_token = _extract_bearer_token(authorization)
    if not provided_token or not hmac.compare_digest(config.api_token, provided_token):
        raise HTTPException(status_code=401, detail='Unauthorized')


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
