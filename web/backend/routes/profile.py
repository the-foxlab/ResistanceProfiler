"""Profile routes for single and batch FASTA/VCF submissions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from rq import Queue
from slowapi import Limiter

from web.backend.artifact_kinds import sanitize_artifact_stem
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.jobs import run_profile_fasta, run_profile_vcf
from web.backend.models import (
    BatchProfileFastaPayload,
    BatchProfileVcfPayload,
    BatchSubmitResponse,
    JobSubmitResponse,
    ProfileFastaPayload,
    ProfileVcfPayload,
)
from web.backend.project_dbs import resolve_project_db_path
from web.backend.queue import build_enqueue_job_options, get_batch_queue, get_queue
from web.backend.rate_limit import consume_sample_quota
from web.backend.routes.common import (
    enqueue_batch_jobs,
    get_session,
    resolve_owned_file,
    validate_batch_lengths_and_cap,
)
from web.backend.services.session import record_job
from web.backend.startup_config import StartupConfig

logger = logging.getLogger(__name__)


def build_profile_router(
    *,
    config: StartupConfig,
    sample_limit_per_minute: int,
    limiter: Limiter,
    api_rate_limit: str,
) -> APIRouter:
    """Build single and batch profile submission routes."""
    router = APIRouter()

    @router.post('/api/profile/fasta', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_fasta_route(
        request: Request,
        payload: ProfileFastaPayload,
        queue: Queue = Depends(get_queue),
    ) -> JobSubmitResponse:
        session = get_session(request)
        fasta_path: Path | None = None
        fasta_id: str | None = None
        if not payload.use_example:
            if not payload.fasta_id:
                raise HTTPException(status_code=422, detail='fasta_id is required when use_example is false.')
            fasta_path = _resolve_upload(payload.fasta_id, session.session_hash, config.allowed_roots, 'FASTA')
            fasta_id = payload.fasta_id

        consume_sample_quota(request, sample_count=1, sample_limit_per_minute=sample_limit_per_minute)
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
        job = queue.enqueue(
            run_profile_fasta,
            project_db=str(project_db),
            output_dir=str(config.results_dir),
            fasta_path=str(fasta_path) if fasta_path else None,
            use_example=payload.use_example,
            sample=payload.sample or defaults.profile_sample_name,
            threads=payload.threads if payload.threads is not None else defaults.profile_threads,
            input_display_name=payload.input_display_name,
            **build_enqueue_job_options(),
        )
        record_job(session_hash=session.session_hash, upload_ids=[fasta_id] if fasta_id else [], job_id=job.id)
        logger.info('Queue job enqueued: job_id=%s mode=fasta database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @router.post('/api/profile/vcf', response_model=JobSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_vcf_route(
        request: Request,
        payload: ProfileVcfPayload,
        queue: Queue = Depends(get_queue),
    ) -> JobSubmitResponse:
        session = get_session(request)
        vcf_path = _resolve_upload(payload.vcf_id, session.session_hash, config.allowed_roots, 'VCF')
        ref_fasta_path = _resolve_upload(
            payload.reference_id, session.session_hash, config.allowed_roots, 'Reference FASTA',
        )
        bam_path = (
            str(_resolve_upload(payload.bam_id, session.session_hash, config.allowed_roots, 'BAM'))
            if payload.bam_id
            else None
        )

        consume_sample_quota(request, sample_count=1, sample_limit_per_minute=sample_limit_per_minute)
        project_db = resolve_project_db_path(config.project_databases_dir, payload.database_id)
        defaults = WEB_BACKEND_CONFIG.defaults
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
            **build_enqueue_job_options(),
        )
        upload_ids = [payload.vcf_id, payload.reference_id, *([payload.bam_id] if payload.bam_id else [])]
        record_job(session_hash=session.session_hash, upload_ids=upload_ids, job_id=job.id)
        logger.info('Queue job enqueued: job_id=%s mode=vcf database_id=%s', job.id, project_db.name)
        return JobSubmitResponse(job_id=job.id)

    @router.post('/api/profile/batch/vcf', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_batch_vcf_route(
        request: Request,
        payload: BatchProfileVcfPayload,
        queue: Queue = Depends(get_batch_queue),
    ) -> BatchSubmitResponse:
        session = get_session(request)
        display_names = validate_batch_lengths_and_cap(
            input_ids=payload.vcf_ids,
            sample_names=payload.sample_names,
            input_display_names=payload.input_display_names,
            id_field='vcf_ids',
            max_batch=sample_limit_per_minute,
            extra_aligned=('bam_ids', payload.bam_ids),
        )
        artifact_base_names = _derive_unique_artifact_base_names(display_names)
        ref_fasta_path = _resolve_upload(
            payload.reference_id, session.session_hash, config.allowed_roots, 'Reference FASTA',
        )
        consume_sample_quota(
            request, sample_count=len(payload.vcf_ids), sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        validated_vcfs = _validate_batch_uploads(
            payload.vcf_ids, payload.sample_names, session.session_hash, config.allowed_roots, 'VCF',
        )
        bam_paths = (
            [
                str(_resolve_upload(bam_id, session.session_hash, config.allowed_roots, 'BAM'))
                if bam_id is not None
                else None
                for bam_id in payload.bam_ids
            ]
            if payload.bam_ids is not None
            else [None] * len(payload.vcf_ids)
        )

        job_specs = [
            (
                sample_name,
                [payload.vcf_ids[index], payload.reference_id],
                {
                    'project_db': str(project_db),
                    'output_dir': str(config.results_dir),
                    'vcf_path': str(vcf_path),
                    'ref_fasta_path': str(ref_fasta_path),
                    'sample': sample_name,
                    'min_af': payload.min_af,
                    'min_depth': payload.min_depth,
                    'bam_path': bam_paths[index],
                    'threads': payload.threads,
                    'input_display_name': display_names[index],
                    'artifact_base_name': artifact_base_names[index],
                },
            )
            for index, (vcf_path, sample_name) in enumerate(validated_vcfs)
        ]
        samples = enqueue_batch_jobs(
            queue=queue,
            session_hash=session.session_hash,
            job_func=run_profile_vcf,
            job_specs=job_specs,
            enqueue_options=build_enqueue_job_options(),
        )
        logger.info('Batch VCF jobs enqueued: count=%d database_id=%s', len(samples), project_db.name)
        return BatchSubmitResponse(samples=samples, total=len(samples))

    @router.post('/api/profile/batch/fasta', response_model=BatchSubmitResponse)
    @limiter.limit(api_rate_limit)
    def profile_batch_fasta_route(
        request: Request,
        payload: BatchProfileFastaPayload,
        queue: Queue = Depends(get_batch_queue),
    ) -> BatchSubmitResponse:
        session = get_session(request)
        display_names = validate_batch_lengths_and_cap(
            input_ids=payload.fasta_ids,
            sample_names=payload.sample_names,
            input_display_names=payload.input_display_names,
            id_field='fasta_ids',
            max_batch=sample_limit_per_minute,
        )
        artifact_base_names = _derive_unique_artifact_base_names(display_names)
        consume_sample_quota(
            request, sample_count=len(payload.fasta_ids), sample_limit_per_minute=sample_limit_per_minute,
        )
        project_db = resolve_project_db_path(config.project_databases_dir, payload.db_path)
        validated_fastas = _validate_batch_uploads(
            payload.fasta_ids, payload.sample_names, session.session_hash, config.allowed_roots, 'FASTA',
        )

        job_specs = [
            (
                sample_name,
                [payload.fasta_ids[index]],
                {
                    'project_db': str(project_db),
                    'output_dir': str(config.results_dir),
                    'fasta_path': str(fasta_path),
                    'sample': sample_name,
                    'threads': payload.threads,
                    'input_display_name': display_names[index],
                    'artifact_base_name': artifact_base_names[index],
                },
            )
            for index, (fasta_path, sample_name) in enumerate(validated_fastas)
        ]
        samples = enqueue_batch_jobs(
            queue=queue,
            session_hash=session.session_hash,
            job_func=run_profile_fasta,
            job_specs=job_specs,
            enqueue_options=build_enqueue_job_options(),
        )
        logger.info('Batch FASTA jobs enqueued: count=%d database_id=%s', len(samples), project_db.name)
        return BatchSubmitResponse(samples=samples, total=len(samples))

    return router


def _resolve_upload(upload_id: str, session_hash: str, allowed_roots: tuple[Path, ...], label: str) -> Path:
    return resolve_owned_file(
        prefix='upload',
        record_id=upload_id,
        session_hash=session_hash,
        allowed_roots=allowed_roots,
        not_found_detail=f'{label} file not found.',
    )


def _validate_batch_uploads(
    upload_ids: list[str],
    sample_names: list[str],
    session_hash: str,
    allowed_roots: tuple[Path, ...],
    path_kind: Literal['VCF', 'FASTA'],
) -> list[tuple[Path, str]]:
    return [
        (
            resolve_owned_file(
                prefix='upload',
                record_id=upload_id,
                session_hash=session_hash,
                allowed_roots=allowed_roots,
                not_found_detail=f'{path_kind} file not found for sample {sample_name!r}.',
            ),
            sample_name,
        )
        for upload_id, sample_name in zip(upload_ids, sample_names)
    ]


def _derive_unique_artifact_base_names(input_display_names: list[str]) -> list[str]:
    """Build deterministic unique artifact stems from display names."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for display_name in input_display_names:
        base = sanitize_artifact_stem(display_name)
        count = seen.get(base, 0)
        result.append(base if count == 0 else f'{base}_{count}')
        seen[base] = count + 1
    return result
