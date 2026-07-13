"""Health and configuration routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.models import ApiEnvelope
from web.backend.startup_config import StartupConfig


def build_health_router(
    *,
    config: StartupConfig,
    sample_limit_per_minute: int,
    require_api_token: Callable[..., None],
    build_readiness_payload: Callable[[StartupConfig], ApiEnvelope],
) -> APIRouter:
    """Build health, readiness, and UI config routes."""
    router = APIRouter()

    @router.get('/api/health', response_model=ApiEnvelope)
    def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'service': WEB_BACKEND_CONFIG.defaults.service_name,
            },
            status='ok',
        )

    @router.get('/api/readiness', response_model=ApiEnvelope)
    def readiness() -> JSONResponse | ApiEnvelope:
        payload = build_readiness_payload(config)
        if payload.status == 'ok':
            return payload
        return JSONResponse(status_code=503, content=payload.model_dump())

    @router.get('/api/ui/config', response_model=ApiEnvelope)
    def ui_config(_auth: None = Depends(require_api_token)) -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'batch_max_samples': sample_limit_per_minute,
                'sample_limit_per_minute': sample_limit_per_minute,
            }
        )

    return router


def build_legal_router(*, impressum_html: str | None) -> APIRouter:
    """Build public legal-notice routes (no API token — DSGVO requires reachability)."""
    router = APIRouter()

    @router.get('/legal', response_class=HTMLResponse)
    def legal() -> HTMLResponse:
        if impressum_html is None:
            raise HTTPException(status_code=404)
        return HTMLResponse(content=impressum_html)

    @router.get('/api/ui/legal', response_model=ApiEnvelope)
    def legal_indicator() -> ApiEnvelope:
        return ApiEnvelope(data={'enabled': impressum_html is not None}, status='ok')

    return router
