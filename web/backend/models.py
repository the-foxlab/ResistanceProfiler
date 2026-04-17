"""Pydantic models for the prototype web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileFastaPayload(BaseModel):
    """Payload for FASTA profiling."""

    fasta_path: str
    sample: str = 'sample'
    threads: int = 1
    aligner: str = 'mappy'


class ProfileVcfPayload(BaseModel):
    """Payload for VCF profiling."""

    vcf_path: str
    ref_fasta_path: str
    sample: str = 'sample'
    min_af: float = 0.01
    min_depth: int = 10
    bam_path: str | None = None
    threads: int = 1
    aligner: str = 'mappy'


class ApiEnvelope(BaseModel):
    """Simple stable response envelope."""

    status: str = 'ok'
    data: dict
    error: str | None = None


class JobSubmitResponse(BaseModel):
    """Response returned immediately after a profiling job is enqueued."""

    job_id: str
    status: str = 'queued'


class JobStatusResponse(BaseModel):
    """Response for polling a profiling job's current state."""

    job_id: str
    # queued | running | succeeded | failed
    status: str
    result: dict | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    """Response returned after a file is successfully uploaded."""

    file_path: str
    file_type: str  # 'fasta' or 'vcf'
    size_bytes: int


class SessionCleanupPayload(BaseModel):
    """Client-provided upload file paths to delete at session end."""

    upload_paths: list[str] = Field(default_factory=list)
    report_paths: list[str] = Field(default_factory=list)


class SessionCleanupResponse(BaseModel):
    """Summary of session upload cleanup."""

    deleted_count: int
