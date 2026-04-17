"""File upload handling with validation."""

import os
import tempfile
from pathlib import Path
from typing import Literal

# Maximum allowed file sizes (in bytes)
MAX_FASTA_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VCF_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_BAM_SIZE = 1 * 1024 * 1024 * 1024  # 1 GB

# MIME types to check (informational; not strictly enforced)
ALLOWED_FASTA_TYPES = {'text/plain', 'application/octet-stream'}
ALLOWED_VCF_TYPES = {'text/plain', 'text/x-vcf', 'application/octet-stream'}
ALLOWED_BAM_TYPES = {'application/octet-stream'}


def validate_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf', 'bam'],
) -> None:
    """
    Validate uploaded file data.

    :param file_data: raw file bytes
    :param file_type: 'fasta', 'vcf', or 'bam'
    :raises ValueError: if file is invalid
    """
    if file_type == 'fasta':
        max_size = MAX_FASTA_SIZE
        # Check size
        if len(file_data) > max_size:
            raise ValueError(f'FASTA file exceeds maximum size of {max_size // (1024 * 1024)} MB')
        # Check basic FASTA format (starts with > or contains ATCG)
        content = file_data.decode('utf-8', errors='ignore')
        if not content.strip():
            raise ValueError('FASTA file is empty')
        if not (content.startswith('>') or any(c in 'ATCGNatcgn' for c in content)):
            raise ValueError('FASTA file does not appear to contain valid sequence data')
    elif file_type == 'vcf':
        max_size = MAX_VCF_SIZE
        # Check size
        if len(file_data) > max_size:
            raise ValueError(f'VCF file exceeds maximum size of {max_size // (1024 * 1024)} MB')
        # Check basic VCF format (should contain header)
        content = file_data.decode('utf-8', errors='ignore')
        if not content.strip():
            raise ValueError('VCF file is empty')
        lines = content.split('\n')
        # VCF must have header lines starting with #
        has_vcf_header = any(line.startswith('##fileformat=VCF') for line in lines)
        has_column_header = any(line.startswith('#CHROM') for line in lines)
        if not (has_vcf_header or has_column_header):
            raise ValueError('VCF file does not appear to have valid VCF headers')
    elif file_type == 'bam':
        max_size = MAX_BAM_SIZE
        # Check size
        if len(file_data) > max_size:
            raise ValueError(f'BAM file exceeds maximum size of {max_size // (1024 * 1024 * 1024)} GB')
        # BAM files are BGZF-compressed; they start with the gzip/BGZF magic bytes \x1f\x8b
        if len(file_data) < 2:
            raise ValueError('BAM file is too small')
        if file_data[:2] != b'\x1f\x8b':
            raise ValueError('BAM file does not have valid BGZF/gzip magic signature')
    else:
        raise ValueError(f'Unknown file type: {file_type}')


def save_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf', 'bam'],
    upload_dir: Path,
) -> Path:
    """
    Validate and save uploaded file to temp storage.

    :param file_data: raw file bytes
    :param file_type: 'fasta', 'vcf', or 'bam'
    :param upload_dir: directory to save uploads to
    :return: absolute path to saved file
    :raises ValueError: if file validation fails
    """
    # Validate file
    validate_upload(file_data, file_type)

    # Ensure upload directory exists
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Create temp file with appropriate extension
    if file_type == 'fasta':
        ext = '.fa'
    elif file_type == 'vcf':
        ext = '.vcf'
    else:  # bam
        ext = '.bam'
    fd, temp_path = tempfile.mkstemp(suffix=ext, dir=str(upload_dir))
    try:
        os.write(fd, file_data)
        os.close(fd)
    except Exception:
        os.close(fd)
        Path(temp_path).unlink(missing_ok=True)
        raise

    return Path(temp_path)


def cleanup_session_files(
    upload_paths: list[str],
    report_paths: list[str],
    upload_dir: Path,
    output_dir: Path,
) -> int:
    """
    Delete session-scoped uploads and generated HTML report files.

    :param upload_paths: uploaded file paths provided by the client session
    :param report_paths: report html paths provided by the client session
    :param upload_dir: allowed uploads directory
    :param output_dir: allowed output directory for generated reports
    :return: number of deleted files
    """
    deleted_count = 0
    deleted_count += _delete_paths(upload_paths, allowed_root=upload_dir, html_only=False)
    deleted_count += _delete_paths(report_paths, allowed_root=output_dir, html_only=True)
    return deleted_count


def _delete_paths(paths: list[str], *, allowed_root: Path, html_only: bool) -> int:
    """Delete existing files under allowed root, optionally restricting to HTML files."""
    resolved_allowed_root = allowed_root.expanduser().resolve()
    deleted_count = 0
    for candidate_path in paths:
        candidate = Path(candidate_path).expanduser().resolve()
        if not _is_within_root(candidate, resolved_allowed_root):
            continue
        if html_only and candidate.suffix.lower() != '.html':
            continue
        if not candidate.is_file():
            continue
        candidate.unlink(missing_ok=True)
        deleted_count += 1
        if not html_only and candidate.suffix.lower() == '.bam':
            deleted_count += _delete_bam_index_sidecars(candidate, resolved_allowed_root)
    return deleted_count


def _delete_bam_index_sidecars(bam_path: Path, allowed_root: Path) -> int:
    """Delete .bam.bai / .bai sidecar files for one BAM path when present."""
    deleted_count = 0
    sidecars = [bam_path.with_suffix(f'{bam_path.suffix}.bai'), bam_path.with_suffix('.bai')]
    for sidecar in sidecars:
        if not _is_within_root(sidecar, allowed_root):
            continue
        if not sidecar.is_file():
            continue
        sidecar.unlink(missing_ok=True)
        deleted_count += 1
    return deleted_count


def _is_within_root(path: Path, root: Path) -> bool:
    """Return whether path is root or contained within root."""
    return path == root or root in path.parents
