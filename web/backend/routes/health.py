"""Health and configuration routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.models import ApiEnvelope
from web.backend.startup_config import ContactEmailConfig, ImprintConfig, StartupConfig


def build_health_router(
    *,
    config: StartupConfig,
    sample_limit_per_minute: int,
    build_readiness_payload: Callable[[StartupConfig], ApiEnvelope],
    version: str,
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
    def ui_config() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                'batch_max_samples': sample_limit_per_minute,
                'sample_limit_per_minute': sample_limit_per_minute,
                'cli_version': version,
            }
        )

    return router


def build_legal_router(*, imprint: ImprintConfig | None) -> APIRouter:
    """Build public legal-notice routes (no API token — DSGVO requires reachability).

    Path mode serves the stored HTML at ``/legal``; URL mode 302-redirects ``/legal``
    to the external imprint so a bookmarked link still lands on the hosted page. The
    ``/api/ui/legal`` indicator carries ``kind`` and ``url`` so the frontend can render
    a direct external link instead of routing through ``/legal``.
    """
    router = APIRouter()

    @router.get('/legal', response_class=HTMLResponse, response_model=None)
    def legal() -> HTMLResponse | RedirectResponse:
        if imprint is None:
            raise HTTPException(status_code=404)
        if imprint.kind == 'url':
            # ``imprint.url`` is guaranteed non-None for kind='url' by ImprintConfig construction.
            return RedirectResponse(url=imprint.url, status_code=302)
        # Path mode — ``imprint.html`` is guaranteed non-None for kind='path'.
        return HTMLResponse(content=imprint.html)

    @router.get('/api/ui/legal', response_model=ApiEnvelope)
    def legal_indicator() -> ApiEnvelope:
        if imprint is None:
            return ApiEnvelope(data={'enabled': False, 'kind': 'path'}, status='ok')
        if imprint.kind == 'url':
            return ApiEnvelope(data={'enabled': True, 'kind': 'url', 'url': imprint.url}, status='ok')
        return ApiEnvelope(data={'enabled': True, 'kind': 'path'}, status='ok')

    return router


def build_contact_router(*, contact_email: ContactEmailConfig | None) -> APIRouter:
    """Build the public contact-e-mail indicator route (no API token — reachability is public).

    ``GET /api/ui/contact`` returns ``{'enabled': bool, 'email': str | None}`` so the
    frontend can render a ``mailto:`` footer link only when a contact address is
    configured. Mirrors :func:`build_legal_router` in keeping the endpoint public.
    """
    router = APIRouter()

    @router.get('/api/ui/contact', response_model=ApiEnvelope)
    def contact_indicator() -> ApiEnvelope:
        if contact_email is None:
            return ApiEnvelope(data={'enabled': False, 'email': None}, status='ok')
        return ApiEnvelope(
            data={'enabled': True, 'email': contact_email.email},
            status='ok',
        )

    return router
