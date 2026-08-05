"""Profile routes for single and batch submissions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from rq import Queue
from slowapi import Limiter

from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.jobs import run_profile_fasta, run_profile_vcf
from web.backend.models import (
    BatchProfileFastaPayload,
    BatchProfileVcfPayload,
    BatchSampleEntry,
    BatchSubmitResponse,
    JobSubmitResponse,
    ProfileFastaPayload,
    ProfileVcfPayload,
)
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue

logger = logging.getLogger(__name__)


def build_profile_router(
    *,
    config,
    sample_limit_per_minute: int,
    require_api_token: Callable[..., None],
    consume_sample_quota: Callable[[Request, int, int], None],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
    resolve_project_db_path,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build profile submission routes."""
    router = APIRouter()

    @router.post('/api/profile/fasta', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_fasta_route(
        request: Request,
        payload: ProfileFastaPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        fasta_path = Path(payload.fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='FASTA path is outside allowed upload directory.')
        if not fasta_path.is_file():
            raise HTTPException(status_code=404, detail='FASTA file not found.')
        consume_sample_quota(
            request,
            sample_count=1,
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
        enqueue_options = build_enqueue_job_options()
        job = queue.enqueue(
            run_profile_fasta,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            fasta_path=str(fasta_path),
            sample=payload.sample or defaults.profile_sample_name,
            threads=payload.threads if payload.threads is not None else defaults.profile_threads,
            input_display_name=payload.input_display_name,
            **enqueue_options,
        )
        logger.info('Queue job enqueued: job_id=%s mode=fasta database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @router.post('/api/profile/vcf', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_vcf_route(
        request: Request,
        payload: ProfileVcfPayload,
        queue: Queue = Depends(get_queue),
        _auth: None = Depends(require_api_token),
    ) -> JobSubmitResponse:
        vcf_path = Path(payload.vcf_path).expanduser().resolve()
        if not is_path_within_allowed_roots(vcf_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='VCF path is outside allowed upload directory.')
        if not vcf_path.is_file():
            raise HTTPException(status_code=404, detail='VCF file not found.')
        ref_fasta_path = Path(payload.ref_fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(ref_fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='Reference FASTA path is outside allowed upload directory.')
        if not ref_fasta_path.is_file():
            raise HTTPException(status_code=404, detail='Reference FASTA file not found.')
        bam_path: str | None = None
        if payload.bam_path:
            resolved_bam = Path(payload.bam_path).expanduser().resolve()
            if not is_path_within_allowed_roots(resolved_bam, config.allowed_roots):
                raise HTTPException(status_code=400, detail='BAM path is outside allowed upload directory.')
            if not resolved_bam.is_file():
                raise HTTPException(status_code=404, detail='BAM file not found.')
            bam_path = str(resolved_bam)
        consume_sample_quota(
            request,
            sample_count=1,
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
        enqueue_options = build_enqueue_job_options()
        job = queue.enqueue(
            run_profile_vcf,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            vcf_path=str(vcf_path),
            ref_fasta_path=str(ref_fasta_path),
            sample=payload.sample or defaults.profile_sample_name,
            min_af=payload.min_af if payload.min_af is not None else defaults.profile_min_af,
            min_depth=payload.min_depth if payload.min_depth is not None else defaults.profile_min_depth,
            bam_path=bam_path,
            threads=payload.threads if payload.threads is not None else defaults.profile_threads,
            input_display_name=payload.input_display_name,
            **enqueue_options,
        )
        logger.info('Queue job enqueued: job_id=%s mode=vcf database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @router.post('/api/profile/batch/vcf', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_batch_vcf_route(
        request: Request,
        payload: BatchProfileVcfPayload,
        queue: Queue = Depends(get_batch_queue),
        _auth: None = Depends(require_api_token),
    ) -> BatchSubmitResponse:
        if len(payload.vcf_paths) != len(payload.sample_names):
            raise HTTPException(
                status_code=422,
                detail='vcf_paths and sample_names must have the same length.',
            )
        if payload.bam_paths is not None and len(payload.bam_paths) != len(payload.vcf_paths):
            raise HTTPException(
                status_code=422,
                detail='bam_paths and vcf_paths must have the same length.',
            )
        input_display_names = _resolve_batch_input_display_names(
            input_paths=payload.vcf_paths,
            input_display_names=payload.input_display_names,
            path_label='vcf_paths',
        )
        artifact_base_names = _derive_unique_artifact_base_names(input_display_names)
        max_batch = sample_limit_per_minute
        if len(payload.vcf_paths) > max_batch:
            raise HTTPException(
                status_code=422,
                detail=f'Batch size {len(payload.vcf_paths)} exceeds the maximum of {max_batch} samples per batch.',
            )
        ref_fasta_path = Path(payload.reference_fasta_path).expanduser().resolve()
        if not is_path_within_allowed_roots(ref_fasta_path, config.allowed_roots):
            raise HTTPException(status_code=400, detail='Reference FASTA path is outside allowed upload directory.')
        if not ref_fasta_path.is_file():
            raise HTTPException(status_code=404, detail='Reference FASTA file not found.')
        consume_sample_quota(
            request,
            sample_count=len(payload.vcf_paths),
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        enqueue_options = build_enqueue_job_options()
        validated_vcf_inputs = _validate_batch_paths(
            input_paths=payload.vcf_paths,
            sample_names=payload.sample_names,
            allowed_roots=config.allowed_roots,
            path_kind='VCF',
            is_path_within_allowed_roots=is_path_within_allowed_roots,
        )
        validated_bam_paths = _validate_batch_bam_paths(
            bam_paths=payload.bam_paths,
            sample_count=len(payload.vcf_paths),
            allowed_roots=config.allowed_roots,
            is_path_within_allowed_roots=is_path_within_allowed_roots,
        )

        samples = []
        for index, (vcf_path, sample_name) in enumerate(validated_vcf_inputs):
            job_id = str(uuid4())
            queue.enqueue(
                run_profile_vcf,
                project_db=str(project_db),
                output_dir=str(config.results_dir),
                vcf_path=str(vcf_path),
                ref_fasta_path=str(ref_fasta_path),
                sample=sample_name,
                min_af=payload.min_af,
                min_depth=payload.min_depth,
                bam_path=validated_bam_paths[index],
                threads=payload.threads,
                input_display_name=input_display_names[index],
                artifact_base_name=artifact_base_names[index],
                job_id=job_id,
                **enqueue_options,
            )
            samples.append(BatchSampleEntry(job_id=job_id, sample_name=sample_name))
        logger.info(
            'Batch VCF jobs enqueued: count=%d database_id=%s',
            len(samples),
            project_db.name,
        )
        return BatchSubmitResponse(samples=samples, total=len(samples))

    @router.post('/api/profile/batch/fasta', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_batch_fasta_route(
        request: Request,
        payload: BatchProfileFastaPayload,
        queue: Queue = Depends(get_batch_queue),
        _auth: None = Depends(require_api_token),
    ) -> BatchSubmitResponse:
        if len(payload.fasta_paths) != len(payload.sample_names):
            raise HTTPException(
                status_code=422,
                detail='fasta_paths and sample_names must have the same length.',
            )
        input_display_names = _resolve_batch_input_display_names(
            input_paths=payload.fasta_paths,
            input_display_names=payload.input_display_names,
            path_label='fasta_paths',
        )
        artifact_base_names = _derive_unique_artifact_base_names(input_display_names)
        max_batch = sample_limit_per_minute
        if len(payload.fasta_paths) > max_batch:
            raise HTTPException(
                status_code=422,
                detail=f'Batch size {len(payload.fasta_paths)} exceeds the maximum of {max_batch} samples per batch.',
            )
        consume_sample_quota(
            request,
            sample_count=len(payload.fasta_paths),
            sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        enqueue_options = build_enqueue_job_options()
        validated_fasta_inputs = _validate_batch_paths(
            input_paths=payload.fasta_paths,
            sample_names=payload.sample_names,
            allowed_roots=config.allowed_roots,
            path_kind='FASTA',
            is_path_within_allowed_roots=is_path_within_allowed_roots,
        )

        samples = []
        for index, (fasta_path, sample_name) in enumerate(validated_fasta_inputs):
            job_id = str(uuid4())
            queue.enqueue(
                run_profile_fasta,
                project_db=str(project_db),
                output_dir=str(config.results_dir),
                fasta_path=str(fasta_path),
                sample=sample_name,
                threads=payload.threads,
                input_display_name=input_display_names[index],
                artifact_base_name=artifact_base_names[index],
                job_id=job_id,
                **enqueue_options,
            )
            samples.append(BatchSampleEntry(job_id=job_id, sample_name=sample_name))
        logger.info(
            'Batch FASTA jobs enqueued: count=%d database_id=%s',
            len(samples),
            project_db.name,
        )
        return BatchSubmitResponse(samples=samples, total=len(samples))

    return router


def _resolve_batch_input_display_names(
    *,
    input_paths: list[str],
    input_display_names: list[str] | None,
    path_label: str,
) -> list[str]:
    """Resolve batch input display names, defaulting to uploaded file basenames."""
    if input_display_names is None:
        return [Path(path).name for path in input_paths]
    if len(input_display_names) != len(input_paths):
        raise HTTPException(
            status_code=422,
            detail=f'{path_label} and input_display_names must have the same length.',
        )
    return [Path(name).name for name in input_display_names]


def _validate_batch_paths(
    *,
    input_paths: list[str],
    sample_names: list[str],
    allowed_roots: tuple[Path, ...],
    path_kind: Literal['VCF', 'FASTA'],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
) -> list[tuple[Path, str]]:
    """Resolve and validate per-sample input paths for batch profiling routes."""
    validated_inputs: list[tuple[Path, str]] = []
    for input_path_str, sample_name in zip(input_paths, sample_names):
        input_path = Path(input_path_str).expanduser().resolve()
        if not is_path_within_allowed_roots(input_path, allowed_roots):
            raise HTTPException(
                status_code=400,
                detail=f'{path_kind} path for sample {sample_name!r} is outside allowed upload directory.',
            )
        if not input_path.is_file():
            raise HTTPException(status_code=404, detail=f'{path_kind} file not found for sample {sample_name!r}.')
        validated_inputs.append((input_path, sample_name))
    return validated_inputs


def _validate_batch_bam_paths(
    *,
    bam_paths: list[str | None] | None,
    sample_count: int,
    allowed_roots: tuple[Path, ...],
    is_path_within_allowed_roots: Callable[[Path, tuple[Path, ...]], bool],
) -> list[str | None]:
    """Resolve and validate per-sample optional BAM paths for the batch VCF route.

    Returns one resolved BAM path string (or ``None``) per sample, positionally aligned with
    ``vcf_paths``. A ``None`` entry means "no BAM for that sample" — coverage-gap analysis is
    skipped for it, mirroring the single-VCF ``bam_path`` option. Content matching between the
    BAM and the VCF/reference is intentionally not checked here: like the single-VCF path, that
    is deferred to the CLI coverage step, which warns-and-skips unmatched contigs.
    """
    if bam_paths is None:
        return [None] * sample_count
    resolved: list[str | None] = []
    for bam_path_str in bam_paths:
        if bam_path_str is None:
            resolved.append(None)
            continue
        resolved_bam = Path(bam_path_str).expanduser().resolve()
        if not is_path_within_allowed_roots(resolved_bam, allowed_roots):
            raise HTTPException(status_code=400, detail='BAM path is outside allowed upload directory.')
        if not resolved_bam.is_file():
            raise HTTPException(status_code=404, detail='BAM file not found.')
        resolved.append(str(resolved_bam))
    return resolved


def _derive_unique_artifact_base_names(input_display_names: list[str]) -> list[str]:
    """Build deterministic unique artifact base names from display names."""
    seen_counts: dict[str, int] = {}
    artifact_base_names: list[str] = []
    for display_name in input_display_names:
        base_name = _sanitize_artifact_base_name(display_name)
        duplicate_count = seen_counts.get(base_name, 0)
        if duplicate_count == 0:
            artifact_base_names.append(base_name)
        else:
            artifact_base_names.append(f'{base_name}_{duplicate_count}')
        seen_counts[base_name] = duplicate_count + 1
    return artifact_base_names


def _sanitize_artifact_base_name(input_name: str) -> str:
    """Normalize one input name to a stable report-artifact base name."""
    raw_stem = Path(input_name).stem.strip() or 'profile'
    raw_stem = raw_stem.removesuffix('.results')
    safe_stem = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in raw_stem) or 'profile'
    return safe_stem
