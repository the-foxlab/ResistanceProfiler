"""Artifact and branding routes."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from slowapi import Limiter

from web.backend.artifact_kinds import (
    REPORT_HTML,
    artifact_kind_for_path,
    derive_download_filename,
    is_html_report_path,
    unsupported_artifact_message,
)
from web.backend.models import (
    ArtifactBundlePayload,
    ComparePayload,
    CompareResponse,
    artifact_bundle_payload_model,
)
from web.backend.services.compare import build_comparison_matrix
from web.backend.services.session import (
    Session,
    resolve_owned_path,
)


def build_artifacts_router(
    *,
    results_dir: Path,
    branding_dir: Path,
    allowed_roots: tuple[Path, ...],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
    artifact_bundle_max_artifacts: int,
    limiter: Limiter,
    api_rate_limit: str,
    get_session: Callable[..., Session],
) -> APIRouter:
    """Build artifact download and branding routes.

    All result artifacts (one per kind registered in ``artifact_kinds``) are
    referenced by opaque ``artifact_id`` tokens resolved through the
    session-ownership store rather than by absolute filesystem paths.
    """
    router = APIRouter()

    @router.get('/api/report')
    @limiter.limit(api_rate_limit)
    def open_report(
        request: Request,
        artifact_id: str = Query(...),
    ) -> FileResponse:
        session = get_session(request)
        try:
            report_path = resolve_owned_path(
                prefix='artifact',
                record_id=artifact_id,
                session_hash=session.session_hash,
                allowed_roots=allowed_roots,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail='Report not found.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not is_html_report_path(report_path):
            raise HTTPException(
                status_code=400,
                detail=f'Unsupported report type. Allowed: {REPORT_HTML.internal_suffix}.',
            )
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail='Report not found.')
        return FileResponse(str(report_path), media_type='text/html')

    @router.get('/api/artifact')
    @limiter.limit(api_rate_limit)
    def download_artifact(
        request: Request,
        artifact_id: str = Query(...),
    ) -> FileResponse:
        session = get_session(request)
        try:
            artifact_path = resolve_owned_path(
                prefix='artifact',
                record_id=artifact_id,
                session_hash=session.session_hash,
                allowed_roots=allowed_roots,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail='Artifact not found.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

        resolved_paths = _resolve_artifact_ids(
            payload.artifact_ids,
            session.session_hash,
            allowed_roots,
        )
        bundle_bytes = _build_artifact_bundle(
            resolved_paths,
            results_dir,
            is_allowed_artifact_path,
            part=part,
        )
        return Response(
            content=bundle_bytes,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="respro-batch-artifacts.zip"'},
        )

    # FastAPI inspects annotations when registering a route; bind the per-app
    # model before SlowAPI wraps the endpoint and the router inspects it.
    download_artifact_bundle.__annotations__['payload'] = artifact_bundle_payload_model(
        artifact_bundle_max_artifacts,
    )
    router.post('/api/artifact-bundle')(limiter.limit(api_rate_limit)(download_artifact_bundle))

    @router.get('/api/branding/logo.svg')
    def branding_logo() -> FileResponse:
        logo_path = branding_dir / 'logo.svg'
        if not logo_path.is_file():
            raise HTTPException(status_code=404, detail='Logo not found.')
        return FileResponse(str(logo_path), media_type='image/svg+xml')

    @router.get('/api/branding/favicon.svg')
    def branding_favicon() -> FileResponse:
        favicon_path = branding_dir / 'favicon.svg'
        if not favicon_path.is_file():
            raise HTTPException(status_code=404, detail='Favicon not found.')
        return FileResponse(str(favicon_path), media_type='image/svg+xml')

    @router.post('/api/compare')
    @limiter.limit(api_rate_limit)
    def compare_samples(
        request: Request,
        payload: ComparePayload,
    ) -> CompareResponse:
        session = get_session(request)
        if not payload.artifact_ids:
            raise HTTPException(status_code=400, detail='At least one artifact id is required.')

        resolved_paths = _resolve_artifact_ids(
            payload.artifact_ids,
            session.session_hash,
            allowed_roots,
        )
        try:
            return build_comparison_matrix(
                result_json_paths=resolved_paths,
                results_dir=results_dir,
                is_path_within_allowed_roots=is_path_within_allowed_roots,
                is_allowed_artifact_path=is_allowed_artifact_path,
                non_synonymous_only=payload.non_synonymous_only,
                db_hits_only=payload.db_hits_only,
            )
        except ValueError as exc:
            msg = str(exc)
            if 'not found' in msg.lower():
                raise HTTPException(status_code=404, detail=msg) from exc
            raise HTTPException(status_code=400, detail=msg) from exc

    return router


def _resolve_artifact_ids(
    artifact_ids: list[str],
    session_hash: str,
    allowed_roots: tuple[Path, ...],
) -> list[Path]:
    """Resolve a list of artifact IDs to owned, path-confined result files."""
    resolved: list[Path] = []
    for artifact_id in artifact_ids:
        try:
            path = resolve_owned_path(
                prefix='artifact',
                record_id=artifact_id,
                session_hash=session_hash,
                allowed_roots=allowed_roots,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail='Artifact not found.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolved.append(path)
    return resolved


def _build_artifact_bundle(
    artifact_paths: list[Path],
    results_dir: Path,
    is_allowed_artifact_path: Callable[[Path], bool],
    *,
    part: int | None = None,
) -> bytes:
    """Pack validated result artifacts into one zip archive."""
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
