"""Catalog routes for browsing rules, mutations, databases, and database comparisons."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from slowapi import Limiter

from web.backend.config import parse_csv_tokens
from web.backend.models import ApiEnvelope, CompareDatabasesPayload
from web.backend.services.browse import list_databases, list_rules
from web.backend.services.compare_databases import (
    compare_databases,
    list_database_reference_accessions,
    list_shared_references,
)

_BROWSE_ERRORS = (FileNotFoundError, ValueError, OSError)
_COMPARE_ERRORS = (FileNotFoundError, ValueError, OSError, sqlite3.Error)


def _envelope_or_400(loader: Callable[[], dict], error_types: tuple[type[Exception], ...]) -> ApiEnvelope:
    try:
        return ApiEnvelope(data=loader())
    except error_types as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_catalog_router(
    *,
    project_databases_dir: Path,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build catalog browsing and database-comparison routes."""
    router = APIRouter()

    def _load_rules(database_id: str | None, reference: str | None) -> ApiEnvelope:
        return _envelope_or_400(
            lambda: list_rules(project_databases_dir, database_id, reference_filter=reference),
            _BROWSE_ERRORS,
        )

    @router.get('/api/rules', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def rules(
        request: Request,
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
    ) -> ApiEnvelope:
        del request
        return _load_rules(database_id, reference)

    @router.get('/api/mutations', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def mutations(
        request: Request,
        database_id: str | None = Query(default=None),
        reference: str | None = Query(default=None),
    ) -> ApiEnvelope:
        del request
        return _load_rules(database_id, reference)

    @router.get('/api/databases', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def databases(request: Request) -> ApiEnvelope:
        del request
        return _envelope_or_400(lambda: list_databases(project_databases_dir), _BROWSE_ERRORS)

    @router.get('/api/databases/shared-references', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def shared_references(request: Request, ids: str = Query(default='')) -> ApiEnvelope:
        del request
        database_ids = parse_csv_tokens(ids)
        return _envelope_or_400(
            lambda: {'items': (items := list_shared_references(project_databases_dir, database_ids)), 'count': len(items)},
            _COMPARE_ERRORS,
        )

    @router.get('/api/databases/reference-accessions', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def reference_accessions(request: Request, ids: str = Query(default='')) -> ApiEnvelope:
        del request
        database_ids = parse_csv_tokens(ids)
        return _envelope_or_400(
            lambda: {'items': (items := list_database_reference_accessions(project_databases_dir, database_ids)), 'count': len(items)},
            _COMPARE_ERRORS,
        )

    @router.post('/api/databases/compare', response_model=ApiEnvelope)
    @limiter.limit(api_rate_limit)
    def compare_databases_route(request: Request, payload: CompareDatabasesPayload) -> ApiEnvelope:
        del request
        return _envelope_or_400(
            lambda: compare_databases(project_databases_dir, payload.database_ids, payload.accession),
            _COMPARE_ERRORS,
        )

    return router
