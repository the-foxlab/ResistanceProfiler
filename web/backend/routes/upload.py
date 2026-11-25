"""File upload routes for FASTA, VCF, BAM, and results JSON inputs."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from slowapi import Limiter

from web.backend.errors import user_facing_error_message
from web.backend.models import UploadResponse
from web.backend.routes.common import get_deployment_mode, get_session
from web.backend.services.session import SessionBackendError, record_upload
from web.backend.services.upload import UploadType, save_upload_stream

logger = logging.getLogger(__name__)


def build_upload_router(
    *,
    uploads_dir: Path,
    limiter: Limiter,
    upload_rate_limit: str,
) -> APIRouter:
    """Build streamed upload endpoints."""
    router = APIRouter()

    async def _handle_upload(request: Request, file: UploadFile, file_type: UploadType) -> UploadResponse:
        session = get_session(request)
        deployment_mode = get_deployment_mode(request)
        saved_path: Path | None = None
        try:
            saved_path, size_bytes = await save_upload_stream(file, file_type, uploads_dir)
            upload_id = await run_in_threadpool(
                record_upload,
                session_hash=session.session_hash,
                canonical_path=saved_path,
                file_type=file_type,
                deployment_mode=deployment_mode,
            )
            return UploadResponse(upload_id=upload_id, file_type=file_type, size_bytes=size_bytes)
        except ValueError as exc:
            if saved_path is not None:
                saved_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=user_facing_error_message(str(exc))) from exc
        except SessionBackendError as exc:
            if saved_path is not None:
                saved_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail='Session store unavailable.') from exc
        except Exception as exc:
            if saved_path is not None:
                saved_path.unlink(missing_ok=True)
            logger.exception('Unexpected server error during %s upload', file_type)
            raise HTTPException(status_code=500, detail='The upload failed on the server.') from exc
        except BaseException:
            if saved_path is not None:
                saved_path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @router.post('/api/upload/fasta', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_fasta(request: Request, file: UploadFile = File(...)) -> UploadResponse:
        return await _handle_upload(request, file, 'fasta')

    @router.post('/api/upload/vcf', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_vcf(request: Request, file: UploadFile = File(...)) -> UploadResponse:
        return await _handle_upload(request, file, 'vcf')

    @router.post('/api/upload/bam', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_bam(request: Request, file: UploadFile = File(...)) -> UploadResponse:
        return await _handle_upload(request, file, 'bam')

    @router.post('/api/upload/json', response_model=UploadResponse)
    @limiter.limit(upload_rate_limit)
    async def upload_json(request: Request, file: UploadFile = File(...)) -> UploadResponse:
        return await _handle_upload(request, file, 'json')

    return router
