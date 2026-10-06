"""Catalog routes for rules, mutations, and databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from slowapi import Limiter

from web.backend.models import (
    ApiEnvelope,
    CompareDatabasesPayload,
)
from web.backend.services.browse import list_databases, list_rules
from web.backend.services.compare_databases import (
    compare_databases,
    list_database_reference_accessions,
    list_shared_references,
)


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

    @router.get('/api/databases/shared-references', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def shared_references(
        request: Request,
        ids: str = Query(default=''),
    ) -> ApiEnvelope:
        del request
        database_ids = [item.strip() for item in ids.split(',') if item.strip()]
        try:
            data = list_shared_references(project_databases_dir, database_ids)
            return ApiEnvelope(data={'items': data, 'count': len(data)})
        except (FileNotFoundError, ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get('/api/databases/reference-accessions', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def reference_accessions(
        request: Request,
        ids: str = Query(default=''),
    ) -> ApiEnvelope:
        """Return each database's reference accessions for comparability checks."""
        del request
        database_ids = [item.strip() for item in ids.split(',') if item.strip()]
        try:
            items = list_database_reference_accessions(project_databases_dir, database_ids)
            return ApiEnvelope(data={'items': items, 'count': len(items)})
        except (FileNotFoundError, ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post('/api/databases/compare', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def compare_databases_route(
        request: Request,
        payload: CompareDatabasesPayload,
    ) -> ApiEnvelope:
        del request
        try:
            data = compare_databases(
                project_databases_dir,
                payload.database_ids,
                payload.accession,
            )
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
