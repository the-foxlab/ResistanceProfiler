"""RQ job entrypoints for background profiling and report regeneration.

Job arguments are primitive strings/numbers/booleans so RQ's ``JSONSerializer``
round-trips them safely without pickle.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rq import get_current_job

from web.backend.artifact_kinds import RESULTS_JSON, artifact_paths_for_html, sanitize_artifact_stem
from web.backend.queue import resolve_subprocess_timeout_seconds

logger = logging.getLogger(__name__)

_EXPORT_ARGS = ('--export', 'json', '--export', 'pdf', '--export', 'tsv')


def run_profile_fasta(
    *,
    project_db: str,
    output_dir: str,
    fasta_path: str | None,
    sample: str,
    threads: int,
    input_display_name: str | None = None,
    artifact_base_name: str | None = None,
    use_example: bool = False,
) -> dict:
    """RQ job entrypoint for FASTA profiling."""
    fasta = Path(fasta_path) if fasta_path else None
    output_html = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=artifact_base_name or input_display_name or (fasta.name if fasta else 'example'),
    )
    extra_args = [
        *(['--example'] if use_example else ['--fasta', str(fasta)]),
        '--sample', sample,
        '--threads', str(threads),
        '--cache',
        *(['--input-display-name', input_display_name] if input_display_name else []),
    ]
    return _run_job_with_logging(
        mode='fasta',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: _execute_respro_job(
            mode='fasta',
            subcommand='fasta',
            extra_args=extra_args,
            project_db=Path(project_db),
            output_html_path=output_html,
            default_sample=sample,
            query_name=_read_first_fasta_name(fasta) if fasta else 'example',
            include_profile_fields=True,
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
    """RQ job entrypoint for VCF profiling."""
    output_html = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=artifact_base_name or input_display_name or Path(vcf_path).name,
    )
    extra_args = [
        '--vcf', str(vcf_path),
        '--ref-fasta', str(ref_fasta_path),
        '--sample', sample,
        '--min-af', str(min_af),
        '--min-depth', str(min_depth),
        '--threads', str(threads),
        '--cache',
        *(['--input-display-name', input_display_name] if input_display_name else []),
        *(['--bam', str(bam_path)] if bam_path else []),
    ]
    return _run_job_with_logging(
        mode='vcf',
        database_id=Path(project_db).name,
        sample=sample,
        job_func=lambda: _execute_respro_job(
            mode='vcf',
            subcommand='vcf',
            extra_args=extra_args,
            project_db=Path(project_db),
            output_html_path=output_html,
            default_sample=sample,
            query_name=_read_first_fasta_name(Path(ref_fasta_path)),
            include_profile_fields=True,
        ),
    )


def run_regenerate_json(
    *,
    project_db: str,
    output_dir: str,
    json_path: str,
) -> dict:
    """RQ job entrypoint for regenerating reports from an uploaded results JSON."""
    output_html = _build_web_output_html_path(
        output_dir=Path(output_dir),
        input_name=Path(json_path).name,
    )
    return _run_job_with_logging(
        mode='regenerate-json',
        database_id=Path(project_db).name,
        sample='regenerate',
        job_func=lambda: _execute_respro_job(
            mode='regenerate-json',
            subcommand='regenerate',
            extra_args=['--json', str(json_path)],
            project_db=Path(project_db),
            output_html_path=output_html,
            default_sample='',
            query_name='',
            include_profile_fields=False,
        ),
    )


def _execute_respro_job(
    *,
    mode: str,
    subcommand: str,
    extra_args: list[str],
    project_db: Path,
    output_html_path: Path,
    default_sample: str,
    query_name: str,
    include_profile_fields: bool,
) -> dict:
    """Invoke ``respro <subcommand>`` and assemble the web API job result payload."""
    command = [
        sys.executable, '-m', 'respro.cli.main', subcommand,
        '--project', str(project_db),
        *extra_args,
        '--output', str(output_html_path),
        *_EXPORT_ARGS,
    ]
    _run_respro_command(command)
    artifacts = artifact_paths_for_html(output_html_path)
    run_payload = _load_run_payload(artifacts[RESULTS_JSON.result_field])

    result: dict = {'mode': mode}
    if include_profile_fields:
        result['run_id'] = None
        result['database_id'] = project_db.name
    result.update({
        'sample_name': run_payload.get('sample_name', default_sample),
        'created_at': run_payload.get('created_at', ''),
        'reference_name': run_payload.get('reference_name', ''),
        'query_name': query_name,
        **{field: str(path) for field, path in artifacts.items()},
        'resistance_hits': int(run_payload.get('resistance_hits', 0) or 0),
        'total_variants': int(run_payload.get('total_variants', 0) or 0),
    })
    return result


class _SubprocessCanceledError(BaseException):
    """Internal signal raised when the worker receives SIGTERM while running a subprocess."""


_SUBPROCESS_TERMINATE_GRACE_SECONDS = 2.0


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Terminate *proc*'s entire process group via SIGTERM → wait → SIGKILL."""
    pgid = proc.pid
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=_SUBPROCESS_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_SUBPROCESS_TERMINATE_GRACE_SECONDS)
        return
    # Ensure any lingering descendants in the session/process group are also cleaned up.
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGKILL)


def _run_respro_command(command: list[str]) -> None:
    """Execute one ``respro`` CLI subprocess bounded by the hard subprocess timeout."""
    timeout_seconds = resolve_subprocess_timeout_seconds()
    is_main_thread = threading.current_thread() is threading.main_thread()
    previous_sigterm: Any = None
    canceling = False
    proc: subprocess.Popen[str] | None = None
    try:
        if is_main_thread:
            def _handle_sigterm(_signum: int, _frame: Any) -> None:
                nonlocal canceling
                if not canceling:
                    canceling = True
                    raise _SubprocessCanceledError()

            previous_sigterm = signal.signal(signal.SIGTERM, _handle_sigterm)

        try:
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                raise RuntimeError(f'Failed to execute respro CLI subprocess: {exc}') from exc

            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            canceling = True
            if proc is not None:
                _terminate_process_group(proc)
            raise RuntimeError(f'respro CLI subprocess timed out after {timeout_seconds} seconds.') from exc
        except _SubprocessCanceledError as exc:
            canceling = True
            if proc is not None:
                _terminate_process_group(proc)
            raise RuntimeError('respro CLI subprocess canceled.') from exc
        except BaseException:
            canceling = True
            if proc is not None:
                _terminate_process_group(proc)
            raise
    finally:
        try:
            if is_main_thread and previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
        finally:
            if proc is not None:
                if proc.stdout is not None:
                    with contextlib.suppress(OSError):
                        proc.stdout.close()
                if proc.stderr is not None:
                    with contextlib.suppress(OSError):
                        proc.stderr.close()

    if proc.returncode != 0:
        details = (stderr or '').strip() or (stdout or '').strip()
        raise ValueError(details or f'command exited with code {proc.returncode}')


def _build_web_output_html_path(*, output_dir: Path, input_name: str) -> Path:
    """Return a timestamped HTML report path inside *output_dir* so runs do not collide."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_artifact_stem(input_name)
    run_stamp = datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')
    return output_dir / f'{stem}.{run_stamp}.report.html'


def _load_run_payload(results_json_path: Path) -> dict:
    """Load and validate the ``run`` object from an exported results JSON artifact."""
    path = Path(results_json_path)
    if not path.is_file():
        raise ValueError('Expected report artifact not found.')
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
                if line.startswith('>'):
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
    """Execute *job_func* with lifecycle logging (``sample`` is intentionally never logged)."""
    del sample  # kept for signature stability; sample names are sensitive metadata
    current_job = get_current_job()
    job_id = current_job.id if current_job is not None else 'unknown'
    logger.info('Queue job started: job_id=%s mode=%s database_id=%s', job_id, mode, database_id)
    try:
        result = job_func()
    except Exception as exc:
        if isinstance(exc, ValueError) and current_job is not None and getattr(current_job, 'retries_left', None) is not None:
            current_job.retries_left = 0
        logger.exception('Queue job failed: job_id=%s mode=%s database_id=%s', job_id, mode, database_id)
        raise

    logger.info('Queue job finished: job_id=%s mode=%s database_id=%s', job_id, mode, database_id)
    return result
