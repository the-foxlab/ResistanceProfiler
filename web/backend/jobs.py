"""
RQ job functions for background profiling.

These are top-level importable functions so that RQ can serialize them via
pickle. All Path arguments are passed as strings for serialization safety and
converted inside the function.
"""

from __future__ import annotations

from pathlib import Path

from web.backend.services.profile import profile_fasta, profile_vcf
from web.backend.services.regenerate import regenerate_from_json


def run_profile_fasta(
    *,
    project_db: str,
    results_db: str,
    output_dir: str,
    fasta_path: str,
    sample: str,
    threads: int,
    aligner: str,
) -> dict:
    """RQ job wrapper for FASTA profiling."""
    return profile_fasta(
        project_db=Path(project_db),
        results_db=Path(results_db),
        output_dir=Path(output_dir),
        fasta_path=Path(fasta_path),
        sample=sample,
        threads=threads,
        aligner=aligner,
    )


def run_profile_vcf(
    *,
    project_db: str,
    results_db: str,
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
    return profile_vcf(
        project_db=Path(project_db),
        results_db=Path(results_db),
        output_dir=Path(output_dir),
        vcf_path=Path(vcf_path),
        ref_fasta_path=Path(ref_fasta_path),
        sample=sample,
        min_af=min_af,
        min_depth=min_depth,
        bam_path=Path(bam_path) if bam_path else None,
        threads=threads,
        aligner=aligner,
    )


def run_regenerate_json(
    *,
    project_db: str,
    output_dir: str,
    json_path: str,
) -> dict:
    """RQ job wrapper for regenerating report artifacts from result JSON."""
    return regenerate_from_json(
        project_db=Path(project_db),
        output_dir=Path(output_dir),
        json_path=Path(json_path),
    )
