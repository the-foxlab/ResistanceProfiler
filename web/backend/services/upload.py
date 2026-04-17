"""File upload handling with validation."""

import os
import tempfile
from pathlib import Path
from typing import Literal

# Maximum allowed file sizes (in bytes)
MAX_FASTA_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VCF_SIZE = 200 * 1024 * 1024  # 200 MB

# MIME types to check (informational; not strictly enforced)
ALLOWED_FASTA_TYPES = {'text/plain', 'application/octet-stream'}
ALLOWED_VCF_TYPES = {'text/plain', 'text/x-vcf', 'application/octet-stream'}


def validate_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf'],
) -> None:
    """
    Validate uploaded file data.

    :param file_data: raw file bytes
    :param file_type: 'fasta' or 'vcf'
    :raises ValueError: if file is invalid
    """
    if file_type == 'fasta':
        max_size = MAX_FASTA_SIZE
        # Check size
        if len(file_data) > max_size:
            raise ValueError(f'FASTA file exceeds maximum size of {max_size // (1024 * 1024)} MB')
        # Check basic FASTA format (starts with > or contains ATCG)
        try:
            content = file_data.decode('utf-8', errors='ignore')
            if not content.strip():
                raise ValueError('FASTA file is empty')
            if not (content.startswith('>') or any(c in 'ATCGNatcgn' for c in content)):
                raise ValueError('FASTA file does not appear to contain valid sequence data')
        except Exception as exc:
            raise ValueError(f'FASTA file validation failed: {exc}') from exc
    elif file_type == 'vcf':
        max_size = MAX_VCF_SIZE
        # Check size
        if len(file_data) > max_size:
            raise ValueError(f'VCF file exceeds maximum size of {max_size // (1024 * 1024)} MB')
        # Check basic VCF format (should contain header)
        try:
            content = file_data.decode('utf-8', errors='ignore')
            if not content.strip():
                raise ValueError('VCF file is empty')
            lines = content.split('\n')
            # VCF must have header lines starting with #
            has_vcf_header = any(line.startswith('##fileformat=VCF') for line in lines)
            has_column_header = any(line.startswith('#CHROM') for line in lines)
            if not (has_vcf_header or has_column_header):
                raise ValueError('VCF file does not appear to have valid VCF headers')
        except Exception as exc:
            raise ValueError(f'VCF file validation failed: {exc}') from exc
    else:
        raise ValueError(f'Unknown file type: {file_type}')


def save_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf'],
    upload_dir: Path,
) -> Path:
    """
    Validate and save uploaded file to temp storage.

    :param file_data: raw file bytes
    :param file_type: 'fasta' or 'vcf'
    :param upload_dir: directory to save uploads to
    :return: absolute path to saved file
    :raises ValueError: if file validation fails
    """
    # Validate file
    validate_upload(file_data, file_type)

    # Ensure upload directory exists
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Create temp file with appropriate extension
    ext = '.fa' if file_type == 'fasta' else '.vcf'
    fd, temp_path = tempfile.mkstemp(suffix=ext, dir=str(upload_dir))
    try:
        os.write(fd, file_data)
        os.close(fd)
    except Exception:
        os.close(fd)
        Path(temp_path).unlink(missing_ok=True)
        raise

    return Path(temp_path)
