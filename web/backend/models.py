"""Pydantic request and response models for the web API.

Inputs reference files via opaque upload/artifact IDs resolved and ownership-checked
server-side rather than raw filesystem paths.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, create_model

from web.backend.config import WEB_BACKEND_CONFIG

_D = WEB_BACKEND_CONFIG.defaults

# Sample names are forwarded as CLI argv values; disallow a leading dash to prevent flag confusion.
_SAMPLE_NAME_PATTERN = r'^[^-].*$'
SampleName = Annotated[str, Field(max_length=_D.sample_name_max_length, pattern=_SAMPLE_NAME_PATTERN)]
OpaqueId = Annotated[str, Field(max_length=_D.opaque_id_max_length)]
DisplayName = Annotated[str, Field(max_length=_D.display_name_max_length)]
Threads = Annotated[int, Field(ge=1, le=_D.profile_max_threads)]
AlleleFreq = Annotated[float, Field(ge=0.0, le=1.0)]
MinDepth = Annotated[int, Field(ge=0, le=_D.min_depth_max)]
IdList = Annotated[list[str], Field(max_length=_D.path_list_max_length)]
SampleNameList = Annotated[list[SampleName], Field(max_length=_D.path_list_max_length)]
DisplayNameList = Annotated[list[str], Field(max_length=_D.path_list_max_length)]


class ProfileFastaPayload(BaseModel):
    fasta_id: OpaqueId | None = None
    use_example: bool = False
    input_display_name: DisplayName | None = None
    database_id: OpaqueId | None = None
    sample: SampleName | None = None
    threads: Threads | None = None


class ProfileVcfPayload(BaseModel):
    vcf_id: OpaqueId
    reference_id: OpaqueId
    input_display_name: DisplayName | None = None
    database_id: OpaqueId | None = None
    sample: SampleName | None = None
    min_af: AlleleFreq | None = None
    min_depth: MinDepth | None = None
    bam_id: OpaqueId | None = None
    threads: Threads | None = None


class RegenerateJsonPayload(BaseModel):
    json_id: OpaqueId
    database_id: OpaqueId | None = None


class ApiEnvelope(BaseModel):
    status: str = 'ok'
    data: dict
    error: str | None = None


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str = 'queued'


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # queued | running | succeeded | failed
    result: dict | None = None
    error: str | None = None


class BatchSampleEntry(BaseModel):
    job_id: str
    sample_name: str
    status: str = 'queued'


class BatchSubmitResponse(BaseModel):
    samples: list[BatchSampleEntry]
    total: int


class _BatchPayloadBase(BaseModel):
    sample_names: SampleNameList
    input_display_names: DisplayNameList | None = None


class BatchProfileVcfPayload(_BatchPayloadBase):
    vcf_ids: IdList
    reference_id: OpaqueId
    db_path: OpaqueId
    min_af: AlleleFreq = _D.profile_min_af
    min_depth: MinDepth = _D.profile_min_depth
    threads: Threads = _D.profile_threads
    # Optional per-sample BAM IDs aligned with vcf_ids; None skips coverage gaps for that sample.
    bam_ids: Annotated[list[str | None], Field(max_length=_D.path_list_max_length)] | None = None


class BatchProfileFastaPayload(_BatchPayloadBase):
    fasta_ids: IdList
    db_path: OpaqueId
    threads: Threads = _D.profile_threads


class BatchRegenerateJsonPayload(_BatchPayloadBase):
    """Batch regenerate payload; each JSON resolves its own DB via stored ``project_fingerprint``."""

    json_ids: IdList
    database_id: OpaqueId | None = None


class UploadResponse(BaseModel):
    upload_id: str
    file_type: str  # 'fasta', 'vcf', 'bam', or 'json'
    size_bytes: int


class ArtifactBundlePayload(BaseModel):
    """Artifact IDs for bundled ZIP download (workflow cap enforced per app by the route)."""

    artifact_ids: IdList = Field(default_factory=list)


class SessionCleanupPayload(BaseModel):
    upload_ids: IdList = Field(default_factory=list)
    artifact_ids: IdList = Field(default_factory=list)


def artifact_bundle_payload_model(artifact_limit: int) -> type[ArtifactBundlePayload]:
    """Bound bundle validation per app while allowing the route to report ``limit + 1`` as 400."""
    ceiling = max(_D.path_list_max_length, artifact_limit + 1)
    return create_model(
        'ConfiguredArtifactBundlePayload',
        __base__=ArtifactBundlePayload,
        artifact_ids=(list[str], Field(default_factory=list, max_length=ceiling)),
    )


def session_cleanup_payload_model(artifact_limit: int) -> type[SessionCleanupPayload]:
    """Bound session cleanup list lengths per app while allowing ``limit + 1`` to return 400."""
    ceiling = _D.path_list_max_length
    return create_model(
        'ConfiguredSessionCleanupPayload',
        __base__=SessionCleanupPayload,
        upload_ids=(list[str], Field(default_factory=list, max_length=max(ceiling, artifact_limit))),
        artifact_ids=(list[str], Field(default_factory=list, max_length=max(ceiling, artifact_limit + 1))),
    )


class SessionCleanupResponse(BaseModel):
    deleted_count: int


class ComparePayload(BaseModel):
    artifact_ids: IdList = Field(default_factory=list)
    non_synonymous_only: bool = False
    db_hits_only: bool = False


class CompareMutationKey(BaseModel):
    feature: str
    position: int
    ref_aa: str
    alt_aa: str
    label: str


class CompareCell(BaseModel):
    allele_freq: float | None = None
    db_hit: bool = False


class CompareResponse(BaseModel):
    samples: list[str]
    references: list[str]
    mutations: list[CompareMutationKey]
    mutation_labels: list[str]
    mutation_tick_labels: list[str]
    features: list[str]
    feature_map: list[int]
    feature_display_names: dict[str, str]
    consequences: list[str]
    db_hit_map: list[bool]
    sample_disambiguation_note: str = ''
    matrix: list[list[CompareCell]]


class CompareDatabasesPayload(BaseModel):
    database_ids: IdList = Field(default_factory=list)
    accession: OpaqueId
