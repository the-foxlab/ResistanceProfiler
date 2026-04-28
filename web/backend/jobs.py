"""
RQ job functions for background profiling.

These are top-level importable functions so that RQ can serialize them via
pickle. All Path arguments are passed as strings for serialization safety and
converted inside the function.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from rq import get_current_job

from web.backend.services.profile import profile_fasta, profile_vcf
from web.backend.services.regenerate import regenerate_from_json

logger = logging.getLogger(__name__)


def run_profile_fasta(
    *,
    project_db: str,
    output_dir: str,
    fasta_path: str,
    sample: str,
    threads: int,
    aligner: str,
) -> dict:
    """RQ job wrapper for FASTA profiling."""
    return _run_job_with_logging(
        mode='fasta',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: profile_fasta(
            project_db=Path(project_db),
            output_dir=Path(output_dir),
            fasta_path=Path(fasta_path),
            sample=sample,
            threads=threads,
            aligner=aligner,
        ),
    )


def run_profile_vcf(
    *,
    project_db: str,
    output_dir: str,
    vcf_path: str,
    ref_fasta_path: str,
    sample: str,
    min_af: float,
    min_depth: int,
    bam_path: str | None,
    threads: int,
    aligner: str,
) -> dict:
    """RQ job wrapper for VCF profiling."""
    return _run_job_with_logging(
        mode='vcf',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: profile_vcf(
            project_db=Path(project_db),
            output_dir=Path(output_dir),
            vcf_path=Path(vcf_path),
            ref_fasta_path=Path(ref_fasta_path),
            sample=sample,
            min_af=min_af,
            min_depth=min_depth,
            bam_path=Path(bam_path) if bam_path else None,
            threads=threads,
            aligner=aligner,
        ),
    )


def run_regenerate_json(
    *,
    project_db: str,
    output_dir: str,
    json_path: str,
) -> dict:
    """RQ job wrapper for regenerating report artifacts from result JSON."""
    return _run_job_with_logging(
        mode='regenerate-json',
        database_id=Path(project_db).name,
        sample='regenerate',
        job_func=lambda: regenerate_from_json(
            project_db=Path(project_db),
            output_dir=Path(output_dir),
            json_path=Path(json_path),
        ),
    )


def _run_job_with_logging(
    *,
    mode: str,
    database_id: str,
    sample: str,
    job_func: Callable[[], dict],
) -> dict:
    """Run one queued task with explicit lifecycle logs for debugging and operations."""
    current_job = get_current_job()
    job_id = current_job.id if current_job is not None else 'unknown'
    logger.info(
        'Queue job started: job_id=%s mode=%s database_id=%s sample=%s',
        job_id,
        mode,
        database_id,
        sample,
    )
    try:
        result = job_func()
    except ValueError:
        _mark_current_job_non_retryable(current_job)
        logger.exception(
            'Queue job failed: job_id=%s mode=%s database_id=%s sample=%s',
            job_id,
            mode,
            database_id,
            sample,
        )
        raise
    except Exception:
        logger.exception(
            'Queue job failed: job_id=%s mode=%s database_id=%s sample=%s',
            job_id,
            mode,
            database_id,
            sample,
        )
        raise

    logger.info(
        'Queue job finished: job_id=%s mode=%s database_id=%s sample=%s',
        job_id,
        mode,
        database_id,
        sample,
    )
    return result


def _mark_current_job_non_retryable(current_job) -> None:
    """Disable retries for deterministic user-input errors handled by this worker job."""
    if current_job is None:
        return
    if getattr(current_job, 'retries_left', None) is None:
        return
    current_job.retries_left = 0
