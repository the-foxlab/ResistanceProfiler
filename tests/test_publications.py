"""
Tests for the NCBI E-utilities and CrossRef publication metadata clients.
"""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

from respro.io.publications import (
    fetch_publication_metadata,
    fetch_pubmed_id_for_doi,
    fetch_pubmed_metadata,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_response(payload: dict) -> MagicMock:
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_ESUMMARY_WITH_DOI = {
    'result': {
        '12345678': {
            'title': 'Some antiviral resistance study.',
            'articleids': [
                {'idtype': 'pubmed', 'value': '12345678'},
                {'idtype': 'doi',    'value': '10.1234/xyz'},
            ],
        }
    }
}

_ESUMMARY_NO_DOI = {
    'result': {
        '99999999': {
            'title': 'Old paper without a DOI.',
            'articleids': [
                {'idtype': 'pubmed', 'value': '99999999'},
            ],
        }
    }
}


# ── fetch_pubmed_metadata ──────────────────────────────────────────────────────

class TestFetchPubmedMetadata:
    def test_returns_title_and_doi_when_both_present(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response(_ESUMMARY_WITH_DOI)):
            result = fetch_pubmed_metadata('12345678')
        assert result is not None
        assert result['title'] == 'Some antiviral resistance study.'
        assert result['doi'] == '10.1234/xyz'

    def test_returns_title_with_empty_doi_when_no_doi_in_response(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response(_ESUMMARY_NO_DOI)):
            result = fetch_pubmed_metadata('99999999')
        assert result is not None
        assert result['title'] == 'Old paper without a DOI.'
        assert result['doi'] == ''

    def test_returns_empty_title_when_field_absent(self) -> None:
        payload = {'result': {'1': {'articleids': []}}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_pubmed_metadata('1')
        assert result is not None
        assert result['title'] == ''
        assert result['doi'] == ''

    def test_returns_none_on_http_error(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=500, msg='Server Error', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            assert fetch_pubmed_metadata('12345678') is None

    def test_retries_on_429_and_eventually_succeeds(self) -> None:
        headers = Message()
        headers['Retry-After'] = '0'
        rate_limited = urllib.error.HTTPError(
            url='', code=429, msg='Too Many Requests', hdrs=headers, fp=None,  # type: ignore[arg-type]
        )
        with (
            patch('respro.io.publications.urlopen', side_effect=[rate_limited, _mock_response(_ESUMMARY_WITH_DOI)]),
            patch('respro.io.publications.sleep') as mock_sleep,
        ):
            result = fetch_pubmed_metadata('12345678')
        assert result is not None
        assert result['title'] == 'Some antiviral resistance study.'
        assert result['doi'] == '10.1234/xyz'
        mock_sleep.assert_called_once_with(0.0)

    def test_returns_none_after_exhausting_429_retries(self) -> None:
        headers = Message()
        headers['Retry-After'] = '0'
        rate_limited = urllib.error.HTTPError(
            url='', code=429, msg='Too Many Requests', hdrs=headers, fp=None,  # type: ignore[arg-type]
        )
        with (
            patch('respro.io.publications.urlopen', side_effect=[rate_limited] * 4),
            patch('respro.io.publications.sleep') as mock_sleep,
        ):
            result = fetch_pubmed_metadata('12345678')
        assert result is None
        assert mock_sleep.call_count == 3

    def test_returns_none_on_network_error(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=OSError('no network')):
            assert fetch_pubmed_metadata('12345678') is None

    def test_returns_none_on_unexpected_json(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response({'foo': 'bar'})):
            result = fetch_pubmed_metadata('12345678')
        assert result is None

    def test_returns_none_when_articleids_is_not_list(self) -> None:
        payload = {'result': {'12345678': {'title': 'x', 'articleids': {}}}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_pubmed_metadata('12345678') is None


# ── fetch_publication_metadata ─────────────────────────────────────────────────

class TestFetchPublicationMetadata:
    def test_returns_title_on_success(self) -> None:
        payload = {'message': {'title': ['A CrossRef Title']}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result == {'title': 'A CrossRef Title'}

    def test_strips_whitespace_from_title(self) -> None:
        payload = {'message': {'title': ['  Trimmed Title  ']}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result == {'title': 'Trimmed Title'}

    def test_returns_none_on_404(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=404, msg='Not Found', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            assert fetch_publication_metadata('10.1234/bad') is None

    def test_returns_none_on_network_error(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=OSError('no network')):
            assert fetch_publication_metadata('10.1234/xyz') is None

    def test_returns_none_when_title_list_empty(self) -> None:
        payload = {'message': {'title': []}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_publication_metadata('10.1234/xyz') is None

    def test_retries_on_429_and_eventually_succeeds(self) -> None:
        headers = Message()
        headers['Retry-After'] = '0'
        rate_limited = urllib.error.HTTPError(
            url='', code=429, msg='Too Many Requests', hdrs=headers, fp=None,  # type: ignore[arg-type]
        )
        payload = {'message': {'title': ['A CrossRef Title']}}
        with (
            patch('respro.io.publications.urlopen', side_effect=[rate_limited, _mock_response(payload)]),
            patch('respro.io.publications.sleep') as mock_sleep,
        ):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result == {'title': 'A CrossRef Title'}
        mock_sleep.assert_called_once_with(0.0)

    def test_returns_none_when_title_field_is_not_list(self) -> None:
        payload = {'message': {'title': 'A CrossRef Title'}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_publication_metadata('10.1234/xyz') is None


# ── fetch_pubmed_id_for_doi ───────────────────────────────────────────────────

class TestFetchPubmedIdForDoi:
    def test_returns_pmid_on_success(self) -> None:
        payload = {'records': [{'doi': '10.1234/xyz', 'pmid': '12345678'}]}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_pubmed_id_for_doi('10.1234/xyz')
        assert result == '12345678'

    def test_returns_none_when_record_has_no_pmid(self) -> None:
        payload = {'records': [{'doi': '10.1234/xyz'}]}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

    def test_returns_none_when_records_empty(self) -> None:
        payload = {'records': []}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

    def test_returns_none_on_http_error(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=500, msg='Server Error', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

    def test_returns_none_on_network_error(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=OSError('no network')):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

    def test_skips_non_doi_tokens_without_http_call(self) -> None:
        with patch('respro.io.publications.urlopen') as mock_urlopen:
            assert fetch_pubmed_id_for_doi('PMID:12345678') is None
        mock_urlopen.assert_not_called()

    def test_returns_none_on_http_400_without_raising(self) -> None:
        with patch('respro.io.publications.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=400, msg='Bad Request', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

    def test_accepts_doi_org_prefixed_input(self) -> None:
        payload = {'records': [{'doi': '10.1234/xyz', 'pmid': '12345678'}]}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_pubmed_id_for_doi('doi.org/10.1234/xyz')
        assert result == '12345678'

    def test_retries_on_429_and_eventually_succeeds(self) -> None:
        headers = Message()
        headers['Retry-After'] = '0'
        rate_limited = urllib.error.HTTPError(
            url='', code=429, msg='Too Many Requests', hdrs=headers, fp=None,  # type: ignore[arg-type]
        )
        payload = {'records': [{'doi': '10.1234/xyz', 'pmid': '12345678'}]}
        with (
            patch('respro.io.publications.urlopen', side_effect=[rate_limited, _mock_response(payload)]),
            patch('respro.io.publications.sleep') as mock_sleep,
        ):
            result = fetch_pubmed_id_for_doi('10.1234/xyz')
        assert result == '12345678'
        mock_sleep.assert_called_once_with(0.0)

    def test_returns_none_when_records_field_is_not_list(self) -> None:
        payload = {'records': {'pmid': '12345678'}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            assert fetch_pubmed_id_for_doi('10.1234/xyz') is None

