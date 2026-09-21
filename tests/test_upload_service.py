"""Unit tests for streamed upload handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

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
