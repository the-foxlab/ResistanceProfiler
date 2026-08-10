"""Catalog routes for rules, mutations, and databases."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from slowapi import Limiter

from web.backend.models import ApiEnvelope
from web.backend.services.browse import list_databases, list_rules


def build_catalog_router(
    *,
    project_databases_dir: Path,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build catalog browsing routes."""
    router = APIRouter()

    @router.get('/api/rules', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def rules(
        request: Request,
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
    ) -> ApiEnvelope:
        del request
        try:
            data = list_rules(
                project_databases_dir,
                database_id,
                reference_filter=reference,
            )
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get('/api/mutations', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def mutations(
        request: Request,
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
    ) -> ApiEnvelope:
        # Alias for /api/rules - delegates to the same handler.
        return rules(request=request, database_id=database_id, reference=reference)

    @router.get('/api/databases', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def databases(
        request: Request,
    ) -> ApiEnvelope:
        del request
        try:
            data = list_databases(project_databases_dir)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
