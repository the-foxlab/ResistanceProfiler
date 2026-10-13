"""
Tests for the Wikipedia REST summary client (respro.io.wikipedia).
"""

from __future__ import annotations

import json
import logging
import urllib.error
from unittest.mock import MagicMock, patch

from respro.io.lookup import LookupStatus
from respro.io.wikipedia import lookup_summary


def _mock_response(payload: object) -> MagicMock:
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestLookupSummary:
    def test_returns_extract_when_found(self) -> None:
        payload = {
            'title': 'Nirsevimab',
            'extract': 'Nirsevimab is a monoclonal antibody for RSV prophylaxis.',
        }
        with patch('urllib.request.urlopen', return_value=_mock_response(payload)):
            result = lookup_summary('Nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value == 'Nirsevimab is a monoclonal antibody for RSV prophylaxis.'

    def test_returns_not_found_on_404(self, caplog) -> None:
        error = urllib.error.HTTPError('https://en.wikipedia.org/api/rest_v1/page/summary/UnknownDrug', 404, 'Not Found', None, None)
        with caplog.at_level(logging.DEBUG, logger='respro.io.wikipedia'):
            with patch('urllib.request.urlopen', side_effect=error):
                result = lookup_summary('UnknownDrug')

        assert result.status is LookupStatus.NOT_FOUND
        assert any('no page found' in entry.message for entry in caplog.records)

    def test_returns_error_on_network_error(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger='respro.io.wikipedia'):
            with patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
                result = lookup_summary('Nirsevimab')

        assert result.status is LookupStatus.ERROR
        assert any('network' in entry.message for entry in caplog.records)

    def test_returns_error_on_http_500(self, caplog) -> None:
        error = urllib.error.HTTPError('https://en.wikipedia.org/api/rest_v1/page/summary/X', 500, 'Server Error', None, None)
        with caplog.at_level(logging.WARNING, logger='respro.io.wikipedia'):
            with patch('urllib.request.urlopen', side_effect=error):
                result = lookup_summary('X')

        assert result.status is LookupStatus.ERROR
        assert any('HTTP 500' in entry.message for entry in caplog.records)

    def test_returns_error_when_payload_is_not_object(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response(['not', 'an', 'object'])):
            assert lookup_summary('Nirsevimab').status is LookupStatus.ERROR

    def test_returns_not_found_when_extract_is_missing(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'title': 'Nirsevimab'})):
            assert lookup_summary('Nirsevimab').status is LookupStatus.NOT_FOUND

    def test_returns_not_found_when_extract_is_empty(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'extract': '   '})):
            assert lookup_summary('Nirsevimab').status is LookupStatus.NOT_FOUND

    def test_returns_error_on_malformed_json(self) -> None:
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'not json')))
        mock.__exit__ = MagicMock(return_value=False)
        with patch('urllib.request.urlopen', return_value=mock):
            assert lookup_summary('Nirsevimab').status is LookupStatus.ERROR

    def test_never_raises_on_unexpected_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=RuntimeError('boom')):
            assert lookup_summary('Nirsevimab').status is LookupStatus.ERROR

    def test_timeout_defaults_to_pubchem_timeout(self) -> None:
        """The timeout kwarg defaults to the configured PubChem timeout."""
        from respro.config.cli_settings import CLI_CONFIG

        captured: dict = {}

        def _capture(url, timeout=None):
            captured['timeout'] = timeout
            return _mock_response({'extract': 'text'})

        with patch('urllib.request.urlopen', side_effect=_capture):
            lookup_summary('Nirsevimab')

        assert captured['timeout'] == CLI_CONFIG.timeouts.pubchem

    def test_explicit_timeout_is_forwarded(self) -> None:
        captured: dict = {}

        def _capture(url, timeout=None):
            captured['timeout'] = timeout
            return _mock_response({'extract': 'text'})

        with patch('urllib.request.urlopen', side_effect=_capture):
            lookup_summary('Nirsevimab', timeout=3)

        assert captured['timeout'] == 3

    def test_url_setting_is_used(self) -> None:
        """The configured wikipedia_summary URL template is used, with the name encoded."""
        captured: dict = {}

        def _capture(url, timeout=None):
            captured['url'] = url
            return _mock_response({'extract': 'text'})

        with patch('urllib.request.urlopen', side_effect=_capture):
            lookup_summary('Nirsevimab alfa')

        assert captured['url'] == 'https://en.wikipedia.org/api/rest_v1/page/summary/Nirsevimab%20alfa'
