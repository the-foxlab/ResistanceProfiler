"""File upload routes for FASTA, VCF, BAM, and results JSON inputs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from slowapi import Limiter

from web.backend.errors import user_facing_error_message
from web.backend.models import UploadResponse
from web.backend.routes.common import get_session
from web.backend.services.session import record_upload
from web.backend.services.upload import UploadType, save_upload_stream


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
        try:
            saved_path, size_bytes = await save_upload_stream(file, file_type, uploads_dir)
            upload_id = record_upload(
                session_hash=session.session_hash,
                canonical_path=saved_path,
                file_type=file_type,
            )
            return UploadResponse(upload_id=upload_id, file_type=file_type, size_bytes=size_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=user_facing_error_message(str(exc))) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=user_facing_error_message(str(exc))) from exc
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
