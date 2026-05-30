"""Catalog routes for rules, mutations, and databases."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from web.backend.models import ApiEnvelope
from web.backend.services.browse import list_databases, list_rules


def build_catalog_router(
    *,
    project_databases_dir: Path,
    require_api_token: Callable[..., None],
) -> APIRouter:
    """Build catalog browsing routes."""
    router = APIRouter()

    @router.get('/api/rules', response_model=ApiEnvelope)
    def rules(
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
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
    def mutations(
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
        _auth: None = Depends(require_api_token),
    ) -> ApiEnvelope:
        # Alias for /api/rules - delegates to the same handler.
        return rules(database_id=database_id, reference=reference, _auth=_auth)

    @router.get('/api/databases', response_model=ApiEnvelope)
    def databases(_auth: None = Depends(require_api_token)) -> ApiEnvelope:
        try:
            data = list_databases(project_databases_dir)
            return ApiEnvelope(data=data)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
