"""Pydantic models for the web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileFastaPayload(BaseModel):
    """Payload for FASTA profiling."""

    fasta_path: str
    input_display_name: str | None = None
    database_id: str | None = None
    sample: str | None = None
    threads: int | None = None


class ProfileVcfPayload(BaseModel):
    """Payload for VCF profiling."""

    vcf_path: str
    ref_fasta_path: str
    input_display_name: str | None = None
    database_id: str | None = None
    sample: str | None = None
    min_af: float | None = Field(default=None, ge=0.0, le=1.0)
    min_depth: int | None = Field(default=None, ge=0)
    bam_path: str | None = None
    threads: int | None = None


class RegenerateJsonPayload(BaseModel):
    """Payload for regenerating reports from uploaded JSON artifacts."""

    json_path: str
    database_id: str | None = None


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


class BatchSampleEntry(BaseModel):
    """A single sample entry within a batch submission response."""

    job_id: str
    sample_name: str
    status: str = 'queued'


class BatchSubmitResponse(BaseModel):
    """Response returned after a batch of profiling jobs is enqueued."""

    samples: list[BatchSampleEntry]
    total: int


class BatchProfileVcfPayload(BaseModel):
    """Payload for submitting a VCF-mode batch profiling job."""

    vcf_paths: list[str]
    sample_names: list[str]
    input_display_names: list[str] | None = None
    reference_fasta_path: str
    db_path: str
    min_af: float = Field(default=0.01, ge=0.0, le=1.0)
    min_depth: int = Field(default=10, ge=0)
    threads: int = 1


class BatchProfileFastaPayload(BaseModel):
    """Payload for submitting a FASTA-mode batch profiling job."""

    fasta_paths: list[str]
    sample_names: list[str]
    input_display_names: list[str] | None = None
    db_path: str
    threads: int = 1


class UploadResponse(BaseModel):
    """Response returned after a file is successfully uploaded."""

    file_path: str
    file_type: str  # 'fasta', 'vcf', 'bam', or 'json'
    size_bytes: int


class ArtifactBundlePayload(BaseModel):
    """Artifact paths requested for bundled download."""

    paths: list[str] = Field(default_factory=list)


class SessionCleanupPayload(BaseModel):
    """Client-provided upload file paths to delete at session end."""

    upload_paths: list[str] = Field(default_factory=list)
    report_paths: list[str] = Field(default_factory=list)
    token: str | None = None


class SessionCleanupResponse(BaseModel):
    """Summary of session upload cleanup."""

    deleted_count: int


class ComparePayload(BaseModel):
    """List of result JSON paths to compare."""

    paths: list[str]
    non_synonymous_only: bool = False
    db_hits_only: bool = False


class CompareMutationKey(BaseModel):
    """Unique mutation identifier used as a heatmap column."""

    feature: str
    position: int
    ref_aa: str
    alt_aa: str
    label: str


class CompareCell(BaseModel):
    """Cell content for one sample x mutation."""

    allele_freq: float | None = None
    db_hit: bool = False


class CompareResponse(BaseModel):
    """Matrix data for the comparison heatmap."""

    samples: list[str]
    references: list[str]
    mutations: list[CompareMutationKey]
    mutation_labels: list[str]
    mutation_tick_labels: list[str]  # AA-only labels for x-axis display (no feature prefix)
    features: list[str]
    feature_map: list[int]
    feature_display_names: dict[str, str]  # feature_name -> display_name mapping
    consequences: list[str]  # consequence type per mutation column
    db_hit_map: list[bool]  # True if any sample has a db_hit for that mutation column
    sample_disambiguation_note: str = ''
    matrix: list[list[CompareCell]]
