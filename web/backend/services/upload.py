"""File upload handling with validation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal, Protocol

from respro.db.results import load_run_from_json
from web.backend.config import WEB_BACKEND_CONFIG

_ALLOWED_TEXT_CONTROL_BYTES = {9, 10, 13}
_ALLOWED_FASTA_SEQUENCE_BYTES = {
    ord('A'),
    ord('C'),
    ord('G'),
    ord('T'),
    ord('U'),
    ord('R'),
    ord('Y'),
    ord('K'),
    ord('M'),
    ord('S'),
    ord('W'),
    ord('B'),
    ord('D'),
    ord('H'),
    ord('V'),
    ord('N'),
    ord('-'),
    ord('.'),
    ord('*'),
}


class UploadStream(Protocol):
    """Minimal async file API required for streamed uploads."""

    async def read(self, size: int = -1) -> bytes:
        """Read up to size bytes from upload stream."""


def _max_size_for_type(file_type: Literal['fasta', 'vcf', 'bam', 'json']) -> int:
    defaults = WEB_BACKEND_CONFIG.defaults
    if file_type == 'fasta':
        return defaults.upload_max_fasta_size
    if file_type == 'vcf':
        return defaults.upload_max_vcf_size
    if file_type == 'json':
        return defaults.upload_max_vcf_size
    return defaults.upload_max_bam_size


def _extension_for_type(file_type: Literal['fasta', 'vcf', 'bam', 'json']) -> str:
    if file_type == 'fasta':
        return '.fa'
    if file_type == 'vcf':
        return '.vcf'
    if file_type == 'json':
        return '.json'
    return '.bam'


def _validate_stream_chunk(
    state: dict[str, object],
    chunk: bytes,
    file_type: Literal['fasta', 'vcf', 'bam', 'json'],
) -> None:
    if file_type == 'fasta':
        _validate_text_chunk(chunk, 'FASTA')
        if chunk.strip():
            state['has_non_whitespace'] = True
        _process_fasta_chunk(state, chunk)
        return

    if file_type == 'vcf':
        _validate_text_chunk(chunk, 'VCF')
        if chunk.strip():
            state['has_non_whitespace'] = True
        _process_vcf_chunk(state, chunk)
        return

    if file_type == 'json':
        if b'\x00' in chunk:
            raise ValueError('JSON upload must be valid UTF-8 text')
        if chunk.strip():
            state['has_non_whitespace'] = True
        state['chunks'].append(chunk)
        return

    bgzf_header_bytes = WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes
    if len(state['first_bytes']) < bgzf_header_bytes:
        missing = bgzf_header_bytes - len(state['first_bytes'])
        state['first_bytes'] = state['first_bytes'] + chunk[:missing]


def _validate_stream_complete(
    state: dict[str, object],
    file_type: Literal['fasta', 'vcf', 'bam', 'json'],
) -> None:
    if file_type == 'fasta':
        _finalize_fasta_lines(state)
        if not state['has_non_whitespace']:
            raise ValueError('FASTA file is empty')
        if not (state['starts_with_header'] or state['has_sequence_char']):
            raise ValueError('FASTA file does not appear to contain valid sequence data')
        return

    if file_type == 'vcf':
        _finalize_vcf_lines(state)
        if not state['has_non_whitespace']:
            raise ValueError('VCF file is empty')
        has_vcf_header = bool(state['has_vcf_header'])
        has_column_header = bool(state['has_column_header'])
        if not (has_vcf_header and has_column_header):
            raise ValueError('VCF file does not appear to have valid VCF headers')
        return

    if file_type == 'json':
        if not state['has_non_whitespace']:
            raise ValueError('JSON upload is empty')
        try:
            b''.join(state['chunks']).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('JSON upload must be valid UTF-8 text') from exc
        return

    if len(state['first_bytes']) < WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes:
        raise ValueError('BAM file is too small')
    if state['first_bytes'][:2] != b'\x1f\x8b':
        raise ValueError('BAM file does not have valid BGZF/gzip magic signature')
    _validate_bgzf_header(state['first_bytes'])


def _new_stream_validation_state(file_type: Literal['fasta', 'vcf', 'bam', 'json']) -> dict[str, object]:
    if file_type == 'fasta':
        return {
            'starts_with_header': None,
            'has_non_whitespace': False,
            'has_sequence_char': False,
            'line_buffer': b'',
            'line_number': 0,
        }
    if file_type == 'vcf':
        return {
            'line_buffer': b'',
            'has_non_whitespace': False,
            'has_vcf_header': False,
            'has_column_header': False,
            'line_number': 0,
            'data_lines': 0,
        }
    if file_type == 'json':
        return {
            'has_non_whitespace': False,
            'chunks': [],
        }
    return {
        'first_bytes': b'',
    }


def _validate_text_chunk(chunk: bytes, label: str) -> None:
    if b'\x00' in chunk:
        raise ValueError(f'{label} file contains non-text/binary bytes')

    for byte in chunk:
        if byte > 127:
            raise ValueError(f'{label} file contains non-text/binary bytes')
        if byte < 32 and byte not in _ALLOWED_TEXT_CONTROL_BYTES:
            raise ValueError(f'{label} file contains non-text/binary bytes')


def _process_fasta_chunk(state: dict[str, object], chunk: bytes) -> None:
    combined = state['line_buffer'] + chunk
    lines = combined.split(b'\n')
    state['line_buffer'] = lines.pop() if lines else combined
    for line in lines:
        _validate_fasta_line(state, line.rstrip(b'\r'))


def _finalize_fasta_lines(state: dict[str, object]) -> None:
    trailing = state['line_buffer']
    if trailing:
        _validate_fasta_line(state, trailing.rstrip(b'\r'))
    state['line_buffer'] = b''


def _validate_fasta_line(state: dict[str, object], raw_line: bytes) -> None:
    state['line_number'] = int(state['line_number']) + 1
    line_number = int(state['line_number'])

    max_fasta_line_length = WEB_BACKEND_CONFIG.defaults.upload_max_fasta_line_length
    if len(raw_line) > max_fasta_line_length:
        raise ValueError(
            f'FASTA file contains line {line_number} longer than {max_fasta_line_length} characters'
        )

    stripped = raw_line.strip()
    if not stripped:
        return

    if state['starts_with_header'] is None:
        state['starts_with_header'] = stripped.startswith(b'>')

    if stripped.startswith(b'>'):
        return

    compact = stripped.replace(b' ', b'').replace(b'\t', b'').upper()
    if not compact:
        return
    if any(byte not in _ALLOWED_FASTA_SEQUENCE_BYTES for byte in compact):
        raise ValueError('FASTA file contains invalid sequence characters')
    if not state['has_sequence_char'] and any(base in compact for base in (b'A', b'C', b'G', b'T', b'N')):
        state['has_sequence_char'] = True


def _process_vcf_chunk(state: dict[str, object], chunk: bytes) -> None:
    combined = state['line_buffer'] + chunk
    lines = combined.split(b'\n')
    state['line_buffer'] = lines.pop() if lines else combined
    for line in lines:
        _validate_vcf_line(state, line.rstrip(b'\r'))


def _finalize_vcf_lines(state: dict[str, object]) -> None:
    trailing = state['line_buffer']
    if trailing:
        _validate_vcf_line(state, trailing.rstrip(b'\r'))
    state['line_buffer'] = b''


def _validate_vcf_line(state: dict[str, object], raw_line: bytes) -> None:
    state['line_number'] = int(state['line_number']) + 1
    line_number = int(state['line_number'])

    max_vcf_line_length = WEB_BACKEND_CONFIG.defaults.upload_max_vcf_line_length
    max_vcf_data_lines = WEB_BACKEND_CONFIG.defaults.upload_max_vcf_data_lines
    if len(raw_line) > max_vcf_line_length:
        raise ValueError(f'VCF file contains line {line_number} longer than {max_vcf_line_length} characters')

    stripped = raw_line.strip()
    if not stripped:
        return

    if stripped.startswith(b'##fileformat=VCF'):
        state['has_vcf_header'] = True
        return

    if stripped.startswith(b'#CHROM'):
        state['has_column_header'] = True
        return

    if stripped.startswith(b'#'):
        return

    if not state['has_column_header']:
        raise ValueError('VCF file contains data rows before #CHROM header')

    state['data_lines'] = int(state['data_lines']) + 1
    if int(state['data_lines']) > max_vcf_data_lines:
        raise ValueError(f'VCF file exceeds maximum data row count of {max_vcf_data_lines}')


def _validate_bgzf_header(first_bytes: bytes) -> None:
    if len(first_bytes) < WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes:
        raise ValueError('BAM file is too small')

    compression_method = first_bytes[2]
    flags = first_bytes[3]
    if compression_method != 8 or (flags & 0x04) == 0:
        raise ValueError('BAM file does not have valid BGZF/gzip magic signature')

    extra_length = int.from_bytes(first_bytes[10:12], byteorder='little')
    if extra_length < 6:
        raise ValueError('BAM file does not have valid BGZF/gzip magic signature')

    if first_bytes[12:14] != b'BC' or first_bytes[14:16] != b'\x02\x00':
        raise ValueError('BAM file does not have valid BGZF/gzip magic signature')

    block_size = int.from_bytes(first_bytes[16:18], byteorder='little') + 1
    if block_size < 26 or block_size > 65536:
        raise ValueError('BAM file does not have valid BGZF/gzip magic signature')


async def save_upload_stream(
    upload_file: UploadStream,
    file_type: Literal['fasta', 'vcf', 'bam', 'json'],
    upload_dir: Path,
    *,
    chunk_size: int | None = None,
) -> tuple[Path, int]:
    """
    Validate and save uploaded content incrementally.

    :param upload_file: async upload stream object
    :param file_type: 'fasta', 'vcf', or 'bam'
    :param upload_dir: directory to save uploads to
    :param chunk_size: bytes per read operation
    :return: saved path and total size in bytes
    :raises ValueError: if file validation fails
    """
    effective_chunk_size = chunk_size or WEB_BACKEND_CONFIG.defaults.upload_chunk_size
    max_size = _max_size_for_type(file_type)
    validation_state = _new_stream_validation_state(file_type)

    upload_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=_extension_for_type(file_type), dir=str(upload_dir))

    total_size = 0
    try:
        with os.fdopen(fd, 'wb') as handle:
            while True:
                chunk = await upload_file.read(effective_chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    if file_type == 'bam':
                        raise ValueError(
                            f'BAM file exceeds maximum size of {max_size // (1024 * 1024 * 1024)} GB'
                        )
                    raise ValueError(
                        f'{file_type.upper()} file exceeds maximum size of {max_size // (1024 * 1024)} MB'
                    )
                _validate_stream_chunk(validation_state, chunk, file_type)
                handle.write(chunk)

        _validate_stream_complete(validation_state, file_type)
        if file_type == 'json':
            load_run_from_json(Path(temp_path))
    except Exception:
        Path(temp_path).unlink(missing_ok=True)
        raise

    return Path(temp_path), total_size


def validate_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf', 'bam', 'json'],
) -> None:
    """
    Validate uploaded file data.

    :param file_data: raw file bytes
    :param file_type: 'fasta', 'vcf', 'bam', or 'json'
    :raises ValueError: if file is invalid
    """
    if file_type not in ('fasta', 'vcf', 'bam', 'json'):
        raise ValueError(f'Unknown file type: {file_type}')

    max_size = _max_size_for_type(file_type)
    if len(file_data) > max_size:
        if file_type == 'bam':
            raise ValueError(f'BAM file exceeds maximum size of {max_size // (1024 * 1024 * 1024)} GB')
        raise ValueError(f'{file_type.upper()} file exceeds maximum size of {max_size // (1024 * 1024)} MB')

    state = _new_stream_validation_state(file_type)
    chunk_size = WEB_BACKEND_CONFIG.defaults.upload.chunk_size
    for offset in range(0, len(file_data), chunk_size):
        chunk = file_data[offset : offset + chunk_size]
        _validate_stream_chunk(state, chunk, file_type)
    _validate_stream_complete(state, file_type)


def save_upload(
    file_data: bytes,
    file_type: Literal['fasta', 'vcf', 'bam', 'json'],
    upload_dir: Path,
) -> Path:
    """
    Validate and save uploaded file to temp storage.

    :param file_data: raw file bytes
    :param file_type: 'fasta', 'vcf', 'bam', or 'json'
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
    elif file_type == 'json':
        ext = '.json'
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
    Delete session-scoped uploads and generated report files.

    :param upload_paths: uploaded file paths provided by the client session
    :param report_paths: report paths provided by the client session
    :param upload_dir: allowed uploads directory
    :param output_dir: allowed output directory for generated reports
    :return: number of deleted files
    """
    deleted_count = 0
    deleted_count += _delete_paths(upload_paths, allowed_root=upload_dir, html_only=False)
    deleted_count += _delete_paths(report_paths, allowed_root=output_dir, html_only=False)
    return deleted_count


def _delete_paths(paths: list[str], *, allowed_root: Path, html_only: bool) -> int:
    """
    Delete existing files under allowed root, optionally restricting to HTML files.
    """
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
    """
    Delete .bam.bai / .bai sidecar files for one BAM path when present.
    """
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
    """
    Return whether path is root or contained within root.
    """
    return path == root or root in path.parents
