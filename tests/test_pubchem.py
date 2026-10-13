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

from conftest import make_profiling_result

from respro.db.drugs import _get_drugs_from_pubchem
from respro.db.models import (
    AnnotatedVariant,
    FeatureMatch,
    FeatureRecord,
    ResistanceRule,
    VariantCall,
)
from respro.io.lookup import LookupResult, LookupStatus
from respro.io.pubchem import (
    PubChemRecord,
    _fetch_cid,
    _fetch_description,
    _fetch_substance_sids,
    _fetch_substance_synonyms,
    lookup_drug,
)
from respro.report.html import render_html


def _mock_response(payload: object) -> MagicMock:
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ──────────────────────────────────────────────────────────────────────
# lookup_drug
# ──────────────────────────────────────────────────────────────────────

class TestLookupDrug:
    def test_returns_record_when_found(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.found(135398513)), \
             patch('respro.io.pubchem._fetch_description', return_value='Antiviral drug.'):
            result = lookup_drug('Aciclovir')

        assert result.status is LookupStatus.FOUND
        assert isinstance(result.value, PubChemRecord)
        assert result.value.cid == 135398513
        assert result.value.url == 'https://pubchem.ncbi.nlm.nih.gov/compound/135398513'
        assert result.value.description == 'Antiviral drug.'

    def test_returns_not_found_when_cid_and_substance_not_found(self) -> None:
        # No compound AND no substance record → definitive NOT_FOUND.
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch('respro.io.pubchem._fetch_substance_sids', return_value=LookupResult.not_found()):
            result = lookup_drug('UnknownDrug')
        assert result.status is LookupStatus.NOT_FOUND

    def test_returns_record_with_empty_description_when_unavailable(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.found(99)), \
             patch('respro.io.pubchem._fetch_description', return_value=''):
            result = lookup_drug('SomeDrug')
        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.description == ''

    def test_returns_error_on_cid_network_error(self) -> None:
        # A transient CID failure is an ERROR, not a definitive negative: the
        # substance fallback is not consulted and the caller should retry.
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.error()), \
             patch('respro.io.pubchem._fetch_substance_sids') as mocked_sids:
            result = lookup_drug('AnyDrug')
        assert result.status is LookupStatus.ERROR
        mocked_sids.assert_not_called()


class TestPubchemJsonShapeValidation:
    def test_fetch_cid_returns_error_when_identifier_list_is_not_object(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'IdentifierList': []})):
            assert _fetch_cid('Aciclovir', timeout=1).status is LookupStatus.ERROR

    def test_fetch_cid_returns_error_when_cid_field_is_not_list(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'IdentifierList': {'CID': 'x'}})):
            assert _fetch_cid('Aciclovir', timeout=1).status is LookupStatus.ERROR

    def test_fetch_description_returns_empty_when_information_list_is_not_object(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'InformationList': []})):
            assert _fetch_description(123, timeout=1) == ''

    def test_fetch_description_returns_empty_when_property_table_is_not_object(self) -> None:
        description_payload = {'InformationList': {'Information': []}}
        title_payload = {'PropertyTable': []}
        with patch(
            'urllib.request.urlopen',
            side_effect=[_mock_response(description_payload), _mock_response(title_payload)],
        ):
            assert _fetch_description(123, timeout=1) == ''


# ──────────────────────────────────────────────────────────────────────
# _get_drugs_from_pubchem — integration with init_project internals
# ──────────────────────────────────────────────────────────────────────

class TestAddPubchemData:
    """Test _get_drugs_from_pubchem with a real in-memory SQLite DB."""

    def _make_db(self) -> sqlite3.Connection:
        import tempfile

        from respro.db.schema import create_schema
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
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.found(record)):
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

        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()) as mocked_lookup, \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            _get_drugs_from_pubchem(conn, project_id=1)

        # Only the still-missing drug should be queried.
        mocked_lookup.assert_called_once_with('Foscarnet')
        conn.close()

    def test_queries_existing_cid_when_description_missing(self) -> None:
        conn = self._make_db()
        conn.execute(
            "UPDATE drug SET pubchem_cid = '123', pubchem_url = 'https://pubchem.ncbi.nlm.nih.gov/compound/123', "
            "description = '' WHERE name = 'Aciclovir'"
        )
        conn.commit()

        record = PubChemRecord(
            cid=123,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/123',
            description='Nucleoside analog antiviral',
        )

        def _side_effect(name: str) -> LookupResult[PubChemRecord]:
            return LookupResult.found(record) if name == 'Aciclovir' else LookupResult.not_found()

        with patch('respro.db.drugs.lookup_drug', side_effect=_side_effect), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute("SELECT description FROM drug WHERE name='Aciclovir'").fetchone()
        assert row['description'] == 'Nucleoside analog antiviral'
        conn.close()

    def test_logs_added_data_wording(self, caplog) -> None:
        conn = self._make_db()
        record = PubChemRecord(
            cid=135398513,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
            description='Antiviral drug.',
        )
        with caplog.at_level(logging.INFO, logger='respro.db.drugs'):
            with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.found(record)):
                _get_drugs_from_pubchem(conn, project_id=1)

        assert any('PubChem: added data for' in entry.message for entry in caplog.records)
        conn.close()

    def test_skips_unrecognised_drug_without_failing(self) -> None:

        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            # Must not raise
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            "SELECT pubchem_cid, pubchem_lookup_failed FROM drug WHERE name='Aciclovir'"
        ).fetchone()
        assert row['pubchem_cid'] == ''
        # A definitive negative is cached via the flag.
        assert row['pubchem_lookup_failed'] == 1
        conn.close()

    def test_skips_network_error_without_failing(self) -> None:
        conn = self._make_db()
        # A transient failure must not raise and must NOT set the flag.
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.error()):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            "SELECT pubchem_cid, pubchem_lookup_failed FROM drug WHERE name='Aciclovir'"
        ).fetchone()
        assert row['pubchem_cid'] == ''
        assert row['pubchem_lookup_failed'] == 0
        conn.close()

    def test_partial_pubchem_lookup_continues_after_failure(self) -> None:
        conn = self._make_db()
        aciclovir_record = PubChemRecord(
            cid=135398513,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
            description='Antiviral drug.',
        )

        def _side_effect(name: str) -> LookupResult[PubChemRecord]:
            return LookupResult.found(aciclovir_record) if name == 'Aciclovir' else LookupResult.not_found()

        with patch('respro.db.drugs.lookup_drug', side_effect=_side_effect), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            _get_drugs_from_pubchem(conn, project_id=1)

        aciclovir = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Aciclovir'").fetchone()
        foscarnet = conn.execute("SELECT pubchem_cid FROM drug WHERE name='Foscarnet'").fetchone()
        assert aciclovir['pubchem_cid'] == '135398513'
        assert foscarnet['pubchem_cid'] == ''
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Substance (SID) fallback for CID-less drugs (antibodies)
# ──────────────────────────────────────────────────────────────────────

# Recorded 2026-10-13 from the live PubChem API (verified payloads).
NIRSEVIMAB_SIDS = [
    381128205, 404720251, 472418861, 480403174, 481988037,
    483928786, 508534155, 513905715, 523051129,
]
NIRSEVIMAB_SYNONYMS = [
    'Nirsevimab [USAN]', 'VRN8S9CW5V', '1989556-22-0', 'UNII-VRN8S9CW5V',
    'MEDI8897', 'Nirsevimab',
]


def _sids_payload(sids: list[int]) -> dict:
    return {'IdentifierList': {'SID': sids}}


def _substance_record_payload(synonyms: list[str], sid: int = 381128205) -> dict:
    return {'PC_Substances': [{'sid': {'id': sid}, 'synonyms': synonyms, 'sourceName': 'ChemIDplus'}]}


class TestSubstanceFallback:
    """lookup_drug falls back to PubChem substance (SID) records on compound 404."""

    def test_substance_fallback_returns_record_for_antibody(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch(
                 'urllib.request.urlopen',
                 side_effect=[
                     _mock_response(_sids_payload(NIRSEVIMAB_SIDS)),
                     _mock_response(_substance_record_payload(NIRSEVIMAB_SYNONYMS)),
                 ],
             ):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.cid is None
        assert result.value.url == 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205'
        # First synonym matching the query name case-insensitively.
        assert result.value.description == 'Nirsevimab'
        assert result.value.structure_url == ''

    def test_compound_path_preferred_when_cid_exists(self) -> None:
        urlopen_mock = MagicMock()
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.found(135398513)), \
             patch('respro.io.pubchem._fetch_description', return_value='Antiviral drug.'), \
             patch('urllib.request.urlopen', urlopen_mock):
            result = lookup_drug('Aciclovir')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.cid == 135398513
        assert result.value.url == 'https://pubchem.ncbi.nlm.nih.gov/compound/135398513'
        assert result.value.structure_url != ''
        # The substance endpoints must not be touched on the compound path.
        urlopen_mock.assert_not_called()

    def test_uses_first_synonym_when_no_exact_match(self) -> None:
        synonyms = ['Nirsevimab [USAN]', 'MEDI8897']
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch(
                 'urllib.request.urlopen',
                 side_effect=[
                     _mock_response(_sids_payload(NIRSEVIMAB_SIDS)),
                     _mock_response(_substance_record_payload(synonyms)),
                 ],
             ):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.description == 'Nirsevimab [USAN]'

    def test_matches_query_name_case_insensitively(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch(
                 'urllib.request.urlopen',
                 side_effect=[
                     _mock_response(_sids_payload(NIRSEVIMAB_SIDS)),
                     _mock_response(_substance_record_payload(NIRSEVIMAB_SYNONYMS)),
                 ],
             ):
            result = lookup_drug('nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.description == 'Nirsevimab'

    def test_empty_description_when_no_synonyms(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch(
                 'urllib.request.urlopen',
                 side_effect=[
                     _mock_response(_sids_payload([381128205])),
                     _mock_response(_substance_record_payload([])),
                 ],
             ):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.cid is None
        assert result.value.description == ''

    def test_returns_not_found_when_no_substance_found(self, caplog) -> None:
        error = urllib.error.HTTPError(
            'https://pubchem.ncbi.nlm.nih.gov/rest/pug/substance/name/X/sids/JSON',
            404, 'Not Found', None, None,
        )
        with caplog.at_level(logging.DEBUG, logger='respro.io.pubchem'):
            with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
                 patch('urllib.request.urlopen', side_effect=error):
                result = lookup_drug('UnknownDrug')

        assert result.status is LookupStatus.NOT_FOUND
        assert any('no substance found' in entry.message for entry in caplog.records)

    def test_returns_error_on_substance_network_error(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.ERROR

    def test_returns_error_when_sids_payload_malformed(self) -> None:
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch('urllib.request.urlopen', return_value=_mock_response({'IdentifierList': []})):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.ERROR

    def test_picks_lowest_sid_as_canonical(self) -> None:
        """The lowest SID is used so the canonical record is deterministic."""
        with patch('respro.io.pubchem._fetch_cid', return_value=LookupResult.not_found()), \
             patch(
                 'urllib.request.urlopen',
                 side_effect=[
                     _mock_response(_sids_payload([523051129, 381128205, 472418861])),
                     _mock_response(_substance_record_payload(NIRSEVIMAB_SYNONYMS)),
                 ],
             ):
            result = lookup_drug('Nirsevimab')

        assert result.status is LookupStatus.FOUND
        assert result.value is not None
        assert result.value.url == 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205'


class TestSubstanceHelperPayloadValidation:
    """Payload-shape validation for the substance helpers."""

    def test_fetch_substance_sids_returns_error_when_sid_field_is_not_list(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({'IdentifierList': {'SID': 'x'}})):
            assert _fetch_substance_sids('Nirsevimab', timeout=1).status is LookupStatus.ERROR

    def test_fetch_substance_sids_skips_non_numeric_entries(self) -> None:
        with patch(
            'urllib.request.urlopen',
            return_value=_mock_response({'IdentifierList': {'SID': [381128205, 'x', None]}}),
        ):
            result = _fetch_substance_sids('Nirsevimab', timeout=1)
        assert result.status is LookupStatus.FOUND
        assert result.value == [381128205]

    def test_fetch_substance_synonyms_returns_error_when_pc_substances_missing(self) -> None:
        with patch('urllib.request.urlopen', return_value=_mock_response({})):
            assert _fetch_substance_synonyms(381128205, timeout=1).status is LookupStatus.ERROR

    def test_fetch_substance_synonyms_returns_error_when_synonyms_not_list(self) -> None:
        with patch(
            'urllib.request.urlopen',
            return_value=_mock_response({'PC_Substances': [{'synonyms': 'Nirsevimab'}]}),
        ):
            assert _fetch_substance_synonyms(381128205, timeout=1).status is LookupStatus.ERROR

    def test_fetch_substance_synonyms_strips_and_drops_blank_entries(self) -> None:
        with patch(
            'urllib.request.urlopen',
            return_value=_mock_response({'PC_Substances': [{'synonyms': ['  Nirsevimab ', '', None]}]}),
        ):
            result = _fetch_substance_synonyms(381128205, timeout=1)
        assert result.status is LookupStatus.FOUND
        assert result.value == ['Nirsevimab']


# ──────────────────────────────────────────────────────────────────────
# _get_drugs_from_pubchem — fallback chain + negative-lookup cache
# ──────────────────────────────────────────────────────────────────────

class TestDrugEnrichmentFallbackChain:
    """Compound → substance → Wikipedia chain and the pubchem_lookup_failed flag."""

    def _make_db(self) -> sqlite3.Connection:
        import tempfile

        from respro.db.schema import create_schema
        conn = create_schema(Path(tempfile.mktemp(suffix='.db')))
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO project (name, schema_version) VALUES ('Test', 7)")
        conn.execute("INSERT INTO drug (project_id, name) VALUES (1, 'nirsevimab')")
        conn.commit()
        return conn

    def _substance_record(self) -> PubChemRecord:
        return PubChemRecord(
            cid=None,
            url='https://pubchem.ncbi.nlm.nih.gov/substance/381128205',
            description='Nirsevimab',
        )

    def test_substance_hit_stores_substance_url_without_cid_or_structure(self) -> None:
        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.found(self._substance_record())):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT pubchem_cid, pubchem_url, description, structure_url, pubchem_lookup_failed '
            "FROM drug WHERE name = 'nirsevimab'"
        ).fetchone()
        assert row['pubchem_cid'] == ''
        assert row['pubchem_url'] == 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205'
        assert row['description'] == 'Nirsevimab'
        assert row['structure_url'] == ''
        assert row['pubchem_lookup_failed'] == 0
        conn.close()

    def test_wikipedia_fallback_when_no_pubchem_record(self) -> None:
        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch(
                 'respro.db.drugs.lookup_summary',
                 return_value=LookupResult.found('Nirsevimab is a monoclonal antibody for RSV prophylaxis.'),
             ):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT pubchem_cid, pubchem_url, description, structure_url, pubchem_lookup_failed '
            "FROM drug WHERE name = 'nirsevimab'"
        ).fetchone()
        assert row['pubchem_cid'] == ''
        assert row['pubchem_url'] == ''
        assert row['description'] == 'Nirsevimab is a monoclonal antibody for RSV prophylaxis.'
        assert row['structure_url'] == ''
        assert row['pubchem_lookup_failed'] == 0
        conn.close()

    def test_full_chain_failure_sets_pubchem_lookup_failed(self) -> None:
        """A definitive 404 at every chain stage sets the flag."""
        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT description, pubchem_lookup_failed FROM drug WHERE name = \'nirsevimab\''
        ).fetchone()
        assert row['description'] == ''
        assert row['pubchem_lookup_failed'] == 1
        conn.close()

    def test_flagged_drug_skipped_on_rerun(self) -> None:
        """A drug whose full chain failed must not be re-queried on the next run."""
        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.not_found()):
            _get_drugs_from_pubchem(conn, project_id=1)

        mocked_lookup = MagicMock(return_value=LookupResult.not_found())
        mocked_summary = MagicMock(return_value=LookupResult.not_found())
        with patch('respro.db.drugs.lookup_drug', mocked_lookup), \
             patch('respro.db.drugs.lookup_summary', mocked_summary):
            _get_drugs_from_pubchem(conn, project_id=1)

        mocked_lookup.assert_not_called()
        mocked_summary.assert_not_called()
        conn.close()

    def test_substance_url_only_drug_treated_as_complete(self) -> None:
        """An SID-only drug (substance URL + synonym description) is not re-queried."""
        conn = self._make_db()
        conn.execute(
            "UPDATE drug SET pubchem_url = 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205', "
            "description = 'Nirsevimab' WHERE name = 'nirsevimab'"
        )
        conn.commit()

        mocked_lookup = MagicMock()
        mocked_summary = MagicMock()
        with patch('respro.db.drugs.lookup_drug', mocked_lookup), \
             patch('respro.db.drugs.lookup_summary', mocked_summary):
            _get_drugs_from_pubchem(conn, project_id=1)

        mocked_lookup.assert_not_called()
        mocked_summary.assert_not_called()
        conn.close()

    def test_wikipedia_only_drug_skipped_on_rerun(self) -> None:
        """A drug enriched only via Wikipedia (no URL) is not re-queried."""
        conn = self._make_db()
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.found('Antibody description.')):
            _get_drugs_from_pubchem(conn, project_id=1)

        mocked_lookup = MagicMock()
        mocked_summary = MagicMock()
        with patch('respro.db.drugs.lookup_drug', mocked_lookup), \
             patch('respro.db.drugs.lookup_summary', mocked_summary):
            _get_drugs_from_pubchem(conn, project_id=1)

        mocked_lookup.assert_not_called()
        mocked_summary.assert_not_called()
        conn.close()

    def test_compound_drug_still_stores_cid_and_structure(self) -> None:
        """The compound path is unchanged: CID, compound URL, and structure URL stored."""
        conn = self._make_db()
        record = PubChemRecord(
            cid=135398513,
            url='https://pubchem.ncbi.nlm.nih.gov/compound/135398513',
            description='Antiviral drug.',
            structure_url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/135398513/PNG',
        )
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.found(record)):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT pubchem_cid, pubchem_url, structure_url, pubchem_lookup_failed '
            "FROM drug WHERE name = 'nirsevimab'"
        ).fetchone()
        assert row['pubchem_cid'] == '135398513'
        assert row['pubchem_url'] == 'https://pubchem.ncbi.nlm.nih.gov/compound/135398513'
        assert row['structure_url'] != ''
        assert row['pubchem_lookup_failed'] == 0
        conn.close()

    def test_transient_failure_does_not_set_flag_and_is_retried(self) -> None:
        """A transient lookup failure leaves the flag cleared so the next run retries."""
        conn = self._make_db()

        # Run 1: the whole chain fails transiently (network down).
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.error()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.error()):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT description, pubchem_lookup_failed FROM drug WHERE name = \'nirsevimab\''
        ).fetchone()
        assert row['description'] == ''
        assert row['pubchem_lookup_failed'] == 0

        # Run 2: the network is back — the drug must be queried again and enriched.
        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.found(self._substance_record())):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT pubchem_url, description, pubchem_lookup_failed FROM drug WHERE name = \'nirsevimab\''
        ).fetchone()
        assert row['pubchem_url'] == 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205'
        assert row['description'] == 'Nirsevimab'
        assert row['pubchem_lookup_failed'] == 0
        conn.close()

    def test_transient_wikipedia_failure_does_not_set_flag_and_is_retried(self) -> None:
        """A Wikipedia transient failure after a definitive PubChem miss also retries."""
        conn = self._make_db()

        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.error()):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT description, pubchem_lookup_failed FROM drug WHERE name = \'nirsevimab\''
        ).fetchone()
        assert row['description'] == ''
        assert row['pubchem_lookup_failed'] == 0

        with patch('respro.db.drugs.lookup_drug', return_value=LookupResult.not_found()), \
             patch('respro.db.drugs.lookup_summary', return_value=LookupResult.found('Antibody description.')):
            _get_drugs_from_pubchem(conn, project_id=1)

        row = conn.execute(
            'SELECT description, pubchem_lookup_failed FROM drug WHERE name = \'nirsevimab\''
        ).fetchone()
        assert row['description'] == 'Antibody description.'
        assert row['pubchem_lookup_failed'] == 0
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Rendered report card for an SID-only drug
# ──────────────────────────────────────────────────────────────────────

class TestSubstanceOnlyDrugReportCard:
    """An SID-only drug card renders the substance link + description, no structure button."""

    def test_sid_only_card_renders_link_and_description_without_structure_button(self) -> None:
        rule = ResistanceRule(
            id=1,
            feature_name='F',
            feature_id=1,
            drug_name='nirsevimab',
            drug_id=1,
            reference_identifier='ref',
            position=1,
            reference='K',
            mutation='R',
            phenotype='resistant',
        )
        feature = FeatureRecord(
            id=1, reference_id=1, name='F', protein='F',
            start=0, end=9, strand='+', codon_start=0,
            nt_sequence='ATGAAAGCT',
        )
        annotation = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=1.0, depth=100),
            feature_name='F',
            codon_pos=1,
            ref_aa='K',
            alt_aa='R',
            consequence='missense',
            af_bin='high',
            rule_matches=[rule],
        )
        result = make_profiling_result(
            project_name='T',
            reference_name='ref',
            reference_length_nt=1000,
            sample_name='S1',
            vcf_name='test.vcf',
            total_variants=1,
            variants_in_cds=1,
            resistance_hits=1,
            annotations=[annotation],
            query_sequence='ATGAAAGCT',
            feature_matches=[
                FeatureMatch(
                    feature=feature, identity=1.0, cds_coverage=1.0, query_coverage=1.0,
                    query_start=0, query_end=9, strand='+', cigar='9M', cds_start=0,
                ),
            ],
        )

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE drug (id INTEGER PRIMARY KEY, name TEXT, pubchem_url TEXT DEFAULT '', "
            "description TEXT DEFAULT '', structure_url TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO drug (name, pubchem_url, description, structure_url) VALUES "
            "('nirsevimab', 'https://pubchem.ncbi.nlm.nih.gov/substance/381128205', "
            "'Nirsevimab is a monoclonal antibody for RSV prophylaxis.', '')"
        )
        conn.commit()

        html = render_html(result, similarity_high=1, similarity_moderate=0, project_conn=conn)

        # Substance link is rendered on the drug card.
        assert 'href="https://pubchem.ncbi.nlm.nih.gov/substance/381128205"' in html
        # The description paragraph is rendered.
        assert 'Nirsevimab is a monoclonal antibody for RSV prophylaxis.' in html
        # No structure button: data-structure-url is only emitted when a
        # structure_url exists, and this card has none. The attribute is
        # asserted with a value ('="') because the inlined report JavaScript
        # also references the attribute *name* (report.js:
        # getAttribute('data-structure-url')), which appears in every report.
        assert 'data-structure-url="' not in html
        conn.close()


