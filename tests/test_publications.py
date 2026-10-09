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

    def test_strips_markup_tags_from_title(self) -> None:
        # Defensive: PubMed titles are usually clean, but strip JATS markup
        # (e.g. <i>) should it ever appear in the esummary payload.
        payload = {
            'result': {
                '33055248': {
                    'title': '<i>In Vitro</i> Profiling of Laninamivir-Resistant Substitutions',
                    'articleids': [],
                },
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_pubmed_metadata('33055248')
        assert result is not None
        assert result['title'] == 'In Vitro Profiling of Laninamivir-Resistant Substitutions'


# ── fetch_publication_metadata ─────────────────────────────────────────────────

class TestFetchPublicationMetadata:
    def test_returns_title_on_success(self) -> None:
        payload = {'message': {'title': ['A CrossRef Title']}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result == {
            'title': 'A CrossRef Title',
            'first_author': '',
            'year': '',
            'journal': '',
        }

    def test_strips_whitespace_from_title(self) -> None:
        payload = {'message': {'title': ['  Trimmed Title  ']}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result == {
            'title': 'Trimmed Title',
            'first_author': '',
            'year': '',
            'journal': '',
        }

    def test_strips_jats_markup_tags_from_title(self) -> None:
        # CrossRef returns publisher-deposit titles verbatim, including
        # JATS/NLM inline markup such as <scp> (small caps).
        payload = {
            'message': {
                'title': [
                    'Drug susceptibility surveillance in the <scp>U</scp>nited '
                    '<scp>S</scp>tates: application of the <scp>WHO</scp> criteria'
                ],
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1111/irv.12215')
        assert result is not None
        assert result['title'] == (
            'Drug susceptibility surveillance in the United States: '
            'application of the WHO criteria'
        )

    def test_collapses_newlines_and_indentation_in_title(self) -> None:
        # Real CrossRef payload shape: markup broken across lines with indentation.
        # The small-caps letter continues the word, so "S" + "outhern" rejoins.
        payload = {
            'message': {
                'title': [
                    '... during the 2011\n                    <scp>S</scp>\n'
                    '                    outhern\n                    <scp>H</scp>\n'
                    '                    emisphere season'
                ],
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1111/irv.12113')
        assert result is not None
        assert result['title'] == '... during the 2011 Southern Hemisphere season'

    def test_rejoins_tight_subtype_tokens(self) -> None:
        # "<scp>A</scp>(<scp>H</scp>3<scp>N</scp>2)" is the virus subtype
        # A(H3N2): fragments join tightly, no spaces inserted.
        payload = {
            'message': {
                'title': [
                    'Progressive emergence of an oseltamivir‐resistant '
                    '<scp>A</scp>(<scp>H</scp>3<scp>N</scp>2) virus'
                ],
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1111/irv.12108')
        assert result is not None
        assert result['title'] == (
            'Progressive emergence of an oseltamivir‐resistant A(H3N2) virus'
        )

    def test_italic_phrase_without_surrounding_spaces_gets_word_boundaries(self) -> None:
        # "Selected<i>In Vitro</i>with" must become "Selected In Vitro with".
        payload = {
            'message': {
                'title': [
                    'Variants Selected<i>In Vitro</i>with Laninamivir'
                ],
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1128/aac.03313-14')
        assert result is not None
        assert result['title'] == 'Variants Selected In Vitro with Laninamivir'

    def test_complete_acronym_keeps_word_boundary(self) -> None:
        # "<scp>WHO</scp> antiviral" is a complete token followed by a real
        # word boundary — the space must survive.
        payload = {
            'message': {
                'title': ['application of the <scp>WHO</scp> antiviral criteria'],
            },
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1111/irv.12215')
        assert result is not None
        assert result['title'] == 'application of the WHO antiviral criteria'

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
        assert result == {
            'title': 'A CrossRef Title',
            'first_author': '',
            'year': '',
            'journal': '',
        }
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


# ── author / year / journal parsing ───────────────────────────────────────────

_ESUMMARY_WITH_AUTHORS = {
    'result': {
        '12345678': {
            'title': 'Some antiviral resistance study.',
            'authors': [
                {'name': 'Smith J'},
                {'name': 'Doe A'},
                {'name': 'Roe B'},
            ],
            'fulljournalname': 'Journal of Virology',
            'pubdate': '2021 Jan 15',
            'articleids': [
                {'idtype': 'pubmed', 'value': '12345678'},
                {'idtype': 'doi', 'value': '10.1234/xyz'},
            ],
        }
    }
}

_CROSSREF_WITH_AUTHORS = {
    'message': {
        'title': ['A CrossRef Title'],
        'author': [
            {'family': 'Smith', 'given': 'J.'},
            {'family': 'Doe', 'given': 'A.'},
        ],
        'container-title': ['Antimicrobial Agents and Chemotherapy'],
        'published': {'date-parts': [[2022, 3, 1]]},
        'issued': {'date-parts': [[2022]]},
    }
}


class TestFetchPubmedMetadataRichFields:
    def test_returns_first_author_year_and_journal_when_present(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response(_ESUMMARY_WITH_AUTHORS)):
            result = fetch_pubmed_metadata('12345678')
        assert result is not None
        assert result['title'] == 'Some antiviral resistance study.'
        assert result['doi'] == '10.1234/xyz'
        assert result['first_author'] == 'Smith J'
        assert result['year'] == '2021'
        assert result['journal'] == 'Journal of Virology'

    def test_returns_empty_rich_fields_when_absent(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response(_ESUMMARY_WITH_DOI)):
            result = fetch_pubmed_metadata('12345678')
        assert result is not None
        assert result['first_author'] == ''
        assert result['year'] == ''
        assert result['journal'] == ''

    def test_extracts_year_from_pubdate_without_month(self) -> None:
        payload = {
            'result': {
                '1': {
                    'title': 'T',
                    'pubdate': '1998',
                    'authors': [{'name': 'Lee K'}],
                    'articleids': [],
                }
            }
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_pubmed_metadata('1')
        assert result is not None
        assert result['year'] == '1998'
        assert result['first_author'] == 'Lee K'


class TestFetchPublicationMetadataRichFields:
    def test_returns_first_author_year_and_journal_when_present(self) -> None:
        with patch('respro.io.publications.urlopen', return_value=_mock_response(_CROSSREF_WITH_AUTHORS)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result is not None
        assert result['title'] == 'A CrossRef Title'
        assert result['first_author'] == 'Smith'
        assert result['year'] == '2022'
        assert result['journal'] == 'Antimicrobial Agents and Chemotherapy'

    def test_returns_empty_rich_fields_when_absent(self) -> None:
        payload = {'message': {'title': ['A CrossRef Title']}}
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result is not None
        assert result['first_author'] == ''
        assert result['year'] == ''
        assert result['journal'] == ''

    def test_falls_back_to_issued_date_parts_when_published_absent(self) -> None:
        payload = {
            'message': {
                'title': ['T'],
                'author': [{'family': 'Ng', 'given': 'P.'}],
                'container-title': ['JCM'],
                'issued': {'date-parts': [[2019, 6]]},
            }
        }
        with patch('respro.io.publications.urlopen', return_value=_mock_response(payload)):
            result = fetch_publication_metadata('10.1234/xyz')
        assert result is not None
        assert result['year'] == '2019'
        assert result['first_author'] == 'Ng'

