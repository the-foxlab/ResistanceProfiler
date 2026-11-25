"""Unit tests for streamed upload handling."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pysam
import pytest

from respro.io.vcf import parse_vcf
from tests.conftest import MINIMAL_BAM_BYTES, TINY_REF_NAME
from web.backend.services import upload as upload_service


class _FakeUploadFile:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self._offset >= len(self._payload):
            return b''
        if size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        end = min(len(self._payload), self._offset + size)
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk


class TestSaveUploadStream:
    def test_streamed_fasta_upload_is_written_with_exact_size(self, tmp_path: Path) -> None:
        payload = b'>seq1\nATCGATCG\n'
        upload = _FakeUploadFile(payload)

        saved_path, size_bytes = asyncio.run(
            upload_service.save_upload_stream(upload, 'fasta', tmp_path, chunk_size=4)
        )

        assert size_bytes == len(payload)
        assert saved_path.exists()
        assert saved_path.read_bytes() == payload

    def test_streamed_upload_reads_multiple_chunks(self, tmp_path: Path) -> None:
        payload = b'>seq\nATCGATCGATCGATCG\n'
        upload = _FakeUploadFile(payload)

        asyncio.run(upload_service.save_upload_stream(upload, 'fasta', tmp_path, chunk_size=5))

        assert len(upload.read_sizes) > 2
        assert all(size == 5 for size in upload.read_sizes[:-1])

    def test_vcf_header_detection_handles_chunk_boundaries(self, tmp_path: Path) -> None:
        payload = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n'
        upload = _FakeUploadFile(payload)

        saved_path, _ = asyncio.run(
            upload_service.save_upload_stream(upload, 'vcf', tmp_path, chunk_size=3)
        )

        assert saved_path.exists()
        assert saved_path.read_bytes() == payload

    def test_oversized_stream_upload_is_rejected_and_temp_file_removed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(upload_service, '_max_size_for_type', lambda _file_type: 8)
        payload = b'>seq\nATCGATCG\n'
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='FASTA file exceeds maximum size'):
            asyncio.run(upload_service.save_upload_stream(upload, 'fasta', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_each_upload_type_uses_its_own_size_limit(self) -> None:
        """Each upload type resolves its dedicated configured limit key."""
        defaults = upload_service.WEB_BACKEND_CONFIG.defaults
        expected = {
            'fasta': defaults.upload_max_fasta_size,
            'vcf': defaults.upload_max_vcf_size,
            'bam': defaults.upload_max_bam_size,
            'json': defaults.upload_max_json_size,
        }
        for file_type, size in expected.items():
            assert upload_service._max_size_for_type(file_type) == size


class TestJsonUploadValidation:
    def _valid_results_json(self) -> bytes:
        payload = {
            'run': {
                'project_name': 'demo',
                'reference_name': 'tiny_ref',
                'sample_name': 'sample1',
                'vcf_path': 'sample.vcf',
                'total_variants': 0,
                'variants_in_cds': 0,
                'resistance_hits': 0,
                'created_at': '2026-04-21T10:00:00',
            },
            'variant_result': [],
            'coverage_gap': [],
            'formula_rule_hit': [],
            'sample_classification': [],
        }
        return json.dumps(payload).encode('utf-8')

    def test_json_size_limit_is_independent_of_vcf_limit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A JSON upload exceeding max_json_size is rejected even when it fits the VCF limit."""
        stub_config = SimpleNamespace(
            defaults=SimpleNamespace(
                upload_max_fasta_size=1024,
                upload_max_vcf_size=1048576,
                upload_max_bam_size=1024,
                upload_max_json_size=8,
                upload_bgzf_header_bytes=18,
            ),
        )
        monkeypatch.setattr(upload_service, 'WEB_BACKEND_CONFIG', stub_config)
        payload = self._valid_results_json()
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='JSON file exceeds maximum size'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_multibyte_utf8_across_chunk_boundaries_is_accepted(
        self,
        tmp_path: Path,
    ) -> None:
        """A multi-byte UTF-8 character split across chunks validates successfully."""
        payload = self._valid_results_json()
        payload = payload.replace(b'"sample1"', b'"s\xc3\xa4mple1"')
        upload = _FakeUploadFile(payload)

        saved_path, size_bytes = asyncio.run(
            upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=1)
        )

        assert size_bytes == len(payload)
        assert saved_path.read_bytes() == payload

    def test_json_invalid_utf8_is_rejected_and_temp_file_removed(
        self,
        tmp_path: Path,
    ) -> None:
        payload = b'{"run": "\xff\xfe"}'
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='JSON upload must be valid UTF-8 text'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_truncated_multibyte_sequence_at_eof_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        """An incomplete multi-byte sequence at end of stream fails the final decoder flush."""
        payload = b'{"run": "\xc3'
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='JSON upload must be valid UTF-8 text'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_truncated_multibyte_sequence_midstream_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        """A broken multi-byte sequence followed by more input fails during streaming."""
        payload = b'{"run": "\xc3"}'
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='JSON upload must be valid UTF-8 text'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_empty_upload_is_rejected(self, tmp_path: Path) -> None:
        upload = _FakeUploadFile(b'   \n  ')

        with pytest.raises(ValueError, match='JSON upload is empty'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_semantic_validation_still_runs_after_write(
        self,
        tmp_path: Path,
    ) -> None:
        """Structurally valid UTF-8 JSON without the results schema is rejected."""
        upload = _FakeUploadFile(b'{"unrelated": true}')

        with pytest.raises(ValueError, match='Invalid results JSON'):
            asyncio.run(upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []

    def test_json_valid_results_payload_is_accepted(self, tmp_path: Path) -> None:
        payload = self._valid_results_json()
        upload = _FakeUploadFile(payload)

        saved_path, size_bytes = asyncio.run(
            upload_service.save_upload_stream(upload, 'json', tmp_path, chunk_size=7)
        )

        assert size_bytes == len(payload)
        assert saved_path.read_bytes() == payload


class _CountingFakeUploadFile:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.reads = 0

    async def read(self, size: int = -1) -> bytes:
        del size
        self.reads += 1
        if not self._chunks:
            return b''
        return self._chunks.pop(0)


class _CancellingFakeUploadFile:
    def __init__(self, initial_chunks: list[bytes]) -> None:
        self._chunks = list(initial_chunks)

    async def read(self, size: int = -1) -> bytes:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        raise asyncio.CancelledError


class TestSecurityAndReliabilityRegression:
    """Regression & adversarial tests for S2/WA-014, WA-015, WA-037, and S5-B4."""

    @staticmethod
    def _valid_vcf_bytes(rows: int = 2) -> bytes:
        header = (
            '##fileformat=VCFv4.2\n'
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        )
        body = ''.join(f'{TINY_REF_NAME}\t{4 + i * 3}\t.\tA\tG\t100\tPASS\tAF=0.5\n' for i in range(rows))
        return (header + body).encode('utf-8')

    def test_bam_magic_rejected_on_chunk_one_before_reading_rest(self, tmp_path: Path) -> None:
        stream = _CountingFakeUploadFile([b'\x00' * 32, b'\x00' * 65536])

        with pytest.raises(ValueError, match='BGZF'):
            asyncio.run(upload_service.save_upload_stream(stream, 'bam', tmp_path, chunk_size=32))

        assert stream.reads == 1
        assert list(tmp_path.iterdir()) == []

    def test_bam_split_chunk_validation_and_small_file_rejection(self, tmp_path: Path) -> None:
        # Corrupt bsize across chunk boundary (5-byte chunks -> fails at chunk 4 when 18 bytes reached)
        bad_header = bytearray(MINIMAL_BAM_BYTES)
        bad_header[16:18] = (0).to_bytes(2, 'little')
        bad_stream = _FakeUploadFile(bytes(bad_header) + b'\x00' * 50)
        with pytest.raises(ValueError, match='BGZF'):
            asyncio.run(upload_service.save_upload_stream(bad_stream, 'bam', tmp_path, chunk_size=5))
        assert len(bad_stream.read_sizes) == 4
        assert list(tmp_path.iterdir()) == []

        # Valid MINIMAL_BAM_BYTES across 5-byte chunks succeeds
        good_path, good_size = asyncio.run(
            upload_service.save_upload_stream(_FakeUploadFile(MINIMAL_BAM_BYTES), 'bam', tmp_path, chunk_size=5)
        )
        assert good_size == len(MINIMAL_BAM_BYTES)
        assert good_path.suffix == '.bam'

        # Fewer than 18 bytes raises BAM file is too small
        with pytest.raises(ValueError, match='too small'):
            asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(b'\x1f\x8b' * 5), 'bam', tmp_path))

    def test_line_buffer_enforces_max_line_length_midstream(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stub_config = SimpleNamespace(
            defaults=SimpleNamespace(
                upload_max_fasta_size=1024 * 1024,
                upload_max_vcf_size=1024 * 1024,
                upload_max_bam_size=1024 * 1024,
                upload_max_json_size=1024 * 1024,
                upload_chunk_size=16,
                upload_max_fasta_line_length=16,
                upload_max_vcf_line_length=16,
                upload_max_vcf_data_lines=100,
                upload_bgzf_header_bytes=18,
            ),
        )
        monkeypatch.setattr(upload_service, 'WEB_BACKEND_CONFIG', stub_config)

        fasta_stream = _CountingFakeUploadFile([b'>seq\n', b'A' * 10, b'A' * 10, b'A' * 100])
        with pytest.raises(ValueError, match='FASTA file contains line 2 longer than 16 characters'):
            asyncio.run(upload_service.save_upload_stream(fasta_stream, 'fasta', tmp_path, chunk_size=16))
        assert fasta_stream.reads == 3
        assert list(tmp_path.iterdir()) == []

        vcf_stream = _CountingFakeUploadFile([b'##fileformat=VCF\n', b'#' + b'A' * 10, b'A' * 10, b'\n'])
        with pytest.raises(ValueError, match='VCF file contains line 2 longer than 16 characters'):
            asyncio.run(upload_service.save_upload_stream(vcf_stream, 'vcf', tmp_path, chunk_size=16))
        assert vcf_stream.reads == 3
        assert list(tmp_path.iterdir()) == []

        # Boundary: exact 16-char line with \r\n split across chunks succeeds (line_buffer == 17 during split)
        crlf_fasta = _CountingFakeUploadFile([b'>seq\r\n', b'A' * 16 + b'\r', b'\n'])
        saved, _ = asyncio.run(upload_service.save_upload_stream(crlf_fasta, 'fasta', tmp_path, chunk_size=17))
        assert saved.exists()

    def test_vcf_gz_streaming_decompression_persists_bgzf_readable_by_pysam(self, tmp_path: Path) -> None:
        raw_vcf = self._valid_vcf_bytes(rows=3)
        compressed = gzip.compress(raw_vcf)

        for chunk_size in (1, 3, 64):
            saved_path, size_bytes = asyncio.run(
                upload_service.save_upload_stream(
                    _FakeUploadFile(compressed),
                    'vcf',
                    tmp_path,
                    chunk_size=chunk_size,
                )
            )
            assert saved_path.name.endswith('.vcf.gz')
            assert size_bytes == len(compressed)
            assert gzip.decompress(saved_path.read_bytes()) == raw_vcf
            variants = parse_vcf(saved_path)
            assert len(variants) == 3

        # Plain .vcf keeps .vcf suffix
        plain_path, _ = asyncio.run(
            upload_service.save_upload_stream(_FakeUploadFile(raw_vcf), 'vcf', tmp_path, chunk_size=3)
        )
        assert plain_path.suffix == '.vcf'
        assert not plain_path.name.endswith('.vcf.gz')

    def test_vcf_gz_accepts_multiblock_bgzf(self, tmp_path: Path) -> None:
        raw_vcf = self._valid_vcf_bytes(rows=3000)
        assert len(raw_vcf) > 70_000
        bgzf_src = tmp_path / 'source_bgzf.vcf.gz'
        with pysam.BGZFile(str(bgzf_src), 'wb', None) as handle:
            handle.write(raw_vcf)
        bgzf_bytes = bgzf_src.read_bytes()
        bgzf_src.unlink()

        upload_dir = tmp_path / 'uploads'
        saved_path, size_bytes = asyncio.run(
            upload_service.save_upload_stream(_FakeUploadFile(bgzf_bytes), 'vcf', upload_dir, chunk_size=4096)
        )
        assert saved_path.name.endswith('.vcf.gz')
        assert size_bytes == len(bgzf_bytes)
        assert len(parse_vcf(saved_path)) == 3000

    def test_vcf_gz_rejects_gzip_bomb_malformed_truncated_and_trailing_garbage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(upload_service, '_max_size_for_type', lambda _file_type: 1024)
        # max_decompressed = 1024 * 16 = 16384 bytes; bomb is < 1024 compressed, > 16384 decompressed
        header = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        row = f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.5\n'.encode()
        bomb = gzip.compress(header + row * 1000, compresslevel=9)
        assert len(bomb) < 1024
        with pytest.raises(ValueError, match='Decompressed VCF file exceeds maximum size'):
            asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(bomb), 'vcf', tmp_path, chunk_size=64))
        assert list(tmp_path.iterdir()) == []

        # Restore normal VCF size limit for structural gzip/VCF failure checks
        monkeypatch.undo()
        valid_gz = gzip.compress(self._valid_vcf_bytes(rows=1))
        bad_payloads = [
            (b'\x1f\x8b\x08\x00corrupt', 'truncated or malformed gzip data'),
            (b'\x1f\x8b', 'truncated or malformed gzip data'),
            (valid_gz[:-4], 'truncated or malformed gzip data'),
            (valid_gz + b'trailing', 'invalid gzip data'),
            (valid_gz + b'\x1f', 'truncated or malformed gzip data'),
            (gzip.compress(b'not a vcf\n'), 'data rows before #CHROM header'),
        ]
        for bad_bytes, pattern in bad_payloads:
            with pytest.raises(ValueError, match=pattern):
                asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(bad_bytes), 'vcf', tmp_path, chunk_size=7))
            assert list(tmp_path.iterdir()) == []

    def test_vcf_short_eof_and_compressed_size_cap_leave_no_temp_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(ValueError):
            asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(b'#'), 'vcf', tmp_path, chunk_size=1))
        assert list(tmp_path.iterdir()) == []

        monkeypatch.setattr(upload_service, '_max_size_for_type', lambda _file_type: 16)
        compressed = gzip.compress(self._valid_vcf_bytes(rows=2))
        assert len(compressed) > 16
        with pytest.raises(ValueError, match='VCF file exceeds maximum size'):
            asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(compressed), 'vcf', tmp_path, chunk_size=8))
        assert list(tmp_path.iterdir()) == []

    def test_cancelled_error_cleans_up_partial_plain_and_gzipped_uploads(self, tmp_path: Path) -> None:
        # Cancel before chunk 1
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(upload_service.save_upload_stream(_CancellingFakeUploadFile([]), 'fasta', tmp_path))
        assert list(tmp_path.iterdir()) == []

        # Cancel mid-stream on plain FASTA
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(upload_service.save_upload_stream(_CancellingFakeUploadFile([b'>seq1\n']), 'fasta', tmp_path))
        assert list(tmp_path.iterdir()) == []

        # Cancel mid-stream on .vcf.gz (with open BGZF handle)
        partial_gz = gzip.compress(self._valid_vcf_bytes(rows=1))[:10]
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                upload_service.save_upload_stream(
                    _CancellingFakeUploadFile([partial_gz[:4], partial_gz[4:]]),
                    'vcf',
                    tmp_path,
                    chunk_size=4,
                )
            )
        assert list(tmp_path.iterdir()) == []

    def test_fasta_validator_rejects_del_embedded_cr_header_only_and_headerless(self, tmp_path: Path) -> None:
        invalid_cases = [
            (b'>seq1\nATG\x7fAAA\n', 'non-text/binary bytes'),
            (b'>seq1\nATG\rAAA\n', 'non-text/binary bytes'),
            (b'>seq1\rATG\nATG\n', 'non-text/binary bytes'),
            (b'>seq1\n', 'valid sequence data'),
            (b'ATGATG\n', 'valid sequence data'),
        ]
        for payload, pattern in invalid_cases:
            with pytest.raises(ValueError, match=pattern):
                asyncio.run(upload_service.save_upload_stream(_FakeUploadFile(payload), 'fasta', tmp_path))
            assert list(tmp_path.iterdir()) == []

        # Valid CRLF FASTA succeeds
        saved, _ = asyncio.run(
            upload_service.save_upload_stream(_FakeUploadFile(b'>seq1\r\nATGATG\r\n'), 'fasta', tmp_path)
        )
        assert saved.exists()
