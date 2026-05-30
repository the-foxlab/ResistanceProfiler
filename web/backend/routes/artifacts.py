"""Artifact and branding routes."""

from __future__ import annotations

import io
import mimetypes
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from web.backend.models import ArtifactBundlePayload

_WEB_TIMESTAMP_TOKEN = re.compile(
    r'\.(\d{20})(?=\.(?:report\.html|report\.pdf|results\.json)$)'
)


def build_artifacts_router(
    *,
    results_dir: Path,
    branding_dir: Path,
    require_api_token: Callable[..., None],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
) -> APIRouter:
    """Build artifact download and branding routes."""
    router = APIRouter()

    @router.get('/api/report')
    def open_report(
        path: str = Query(...),
        _auth: None = Depends(require_api_token),
    ) -> FileResponse:
        report_path = Path(path).expanduser().resolve()
        if not is_path_within_allowed_roots(report_path, (results_dir,)):
            raise HTTPException(status_code=400, detail='Report path is outside allowed output directory.')
        if not str(report_path).endswith('.report.html'):
            raise HTTPException(status_code=400, detail='Unsupported report type. Allowed: .report.html.')
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail='Report not found.')
        return FileResponse(str(report_path), media_type='text/html')

    @router.get('/api/artifact')
    def download_artifact(
        path: str = Query(...),
        _auth: None = Depends(require_api_token),
    ) -> FileResponse:
        artifact_path = Path(path).expanduser().resolve()
        if not is_path_within_allowed_roots(artifact_path, (results_dir,)):
            raise HTTPException(status_code=400, detail='Artifact path is outside allowed results directory.')
        if not is_allowed_artifact_path(artifact_path):
            raise HTTPException(
                status_code=400,
                detail='Unsupported artifact type. Allowed: .report.pdf, .results.json, .report.html.',
            )
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail='Artifact not found.')

        media_type = mimetypes.guess_type(str(artifact_path))[0] or 'application/octet-stream'
        return FileResponse(
            str(artifact_path),
            media_type=media_type,
            filename=_derive_download_filename(artifact_path),
        )

    @router.post('/api/artifact-bundle')
    def download_artifact_bundle(
        payload: ArtifactBundlePayload,
        _auth: None = Depends(require_api_token),
    ) -> Response:
        if not payload.paths:
            raise HTTPException(status_code=400, detail='At least one artifact path is required.')

        bundle_bytes = _build_artifact_bundle(
            payload.paths,
            results_dir,
            is_path_within_allowed_roots,
            is_allowed_artifact_path,
        )
        return Response(
            content=bundle_bytes,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="respro-batch-artifacts.zip"'},
        )

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

    return router


def _build_artifact_bundle(
    artifact_paths: list[str],
    results_dir: Path,
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    is_allowed_artifact_path: Callable[[Path], bool],
) -> bytes:
    """Pack validated result artifacts into one zip archive."""
    buffer = io.BytesIO()
    used_names: set[str] = set()

    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for raw_path in artifact_paths:
            artifact_path = Path(raw_path).expanduser().resolve()
            if not is_path_within_allowed_roots(artifact_path, (results_dir,)):
                raise HTTPException(status_code=400, detail='Artifact path is outside allowed results directory.')
            if not is_allowed_artifact_path(artifact_path):
                raise HTTPException(
                    status_code=400,
                    detail='Unsupported artifact type. Allowed: .report.pdf, .results.json, .report.html.',
                )
            if not artifact_path.is_file():
                raise HTTPException(status_code=404, detail='Artifact not found.')

            archive.write(
                artifact_path,
                arcname=_deduplicate_archive_name(_derive_download_filename(artifact_path), used_names),
            )

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


def _derive_download_filename(artifact_path: Path) -> str:
    """Map internal artifact names to user-facing download names."""
    file_name = _WEB_TIMESTAMP_TOKEN.sub('', artifact_path.name)
    if file_name.endswith('.report.html'):
        return file_name[:-12] + '.html'
    if file_name.endswith('.report.pdf'):
        return file_name[:-11] + '.pdf'
    if file_name.endswith('.results.json'):
        return file_name[:-13] + '.json'
    return file_name
