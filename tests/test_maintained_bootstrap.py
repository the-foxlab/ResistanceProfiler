"""Tests for the maintained-database bootstrap and auto-update service."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from web.backend.services.maintained_bootstrap import (
    _read_local_tsv_checksum,
    bootstrap_missing_maintained_databases,
    check_and_update_maintained_databases,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

_MANAGER_TSV_CHECKSUM = 'sha256:abc123'
_MANIFEST = [
    ('beta_db', 'sha256:beta-v2'),
    ('alpha_db', 'sha256:alpha-v1'),
]


def _json_mock(payload: object) -> MagicMock:
    """Context-manager mock returning payload as JSON bytes (mirrors test_maintained_db)."""
    body = json.dumps(payload).encode()
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _make_files(tmp_path: Path, *, tsv_checksum: str = _MANAGER_TSV_CHECKSUM) -> dict:
    """Return a fake download_database_files payload pointing at real temp files."""
    gb = tmp_path / 'X04770.gb'
    gb.write_bytes(b'LOCUS       X04770    1000 bp    DNA     linear   VRL 01-JAN-2000\n//\n')
    rules = tmp_path / 'rules.tsv'
    rules.write_text('feature\tposition\tref_aa\tmut_aa\tphenotype\treference_identifier\n')
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({'tsv_checksum': tsv_checksum}))
    return {
        'rules': rules,
        'metadata': metadata,
        'formula_rules': None,
        'example': None,
        'genbank': [gb],
    }


def _make_project_db(db_path: Path, *, name: str = 'alpha_db', tsv_checksum: str = '') -> Path:
    """Create a minimal valid project DB with the given checksum using the schema helper."""
    from respro.db.schema import create_schema

    conn = create_schema(db_path)
    try:
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            (name, 6, 'test-uuid-' + name),
        )
        if tsv_checksum:
            conn.execute(
                'UPDATE project SET metadata_tsv_checksum = ? WHERE id = 1',
                (tsv_checksum,),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _install_init_checksum_writer(remote_checksum: str) -> None:
    """Patch init_project so the freshly built DB stores the remote checksum.

    The real ``init_project`` derives ``metadata_tsv_checksum`` from metadata.json; we
    mirror that by writing the remote checksum into the resulting temp DB right after
    init runs.
    """

    def fake_init(*, db_path, name, genbank_paths, rules_tsv, formula_rules_tsv, metadata_json,
                  overwrite=False, additional_info=True, example_fasta=None):
        # Build a minimal valid DB then stamp the remote checksum.
        from respro.db.schema import create_schema

        conn = create_schema(db_path)
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
            (name, 6, 'fresh-uuid-' + name),
        )
        conn.execute('UPDATE project SET metadata_tsv_checksum = ? WHERE id = 1', (remote_checksum,))
        conn.commit()
        conn.close()
        return db_path

    return fake_init


# ── _read_local_tsv_checksum ────────────────────────────────────────────────────

class TestReadLocalTsvChecksum:
    def test_returns_stored_checksum(self, tmp_path: Path) -> None:
        db = _make_project_db(tmp_path / 'alpha.db', tsv_checksum='sha256:stored')
        assert _read_local_tsv_checksum(db) == 'sha256:stored'

    def test_returns_empty_when_unset(self, tmp_path: Path) -> None:
        db = _make_project_db(tmp_path / 'alpha.db')
        assert _read_local_tsv_checksum(db) == ''

    def test_missing_project_row_returns_empty(self, tmp_path: Path) -> None:
        from respro.db.schema import create_schema

        db = tmp_path / 'empty.db'
        conn = create_schema(db)
        conn.close()
        assert _read_local_tsv_checksum(db) == ''


# ── check_and_update_maintained_databases ───────────────────────────────────────

class TestCheckAndUpdateMaintainedDatabases:
    def test_noop_when_checksums_match(self, tmp_path: Path) -> None:
        db = _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:alpha-v1')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=_MANIFEST,
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            check_and_update_maintained_databases(tmp_path)
        assert download_mock.call_count == 0
        assert db.is_file()

    def test_rebuilds_on_checksum_mismatch_and_swaps_atomically(self, tmp_path: Path) -> None:
        old_db = _make_project_db(
            tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:old'
        )
        remote = 'sha256:new-alpha'
        init_side_effect = _install_init_checksum_writer(remote)

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=[('alpha_db', remote)],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                return_value=_make_files(tmp_path),
            ),
            patch(
                'web.backend.services.maintained_bootstrap.init_project',
                side_effect=init_side_effect,
            ),
        ):
            check_and_update_maintained_databases(tmp_path)

        assert old_db.is_file()
        assert not (tmp_path / 'alpha_db.db.tmp').is_file()
        assert _read_local_tsv_checksum(old_db) == remote

    def test_skips_user_created_db_not_in_manifest(self, tmp_path: Path) -> None:
        user_db = _make_project_db(tmp_path / 'my_custom.db', name='my_custom', tsv_checksum='sha256:mine')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=_MANIFEST,
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            check_and_update_maintained_databases(tmp_path)
        assert download_mock.call_count == 0
        assert _read_local_tsv_checksum(user_db) == 'sha256:mine'

    def test_fail_soft_when_manifest_fetch_raises(self, tmp_path: Path) -> None:
        _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:old')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                side_effect=RuntimeError('manifest down'),
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            # Must not raise.
            check_and_update_maintained_databases(tmp_path)
        assert download_mock.call_count == 0

    def test_fail_soft_per_db_one_failure_does_not_abort_others(self, tmp_path: Path) -> None:
        _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:old')
        _make_project_db(tmp_path / 'beta_db.db', name='beta_db', tsv_checksum='sha256:old')
        beta_remote = 'sha256:beta-v2'

        def download_side_effect(name, dest_dir):  # noqa: ANN001
            materialized = _make_files(tmp_path)
            if name == 'alpha_db':
                raise RuntimeError('alpha download blows up')
            return materialized

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=[('alpha_db', 'sha256:alpha-new'), ('beta_db', beta_remote)],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                side_effect=download_side_effect,
            ),
            patch(
                'web.backend.services.maintained_bootstrap.init_project',
                side_effect=_install_init_checksum_writer(beta_remote),
            ),
        ):
            check_and_update_maintained_databases(tmp_path)

        # alpha untouched (failure), beta refreshed.
        assert _read_local_tsv_checksum(tmp_path / 'alpha_db.db') == 'sha256:old'
        assert _read_local_tsv_checksum(tmp_path / 'beta_db.db') == beta_remote

    def test_skips_when_manifest_lacks_checksum(self, tmp_path: Path) -> None:
        _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:old')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=[('alpha_db', '')],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            check_and_update_maintained_databases(tmp_path)
        assert download_mock.call_count == 0
        assert _read_local_tsv_checksum(tmp_path / 'alpha_db.db') == 'sha256:old'

    def test_missing_db_file_is_skipped(self, tmp_path: Path) -> None:
        # alpha_db exists, beta_db is absent (bootstrap owns missing ones).
        _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db', tsv_checksum='sha256:alpha-v1')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases_with_checksums',
                return_value=_MANIFEST,
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            check_and_update_maintained_databases(tmp_path)
        # alpha matched (no rebuild); beta absent (no rebuild). No downloads at all.
        assert download_mock.call_count == 0


# ── bootstrap_missing_maintained_databases (refactor regression) ───────────────

class TestBootstrapMissingMaintainedDatabasesRefactorRegression:
    def test_downloads_missing_database_and_initializes_atomically(self, tmp_path: Path) -> None:
        init_calls: list[Path] = []

        def init_side_effect(*, db_path, name, genbank_paths, rules_tsv, formula_rules_tsv,
                             metadata_json, overwrite=False, additional_info=True, example_fasta=None):
            from respro.db.schema import create_schema

            conn = create_schema(db_path)
            conn.execute(
                'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
                (name, 6, 'boot-uuid-' + name),
            )
            conn.commit()
            conn.close()
            init_calls.append(db_path)
            return db_path

        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases',
                return_value=['alpha_db'],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
                return_value=_make_files(tmp_path),
            ),
            patch(
                'web.backend.services.maintained_bootstrap.init_project',
                side_effect=init_side_effect,
            ),
        ):
            bootstrap_missing_maintained_databases(tmp_path)

        final_db = tmp_path / 'alpha_db.db'
        assert final_db.is_file()
        assert not (tmp_path / 'alpha_db.db.tmp').is_file()
        assert init_calls and init_calls[-1].name == 'alpha_db.db.tmp'

    def test_skips_existing_valid_db(self, tmp_path: Path) -> None:
        _make_project_db(tmp_path / 'alpha_db.db', name='alpha_db')
        with (
            patch(
                'web.backend.services.maintained_bootstrap.list_maintained_databases',
                return_value=['alpha_db'],
            ),
            patch(
                'web.backend.services.maintained_bootstrap.download_database_files',
            ) as download_mock,
        ):
            bootstrap_missing_maintained_databases(tmp_path)
        assert download_mock.call_count == 0


# ── startup wiring ─────────────────────────────────────────────────────────────

class TestStartupWiring:
    def test_load_startup_config_returns_config_even_if_update_check_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from web.backend.startup_config import StartupConfig, load_startup_config

        data_dir = tmp_path / 'data'
        for sub in ('project_databases', 'uploads', 'results'):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        # A pre-existing valid DB so _validate_at_least_one_project_db passes without bootstrapping.
        _make_project_db(data_dir / 'project_databases' / 'alpha_db.db', name='alpha_db')

        monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir.resolve()))
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'http://localhost:5173')
        monkeypatch.delenv('RESPRO_WEB_API_TOKEN', raising=False)
        monkeypatch.setattr(
            'web.backend.startup_config.bootstrap_missing_maintained_databases',
            lambda *_args, **_kwargs: None,
        )

        def boom(*_args, **_kwargs):  # noqa: ANN202
            raise RuntimeError('update check exploded')

        monkeypatch.setattr(
            'web.backend.startup_config.check_and_update_maintained_databases',
            boom,
        )

        config = load_startup_config()
        assert isinstance(config, StartupConfig)
        assert config.project_databases_dir == (data_dir / 'project_databases').resolve()


# ── weekly thread ──────────────────────────────────────────────────────────────

class TestMaintainedDbUpdateThread:
    def test_does_not_start_thread_when_interval_zero(self, tmp_path: Path) -> None:
        from web.backend.main import _start_maintained_db_update_thread

        app_state = MagicMock()
        with patch('web.backend.main._maintained_db_update_loop') as loop_mock:
            _start_maintained_db_update_thread(tmp_path, 0, app_state)
        time.sleep(0.02)
        assert loop_mock.call_count == 0

    def test_starts_thread_and_calls_check_when_interval_positive(self, tmp_path: Path) -> None:
        from web.backend.main import _start_maintained_db_update_thread

        app_state = MagicMock()
        app_state.project_db_uuid_index = {}
        called = threading.Event()
        stop_event = threading.Event()

        def fake_check(_: Path) -> None:
            called.set()

        try:
            with (
                patch(
                    'web.backend.main.check_and_update_maintained_databases',
                    side_effect=fake_check,
                ),
                patch('web.backend.main.refresh_project_db_uuid_index', return_value={'u': tmp_path / 'x.db'}),
            ):
                _start_maintained_db_update_thread(tmp_path, 0.01, app_state, stop_event=stop_event)
                assert called.wait(timeout=2)
            assert app_state.project_db_uuid_index == {'u': tmp_path / 'x.db'}
        finally:
            stop_event.set()

    def test_interval_resolver_reads_env_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web.backend.main import _resolve_maintained_db_update_interval

        default = 604800
        monkeypatch.delenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', raising=False)
        assert _resolve_maintained_db_update_interval() == default
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '123')
        assert _resolve_maintained_db_update_interval() == 123
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '0')
        assert _resolve_maintained_db_update_interval() == 0

    def test_interval_resolver_falls_back_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web.backend.main import _resolve_maintained_db_update_interval

        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', 'not-a-number')
        assert _resolve_maintained_db_update_interval() == 604800
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '-5')
        assert _resolve_maintained_db_update_interval() == 604800


# ── stale UUID index refresh ───────────────────────────────────────────────────

class TestStaleUuidIndexRefresh:
    def test_weekly_loop_clears_and_updates_app_state_index(self, tmp_path: Path) -> None:
        from web.backend.main import _start_maintained_db_update_thread

        # A real mutable dict seeded with a stale entry; the loop clears and replaces it.
        app_state = MagicMock()
        stale = {'old-uuid': tmp_path / 'old.db'}
        app_state.project_db_uuid_index = stale
        refreshed = {'new-uuid': tmp_path / 'new.db'}

        refreshed_event = threading.Event()
        stop_event = threading.Event()

        def fake_refresh(_: Path) -> dict[str, Path]:
            refreshed_event.set()
            return refreshed

        try:
            with (
                patch(
                    'web.backend.main.check_and_update_maintained_databases'
                ),
                patch('web.backend.main.refresh_project_db_uuid_index', side_effect=fake_refresh),
            ):
                _start_maintained_db_update_thread(tmp_path, 0.01, app_state, stop_event=stop_event)
                assert refreshed_event.wait(timeout=2)

            # The same dict object, now emptied and repopulated with the refreshed index.
            assert app_state.project_db_uuid_index is stale
            assert app_state.project_db_uuid_index == refreshed
        finally:
            stop_event.set()
