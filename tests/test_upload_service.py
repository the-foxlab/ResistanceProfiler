"""Unit tests for streamed upload handling."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
        monkeypatch.setattr(upload_service, 'MAX_FASTA_SIZE', 8)
        payload = b'>seq\nATCGATCG\n'
        upload = _FakeUploadFile(payload)

        with pytest.raises(ValueError, match='FASTA file exceeds maximum size'):
            asyncio.run(upload_service.save_upload_stream(upload, 'fasta', tmp_path, chunk_size=4))

        assert list(tmp_path.iterdir()) == []
