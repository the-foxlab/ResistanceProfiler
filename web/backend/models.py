"""Pydantic models for the web API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from web.backend.config import WEB_BACKEND_CONFIG


class ProfileFastaPayload(BaseModel):
    """Payload for FASTA profiling.

    Inputs are referenced by opaque upload IDs (returned by the upload routes)
    rather than absolute filesystem paths, so the server resolves and
    ownership-checks them server-side.
    """

    fasta_id: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    input_display_name: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.display_name_max_length)
    database_id: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    sample: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.sample_name_max_length)
    threads: int | None = Field(default=None, ge=1, le=WEB_BACKEND_CONFIG.defaults.profile_max_threads)


class ProfileVcfPayload(BaseModel):
    """Payload for VCF profiling.

    Inputs are referenced by opaque upload IDs rather than absolute filesystem
    paths.
    """

    vcf_id: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    reference_id: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    input_display_name: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.display_name_max_length)
    database_id: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    sample: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.sample_name_max_length)
    min_af: float | None = Field(default=None, ge=0.0, le=1.0)
    min_depth: int | None = Field(default=None, ge=0, le=WEB_BACKEND_CONFIG.defaults.min_depth_max)
    bam_id: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    threads: int | None = Field(default=None, ge=1, le=WEB_BACKEND_CONFIG.defaults.profile_max_threads)


class RegenerateJsonPayload(BaseModel):
    """Payload for regenerating reports from uploaded JSON artifacts.

    The JSON input is referenced by an opaque upload ID.
    """

    json_id: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    database_id: str | None = Field(default=None, max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)


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
    """Payload for submitting a VCF-mode batch profiling job.

    Inputs are referenced by opaque upload IDs rather than absolute filesystem
    paths.
    """

    vcf_ids: list[str] = Field(max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length)
    sample_names: list[str] = Field(max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length)
    input_display_names: list[str] | None = Field(
        default=None,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )
    reference_id: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    db_path: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    min_af: float = Field(default=0.01, ge=0.0, le=1.0)
    min_depth: int = Field(default=10, ge=0, le=WEB_BACKEND_CONFIG.defaults.min_depth_max)
    threads: int = Field(default=1, ge=1, le=WEB_BACKEND_CONFIG.defaults.profile_max_threads)
    # Optional per-sample BAM IDs, positionally aligned with ``vcf_ids``. A ``None`` entry
    # means "no BAM for that sample" (coverage-gap analysis is skipped for it), mirroring the
    # single-VCF ``bam_id`` option. When omitted entirely, every sample runs without a BAM.
    bam_ids: list[str | None] | None = Field(
        default=None,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )


class BatchProfileFastaPayload(BaseModel):
    """Payload for submitting a FASTA-mode batch profiling job.

    Inputs are referenced by opaque upload IDs rather than absolute filesystem
    paths.
    """

    fasta_ids: list[str] = Field(max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length)
    sample_names: list[str] = Field(max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length)
    input_display_names: list[str] | None = Field(
        default=None,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )
    db_path: str = Field(max_length=WEB_BACKEND_CONFIG.defaults.opaque_id_max_length)
    threads: int = Field(default=1, ge=1, le=WEB_BACKEND_CONFIG.defaults.profile_max_threads)


class UploadResponse(BaseModel):
    """Response returned after a file is successfully uploaded.

    Returns an opaque ``upload_id`` that subsequent profile/regenerate/compare
    routes use to reference the file server-side. The absolute path is never
    exposed to clients.
    """

    upload_id: str
    file_type: str  # 'fasta', 'vcf', 'bam', or 'json'
    size_bytes: int


class ArtifactBundlePayload(BaseModel):
    """Artifact IDs requested for bundled download.

    Artifacts are referenced by opaque IDs (recorded when a job produces them)
    rather than filesystem paths, so the server resolves and ownership-checks
    them server-side.
    """

    artifact_ids: list[str] = Field(
        default_factory=list,
        max_length=WEB_BACKEND_CONFIG.defaults.artifact_bundle_max_paths,
    )


class SessionCleanupPayload(BaseModel):
    """Client-provided upload/artifact IDs to delete at session end.

    References are opaque IDs, not filesystem paths.
    """

    upload_ids: list[str] = Field(
        default_factory=list,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )
    artifact_ids: list[str] = Field(
        default_factory=list,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )


class SessionCleanupResponse(BaseModel):
    """Summary of session upload cleanup."""

    deleted_count: int


class ComparePayload(BaseModel):
    """List of result JSON artifact IDs to compare.

    Inputs are referenced by opaque artifact IDs rather than filesystem paths.
    """

    artifact_ids: list[str] = Field(
        default_factory=list,
        max_length=WEB_BACKEND_CONFIG.defaults.path_list_max_length,
    )
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
