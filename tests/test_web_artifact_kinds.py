"""Registry-driven artifact kind tests."""

from __future__ import annotations

from pathlib import Path

from web.backend.artifact_kinds import (
    ARTIFACT_KINDS,
    ARTIFACT_MEDIA_TYPES,
    ARTIFACT_RESULT_FIELDS,
    derive_download_filename,
    is_allowed_artifact_path,
    is_html_report_path,
    unsupported_artifact_message,
)


class TestArtifactKindRegistry:
    """The registry is the single source for artifact suffixes and media types."""

    def test_registry_lists_every_exported_artifact_in_stable_order(self) -> None:
        assert [kind.download_suffix for kind in ARTIFACT_KINDS] == [
            '.html',
            '.json',
            '.pdf',
            '.tsv',
        ]

    def test_result_fields_and_media_types_are_derived_from_the_registry(self) -> None:
        assert ARTIFACT_RESULT_FIELDS == tuple(kind.result_field for kind in ARTIFACT_KINDS)
        assert ARTIFACT_MEDIA_TYPES == {
            kind.result_field: kind.media_type for kind in ARTIFACT_KINDS
        }

    def test_every_registry_suffix_is_an_allowed_artifact(self) -> None:
        for kind in ARTIFACT_KINDS:
            assert is_allowed_artifact_path(Path(f'/data/results/sample{kind.internal_suffix}'))

    def test_unknown_suffix_is_not_an_allowed_artifact(self) -> None:
        assert not is_allowed_artifact_path(Path('/data/results/sample.txt'))

    def test_download_filename_uses_the_registry_download_suffix(self) -> None:
        for kind in ARTIFACT_KINDS:
            path = Path(f'/data/results/sample.20240101120000000000{kind.internal_suffix}')
            assert derive_download_filename(path) == f'sample{kind.download_suffix}'

    def test_download_filename_keeps_unknown_names_unchanged(self) -> None:
        assert derive_download_filename(Path('/data/results/sample.txt')) == 'sample.txt'

    def test_html_report_detection_follows_the_html_kind(self) -> None:
        assert is_html_report_path(Path('/data/results/sample.report.html'))
        assert not is_html_report_path(Path('/data/results/sample.results.json'))

    def test_unsupported_message_lists_every_internal_suffix(self) -> None:
        message = unsupported_artifact_message()
        for kind in ARTIFACT_KINDS:
            assert kind.internal_suffix in message

    def test_backend_path_guard_accepts_every_registry_suffix(self) -> None:
        from web.backend.main import _is_allowed_artifact_path

        for kind in ARTIFACT_KINDS:
            assert _is_allowed_artifact_path(Path(f'/data/results/sample{kind.internal_suffix}'))
