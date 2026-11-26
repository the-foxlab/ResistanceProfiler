"""Artifact download, bundle export, comparison, and branding routes."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from slowapi import Limiter

from web.backend.artifact_kinds import (
    REPORT_HTML,
    artifact_kind_for_path,
    derive_download_filename,
    is_allowed_artifact_path,
    is_html_report_path,
    unsupported_artifact_message,
)
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.errors import user_facing_error_message
from web.backend.models import (
    ArtifactBundlePayload,
    ComparePayload,
    CompareResponse,
    artifact_bundle_payload_model,
)
from web.backend.routes.common import get_deployment_mode, get_session, resolve_owned_file
from web.backend.services.compare import build_comparison_matrix
from web.backend.startup_config import is_path_within_allowed_roots

_OPAQUE_ID_MAX_LEN = WEB_BACKEND_CONFIG.defaults.opaque_id_max_length
REPORT_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src 'self' data: blob: https://pubchem.ncbi.nlm.nih.gov; "
    "font-src 'self' data:; "
    "connect-src https://translate.googleapis.com; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'; "
    "object-src 'none'"
)


def build_artifacts_router(
    *,
    results_dir: Path,
    branding_dir: Path,
    allowed_roots: tuple[Path, ...],
    artifact_bundle_max_artifacts: int,
    limiter: Limiter,
    api_rate_limit: str,
    project_databases_dir: Path | None = None,
) -> APIRouter:
    """Build report, artifact download, ZIP bundle, branding, and sample-compare routes."""
    router = APIRouter()
    artifact_roots = tuple(
        root for root in allowed_roots if is_path_within_allowed_roots(root, (results_dir,))
    )

    @router.get('/api/report')
    @limiter.limit(api_rate_limit)
    def open_report(
        request: Request,
        artifact_id: str = Query(..., max_length=_OPAQUE_ID_MAX_LEN),
    ) -> FileResponse:
        session = get_session(request)
        report_path = resolve_owned_file(
            prefix='artifact',
            record_id=artifact_id,
            session_hash=session.session_hash,
            allowed_roots=artifact_roots,
            not_found_detail='Report not found.',
            require_file=False,
            deployment_mode=get_deployment_mode(request),
        )
        if not is_html_report_path(report_path):
            raise HTTPException(
                status_code=400,
                detail=f'Unsupported report type. Allowed: {REPORT_HTML.internal_suffix}.',
            )
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail='Report not found.')
        return FileResponse(
            str(report_path),
            media_type='text/html',
            headers={
                'Content-Security-Policy': REPORT_CSP,
                'X-Frame-Options': 'SAMEORIGIN',
            },
        )

    @router.get('/api/artifact')
    @limiter.limit(api_rate_limit)
    def download_artifact(
        request: Request,
        artifact_id: str = Query(..., max_length=_OPAQUE_ID_MAX_LEN),
    ) -> FileResponse:
        session = get_session(request)
        artifact_path = resolve_owned_file(
            prefix='artifact',
            record_id=artifact_id,
            session_hash=session.session_hash,
            allowed_roots=artifact_roots,
            not_found_detail='Artifact not found.',
            require_file=False,
            deployment_mode=get_deployment_mode(request),
        )
        artifact_kind = artifact_kind_for_path(artifact_path)
        if artifact_kind is None:
            raise HTTPException(status_code=400, detail=unsupported_artifact_message())
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail='Artifact not found.')
        return FileResponse(
            str(artifact_path),
            media_type=artifact_kind.media_type,
            filename=derive_download_filename(artifact_path),
        )

    def download_artifact_bundle(
        request: Request,
        payload: ArtifactBundlePayload,
        part: int | None = Query(default=None, ge=1, le=4294967295),
    ) -> Response:
        session = get_session(request)
        if not payload.artifact_ids:
            raise HTTPException(status_code=400, detail='At least one artifact id is required.')
        if len(payload.artifact_ids) > artifact_bundle_max_artifacts:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Too many artifacts ({len(payload.artifact_ids)}). '
                    f'At most {artifact_bundle_max_artifacts} can be downloaded at once.'
                ),
            )
        unique_ids = list(dict.fromkeys(payload.artifact_ids))
        resolved_paths = _resolve_artifact_ids(
            unique_ids,
            session.session_hash,
            artifact_roots,
            deployment_mode=get_deployment_mode(request),
        )
        return Response(
            content=_build_artifact_bundle(resolved_paths, part=part),
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="respro-batch-artifacts.zip"'},
        )

    # Bind the per-app payload model before SlowAPI wraps and FastAPI inspects annotations.
    download_artifact_bundle.__annotations__['payload'] = artifact_bundle_payload_model(artifact_bundle_max_artifacts)
    router.post('/api/artifact-bundle')(limiter.limit(api_rate_limit)(download_artifact_bundle))

    @router.get('/api/branding/logo.svg')
    def branding_logo() -> FileResponse:
        return _serve_branding_svg(branding_dir / 'logo.svg', 'Logo not found.')

    @router.get('/api/branding/favicon.svg')
    def branding_favicon() -> FileResponse:
        return _serve_branding_svg(branding_dir / 'favicon.svg', 'Favicon not found.')

    @router.post('/api/compare')
    @limiter.limit(api_rate_limit)
    def compare_samples(request: Request, payload: ComparePayload) -> CompareResponse:
        session = get_session(request)
        if not payload.artifact_ids:
            raise HTTPException(status_code=400, detail='At least one artifact id is required.')
        resolved_paths = _resolve_artifact_ids(
            payload.artifact_ids,
            session.session_hash,
            artifact_roots,
            deployment_mode=get_deployment_mode(request),
        )
        try:
            return build_comparison_matrix(
                result_json_paths=resolved_paths,
                results_dir=results_dir,
                is_path_within_allowed_roots=is_path_within_allowed_roots,
                is_allowed_artifact_path=is_allowed_artifact_path,
                non_synonymous_only=payload.non_synonymous_only,
                db_hits_only=payload.db_hits_only,
                project_databases_dir=project_databases_dir,
            )
        except ValueError as exc:
            msg = str(exc)
            raise HTTPException(
                status_code=404 if 'not found' in msg.lower() else 400,
                detail=user_facing_error_message(msg),
            ) from exc

    return router


def _serve_branding_svg(path: Path, not_found_detail: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(str(path), media_type='image/svg+xml')


def _resolve_artifact_ids(
    artifact_ids: list[str],
    session_hash: str,
    allowed_roots: tuple[Path, ...],
    *,
    deployment_mode: str | None = None,
) -> list[Path]:
    return [
        resolve_owned_file(
            prefix='artifact',
            record_id=artifact_id,
            session_hash=session_hash,
            allowed_roots=allowed_roots,
            not_found_detail='Artifact not found.',
            require_file=False,
            deployment_mode=deployment_mode,
        )
        for artifact_id in artifact_ids
    ]


def _build_artifact_bundle(artifact_paths: list[Path], *, part: int | None = None) -> bytes:
    """Pack validated result artifacts into one ZIP archive."""
    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact_path in artifact_paths:
            if not is_allowed_artifact_path(artifact_path):
                raise HTTPException(status_code=400, detail=unsupported_artifact_message())
            if not artifact_path.is_file():
                raise HTTPException(status_code=404, detail='Artifact not found.')
            name = derive_download_filename(artifact_path)
            if part is not None:
                name = f'part{part}-{name}'
            archive.write(artifact_path, arcname=_deduplicate_archive_name(name, used_names))
    return buffer.getvalue()


def _deduplicate_archive_name(file_name: str, used_names: set[str]) -> str:
    """Ensure unique ZIP member names by appending ``_1``, ``_2``, ... on collision."""
    if file_name not in used_names:
        used_names.add(file_name)
        return file_name
    path = Path(file_name)
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while (candidate := f'{stem}_{counter}{suffix}') in used_names:
        counter += 1
    used_names.add(candidate)
    return candidate
