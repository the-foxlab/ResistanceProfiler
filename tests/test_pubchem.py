"""
Tests for the PubChem REST client and PubChem data loading in init_project.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from respro.io.pubchem import PubChemRecord, _fetch_cid, _fetch_description, lookup_drug
from respro.db.project.drugs import _get_drugs_from_pubchem


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _mock_response(payload: dict) -> MagicMock:
    """Return a context-manager mock that yields a readable JSON response."""
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


_CID_RESPONSE = {'IdentifierList': {'CID': [135398513]}}
_DESC_RESPONSE = {
    'InformationList': {
        'Information': [
            {'CID': 135398513, 'Title': 'Aciclovir', 'Description': 'Antiviral drug.'},
        ]
    }
}


# ──────────────────────────────────────────────────────────────────────
# _fetch_cid
# ──────────────────────────────────────────────────────────────────────

class TestFetchCid:
    def test_returns_cid_on_success(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response(_CID_RESPONSE)):
            result = _fetch_cid('Aciclovir', timeout=5)
        assert result == 135398513

    def test_returns_none_on_404(self) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=404, msg='Not Found', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            result = _fetch_cid('NoSuchDrug', timeout=5)
        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError(
            url='', code=500, msg='Server Error', hdrs=None, fp=None,  # type: ignore[arg-type]
        )):
            result = _fetch_cid('AnyDrug', timeout=5)
        assert result is None

    def test_returns_none_on_network_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=OSError('no network')):
            result = _fetch_cid('AnyDrug', timeout=5)
        assert result is None

    def test_returns_none_on_empty_cid_list(self) -> None:
        with patch('urllib.request.urlopen',
                   return_value=_mock_response({'IdentifierList': {'CID': []}})):
            result = _fetch_cid('AnyDrug', timeout=5)
        assert result is None

    def test_returns_none_on_unexpected_json(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'foo': 'bar'})):
            result = _fetch_cid('AnyDrug', timeout=5)
        assert result is None


# ──────────────────────────────────────────────────────────────────────
# _fetch_description
# ──────────────────────────────────────────────────────────────────────

class TestFetchDescription:
    def test_returns_first_description(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response(_DESC_RESPONSE)):
            result = _fetch_description(135398513, timeout=5)
        assert result == 'Antiviral drug.'

    def test_skips_empty_descriptions(self) -> None:
        payload = {
            'InformationList': {
                'Information': [
                    {'CID': 1, 'Description': ''},
                    {'CID': 1, 'Description': '   '},
                    {'CID': 1, 'Description': 'Valid description.'},
                ]
            }
        }
        with patch('urllib.request.urlopen', return_value=_mock_response(payload)):
            result = _fetch_description(1, timeout=5)
        assert result == 'Valid description.'

    def test_returns_empty_string_when_no_descriptions(self) -> None:
        payload = {'InformationList': {'Information': [{'CID': 1}]}}
        with patch('urllib.request.urlopen', return_value=_mock_response(payload)):
            result = _fetch_description(1, timeout=5)
        assert result == ''

    def test_returns_empty_string_on_network_error(self) -> None:
        with patch('urllib.request.urlopen', side_effect=OSError('no network')):
            result = _fetch_description(1, timeout=5)
        assert result == ''


# ──────────────────────────────────────────────────────────────────────
# lookup_drug
# ──────────────────────────────────────────────────────────────────────

class TestLookupDrug:
    def test_returns_record_when_found(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=135398513), \
             patch('respro.io.pubchem._fetch_description', return_value='Antiviral drug.'):
            result = lookup_drug('Aciclovir')

        assert isinstance(result, PubChemRecord)
        assert result.cid == 135398513
        assert result.url == 'https://pubchem.ncbi.nlm.nih.gov/compound/135398513'
        assert result.description == 'Antiviral drug.'

    def test_returns_none_when_cid_not_found(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=None):
            result = lookup_drug('UnknownDrug')
        assert result is None

    def test_returns_record_with_empty_description_when_unavailable(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=99), \
             patch('respro.io.pubchem._fetch_description', return_value=''):
            result = lookup_drug('SomeDrug')
        assert result is not None
        assert result.description == ''

    def test_returns_none_on_network_error(self) -> None:
        # _fetch_cid catches OSError internally and returns None;
        # lookup_drug therefore receives None and returns None itself.
        with patch('respro.io.pubchem._fetch_cid', return_value=None):
            result = lookup_drug('AnyDrug')
        assert result is None


# ──────────────────────────────────────────────────────────────────────
# _get_drugs_from_pubchem — integration with init_project internals
# ──────────────────────────────────────────────────────────────────────

class TestAddPubchemData:
    """Test _get_drugs_from_pubchem with a real in-memory SQLite DB."""

    def _make_db(self) -> sqlite3.Connection:
        from respro.db.schema import create_schema
        import tempfile
        conn = create_schema(Path(tempfile.mktemp(suffix='.db')))
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO project (name, schema_version) VALUES ('Test', 7)")
        conn.execute(
            "INSERT INTO drug (project_id, name) VALUES (1, 'Aciclovir'), (1, 'Foscarnet')"
        )
        conn.commit()
        return conn

    def test_adds_data_for_known_drug(self) -> None:
        conn = self._make_db()
        record = PubChemRecord(cid=135398513, url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
                               description='Antiviral drug.')
        with patch('respro.db.project.drugs.lookup_drug', return_value=record):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute("SELECT pubchem_cid, pubchem_url, description FROM drug WHERE name='Aciclovir'").fetchone()
        assert row['pubchem_cid'] == '135398513'
        assert 'pubchem.ncbi.nlm.nih.gov' in row['pubchem_url']
        assert row['description'] != ''
        conn.close()

    def test_skips_drugs_with_existing_pubchem_data(self) -> None:

        conn = self._make_db()
        conn.execute(
            "UPDATE drug SET pubchem_cid = '135398513', pubchem_url = 'https://pubchem.ncbi.nlm.nih.gov/compound/135398513', "
            "description = 'Antiviral drug.', structure_url = 'https://pubchem.ncbi.nlm.nih.gov/image/imgsrv.php?cid=135398513&t=l' "
            "WHERE name = 'Aciclovir'"
        )
        conn.commit()

        with patch('respro.db.project.drugs.lookup_drug', return_value=None) as mocked_lookup:
            _get_drugs_from_pubchem(conn, project_id=1)

        # Only the still-missing drug should be queried.
        mocked_lookup.assert_called_once_with('Foscarnet')
        conn.close()

    def test_logs_added_data_wording(self, caplog) -> None:
        conn = self._make_db()
        record = PubChemRecord(
            cid=135398513,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
            description='Antiviral drug.',
        )
        with caplog.at_level(logging.INFO, logger='respro.db.project.drugs'):
            with patch('respro.db.project.drugs.lookup_drug', return_value=record):
                _get_drugs_from_pubchem(conn, project_id=1)

        assert any('PubChem: added data for' in entry.message for entry in caplog.records)
        conn.close()

    def test_skips_unrecognised_drug_without_failing(self) -> None:

        conn = self._make_db()
        with patch('respro.db.project.drugs.lookup_drug', return_value=None):
            # Must not raise
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Aciclovir'").fetchone()
        assert row['pubchem_cid'] == ''
        conn.close()

    def test_skips_network_error_without_failing(self) -> None:
        conn = self._make_db()
        # Re-test with lookup_drug returning None (as it would after catching OSError)
        with patch('respro.db.project.drugs.lookup_drug', return_value=None):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Aciclovir'").fetchone()
        assert row['pubchem_cid'] == ''
        conn.close()

    def test_partial_pubchem_lookup_continues_after_failure(self) -> None:
        conn = self._make_db()
        aciclovir_record = PubChemRecord(
            cid=135398513,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
            description='Antiviral drug.',
        )

        def _side_effect(name: str) -> PubChemRecord | None:
            return aciclovir_record if name == 'Aciclovir' else None

        with patch('respro.db.project.drugs.lookup_drug', side_effect=_side_effect):
            _get_drugs_from_pubchem(conn, project_id=1)

        aciclovir = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Aciclovir'").fetchone()
        foscarnet = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Foscarnet'").fetchone()
        assert aciclovir['pubchem_cid'] == '135398513'
        assert foscarnet['pubchem_cid'] == ''
        conn.close()


