"""Shared web backend configuration loaded from a TOML defaults file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebEnvKeys:
    """Environment variable keys used by the web backend."""

    data_dir: str = 'RESPRO_WEB_DATA_DIR'
    allowed_roots: str = 'RESPRO_WEB_ALLOWED_ROOTS'
    cors_origins: str = 'RESPRO_WEB_CORS_ORIGINS'
    upload_rate_limit: str = 'RESPRO_WEB_UPLOAD_RATE_LIMIT'
    api_rate_limit: str = 'RESPRO_WEB_API_RATE_LIMIT'
    host: str = 'RESPRO_WEB_HOST'
    port: str = 'RESPRO_WEB_PORT'
    redis_url: str = 'REDIS_URL'
    job_timeout: str = 'RESPRO_WEB_JOB_TIMEOUT'
    job_retry_max: str = 'RESPRO_WEB_JOB_RETRY_MAX'
    job_retry_intervals: str = 'RESPRO_WEB_JOB_RETRY_INTERVALS'
    maintained_bootstrap: str = 'RESPRO_WEB_MAINTAINED_BOOTSTRAP'
    maintained_db_update_interval: str = 'RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS'
    trusted_proxies: str = 'RESPRO_WEB_TRUSTED_PROXIES'
    imprint: str = 'RESPRO_WEB_IMPRINT'
    contact_email: str = 'RESPRO_WEB_CONTACT_EMAIL'
    max_batch_size: str = 'RESPRO_WEB_MAX_BATCH_SIZE'
    deployment_mode: str = 'RESPRO_WEB_DEPLOYMENT_MODE'
    result_ttl: str = 'RESPRO_WEB_RESULT_TTL'
    session_ttl: str = 'RESPRO_WEB_SESSION_TTL'


@dataclass(frozen=True)
class WebDefaults:
    """Runtime defaults for web backend."""

    web_host: str
    web_port: int
    redis_url: str
    job_timeout_seconds: int
    job_retry_max: int
    job_retry_intervals_seconds: tuple[int, ...]
    sweep_frequency_seconds: int
    upload_rate_limit: str
    api_rate_limit: str
    max_batch_size: int
    artifact_bundle_max_paths: int
    profile_max_threads: int
    sample_name_max_length: int
    display_name_max_length: int
    path_list_max_length: int
    opaque_id_max_length: int
    min_depth_max: int
    batch_queue_name: str
    cors_local_origins: tuple[str, ...]
    profile_queue_name: str
    frontend_base_path: str
    service_name: str
    maintained_bootstrap: bool
    maintained_db_update_interval_seconds: int
    deployment_mode: str
    result_ttl_seconds: int
    session_ttl_seconds: int
    # Profile defaults
    profile_sample_name: str
    profile_threads: int
    profile_min_af: float
    profile_min_depth: int
    # Upload defaults
    upload_max_fasta_size: int
    upload_max_vcf_size: int
    upload_max_bam_size: int
    upload_chunk_size: int
    upload_max_fasta_line_length: int
    upload_max_vcf_line_length: int
    upload_max_vcf_data_lines: int
    upload_bgzf_header_bytes: int
    upload_allowed_fasta_types: tuple[str, ...]
    upload_allowed_vcf_types: tuple[str, ...]
    upload_allowed_bam_types: tuple[str, ...]


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
        allowed_roots=str(env_payload['allowed_roots']),
        cors_origins=str(env_payload['cors_origins']),
        upload_rate_limit=str(env_payload['upload_rate_limit']),
        api_rate_limit=str(env_payload['api_rate_limit']),
        max_batch_size=str(env_payload['max_batch_size']),
        host=str(env_payload['host']),
        port=str(env_payload['port']),
        redis_url=str(env_payload['redis_url']),
        job_timeout=str(env_payload['job_timeout']),
        job_retry_max=str(env_payload['job_retry_max']),
        job_retry_intervals=str(env_payload['job_retry_intervals']),
        maintained_bootstrap=str(env_payload['maintained_bootstrap']),
        maintained_db_update_interval=str(env_payload['maintained_db_update_interval']),
        trusted_proxies=str(env_payload['trusted_proxies']),
        imprint=str(env_payload['imprint']),
        contact_email=str(env_payload['contact_email']),
        deployment_mode=str(env_payload['deployment_mode']),
        result_ttl=str(env_payload['result_ttl']),
        session_ttl=str(env_payload['session_ttl']),
    )

    defaults = WebDefaults(
        web_host=str(defaults_payload['web_host']),
        web_port=int(defaults_payload['web_port']),
        redis_url=str(defaults_payload['redis_url']),
        job_timeout_seconds=int(defaults_payload['job_timeout_seconds']),
        job_retry_max=int(defaults_payload['job_retry_max']),
        job_retry_intervals_seconds=tuple(
            int(item) for item in defaults_payload['job_retry_intervals_seconds']
        ),
        sweep_frequency_seconds=int(defaults_payload['sweep_frequency_seconds']),
        upload_rate_limit=str(defaults_payload['upload_rate_limit']),
        api_rate_limit=str(defaults_payload['api_rate_limit']),
        max_batch_size=int(defaults_payload['max_batch_size']),
        artifact_bundle_max_paths=int(defaults_payload['artifact_bundle_max_paths']),
        profile_max_threads=int(defaults_payload['profile_max_threads']),
        sample_name_max_length=int(defaults_payload['sample_name_max_length']),
        display_name_max_length=int(defaults_payload['display_name_max_length']),
        path_list_max_length=int(defaults_payload['path_list_max_length']),
        opaque_id_max_length=int(defaults_payload['opaque_id_max_length']),
        min_depth_max=int(defaults_payload['min_depth_max']),
        batch_queue_name=str(defaults_payload['batch_queue_name']),
        cors_local_origins=tuple(str(item) for item in defaults_payload['cors_local_origins']),
        profile_queue_name=str(defaults_payload['profile_queue_name']),
        frontend_base_path=str(defaults_payload['frontend_base_path']),
        service_name=str(defaults_payload['service_name']),
        maintained_bootstrap=bool(defaults_payload['maintained_bootstrap']),
        maintained_db_update_interval_seconds=int(
            defaults_payload['maintained_db_update_interval_seconds']
        ),
        deployment_mode=str(defaults_payload['deployment_mode']),
        result_ttl_seconds=int(defaults_payload['result_ttl_seconds']),
        session_ttl_seconds=int(defaults_payload['session_ttl_seconds']),
        profile_sample_name=str(profile_payload['sample_name']),
        profile_threads=int(profile_payload['threads']),
        profile_min_af=float(profile_payload['min_af']),
        profile_min_depth=int(profile_payload['min_depth']),
        upload_max_fasta_size=int(upload_payload['max_fasta_size']),
        upload_max_vcf_size=int(upload_payload['max_vcf_size']),
        upload_max_bam_size=int(upload_payload['max_bam_size']),
        upload_chunk_size=int(upload_payload['chunk_size']),
        upload_max_fasta_line_length=int(upload_payload['max_fasta_line_length']),
        upload_max_vcf_line_length=int(upload_payload['max_vcf_line_length']),
        upload_max_vcf_data_lines=int(upload_payload['max_vcf_data_lines']),
        upload_bgzf_header_bytes=int(upload_payload['bgzf_header_bytes']),
        upload_allowed_fasta_types=tuple(str(item) for item in upload_payload['allowed_fasta_types']),
        upload_allowed_vcf_types=tuple(str(item) for item in upload_payload['allowed_vcf_types']),
        upload_allowed_bam_types=tuple(str(item) for item in upload_payload['allowed_bam_types']),
    )

    return WebBackendConfig(env=env, defaults=defaults)


WEB_BACKEND_CONFIG = _load_web_backend_config()
WEB_ENV = WEB_BACKEND_CONFIG.env
