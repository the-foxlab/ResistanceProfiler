"""Incremental upload streaming and per-type validation (FASTA, VCF, BAM, results JSON)."""

from __future__ import annotations

import codecs
import os
import tempfile
from pathlib import Path
from typing import Literal, Protocol

from respro.db.results import load_run_from_json
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.startup_config import is_path_within_allowed_roots

UploadType = Literal['fasta', 'vcf', 'bam', 'json']

# ASCII printable characters plus HT, LF, CR.
_ALLOWED_TEXT_BYTES = bytes([9, 10, 13, *range(32, 128)])
_ALLOWED_FASTA_BYTES = b'ACGTURYKMSWBDHVN-.*'
_CANONICAL_BASES = (b'A', b'C', b'G', b'T', b'N')

_EXTENSIONS: dict[UploadType, str] = {
    'fasta': '.fa',
    'vcf': '.vcf',
    'json': '.json',
    'bam': '.bam',
}


class UploadStream(Protocol):
    """Minimal async file API required for streamed uploads."""

    async def read(self, size: int = -1) -> bytes: ...


class _StreamValidator(Protocol):
    def feed(self, chunk: bytes) -> None: ...
    def finalize(self) -> None: ...


class _LineStreamValidator:
    """Shared ASCII-text and line-buffering stream validator for FASTA and VCF uploads."""

    label: str = ''

    def __init__(self) -> None:
        self.line_buffer = b''
        self.line_number = 0
        self.has_non_whitespace = False

    def feed(self, chunk: bytes) -> None:
        if chunk.translate(None, _ALLOWED_TEXT_BYTES):
            raise ValueError(f'{self.label} file contains non-text/binary bytes')
        if chunk.strip():
            self.has_non_whitespace = True
        lines = (self.line_buffer + chunk).split(b'\n')
        self.line_buffer = lines.pop()
        for line in lines:
            self._validate_line(line.rstrip(b'\r'))

    def finalize(self) -> None:
        if self.line_buffer:
            self._validate_line(self.line_buffer.rstrip(b'\r'))
            self.line_buffer = b''
        if not self.has_non_whitespace:
            raise ValueError(f'{self.label} file is empty')
        self._check_complete()

    def _validate_line(self, raw_line: bytes) -> None:
        raise NotImplementedError

    def _check_complete(self) -> None:
        raise NotImplementedError


class _FastaValidator(_LineStreamValidator):
    label = 'FASTA'

    def __init__(self) -> None:
        super().__init__()
        self.starts_with_header: bool | None = None
        self.has_sequence_char = False

    def _validate_line(self, raw_line: bytes) -> None:
        self.line_number += 1
        max_len = WEB_BACKEND_CONFIG.defaults.upload_max_fasta_line_length
        if len(raw_line) > max_len:
            raise ValueError(f'FASTA file contains line {self.line_number} longer than {max_len} characters')

        stripped = raw_line.strip()
        if not stripped:
            return
        if self.starts_with_header is None:
            self.starts_with_header = stripped.startswith(b'>')
        if stripped.startswith(b'>'):
            return

        compact = stripped.replace(b' ', b'').replace(b'\t', b'').upper()
        if not compact:
            return
        if compact.translate(None, _ALLOWED_FASTA_BYTES):
            raise ValueError('FASTA file contains invalid sequence characters')
        if not self.has_sequence_char and any(base in compact for base in _CANONICAL_BASES):
            self.has_sequence_char = True

    def _check_complete(self) -> None:
        if not (self.starts_with_header or self.has_sequence_char):
            raise ValueError('FASTA file does not appear to contain valid sequence data')


class _VcfValidator(_LineStreamValidator):
    label = 'VCF'

    def __init__(self) -> None:
        super().__init__()
        self.has_vcf_header = False
        self.has_column_header = False
        self.data_lines = 0

    def _validate_line(self, raw_line: bytes) -> None:
        self.line_number += 1
        defaults = WEB_BACKEND_CONFIG.defaults
        if len(raw_line) > defaults.upload_max_vcf_line_length:
            raise ValueError(
                f'VCF file contains line {self.line_number} longer than '
                f'{defaults.upload_max_vcf_line_length} characters'
            )

        stripped = raw_line.strip()
        if not stripped:
            return
        if stripped.startswith(b'##fileformat=VCF'):
            self.has_vcf_header = True
            return
        if stripped.startswith(b'#CHROM'):
            self.has_column_header = True
            return
        if stripped.startswith(b'#'):
            return
        if not self.has_column_header:
            raise ValueError('VCF file contains data rows before #CHROM header')

        self.data_lines += 1
        if self.data_lines > defaults.upload_max_vcf_data_lines:
            raise ValueError(f'VCF file exceeds maximum data row count of {defaults.upload_max_vcf_data_lines}')

    def _check_complete(self) -> None:
        if not (self.has_vcf_header and self.has_column_header):
            raise ValueError('VCF file does not appear to have valid VCF headers')


class _JsonValidator:
    def __init__(self) -> None:
        self.has_non_whitespace = False
        self.decoder = codecs.getincrementaldecoder('utf-8')()

    def feed(self, chunk: bytes) -> None:
        if b'\x00' in chunk:
            raise ValueError('JSON upload must be valid UTF-8 text')
        try:
            self.decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise ValueError('JSON upload must be valid UTF-8 text') from exc
        if chunk.strip():
            self.has_non_whitespace = True

    def finalize(self) -> None:
        try:
            self.decoder.decode(b'', final=True)
        except UnicodeDecodeError as exc:
            raise ValueError('JSON upload must be valid UTF-8 text') from exc
        if not self.has_non_whitespace:
            raise ValueError('JSON upload is empty')


class _BamValidator:
    def __init__(self) -> None:
        self.first_bytes = b''

    def feed(self, chunk: bytes) -> None:
        header_len = WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes
        if len(self.first_bytes) < header_len:
            self.first_bytes += chunk[: header_len - len(self.first_bytes)]

    def finalize(self) -> None:
        header = self.first_bytes
        if len(header) < WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes:
            raise ValueError('BAM file is too small')
        invalid = (
            header[:2] != b'\x1f\x8b'
            or header[2] != 8
            or (header[3] & 0x04) == 0
            or int.from_bytes(header[10:12], 'little') < 6
            or header[12:14] != b'BC'
            or header[14:16] != b'\x02\x00'
            or not (26 <= int.from_bytes(header[16:18], 'little') + 1 <= 65536)
        )
        if invalid:
            raise ValueError('BAM file does not have valid BGZF/gzip magic signature')


_VALIDATORS: dict[UploadType, type[_StreamValidator]] = {
    'fasta': _FastaValidator,
    'vcf': _VcfValidator,
    'json': _JsonValidator,
    'bam': _BamValidator,
}


def _max_size_for_type(file_type: UploadType) -> int:
    defaults = WEB_BACKEND_CONFIG.defaults
    return {
        'fasta': defaults.upload_max_fasta_size,
        'vcf': defaults.upload_max_vcf_size,
        'json': defaults.upload_max_json_size,
        'bam': defaults.upload_max_bam_size,
    }[file_type]


def _size_limit_error(file_type: UploadType, max_size: int) -> ValueError:
    if file_type == 'bam':
        return ValueError(f'BAM file exceeds maximum size of {max_size // (1024 * 1024 * 1024)} GB')
    return ValueError(f'{file_type.upper()} file exceeds maximum size of {max_size // (1024 * 1024)} MB')


async def save_upload_stream(
    upload_file: UploadStream,
    file_type: UploadType,
    upload_dir: Path,
    *,
    chunk_size: int | None = None,
) -> tuple[Path, int]:
    """Validate and persist an async upload stream into *upload_dir*, returning ``(path, size_bytes)``."""
    effective_chunk_size = chunk_size or WEB_BACKEND_CONFIG.defaults.upload_chunk_size
    max_size = _max_size_for_type(file_type)
    validator = _VALIDATORS[file_type]()

    upload_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path_str = tempfile.mkstemp(suffix=_EXTENSIONS[file_type], dir=str(upload_dir))
    temp_path = Path(temp_path_str)

    total_size = 0
    try:
        with os.fdopen(fd, 'wb') as handle:
            while chunk := await upload_file.read(effective_chunk_size):
                total_size += len(chunk)
                if total_size > max_size:
                    raise _size_limit_error(file_type, max_size)
                validator.feed(chunk)
                handle.write(chunk)

        validator.finalize()
        if file_type == 'json':
            load_run_from_json(temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    # Defense-in-depth: confirm the created path stays confined within upload_dir.
    if not is_path_within_allowed_roots(temp_path, (upload_dir,)):
        temp_path.unlink(missing_ok=True)
        raise ValueError('Upload path escapes target directory')

    return temp_path, total_size
