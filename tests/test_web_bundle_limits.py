"""Regression tests for startup-configured artifact and cleanup limits."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.test_web_api import startup_config as startup_config
from web.backend.main import create_app
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    record_artifact,
    record_upload,
)
from web.backend.startup_config import StartupConfig


@pytest.fixture()
def configured_client(startup_config: StartupConfig, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '65')
    return TestClient(create_app(startup_config=startup_config))


def _session_hash(client: TestClient) -> str:
    client.get('/api/ui/config')
    return hash_session_token(client.cookies.get(SESSION_COOKIE_NAME))


class TestConfiguredArtifactBundleBounds:
    """The advertised limit is usable for downloads and session cleanup."""

    def test_ui_config_advertises_full_batch(self, configured_client: TestClient) -> None:
        data = configured_client.get('/api/ui/config').json()['data']
        assert data['batch_max_samples'] == 65
        assert data['artifact_bundle_max_artifacts'] == 260

    def test_validation_limits_are_isolated_per_app(
        self, configured_client: TestClient, startup_config: StartupConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        smaller_client = TestClient(create_app(startup_config=startup_config))

        large_response = configured_client.post('/api/artifact-bundle', json={'artifact_ids': ['id'] * 260})
        small_response = smaller_client.post('/api/artifact-bundle', json={'artifact_ids': ['id'] * 260})

        assert large_response.status_code == 404
        assert small_response.status_code == 422

    def test_bundle_accepts_full_batch(
        self, configured_client: TestClient, startup_config: StartupConfig,
    ) -> None:
        session_hash = _session_hash(configured_client)
        artifact = startup_config.results_dir / 'sample.results.json'
        artifact.write_text('{}')
        artifact_id = record_artifact(
            session_hash=session_hash, canonical_path=artifact, media_type='application/json',
        )

        response = configured_client.post('/api/artifact-bundle', json={'artifact_ids': [artifact_id] * 260})

        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 260

    @pytest.mark.parametrize('endpoint', ['/api/artifact-bundle', '/api/session/cleanup'])
    def test_artifact_limit_plus_one_returns_readable_error(
        self, configured_client: TestClient, endpoint: str,
    ) -> None:
        response = configured_client.post(endpoint, json={'artifact_ids': ['id'] * 261})

        assert response.status_code == 400
        assert isinstance(response.json()['detail'], str)
        assert '260' in response.json()['detail']

    def test_cleanup_accepts_frontend_sized_upload_and_artifact_chunks(
        self, configured_client: TestClient, startup_config: StartupConfig,
    ) -> None:
        session_hash = _session_hash(configured_client)
        upload = startup_config.uploads_dir / 'sample.fasta'
        upload.write_text('>sample\nACGT\n')
        artifact = startup_config.results_dir / 'sample.results.json'
        artifact.write_text('{}')
        upload_id = record_upload(session_hash=session_hash, canonical_path=upload, file_type='fasta')
        artifact_id = record_artifact(
            session_hash=session_hash, canonical_path=artifact, media_type='application/json',
        )

        response = configured_client.post('/api/session/cleanup', json={
            'upload_ids': [upload_id] * 260, 'artifact_ids': [artifact_id] * 260,
        })

        assert response.status_code == 200, response.text
        assert response.json()['deleted_count'] == 2
        assert not upload.exists()
        assert not artifact.exists()

    @pytest.mark.parametrize('endpoint,field,count', [
        ('/api/artifact-bundle', 'artifact_ids', 262),
        ('/api/session/cleanup', 'artifact_ids', 262),
        ('/api/session/cleanup', 'upload_ids', 261),
    ])
    def test_oversized_lists_remain_bounded(
        self, configured_client: TestClient, endpoint: str, field: str, count: int,
    ) -> None:
        response = configured_client.post(endpoint, json={field: ['id'] * count})
        assert response.status_code == 422
