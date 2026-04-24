"""
Tests for the maintained-db IO client and databases CLI command.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from respro.cli.main import app
from respro.io.maintained_db import (
    _fetch_genbank_records,
    _parse_reference_identifiers,
    download_database_files,
    fetch_database_metadata,
    list_maintained_databases,
    list_output_files,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _json_mock(payload: object) -> MagicMock:
    """Context-manager mock returning payload as JSON bytes."""
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _bytes_mock(content: bytes) -> MagicMock:
    """Context-manager mock returning raw bytes."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=content)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_DB_LISTING = [
    {'name': 'hsv_daehne_jaki', 'type': 'dir'},
    {'name': 'hiv_hivdb', 'type': 'dir'},
    {'name': 'README.md', 'type': 'file'},  # should be filtered out
]

_METADATA = {
    'maintainers': ['Daehne, Theo'],
    'contact': 'test@example.com',
    'description': 'HSV resistance database.',
    'license': 'CC-BY-4.0',
    'maintainer_update': '2026-04-23',
    'publication_pmid': '12345678',
    'website': 'https://example.com',
    'tsv_checksum': 'sha256:abc123',
}

_OUTPUT_LISTING = [
    {'name': 'rules.tsv', 'download_url': 'https://raw.test/rules.tsv'},
    {'name': 'metadata.json', 'download_url': 'https://raw.test/metadata.json'},
    {'name': 'formula-rules.tsv', 'download_url': 'https://raw.test/formula-rules.tsv'},
]

_RULES_TSV_CONTENT = (
    'gene\tposition\tref_aa\tmut_aa\tphenotype\treference_identifier\n'
    'UL23\t168\tA\tT\tresistant\tX04770\n'
    'UL30\t700\tL\tM\tresistant\tX04771\n'
    'UL23\t200\tR\tW\tresistant\tX04770\n'  # duplicate accession — should deduplicate
)

_GENBANK_CONTENT = b'LOCUS       X04770    1000 bp    DNA     linear   VRL 01-JAN-2000\n//\n'

runner = CliRunner()


# ── list_maintained_databases ─────────────────────────────────────────────────

class TestListMaintainedDatabases:
    def test_returns_sorted_dir_names(self) -> None:
        with patch('urllib.request.urlopen', return_value=_json_mock(_DB_LISTING)):
            result = list_maintained_databases()
        assert result == ['hiv_hivdb', 'hsv_daehne_jaki']

    def test_filters_out_non_dir_entries(self) -> None:
        with patch('urllib.request.urlopen', return_value=_json_mock(_DB_LISTING)):
            result = list_maintained_databases()
        assert 'README.md' not in result

    def test_raises_on_http_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=403, msg='Forbidden', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            with pytest.raises(RuntimeError, match='HTTP 403'):
                list_maintained_databases()

    def test_raises_on_network_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=OSError('no network')):
            with pytest.raises(RuntimeError, match='Network error'):
                list_maintained_databases()


# ── fetch_database_metadata ───────────────────────────────────────────────────

class TestFetchDatabaseMetadata:
    def test_returns_parsed_metadata(self) -> None:
        with patch('urllib.request.urlopen', return_value=_json_mock(_METADATA)):
            result = fetch_database_metadata('hsv_daehne_jaki')
        assert result['description'] == 'HSV resistance database.'
        assert result['license'] == 'CC-BY-4.0'

    def test_raises_on_http_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=404, msg='Not Found', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            with pytest.raises(RuntimeError, match='HTTP 404'):
                fetch_database_metadata('no_such_db')


# ── list_output_files ─────────────────────────────────────────────────────────

class TestListOutputFiles:
    def test_returns_file_listing(self) -> None:
        with patch('urllib.request.urlopen', return_value=_json_mock(_OUTPUT_LISTING)):
            result = list_output_files('hsv_daehne_jaki')
        assert len(result) == 3
        assert result[0]['name'] == 'rules.tsv'

    def test_raises_on_http_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=500, msg='Server Error', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            with pytest.raises(RuntimeError, match='HTTP 500'):
                list_output_files('hsv_daehne_jaki')


# ── _parse_reference_identifiers ─────────────────────────────────────────────

class TestParseReferenceIdentifiers:
    def test_returns_unique_sorted_accessions(self, tmp_path: Path) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text(_RULES_TSV_CONTENT)
        result = _parse_reference_identifiers(tsv)
        assert result == ['X04770', 'X04771']

    def test_returns_empty_when_column_missing(self, tmp_path: Path) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text('gene\tposition\n')
        result = _parse_reference_identifiers(tsv)
        assert result == []

    def test_skips_empty_values(self, tmp_path: Path) -> None:
        tsv = tmp_path / 'rules.tsv'
        tsv.write_text('gene\treference_identifier\nUL23\t\nUL30\tX04770\n')
        result = _parse_reference_identifiers(tsv)
        assert result == ['X04770']


# ── _fetch_genbank_records ────────────────────────────────────────────────────

class TestFetchGenbankRecords:
    def test_writes_gb_files(self, tmp_path: Path) -> None:
        with patch('urllib.request.urlopen', return_value=_bytes_mock(_GENBANK_CONTENT)):
            paths = _fetch_genbank_records(['X04770'], tmp_path)
        assert len(paths) == 1
        assert paths[0].name == 'X04770.gb'
        assert paths[0].read_bytes() == _GENBANK_CONTENT

    def test_raises_on_invalid_genbank_content(self, tmp_path: Path) -> None:
        with patch('urllib.request.urlopen', return_value=_bytes_mock(b'<html>error</html>')):
            with pytest.raises(RuntimeError, match='not a valid GenBank record'):
                _fetch_genbank_records(['X04770'], tmp_path)

    def test_raises_on_http_error(self, tmp_path: Path) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=400, msg='Bad Request', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            with pytest.raises(RuntimeError, match='HTTP 400'):
                _fetch_genbank_records(['BAD'], tmp_path)

    def test_fetches_multiple_accessions(self, tmp_path: Path) -> None:
        with patch('urllib.request.urlopen', return_value=_bytes_mock(_GENBANK_CONTENT)):
            paths = _fetch_genbank_records(['X04770', 'X04771'], tmp_path)
        assert len(paths) == 2
        assert {p.name for p in paths} == {'X04770.gb', 'X04771.gb'}


# ── download_database_files ───────────────────────────────────────────────────

class TestDownloadDatabaseFiles:
    def _make_download_side_effect(self, rules_content: bytes, metadata_content: bytes) -> object:
        """Return a side_effect callable that serves different content per URL."""
        call_count = [0]
        responses = [
            _json_mock(json.loads(json.dumps(_OUTPUT_LISTING))),  # list_output_files
            _bytes_mock(rules_content),                           # rules.tsv download
            _bytes_mock(metadata_content),                        # metadata.json download
            _bytes_mock(b'gene\tformula\n'),                      # formula-rules.tsv download
            _bytes_mock(_GENBANK_CONTENT),                        # X04770.gb
            _bytes_mock(_GENBANK_CONTENT),                        # X04771.gb
        ]

        def side_effect(*args, **kwargs):  # noqa: ANN001, ANN202
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

        return side_effect

    def test_returns_all_required_keys(self, tmp_path: Path) -> None:
        rules_bytes = _RULES_TSV_CONTENT.encode()
        meta_bytes = json.dumps(_METADATA).encode()

        se = self._make_download_side_effect(rules_bytes, meta_bytes)
        with patch('urllib.request.urlopen', side_effect=se):
            result = download_database_files('hsv_daehne_jaki', tmp_path)

        assert 'rules' in result
        assert 'metadata' in result
        assert 'formula_rules' in result
        assert 'genbank' in result

    def test_raises_when_rules_tsv_missing(self, tmp_path: Path) -> None:
        listing_no_rules = [
            {'name': 'metadata.json', 'download_url': 'https://raw.test/metadata.json'},
        ]
        with patch('urllib.request.urlopen', return_value=_json_mock(listing_no_rules)):
            with pytest.raises(RuntimeError, match='missing required rules.tsv'):
                download_database_files('hsv_daehne_jaki', tmp_path)

    def test_raises_when_metadata_json_missing(self, tmp_path: Path) -> None:
        listing_no_meta = [
            {'name': 'rules.tsv', 'download_url': 'https://raw.test/rules.tsv'},
        ]
        with patch('urllib.request.urlopen', return_value=_json_mock(listing_no_meta)):
            with pytest.raises(RuntimeError, match='missing required metadata.json'):
                download_database_files('hsv_daehne_jaki', tmp_path)

    def test_formula_rules_is_none_when_absent(self, tmp_path: Path) -> None:
        listing_no_formula = [
            {'name': 'rules.tsv', 'download_url': 'https://raw.test/rules.tsv'},
            {'name': 'metadata.json', 'download_url': 'https://raw.test/metadata.json'},
        ]
        rules_bytes = _RULES_TSV_CONTENT.encode()
        meta_bytes = json.dumps(_METADATA).encode()

        responses = [
            _json_mock(listing_no_formula),
            _bytes_mock(rules_bytes),
            _bytes_mock(meta_bytes),
            _bytes_mock(_GENBANK_CONTENT),  # X04770
            _bytes_mock(_GENBANK_CONTENT),  # X04771
        ]
        call_count = [0]

        def se(*args, **kwargs):  # noqa: ANN001, ANN202
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

        with patch('urllib.request.urlopen', side_effect=se):
            result = download_database_files('hsv_daehne_jaki', tmp_path)

        assert result['formula_rules'] is None


# ── CLI: databases --list ────────────────────────────────────────────────

class TestMaintainedDbListCommand:
    def test_list_prints_database_name(self) -> None:
        with (
            patch('respro.cli.maintained_db.list_maintained_databases', return_value=['hsv_daehne_jaki']),
            patch('respro.cli.maintained_db.fetch_database_metadata', return_value=_METADATA),
        ):
            result = runner.invoke(app, ['databases', '--list'])
        assert result.exit_code == 0
        assert 'hsv_daehne_jaki' in result.output

    def test_list_prints_metadata_fields(self) -> None:
        with (
            patch('respro.cli.maintained_db.list_maintained_databases', return_value=['hsv_daehne_jaki']),
            patch('respro.cli.maintained_db.fetch_database_metadata', return_value=_METADATA),
        ):
            result = runner.invoke(app, ['databases', '--list'])
        assert 'HSV resistance database.' in result.output
        assert 'CC-BY-4.0' in result.output

    def test_list_fails_on_network_error(self) -> None:
        with patch(
            'respro.cli.maintained_db.list_maintained_databases',
            side_effect=RuntimeError('Network error while fetching database listing: no network'),
        ):
            result = runner.invoke(app, ['databases', '--list'])
        assert result.exit_code != 0
        assert 'Network error' in result.output

    def test_list_prints_no_databases_message_when_empty(self) -> None:
        with patch('respro.cli.maintained_db.list_maintained_databases', return_value=[]):
            result = runner.invoke(app, ['databases', '--list'])
        assert result.exit_code == 0
        assert 'No databases found' in result.output

    def test_list_and_download_are_mutually_exclusive(self) -> None:
        result = runner.invoke(
            app,
            ['databases', '--list', '--download', 'hsv_daehne_jaki'],
        )
        assert result.exit_code != 0
        assert 'Use either --list or --download' in result.output

    def test_requires_list_or_download(self) -> None:
        result = runner.invoke(app, ['databases'])
        assert result.exit_code != 0
        assert 'Provide either --list or --download NAME' in result.output


# ── CLI: databases --download ────────────────────────────────────────────

class TestMaintainedDbDownloadCommand:
    def test_download_calls_init_project(self, tmp_path: Path) -> None:
        fake_gb = tmp_path / 'X04770.gb'
        fake_gb.write_bytes(_GENBANK_CONTENT)

        fake_files = {
            'rules': tmp_path / 'rules.tsv',
            'metadata': tmp_path / 'metadata.json',
            'formula_rules': None,
            'genbank': [fake_gb],
        }

        output_path = tmp_path / 'custom.db'
        with (
            patch('respro.cli.maintained_db.download_database_files', return_value=fake_files),
            patch('respro.cli.maintained_db.init_project') as mock_init,
        ):
            result = runner.invoke(app, [
                'databases',
                '--download', 'hsv_daehne_jaki',
                '--output', str(output_path),
            ])

        assert result.exit_code == 0
        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs['name'] == 'hsv_daehne_jaki'
        assert call_kwargs['overwrite'] is True
        assert call_kwargs['additional_info'] is True
        assert call_kwargs['db_path'] == output_path

    def test_download_fails_when_no_genbank(self, tmp_path: Path) -> None:
        fake_files = {
            'rules': tmp_path / 'rules.tsv',
            'metadata': tmp_path / 'metadata.json',
            'formula_rules': None,
            'genbank': [],
        }
        with patch('respro.cli.maintained_db.download_database_files', return_value=fake_files):
            result = runner.invoke(app, [
                'databases',
                '--download', 'hsv_daehne_jaki',
                '--output', str(tmp_path / 'custom.db'),
            ])
        assert result.exit_code != 0
        assert 'No GenBank records' in result.output

    def test_download_fails_on_network_error(self, tmp_path: Path) -> None:
        with patch(
            'respro.cli.maintained_db.download_database_files',
            side_effect=RuntimeError('HTTP 404'),
        ):
            result = runner.invoke(app, [
                'databases',
                '--download', 'no_such_db',
                '--output', str(tmp_path / 'custom.db'),
            ])
        assert result.exit_code != 0
        assert 'HTTP 404' in result.output

    def test_download_outputs_success_path(self, tmp_path: Path) -> None:
        fake_gb = tmp_path / 'X04770.gb'
        fake_gb.write_bytes(_GENBANK_CONTENT)
        fake_files = {
            'rules': tmp_path / 'rules.tsv',
            'metadata': tmp_path / 'metadata.json',
            'formula_rules': None,
            'genbank': [fake_gb],
        }
        with (
            patch('respro.cli.maintained_db.download_database_files', return_value=fake_files),
            patch('respro.cli.maintained_db.init_project'),
        ):
            result = runner.invoke(app, [
                'databases',
                '--download', 'hsv_daehne_jaki',
                '--output', str(tmp_path / 'custom.db'),
            ])
        assert result.exit_code == 0
        assert 'custom.db' in result.output

    def test_download_uses_name_db_default_when_output_omitted(self, tmp_path: Path) -> None:
        fake_gb = tmp_path / 'X04770.gb'
        fake_gb.write_bytes(_GENBANK_CONTENT)
        fake_files = {
            'rules': tmp_path / 'rules.tsv',
            'metadata': tmp_path / 'metadata.json',
            'formula_rules': None,
            'genbank': [fake_gb],
        }

        with (
            patch('respro.cli.maintained_db.download_database_files', return_value=fake_files),
            patch('respro.cli.maintained_db.init_project') as mock_init,
        ):
            result = runner.invoke(app, [
                'databases',
                '--download', 'hsv_daehne_jaki',
            ])

        assert result.exit_code == 0
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs['db_path'] == Path('hsv_daehne_jaki.db')

    def test_download_treats_output_without_suffix_as_directory(self, tmp_path: Path) -> None:
        fake_gb = tmp_path / 'X04770.gb'
        fake_gb.write_bytes(_GENBANK_CONTENT)
        fake_files = {
            'rules': tmp_path / 'rules.tsv',
            'metadata': tmp_path / 'metadata.json',
            'formula_rules': None,
            'genbank': [fake_gb],
        }

        with (
            patch('respro.cli.maintained_db.download_database_files', return_value=fake_files),
            patch('respro.cli.maintained_db.init_project') as mock_init,
        ):
            result = runner.invoke(app, [
                'databases',
                '--download', 'hsv_daehne_jaki',
                '--output', 'example',
            ])

        assert result.exit_code == 0
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs['db_path'] == Path('example') / 'hsv_daehne_jaki.db'
