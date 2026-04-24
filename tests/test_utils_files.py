"""Tests for utility file path helpers."""

from __future__ import annotations

from pathlib import Path

from respro.utils.files import resolve_output_file


class TestResolveOutputFile:
    def test_existing_directory_appends_default_filename(self, tmp_path: Path) -> None:
        result = resolve_output_file(tmp_path, 'default.db')
        assert result == tmp_path / 'default.db'

    def test_suffix_path_is_treated_as_filename(self) -> None:
        result = resolve_output_file(Path('reports/custom.report.html'), 'default.report.html')
        assert result == Path('reports/custom.report.html')

    def test_path_without_suffix_is_treated_as_directory(self) -> None:
        result = resolve_output_file(Path('output'), 'default.report.html')
        assert result == Path('output/default.report.html')
