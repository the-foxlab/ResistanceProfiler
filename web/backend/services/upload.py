"""Incremental upload streaming and per-type validation (FASTA, VCF, BAM, results JSON)."""

from __future__ import annotations

import codecs
import logging
import os
import tempfile
import zlib
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

import pysam

from respro.db.results import load_run_from_json
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.startup_config import is_path_within_allowed_roots

logger = logging.getLogger(__name__)

UploadType = Literal['fasta', 'vcf', 'bam', 'json']

# ASCII printable characters (0x20-0x7E) plus HT, LF, CR; 0x7F DEL is excluded.
_ALLOWED_TEXT_BYTES = bytes([9, 10, 13, *range(32, 127)])
_ALLOWED_FASTA_BYTES = b'ACGTURYKMSWBDHVN-.*'
_CANONICAL_BASES = (b'A', b'C', b'G', b'T', b'N')
_VCF_GZ_MAGIC = b'\x1f\x8b'
_VCF_GZ_MAX_EXPANSION_FACTOR = 16

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


def _strip_trailing_cr(line: bytes) -> bytes:
    """Strip at most one trailing ``\\r`` (CRLF line ending) while preserving embedded ``\\r``."""
    return line[:-1] if line.endswith(b'\r') else line


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
            self._validate_line(_strip_trailing_cr(line))
        max_len = self._max_line_length()
        if len(self.line_buffer) > max_len + 1:
            raise ValueError(
                f'{self.label} file contains line {self.line_number + 1} longer than {max_len} characters'
            )

    def finalize(self) -> None:
        if self.line_buffer:
            self._validate_line(_strip_trailing_cr(self.line_buffer))
            self.line_buffer = b''
        if not self.has_non_whitespace:
            raise ValueError(f'{self.label} file is empty')
        self._check_complete()

    def _max_line_length(self) -> int:
        raise NotImplementedError

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

    def _max_line_length(self) -> int:
        return WEB_BACKEND_CONFIG.defaults.upload_max_fasta_line_length

    def _validate_line(self, raw_line: bytes) -> None:
        self.line_number += 1
        max_len = self._max_line_length()
        if len(raw_line) > max_len:
            raise ValueError(f'FASTA file contains line {self.line_number} longer than {max_len} characters')
        if b'\r' in raw_line:
            raise ValueError('FASTA file contains non-text/binary bytes')

        stripped = raw_line.strip()
        if not stripped:
            return
        if self.starts_with_header is None:
            self.starts_with_header = stripped.startswith(b'>')
        if stripped.startswith(b'>'):
            return

        compact = stripped.replace(b' ', b'').replace(b'\t', b'').upper()
        if compact.translate(None, _ALLOWED_FASTA_BYTES):
            raise ValueError('FASTA file contains invalid sequence characters')
        if not self.has_sequence_char and any(base in compact for base in _CANONICAL_BASES):
            self.has_sequence_char = True

    def _check_complete(self) -> None:
        if not (self.starts_with_header and self.has_sequence_char):
            raise ValueError('FASTA file does not appear to contain valid sequence data')


class _VcfValidator(_LineStreamValidator):
    label = 'VCF'

    def __init__(self) -> None:
        super().__init__()
        self.has_vcf_header = False
        self.has_column_header = False
        self.data_lines = 0

    def _max_line_length(self) -> int:
        return WEB_BACKEND_CONFIG.defaults.upload_max_vcf_line_length

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
        self._magic_checked = False

    def feed(self, chunk: bytes) -> None:
        header_len = WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes
        if not self._magic_checked:
            self.first_bytes += chunk[: header_len - len(self.first_bytes)]
            if len(self.first_bytes) >= header_len:
                self._validate_bgzf_header(self.first_bytes)
                self._magic_checked = True

    def finalize(self) -> None:
        if not self._magic_checked:
            if len(self.first_bytes) < WEB_BACKEND_CONFIG.defaults.upload_bgzf_header_bytes:
                raise ValueError('BAM file is too small')
            self._validate_bgzf_header(self.first_bytes)
            self._magic_checked = True

    @staticmethod
    def _validate_bgzf_header(header: bytes) -> None:
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


class _GzipVcfStream:
    """Stream-decompress gzip/BGZF VCF uploads into ``_VcfValidator`` and a BGZF writer."""

    def __init__(
        self,
        *,
        validator: _StreamValidator,
        bgz_handle: pysam.BGZFile,
        max_decompressed_size: int,
    ) -> None:
        self._validator = validator
        self._bgz_handle = bgz_handle
        self._max_decompressed_size = max_decompressed_size
        self._decompressed_size = 0
        self._decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._member_active = False

    def _size_exceeded_error(self) -> ValueError:
        if self._max_decompressed_size >= 1024 * 1024:
            return ValueError(
                f'Decompressed VCF file exceeds maximum size of {self._max_decompressed_size // (1024 * 1024)} MB'
            )
        return ValueError(f'Decompressed VCF file exceeds maximum size of {self._max_decompressed_size} bytes')

    def feed(self, chunk: bytes) -> None:
        pending = chunk
        while pending:
            self._member_active = True
            remaining = self._max_decompressed_size - self._decompressed_size
            try:
                decompressed = self._decompressor.decompress(pending, max_length=remaining + 1)
            except zlib.error as exc:
                raise ValueError('VCF file contains invalid gzip data') from exc

            if decompressed:
                self._decompressed_size += len(decompressed)
                if self._decompressed_size > self._max_decompressed_size:
                    raise self._size_exceeded_error()
                self._validator.feed(decompressed)
                self._bgz_handle.write(decompressed)

            if self._decompressor.eof:
                pending = self._decompressor.unused_data
                self._decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                self._member_active = False
                continue

            if self._decompressor.unconsumed_tail:
                raise self._size_exceeded_error()
            pending = b''

    def finalize(self) -> None:
        if self._member_active:
            remaining = self._max_decompressed_size - self._decompressed_size
            try:
                tail = self._decompressor.flush(remaining + 1)
            except zlib.error as exc:
                raise ValueError('VCF file contains invalid gzip data') from exc
            if tail:
                self._decompressed_size += len(tail)
                if self._decompressed_size > self._max_decompressed_size:
                    raise self._size_exceeded_error()
                self._validator.feed(tail)
                self._bgz_handle.write(tail)
            if not self._decompressor.eof:
                raise ValueError('VCF file contains truncated or malformed gzip data')
        self._validator.finalize()


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


def _max_decompressed_vcf_size(max_compressed_size: int) -> int:
    return max_compressed_size * _VCF_GZ_MAX_EXPANSION_FACTOR


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
    temp_path: Path | None = None
    handle: BinaryIO | None = None
    bgz_handle: pysam.BGZFile | None = None
    total_size = 0

    try:
        if file_type == 'vcf':
            prefix = bytearray()
            while len(prefix) < 2:
                chunk = await upload_file.read(effective_chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise _size_limit_error(file_type, max_size)
                prefix.extend(chunk)
            if len(prefix) < 2:
                if prefix:
                    validator.feed(bytes(prefix))
                validator.finalize()
                raise ValueError('VCF file does not appear to have valid VCF headers')

            initial_bytes = bytes(prefix)
            if initial_bytes.startswith(_VCF_GZ_MAGIC):
                fd, temp_path_str = tempfile.mkstemp(suffix='.vcf.gz', dir=str(upload_dir))
                temp_path = Path(temp_path_str)
                os.close(fd)
                bgz_handle = pysam.BGZFile(temp_path_str, 'wb', None)
                gzip_stream = _GzipVcfStream(
                    validator=validator,
                    bgz_handle=bgz_handle,
                    max_decompressed_size=_max_decompressed_vcf_size(max_size),
                )
                gzip_stream.feed(initial_bytes)
                while chunk := await upload_file.read(effective_chunk_size):
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise _size_limit_error(file_type, max_size)
                    gzip_stream.feed(chunk)
                gzip_stream.finalize()
            else:
                fd, temp_path_str = tempfile.mkstemp(suffix=_EXTENSIONS[file_type], dir=str(upload_dir))
                temp_path = Path(temp_path_str)
                handle = os.fdopen(fd, 'wb')
                validator.feed(initial_bytes)
                handle.write(initial_bytes)
                while chunk := await upload_file.read(effective_chunk_size):
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise _size_limit_error(file_type, max_size)
                    validator.feed(chunk)
                    handle.write(chunk)
                validator.finalize()
        else:
            fd, temp_path_str = tempfile.mkstemp(suffix=_EXTENSIONS[file_type], dir=str(upload_dir))
            temp_path = Path(temp_path_str)
            handle = os.fdopen(fd, 'wb')
            while chunk := await upload_file.read(effective_chunk_size):
                total_size += len(chunk)
                if total_size > max_size:
                    raise _size_limit_error(file_type, max_size)
                validator.feed(chunk)
                handle.write(chunk)
            validator.finalize()

        if bgz_handle is not None:
            active_bgz = bgz_handle
            bgz_handle = None
            active_bgz.close()
        if handle is not None:
            active_handle = handle
            handle = None
            active_handle.close()

        if file_type == 'json':
            load_run_from_json(temp_path)
    except BaseException:
        if bgz_handle is not None:
            try:
                bgz_handle.close()
            except OSError as close_exc:
                logger.warning('Failed closing BGZF temp handle during upload cleanup: %s', close_exc)
        if handle is not None:
            try:
                handle.close()
            except OSError as close_exc:
                logger.warning('Failed closing upload temp handle during cleanup: %s', close_exc)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

    # Defense-in-depth: confirm the created path stays confined within upload_dir.
    if not is_path_within_allowed_roots(temp_path, (upload_dir,)):
        temp_path.unlink(missing_ok=True)
        raise ValueError('Upload path escapes target directory')

    return temp_path, total_size
