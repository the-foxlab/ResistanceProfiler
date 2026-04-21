"""Shared web backend configuration loaded from a TOML defaults file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebEnvKeys:
    """Environment variable keys used by the web backend."""

    data_dir: str = 'RESPRO_WEB_DATA_DIR'
    project_db: str = 'RESPRO_WEB_PROJECT_DB'
    results_db: str = 'RESPRO_WEB_RESULTS_DB'
    api_token: str = 'RESPRO_WEB_API_TOKEN'
    allowed_roots: str = 'RESPRO_WEB_ALLOWED_ROOTS'
    frontend_dist: str = 'RESPRO_FRONTEND_DIST'
    cors_origins: str = 'RESPRO_WEB_CORS_ORIGINS'
    upload_rate_limit: str = 'RESPRO_WEB_UPLOAD_RATE_LIMIT'
    host: str = 'RESPRO_WEB_HOST'
    port: str = 'RESPRO_WEB_PORT'
    redis_url: str = 'REDIS_URL'
    job_timeout: str = 'RESPRO_WEB_JOB_TIMEOUT'


@dataclass(frozen=True)
class WebDefaults:
    """Runtime defaults used by the web backend when env vars are unset."""

    web_host: str
    web_port: int
    redis_url: str
    job_timeout_seconds: int
    upload_rate_limit: str
    cors_local_origins: tuple[str, ...]
    profile_queue_name: str
    frontend_base_path: str
    service_name: str
    profile: 'WebProfileDefaults'
    upload: 'WebUploadDefaults'


@dataclass(frozen=True)
class WebProfileDefaults:
    """Default profiling parameters for web API requests."""

    sample_name: str
    threads: int
    aligner: str
    min_af: float
    min_depth: int


@dataclass(frozen=True)
class WebUploadDefaults:
    """Upload validation and streaming defaults for web API file ingestion."""

    max_fasta_size: int
    max_vcf_size: int
    max_bam_size: int
    chunk_size: int
    max_fasta_line_length: int
    max_vcf_line_length: int
    max_vcf_data_lines: int
    bgzf_header_bytes: int
    allowed_fasta_types: tuple[str, ...]
    allowed_vcf_types: tuple[str, ...]
    allowed_bam_types: tuple[str, ...]


@dataclass(frozen=True)
class WebBackendConfig:
    """Complete backend configuration: env keys plus default values."""

    env: WebEnvKeys
    defaults: WebDefaults


def _load_web_backend_config() -> WebBackendConfig:
    defaults_path = Path(__file__).with_name('defaults.toml')
    payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))

    env_payload = payload['env']
    defaults_payload = payload['defaults']
    upload_payload = defaults_payload['upload']
    profile_payload = defaults_payload['profile']

    env = WebEnvKeys(
        data_dir=str(env_payload['data_dir']),
        project_db=str(env_payload['project_db']),
        results_db=str(env_payload['results_db']),
        api_token=str(env_payload['api_token']),
        allowed_roots=str(env_payload['allowed_roots']),
        frontend_dist=str(env_payload['frontend_dist']),
        cors_origins=str(env_payload['cors_origins']),
        upload_rate_limit=str(env_payload['upload_rate_limit']),
        host=str(env_payload['host']),
        port=str(env_payload['port']),
        redis_url=str(env_payload['redis_url']),
        job_timeout=str(env_payload['job_timeout']),
    )

    defaults = WebDefaults(
        web_host=str(defaults_payload['web_host']),
        web_port=int(defaults_payload['web_port']),
        redis_url=str(defaults_payload['redis_url']),
        job_timeout_seconds=int(defaults_payload['job_timeout_seconds']),
        upload_rate_limit=str(defaults_payload['upload_rate_limit']),
        cors_local_origins=tuple(str(item) for item in defaults_payload['cors_local_origins']),
        profile_queue_name=str(defaults_payload['profile_queue_name']),
        frontend_base_path=str(defaults_payload['frontend_base_path']),
        service_name=str(defaults_payload['service_name']),
        profile=WebProfileDefaults(
            sample_name=str(profile_payload['sample_name']),
            threads=int(profile_payload['threads']),
            aligner=str(profile_payload['aligner']),
            min_af=float(profile_payload['min_af']),
            min_depth=int(profile_payload['min_depth']),
        ),
        upload=WebUploadDefaults(
            max_fasta_size=int(upload_payload['max_fasta_size']),
            max_vcf_size=int(upload_payload['max_vcf_size']),
            max_bam_size=int(upload_payload['max_bam_size']),
            chunk_size=int(upload_payload['chunk_size']),
            max_fasta_line_length=int(upload_payload['max_fasta_line_length']),
            max_vcf_line_length=int(upload_payload['max_vcf_line_length']),
            max_vcf_data_lines=int(upload_payload['max_vcf_data_lines']),
            bgzf_header_bytes=int(upload_payload['bgzf_header_bytes']),
            allowed_fasta_types=tuple(str(item) for item in upload_payload['allowed_fasta_types']),
            allowed_vcf_types=tuple(str(item) for item in upload_payload['allowed_vcf_types']),
            allowed_bam_types=tuple(str(item) for item in upload_payload['allowed_bam_types']),
        ),
    )

    return WebBackendConfig(env=env, defaults=defaults)


WEB_BACKEND_CONFIG = _load_web_backend_config()
WEB_ENV = WEB_BACKEND_CONFIG.env
