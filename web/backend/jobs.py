"""
RQ job functions for background profiling.

These are top-level importable functions so that RQ can serialize them via
pickle. All Path arguments are passed as strings for serialization safety and
converted inside the function.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rq import get_current_job

logger = logging.getLogger(__name__)


def run_profile_fasta(
    *,
    project_db: str,
    output_dir: str,
    fasta_path: str,
    sample: str,
    threads: int,
    input_display_name: str | None = None,
    artifact_base_name: str | None = None,
) -> dict:
    """RQ job wrapper for FASTA profiling."""
    output_html_path = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=artifact_base_name or input_display_name or Path(fasta_path).name,
    )
    return _run_job_with_logging(
        mode='fasta',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: _run_profile_fasta_subprocess(
            project_db=Path(project_db),
            output_html_path=output_html_path,
            fasta_path=Path(fasta_path),
            sample=sample,
            threads=threads,
            input_display_name=input_display_name,
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
    input_display_name: str | None = None,
    artifact_base_name: str | None = None,
) -> dict:
    """RQ job wrapper for VCF profiling."""
    output_html_path = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=artifact_base_name or input_display_name or Path(vcf_path).name,
    )
    return _run_job_with_logging(
        mode='vcf',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: _run_profile_vcf_subprocess(
            project_db=Path(project_db),
            output_html_path=output_html_path,
            vcf_path=Path(vcf_path),
            ref_fasta_path=Path(ref_fasta_path),
            sample=sample,
            min_af=min_af,
            min_depth=min_depth,
            bam_path=Path(bam_path) if bam_path else None,
            threads=threads,
            input_display_name=input_display_name,
        ),
    )


def run_regenerate_json(
    *,
    project_db: str,
    output_dir: str,
    json_path: str,
) -> dict:
    """RQ job wrapper for regenerating report artifacts from result JSON."""
    output_html_path = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=Path(json_path).name,
    )
    return _run_job_with_logging(
        mode='regenerate-json',
        database_id=Path(project_db).name,
        sample='regenerate',
        job_func=lambda: _run_regenerate_json_subprocess(
            project_db=Path(project_db),
            output_html_path=output_html_path,
            json_path=Path(json_path),
        ),
    )


def _run_profile_fasta_subprocess(
    *,
    project_db: Path,
    output_html_path: Path,
    fasta_path: Path,
    sample: str,
    threads: int,
    input_display_name: str | None,
) -> dict:
    """Execute FASTA profiling through the respro CLI and return the web API payload."""
    command = [
        *_respro_command_prefix(),
        'fasta',
        '--project',
        str(project_db),
        '--fasta',
        str(fasta_path),
        '--sample',
        sample,
        '--output',
        str(output_html_path),
        '--threads',
        str(threads),
        '--cache',
        '--export',
        'json',
        '--export',
        'tabular',
        '--export',
        'pdf',
    ]
    if input_display_name:
        command.extend(['--input-display-name', input_display_name])

    _run_respro_command(command)
    artifacts = _artifact_paths_for_html(output_html_path)
    run_payload = _load_run_payload(artifacts['json'])
    return {
        'mode': 'fasta',
        'run_id': None,
        'database_id': project_db.name,
        'database_path': str(project_db.resolve()),
        'input_path': str(fasta_path.resolve()),
        'sample_name': run_payload.get('sample_name', sample),
        'created_at': run_payload.get('created_at', ''),
        'reference_name': run_payload.get('reference_name', ''),
        'query_name': _read_first_fasta_name(fasta_path),
        'report_html_path': str(artifacts['html']),
        'report_json_path': str(artifacts['json']),
        'report_tabular_path': str(artifacts['tabular']),
        'report_pdf_path': str(artifacts['pdf']),
        'resistance_hits': int(run_payload.get('resistance_hits', 0) or 0),
        'total_variants': int(run_payload.get('total_variants', 0) or 0),
    }


def _run_profile_vcf_subprocess(
    *,
    project_db: Path,
    output_html_path: Path,
    vcf_path: Path,
    ref_fasta_path: Path,
    sample: str,
    min_af: float,
    min_depth: int,
    bam_path: Path | None,
    threads: int,
    input_display_name: str | None,
) -> dict:
    """Execute VCF profiling through the respro CLI and return the web API payload."""
    command = [
        *_respro_command_prefix(),
        'vcf',
        '--project',
        str(project_db),
        '--vcf',
        str(vcf_path),
        '--ref-fasta',
        str(ref_fasta_path),
        '--sample',
        sample,
        '--output',
        str(output_html_path),
        '--min-af',
        str(min_af),
        '--min-depth',
        str(min_depth),
        '--threads',
        str(threads),
        '--cache',
        '--export',
        'json',
        '--export',
        'tabular',
        '--export',
        'pdf',
    ]
    if input_display_name:
        command.extend(['--input-display-name', input_display_name])
    if bam_path is not None:
        command.extend(['--bam', str(bam_path)])

    _run_respro_command(command)
    artifacts = _artifact_paths_for_html(output_html_path)
    run_payload = _load_run_payload(artifacts['json'])
    return {
        'mode': 'vcf',
        'run_id': None,
        'database_id': project_db.name,
        'database_path': str(project_db.resolve()),
        'input_path': str(vcf_path.resolve()),
        'reference_fasta_path': str(ref_fasta_path.resolve()),
        'sample_name': run_payload.get('sample_name', sample),
        'created_at': run_payload.get('created_at', ''),
        'reference_name': run_payload.get('reference_name', ''),
        'query_name': _read_first_fasta_name(ref_fasta_path),
        'report_html_path': str(artifacts['html']),
        'report_json_path': str(artifacts['json']),
        'report_tabular_path': str(artifacts['tabular']),
        'report_pdf_path': str(artifacts['pdf']),
        'resistance_hits': int(run_payload.get('resistance_hits', 0) or 0),
        'total_variants': int(run_payload.get('total_variants', 0) or 0),
    }


def _run_regenerate_json_subprocess(
    *,
    project_db: Path,
    output_html_path: Path,
    json_path: Path,
) -> dict:
    """Execute JSON-based regeneration through the respro CLI and return the web API payload."""
    command = [
        *_respro_command_prefix(),
        'regenerate',
        '--project',
        str(project_db),
        '--json',
        str(json_path),
        '--output',
        str(output_html_path),
        '--export',
        'json',
        '--export',
        'tabular',
        '--export',
        'pdf',
    ]
    _run_respro_command(command)

    artifacts = _artifact_paths_for_html(output_html_path)
    run_payload = _load_run_payload(artifacts['json'])
    return {
        'mode': 'regenerate-json',
        'sample_name': run_payload.get('sample_name', ''),
        'created_at': run_payload.get('created_at', ''),
        'reference_name': run_payload.get('reference_name', ''),
        'query_name': '',
        'report_html_path': str(artifacts['html']),
        'report_json_path': str(artifacts['json']),
        'report_tabular_path': str(artifacts['tabular']),
        'report_pdf_path': str(artifacts['pdf']),
        'resistance_hits': int(run_payload.get('resistance_hits', 0) or 0),
        'total_variants': int(run_payload.get('total_variants', 0) or 0),
    }


def _respro_command_prefix() -> list[str]:
    """Return a command prefix that invokes the local respro module with the active interpreter."""
    return [sys.executable, '-m', 'respro.cli.main']


def _run_respro_command(command: list[str]) -> None:
    """Execute one respro subprocess command and raise a user-facing error on failure."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f'Failed to execute respro CLI subprocess: {exc}') from exc

    if completed.returncode == 0:
        return

    stderr_text = (completed.stderr or '').strip()
    stdout_text = (completed.stdout or '').strip()
    details = stderr_text or stdout_text or f'command exited with code {completed.returncode}'
    raise ValueError(details)


def _build_web_output_html_path(*, output_dir: Path, input_name: str) -> Path:
    """Return a unique HTML report path so session history keeps every run."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_stem = Path(input_name).stem.strip() or 'profile'
    raw_stem = raw_stem.removesuffix('.results')
    safe_stem = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in raw_stem) or 'profile'
    run_stamp = datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')
    return output_dir / f'{safe_stem}.{run_stamp}.report.html'


def _artifact_paths_for_html(output_html_path: Path) -> dict[str, Path]:
    """Resolve expected report artifact paths for one HTML report target."""
    html_path = Path(output_html_path)
    if html_path.name.endswith('.report.html'):
        stem = html_path.name[:-12]
    elif html_path.suffix == '.html':
        stem = html_path.stem
    else:
        stem = html_path.name
    return {
        'html': html_path,
        'json': html_path.parent / f'{stem}.results.json',
        'tabular': html_path.parent / f'{stem}.mutations.tsv',
        'pdf': html_path.parent / f'{stem}.report.pdf',
    }


def _load_run_payload(results_json_path: Path) -> dict:
    """Load the run block from one exported results JSON file."""
    path = Path(results_json_path)
    if not path.is_file():
        raise ValueError(f'Expected report artifact not found: {path}')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid report JSON artifact generated by respro: {exc.msg}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Invalid report JSON artifact generated by respro: payload must be an object')
    run_payload = payload.get('run')
    if not isinstance(run_payload, dict):
        raise ValueError("Invalid report JSON artifact generated by respro: missing object key 'run'")
    return run_payload


def _read_first_fasta_name(path: Path) -> str:
    """Return the first FASTA header token as a lightweight query-name hint."""
    try:
        with Path(path).open('r', encoding='utf-8') as handle:
            for line in handle:
                if not line.startswith('>'):
                    continue
                return line[1:].strip().split(maxsplit=1)[0]
    except OSError:
        return ''
    return ''


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
